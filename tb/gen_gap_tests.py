#!/usr/bin/env python3
"""
Generate tests for identified coverage gaps in the LM-1 RTL testbench.

Tests cover:
  30: DIV/MOD (raw unsigned)
  31: DIV.FIX (signed fixnum division)
  32: TST all tag types (0-7)
  33: LI32 / large immediates
  34: Sub-word memory ops (LDB/STB/LDH/STH/LDW/STW)
  35: SEND/RECV (message queue round-trip)
  36: CAS.TAGGED (compare-and-swap)
  37: FAA (fetch-and-add)
  38: PUSH.MULTI / POP.MULTI
  39: TST.SHAPE (header-based type test)
  40: JR / TAILCALL.DIR
  41: CALL.CLOSURE
  42: Backward branches and BR.T edge cases
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'emu'))
from lm1.asm import Assembler
from lm1.word import tag_fixnum, make_header, make_ref, make_char, NIL, T

TB_DIR = os.path.join(os.path.dirname(__file__), 'tests')

# Memory layout constants
NURSERY_BASE   = 0x7000
NURSERY_LIMIT  = 0x7FF0
STACK_TOP      = 0x4000
TRAP_TABLE     = 0x5000
CARD_TABLE     = 0x6000
CARD_SHIFT     = 6
GEN_BOUNDARY_V = 0x7800


def emit_test(name: str, source: str, expected: dict[str, int]):
    """Assemble source, write .hex and .expected files."""
    asm = Assembler()
    binary = asm.assemble(source)
    while len(binary) % 8:
        binary += b'\x00'
    words = []
    for i in range(0, len(binary), 8):
        chunk = binary[i:i+8].ljust(8, b'\x00')
        words.append(int.from_bytes(chunk, 'little'))
    while len(words) < (1 << 16):
        words.append(0)

    hex_path = os.path.join(TB_DIR, f'{name}.hex')
    exp_path = os.path.join(TB_DIR, f'{name}.expected')

    with open(hex_path, 'w') as f:
        for w in words:
            f.write(f'{w:016x}\n')
    with open(exp_path, 'w') as f:
        for reg, val in sorted(expected.items()):
            f.write(f'{reg}=0x{val:x}\n')
    print(f"  {name}: generated")


# ===========================================================================
# Test 30: DIV / MOD — raw unsigned division
# ===========================================================================
def gen_test_30():
    source = """\
    ; Test 30: Raw DIV and MOD
    ;
    ; r1 = 100 / 10 = 10
    ; r2 = 100 % 10 = 0
    ; r3 = 17 / 5 = 3
    ; r4 = 17 % 5 = 2
    ; r5 = 0 / 7 = 0
    ; r6 = 7 / 1 = 7
    ; r7 = 255 / 255 = 1
    ; r8 = 255 % 255 = 0

    LI r10, 100
    LI r11, 10
    LI r12, 17
    LI r13, 5
    LI r14, 0
    LI r15, 7
    LI r16, 1
    LI r17, 255

    DIV r1, r10, r11    ; 100/10 = 10
    MOD r2, r10, r11    ; 100%10 = 0
    DIV r3, r12, r13    ; 17/5 = 3
    MOD r4, r12, r13    ; 17%5 = 2
    DIV r5, r14, r15    ; 0/7 = 0
    DIV r6, r15, r16    ; 7/1 = 7
    DIV r7, r17, r17    ; 255/255 = 1
    MOD r8, r17, r17    ; 255%255 = 0

    HALT
"""
    emit_test('30_div_mod_raw', source, {
        'r1': 10,
        'r2': 0,
        'r3': 3,
        'r4': 2,
        'r5': 0,
        'r6': 7,
        'r7': 1,
        'r8': 0,
    })


# ===========================================================================
# Test 31: DIV.FIX — signed tagged fixnum division
# ===========================================================================
def gen_test_31():
    # tag_fixnum(x) = (x << 1) & WORD_MASK, which produces unsigned values.
    # For negative fixnums, use raw signed value so LI sign-extends correctly.
    def stf(x):
        """Signed tagged fixnum: x*2 as a signed Python int."""
        return x * 2

    source = f"""\
    ; Test 31: Tagged fixnum division (DIV.FIX)
    ;
    ;  12 /  3 =  4       (positive / positive)
    ; -12 /  3 = -4       (negative / positive)
    ;  12 / -3 = -4       (positive / negative)
    ; -12 / -3 =  4       (negative / negative)
    ;  7  /  2 =  3       (truncate toward zero)
    ; -7  /  2 = -3       (truncate toward zero)

    ; Small tagged values fit in signed 16-bit immediate and LI sign-extends.
    ; tag_fixnum(-12)= -24,  tag_fixnum(-3)= -6,  tag_fixnum(-7)= -14
    LI r10, {stf(12)}        ; tagged 12
    LI r11, {stf(3)}         ; tagged 3
    LI r12, {stf(-12)}       ; tagged -12 (sign-extended by LI)
    LI r13, {stf(-3)}        ; tagged -3
    LI r14, {stf(7)}         ; tagged 7
    LI r15, {stf(2)}         ; tagged 2
    LI r16, {stf(-7)}        ; tagged -7

    DIV.FIX r1, r10, r11  ; 12/3 = 4
    DIV.FIX r2, r12, r11  ; -12/3 = -4
    DIV.FIX r3, r10, r13  ; 12/-3 = -4
    DIV.FIX r4, r12, r13  ; -12/-3 = 4
    DIV.FIX r5, r14, r15  ; 7/2 = 3 (truncate)
    DIV.FIX r6, r16, r15  ; -7/2 = -3 (truncate)

    HALT
"""
    emit_test('31_div_fix_signed', source, {
        'r1': tag_fixnum(4),
        'r2': tag_fixnum(-4),  # unsigned 64-bit
        'r3': tag_fixnum(-4),
        'r4': tag_fixnum(4),
        'r5': tag_fixnum(3),
        'r6': tag_fixnum(-3),
    })


# ===========================================================================
# Test 32: TST all tag types
# ===========================================================================
def gen_test_32():
    # Construct values of each type
    char_A = make_char(65)  # 'A' = (65 << 8) | 0x35 = 0x4135
    # A short-float: low byte = 0x3D
    sfloat_val = 0x12_3D  # arbitrary value with low byte 0x3D
    # A header word
    hdr_val = make_header(0, 3, 42)  # subtype 0, size 3, shape 42
    # A ref
    ref_val = make_ref(0x1000)  # general ref to addr 0x1000
    # A cons ref
    cons_val = make_ref(0x2000, cons=True)

    source = f"""\
    ; Test 32: TST with all tag type constants (0-7)
    ;
    ; Test each tag type returns T for the right value and NIL for wrong

    ; --- Build test values ---
    LI r10, 42           ; tagged fixnum (42<<1 = 84 = 0x54)
    LI r11, 0            ; also fixnum (0)
    ; r12 = a general ref (low bits = 001)
    LI r12, {ref_val & 0xFFFF}
    LUI r18, {(ref_val >> 16) & 0xFFFF}
    OR  r12, r12, r18
    ; r13 = a cons ref (low bits = 011)
    LI r13, {cons_val & 0xFFFF}
    LUI r18, {(cons_val >> 16) & 0xFFFF}
    OR  r13, r13, r18
    ; r14 = NIL (0x05, low bits = 101)
    LI r14, 0x05
    ; r15 = a char (low byte = 0x35)
    LI r15, {char_A & 0xFFFF}
    ; r16 = a short-float (low byte = 0x3D)
    LI r16, {sfloat_val & 0xFFFF}
    ; r17 = a header (low bits = 111)
    LI r17, {hdr_val & 0xFFFF}
    LUI r18, {(hdr_val >> 16) & 0xFFFF}
    OR  r17, r17, r18

    ; --- TST.FIX ---
    TST.FIX r1, r10       ; fixnum 84 -> T (0x0D)

    ; --- TST.REF ---
    TST.REF r2, r12       ; ref -> T

    ; --- TST.CONS ---
    TST.CONS r3, r13      ; cons-ref -> T

    ; --- TST.SPECIAL ---
    TST.SPECIAL r4, r14   ; NIL (0x05) has bits 101 -> T (special tag)

    ; --- TST.NIL ---
    TST.NIL r5, r14       ; NIL -> T
    TST.NIL r6, r10       ; fixnum -> NIL (not NIL)

    ; --- TST.CHAR ---
    TST.CHAR r7, r15      ; char -> T

    ; --- TST.HDR ---
    TST.HDR r8, r17       ; header -> T

    ; --- Cross-checks: wrong types ---
    TST.FIX r9, r12       ; ref is NOT fixnum -> NIL
    ; r19 = TST.REF on fixnum -> should be NIL
    TST.REF r19, r10      ; fixnum is NOT ref -> NIL
    ; r20 = TST.CONS on ref -> should be NIL (001 != 011)
    TST.CONS r20, r12     ; ref is NOT cons -> NIL

    HALT
"""
    emit_test('32_tst_all_tags', source, {
        'r1': T,     # TST.FIX on fixnum -> T
        'r2': T,     # TST.REF on ref -> T
        'r3': T,     # TST.CONS on cons -> T
        'r4': T,     # TST.SPECIAL on NIL -> T
        'r5': T,     # TST.NIL on NIL -> T
        'r6': NIL,   # TST.NIL on fixnum -> NIL
        'r7': T,     # TST.CHAR on char -> T
        'r8': T,     # TST.HDR on header -> T
        'r9': NIL,   # TST.FIX on ref -> NIL
        'r19': NIL,  # TST.REF on fixnum -> NIL
        'r20': NIL,  # TST.CONS on ref -> NIL
    })


# ===========================================================================
# Test 33: LI32 — large immediates
# ===========================================================================
def gen_test_33():
    source = """\
    ; Test 33: LI32 (large immediates, auto-expanded from LI)
    ;
    ; LI auto-expands to LI32 for values outside [-32768, 32767]
    ; LI32 zero-extends from 32 bits

    LI r1, 0x12345       ; > 32767 -> LI32
    LI r2, 100000        ; 0x186A0 -> LI32
    LI r3, 0xDEAD        ; 0xDEAD = 57005 -> LI32
    LI r4, -1000         ; fits in 16-bit signed -> regular LI (sign-extend)
    LI r5, 32767         ; max 16-bit signed -> regular LI
    LI r6, 32768         ; just over -> LI32 (zero-extend from 32)
    LI r7, 0xFFFFFFFF    ; max 32-bit -> LI32
    LI r8, 0             ; zero -> regular LI

    HALT
"""
    emit_test('33_li32', source, {
        'r1': 0x12345,
        'r2': 100000,
        'r3': 0xDEAD,
        'r4': 0xFFFF_FFFF_FFFF_FC18,  # -1000 sign-extended to 64 bits
        'r5': 32767,
        'r6': 32768,      # zero-extended from 32 bits
        'r7': 0xFFFFFFFF,  # zero-extended from 32 bits (NOT sign-extended)
        'r8': 0,
    })


# ===========================================================================
# Test 34: Sub-word memory ops
# ===========================================================================
def gen_test_34():
    source = f"""\
    ; Test 34: Sub-word memory operations (LDB/STB, LDH/STH, LDW/STW)

    ; Use memory at 0x2000 for scratch
    LI r10, 0x2000

    ; --- STR a known 64-bit pattern, then read back sub-words ---
    ; Store 0x0807060504030201 at address 0x2000
    LI r11, 0x0201
    LUI r12, 0x0403
    OR  r11, r11, r12
    LUI r12, 0x0605
    ; Need upper 32 bits: 0x08070605 — but we can only LI32 up to 32 bits
    ; Just use the lower 32 bits for now: 0x04030201
    STR r10, r11, 0      ; store 0x04030201 at 0x2000

    ; --- Byte loads (LDB) ---
    LDB r1, r10, 0       ; byte at offset 0 -> 0x01
    LDB r2, r10, 1       ; byte at offset 1 -> 0x02
    LDB r3, r10, 2       ; byte at offset 2 -> 0x03
    LDB r4, r10, 3       ; byte at offset 3 -> 0x04

    ; --- Halfword loads (LDH) ---
    LDH r5, r10, 0       ; halfword at offset 0 -> 0x0201
    LDH r6, r10, 2       ; halfword at offset 2 -> 0x0403

    ; --- Word loads (LDW) ---
    LDW r7, r10, 0       ; word at offset 0 -> 0x04030201

    ; --- STB: store a single byte, verify others unchanged ---
    LI r13, 0xFF
    STB r10, r13, 1      ; store 0xFF at byte offset 1
    LDB r8, r10, 0       ; byte 0 should still be 0x01
    LDB r9, r10, 1       ; byte 1 should now be 0xFF

    ; --- STH: store halfword ---
    LI r14, 0x2000
    LI r15, 8            ; offset 8 -> address 0x2008
    ADD r14, r14, r15
    LI r15, 0xBEEF
    STH r14, r15, 0      ; store 0xBEEF at 0x2008 offset 0
    LDH r19, r14, 0      ; read back -> 0xBEEF

    ; --- STW: store 32-bit word ---
    LI r14, 0x2000
    LI r15, 16
    ADD r14, r14, r15    ; addr 0x2010
    LI r15, 0x1234
    LUI r16, 0x5678
    OR  r15, r15, r16   ; 0x56781234
    STW r14, r15, 0
    LDW r20, r14, 0     ; read back -> 0x56781234

    HALT
"""
    emit_test('34_subword_mem', source, {
        'r1': 0x01,
        'r2': 0x02,
        'r3': 0x03,
        'r4': 0x04,
        'r5': 0x0201,
        'r6': 0x0403,
        'r7': 0x04030201,
        'r8': 0x01,         # unchanged after STB at offset 1
        'r9': 0xFF,         # overwritten by STB
        'r19': 0xBEEF,      # STH round-trip
        'r20': 0x56781234,  # STW round-trip
    })


# ===========================================================================
# Test 35: SEND / RECV — message queue round-trip
# ===========================================================================
def gen_test_35():
    source = """\
    ; Test 35: SEND/RECV message queue round-trip (same-core)
    ;
    ; SEND value to queue 0, RECV it back.
    ; SEND two values (FIFO order check).
    ; TRY.RECV on empty queue -> NIL+NIL.

    ; --- TRY.RECV on empty queue -> should get NIL ---
    TRY.RECV r1, r2, 0   ; r1=value (NIL), r2=status (NIL)

    ; --- SEND and RECV on queue 0 ---
    LI r10, 42
    SEND r10, 0           ; send 42 to queue 0
    RECV r3, 0            ; recv from queue 0 -> r3 = 42

    ; --- FIFO ordering: send A then B, recv should get A first ---
    LI r11, 100
    LI r12, 200
    SEND r11, 0           ; send 100
    SEND r12, 0           ; send 200
    RECV r4, 0            ; should get 100 (FIFO)
    RECV r5, 0            ; should get 200

    ; --- TRY.RECV on non-empty queue ---
    LI r13, 999
    SEND r13, 0           ; send 999
    TRY.RECV r6, r7, 0   ; r6=999, r7=T (success)

    ; --- TRY.RECV on now-empty queue -> NIL ---
    TRY.RECV r8, r9, 0   ; r8=NIL, r9=NIL

    HALT
"""
    emit_test('35_send_recv', source, {
        'r1': NIL,   # TRY.RECV empty -> value = NIL
        'r2': NIL,   # TRY.RECV empty -> status = NIL
        'r3': 42,    # RECV got 42
        'r4': 100,   # FIFO order: first in
        'r5': 200,   # FIFO order: second in
        'r6': 999,   # TRY.RECV success -> value
        'r7': T,     # TRY.RECV success -> status = T
        'r8': NIL,   # TRY.RECV empty again
        'r9': NIL,   # TRY.RECV empty again
    })


# ===========================================================================
# Test 36: CAS.TAGGED — compare-and-swap
# ===========================================================================
def gen_test_36():
    source = f"""\
    ; Test 36: CAS.TAGGED (compare-and-swap)
    ;
    ; CAS.TAGGED rd, rs_addr, rs_expected, rs_new
    ;   Reads mem[addr], compares to expected.
    ;   If match -> store new, rd = T.
    ;   If mismatch -> no store, rd = NIL.

    ; Set up memory at 0x2000 with initial value 0xAAAA
    LI r10, 0x2000
    LI r11, 0x0AAA
    STR r10, r11, 0

    ; --- CAS match: expected=0xAAAA, new=0xBBBB ---
    LI r12, 0x0AAA       ; expected
    LI r13, 0x0BBB       ; new value
    CAS.TAGGED r1, r10, r12, r13
    ; r1 should be T (match -> swap succeeded)

    ; Verify memory was actually updated
    LDR r2, r10, 0        ; should be 0x0BBB

    ; --- CAS mismatch: expected=0xAAAA (stale), actual is now 0xBBBB ---
    LI r14, 0x0AAA       ; expected (wrong now)
    LI r15, 0x0CCC       ; new value (should NOT be written)
    CAS.TAGGED r3, r10, r14, r15
    ; r3 should be NIL (mismatch -> no swap)

    ; Verify memory was NOT changed
    LDR r4, r10, 0        ; should still be 0x0BBB

    ; --- CAS match again with the correct current value ---
    LI r16, 0x0BBB       ; expected (correct)
    LI r17, 0x0DDD       ; new value
    CAS.TAGGED r5, r10, r16, r17
    ; r5 should be T
    LDR r6, r10, 0        ; should be 0x0DDD

    HALT
"""
    emit_test('36_cas_tagged', source, {
        'r1': T,       # first CAS matched
        'r2': 0x0BBB,  # memory updated
        'r3': NIL,     # second CAS mismatched
        'r4': 0x0BBB,  # memory unchanged
        'r5': T,       # third CAS matched
        'r6': 0x0DDD,  # memory updated again
    })


# ===========================================================================
# Test 37: FAA — fetch-and-add
# ===========================================================================
def gen_test_37():
    source = f"""\
    ; Test 37: FAA (fetch-and-add)
    ;
    ; FAA rd, rs_addr, rs_delta
    ;   old = mem[addr]
    ;   mem[addr] = old + delta
    ;   rd = old

    ; Set up memory at 0x2000 with initial value 10
    LI r10, 0x2000
    LI r11, 10
    STR r10, r11, 0

    ; FAA: delta=5 -> old=10, new=15
    LI r12, 5
    FAA r1, r10, r12      ; r1 = 10 (old value)
    LDR r2, r10, 0        ; should be 15

    ; FAA again: delta=3 -> old=15, new=18
    LI r13, 3
    FAA r3, r10, r13      ; r3 = 15 (old value)
    LDR r4, r10, 0        ; should be 18

    ; FAA with delta=0 -> should return old, no change
    LI r14, 0
    FAA r5, r10, r14      ; r5 = 18 (old value)
    LDR r6, r10, 0        ; should still be 18

    HALT
"""
    emit_test('37_faa', source, {
        'r1': 10,    # old value returned
        'r2': 15,    # memory updated: 10+5
        'r3': 15,    # old value after first FAA
        'r4': 18,    # memory: 15+3
        'r5': 18,    # old value, delta=0
        'r6': 18,    # memory unchanged
    })


# ===========================================================================
# Test 38: PUSH.MULTI / POP.MULTI
# ===========================================================================
def gen_test_38():
    source = f"""\
    ; Test 38: PUSH.MULTI / POP.MULTI
    ;
    ; Push registers r1,r2,r3 (bank 0, mask = 0b1110 = 0x0E)
    ; Clobber them, then pop back.
    ;
    ; Also test: empty mask (no-op), single register

    ; Init stack
    LI sp, {STACK_TOP}

    ; Set up values
    LI r1, 11
    LI r2, 22
    LI r3, 33

    ; Push r1,r2,r3 (bank 0, mask bits 1,2,3 = 0x000E)
    PUSH.MULTI 0, 0x000E

    ; Clobber
    LI r1, 0
    LI r2, 0
    LI r3, 0

    ; Pop r1,r2,r3 back
    POP.MULTI 0, 0x000E

    ; r1,r2,r3 should be restored
    ; Save to r4,r5,r6 so we can test additional cases
    MOV r4, r1
    MOV r5, r2
    MOV r6, r3

    ; --- Empty mask (no-op) ---
    LI r7, 77
    PUSH.MULTI 0, 0x0000  ; no registers -> no stack change
    POP.MULTI 0, 0x0000   ; no registers -> no stack change
    ; r7 should still be 77

    ; --- Single register (bit 8 -> r8) ---
    LI r8, 88
    PUSH.MULTI 0, 0x0100  ; push r8 only
    LI r8, 0              ; clobber
    POP.MULTI 0, 0x0100   ; pop r8 back
    ; r8 should be 88

    HALT
"""
    emit_test('38_push_pop_multi', source, {
        'r4': 11,   # r1 restored to 11
        'r5': 22,   # r2 restored to 22
        'r6': 33,   # r3 restored to 33
        'r7': 77,   # untouched by empty mask
        'r8': 88,   # single-register push/pop
    })


# ===========================================================================
# Test 39: TST.SHAPE — header-based type testing
# ===========================================================================
def gen_test_39():
    source = f"""\
    ; Test 39: TST.SHAPE (header-based type test)
    ;
    ; SET_TEMPLATE (TRAP 0x91) takes r1=index, r2=full header word.
    ; Allocate objects via templates with known shape IDs, then TST.SHAPE.

    LI sp, {STACK_TOP}
    LI np, {NURSERY_BASE}
    LI nl, {NURSERY_LIMIT}

    ; Configure template 0: shape_id=42 (full header in r2)
    LI r1, 0
    LI32 r2, {make_header(0, 0, 42)}
    TRAP 0x91

    ; Configure template 1: shape_id=99
    LI r1, 1
    LI32 r2, {make_header(0, 0, 99)}
    TRAP 0x91

    ; Allocate object with template 0 (shape 42)
    ALLOC r10, 3, 0

    ; Allocate object with template 1 (shape 99)
    ALLOC r11, 3, 1

    ; TST.SHAPE r10 against shape 42 -> T
    TST.SHAPE r1, r10, 42

    ; TST.SHAPE r10 against shape 99 -> NIL
    TST.SHAPE r2, r10, 99

    ; TST.SHAPE r11 against shape 99 -> T
    TST.SHAPE r3, r11, 99

    ; TST.SHAPE on fixnum -> NIL (fast path)
    LI r12, 84
    TST.SHAPE r4, r12, 42

    ; TST.SHAPE on NIL -> NIL (fast path)
    LI r13, 0x05
    TST.SHAPE r5, r13, 42

    HALT
"""
    emit_test('39_tst_shape', source, {
        'r1': T,     # TST.SHAPE on shape-42 object, query 42 -> T
        'r2': NIL,   # TST.SHAPE on shape-42 object, query 99 -> NIL
        'r3': T,     # TST.SHAPE on shape-99 object, query 99 -> T
        'r4': NIL,   # fixnum -> NIL (fast path)
        'r5': NIL,   # NIL -> NIL (fast path)
    })


# ===========================================================================
# Test 40: JR / TAILCALL.DIR
# ===========================================================================
def gen_test_40():
    source = f"""\
    ; Test 40: JR (jump register) and TAILCALL.DIR
    ;
    ; JR: load target address, jump to it.
    ; TAILCALL.DIR: direct tail call (no frame push, just PC-relative jump)

    LI sp, {STACK_TOP}

    ; --- JR: jump to target1 ---
    LI r10, target1
    JR r10
    LI r1, 0xFF          ; should be skipped
    BR done               ; should be skipped

target1:
    LI r1, 11            ; should execute

    ; --- TAILCALL.DIR: tail-call to target2 ---
    ; Save SP to verify no frame push
    MOV r8, sp
    TAILCALL.DIRECT target2
    LI r2, 0xFF          ; should be skipped

target2:
    LI r2, 22            ; should execute
    MOV r9, sp
    SUB r3, r8, r9       ; r3 = 0 if SP unchanged (no frame pushed)

done:
    HALT
"""
    emit_test('40_jr_tailcall', source, {
        'r1': 11,    # reached via JR
        'r2': 22,    # reached via TAILCALL.DIR
        'r3': 0,     # SP unchanged (no frame push)
    })


# ===========================================================================
# Test 41: CALL.CLOSURE
# ===========================================================================
def gen_test_41():
    source = f"""\
    ; Test 41: CALL.CLOSURE
    ;
    ; Allocate a closure object, install code entry and env slots,
    ; then call it. The closure body accesses its env via LD.FLD.

    LI sp, {STACK_TOP}
    LI fp, {STACK_TOP}
    LI np, {NURSERY_BASE}
    LI nl, {NURSERY_LIMIT}

    ; Initialize closure template (entry 1):
    ; HDR_CLOSURE = subtype 4, size patched by allocator
    LI r1, 1
    LI r2, {make_header(4, 0, 0)}
    TRAP 0x91

    ; Allocate closure with 2 env slots
    LI r10, closure_body
    ALLOC.CLOSURE r11, r10, 2    ; r11 = closure ref, 2 env slots

    ; Store environment values
    LI r12, 100
    ST.FLD r11, r12, 1          ; env slot 0 (field 1, after code entry) = 100
    LI r13, 200
    ST.FLD r11, r13, 2          ; env slot 1 (field 2) = 200

    ; Call the closure
    CALL.CLOSURE r11
    ; After return, r1 should be 300 (100 + 200)
    BR done

closure_body:
    ; r11 still holds the closure ref (preserved across CALL.CLOSURE)
    LD.FLD r20, r11, 1          ; env slot 0 -> 100
    LD.FLD r21, r11, 2          ; env slot 1 -> 200
    ADD r1, r20, r21            ; r1 = 300
    RET

done:
    HALT
"""
    emit_test('41_call_closure', source, {
        'r1': 300,  # 100 + 200 from env slots
    })


# ===========================================================================
# Test 42: Backward branches and BR.T edge cases
# ===========================================================================
def gen_test_42():
    source = f"""\
    ; Test 42: Backward branches and BR.T edge cases
    ;
    ; Count-up loop using backward branch.
    ; BR.T edge cases: val=1 (truthy), val=NIL (not), val=0 (not).

    ; --- Backward branch: count-up loop ---
    ; Avoid counting DOWN because raw 5 == NIL (0x05) would
    ; cause BR.T to exit early. Instead, count UP and compare
    ; against limit: (counter - limit) is 0 when done → falsy.
    LI r1, 0              ; iteration count
    LI r2, 10             ; limit
bloop:
    LI r10, 1
    ADD r1, r1, r10       ; r1++
    SUB r10, r1, r2       ; r10 = r1 - 10  (negative = large u64 → truthy)
    BR.T r10, bloop       ; loop while r1 != 10
    ; r1 = 10 (executed 10 times)

    ; --- BR.T edge cases ---
    ; val = 1: truthy (non-zero, non-NIL)
    LI r10, 1
    BR.T r10, brt_1_taken
    LI r3, 0              ; skipped
    BR brt_1_done
brt_1_taken:
    LI r3, 1              ; taken
brt_1_done:

    ; val = NIL: not truthy
    LI r10, 5             ; NIL = 0x05
    BR.T r10, brt_nil_taken
    LI r4, 0              ; NOT taken (NIL is falsy)
    BR brt_nil_done
brt_nil_taken:
    LI r4, 1              ; should NOT reach here
brt_nil_done:

    ; val = 0: not truthy
    LI r10, 0
    BR.T r10, brt_0_taken
    LI r5, 0              ; NOT taken (zero is falsy)
    BR brt_0_done
brt_0_taken:
    LI r5, 1              ; should NOT reach here
brt_0_done:

    HALT
"""
    emit_test('42_backward_br', source, {
        'r1': 10,   # loop ran 10 times
        'r3': 1,    # BR.T on val=1: taken (truthy)
        'r4': 0,    # BR.T on val=NIL: not taken
        'r5': 0,    # BR.T on val=0: not taken
    })


# ===========================================================================
# Main
# ===========================================================================

if __name__ == '__main__':
    os.makedirs(TB_DIR, exist_ok=True)
    print("Generating gap tests...")
    gen_test_30()
    gen_test_31()
    gen_test_32()
    gen_test_33()
    gen_test_34()
    gen_test_35()
    gen_test_36()
    gen_test_37()
    gen_test_38()
    gen_test_39()
    gen_test_40()
    gen_test_41()
    gen_test_42()
    print("Done. All gap tests generated.")
