"""LM-1 instruction decoder.

Decodes a 32-bit instruction word into an Instruction namedtuple.
Encoding follows spec/07-encoding.md.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum

# ---------------------------------------------------------------------------
# Opcode constants  (bits 31:26, 6 bits)
# ---------------------------------------------------------------------------

class Op(IntEnum):
    # Family 1 — Tagged Arithmetic & Type Tests
    TST           = 0b000000  # 0
    TST_SHAPE     = 0b000001  # 1
    ARITH_FIX     = 0b000010  # 2   (func selects ADD/SUB/MUL/DIV)
    ADD_FIX_IMM   = 0b000011  # 3
    CMP_TAGGED    = 0b000100  # 4   (func=0 CMP, func=1 EQ)

    # Family 2 — Allocation
    ALLOC         = 0b001000  # 8
    ALLOC_CONS    = 0b001001  # 9
    ALLOCV        = 0b001010  # 10
    ALLOC_CLOSURE = 0b001011  # 11

    # Family 3 — Field Access
    LD            = 0b010000  # 16
    LD_CAR_CDR    = 0b010001  # 17  (imm16 selects car=0 / cdr=1)
    ST            = 0b010010  # 18
    ST_WB         = 0b010011  # 19
    ST_CAR_CDR    = 0b010100  # 20  (field selects car=0 / cdr=1)

    # Family 4 — Dispatch
    CALL_IC       = 0b011000  # 24
    IC_INSTALL    = 0b011001  # 25
    CALL_DIRECT   = 0b011010  # 26
    CALL_CLOSURE  = 0b011011  # 27
    RET           = 0b011100  # 28
    TAILCALL_IC   = 0b011101  # 29
    TAILCALL_DIR  = 0b011110  # 30

    # Family 5 — Prefetch (no-ops in emulator)
    PREFETCH_REF  = 0b100000  # 32
    PREFETCH_FLD  = 0b100001  # 33
    PREFETCH_CDR  = 0b100010  # 34
    GATHER_PRE    = 0b100011  # 35

    # Family 6 — Concurrency
    SEND          = 0b100100  # 36
    RECV          = 0b100101  # 37
    CAS_TAGGED    = 0b100110  # 38
    FAA_FENCE     = 0b100111  # 39  (func distinguishes FAA vs FENCE.GC)

    # Family 7 — Region / Bulk
    ENQ_SCAN      = 0b101000  # 40
    ENQ_COPY      = 0b101001  # 41
    ENQ_FIXUP     = 0b101010  # 42
    ENQ_COMPACT   = 0b101011  # 43

    # Scalar — Supplementary
    ARITH_RAW     = 0b110000  # 48  (func: ADD=0,SUB=1,MUL=2,DIV=3,MOD=4)
    BITWISE       = 0b110001  # 49  (func: AND=0,OR=1,XOR=2,SHL=3,SHR=4,ASR=5,NOT=6)
    LDR           = 0b110010  # 50
    STR           = 0b110011  # 51
    BR            = 0b110100  # 52  (unconditional)
    BR_COND       = 0b110101  # 53  (Rs2/func encodes condition)
    PUSH_POP      = 0b110110  # 54
    LI            = 0b110111  # 55
    LUI           = 0b111000  # 56
    PUSH_MULTI    = 0b111001  # 57  (Format I: rd=bank, imm16=register mask)
    POP_MULTI     = 0b111010  # 58  (Format I: rd=bank, imm16=register mask)

    # System
    TRAP          = 0b111100  # 60
    ERET          = 0b111101  # 61
    SYS_INFO      = 0b111110  # 62  (TILE.ID, THREAD.ID, CYCLE)
    HALT_NOP      = 0b111111  # 63

# Sub-function codes for ARITH_RAW
FUNC_ADD = 0
FUNC_SUB = 1
FUNC_MUL = 2
FUNC_DIV = 3
FUNC_MOD = 4

# Sub-function codes for BITWISE
FUNC_AND = 0
FUNC_OR  = 1
FUNC_XOR = 2
FUNC_SHL = 3
FUNC_SHR = 4
FUNC_ASR = 5
FUNC_NOT = 6

# Sub-function codes for ARITH_FIX
FUNC_ADD_FIX = 0
FUNC_SUB_FIX = 1
FUNC_MUL_FIX = 2
FUNC_DIV_FIX = 3

# Sub-function codes for CMP_TAGGED
FUNC_CMP = 0
FUNC_EQ  = 1

# Branch condition codes (encoded in Rs2 field of BR_COND)
BR_T      = 0   # branch if truthy
BR_NIL    = 1   # branch if nil
BR_FIX_LT = 2   # branch if fixnum <
BR_FIX_EQ = 3   # branch if fixnum ==
BR_FIX_GT = 4   # branch if fixnum >
BR_EQ     = 5   # branch if word-equal

# PUSH/POP sub-functions (in func / imm)
FUNC_PUSH       = 0
FUNC_POP        = 1
FUNC_PUSH_MULTI = 2
FUNC_POP_MULTI  = 3

# SYS_INFO sub-functions
FUNC_TILE_ID   = 0
FUNC_THREAD_ID = 1
FUNC_CYCLE     = 2
FUNC_TRAP_CAUSE = 3
FUNC_TRAP_PC    = 4

# HALT_NOP sub-functions
FUNC_HALT = 0
FUNC_NOP  = 1

# ---------------------------------------------------------------------------
# Decoded instruction
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Instruction:
    """Decoded instruction fields."""
    opcode: int       # 6-bit opcode (Op enum value)
    rd: int           # destination register (5 bits)
    rs1: int          # source register 1 (5 bits)
    rs2: int          # source register 2 (5 bits)
    func: int         # function selector (5 bits)
    imm16: int        # 16-bit signed immediate
    imm11: int        # 11-bit field (Format S)
    raw26: int        # raw 26-bit payload (Format X)
    raw: int          # the original 32-bit word

def _sign_extend(value: int, bits: int) -> int:
    """Sign-extend a `bits`-wide integer to a Python int."""
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit

def decode(word: int) -> Instruction:
    """Decode a 32-bit instruction word into its fields.

    All fields are extracted regardless of format; the executor
    picks the relevant fields based on the opcode.
    """
    opcode = (word >> 26) & 0x3F

    # Format R fields
    rd   = (word >> 21) & 0x1F
    rs1  = (word >> 16) & 0x1F
    rs2  = (word >> 11) & 0x1F
    func = (word >> 6)  & 0x1F

    # Format I fields (imm16 = bits 15:0, sign-extended)
    imm16_raw = word & 0xFFFF
    imm16 = _sign_extend(imm16_raw, 16)

    # Format S fields (imm11 = bits 10:0)
    imm11 = word & 0x7FF

    # Format X (raw payload)
    raw26 = word & 0x03FF_FFFF

    return Instruction(
        opcode=opcode,
        rd=rd, rs1=rs1, rs2=rs2, func=func,
        imm16=imm16, imm11=imm11, raw26=raw26,
        raw=word,
    )

# ---------------------------------------------------------------------------
# Instruction encoding helpers (for tests / hand-assembly)
# ---------------------------------------------------------------------------

def encode_r(opcode: int, rd: int, rs1: int, rs2: int, func: int = 0) -> int:
    """Encode a Format R instruction."""
    return (
        ((opcode & 0x3F) << 26)
        | ((rd & 0x1F) << 21)
        | ((rs1 & 0x1F) << 16)
        | ((rs2 & 0x1F) << 11)
        | ((func & 0x1F) << 6)
    )

def encode_i(opcode: int, rd: int, rs1: int, imm16: int) -> int:
    """Encode a Format I instruction."""
    return (
        ((opcode & 0x3F) << 26)
        | ((rd & 0x1F) << 21)
        | ((rs1 & 0x1F) << 16)
        | (imm16 & 0xFFFF)
    )

def encode_s(opcode: int, rs: int, rt: int, field: int, imm11: int = 0) -> int:
    """Encode a Format S instruction."""
    return (
        ((opcode & 0x3F) << 26)
        | ((rs & 0x1F) << 21)
        | ((rt & 0x1F) << 16)
        | ((field & 0x1F) << 11)
        | (imm11 & 0x7FF)
    )

def encode_b(opcode: int, rs1: int, rs2: int, offset16: int) -> int:
    """Encode a Format B instruction."""
    return (
        ((opcode & 0x3F) << 26)
        | ((rs1 & 0x1F) << 21)
        | ((rs2 & 0x1F) << 16)
        | (offset16 & 0xFFFF)
    )

def encode_x(opcode: int, payload26: int) -> int:
    """Encode a Format X instruction with a raw 26-bit payload."""
    return ((opcode & 0x3F) << 26) | (payload26 & 0x03FF_FFFF)
