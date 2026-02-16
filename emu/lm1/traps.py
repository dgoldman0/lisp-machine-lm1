"""LM-1 trap codes.

Mirrors spec/02-isa.md § 12.
"""

# Family 1 — tagged arith
TRAP_NOT_FIXNUM       = 0x01
TRAP_FIXNUM_OVERFLOW  = 0x02
TRAP_DIVIDE_BY_ZERO   = 0x03
TRAP_TYPE_MISMATCH    = 0x04

# Family 3 — field access
TRAP_NOT_REF          = 0x05
TRAP_NOT_CONS         = 0x06
TRAP_NOT_CLOSURE      = 0x07

# Family 2 — allocation
TRAP_NURSERY_OVERFLOW = 0x10

# Family 4 — dispatch
TRAP_IC_MISS          = 0x20

# Family 6 — concurrency
TRAP_QUEUE_FULL       = 0x30
TRAP_QUEUE_EMPTY      = 0x31

# Family 7 — movement engines
TRAP_ENGINE_BUSY      = 0x40

# Family 3 — barriers
TRAP_BARRIER_OVERFLOW = 0x50

# Family 8 — capability
TRAP_CAPABILITY_VIOLATION = 0x60

# Stack
TRAP_STACK_UNDERFLOW  = 0x70
TRAP_STACK_OVERFLOW   = 0x80

# System
TRAP_UNIMPLEMENTED    = 0xFE
TRAP_USER             = 0xFF

# Emulator-specific I/O traps (not ISA — emulator bridge)
TRAP_IO_PUTCHAR = 0x80  # NOTE: shares code with TRAP_STACK_OVERFLOW in spec;
                        # in practice the emulator uses explicit TRAP #code
                        # so the code in the instruction distinguishes them.
# We'll use the instruction-embedded code, not the above constants,
# for the emulator I/O bridge.  See execute.py.

# Human-readable names
_NAMES: dict[int, str] = {
    0x01: "TRAP_NOT_FIXNUM",
    0x02: "TRAP_FIXNUM_OVERFLOW",
    0x03: "TRAP_DIVIDE_BY_ZERO",
    0x04: "TRAP_TYPE_MISMATCH",
    0x05: "TRAP_NOT_REF",
    0x06: "TRAP_NOT_CONS",
    0x07: "TRAP_NOT_CLOSURE",
    0x10: "TRAP_NURSERY_OVERFLOW",
    0x20: "TRAP_IC_MISS",
    0x30: "TRAP_QUEUE_FULL",
    0x31: "TRAP_QUEUE_EMPTY",
    0x40: "TRAP_ENGINE_BUSY",
    0x50: "TRAP_BARRIER_OVERFLOW",
    0x60: "TRAP_CAPABILITY_VIOLATION",
    0x70: "TRAP_STACK_UNDERFLOW",
    0x80: "TRAP_STACK_OVERFLOW",
    0xFE: "TRAP_UNIMPLEMENTED",
    0xFF: "TRAP_USER",
}


def trap_name(code: int) -> str:
    return _NAMES.get(code, f"TRAP_{code:#04x}")


class LM1Trap(Exception):
    """Raised by instruction execution when a trap fires.

    The emulator's main loop catches this and dispatches to the trap handler
    (or, in Phase 1, just prints a diagnostic and halts).
    """

    def __init__(self, code: int, message: str = ""):
        self.code = code
        self.message = message or trap_name(code)
        super().__init__(f"{self.message} (code={code:#04x})")
