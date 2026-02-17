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
    TRAP_IC_MISS, TRAP_NOT_CLOSURE,
    TRAP_QUEUE_FULL, TRAP_QUEUE_EMPTY,
    TRAP_UNIMPLEMENTED,
    trap_name,
)
from .core import ThreadContext
from .memory import Memory

from collections import deque


# ---------------------------------------------------------------------------
# Emulator I/O trap codes (emulator-specific, per design/emulator.md § 7)
# ---------------------------------------------------------------------------
EMU_TRAP_PUTCHAR   = 0x80
EMU_TRAP_GETCHAR   = 0x81
EMU_TRAP_BLOCK_IO  = 0x82
EMU_TRAP_VDI       = 0x83
EMU_TRAP_MEM_BYTE  = 0x84  # Byte-level memory: r1=sub(0=load,1=store), r2=addr, r3=offset, r4=value
EMU_TRAP_SET_TRAP_TABLE = 0x90    # BIOS: r1 = trap table base address
EMU_TRAP_SET_TEMPLATE   = 0x91   # BIOS: r1 = index, r2 = 64-bit header value
EMU_TRAP_DEBUG_PRINT    = 0x9F   # Debug: print string (r1=addr, r2=len)

# Block I/O sub-functions (in r1)
BLOCK_IO_READ  = 0
BLOCK_IO_WRITE = 1
BLOCK_SIZE     = 4096  # bytes per block


class Emulator:
    """Single-tile LM-1 functional emulator.

    Phases 1-4: single-thread scalar, tagged, GC, dispatch.
    Phase 5: message queues (SEND/RECV/TRY.RECV), CAS.TAGGED, FAA, FENCE.GC,
             multi-thread round-robin scheduling, multi-tile cluster support.
    """

    # Default nursery: 64 KiB at the top of the first 256 KiB
    DEFAULT_NURSERY_BASE  = 0x0003_0000   # 192 KiB offset
    DEFAULT_NURSERY_SIZE  = 0x0001_0000   # 64 KiB

    # Default old-gen: 256 KiB starting at 512 KiB offset
    DEFAULT_OLDGEN_BASE   = 0x0008_0000   # 512 KiB offset
    DEFAULT_OLDGEN_SIZE   = 0x0004_0000   # 256 KiB

    # Card table: one byte per 256-byte card (covers nursery + old-gen)
    CARD_SIZE = 256  # bytes per card

    # Queue depth (per queue, per tile)
    QUEUE_DEPTH = 512
    NUM_QUEUES  = 4

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
        tile_id: int = 0,
        num_threads: int = 1,
        block_device: str | None = None,
        vdi: 'VDI | None' = None,
    ):
        self.mem = Memory(mem_size)
        self.tile_id = tile_id

        # Multi-thread support (Phase 5)
        self.threads: list[ThreadContext] = []
        for tid in range(num_threads):
            tc = ThreadContext(tile_id=tile_id, thread_id=tid)
            self.threads.append(tc)
        self._current_thread_idx = 0

        # I/O
        self._stdin: TextIO = stdin or sys.stdin
        self._stdout: TextIO = stdout or sys.stdout
        self.trace = trace

        # Nursery region
        self.nursery_base = nursery_base if nursery_base is not None else self.DEFAULT_NURSERY_BASE
        self.nursery_size = nursery_size if nursery_size is not None else self.DEFAULT_NURSERY_SIZE

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

        # Inline cache table: (callsite_pc, shape_id) → code_entry_addr
        # Per-tile hash map used for polymorphic dispatch.
        self.ic_table: dict[tuple[int, int], int] = {}

        # Hardware message queues (Phase 5): 4 queues per tile
        self.queues: list[deque] = [
            deque(maxlen=self.QUEUE_DEPTH) for _ in range(self.NUM_QUEUES)
        ]

        # Cluster reference for cross-tile messaging (set by Cluster)
        self.cluster: Optional['Cluster'] = None

        # Initialize nursery pointers for all threads
        for tc in self.threads:
            tc.np = self.nursery_base
            tc.nl = self.nursery_base + self.nursery_size

        # Install default header templates (index 0 = cons)
        self._init_header_templates()

        # Block device (Phase 7)
        self.block_device_path: str | None = block_device

        # VDI display engine (Phase 9)
        self.vdi = vdi

        # Stats
        self.instruction_count = 0

    @property
    def thread(self) -> ThreadContext:
        """Current active thread — backward compatible with single-thread API."""
        return self.threads[self._current_thread_idx]

    @thread.setter
    def thread(self, value: ThreadContext) -> None:
        self.threads[self._current_thread_idx] = value

    def _init_header_templates(self) -> None:
        """Set up the default header-template table entries."""
        t = self.thread
        # Index 0: Cons cell header (hdr_sub=1, size=2, shape_id=0)
        t.header_templates[0] = make_header(HDR_CONS, 2, 0)
        # Index 1: Closure header template (hdr_sub=4, size=0 — filled at alloc)
        t.header_templates[1] = make_header(HDR_CLOSURE, 0, 1)
        # Index 2: Vector header template (hdr_sub=2, size=0 — filled at alloc)
        t.header_templates[2] = make_header(HDR_VECTOR, 0, 2)

    def load_bios(self, bios_words: list[int], base: int = 0) -> None:
        """Load BIOS instruction words into memory and set PC to base."""
        self.mem.load_instructions(base, bios_words)
        for tc in self.threads:
            tc.pc = base
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

        while not t.halted and not t.stalled:
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

        # ---- LI32 (load 32-bit immediate from next word) ----
        elif op == Op.LI32:
            imm32 = self.mem.load_u32(next_pc)
            t.regs[inst.rd] = imm32 & WORD_MASK
            next_pc += 4  # skip the immediate word

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
            rs1 = inst.rs1  # car register
            rs2 = inst.rs2  # cdr register
            # Convention: rs1/rs2 == 0 → default to NIL (X-format compat)
            car = t.regs[rs1] if rs1 != 0 else NIL
            cdr = t.regs[rs2] if rs2 != 0 else NIL
            self._alloc_cons(t, rd, car, cdr)

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

        # ================================================================
        # Phase 4: Dispatch (Inline Cache)
        # ================================================================

        # ---- CALL.IC: inline-cache dispatch ----
        elif op == Op.CALL_IC:
            # Format I: Rd = receiver register, Rs1 = selector (shape comes
            # from the object header).  imm16 is unused / reserved.
            receiver = t.regs[inst.rd]
            if not is_any_ref(receiver):
                raise LM1Trap(TRAP_NOT_REF, "CALL.IC: receiver is not a ref")
            addr = ref_address(receiver)
            hdr = self.mem.load_word(addr)
            shape = header_shape_id(hdr) if is_header(hdr) else 0
            callsite = t.pc  # use current PC as callsite key
            key = (callsite, shape)
            target = self.ic_table.get(key)
            if target is not None:
                # IC hit — push frame and jump
                self._push_frame(t, next_pc)
                next_pc = target
            else:
                # IC miss — trap.  Handler should IC.INSTALL then ERET.
                raise LM1Trap(TRAP_IC_MISS,
                              f"IC miss: callsite={callsite:#x} shape={shape}")

        # ---- IC.INSTALL: populate IC entry ----
        elif op == Op.IC_INSTALL:
            # Format R: Rs1 = receiver (for shape), Rs2 = code_entry register,
            # Rd = callsite PC register.
            receiver = t.regs[inst.rs1]
            code_entry = t.regs[inst.rs2]
            callsite = t.regs[inst.rd]
            if is_any_ref(receiver):
                addr = ref_address(receiver)
                hdr = self.mem.load_word(addr)
                shape = header_shape_id(hdr) if is_header(hdr) else 0
            else:
                shape = 0
            self.ic_table[(callsite, shape)] = code_entry

        # ---- CALL.DIRECT: direct call (no IC) ----
        elif op == Op.CALL_DIRECT:
            # Format I: Rd = unused, Rs1 = unused, imm16 = relative offset (words)
            # Or: I-format with target address.
            # Convention: imm16 is a signed word offset from current PC.
            self._push_frame(t, next_pc)
            offset = inst.imm16  # signed word offset
            next_pc = t.pc + (offset * 4)

        # ---- CALL.CLOSURE: call through closure object ----
        elif op == Op.CALL_CLOSURE:
            # Format I: Rd = closure register.
            # Closure layout: [header][code_entry][env0][env1]...
            closure_ref = t.regs[inst.rd]
            if not is_any_ref(closure_ref):
                raise LM1Trap(TRAP_NOT_CLOSURE, "CALL.CLOSURE: not a ref")
            caddr = ref_address(closure_ref)
            hdr = self.mem.load_word(caddr)
            if not is_header(hdr) or header_subtype(hdr) != HDR_CLOSURE:
                raise LM1Trap(TRAP_NOT_CLOSURE, "CALL.CLOSURE: not a closure")
            code_entry = self.mem.load_word(caddr + 8)  # field 0 = code entry
            self._push_frame(t, next_pc)
            next_pc = code_entry

        # ---- RET: return from call ----
        elif op == Op.RET:
            next_pc = self._pop_frame(t)

        # ---- JR: jump register (absolute jump to address in Rs1) ----
        elif op == Op.JR:
            next_pc = t.regs[inst.rd]  # register is in rd position (bits 25:21)

        # ---- TAILCALL.IC: tail-call with inline cache ----
        elif op == Op.TAILCALL_IC:
            receiver = t.regs[inst.rd]
            if not is_any_ref(receiver):
                raise LM1Trap(TRAP_NOT_REF, "TAILCALL.IC: receiver is not a ref")
            addr = ref_address(receiver)
            hdr = self.mem.load_word(addr)
            shape = header_shape_id(hdr) if is_header(hdr) else 0
            callsite = t.pc
            key = (callsite, shape)
            target = self.ic_table.get(key)
            if target is not None:
                # Tail call: DON'T push a new frame, reuse current
                next_pc = target
            else:
                raise LM1Trap(TRAP_IC_MISS,
                              f"IC miss (tail): callsite={callsite:#x} shape={shape}")

        # ---- TAILCALL.DIRECT: direct tail-call ----
        elif op == Op.TAILCALL_DIR:
            offset = inst.imm16
            next_pc = t.pc + (offset * 4)
            # No frame push — reuse current frame

        # ================================================================
        # Phase 5: Messaging & Atomics
        # ================================================================

        # ---- SEND: enqueue value to a hardware queue ----
        elif op == Op.SEND:
            # Format S: rd (25:21) = queue descriptor register,
            #           rs1 (20:16) = value register
            queue_desc = t.regs[inst.rd]
            value = t.regs[inst.rs1]
            # Queue descriptor: fixnum encoding (tile_id << 2) | queue_idx
            # For local queues, tile_id matches self.tile_id.
            q_raw = untag_fixnum(queue_desc) if is_fixnum(queue_desc) else int(queue_desc)
            target_tile = q_raw >> 2
            queue_idx = q_raw & 3
            if target_tile == self.tile_id:
                # Local queue
                q = self.queues[queue_idx]
                if len(q) >= self.QUEUE_DEPTH:
                    raise LM1Trap(TRAP_QUEUE_FULL,
                                  f"SEND: queue {queue_idx} full")
                q.append(value)
            elif self.cluster is not None:
                # Cross-tile: route through cluster
                self.cluster.route_message(target_tile, queue_idx, value)
            else:
                raise LM1Trap(TRAP_QUEUE_FULL,
                              f"SEND: no cluster for tile {target_tile}")

        # ---- RECV / TRY.RECV: dequeue from a hardware queue ----
        elif op == Op.RECV:
            # When func != 0 → TRY.RECV (non-blocking)
            # When func == 0 → RECV (blocking / traps on empty)
            queue_desc = t.regs[inst.rs1]
            q_raw = untag_fixnum(queue_desc) if is_fixnum(queue_desc) else int(queue_desc)
            target_tile = q_raw >> 2
            queue_idx = q_raw & 3
            if target_tile == self.tile_id:
                q = self.queues[queue_idx]
            elif self.cluster is not None:
                q = self.cluster.tiles[target_tile].queues[queue_idx]
            else:
                q = deque()  # empty — will trigger trap/nil

            if inst.func != 0:
                # TRY.RECV: non-blocking.  Rd = value (or nil), Rd2 = status
                rd2 = inst.func  # Rd2 register index is encoded in func field
                if len(q) > 0:
                    t.regs[inst.rd] = q.popleft()
                    t.regs[rd2] = T
                else:
                    t.regs[inst.rd] = NIL
                    t.regs[rd2] = NIL
            else:
                # RECV: blocking.  For multi-thread emulator, stall the thread.
                if len(q) > 0:
                    t.regs[inst.rd] = q.popleft()
                else:
                    # Stall: mark thread as stalled, don't advance PC
                    t.stalled = True
                    t.stall_queue = queue_idx
                    next_pc = t.pc  # retry on next schedule

        # ---- CAS.TAGGED: atomic compare-and-swap ----
        elif op == Op.CAS_TAGGED:
            # Format X: Rd (25:21), Rs_addr (20:16), Rs_expected (15:11),
            #           Rt_new (10:6) — register index
            addr_reg = inst.rs1
            expected_reg = inst.rs2
            new_reg = inst.func  # bits 10:6 = register index for new value
            addr = ref_address(t.regs[addr_reg]) if is_any_ref(t.regs[addr_reg]) else t.regs[addr_reg]
            old = self.mem.load_word(addr)
            expected = t.regs[expected_reg]
            if old == expected:
                new_val = t.regs[new_reg]
                self.mem.store_word(addr, new_val)
                # Write barrier if storing a nursery ref into old-gen
                if is_any_ref(new_val):
                    self._write_barrier(addr, new_val)
                t.regs[inst.rd] = T
            else:
                t.regs[inst.rd] = NIL

        # ---- FAA / FENCE.GC ----
        elif op == Op.FAA_FENCE:
            if inst.func == 0x1F:
                # FENCE.GC: memory fence for GC coordination
                # In sequential emulator, this is a no-op.
                pass
            else:
                # FAA: fetch-and-add on fixnum memory
                addr = ref_address(t.regs[inst.rs1]) if is_any_ref(t.regs[inst.rs1]) else t.regs[inst.rs1]
                old = self.mem.load_word(addr)
                delta = t.regs[inst.rs2]
                if not is_fixnum(old):
                    raise LM1Trap(TRAP_NOT_FIXNUM,
                                  f"FAA: mem[{addr:#x}] = {old:#x} is not a fixnum")
                if not is_fixnum(delta):
                    raise LM1Trap(TRAP_NOT_FIXNUM,
                                  "FAA: delta is not a fixnum")
                # Fixnum add: since tag bit 0 is 0 for both, a + b preserves
                # the fixnum tag.  We mask to 64 bits.
                new_val = (old + delta) & WORD_MASK
                self.mem.store_word(addr, new_val)
                t.regs[inst.rd] = old

        # ---- Not yet implemented ----
        else:
            raise LM1Trap(TRAP_UNIMPLEMENTED, f"Unimplemented opcode {op} ({op:#04x})")

        return next_pc

    # -- Allocation helpers (Phase 2) ---

    # -- Frame helpers (Phase 4) ---

    def _push_frame(self, t: ThreadContext, return_addr: int) -> None:
        """Push a call frame: save LR and FP on the stack, set FP = SP."""
        # Push LR (return address)
        t.sp = (t.sp - 8) & WORD_MASK
        self.mem.store_word(t.sp, t.lr)
        # Push FP (saved frame pointer)
        t.sp = (t.sp - 8) & WORD_MASK
        self.mem.store_word(t.sp, t.fp)
        # Set up new frame
        t.lr = return_addr
        t.fp = t.sp

    def _pop_frame(self, t: ThreadContext) -> int:
        """Pop a call frame: restore FP and LR from stack, return the saved LR."""
        return_addr = t.lr
        # Restore FP and LR from stack
        t.sp = t.fp
        t.fp = self.mem.load_word(t.sp)
        t.sp = (t.sp + 8) & WORD_MASK
        t.lr = self.mem.load_word(t.sp)
        t.sp = (t.sp + 8) & WORD_MASK
        return return_addr

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

    def _alloc_cons(self, t: ThreadContext, rd: int,
                    car: int = NIL, cdr: int = NIL) -> None:
        """Allocate a cons cell (header + car + cdr = 24 bytes)."""
        addr = self._bump_alloc(t, 24)

        # Cons header: template index 0
        hdr = t.header_templates[0]
        self.mem.store_word(addr, hdr)
        # car and cdr from register operands (default nil for backward compat)
        self.mem.store_word(addr + 8, car)
        self.mem.store_word(addr + 16, cdr)

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
            # Block I/O: r1 = sub-function (0=read, 1=write)
            #            r2 = block number
            #            r3 = memory destination/source address
            func = t.regs[1]
            block_num = t.regs[2]
            mem_addr = t.regs[3]
            if self.block_device_path is None:
                # No block device attached — return error in r0
                t.regs[0] = tag_fixnum(-1)
                return
            import os
            try:
                if func == BLOCK_IO_READ:
                    with open(self.block_device_path, 'rb') as f:
                        f.seek(block_num * BLOCK_SIZE)
                        data = f.read(BLOCK_SIZE)
                    # Pad to BLOCK_SIZE if file is shorter
                    if len(data) < BLOCK_SIZE:
                        data = data + b'\x00' * (BLOCK_SIZE - len(data))
                    # Write into emulator memory byte by byte
                    for i, b in enumerate(data):
                        self.mem.store_byte(mem_addr + i, b)
                    t.regs[0] = tag_fixnum(0)  # success
                elif func == BLOCK_IO_WRITE:
                    # Read BLOCK_SIZE bytes from emulator memory
                    data = bytearray(BLOCK_SIZE)
                    for i in range(BLOCK_SIZE):
                        data[i] = self.mem.load_byte(mem_addr + i)
                    # Ensure file exists and write
                    mode = 'r+b' if os.path.exists(self.block_device_path) else 'wb'
                    with open(self.block_device_path, mode) as f:
                        f.seek(block_num * BLOCK_SIZE)
                        f.write(data)
                    t.regs[0] = tag_fixnum(0)  # success
                else:
                    t.regs[0] = tag_fixnum(-2)  # unknown sub-function
            except (IOError, OSError):
                t.regs[0] = tag_fixnum(-1)  # I/O error

        elif code == EMU_TRAP_SET_TRAP_TABLE:
            # Set the trap table base address for the current thread
            t.trap_table_base = t.regs[1]

        elif code == EMU_TRAP_SET_TEMPLATE:
            # Install a header template: r1 = index, r2 = 64-bit header value
            idx = t.regs[1] & 0xFF
            val = t.regs[2]
            t.header_templates[idx] = val

        elif code == EMU_TRAP_VDI:
            self._handle_vdi_trap()

        elif code == EMU_TRAP_MEM_BYTE:
            # Byte-level memory access:
            #   r1 = sub-function: 0=load_byte, 1=store_byte
            #   r2 = base address (raw)
            #   r3 = byte offset (tagged fixnum)
            #   r4 = value to store (tagged fixnum, for store only)
            sub = untag_fixnum(t.regs[1]) if is_fixnum(t.regs[1]) else int(t.regs[1])
            addr = t.regs[2]
            offset = untag_fixnum(t.regs[3]) if is_fixnum(t.regs[3]) else int(t.regs[3])
            if sub == 0:
                # Load byte → return as tagged fixnum in r1
                byte_val = self.mem.load_byte(addr + offset)
                t.regs[1] = tag_fixnum(byte_val)
            elif sub == 1:
                # Store byte
                val = untag_fixnum(t.regs[4]) if is_fixnum(t.regs[4]) else int(t.regs[4])
                self.mem.store_byte(addr + offset, val & 0xFF)
                t.regs[1] = tag_fixnum(0)  # success

        elif code == EMU_TRAP_DEBUG_PRINT:
            # Debug print: r1 = address of byte string, r2 = length
            addr = t.regs[1]
            length = t.regs[2]
            chars = []
            for i in range(length):
                chars.append(chr(self.mem.load_byte(addr + i)))
            self._stdout.write(''.join(chars))
            self._stdout.flush()

        else:
            raise LM1Trap(code, f"Unhandled trap: {trap_name(code)}")

    def _handle_vdi_trap(self) -> None:
        """Handle VDI display engine trap (0x83).

        r1 = function code (tagged fixnum)
        r2..r8 = arguments (tagged fixnums)
        Returns results in r1 (and sometimes r2, r3) as tagged fixnums.
        """
        from .vdi import (
            VDI_SET_MODE, VDI_FILL_RECT, VDI_BLIT,
            VDI_DRAW_CHAR, VDI_DRAW_STRING, VDI_SET_CURSOR, VDI_READ_PIXEL,
            VDI_DRAW_LINE, VDI_GET_MODE, VDI_SCROLL, VDI_PRESENT,
            VDI_READ_EVENT, VDI_GRAD_RECT, VDI_SHADOW_RECT,
        )
        t = self.thread
        if self.vdi is None:
            # No VDI attached — return error
            t.regs[1] = tag_fixnum(-1)
            return
        vdi = self.vdi
        func = untag_fixnum(t.regs[1])

        # Helper: untag register as int
        def arg(n: int) -> int:
            return untag_fixnum(t.regs[n])

        if func == VDI_SET_MODE:
            vdi.set_mode(arg(2), arg(3))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_FILL_RECT:
            vdi.fill_rect(arg(2), arg(3), arg(4), arg(5), arg(6))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_BLIT:
            vdi.blit(arg(2), arg(3), arg(4), arg(5), arg(6), arg(7))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_DRAW_CHAR:
            vdi.draw_char(arg(2), arg(3), arg(4), arg(5), arg(6))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_DRAW_STRING:
            # Read string from emulator memory
            vdi.draw_string_from_mem(
                arg(2), arg(3),
                self.mem.load_byte, arg(4), arg(5),
                arg(6), arg(7))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_SET_CURSOR:
            vdi.set_cursor(arg(2), arg(3), bool(arg(4)))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_READ_PIXEL:
            px = vdi.read_pixel(arg(2), arg(3))
            t.regs[1] = tag_fixnum(px)
        elif func == VDI_DRAW_LINE:
            vdi.draw_line(arg(2), arg(3), arg(4), arg(5), arg(6))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_GET_MODE:
            t.regs[1] = tag_fixnum(vdi.width)
            t.regs[2] = tag_fixnum(vdi.height)
        elif func == VDI_SCROLL:
            vdi.scroll(arg(2), arg(3), arg(4), arg(5), arg(6), arg(7))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_PRESENT:
            vdi.present()
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_READ_EVENT:
            evt_type, data1, data2 = vdi.read_event()
            t.regs[1] = tag_fixnum(evt_type)
            t.regs[2] = tag_fixnum(data1)
            t.regs[3] = tag_fixnum(data2)
        elif func == VDI_GRAD_RECT:
            vdi.grad_rect(arg(2), arg(3), arg(4), arg(5),
                          arg(6), arg(7), arg(8))
            t.regs[1] = tag_fixnum(0)
        elif func == VDI_SHADOW_RECT:
            vdi.shadow_rect(arg(2), arg(3), arg(4), arg(5),
                            arg(6), arg(7))
            t.regs[1] = tag_fixnum(0)
        else:
            # Unknown VDI function
            t.regs[1] = tag_fixnum(-1)

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

    # -- Multi-thread round-robin scheduling (Phase 5) ---

    def run_round_robin(self, max_instructions: int = 0,
                        quanta: int = 1) -> None:
        """Run multiple threads in round-robin order.

        Each non-halted, non-stalled thread gets *quanta* instructions per
        round.  A stalled thread (blocked on RECV) is un-stalled when its
        queue has data.
        """
        mem = self.mem
        count = 0
        n_threads = len(self.threads)

        while True:
            any_runnable = False

            for tidx in range(n_threads):
                self._current_thread_idx = tidx
                t = self.threads[tidx]

                if t.halted:
                    continue

                # Check if stalled thread can be un-stalled
                if t.stalled:
                    q = self.queues[t.stall_queue]
                    if len(q) > 0:
                        t.stalled = False
                        t.stall_queue = -1
                    else:
                        continue  # still stalled

                any_runnable = True

                # Execute up to *quanta* instructions on this thread
                for _ in range(quanta):
                    if t.halted or t.stalled:
                        break

                    raw = mem.load_u32(t.pc)
                    inst = decode(raw)

                    if self.trace:
                        self._trace_instruction(t.pc, inst)

                    next_pc = t.pc + 4

                    try:
                        next_pc = self._execute(inst, next_pc)
                    except LM1Trap as trap:
                        if trap.code == TRAP_NURSERY_OVERFLOW:
                            self._gc_collect()
                            next_pc = t.pc
                        elif t.trap_table_base != 0 and trap.code < 0x80:
                            t.trap_pc = t.pc
                            t.trap_cause = trap.code
                            t.in_trap = True
                            handler_addr = mem.load_word(
                                t.trap_table_base + trap.code * 8
                            )
                            if handler_addr == 0:
                                raise
                            next_pc = handler_addr
                        else:
                            raise

                    t.pc = next_pc
                    t.cycle_count += 1
                    self.instruction_count += 1
                    count += 1

                    if max_instructions and count >= max_instructions:
                        return

            if not any_runnable:
                break  # all threads halted or deadlocked


# ===========================================================================
# Cluster: multi-tile emulator (Phase 5)
# ===========================================================================

class Cluster:
    """Manages multiple Emulator tiles with cross-tile message routing.

    Queue descriptors are fixnum-encoded: (tile_id << 2) | queue_idx.
    """

    def __init__(
        self,
        n_tiles: int = 2,
        *,
        mem_size: int = 4 * 1024 * 1024,
        trace: bool = False,
        nursery_base: int | None = None,
        nursery_size: int | None = None,
        oldgen_base: int | None = None,
        oldgen_size: int | None = None,
        num_threads_per_tile: int = 1,
    ):
        self.tiles: list[Emulator] = []
        for i in range(n_tiles):
            emu = Emulator(
                mem_size=mem_size,
                trace=trace,
                nursery_base=nursery_base,
                nursery_size=nursery_size,
                oldgen_base=oldgen_base,
                oldgen_size=oldgen_size,
                tile_id=i,
                num_threads=num_threads_per_tile,
            )
            emu.cluster = self
            self.tiles.append(emu)

    def route_message(self, target_tile: int, queue_idx: int,
                      value: int) -> None:
        """Deliver a message to a remote tile's queue, trapping on full."""
        if target_tile < 0 or target_tile >= len(self.tiles):
            raise LM1Trap(TRAP_QUEUE_FULL,
                          f"SEND: invalid tile {target_tile}")
        q = self.tiles[target_tile].queues[queue_idx]
        if len(q) >= Emulator.QUEUE_DEPTH:
            raise LM1Trap(TRAP_QUEUE_FULL,
                          f"SEND: tile {target_tile} queue {queue_idx} full")
        q.append(value)

    def run(self, max_instructions: int = 0, quanta: int = 1) -> None:
        """Round-robin across all tiles.

        Each tile executes up to *quanta* instructions per round on its
        active thread(s).  The outer loop terminates when all tiles' threads
        are halted or the instruction budget is exhausted.
        """
        count = 0

        while True:
            any_runnable = False

            for tile in self.tiles:
                n_threads = len(tile.threads)
                for tidx in range(n_threads):
                    tile._current_thread_idx = tidx
                    t = tile.threads[tidx]

                    if t.halted:
                        continue

                    # Try to un-stall
                    if t.stalled:
                        q = tile.queues[t.stall_queue]
                        if len(q) > 0:
                            t.stalled = False
                            t.stall_queue = -1
                        else:
                            continue

                    any_runnable = True
                    mem = tile.mem

                    for _ in range(quanta):
                        if t.halted or t.stalled:
                            break

                        raw = mem.load_u32(t.pc)
                        inst = decode(raw)

                        if tile.trace:
                            tile._trace_instruction(t.pc, inst)

                        next_pc = t.pc + 4

                        try:
                            next_pc = tile._execute(inst, next_pc)
                        except LM1Trap as trap:
                            if trap.code == TRAP_NURSERY_OVERFLOW:
                                tile._gc_collect()
                                next_pc = t.pc
                            elif t.trap_table_base != 0 and trap.code < 0x80:
                                t.trap_pc = t.pc
                                t.trap_cause = trap.code
                                t.in_trap = True
                                handler_addr = mem.load_word(
                                    t.trap_table_base + trap.code * 8
                                )
                                if handler_addr == 0:
                                    raise
                                next_pc = handler_addr
                            else:
                                raise

                        t.pc = next_pc
                        t.cycle_count += 1
                        tile.instruction_count += 1
                        count += 1

                        if max_instructions and count >= max_instructions:
                            return

            if not any_runnable:
                break
