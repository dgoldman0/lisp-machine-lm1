#!/usr/bin/env python3
"""
Generate test programs for the LM-1 RTL testbench.

Each test emits a .hex file (one 64-bit word per line, in hex)
suitable for $readmemh or loading via the ext_mem port.

Instructions are 32 bits but memory is 64 bits wide.
Two instructions pack into one 64-bit word:
  word[31:0]  = instruction at byte address 8*n
  word[63:32] = instruction at byte address 8*n+4

Output: tb/tests/<name>.hex  — one hex line per 64-bit SRAM word
        tb/tests/<name>.expected — expected register values (r<N>=<val>)
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'emu'))

from lm1.decode import (
    encode_r, encode_i, encode_s, encode_b, encode_x,
    Op,
    FUNC_ADD, FUNC_SUB, FUNC_MUL, FUNC_DIV, FUNC_MOD,
    FUNC_AND, FUNC_OR, FUNC_XOR, FUNC_SHL, FUNC_SHR, FUNC_ASR, FUNC_NOT,
    FUNC_ADD_FIX, FUNC_SUB_FIX, FUNC_MUL_FIX, FUNC_DIV_FIX,
    FUNC_CMP, FUNC_EQ,
    FUNC_PUSH, FUNC_POP,
    FUNC_HALT, FUNC_NOP,
)

OUTDIR = os.path.join(os.path.dirname(__file__), 'tests')
os.makedirs(OUTDIR, exist_ok=True)

def tag_fixnum(val):
    """Tag an integer as a fixnum (val << 1)."""
    return (val << 1) & 0xFFFFFFFFFFFFFFFF

VAL_NIL = 0x05
VAL_T   = 0x0D

def halt():
    return encode_r(Op.HALT_NOP, 0, 0, 0, FUNC_HALT)

def nop():
    return encode_r(Op.HALT_NOP, 1, 0, 0, FUNC_NOP)

def pack_instructions(insns):
    """Pack 32-bit instructions into 64-bit SRAM words.
    Returns list of 64-bit ints."""
    words = []
    for i in range(0, len(insns), 2):
        lo = insns[i]
        hi = insns[i+1] if i+1 < len(insns) else 0
        words.append((hi << 32) | lo)
    return words

def write_test(name, insns, expected, mem_init=None, n_words=256):
    """Write a test's .hex and .expected files.
    
    mem_init: dict of {word_addr: 64-bit value} for data initialization
    """
    mem = pack_instructions(insns)
    # Pad to n_words
    while len(mem) < n_words:
        mem.append(0)
    # Apply data initialization
    if mem_init:
        for addr, val in mem_init.items():
            mem[addr] = val
    # Write hex
    with open(os.path.join(OUTDIR, f'{name}.hex'), 'w') as f:
        for w in mem:
            f.write(f'{w:016x}\n')
    # Write expected values
    with open(os.path.join(OUTDIR, f'{name}.expected'), 'w') as f:
        for reg, val in sorted(expected.items()):
            f.write(f'r{reg}={val:#x}\n')
    print(f'  {name}: {len(insns)} instructions')


# =====================================================================
# Test 1: LI (load immediate)
# =====================================================================
def test_li():
    insns = [
        encode_i(Op.LI, 1, 0, 42),       # r1 = sext(42) = 42
        encode_i(Op.LI, 2, 0, -1 & 0xFFFF),  # r2 = sext(0xFFFF) = -1
        encode_i(Op.LI, 3, 0, 0x1234),   # r3 = 0x1234
        halt(),
    ]
    expected = {1: 42, 2: 0xFFFFFFFFFFFFFFFF, 3: 0x1234}
    write_test('01_li', insns, expected)


# =====================================================================
# Test 2: ALU raw add/sub
# =====================================================================
def test_alu_raw():
    insns = [
        encode_i(Op.LI, 1, 0, 10),       # r1 = 10
        encode_i(Op.LI, 2, 0, 20),       # r2 = 20
        encode_r(Op.ARITH_RAW, 3, 1, 2, FUNC_ADD),  # r3 = r1 + r2 = 30
        encode_r(Op.ARITH_RAW, 4, 2, 1, FUNC_SUB),  # r4 = r2 - r1 = 10
        encode_r(Op.ARITH_RAW, 5, 1, 2, FUNC_MUL),  # r5 = r1 * r2 = 200
        halt(),
    ]
    expected = {1: 10, 2: 20, 3: 30, 4: 10, 5: 200}
    write_test('02_alu_raw', insns, expected)


# =====================================================================
# Test 3: Bitwise ops
# =====================================================================
def test_bitwise():
    insns = [
        encode_i(Op.LI, 1, 0, 0xFF),     # r1 = 0xFF
        encode_i(Op.LI, 2, 0, 0x0F),     # r2 = 0x0F
        encode_r(Op.BITWISE, 3, 1, 2, FUNC_AND),  # r3 = 0xFF & 0x0F = 0x0F
        encode_r(Op.BITWISE, 4, 1, 2, FUNC_OR),   # r4 = 0xFF | 0x0F = 0xFF
        encode_r(Op.BITWISE, 5, 1, 2, FUNC_XOR),  # r5 = 0xFF ^ 0x0F = 0xF0
        encode_r(Op.BITWISE, 6, 1, 0, FUNC_NOT),  # r6 = ~0xFF
        halt(),
    ]
    expected = {
        1: 0xFF, 2: 0x0F,
        3: 0x0F, 4: 0xFF, 5: 0xF0,
        6: 0xFFFFFFFFFFFFFF00,
    }
    write_test('03_bitwise', insns, expected)


# =====================================================================
# Test 4: Branches
# =====================================================================
def test_branch():
    # r1 = 1, branch forward over r2=99, so r2 stays 0
    insns = [
        encode_i(Op.LI, 1, 0, 1),         # 0: r1 = 1
        encode_b(Op.BR, 0, 0, 2),          # 4: BR +2 (skip to addr 4+2*4=12)
        encode_i(Op.LI, 2, 0, 99),        # 8: r2 = 99 (should be skipped)
        encode_i(Op.LI, 3, 0, 42),        # 12: r3 = 42
        halt(),                             # 16
    ]
    expected = {1: 1, 2: 0, 3: 42}
    write_test('04_branch', insns, expected)


# =====================================================================
# Test 5: Conditional branch (BR.FIX.EQ)
# =====================================================================
def test_br_cond():
    # r1 = 0 → fixnum equal to 0 → branch taken
    # r2 = 5 → fixnum not zero → branch not taken
    insns = [
        encode_i(Op.LI, 1, 0, 0),         # 0: r1 = 0
        encode_i(Op.LI, 2, 0, 5),         # 4: r2 = 5
        # BR_COND: test r1 with BR.FIX.EQ (cond=3), offset=2
        encode_b(Op.BR_COND, 1, 3, 2),    # 8: if r1==0, skip +2 → 16
        encode_i(Op.LI, 10, 0, 99),       # 12: r10=99 (skipped)
        encode_i(Op.LI, 3, 0, 10),        # 16: r3 = 10
        # BR_COND: test r2 with BR.FIX.EQ (cond=3), offset=2
        encode_b(Op.BR_COND, 2, 3, 2),    # 20: if r2==0 skip+2→28 (NOT taken)
        encode_i(Op.LI, 4, 0, 20),        # 24: r4 = 20 (executed)
        halt(),                             # 28
    ]
    expected = {1: 0, 2: 5, 3: 10, 4: 20, 10: 0}
    write_test('05_br_cond', insns, expected)


# =====================================================================
# Test 6: LDR / STR (raw memory load/store)
# =====================================================================
def test_ldr_str():
    # Store 0x1234 to mem[0x400], load it back
    insns = [
        encode_i(Op.LI, 1, 0, 0x1234),    # r1 = 0x1234
        encode_i(Op.LI, 2, 0, 0x0400),    # r2 = 0x400 (byte addr)
        # STR: mem[rd + sext(imm)] = rs1 → mem[r2+0] = r1
        encode_i(Op.STR, 2, 1, 0),        # store r1 to mem[r2]
        # LDR: rd = mem[rs1 + sext(imm)]
        encode_i(Op.LDR, 3, 2, 0),        # r3 = mem[r2]
        halt(),
    ]
    expected = {1: 0x1234, 2: 0x400, 3: 0x1234}
    write_test('06_ldr_str', insns, expected)


# =====================================================================
# Test 7: LUI
# =====================================================================
def test_lui():
    insns = [
        encode_i(Op.LUI, 1, 0, 0x1234),   # r1 = 0x1234_0000
        halt(),
    ]
    expected = {1: 0x12340000}
    write_test('07_lui', insns, expected)


# =====================================================================
# Test 8: SHL / SHR
# =====================================================================
def test_shifts():
    insns = [
        encode_i(Op.LI, 1, 0, 1),         # r1 = 1
        encode_i(Op.LI, 2, 0, 4),         # r2 = 4
        encode_r(Op.BITWISE, 3, 1, 2, FUNC_SHL),  # r3 = 1 << 4 = 16
        encode_r(Op.BITWISE, 4, 3, 2, FUNC_SHR),  # r4 = 16 >> 4 = 1
        halt(),
    ]
    expected = {1: 1, 2: 4, 3: 16, 4: 1}
    write_test('08_shifts', insns, expected)


# =====================================================================
# Test 9: TST (type test — fixnum)
# =====================================================================
def test_tst():
    insns = [
        encode_i(Op.LI, 1, 0, 42),        # r1 = 42 (tagged fixnum: even)
        encode_i(Op.LI, 2, 0, 5),         # r2 = 5  (looks like VAL_NIL!)
        # TST rd, rs1, tag_const
        # tag_const 0 = fixnum
        encode_i(Op.TST, 3, 1, 0),        # r3 = tst_fixnum(r1) — 42 is even → T
        encode_i(Op.TST, 4, 2, 0),        # r4 = tst_fixnum(r2) — 5 is odd → NIL
        halt(),
    ]
    expected = {1: 42, 2: 5, 3: VAL_T, 4: VAL_NIL}
    write_test('09_tst', insns, expected)


# =====================================================================
# Main: generate all tests
# =====================================================================
if __name__ == '__main__':
    print('Generating test programs...')
    test_li()
    test_alu_raw()
    test_bitwise()
    test_branch()
    test_br_cond()
    test_ldr_str()
    test_lui()
    test_shifts()
    test_tst()
    print('Done.')
