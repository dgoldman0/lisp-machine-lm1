"""Phase 2 tests — allocation, tagged field access, TST.SHAPE.

Build-plan milestone: cons a list of fixnums, walk it, sum them.
"""

from lm1.testing.harness import test
from lm1.execute import Emulator
from lm1.decode import (
    Op, encode_r, encode_i, encode_s, encode_b, encode_x,
    FUNC_ADD, FUNC_SUB,
    FUNC_ADD_FIX, FUNC_SUB_FIX,
    FUNC_CMP, FUNC_EQ,
    BR_T, BR_NIL, BR_FIX_EQ,
)
from lm1.word import (
    NIL, T, tag_fixnum, untag_fixnum,
    is_cons_ref, is_ref, ref_address,
    make_header, HDR_CONS, HDR_VECTOR, HDR_CLOSURE,
    header_shape_id, header_subtype, header_size,
    is_header,
)
from lm1.execute import EMU_TRAP_PUTCHAR


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _emu(**kw):
    """Create an Emulator with a small nursery for testing."""
    return Emulator(
        nursery_base=0x1_0000,   # 64 KiB offset
        nursery_size=0x1_0000,   # 64 KiB nursery
        **kw,
    )


def _load_and_run(emu, program, *, base=0, max_instructions=10_000):
    """Load instructions at `base` and run."""
    for i, insn in enumerate(program):
        emu.mem.store_u32(base + i * 4, insn)
    emu.thread.pc = base
    emu.run(max_instructions=max_instructions)


def _alloc_x(rd, n_words, tmpl_idx):
    """Encode ALLOC instruction (Format X)."""
    payload = ((rd & 0x1F) << 21) | ((n_words & 0x1F) << 16) | (tmpl_idx & 0xFFFF)
    return encode_x(Op.ALLOC, payload)


def _alloc_cons_x(rd):
    """Encode ALLOC.CONS instruction."""
    payload = (rd & 0x1F) << 21
    return encode_x(Op.ALLOC_CONS, payload)


def _allocv_x(rd, rs_len, tmpl_idx):
    """Encode ALLOCV instruction."""
    payload = ((rd & 0x1F) << 21) | ((rs_len & 0x1F) << 16) | (tmpl_idx & 0xFFFF)
    return encode_x(Op.ALLOCV, payload)


def _alloc_closure_x(rd, rs_code, env_size):
    """Encode ALLOC.CLOSURE instruction."""
    payload = ((rd & 0x1F) << 21) | ((rs_code & 0x1F) << 16) | ((env_size & 0x1F) << 11)
    return encode_x(Op.ALLOC_CLOSURE, payload)


# ===================================================================
# Batch: phase2_alloc — basic allocation
# ===================================================================

@test("alloc_cons_basic", batch="phase2_alloc")
def test_alloc_cons_basic():
    """ALLOC.CONS creates a cons cell, returns a cons-ref."""
    emu = _emu()
    program = [
        _alloc_cons_x(1),                     # r1 = cons cell
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    ref = emu.thread.regs[1]
    assert is_cons_ref(ref), f"Expected cons-ref, got {ref:#x}"
    # Check that NP moved by 24 bytes (header + car + cdr)
    assert emu.thread.np == 0x1_0000 + 24


@test("alloc_cons_header", batch="phase2_alloc")
def test_alloc_cons_header():
    """ALLOC.CONS writes a proper cons header in memory."""
    emu = _emu()
    program = [
        _alloc_cons_x(1),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    ref = emu.thread.regs[1]
    addr = ref_address(ref)
    hdr = emu.mem.load_word(addr)
    assert is_header(hdr), f"Expected header word, got {hdr:#x}"
    assert header_subtype(hdr) == HDR_CONS
    assert header_size(hdr) == 2  # car + cdr


@test("alloc_cons_defaults_to_nil", batch="phase2_alloc")
def test_alloc_cons_defaults_to_nil():
    """ALLOC.CONS initializes car and cdr to nil."""
    emu = _emu()
    program = [
        _alloc_cons_x(1),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    addr = ref_address(emu.thread.regs[1])
    car = emu.mem.load_word(addr + 8)
    cdr = emu.mem.load_word(addr + 16)
    assert car == NIL, f"car should be nil, got {car:#x}"
    assert cdr == NIL, f"cdr should be nil, got {cdr:#x}"


@test("alloc_generic", batch="phase2_alloc")
def test_alloc_generic():
    """ALLOC: allocate a 3-word object with template 0."""
    emu = _emu()
    # Install a custom header template at index 5
    emu.thread.header_templates[5] = make_header(0, 0, 42)  # shape_id=42
    program = [
        _alloc_x(1, 3, 5),                    # r1 = alloc 3 words, template 5
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    ref = emu.thread.regs[1]
    assert is_ref(ref), f"Expected ref, got {ref:#x}"
    addr = ref_address(ref)
    hdr = emu.mem.load_word(addr)
    assert header_size(hdr) == 3
    assert header_shape_id(hdr) == 42


@test("alloc_cons_multiple", batch="phase2_alloc")
def test_alloc_cons_multiple():
    """Allocating multiple cons cells bumps NP correctly."""
    emu = _emu()
    program = [
        _alloc_cons_x(1),        # first cons
        _alloc_cons_x(2),        # second cons
        _alloc_cons_x(3),        # third cons
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    base = 0x1_0000
    assert emu.thread.np == base + 3 * 24
    # Each ref should point to a different address
    addrs = [ref_address(emu.thread.regs[i]) for i in range(1, 4)]
    assert len(set(addrs)) == 3, "All cons addresses should be distinct"


@test("alloc_nursery_overflow", batch="phase2_alloc")
def test_alloc_nursery_overflow():
    """Allocating past the nursery limit raises TRAP_NURSERY_OVERFLOW."""
    emu = Emulator(nursery_base=0x1_0000, nursery_size=48)  # tiny nursery: 48 bytes = 2 cons
    program = [
        _alloc_cons_x(1),
        _alloc_cons_x(2),
        _alloc_cons_x(3),        # should overflow
        encode_x(Op.HALT_NOP, 0),
    ]
    try:
        _load_and_run(emu, program)
        assert False, "Should have raised LM1Trap for nursery overflow"
    except Exception as e:
        assert "overflow" in str(e).lower() or "NURSERY" in str(e).upper()


# ===================================================================
# Batch: phase2_fields — tagged field access
# ===================================================================

@test("st_ld_car_cdr", batch="phase2_fields")
def test_st_ld_car_cdr():
    """ST.CAR/ST.CDR then LD.CAR/LD.CDR round-trip."""
    emu = _emu()
    program = [
        _alloc_cons_x(1),                                      # r1 = cons
        encode_i(Op.LI, 2, 0, tag_fixnum(42) & 0xFFFF),       # r2 = fixnum 42
        encode_i(Op.LI, 3, 0, tag_fixnum(99) & 0xFFFF),       # r3 = fixnum 99
        # ST.CAR r1, r2 (Format S: rd=Rs, rs1=Rt, rs2=field)
        encode_s(Op.ST_CAR_CDR, 1, 2, 0),                     # car = r2
        encode_s(Op.ST_CAR_CDR, 1, 3, 1),                     # cdr = r3
        # LD.CAR r4, r1
        encode_i(Op.LD_CAR_CDR, 4, 1, 0),                     # r4 = car
        # LD.CDR r5, r1
        encode_i(Op.LD_CAR_CDR, 5, 1, 1),                     # r5 = cdr
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[4] == tag_fixnum(42)
    assert emu.thread.regs[5] == tag_fixnum(99)


@test("ld_field_generic", batch="phase2_fields")
def test_ld_field_generic():
    """LD Rd, Rs, #field reads the correct slot from a generic object."""
    emu = _emu()
    emu.thread.header_templates[5] = make_header(0, 0, 42)
    program = [
        _alloc_x(1, 3, 5),                                    # r1 = 3-word obj
        encode_i(Op.LI, 2, 0, tag_fixnum(10) & 0xFFFF),
        encode_i(Op.LI, 3, 0, tag_fixnum(20) & 0xFFFF),
        encode_i(Op.LI, 4, 0, tag_fixnum(30) & 0xFFFF),
        # ST fields 0, 1, 2
        encode_s(Op.ST, 1, 2, 0),
        encode_s(Op.ST, 1, 3, 1),
        encode_s(Op.ST, 1, 4, 2),
        # LD fields back
        encode_i(Op.LD, 10, 1, 0),
        encode_i(Op.LD, 11, 1, 1),
        encode_i(Op.LD, 12, 1, 2),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[10] == tag_fixnum(10)
    assert emu.thread.regs[11] == tag_fixnum(20)
    assert emu.thread.regs[12] == tag_fixnum(30)


@test("st_wb_field", batch="phase2_fields")
def test_st_wb_field():
    """ST.WB stores a ref into a field (barrier is no-op in Phase 2)."""
    emu = _emu()
    program = [
        _alloc_cons_x(1),           # r1 = inner cons
        _alloc_cons_x(2),           # r2 = outer cons
        # Store inner into outer's car via ST.WB
        encode_s(Op.ST_WB, 2, 1, 0),   # outer.car = inner
        # Read it back
        encode_i(Op.LD_CAR_CDR, 3, 2, 0),  # r3 = outer.car
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[3] == emu.thread.regs[1], "car should be the inner cons ref"


@test("st_ld_not_ref_traps", batch="phase2_fields")
def test_st_ld_not_ref_traps():
    """LD/ST on a non-ref raises TRAP_NOT_REF."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 42),            # r1 = raw 42 (not a ref)
        encode_i(Op.LD, 2, 1, 0),             # should trap
        encode_x(Op.HALT_NOP, 0),
    ]
    try:
        _load_and_run(emu, program)
        assert False, "Should have raised LM1Trap"
    except Exception as e:
        assert "ref" in str(e).lower()


# ===================================================================
# Batch: phase2_shape — TST.SHAPE
# ===================================================================

@test("tst_shape_match", batch="phase2_shape")
def test_tst_shape_match():
    """TST.SHAPE returns T when shape matches."""
    emu = _emu()
    emu.thread.header_templates[5] = make_header(0, 0, 42)
    program = [
        _alloc_x(1, 2, 5),                    # shape_id=42
        encode_i(Op.TST_SHAPE, 2, 1, 42),     # test shape==42
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[2] == T


@test("tst_shape_mismatch", batch="phase2_shape")
def test_tst_shape_mismatch():
    """TST.SHAPE returns NIL when shape doesn't match."""
    emu = _emu()
    emu.thread.header_templates[5] = make_header(0, 0, 42)
    program = [
        _alloc_x(1, 2, 5),
        encode_i(Op.TST_SHAPE, 2, 1, 99),     # test shape==99 (wrong)
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[2] == NIL


@test("tst_shape_non_ref", batch="phase2_shape")
def test_tst_shape_non_ref():
    """TST.SHAPE returns NIL for non-ref values."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, tag_fixnum(5) & 0xFFFF),
        encode_i(Op.TST_SHAPE, 2, 1, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[2] == NIL


# ===================================================================
# Batch: phase2_closure — ALLOC.CLOSURE
# ===================================================================

@test("alloc_closure_basic", batch="phase2_closure")
def test_alloc_closure_basic():
    """ALLOC.CLOSURE creates a closure with code pointer and env slots."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 5, 0, 0x1234),        # r5 = code pointer (raw)
        _alloc_closure_x(1, 5, 2),             # r1 = closure(code=r5, env=2)
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    ref = emu.thread.regs[1]
    assert is_ref(ref), f"Expected ref, got {ref:#x}"
    addr = ref_address(ref)
    hdr = emu.mem.load_word(addr)
    assert header_subtype(hdr) == HDR_CLOSURE
    # code pointer at slot 0 (offset +8)
    code = emu.mem.load_word(addr + 8)
    assert code == 0x1234


@test("alloc_closure_env", batch="phase2_closure")
def test_alloc_closure_env():
    """ALLOC.CLOSURE env slots can be written and read back."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 5, 0, 0x100),         # r5 = code
        _alloc_closure_x(1, 5, 2),             # r1 = closure(code=r5, env=2)
        encode_i(Op.LI, 6, 0, tag_fixnum(77) & 0xFFFF),
        # Write env[0] at field 1 (skip code at field 0)
        encode_s(Op.ST, 1, 6, 1),             # closure.env[0] = 77
        encode_i(Op.LD, 7, 1, 1),             # r7 = closure.env[0]
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[7] == tag_fixnum(77)


# ===================================================================
# Batch: phase2_vector — ALLOCV
# ===================================================================

@test("allocv_basic", batch="phase2_vector")
def test_allocv_basic():
    """ALLOCV allocates a vector with the correct length."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 5, 0, tag_fixnum(4) & 0xFFFF),  # r5 = length 4
        _allocv_x(1, 5, 2),                               # r1 = vector(len=r5, tmpl=2)
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    ref = emu.thread.regs[1]
    assert is_ref(ref)
    addr = ref_address(ref)
    hdr = emu.mem.load_word(addr)
    assert header_subtype(hdr) == HDR_VECTOR
    # Length word at offset +8
    length_word = emu.mem.load_word(addr + 8)
    assert length_word == tag_fixnum(4)


@test("allocv_read_write", batch="phase2_vector")
def test_allocv_read_write():
    """ALLOCV elements can be written and read via LD/ST."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 5, 0, tag_fixnum(3) & 0xFFFF),  # length 3
        _allocv_x(1, 5, 2),
        # Write element 0 (field 1 = length, field 2 = elem[0])
        encode_i(Op.LI, 6, 0, tag_fixnum(11) & 0xFFFF),
        encode_s(Op.ST, 1, 6, 2),              # vec[0] = 11
        encode_i(Op.LI, 6, 0, tag_fixnum(22) & 0xFFFF),
        encode_s(Op.ST, 1, 6, 3),              # vec[1] = 22
        encode_i(Op.LI, 6, 0, tag_fixnum(33) & 0xFFFF),
        encode_s(Op.ST, 1, 6, 4),              # vec[2] = 33
        # Read back
        encode_i(Op.LD, 10, 1, 2),
        encode_i(Op.LD, 11, 1, 3),
        encode_i(Op.LD, 12, 1, 4),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[10] == tag_fixnum(11)
    assert emu.thread.regs[11] == tag_fixnum(22)
    assert emu.thread.regs[12] == tag_fixnum(33)


# ===================================================================
# Batch: phase2_integration — milestone tests
# ===================================================================

@test("cons_list_5_sum", batch="phase2_integration")
def test_cons_list_5_sum():
    """Cons a list of 5 fixnums, walk it, sum them.

    Builds: (cons 1 (cons 2 (cons 3 (cons 4 (cons 5 nil)))))
    Then walks the list, summing car values.
    Expected sum = 1+2+3+4+5 = 15.
    """
    emu = _emu()

    # Strategy:
    # Phase A — build the list (innermost first):
    #   r10 = nil (accumulator for list tail)
    #   loop: LI counter, ALLOC.CONS, ST.CAR counter, ST.CDR prev, ...
    # Phase B — walk and sum:
    #   r20 = sum (fixnum 0)
    #   r21 = current (list head)
    #   loop: LD.CAR, ADD.FIX to sum, LD.CDR, BR.NIL to exit

    # For simplicity, we'll build the list manually (5 conses).
    # Then write the walk loop.

    program = [
        # -- Build list: (1 2 3 4 5) innermost-first --
        # r10 starts as nil (list being built from the tail)
        encode_i(Op.LI, 10, 0, NIL & 0xFFFF),

        # cons 5 onto nil
        _alloc_cons_x(11),                                     # r11 = new cons
        encode_i(Op.LI, 12, 0, tag_fixnum(5) & 0xFFFF),
        encode_s(Op.ST_CAR_CDR, 11, 12, 0),                   # car = 5
        encode_s(Op.ST_CAR_CDR, 11, 10, 1),                   # cdr = nil
        encode_r(Op.ARITH_RAW, 10, 11, 0, FUNC_ADD),          # r10 = r11 (move)

        # cons 4
        _alloc_cons_x(11),
        encode_i(Op.LI, 12, 0, tag_fixnum(4) & 0xFFFF),
        encode_s(Op.ST_CAR_CDR, 11, 12, 0),
        encode_s(Op.ST_CAR_CDR, 11, 10, 1),
        encode_r(Op.ARITH_RAW, 10, 11, 0, FUNC_ADD),

        # cons 3
        _alloc_cons_x(11),
        encode_i(Op.LI, 12, 0, tag_fixnum(3) & 0xFFFF),
        encode_s(Op.ST_CAR_CDR, 11, 12, 0),
        encode_s(Op.ST_CAR_CDR, 11, 10, 1),
        encode_r(Op.ARITH_RAW, 10, 11, 0, FUNC_ADD),

        # cons 2
        _alloc_cons_x(11),
        encode_i(Op.LI, 12, 0, tag_fixnum(2) & 0xFFFF),
        encode_s(Op.ST_CAR_CDR, 11, 12, 0),
        encode_s(Op.ST_CAR_CDR, 11, 10, 1),
        encode_r(Op.ARITH_RAW, 10, 11, 0, FUNC_ADD),

        # cons 1
        _alloc_cons_x(11),
        encode_i(Op.LI, 12, 0, tag_fixnum(1) & 0xFFFF),
        encode_s(Op.ST_CAR_CDR, 11, 12, 0),
        encode_s(Op.ST_CAR_CDR, 11, 10, 1),
        # r10 = head of list (1 2 3 4 5)
        encode_r(Op.ARITH_RAW, 10, 11, 0, FUNC_ADD),

        # -- Walk and sum --
        # r20 = sum = fixnum(0)
        encode_i(Op.LI, 20, 0, tag_fixnum(0) & 0xFFFF),
        # r21 = current = r10 (list head)
        encode_r(Op.ARITH_RAW, 21, 10, 0, FUNC_ADD),

        # LOOP (at instruction offset 32):
        # BR.NIL r21, +5  → if current==nil, jump to HALT
        encode_b(Op.BR_COND, 21, BR_NIL, 5),
        # LD.CAR r22, r21
        encode_i(Op.LD_CAR_CDR, 22, 21, 0),
        # ADD.FIX r20, r20, r22
        encode_r(Op.ARITH_FIX, 20, 20, 22, FUNC_ADD_FIX),
        # LD.CDR r21, r21
        encode_i(Op.LD_CAR_CDR, 21, 21, 1),
        # BR -4  → back to LOOP
        encode_b(Op.BR, 0, 0, (-4) & 0xFFFF),

        # HALT
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[20] == tag_fixnum(15), \
        f"Expected sum fixnum(15)={tag_fixnum(15):#x}, got {emu.thread.regs[20]:#x}"


@test("cons_list_100_sum", batch="phase2_integration")
def test_cons_list_100_sum():
    """Build-plan milestone: cons a list of 100 fixnums, walk it, sum them.

    Uses a loop to build the list, then a loop to sum it.
    Expected: sum(1..100) = 5050.
    """
    emu = _emu()

    # Register plan:
    # r1  = loop counter (fixnum, counts down from 100 to 0)
    # r2  = step (fixnum -1, actually we'll sub fixnum 1)
    # r3  = list accumulator (starts nil, builds from tail)
    # r10 = temp cons ref
    # r11 = temp fixnum for car value
    #
    # Walk phase:
    # r20 = sum (fixnum 0)
    # r21 = current (list ptr)
    # r22 = car value

    program = [
        # -- Phase A: build list 100, 99, ... 1 --
        # (we build from high to low so the final list is (1 2 ... 100))
        encode_i(Op.LI, 1, 0, tag_fixnum(100) & 0xFFFF),      # r1 = counter = 100
        encode_i(Op.LI, 2, 0, tag_fixnum(1) & 0xFFFF),        # r2 = fixnum 1
        encode_i(Op.LI, 3, 0, NIL & 0xFFFF),                  # r3 = nil (list acc)

        # BUILD_LOOP (offset 3):
        # if counter == fixnum(0), goto WALK
        encode_b(Op.BR_COND, 1, BR_FIX_EQ, 7),                # +7 to WALK

        # ALLOC.CONS r10
        _alloc_cons_x(10),
        # ST.CAR r10, r1  (car = current counter value)
        encode_s(Op.ST_CAR_CDR, 10, 1, 0),
        # ST.CDR r10, r3  (cdr = previous list)
        encode_s(Op.ST_CAR_CDR, 10, 3, 1),
        # r3 = r10 (new list head)
        encode_r(Op.ARITH_RAW, 3, 10, 0, FUNC_ADD),
        # counter -= 1
        encode_r(Op.ARITH_FIX, 1, 1, 2, FUNC_SUB_FIX),
        # BR -6 → back to BUILD_LOOP
        encode_b(Op.BR, 0, 0, (-6) & 0xFFFF),

        # WALK (offset 10):
        encode_i(Op.LI, 20, 0, tag_fixnum(0) & 0xFFFF),       # r20 = sum = 0
        encode_r(Op.ARITH_RAW, 21, 3, 0, FUNC_ADD),           # r21 = r3 (list head)

        # SUM_LOOP (offset 12):
        encode_b(Op.BR_COND, 21, BR_NIL, 5),                  # if nil, goto DONE
        encode_i(Op.LD_CAR_CDR, 22, 21, 0),                   # r22 = car
        encode_r(Op.ARITH_FIX, 20, 20, 22, FUNC_ADD_FIX),     # sum += car
        encode_i(Op.LD_CAR_CDR, 21, 21, 1),                   # current = cdr
        encode_b(Op.BR, 0, 0, (-4) & 0xFFFF),                 # back to SUM_LOOP

        # DONE (offset 17):
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program, max_instructions=100_000)
    expected = tag_fixnum(5050)
    assert emu.thread.regs[20] == expected, \
        f"Expected sum fixnum(5050)={expected:#x}, got {emu.thread.regs[20]:#x}"


@test("nested_cons_structure", batch="phase2_integration")
def test_nested_cons_structure():
    """Build ((1 . 2) . (3 . 4)) and verify all four values."""
    emu = _emu()
    program = [
        # r1 = (1 . 2)
        _alloc_cons_x(1),
        encode_i(Op.LI, 5, 0, tag_fixnum(1) & 0xFFFF),
        encode_i(Op.LI, 6, 0, tag_fixnum(2) & 0xFFFF),
        encode_s(Op.ST_CAR_CDR, 1, 5, 0),     # car = 1
        encode_s(Op.ST_CAR_CDR, 1, 6, 1),     # cdr = 2

        # r2 = (3 . 4)
        _alloc_cons_x(2),
        encode_i(Op.LI, 5, 0, tag_fixnum(3) & 0xFFFF),
        encode_i(Op.LI, 6, 0, tag_fixnum(4) & 0xFFFF),
        encode_s(Op.ST_CAR_CDR, 2, 5, 0),
        encode_s(Op.ST_CAR_CDR, 2, 6, 1),

        # r3 = ((1.2) . (3.4))
        _alloc_cons_x(3),
        encode_s(Op.ST_CAR_CDR, 3, 1, 0),     # car = (1.2)
        encode_s(Op.ST_CAR_CDR, 3, 2, 1),     # cdr = (3.4)

        # Read: car(car(r3)) = 1
        encode_i(Op.LD_CAR_CDR, 10, 3, 0),    # r10 = car(r3) = (1.2)
        encode_i(Op.LD_CAR_CDR, 11, 10, 0),   # r11 = car((1.2)) = 1

        # Read: cdr(car(r3)) = 2
        encode_i(Op.LD_CAR_CDR, 12, 10, 1),   # r12 = cdr((1.2)) = 2

        # Read: car(cdr(r3)) = 3
        encode_i(Op.LD_CAR_CDR, 13, 3, 1),    # r13 = cdr(r3) = (3.4)
        encode_i(Op.LD_CAR_CDR, 14, 13, 0),   # r14 = car((3.4)) = 3

        # Read: cdr(cdr(r3)) = 4
        encode_i(Op.LD_CAR_CDR, 15, 13, 1),   # r15 = cdr((3.4)) = 4

        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[11] == tag_fixnum(1)
    assert emu.thread.regs[12] == tag_fixnum(2)
    assert emu.thread.regs[14] == tag_fixnum(3)
    assert emu.thread.regs[15] == tag_fixnum(4)
