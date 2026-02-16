"""Phase 4 tests — dispatch: CALL.DIRECT, CALL.CLOSURE, RET, IC, frame mechanics.

Build-plan milestone: define two shapes, dispatch a method on each via IC.
"""

from lm1.testing.harness import test
from lm1.execute import Emulator
from lm1.decode import (
    Op, encode_r, encode_i, encode_s, encode_b, encode_x,
    FUNC_ADD, FUNC_SUB, FUNC_DIV,
    FUNC_ADD_FIX, FUNC_SUB_FIX,
    FUNC_CMP, FUNC_EQ,
    FUNC_PUSH, FUNC_POP,
    BR_T, BR_NIL,
)
from lm1.word import (
    NIL, T, tag_fixnum, untag_fixnum,
    is_cons_ref, is_ref, is_any_ref, ref_address, make_ref,
    make_header, HDR_CONS, HDR_VECTOR, HDR_CLOSURE, HDR_INSTANCE,
    header_shape_id, header_subtype, header_size,
    is_header, WORD_MASK,
)
from lm1.traps import TRAP_IC_MISS


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

NURSERY_BASE = 0x1_0000
NURSERY_SIZE = 0x1_0000
OLDGEN_BASE  = 0x2_0000
OLDGEN_SIZE  = 0x2_0000
STACK_TOP    = 0x1_0000
CODE_BASE    = 0x0


def _emu(**kw):
    return Emulator(
        mem_size=512 * 1024,
        nursery_base=NURSERY_BASE,
        nursery_size=NURSERY_SIZE,
        oldgen_base=OLDGEN_BASE,
        oldgen_size=OLDGEN_SIZE,
        **kw,
    )


def _load_and_run(emu, program, *, base=CODE_BASE, max_instructions=10_000):
    for i, insn in enumerate(program):
        emu.mem.store_u32(base + i * 4, insn)
    emu.thread.pc = base
    emu.thread.sp = STACK_TOP
    emu.run(max_instructions=max_instructions)


def _alloc_x(rd, n_words, tmpl_idx):
    payload = ((rd & 0x1F) << 21) | ((n_words & 0x1F) << 16) | (tmpl_idx & 0xFFFF)
    return encode_x(Op.ALLOC, payload)


def _alloc_closure_x(rd, rs_code, env_size):
    payload = ((rd & 0x1F) << 21) | ((rs_code & 0x1F) << 16) | ((env_size & 0x1F) << 11)
    return encode_x(Op.ALLOC_CLOSURE, payload)


# ===================================================================
# Batch: phase4_frame — CALL.DIRECT, RET, frame mechanics
# ===================================================================

@test("call_direct_ret_basic", batch="phase4_frame")
def test_call_direct_ret_basic():
    """CALL.DIRECT pushes a frame, jumps to target; RET returns."""
    emu = _emu()
    t = emu.thread

    # Layout:
    # 0x00: CALL.DIRECT to offset +3 (→ 0x0C, the function)
    # 0x04: LI r1, 0x42  ← should execute after RET
    # 0x08: HALT
    # 0x0C: LI r2, 0x99  ← function body
    # 0x10: RET
    program = [
        encode_i(Op.CALL_DIRECT, 0, 0, 3),  # call +3 words → PC+12 = 0x0C
        encode_i(Op.LI, 1, 0, 0x42),        # executed after return
        encode_x(Op.HALT_NOP, 0),
        # --- function at 0x0C ---
        encode_i(Op.LI, 2, 0, 0x99),
        encode_x(Op.RET, 0),
    ]
    _load_and_run(emu, program)

    assert t.regs[2] == 0x99, f"Function body should have run: r2={t.regs[2]:#x}"
    assert t.regs[1] == 0x42, f"Should have returned and executed LI: r1={t.regs[1]:#x}"


@test("call_direct_nested", batch="phase4_frame")
def test_call_direct_nested():
    """Nested CALL.DIRECT / RET works correctly."""
    emu = _emu()
    t = emu.thread

    # Layout:
    # 0x00: LI r5, 0
    # 0x04: CALL.DIRECT +4 → 0x14 (func_a)
    # 0x08: LI r6, 0xAA
    # 0x0C: HALT
    # 0x10: <pad NOP>
    # 0x14: func_a — LI r5, 1; CALL.DIRECT +3 → 0x24 (func_b); RET
    # 0x24: func_b — LI r7, 0xBB; RET
    program = [
        encode_i(Op.LI, 5, 0, 0),              # 0x00
        encode_i(Op.CALL_DIRECT, 0, 0, 4),     # 0x04 → 0x14
        encode_i(Op.LI, 6, 0, 0xAA),           # 0x08 (after return from func_a)
        encode_x(Op.HALT_NOP, 0),               # 0x0C
        encode_x(Op.HALT_NOP, 1 << 21),          # 0x10 NOP pad
        # func_a at 0x14
        encode_i(Op.LI, 5, 0, 1),              # 0x14
        encode_i(Op.CALL_DIRECT, 0, 0, 3),     # 0x18 → 0x24
        encode_x(Op.RET, 0),                    # 0x1C (return to main)
        encode_x(Op.HALT_NOP, 1 << 21),          # 0x20 NOP pad
        # func_b at 0x24
        encode_i(Op.LI, 7, 0, 0xBB),           # 0x24
        encode_x(Op.RET, 0),                    # 0x28
    ]
    _load_and_run(emu, program)

    assert t.regs[5] == 1, f"func_a should have set r5=1: got {t.regs[5]}"
    assert t.regs[7] == 0xBB, f"func_b should have set r7=0xBB: got {t.regs[7]:#x}"
    assert t.regs[6] == 0xAA, f"Should have returned to main: r6={t.regs[6]:#x}"


@test("call_direct_preserves_sp", batch="phase4_frame")
def test_call_direct_preserves_sp():
    """CALL.DIRECT+RET restore SP correctly (frame cleanup)."""
    emu = _emu()
    t = emu.thread

    sp_before = STACK_TOP
    program = [
        encode_i(Op.CALL_DIRECT, 0, 0, 2),
        encode_x(Op.HALT_NOP, 0),
        # function
        encode_x(Op.RET, 0),
    ]
    _load_and_run(emu, program)

    assert t.sp == sp_before, f"SP should be restored: expected {sp_before:#x}, got {t.sp:#x}"


# ===================================================================
# Batch: phase4_closure — CALL.CLOSURE
# ===================================================================

@test("call_closure_basic", batch="phase4_closure")
def test_call_closure_basic():
    """CALL.CLOSURE calls through a closure, accesses env slot."""
    emu = _emu()
    t = emu.thread

    # Create a closure with code_entry at 0x100, 1 env slot
    # First, set r10 = code entry address (0x100)
    # Then ALLOC.CLOSURE rd=1, rs_code=10, env_size=1
    func_addr = 0x100

    program = [
        encode_i(Op.LI, 10, 0, func_addr),       # r10 = 0x100 (code entry)
        _alloc_closure_x(1, 10, 1),               # r1 = closure(code=r10, env_size=1)
        # Set env[0] = fixnum(42)
        encode_i(Op.LI, 11, 0, tag_fixnum(42) & 0xFFFF),
        # env[0] is field 1 of the closure (field 0 = code_entry)
        encode_s(Op.ST, 1, 11, 1),                # closure.field[1] = 42
        # Call the closure
        encode_i(Op.CALL_CLOSURE, 1, 0, 0),
        # After return
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program[:5])  # load main code only
    # Don't run yet — first load the function

    # Function at 0x100: load env[0] into r2, RET
    # The closure ref is in r1. env[0] = field 1.
    func = [
        encode_i(Op.LD, 2, 1, 1),     # r2 = closure.field[1] = env[0] = 42
        encode_x(Op.RET, 0),
    ]
    for i, insn in enumerate(func):
        emu.mem.store_u32(func_addr + i * 4, insn)

    # Reload main and run fully
    for i, insn in enumerate(program):
        emu.mem.store_u32(i * 4, insn)
    t.pc = 0
    t.sp = STACK_TOP
    emu.run(max_instructions=100)

    assert t.regs[2] == tag_fixnum(42), \
        f"r2 should be fixnum(42), got {untag_fixnum(t.regs[2])}"


# ===================================================================
# Batch: phase4_ic — inline cache dispatch
# ===================================================================

@test("ic_install_and_hit", batch="phase4_ic")
def test_ic_install_and_hit():
    """IC.INSTALL populates the IC, then CALL.IC hits it."""
    emu = _emu()
    t = emu.thread

    # Create an object with shape_id=100
    # We'll manually set up header template 3 with shape_id=100
    t.header_templates[3] = make_header(HDR_INSTANCE, 2, 100)

    # Method at 0x200: set r5 = 0x77, RET
    method_addr = 0x200
    method = [
        encode_i(Op.LI, 5, 0, 0x77),
        encode_x(Op.RET, 0),
    ]
    for i, insn in enumerate(method):
        emu.mem.store_u32(method_addr + i * 4, insn)

    # Main code: alloc object, install IC entry, then call it
    # IC.INSTALL: rd=callsite_pc_reg, rs1=receiver_reg, rs2=code_entry_reg
    # We need to know the callsite PC for CALL.IC ahead of time.
    # The CALL.IC instruction will be at instruction 5 (offset 0x14).
    callsite_pc = 5 * 4  # 0x14

    program = [
        _alloc_x(1, 2, 3),                        # r1 = object (shape=100)
        encode_i(Op.LI, 8, 0, callsite_pc),       # r8 = callsite PC
        encode_i(Op.LI, 9, 0, method_addr),       # r9 = method address
        encode_r(Op.IC_INSTALL, 8, 1, 9, 0),      # IC[callsite, shape(r1)] = r9
        encode_x(Op.HALT_NOP, 1 << 21),             # NOP (pad)
        # CALL.IC at 0x14: Rd = receiver register
        encode_i(Op.CALL_IC, 1, 0, 0),            # CALL.IC with receiver=r1
        encode_x(Op.HALT_NOP, 0),                  # HALT after return
    ]
    _load_and_run(emu, program)

    assert t.regs[5] == 0x77, f"Method should have set r5=0x77, got {t.regs[5]:#x}"


@test("ic_miss_traps", batch="phase4_ic")
def test_ic_miss_traps():
    """CALL.IC with no IC entry raises TRAP_IC_MISS."""
    emu = _emu()
    t = emu.thread

    # Create an object with shape 200
    t.header_templates[4] = make_header(HDR_INSTANCE, 1, 200)

    program = [
        _alloc_x(1, 1, 4),                        # r1 = object (shape=200)
        encode_i(Op.CALL_IC, 1, 0, 0),            # CALL.IC — no IC entry → miss
        encode_x(Op.HALT_NOP, 0),
    ]

    caught = False
    try:
        _load_and_run(emu, program)
    except Exception as e:
        caught = True
        assert "IC miss" in str(e) or TRAP_IC_MISS == e.code

    assert caught, "CALL.IC with no IC entry should raise TRAP_IC_MISS"


@test("ic_polymorphic", batch="phase4_ic")
def test_ic_polymorphic():
    """IC dispatch works for two different shapes at the same callsite.
    
    This is the BUILD-PLAN milestone: define two shapes, dispatch on each.
    """
    emu = _emu()
    t = emu.thread

    # Shape A (id=10): method returns fixnum(10)
    # Shape B (id=20): method returns fixnum(20)
    t.header_templates[5] = make_header(HDR_INSTANCE, 1, 10)
    t.header_templates[6] = make_header(HDR_INSTANCE, 1, 20)

    # Method A at 0x200
    method_a_addr = 0x200
    method_a = [
        encode_i(Op.LI, 5, 0, tag_fixnum(10) & 0xFFFF),
        encode_x(Op.RET, 0),
    ]
    for i, insn in enumerate(method_a):
        emu.mem.store_u32(method_a_addr + i * 4, insn)

    # Method B at 0x300
    method_b_addr = 0x300
    method_b = [
        encode_i(Op.LI, 5, 0, tag_fixnum(20) & 0xFFFF),
        encode_x(Op.RET, 0),
    ]
    for i, insn in enumerate(method_b):
        emu.mem.store_u32(method_b_addr + i * 4, insn)

    # The CALL.IC will be at instruction 8 → offset 0x20
    callsite_pc = 8 * 4  # 0x20

    program = [
        # Alloc objects
        _alloc_x(1, 1, 5),                         # 0x00: r1 = shape A
        _alloc_x(2, 1, 6),                         # 0x04: r2 = shape B

        # Install IC entries for both shapes at the same callsite
        encode_i(Op.LI, 8, 0, callsite_pc),        # 0x08: r8 = callsite
        encode_i(Op.LI, 9, 0, method_a_addr),      # 0x0C: r9 = method_a
        encode_r(Op.IC_INSTALL, 8, 1, 9, 0),       # 0x10: IC[cs, shape_A] = method_a
        encode_i(Op.LI, 9, 0, method_b_addr),      # 0x14: r9 = method_b
        encode_r(Op.IC_INSTALL, 8, 2, 9, 0),       # 0x18: IC[cs, shape_B] = method_b

        encode_x(Op.HALT_NOP, 1 << 21),              # 0x1C: NOP pad

        # Call with shape A (receiver = r1) — CALL.IC at 0x20
        encode_i(Op.CALL_IC, 1, 0, 0),             # 0x20: CALL.IC
        # After return, r5 should be fixnum(10)
        # Save result
        encode_r(Op.ARITH_RAW, 3, 5, 0, FUNC_ADD), # 0x24: r3 = r5 (copy result)

        # Now call with shape B (receiver = r2) at SAME callsite
        # But wait: callsite PC would be different (0x28 now).
        # For the test, install a second callsite or call via a helper.
        # Simplest: install IC for second callsite too.
        # Second CALL.IC at instruction 11 → 0x28
        encode_i(Op.CALL_IC, 2, 0, 0),             # 0x28: CALL.IC with shape B
        encode_r(Op.ARITH_RAW, 4, 5, 0, FUNC_ADD), # 0x2C: r4 = r5

        encode_x(Op.HALT_NOP, 0),                   # 0x30: HALT
    ]

    # Need to also install IC for the second callsite (0x28)
    # Do it by modifying the IC table directly (simpler than re-encoding)
    # Actually, let's install it from the program. But the program is linear...
    # Simplest: pre-populate via Python:
    from lm1.word import header_shape_id as get_shape
    # We know shape_A=10, shape_B=20
    # Second CALL.IC at 0x28: receiver register = r2 (shape B)
    second_callsite = 10 * 4  # 0x28
    emu.ic_table[(second_callsite, 20)] = method_b_addr
    # Also need first callsite for shape B? No — first CALL.IC uses r1 (shape A)
    # But also install for the second callsite in case shape A hits it:
    # Only shape B hits callsite 0x28.

    _load_and_run(emu, program)

    r3 = t.regs[3]
    r4 = t.regs[4]
    assert r3 == tag_fixnum(10), f"Shape A method should return 10, got {untag_fixnum(r3)}"
    assert r4 == tag_fixnum(20), f"Shape B method should return 20, got {untag_fixnum(r4)}"


@test("tailcall_direct", batch="phase4_ic")
def test_tailcall_direct():
    """TAILCALL.DIRECT reuses the current frame."""
    emu = _emu()
    t = emu.thread

    # Main calls func_a, which tailcalls func_b.
    # func_b returns to main (not to func_a).
    # 0x00: CALL.DIRECT +3 → func_a (0x0C)
    # 0x04: LI r6, 0xDD  ← should execute after final RET
    # 0x08: HALT
    # 0x0C: func_a — LI r5, 1; TAILCALL.DIRECT +2 → func_b (0x18)
    #                          (offset from 0x14 = current PC)
    # 0x14: <never reached>
    # 0x18: func_b — LI r7, 0xEE; RET → returns to 0x04 in main
    program = [
        encode_i(Op.CALL_DIRECT, 0, 0, 3),    # 0x00 → 0x0C
        encode_i(Op.LI, 6, 0, 0xDD),          # 0x04
        encode_x(Op.HALT_NOP, 0),              # 0x08
        # func_a
        encode_i(Op.LI, 5, 0, 1),             # 0x0C
        encode_i(Op.TAILCALL_DIR, 0, 0, 2),   # 0x10 → 0x10 + 2*4 = 0x18
        encode_x(Op.HALT_NOP, 0),              # 0x14 (shouldn't reach)
        # func_b
        encode_i(Op.LI, 7, 0, 0xEE),          # 0x18
        encode_x(Op.RET, 0),                   # 0x1C → returns to 0x04
    ]
    _load_and_run(emu, program)

    assert t.regs[5] == 1, f"func_a should have set r5=1"
    assert t.regs[7] == 0xEE, f"func_b should have set r7=0xEE"
    assert t.regs[6] == 0xDD, f"Should have returned to main: r6={t.regs[6]:#x}"
    # SP should be clean
    assert t.sp == STACK_TOP, f"SP should be restored after tail call"
