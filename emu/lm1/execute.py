"""LM-1 instruction executor.

Phase 1: scalar ops, raw loads/stores, branches, LI/LUI, NOP, HALT,
TILE.ID, THREAD.ID, CYCLE, console I/O traps, PUSH/POP.

Implements the fetch-decode-execute loop for a single tile / single thread.
"""

from __future__ import annotations

import sys
from typing import Optional, TextIO

from .word import (
    WORD_MASK, SIGN_BIT, NIL, T,
    is_fixnum, tag_fixnum, untag_fixnum,
    is_truthy, u64, s64, add64, sub64,
)
from .decode import (
    Op, Instruction, decode,
    FUNC_ADD, FUNC_SUB, FUNC_MUL, FUNC_DIV, FUNC_MOD,
    FUNC_AND, FUNC_OR, FUNC_XOR, FUNC_SHL, FUNC_SHR, FUNC_ASR, FUNC_NOT,
    FUNC_ADD_FIX, FUNC_SUB_FIX, FUNC_MUL_FIX, FUNC_DIV_FIX,
    FUNC_CMP, FUNC_EQ,
    BR_T, BR_NIL, BR_FIX_LT, BR_FIX_EQ, BR_FIX_GT, BR_EQ,
    FUNC_PUSH, FUNC_POP,
    FUNC_TILE_ID, FUNC_THREAD_ID, FUNC_CYCLE,
    FUNC_HALT, FUNC_NOP,
)
from .traps import (
    LM1Trap,
    TRAP_NOT_FIXNUM, TRAP_FIXNUM_OVERFLOW, TRAP_DIVIDE_BY_ZERO,
    TRAP_TYPE_MISMATCH, TRAP_STACK_UNDERFLOW,
    TRAP_UNIMPLEMENTED,
    trap_name,
)
from .core import ThreadContext
from .memory import Memory


# ---------------------------------------------------------------------------
# Emulator I/O trap codes (emulator-specific, per design/emulator.md § 7)
# ---------------------------------------------------------------------------
EMU_TRAP_PUTCHAR   = 0x80
EMU_TRAP_GETCHAR   = 0x81
EMU_TRAP_BLOCK_IO  = 0x82


class Emulator:
    """Single-tile, single-thread LM-1 functional emulator (Phase 1)."""

    def __init__(
        self,
        mem_size: int = 4 * 1024 * 1024,  # 4 MiB default
        *,
        trace: bool = False,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ):
        self.mem = Memory(mem_size)
        self.thread = ThreadContext()

        # I/O
        self._stdin: TextIO = stdin or sys.stdin
        self._stdout: TextIO = stdout or sys.stdout
        self.trace = trace

        # Stats
        self.instruction_count = 0

    # -- Register / memory convenience ---

    def reg(self, idx: int) -> int:
        return self.thread.regs[idx & 0x1F]

    def set_reg(self, idx: int, val: int) -> None:
        self.thread.regs[idx & 0x1F] = val & WORD_MASK

    # -- Main loop ---

    def run(self, max_instructions: int = 0) -> None:
        """Run until HALT or max_instructions reached (0 = unlimited)."""
        t = self.thread
        mem = self.mem
        count = 0

        while not t.halted:
            # Fetch
            raw = mem.load_u32(t.pc)
            inst = decode(raw)

            if self.trace:
                self._trace_instruction(t.pc, inst)

            # Advance PC (may be overwritten by branches)
            next_pc = t.pc + 4

            # Execute
            next_pc = self._execute(inst, next_pc)

            t.pc = next_pc
            t.cycle_count += 1
            self.instruction_count += 1
            count += 1

            if max_instructions and count >= max_instructions:
                break

    # -- Execute dispatch ---

    def _execute(self, inst: Instruction, next_pc: int) -> int:
        """Execute one decoded instruction.  Returns the next PC."""
        op = inst.opcode
        t = self.thread

        # ---- Scalar arithmetic (raw, untagged 64-bit) ----
        if op == Op.ARITH_RAW:
            a = t.regs[inst.rs1]
            b = t.regs[inst.rs2]
            match inst.func:
                case 0:  # ADD
                    t.regs[inst.rd] = (a + b) & WORD_MASK
                case 1:  # SUB
                    t.regs[inst.rd] = (a - b) & WORD_MASK
                case 2:  # MUL
                    t.regs[inst.rd] = (a * b) & WORD_MASK
                case 3:  # DIV
                    if b == 0:
                        raise LM1Trap(TRAP_DIVIDE_BY_ZERO)
                    # Unsigned division for raw
                    t.regs[inst.rd] = (a // b) & WORD_MASK
                case 4:  # MOD
                    if b == 0:
                        raise LM1Trap(TRAP_DIVIDE_BY_ZERO)
                    t.regs[inst.rd] = (a % b) & WORD_MASK

        # ---- Bitwise ----
        elif op == Op.BITWISE:
            a = t.regs[inst.rs1]
            b = t.regs[inst.rs2]
            match inst.func:
                case 0:  # AND
                    t.regs[inst.rd] = a & b
                case 1:  # OR
                    t.regs[inst.rd] = a | b
                case 2:  # XOR
                    t.regs[inst.rd] = a ^ b
                case 3:  # SHL
                    shift = b & 63
                    t.regs[inst.rd] = (a << shift) & WORD_MASK
                case 4:  # SHR (logical)
                    shift = b & 63
                    t.regs[inst.rd] = a >> shift
                case 5:  # ASR (arithmetic)
                    shift = b & 63
                    t.regs[inst.rd] = u64(s64(a) >> shift)
                case 6:  # NOT
                    t.regs[inst.rd] = (~a) & WORD_MASK

        # ---- Raw loads (LDR) ----
        elif op == Op.LDR:
            base = t.regs[inst.rs1]
            offset = inst.imm16  # signed, byte offset
            addr = (base + offset) & WORD_MASK
            # Default: 64-bit (dword) load
            t.regs[inst.rd] = self.mem.load_word(addr & ~7)

        # ---- Raw stores (STR) ----
        elif op == Op.STR:
            base = t.regs[inst.rd]   # Format S: Rs is in rd position
            val = t.regs[inst.rs1]   # Rt is in rs1 position
            offset = inst.imm16
            addr = (base + offset) & WORD_MASK
            self.mem.store_word(addr & ~7, val)

        # ---- LI (load immediate) ----
        elif op == Op.LI:
            # imm16 is sign-extended to 64 bits
            t.regs[inst.rd] = inst.imm16 & WORD_MASK

        # ---- LUI (load upper immediate) ----
        elif op == Op.LUI:
            # Load imm16 into bits 31:16, zero other bits
            t.regs[inst.rd] = (inst.imm16 & 0xFFFF) << 16

        # ---- Unconditional branch ----
        elif op == Op.BR:
            offset = inst.imm16  # in words
            next_pc = t.pc + (offset * 4)

        # ---- Conditional branches ----
        elif op == Op.BR_COND:
            # B-format: bits 25:21 = Rs1 (register), bits 20:16 = cond
            # Decoder maps 25:21 → rd, 20:16 → rs1
            cond = inst.rs1   # condition type in bits 20:16
            val1 = t.regs[inst.rd]  # register in bits 25:21
            offset = inst.imm16
            taken = False

            match cond:
                case 0:  # BR.T — branch if truthy
                    taken = is_truthy(val1)
                case 1:  # BR.NIL — branch if nil
                    taken = (val1 == NIL)
                case 2:  # BR.FIX.LT — needs second reg; we repurpose func field
                    # For 2-register cond branches, val2 is in a register
                    # specified differently. Let's use: Rs1 = first, 
                    # val2 from the word at func field as register index.
                    # Actually, per the B format, Rs1 and Rs2 ARE both register
                    # fields — but Rs2 is being used as condition selector.
                    # 
                    # Resolution: For BR.FIX.LT/EQ/GT and BR.EQ which need
                    # two registers, we'll use a different encoding scheme:
                    # Rs1 bits 25:21 = register, bits 20:16 encode BOTH
                    # the condition AND the second register.
                    # 
                    # Simpler approach for now: single-register conditions only.
                    # Two-register comparisons use CMP.TAGGED first.
                    taken = is_fixnum(val1) and s64(val1) < 0
                case 3:  # BR.FIX.EQ (== 0)
                    taken = val1 == 0
                case 4:  # BR.FIX.GT (> 0)
                    taken = is_fixnum(val1) and s64(val1) > 0 and val1 != 0
                case 5:  # BR.EQ — word-equal to zero
                    taken = val1 == 0

            if taken:
                next_pc = t.pc + (offset * 4)

        # ---- PUSH / POP ----
        elif op == Op.PUSH_POP:
            func = (inst.imm16 >> 14) & 3 if inst.imm16 != 0 else inst.func
            # Simple encoding: rd specifies register, func selects push/pop
            match inst.func:
                case 0:  # PUSH
                    t.sp = (t.sp - 8) & WORD_MASK
                    self.mem.store_word(t.sp, t.regs[inst.rd])
                case 1:  # POP
                    t.regs[inst.rd] = self.mem.load_word(t.sp)
                    t.sp = (t.sp + 8) & WORD_MASK

        # ---- TRAP ----
        elif op == Op.TRAP:
            trap_code = inst.raw26 & 0xFF
            self._handle_trap(trap_code)

        # ---- ERET ----
        elif op == Op.ERET:
            # Phase 1: not implemented (no trap handlers yet)
            raise LM1Trap(TRAP_UNIMPLEMENTED, "ERET not implemented in Phase 1")

        # ---- System info ----
        elif op == Op.SYS_INFO:
            func = inst.rd  # overloaded: func is actually in bits 25:21
            match func:
                case 0:  # TILE.ID  — result in rs1 field as destination
                    dest = inst.rs1
                    t.regs[dest] = t.tile_id
                case 1:  # THREAD.ID
                    dest = inst.rs1
                    t.regs[dest] = t.thread_id
                case 2:  # CYCLE
                    dest = inst.rs1
                    t.regs[dest] = t.cycle_count & WORD_MASK
                case _:
                    pass
            # Alternative simpler encoding: Rd is the destination
            #   Actually let's just use Rd (bits 25:21) as dest and
            #   func to select.
            #   Re-reading the encoding spec: SYS_INFO uses Format X
            #   opcode=111110, "func selects which"
            #   Let's use: bits 25:21 = Rd, bits 20:16 = sub-function  
            # Fixing:
            sub = inst.rs1  # sub-function in rs1 position
            match sub:
                case 0:  t.regs[inst.rd] = t.tile_id
                case 1:  t.regs[inst.rd] = t.thread_id
                case 2:  t.regs[inst.rd] = t.cycle_count & WORD_MASK

        # ---- HALT / NOP ----
        elif op == Op.HALT_NOP:
            sub = (inst.raw26 >> 21) & 0x1F
            if sub == 0:   # HALT
                t.halted = True
            # else: NOP — do nothing

        # ---- Tagged arithmetic (Phase 1 includes these) ----
        elif op == Op.ARITH_FIX:
            a = t.regs[inst.rs1]
            b = t.regs[inst.rs2]
            if not is_fixnum(a) or not is_fixnum(b):
                raise LM1Trap(TRAP_NOT_FIXNUM)
            match inst.func:
                case 0:  # ADD.FIX
                    result, overflow = add64(a, b)
                    if overflow:
                        raise LM1Trap(TRAP_FIXNUM_OVERFLOW)
                    t.regs[inst.rd] = result
                case 1:  # SUB.FIX
                    result, overflow = sub64(a, b)
                    if overflow:
                        raise LM1Trap(TRAP_FIXNUM_OVERFLOW)
                    t.regs[inst.rd] = result
                case 2:  # MUL.FIX
                    va = untag_fixnum(a)
                    # result = va * b  (b is still tagged)
                    result = (va * b) & WORD_MASK
                    # Check overflow: if untagged result doesn't round-trip
                    check = untag_fixnum(result)
                    if tag_fixnum(check) != result:
                        raise LM1Trap(TRAP_FIXNUM_OVERFLOW)
                    t.regs[inst.rd] = result
                case 3:  # DIV.FIX
                    if b == 0:
                        raise LM1Trap(TRAP_DIVIDE_BY_ZERO)
                    va = untag_fixnum(a)
                    vb = untag_fixnum(b)
                    if vb == 0:
                        raise LM1Trap(TRAP_DIVIDE_BY_ZERO)
                    # Python's // truncates toward negative infinity;
                    # use int() to truncate toward zero
                    result = int(va / vb)
                    t.regs[inst.rd] = tag_fixnum(result)

        elif op == Op.ADD_FIX_IMM:
            a = t.regs[inst.rs1]
            imm = inst.imm16 & WORD_MASK  # pre-tagged fixnum
            if not is_fixnum(a):
                raise LM1Trap(TRAP_NOT_FIXNUM)
            result, overflow = add64(a, imm)
            if overflow:
                raise LM1Trap(TRAP_FIXNUM_OVERFLOW)
            t.regs[inst.rd] = result

        elif op == Op.CMP_TAGGED:
            a = t.regs[inst.rs1]
            b = t.regs[inst.rs2]
            match inst.func:
                case 0:  # CMP.TAGGED
                    if is_fixnum(a) and is_fixnum(b):
                        va, vb = s64(a), s64(b)
                        if va < vb:
                            t.regs[inst.rd] = tag_fixnum(-1)
                        elif va == vb:
                            t.regs[inst.rd] = tag_fixnum(0)
                        else:
                            t.regs[inst.rd] = tag_fixnum(1)
                    elif (a & 7) == (b & 7):
                        # Same primary tag — identity comparison
                        t.regs[inst.rd] = tag_fixnum(0) if a == b else tag_fixnum(1)
                    else:
                        raise LM1Trap(TRAP_TYPE_MISMATCH)
                case 1:  # EQ (raw word equality)
                    t.regs[inst.rd] = T if a == b else NIL

        # ---- TST (type test) ----
        elif op == Op.TST:
            val = t.regs[inst.rs1]
            tag_const = inst.imm16 & 0x7
            result = False
            match tag_const:
                case 0: result = is_fixnum(val)            # TAG_FIXNUM
                case 1: result = (val & 3) == 1            # TAG_REF
                case 2: result = (val & 7) == 3            # TAG_CONS
                case 3: result = (val & 7) == 5            # TAG_SPECIAL
                case 4: result = val == NIL                 # TAG_NIL
                case 5: result = (val & 0xFF) == 0x35      # TAG_CHAR
                case 6: result = (val & 0xFF) == 0x3D      # TAG_SFLOAT
                case 7: result = (val & 7) == 7            # TAG_HEADER
            t.regs[inst.rd] = T if result else NIL

        # ---- Prefetch (no-ops) ----
        elif op in (Op.PREFETCH_REF, Op.PREFETCH_FLD, Op.PREFETCH_CDR, Op.GATHER_PRE):
            pass  # no-op in emulator

        # ---- Not yet implemented ----
        else:
            raise LM1Trap(TRAP_UNIMPLEMENTED, f"Unimplemented opcode {op} ({op:#04x})")

        return next_pc

    # -- Trap handling (Phase 1: emulator-level I/O traps) ---

    def _handle_trap(self, code: int) -> None:
        """Handle TRAP instruction.

        Phase 1: only emulator I/O traps are handled natively.
        All other traps raise LM1Trap (fatal in Phase 1).
        """
        t = self.thread

        if code == EMU_TRAP_PUTCHAR:
            # r1 holds the character (as a tagged fixnum or char immediate)
            val = t.regs[1]
            if is_fixnum(val):
                ch = untag_fixnum(val) & 0xFF
            elif (val & 0xFF) == 0x35:
                # Character immediate
                ch = (val >> 8) & 0x1F_FFFF
            else:
                ch = val & 0xFF
            self._stdout.write(chr(ch))
            self._stdout.flush()

        elif code == EMU_TRAP_GETCHAR:
            # Read one character, put in r0 as tagged fixnum
            ch = self._stdin.read(1)
            if ch:
                t.regs[0] = tag_fixnum(ord(ch))
            else:
                t.regs[0] = tag_fixnum(-1)  # EOF

        elif code == EMU_TRAP_BLOCK_IO:
            # Phase 1: not implemented
            raise LM1Trap(TRAP_UNIMPLEMENTED, "TRAP_BLOCK_IO not implemented in Phase 1")

        else:
            raise LM1Trap(code, f"Unhandled trap: {trap_name(code)}")

    # -- Trace output ---

    def _trace_instruction(self, pc: int, inst: Instruction) -> None:
        """Print a trace line for the current instruction."""
        try:
            name = Op(inst.opcode).name
        except ValueError:
            name = f"???({inst.opcode:#04x})"
        self._stdout.write(
            f"[{pc:#010x}] {inst.raw:#010x}  {name:16s} "
            f"rd={inst.rd} rs1={inst.rs1} rs2={inst.rs2} "
            f"func={inst.func} imm16={inst.imm16}\n"
        )
