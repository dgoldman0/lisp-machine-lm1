"""LM-1 instruction executor.

Phase 1: scalar ops, raw loads/stores, branches, LI/LUI, NOP, HALT,
TILE.ID, THREAD.ID, CYCLE, console I/O traps, PUSH/POP.

Phase 2: tagged arithmetic (already in Phase 1), nursery allocation,
header template table, tagged field access (LD/ST/LD.CAR/LD.CDR/ST.WB/
ST.CAR/ST.CDR), TST.SHAPE, ALLOC/ALLOC.CONS/ALLOCV/ALLOC.CLOSURE.

Implements the fetch-decode-execute loop for a single tile / single thread.
"""

from __future__ import annotations

import sys
from typing import Optional, TextIO

from .word import (
    WORD_MASK, SIGN_BIT, NIL, T,
    is_fixnum, tag_fixnum, untag_fixnum,
    is_ref, is_cons_ref, is_any_ref, ref_address, make_ref,
    is_header, make_header, header_size, header_shape_id, header_subtype,
    is_truthy, u64, s64, add64, sub64,
    HDR_CONS, HDR_CLOSURE, HDR_VECTOR,
    REF_ADDR_MASK, TAG_REF, TAG_CONS,
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
    TRAP_NURSERY_OVERFLOW, TRAP_NOT_REF,
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

    # Default nursery: 64 KiB at the top of the first 256 KiB
    DEFAULT_NURSERY_BASE  = 0x0003_0000   # 192 KiB offset
    DEFAULT_NURSERY_SIZE  = 0x0001_0000   # 64 KiB

    # Default old-gen: 256 KiB starting at 512 KiB offset
    DEFAULT_OLDGEN_BASE   = 0x0008_0000   # 512 KiB offset
    DEFAULT_OLDGEN_SIZE   = 0x0004_0000   # 256 KiB

    # Card table: one byte per 256-byte card (covers nursery + old-gen)
    CARD_SIZE = 256  # bytes per card

    def __init__(
        self,
        mem_size: int = 4 * 1024 * 1024,  # 4 MiB default
        *,
        trace: bool = False,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        nursery_base: int | None = None,
        nursery_size: int | None = None,
        oldgen_base: int | None = None,
        oldgen_size: int | None = None,
    ):
        self.mem = Memory(mem_size)
        self.thread = ThreadContext()

        # I/O
        self._stdin: TextIO = stdin or sys.stdin
        self._stdout: TextIO = stdout or sys.stdout
        self.trace = trace

        # Nursery region
        self.nursery_base = nursery_base if nursery_base is not None else self.DEFAULT_NURSERY_BASE
        self.nursery_size = nursery_size if nursery_size is not None else self.DEFAULT_NURSERY_SIZE
        # NP = current allocation pointer (bumps upward)
        # NL = nursery limit (first address past the nursery)
        self.thread.np = self.nursery_base
        self.thread.nl = self.nursery_base + self.nursery_size

        # Old-gen region (promotion target for surviving nursery objects)
        self.oldgen_base = oldgen_base if oldgen_base is not None else self.DEFAULT_OLDGEN_BASE
        self.oldgen_size = oldgen_size if oldgen_size is not None else self.DEFAULT_OLDGEN_SIZE
        self.oldgen_ptr = self.oldgen_base  # bump pointer into old-gen

        # Card table: one byte per CARD_SIZE bytes of heap memory.
        # Covers the entire memory space.  A card is "dirty" (non-zero) when
        # a store-with-barrier writes a nursery ref into an old-gen object.
        self.card_table_size = mem_size // self.CARD_SIZE
        self.card_table = bytearray(self.card_table_size)

        # GC statistics
        self.gc_count = 0

        # Install default header templates (index 0 = cons)
        self._init_header_templates()

        # Stats
        self.instruction_count = 0

    def _init_header_templates(self) -> None:
        """Set up the default header-template table entries."""
        t = self.thread
        # Index 0: Cons cell header (hdr_sub=1, size=2, shape_id=0)
        t.header_templates[0] = make_header(HDR_CONS, 2, 0)
        # Index 1: Closure header template (hdr_sub=4, size=0 — filled at alloc)
        t.header_templates[1] = make_header(HDR_CLOSURE, 0, 1)
        # Index 2: Vector header template (hdr_sub=2, size=0 — filled at alloc)
        t.header_templates[2] = make_header(HDR_VECTOR, 0, 2)

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

            try:
                # Execute
                next_pc = self._execute(inst, next_pc)
            except LM1Trap as trap:
                # Phase 3: handle traps

                # Special case: nursery overflow → run GC at emulator level
                if trap.code == TRAP_NURSERY_OVERFLOW:
                    self._gc_collect()
                    # Retry the faulting instruction (don't advance PC)
                    next_pc = t.pc
                # Dispatch through trap table if installed
                elif t.trap_table_base != 0 and trap.code < 0x80:
                    # Save state for ERET — save PC of *faulting* instruction.
                    # For software TRAP: handler should ERET to trap_pc+4.
                    # For faults (NURSERY_OVERFLOW etc): ERET retries the insn.
                    t.trap_pc = t.pc
                    t.trap_cause = trap.code
                    t.in_trap = True
                    # Look up handler address: trap_table[code] is a 64-bit
                    # word containing the handler PC
                    handler_addr = mem.load_word(
                        t.trap_table_base + trap.code * 8
                    )
                    if handler_addr == 0:
                        # No handler installed for this trap — fatal
                        raise
                    next_pc = handler_addr
                else:
                    # Emulator traps (>= 0x80) or no trap table — fatal
                    raise

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
            match inst.func:
                case 0:  # PUSH
                    t.sp = (t.sp - 8) & WORD_MASK
                    self.mem.store_word(t.sp, t.regs[inst.rd])
                case 1:  # POP
                    t.regs[inst.rd] = self.mem.load_word(t.sp)
                    t.sp = (t.sp + 8) & WORD_MASK

        # ---- PUSH.MULTI (Format I: rd=bank, imm16=mask) ----
        elif op == Op.PUSH_MULTI:
            bank = inst.rd
            mask = inst.imm16 & 0xFFFF
            base_reg = bank * 16
            for i in range(16):
                if mask & (1 << i):
                    t.sp = (t.sp - 8) & WORD_MASK
                    self.mem.store_word(t.sp, t.regs[base_reg + i])

        # ---- POP.MULTI (Format I: rd=bank, imm16=mask) ----
        elif op == Op.POP_MULTI:
            bank = inst.rd
            mask = inst.imm16 & 0xFFFF
            base_reg = bank * 16
            for i in range(15, -1, -1):
                if mask & (1 << i):
                    t.regs[base_reg + i] = self.mem.load_word(t.sp)
                    t.sp = (t.sp + 8) & WORD_MASK

        # ---- TRAP ----
        elif op == Op.TRAP:
            trap_code = inst.raw26 & 0xFF
            self._handle_trap(trap_code)

        # ---- ERET ----
        elif op == Op.ERET:
            # Restore PC from trap state and resume normal execution
            if not t.in_trap:
                raise LM1Trap(TRAP_UNIMPLEMENTED, "ERET outside of trap handler")
            t.in_trap = False
            next_pc = t.trap_pc

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
                case 3:  t.regs[inst.rd] = t.trap_cause    # TRAP_CAUSE
                case 4:  t.regs[inst.rd] = t.trap_pc       # TRAP_PC

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

        # ================================================================
        # Phase 2: Allocation
        # ================================================================

        # ---- ALLOC Rd, #words, #header_template ----
        elif op == Op.ALLOC:
            rd = (inst.raw26 >> 21) & 0x1F
            n_words = (inst.raw26 >> 16) & 0x1F
            tmpl_idx = inst.raw26 & 0xFFFF
            total_bytes = (1 + n_words) * 8  # header + payload
            self._alloc_object(t, rd, total_bytes, tmpl_idx, n_words)

        # ---- ALLOC.CONS Rd ----
        elif op == Op.ALLOC_CONS:
            rd = (inst.raw26 >> 21) & 0x1F
            total_bytes = 3 * 8  # header + car + cdr = 24 bytes
            self._alloc_cons(t, rd)

        # ---- ALLOCV Rd, Rs_length, #header_template ----
        elif op == Op.ALLOCV:
            rd = (inst.raw26 >> 21) & 0x1F
            rs_len = (inst.raw26 >> 16) & 0x1F
            tmpl_idx = inst.raw26 & 0xFFFF
            length = t.regs[rs_len]
            if not is_fixnum(length):
                raise LM1Trap(TRAP_NOT_FIXNUM, "ALLOCV: length must be a fixnum")
            n_elems = untag_fixnum(length)
            # Vector layout: header + length_word + elements
            n_words = 1 + n_elems  # length word + elements
            total_bytes = (1 + n_words) * 8  # header + payload
            self._alloc_object(t, rd, total_bytes, tmpl_idx, n_words,
                               init_fn=lambda addr: self.mem.store_word(addr + 8, length))

        # ---- ALLOC.CLOSURE Rd, Rs_code, #env_size ----
        elif op == Op.ALLOC_CLOSURE:
            rd = (inst.raw26 >> 21) & 0x1F
            rs_code = (inst.raw26 >> 16) & 0x1F
            env_size = (inst.raw26 >> 11) & 0x1F
            code_ptr = t.regs[rs_code]
            n_words = 1 + env_size  # code_entry + env slots
            total_bytes = (1 + n_words) * 8
            self._alloc_object(t, rd, total_bytes, 1, n_words,  # template 1 = closure
                               init_fn=lambda addr: self.mem.store_word(addr + 8, code_ptr))

        # ================================================================
        # Phase 2: Tagged Field Access
        # ================================================================

        # ---- LD Rd, Rs, #field ----
        elif op == Op.LD:
            obj_ref = t.regs[inst.rs1]
            if not is_any_ref(obj_ref):
                raise LM1Trap(TRAP_NOT_REF, "LD: source is not a ref")
            field_idx = inst.imm16 & 0x1F
            addr = ref_address(obj_ref)
            # Skip header: field 0 is at offset +8
            t.regs[inst.rd] = self.mem.load_word(addr + (field_idx + 1) * 8)

        # ---- LD.CAR / LD.CDR ----
        elif op == Op.LD_CAR_CDR:
            obj_ref = t.regs[inst.rs1]
            if not is_any_ref(obj_ref):
                raise LM1Trap(TRAP_NOT_REF, "LD.CAR/CDR: source is not a ref")
            addr = ref_address(obj_ref)
            selector = inst.imm16 & 1  # 0 = car, 1 = cdr
            # car at offset +8, cdr at offset +16
            t.regs[inst.rd] = self.mem.load_word(addr + (selector + 1) * 8)

        # ---- ST Rs, #field, Rt  (no barrier) ----
        elif op == Op.ST:
            obj_ref = t.regs[inst.rd]  # Format S: Rs in rd position
            val = t.regs[inst.rs1]     # Rt in rs1 position
            field_idx = inst.rs2       # field in rs2 position
            if not is_any_ref(obj_ref):
                raise LM1Trap(TRAP_NOT_REF, "ST: target is not a ref")
            addr = ref_address(obj_ref)
            self.mem.store_word(addr + (field_idx + 1) * 8, val)

        # ---- ST.WB Rs, #field, Rt  (with write barrier) ----
        elif op == Op.ST_WB:
            obj_ref = t.regs[inst.rd]
            val = t.regs[inst.rs1]
            field_idx = inst.rs2
            if not is_any_ref(obj_ref):
                raise LM1Trap(TRAP_NOT_REF, "ST.WB: target is not a ref")
            addr = ref_address(obj_ref)
            self.mem.store_word(addr + (field_idx + 1) * 8, val)
            # Write barrier: if storing a nursery ref into an old-gen object,
            # mark the card table entry for the object's address.
            self._write_barrier(addr, val)

        # ---- ST.CAR / ST.CDR (with write barrier) ----
        elif op == Op.ST_CAR_CDR:
            obj_ref = t.regs[inst.rd]
            val = t.regs[inst.rs1]
            selector = inst.rs2  # field: 0 = car, 1 = cdr
            if not is_any_ref(obj_ref):
                raise LM1Trap(TRAP_NOT_REF, "ST.CAR/CDR: target is not a ref")
            addr = ref_address(obj_ref)
            self.mem.store_word(addr + (selector + 1) * 8, val)
            self._write_barrier(addr, val)

        # ---- TST.SHAPE Rd, Rs, #shape_id ----
        elif op == Op.TST_SHAPE:
            val = t.regs[inst.rs1]
            shape_test = inst.imm16 & 0xFFFF
            if is_any_ref(val):
                addr = ref_address(val)
                hdr = self.mem.load_word(addr)
                if is_header(hdr) and (header_shape_id(hdr) & 0xFFFF) == shape_test:
                    t.regs[inst.rd] = T
                else:
                    t.regs[inst.rd] = NIL
            else:
                t.regs[inst.rd] = NIL

        # ---- Not yet implemented ----
        else:
            raise LM1Trap(TRAP_UNIMPLEMENTED, f"Unimplemented opcode {op} ({op:#04x})")

        return next_pc

    # -- Allocation helpers (Phase 2) ---

    def _write_barrier(self, obj_addr: int, stored_val: int) -> None:
        """Card-table write barrier.

        If *stored_val* is a nursery reference and *obj_addr* lives in old-gen,
        mark the card covering *obj_addr* as dirty so the GC knows to scan it.
        """
        if not is_any_ref(stored_val):
            return
        val_addr = ref_address(stored_val)
        # Is the stored value pointing into the nursery?
        if not (self.nursery_base <= val_addr < self.nursery_base + self.nursery_size):
            return
        # Is the object in old-gen (or at least outside the nursery)?
        if self.nursery_base <= obj_addr < self.nursery_base + self.nursery_size:
            return  # both in nursery — no barrier needed
        card_idx = obj_addr // self.CARD_SIZE
        if card_idx < self.card_table_size:
            self.card_table[card_idx] = 1

    def _bump_alloc(self, t: ThreadContext, total_bytes: int) -> int:
        """Bump-allocate `total_bytes` from the nursery.

        Returns the address of the new object (aligned to 8).
        Raises TRAP_NURSERY_OVERFLOW if the nursery is full.
        """
        addr = t.np
        new_np = addr + total_bytes
        if new_np > t.nl:
            raise LM1Trap(TRAP_NURSERY_OVERFLOW,
                          f"Nursery overflow: need {total_bytes} bytes, "
                          f"have {t.nl - t.np}")
        t.np = new_np
        return addr

    def _alloc_object(self, t: ThreadContext, rd: int, total_bytes: int,
                      tmpl_idx: int, n_words: int,
                      *, init_fn=None) -> None:
        """Generic allocation: bump NP, write header, zero fields, make ref."""
        addr = self._bump_alloc(t, total_bytes)

        # Build header from template, patching in the actual size
        tmpl = t.header_templates[tmpl_idx & 0xFFFF] if tmpl_idx < len(t.header_templates) else 0
        # Patch size into the header (bits 23:8)
        hdr = (tmpl & ~(0xFFFF << 8)) | ((n_words & 0xFFFF) << 8)
        self.mem.store_word(addr, hdr)

        # Zero payload words
        for i in range(1, n_words + 1):
            self.mem.store_word(addr + i * 8, 0)

        # Optional init (e.g., write code pointer for closure, length for vector)
        if init_fn is not None:
            init_fn(addr)

        # Write a ref to the new object into Rd
        t.regs[rd] = make_ref(addr)

    def _alloc_cons(self, t: ThreadContext, rd: int) -> None:
        """Allocate a cons cell (header + car + cdr = 24 bytes)."""
        addr = self._bump_alloc(t, 24)

        # Cons header: template index 0
        hdr = t.header_templates[0]
        self.mem.store_word(addr, hdr)
        # car and cdr default to nil
        self.mem.store_word(addr + 8, NIL)
        self.mem.store_word(addr + 16, NIL)

        # Cons ref (tag = 011)
        t.regs[rd] = make_ref(addr, cons=True)

    # -- Trap handling (Phase 1: emulator-level I/O traps) ---

    # -- GC: Cheney copy collector (Phase 3) ---

    def _is_nursery_addr(self, addr: int) -> bool:
        """Check if an address falls within the nursery."""
        return self.nursery_base <= addr < self.nursery_base + self.nursery_size

    def _forward_ref(self, w: int) -> int:
        """If w is a ref pointing into the nursery, copy the object to old-gen
        (or return the already-forwarded address).  Returns the updated word.
        Non-ref or non-nursery words are returned unchanged."""
        if not is_any_ref(w):
            return w
        addr = ref_address(w)
        if not self._is_nursery_addr(addr):
            return w  # already in old-gen or elsewhere
        tag = w & 7  # preserve original tag (cons vs ref)

        # Check if already forwarded: a forwarded object has its header
        # replaced with a forwarding pointer (a ref with TAG_REF).
        hdr = self.mem.load_word(addr)
        if is_ref(hdr) or is_cons_ref(hdr):
            # Already forwarded — hdr IS the forwarding pointer
            new_addr = ref_address(hdr)
            return new_addr | tag

        # Not yet forwarded — copy the object to old-gen
        if not is_header(hdr):
            # Shouldn't happen — a non-header, non-forwarded word at an obj base
            return w

        n_payload_words = header_size(hdr)
        total_words = 1 + n_payload_words  # header + payload
        total_bytes = total_words * 8

        # Allocate in old-gen
        new_addr = self.oldgen_ptr
        if new_addr + total_bytes > self.oldgen_base + self.oldgen_size:
            raise RuntimeError(
                f"Old-gen overflow during GC: need {total_bytes} bytes, "
                f"have {self.oldgen_base + self.oldgen_size - new_addr}"
            )
        self.oldgen_ptr += total_bytes

        # Copy all words (header + payload)
        for i in range(total_words):
            self.mem.store_word(new_addr + i * 8,
                                self.mem.load_word(addr + i * 8))

        # Install forwarding pointer in the nursery copy
        self.mem.store_word(addr, make_ref(new_addr))

        return new_addr | tag

    def _gc_collect(self) -> None:
        """Cheney copy collector: evacuate live nursery objects to old-gen.

        Roots: all 32 registers + the stack (from SP to stack_top).
        Algorithm:
          1. Scan roots, forward any nursery refs → copies to old-gen
          2. Scan the copied objects in old-gen (Cheney scan pointer),
             forwarding any nursery refs found in their fields
          3. Scan dirty card table entries for old-gen objects that
             point back into the nursery  (remembered set)
          4. Reset the nursery
        """
        t = self.thread
        mem = self.mem
        scan_start = self.oldgen_ptr  # where new copies start

        # --- Phase A: forward roots ---

        # 1. Registers
        for i in range(32):
            t.regs[i] = self._forward_ref(t.regs[i])

        # 2. Stack (SP to stack base)
        # Stack grows downward.  We scan from SP up to the initial stack top.
        # We don't know the exact stack base, so scan up to a reasonable limit.
        # Convention: stack area is just below nursery_base (e.g. 0x20000-0x30000)
        # Actually let's be safe: scan from SP up to nursery_base (exclusive).
        stack_scan_limit = self.nursery_base
        sp = t.sp
        while sp < stack_scan_limit:
            w = mem.load_word(sp)
            mem.store_word(sp, self._forward_ref(w))
            sp += 8

        # 3. Dirty cards: scan old-gen objects that have dirty card entries
        #    (these contain cross-generational pointers)
        og_card_start = self.oldgen_base // self.CARD_SIZE
        og_card_end = min(
            (self.oldgen_base + self.oldgen_size) // self.CARD_SIZE,
            self.card_table_size
        )
        for card_idx in range(og_card_start, og_card_end):
            if self.card_table[card_idx]:
                # Scan all words in this card
                card_addr = card_idx * self.CARD_SIZE
                card_end = card_addr + self.CARD_SIZE
                a = card_addr
                while a < card_end:
                    w = mem.load_word(a)
                    mem.store_word(a, self._forward_ref(w))
                    a += 8
                self.card_table[card_idx] = 0  # clear dirty bit

        # --- Phase B: Cheney scan — process copied objects ---
        scan_ptr = scan_start
        while scan_ptr < self.oldgen_ptr:
            w = mem.load_word(scan_ptr)
            if is_header(w):
                # Skip the header itself, but forward all payload words
                n_words = header_size(w)
                for i in range(1, n_words + 1):
                    field_addr = scan_ptr + i * 8
                    fv = mem.load_word(field_addr)
                    mem.store_word(field_addr, self._forward_ref(fv))
                scan_ptr += (1 + n_words) * 8
            else:
                # Not a header — shouldn't happen in well-formed heap
                scan_ptr += 8

        # --- Phase C: reset nursery ---
        t.np = self.nursery_base
        # NL stays the same (nursery limit unchanged)

        self.gc_count += 1

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
