"""Phase 1 tests — scalar execution, branches, I/O, tagged arith, type tests.

Each test is a self-contained LM-1 program that is hand-assembled,
loaded into a fresh emulator instance, and run to completion.
"""

from __future__ import annotations

import io

from lm1.testing.harness import test
from lm1.decode import Op, encode_i, encode_r, encode_b, encode_x
from lm1.execute import Emulator, EMU_TRAP_PUTCHAR
from lm1.word import tag_fixnum, untag_fixnum, T, NIL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emu(**kwargs) -> Emulator:
    """Create a small emulator with captured stdout."""
    stdout = kwargs.pop("stdout", io.StringIO())
    return Emulator(mem_size=64 * 1024, stdout=stdout, **kwargs)


def _emu_with_output(**kwargs) -> tuple[Emulator, io.StringIO]:
    """Create emulator + its captured output buffer."""
    buf = io.StringIO()
    emu = Emulator(mem_size=64 * 1024, stdout=buf, **kwargs)
    return emu, buf


def _load_and_run(emu: Emulator, program: list[int], **kwargs) -> None:
    emu.mem.load_instructions(0, program)
    emu.thread.pc = 0
    emu.thread.sp = 64 * 1024
    emu.run(**kwargs)


# ===================================================================
# Batch: phase1_io
# ===================================================================

@test("print_lm1", batch="phase1_io")
def test_print_lm1():
    """Prints 'LM-1\\n' via TRAP #0x80 (putchar)."""
    instructions: list[int] = []
    for ch in "LM-1\n":
        val = ord(ch) << 1  # tagged fixnum
        instructions.append(encode_i(Op.LI, 1, 0, val & 0xFFFF))
        instructions.append(encode_x(Op.TRAP, EMU_TRAP_PUTCHAR))
    instructions.append(encode_x(Op.HALT_NOP, 0))

    emu, out = _emu_with_output()
    _load_and_run(emu, instructions)

    result = out.getvalue()
    assert result == "LM-1\n", f"Expected 'LM-1\\n', got {result!r}"


@test("print_empty", batch="phase1_io")
def test_print_empty():
    """Just HALT — should produce no output."""
    emu, out = _emu_with_output()
    _load_and_run(emu, [encode_x(Op.HALT_NOP, 0)])
    assert out.getvalue() == "", f"Expected empty output, got {out.getvalue()!r}"


# ===================================================================
# Batch: phase1_scalar
# ===================================================================

@test("scalar_add_sub_mul", batch="phase1_scalar")
def test_scalar_add_sub_mul():
    """Raw ADD, SUB, MUL on untagged 64-bit values."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 42),
        encode_i(Op.LI, 2, 0, 10),
        encode_r(Op.ARITH_RAW, 3, 1, 2, 0),   # ADD → 52
        encode_r(Op.ARITH_RAW, 4, 1, 2, 1),   # SUB → 32
        encode_r(Op.ARITH_RAW, 5, 1, 2, 2),   # MUL → 420
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[3] == 52
    assert emu.thread.regs[4] == 32
    assert emu.thread.regs[5] == 420


@test("scalar_div_mod", batch="phase1_scalar")
def test_scalar_div_mod():
    """Raw DIV and MOD."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 100),
        encode_i(Op.LI, 2, 0, 7),
        encode_r(Op.ARITH_RAW, 3, 1, 2, 3),   # DIV → 14
        encode_r(Op.ARITH_RAW, 4, 1, 2, 4),   # MOD → 2
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[3] == 14
    assert emu.thread.regs[4] == 2


@test("bitwise_and_or_xor", batch="phase1_scalar")
def test_bitwise():
    """AND, OR, XOR."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 0xFF),
        encode_i(Op.LI, 2, 0, 0x0F),
        encode_r(Op.BITWISE, 3, 1, 2, 0),   # AND → 0x0F
        encode_r(Op.BITWISE, 4, 1, 2, 1),   # OR  → 0xFF
        encode_r(Op.BITWISE, 5, 1, 2, 2),   # XOR → 0xF0
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[3] == 0x0F
    assert emu.thread.regs[4] == 0xFF
    assert emu.thread.regs[5] == 0xF0


@test("bitwise_shifts", batch="phase1_scalar")
def test_shifts():
    """SHL, SHR."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 0xFF),
        encode_i(Op.LI, 2, 0, 4),
        encode_r(Op.BITWISE, 3, 1, 2, 3),   # SHL → 0xFF0
        encode_r(Op.BITWISE, 4, 1, 2, 4),   # SHR → 0x0F
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[3] == 0xFF0
    assert emu.thread.regs[4] == 0x0F


@test("bitwise_not", batch="phase1_scalar")
def test_not():
    """NOT (bitwise complement)."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 0),
        encode_r(Op.BITWISE, 2, 1, 0, 6),   # NOT 0 → all ones
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[2] == 0xFFFF_FFFF_FFFF_FFFF


@test("li_sign_extend", batch="phase1_scalar")
def test_li_sign_extend():
    """LI with negative immediate (sign extension)."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, (-1) & 0xFFFF),     # LI r1, -1
        encode_i(Op.LI, 2, 0, (-100) & 0xFFFF),   # LI r2, -100
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[1] == 0xFFFF_FFFF_FFFF_FFFF, f"got {emu.thread.regs[1]:#x}"
    assert emu.thread.regs[2] == ((-100) & 0xFFFF_FFFF_FFFF_FFFF), f"got {emu.thread.regs[2]:#x}"


@test("lui", batch="phase1_scalar")
def test_lui():
    """LUI sets bits 31:16."""
    emu = _emu()
    program = [
        encode_i(Op.LUI, 1, 0, 0xABCD),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[1] == 0xABCD_0000, f"got {emu.thread.regs[1]:#x}"


@test("li_lui_compose", batch="phase1_scalar")
def test_li_lui_compose():
    """Compose a 32-bit value from LI + LUI + OR."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 0x5678),
        encode_i(Op.LUI, 2, 0, 0x1234),
        encode_r(Op.BITWISE, 1, 1, 2, 1),   # OR → 0x1234_5678
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[1] == 0x1234_5678


# ===================================================================
# Batch: phase1_memory
# ===================================================================

@test("load_store_word", batch="phase1_memory")
def test_load_store_word():
    """STR then LDR round-trips a 64-bit value."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 0x5678),
        encode_i(Op.LUI, 2, 0, 0x1234),
        encode_r(Op.BITWISE, 1, 1, 2, 1),    # r1 = 0x1234_5678
        encode_i(Op.LI, 10, 0, 0x1000),      # r10 = data address
        encode_i(Op.STR, 10, 1, 0),           # mem[r10] = r1
        encode_i(Op.LI, 1, 0, 0),             # clear r1
        encode_i(Op.LDR, 3, 10, 0),           # r3 = mem[r10]
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[3] == 0x1234_5678


@test("load_store_offset", batch="phase1_memory")
def test_load_store_offset():
    """STR/LDR with non-zero offset."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 0x3EEF),        # fits in 15 bits (positive)
        encode_i(Op.LI, 10, 0, 0x1000),
        encode_i(Op.STR, 10, 1, 8),           # mem[r10+8] = r1
        encode_i(Op.LDR, 3, 10, 8),           # r3 = mem[r10+8]
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[3] == 0x3EEF


# ===================================================================
# Batch: phase1_branch
# ===================================================================

@test("branch_unconditional", batch="phase1_branch")
def test_br_unconditional():
    """BR skips over an instruction."""
    emu, out = _emu_with_output()
    program = [
        # 0x00: BR +3     → skip to 0x0C
        encode_b(Op.BR, 0, 0, 3),
        # 0x04: LI r1, 84  (should be skipped)
        encode_i(Op.LI, 1, 0, 84),
        # 0x08: TRAP putchar (should be skipped)
        encode_x(Op.TRAP, EMU_TRAP_PUTCHAR),
        # 0x0C: HALT
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert out.getvalue() == "", "Should have skipped the print"


@test("branch_loop_countdown", batch="phase1_branch")
def test_branch_loop():
    """Count down from 5, printing '*' each time → '*****'."""
    emu, out = _emu_with_output()
    program = [
        # 0x00: LI r10, 10    (fixnum 5)
        encode_i(Op.LI, 10, 0, 10),
        # 0x04: LI r11, 2     (fixnum 1)
        encode_i(Op.LI, 11, 0, 2),
        # 0x08: LI r1, 84     ('*' as fixnum)
        encode_i(Op.LI, 1, 0, 84),
        # 0x0C: BR.FIX.EQ r10, +5   → if r10==0 goto HALT
        encode_b(Op.BR_COND, 10, 3, 5),
        # 0x10: TRAP putchar
        encode_x(Op.TRAP, EMU_TRAP_PUTCHAR),
        # 0x14: SUB r10, r10, r11
        encode_r(Op.ARITH_RAW, 10, 10, 11, 1),
        # 0x18: BR -3   → back to 0x0C
        encode_b(Op.BR, 0, 0, (-3) & 0xFFFF),
        # 0x1C: NOP
        encode_x(Op.HALT_NOP, 1 << 21),
        # 0x20: HALT
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert out.getvalue() == "*****"


@test("branch_br_t", batch="phase1_branch")
def test_br_t():
    """BR.T: branch taken on truthy, not taken on nil/0."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, T & 0xFFFF),
        # BR.T r1, +2  → skip next insn
        encode_b(Op.BR_COND, 1, 0, 2),
        # r5 = 999 (should be skipped)
        encode_i(Op.LI, 5, 0, 999),
        # r6 = 1 (marker that we got here)
        encode_i(Op.LI, 6, 0, 1),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[5] == 0, "BR.T should have skipped LI r5"
    assert emu.thread.regs[6] == 1, "Should have reached LI r6"


@test("branch_br_nil", batch="phase1_branch")
def test_br_nil():
    """BR.NIL: branch taken on nil."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, NIL & 0xFFFF),
        # BR.NIL r1, +2  → skip next insn
        encode_b(Op.BR_COND, 1, 1, 2),
        encode_i(Op.LI, 5, 0, 999),
        encode_i(Op.LI, 6, 0, 1),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[5] == 0, "BR.NIL should have skipped LI r5"
    assert emu.thread.regs[6] == 1


# ===================================================================
# Batch: phase1_tagged
# ===================================================================

@test("tagged_add_fix", batch="phase1_tagged")
def test_add_fix():
    """ADD.FIX: 30 + 12 = 42 (as tagged fixnums)."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, tag_fixnum(30) & 0xFFFF),
        encode_i(Op.LI, 2, 0, tag_fixnum(12) & 0xFFFF),
        encode_r(Op.ARITH_FIX, 3, 1, 2, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert untag_fixnum(emu.thread.regs[3]) == 42


@test("tagged_sub_fix", batch="phase1_tagged")
def test_sub_fix():
    """SUB.FIX: 30 - 12 = 18."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, tag_fixnum(30) & 0xFFFF),
        encode_i(Op.LI, 2, 0, tag_fixnum(12) & 0xFFFF),
        encode_r(Op.ARITH_FIX, 3, 1, 2, 1),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert untag_fixnum(emu.thread.regs[3]) == 18


@test("tagged_add_fix_imm", batch="phase1_tagged")
def test_add_fix_imm():
    """ADD.FIX.IMM: 10 + imm(5) = 15."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, tag_fixnum(10) & 0xFFFF),
        encode_i(Op.ADD_FIX_IMM, 2, 1, tag_fixnum(5) & 0xFFFF),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert untag_fixnum(emu.thread.regs[2]) == 15


@test("tst_fixnum", batch="phase1_tagged")
def test_tst_fixnum():
    """TST: fixnum predicate."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, tag_fixnum(42) & 0xFFFF),
        encode_i(Op.TST, 2, 1, 0),   # TAG_FIXNUM → T
        encode_i(Op.TST, 3, 1, 1),   # TAG_REF    → NIL
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[2] == T
    assert emu.thread.regs[3] == NIL


@test("tst_nil", batch="phase1_tagged")
def test_tst_nil():
    """TST: nil predicate."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, NIL & 0xFFFF),
        encode_i(Op.TST, 2, 1, 4),   # TAG_NIL → T
        encode_i(Op.TST, 3, 1, 0),   # TAG_FIXNUM → NIL (nil is not fixnum)
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[2] == T
    assert emu.thread.regs[3] == NIL


@test("eq_identity", batch="phase1_tagged")
def test_eq():
    """EQ: word-level identity."""
    emu = _emu()
    program = [
        encode_i(Op.LI, 1, 0, 42),
        encode_i(Op.LI, 2, 0, 42),
        encode_i(Op.LI, 3, 0, 99),
        encode_r(Op.CMP_TAGGED, 4, 1, 2, 1),   # EQ → T
        encode_r(Op.CMP_TAGGED, 5, 1, 3, 1),   # EQ → NIL
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[4] == T
    assert emu.thread.regs[5] == NIL


# ===================================================================
# Batch: phase1_system
# ===================================================================

@test("halt", batch="phase1_system")
def test_halt():
    """HALT stops execution."""
    emu = _emu()
    _load_and_run(emu, [encode_x(Op.HALT_NOP, 0)])
    assert emu.thread.halted


@test("nop", batch="phase1_system")
def test_nop():
    """NOP followed by HALT."""
    emu = _emu()
    program = [
        encode_x(Op.HALT_NOP, 1 << 21),   # NOP
        encode_x(Op.HALT_NOP, 1 << 21),   # NOP
        encode_x(Op.HALT_NOP, 0),          # HALT
    ]
    _load_and_run(emu, program)
    assert emu.thread.halted
    assert emu.instruction_count == 3


@test("cycle_count", batch="phase1_system")
def test_cycle_count():
    """CYCLE returns a non-zero value after some instructions."""
    emu = _emu()
    program = [
        encode_x(Op.HALT_NOP, 1 << 21),   # NOP
        encode_x(Op.HALT_NOP, 1 << 21),   # NOP
        encode_x(Op.HALT_NOP, 1 << 21),   # NOP
        # SYS_INFO: Rd=r1, sub=2 (CYCLE)
        encode_i(Op.SYS_INFO, 1, 2, 0),
        encode_x(Op.HALT_NOP, 0),
    ]
    _load_and_run(emu, program)
    assert emu.thread.regs[1] >= 3, f"CYCLE: expected >= 3, got {emu.thread.regs[1]}"


@test("max_instructions_limit", batch="phase1_system", timeout=5.0)
def test_max_instructions():
    """max_instructions parameter stops execution."""
    emu = _emu()
    # Infinite NOP loop: NOP then BR -1
    program = [
        encode_x(Op.HALT_NOP, 1 << 21),       # NOP
        encode_b(Op.BR, 0, 0, (-1) & 0xFFFF), # BR -1 (back to NOP)
    ]
    _load_and_run(emu, program, max_instructions=100)
    assert emu.instruction_count == 100
    assert not emu.thread.halted
