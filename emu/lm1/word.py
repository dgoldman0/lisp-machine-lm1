"""LM-1 tagged word representation and helpers.

Every 64-bit machine word is self-describing via its low 3 bits (primary tag).
Fixnum test: bit 0 == 0.  All arithmetic uses masked 64-bit Python ints.

Constants and helpers follow spec/01-object-model.md exactly.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Masks
# ---------------------------------------------------------------------------
WORD_MASK = 0xFFFF_FFFF_FFFF_FFFF  # 64-bit
SIGN_BIT  = 1 << 63
REF_ADDR_MASK = 0x0007_FFFF_FFFF_FFF8  # bits 50:3

# ---------------------------------------------------------------------------
# Primary tags  (bits 2:0)
# ---------------------------------------------------------------------------
TAG_FIXNUM  = 0       # bit 0 == 0  (includes 000, 010, 100, 110)
TAG_REF     = 0b001   # general heap ref
TAG_CONS    = 0b011   # cons-cell ref
TAG_SPECIAL = 0b101   # immediate specials (nil, t, char, …)
TAG_HEADER  = 0b111   # object header (memory only)

# ---------------------------------------------------------------------------
# Special immediate values
# ---------------------------------------------------------------------------
NIL        = 0x05               # tag=101, subtype=00000
T          = 0x0D               # tag=101, subtype=00001
UNBOUND    = 0x15               # tag=101, subtype=00010
EOF_OBJ    = 0x1D               # tag=101, subtype=00011
VOID       = 0x25               # tag=101, subtype=00100
UNDEFINED  = 0x2D               # tag=101, subtype=00101

# ---------------------------------------------------------------------------
# Tag constants for TST instruction
# ---------------------------------------------------------------------------
TAG_CONST_FIXNUM  = 0
TAG_CONST_REF     = 1
TAG_CONST_CONS    = 2
TAG_CONST_SPECIAL = 3
TAG_CONST_NIL     = 4
TAG_CONST_CHAR    = 5
TAG_CONST_SFLOAT  = 6
TAG_CONST_HEADER  = 7

# ---------------------------------------------------------------------------
# Fixnum helpers
# ---------------------------------------------------------------------------

def is_fixnum(w: int) -> bool:
    """Bit-0 test: fixnum iff bit 0 is 0."""
    return (w & 1) == 0

def tag_fixnum(value: int) -> int:
    """Encode a Python int as a tagged fixnum (shift left 1)."""
    return (value << 1) & WORD_MASK

def untag_fixnum(w: int) -> int:
    """Decode a tagged fixnum to a Python int (arithmetic shift right 1)."""
    # Simulate 64-bit arithmetic right shift
    if w & SIGN_BIT:
        return -((~w & WORD_MASK) >> 1) - 1
    return w >> 1

def fixnum_value(w: int) -> int:
    """Alias for untag_fixnum."""
    return untag_fixnum(w)

# ---------------------------------------------------------------------------
# Ref helpers
# ---------------------------------------------------------------------------

def is_ref(w: int) -> bool:
    """General ref: low 2 bits == 01."""
    return (w & 3) == 1

def is_cons_ref(w: int) -> bool:
    """Cons ref: low 3 bits == 011."""
    return (w & 7) == 3

def is_any_ref(w: int) -> bool:
    """Any heap pointer (ref or cons-ref): low bit 0 == 1, bit 1 == 0 is NOT enough.
    Refs have (w & 3) == 1."""
    return (w & 1) == 1 and (w & 7) in (0b001, 0b011)

def ref_address(w: int) -> int:
    """Extract the effective address from a ref word."""
    return w & REF_ADDR_MASK

def make_ref(address: int, cons: bool = False) -> int:
    """Construct a ref word from an aligned address."""
    tag = TAG_CONS if cons else TAG_REF
    return (address & REF_ADDR_MASK) | tag

# ---------------------------------------------------------------------------
# Special helpers
# ---------------------------------------------------------------------------

def is_special(w: int) -> bool:
    return (w & 7) == TAG_SPECIAL

def is_nil(w: int) -> bool:
    return w == NIL

def is_t(w: int) -> bool:
    return w == T

def is_char(w: int) -> bool:
    return (w & 0xFF) == 0x35

def make_char(codepoint: int) -> int:
    """Encode a Unicode codepoint as a special-immediate character."""
    return (codepoint << 8) | 0x35

def char_codepoint(w: int) -> int:
    return (w >> 8) & 0x1F_FFFF

# ---------------------------------------------------------------------------
# Header helpers (memory-only)
# ---------------------------------------------------------------------------

def is_header(w: int) -> bool:
    return (w & 7) == TAG_HEADER

def make_header(hdr_sub: int, size: int, shape_id: int, gc_bits: int = 0) -> int:
    """Construct a header word.

    Layout: [gc_bits:8][shape_id:32][size:16][hdr_sub:5][111]
    """
    return (
        TAG_HEADER
        | ((hdr_sub & 0x1F) << 3)
        | ((size & 0xFFFF) << 8)
        | ((shape_id & 0xFFFF_FFFF) << 24)
        | ((gc_bits & 0xFF) << 56)
    )

def header_subtype(w: int) -> int:
    return (w >> 3) & 0x1F

def header_size(w: int) -> int:
    return (w >> 8) & 0xFFFF

def header_shape_id(w: int) -> int:
    return (w >> 24) & 0xFFFF_FFFF

def header_gc_bits(w: int) -> int:
    return (w >> 56) & 0xFF

# Header subtypes
HDR_INSTANCE  = 0b00000
HDR_CONS      = 0b00001
HDR_VECTOR    = 0b00010
HDR_BYTEVEC   = 0b00011
HDR_CLOSURE   = 0b00100
HDR_SYMBOL    = 0b00101
HDR_BIGNUM    = 0b00110
HDR_RATIO     = 0b00111
HDR_DOUBLE    = 0b01000
HDR_COMPLEX   = 0b01001
HDR_WEAKREF   = 0b01010
HDR_CONT      = 0b01011
HDR_PORT      = 0b01100
HDR_HASHTABLE = 0b01101
HDR_FOREIGN   = 0b01110
HDR_SHAPE     = 0b01111
HDR_EXTENDED  = 0b11111

# ---------------------------------------------------------------------------
# Truthiness (Lisp-style: nil and fixnum-0 are false, everything else is true)
# ---------------------------------------------------------------------------

def is_truthy(w: int) -> bool:
    """LM-1 truthiness: anything that is not nil is truthy.
    (BR.T tests 'not nil and not fixnum 0' per ISA)"""
    return w != NIL and w != 0

# ---------------------------------------------------------------------------
# 64-bit arithmetic helpers
# ---------------------------------------------------------------------------

def u64(val: int) -> int:
    """Clamp to unsigned 64-bit."""
    return val & WORD_MASK

def s64(val: int) -> int:
    """Interpret a u64 as signed 64-bit."""
    if val & SIGN_BIT:
        return val - (1 << 64)
    return val

def add64(a: int, b: int) -> tuple[int, bool]:
    """Add two u64 values, return (result, overflow).
    Overflow means the signed result doesn't fit in 63 bits
    (for fixnum: the tag bit gets corrupted)."""
    result = (a + b) & WORD_MASK
    # Signed overflow: both operands same sign, result different sign
    sa, sb, sr = a & SIGN_BIT, b & SIGN_BIT, result & SIGN_BIT
    overflow = (sa == sb) and (sa != sr)
    return result, overflow

def sub64(a: int, b: int) -> tuple[int, bool]:
    result = (a - b) & WORD_MASK
    sa, sb, sr = a & SIGN_BIT, b & SIGN_BIT, result & SIGN_BIT
    overflow = (sa != sb) and (sb == sr)
    return result, overflow
