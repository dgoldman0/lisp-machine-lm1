"""LM-1 thread context and register file.

A ThreadContext holds the full execution state for one hardware thread:
32 GPRs, special registers, and bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from .word import WORD_MASK, NIL


# Register aliases (per spec/02-isa.md § 2.1)
REG_SP = 30
REG_FP = 29
REG_LR = 28
REG_TP = 27
REG_NP = 26   # nursery pointer
REG_NL = 25   # nursery limit


@dataclass
class ThreadContext:
    """Execution state for one LM-1 hardware thread."""

    # General-purpose registers r0–r31 (each a 64-bit tagged word)
    regs: list[int] = field(default_factory=lambda: [0] * 32)

    # Program counter
    pc: int = 0

    # Cycle counter
    cycle_count: int = 0

    # Halted flag
    halted: bool = False

    # Stalled flag (Phase 5: thread stalled on empty RECV queue)
    stalled: bool = False
    stall_queue: int = -1  # queue index this thread is blocked on

    # Trap state
    trap_table_base: int = 0
    trap_cause: int = 0
    trap_pc: int = 0          # saved PC on trap entry
    in_trap: bool = False

    # Tile / thread identity
    tile_id: int = 0
    thread_id: int = 0

    # Header-template table (indexed by 16-bit template index)
    # Each entry is a full 64-bit header word.
    header_templates: list[int] = field(default_factory=lambda: [0] * 256)

    # -- Register access helpers --

    def get_reg(self, idx: int) -> int:
        return self.regs[idx & 0x1F]

    def set_reg(self, idx: int, value: int) -> None:
        idx &= 0x1F
        if idx == 0:
            # r0 is always 0 (hardwired zero — common RISC convention)
            # Actually, the LM-1 spec does NOT hardwire r0 to zero.
            # All 32 regs are general-purpose.  So we allow writes to r0.
            pass
        self.regs[idx] = value & WORD_MASK

    # -- Named register shortcuts --

    @property
    def sp(self) -> int:
        return self.regs[REG_SP]

    @sp.setter
    def sp(self, v: int) -> None:
        self.regs[REG_SP] = v & WORD_MASK

    @property
    def fp(self) -> int:
        return self.regs[REG_FP]

    @fp.setter
    def fp(self, v: int) -> None:
        self.regs[REG_FP] = v & WORD_MASK

    @property
    def lr(self) -> int:
        return self.regs[REG_LR]

    @lr.setter
    def lr(self, v: int) -> None:
        self.regs[REG_LR] = v & WORD_MASK

    @property
    def np(self) -> int:
        return self.regs[REG_NP]

    @np.setter
    def np(self, v: int) -> None:
        self.regs[REG_NP] = v & WORD_MASK

    @property
    def nl(self) -> int:
        return self.regs[REG_NL]

    @nl.setter
    def nl(self, v: int) -> None:
        self.regs[REG_NL] = v & WORD_MASK

    def dump_regs(self) -> str:
        """Pretty-print all registers."""
        lines = [f"  PC  = {self.pc:#010x}  cycle = {self.cycle_count}"]
        for i in range(0, 32, 4):
            parts = []
            for j in range(4):
                r = i + j
                v = self.regs[r]
                parts.append(f"r{r:<2d}={v:#018x}")
            lines.append("  " + "  ".join(parts))
        return "\n".join(lines)
