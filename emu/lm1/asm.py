"""LM-1 assembler — Phase 6.

Two-pass assembler: reads LM-1 assembly source, outputs 32-bit instruction words.

Syntax:
    label:              ; defines a label
    MNEMONIC args       ; instruction
    .word VALUE         ; emit a 64-bit word (data)
    .u32  VALUE         ; emit a 32-bit word (data)
    .byte VALUE         ; emit a byte (padded to alignment later)
    .align N            ; align to N-byte boundary
    .equ NAME, VALUE    ; define a named constant
    .template IDX, SUB, SIZE, SHAPE  ; header template (emitted as directive)
    ; comment
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .decode import (
    Op,
    encode_r, encode_i, encode_s, encode_b, encode_x,
    FUNC_ADD, FUNC_SUB, FUNC_MUL, FUNC_DIV, FUNC_MOD,
    FUNC_AND, FUNC_OR, FUNC_XOR, FUNC_SHL, FUNC_SHR, FUNC_ASR, FUNC_NOT,
    FUNC_ADD_FIX, FUNC_SUB_FIX, FUNC_MUL_FIX, FUNC_DIV_FIX,
    FUNC_CMP, FUNC_EQ,
    FUNC_PUSH, FUNC_POP,
    FUNC_TILE_ID, FUNC_THREAD_ID, FUNC_CYCLE,
    FUNC_TRAP_CAUSE, FUNC_TRAP_PC,
    BR_T, BR_NIL, BR_FIX_LT, BR_FIX_EQ, BR_FIX_GT, BR_EQ,
)
from .word import make_header


# ---------------------------------------------------------------------------
# Register name mapping
# ---------------------------------------------------------------------------

REGISTER_MAP: dict[str, int] = {}
for i in range(32):
    REGISTER_MAP[f"r{i}"] = i
REGISTER_MAP["sp"] = 30
REGISTER_MAP["fp"] = 29
REGISTER_MAP["lr"] = 28
REGISTER_MAP["tp"] = 27
REGISTER_MAP["np"] = 26
REGISTER_MAP["nl"] = 25


# ---------------------------------------------------------------------------
# ASM error
# ---------------------------------------------------------------------------

class AsmError(Exception):
    def __init__(self, line_no: int, msg: str):
        self.line_no = line_no
        super().__init__(f"line {line_no}: {msg}")


# ---------------------------------------------------------------------------
# Parsed line
# ---------------------------------------------------------------------------

@dataclass
class AsmLine:
    line_no: int
    label: str | None
    mnemonic: str | None
    args: list[str]
    raw: str


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------

class Assembler:
    """Two-pass LM-1 assembler."""

    def __init__(self):
        self.labels: dict[str, int] = {}
        self.equ: dict[str, int] = {}
        self.output: bytearray = bytearray()
        self.templates: list[tuple[int, int, int, int]] = []  # idx, sub, size, shape
        self._lines: list[AsmLine] = []
        self._base: int = 0  # base address for .org

    def assemble(self, source: str, base: int = 0) -> bytes:
        """Assemble source text, return binary output."""
        self._base = base
        self._lines = self._parse(source)
        self._pass1()
        self.output = bytearray()
        self._pass2()
        return bytes(self.output)

    def assemble_to_words(self, source: str, base: int = 0) -> list[int]:
        """Assemble source text, return list of 32-bit instruction words."""
        binary = self.assemble(source, base)
        words = []
        for i in range(0, len(binary), 4):
            w = int.from_bytes(binary[i:i+4], 'little')
            words.append(w)
        return words

    # -- Parsing --

    def _parse(self, source: str) -> list[AsmLine]:
        lines = []
        for i, raw in enumerate(source.split('\n'), 1):
            line = raw.strip()
            # Strip comments
            if ';' in line:
                line = line[:line.index(';')].strip()
            if not line:
                continue

            label = None
            # Check for label
            m = re.match(r'^([A-Za-z_]\w*)\s*:', line)
            if m:
                label = m.group(1)
                line = line[m.end():].strip()

            if not line:
                lines.append(AsmLine(i, label, None, [], raw))
                continue

            # Split mnemonic and args
            parts = line.split(None, 1)
            mnemonic = parts[0].upper()
            if len(parts) > 1:
                # Split args by comma, strip whitespace
                args = [a.strip() for a in parts[1].split(',')]
            else:
                args = []

            lines.append(AsmLine(i, label, mnemonic, args, raw))
        return lines

    # -- Pass 1: collect labels, compute sizes --

    def _pass1(self):
        self.labels = {}
        pc = self._base

        for line in self._lines:
            if line.label:
                if line.label in self.labels:
                    raise AsmError(line.line_no, f"duplicate label: {line.label}")
                self.labels[line.label] = pc

            if line.mnemonic is None:
                continue

            mn = line.mnemonic
            if mn == '.WORD':
                pc += 8  # 64-bit
            elif mn == '.U32':
                pc += 4
            elif mn == '.BYTE':
                pc += 1
            elif mn == '.ALIGN':
                align = self._eval_expr(line.args[0], line.line_no)
                rem = pc % align
                if rem != 0:
                    pc += (align - rem)
            elif mn == '.EQU':
                name = line.args[0].strip()
                val = self._eval_expr(line.args[1], line.line_no)
                self.equ[name] = val
            elif mn == '.TEMPLATE':
                pass  # meta, no output
            elif mn == '.ORG':
                pc = self._eval_expr(line.args[0], line.line_no)
            elif mn == '.SPACE':
                pc += self._eval_expr(line.args[0], line.line_no)
            elif mn == 'LI32':
                pc += 8  # always two words
            elif mn == 'LI':
                # Check if value fits in 16-bit signed immediate
                try:
                    imm = self._eval_expr(line.args[1], line.line_no)
                    if -32768 <= imm <= 32767:
                        pc += 4
                    else:
                        pc += 8  # auto-expand to LI32
                except Exception:
                    pc += 4  # assume fits (forward ref label)
            else:
                pc += 4  # all other instructions are 4 bytes

    # -- Pass 2: emit code --

    def _pass2(self):
        pc = self._base

        for line in self._lines:
            if line.mnemonic is None:
                continue

            mn = line.mnemonic
            try:
                if mn == '.WORD':
                    val = self._eval_expr(line.args[0], line.line_no)
                    self._emit64(val)
                    pc += 8
                elif mn == '.U32':
                    val = self._eval_expr(line.args[0], line.line_no)
                    self._emit32(val)
                    pc += 4
                elif mn == '.BYTE':
                    val = self._eval_expr(line.args[0], line.line_no)
                    self.output.append(val & 0xFF)
                    pc += 1
                elif mn == '.ALIGN':
                    align = self._eval_expr(line.args[0], line.line_no)
                    rem = pc % align
                    if rem != 0:
                        pad = align - rem
                        self.output.extend(b'\x00' * pad)
                        pc += pad
                elif mn == '.EQU':
                    pass  # already handled
                elif mn == '.TEMPLATE':
                    idx = self._eval_expr(line.args[0], line.line_no)
                    sub = self._eval_expr(line.args[1], line.line_no)
                    size = self._eval_expr(line.args[2], line.line_no)
                    shape = self._eval_expr(line.args[3], line.line_no)
                    self.templates.append((idx, sub, size, shape))
                elif mn == '.ORG':
                    new_pc = self._eval_expr(line.args[0], line.line_no)
                    if new_pc > pc:
                        self.output.extend(b'\x00' * (new_pc - pc))
                    pc = new_pc
                elif mn == '.SPACE':
                    n = self._eval_expr(line.args[0], line.line_no)
                    self.output.extend(b'\x00' * n)
                    pc += n
                else:
                    result = self._assemble_instruction(mn, line.args, pc, line.line_no)
                    if isinstance(result, tuple):
                        # Two-word instruction (e.g., LI32)
                        self._emit32(result[0])
                        self._emit32(result[1])
                        pc += 8
                    else:
                        self._emit32(result)
                        pc += 4
            except AsmError:
                raise
            except Exception as e:
                raise AsmError(line.line_no, f"{mn}: {e}") from e

    def _emit32(self, val: int):
        self.output.extend((val & 0xFFFF_FFFF).to_bytes(4, 'little'))

    def _emit64(self, val: int):
        self.output.extend((val & 0xFFFF_FFFF_FFFF_FFFF).to_bytes(8, 'little'))

    # -- Expression evaluator --

    def _eval_expr(self, expr: str, line_no: int) -> int:
        """Evaluate an expression that may contain labels, constants, hex/dec."""
        expr = expr.strip()

        # Handle simple arithmetic: expr +/- expr
        # First try direct parse
        try:
            return self._eval_atom(expr, line_no)
        except (ValueError, KeyError):
            pass

        # Try binary ops: +, -, *, <<, >>
        # Find the last + or - not inside parens (simple left-to-right)
        for op_char in ('+', '-'):
            idx = expr.rfind(op_char)
            if idx > 0:
                left = expr[:idx].strip()
                right = expr[idx+1:].strip()
                try:
                    lval = self._eval_expr(left, line_no)
                    rval = self._eval_expr(right, line_no)
                    if op_char == '+':
                        return lval + rval
                    else:
                        return lval - rval
                except (ValueError, KeyError):
                    pass

        # Try shift
        if '<<' in expr:
            parts = expr.split('<<', 1)
            return self._eval_expr(parts[0], line_no) << self._eval_expr(parts[1], line_no)
        if '>>' in expr:
            parts = expr.split('>>', 1)
            return self._eval_expr(parts[0], line_no) >> self._eval_expr(parts[1], line_no)

        # Try multiply
        if '*' in expr:
            parts = expr.split('*', 1)
            return self._eval_expr(parts[0], line_no) * self._eval_expr(parts[1], line_no)

        raise AsmError(line_no, f"cannot evaluate expression: {expr}")

    def _eval_atom(self, s: str, line_no: int) -> int:
        s = s.strip()
        if not s:
            raise ValueError("empty")
        # Hex
        if s.startswith('0x') or s.startswith('0X'):
            return int(s, 16)
        # Binary
        if s.startswith('0b') or s.startswith('0B'):
            return int(s, 2)
        # Decimal (including negative)
        if s.lstrip('-').isdigit():
            return int(s)
        # Label or EQU constant
        if s in self.labels:
            return self.labels[s]
        if s in self.equ:
            return self.equ[s]
        raise ValueError(f"unknown symbol: {s}")

    # -- Register parsing --

    def _reg(self, s: str, line_no: int) -> int:
        s = s.strip().lower()
        if s in REGISTER_MAP:
            return REGISTER_MAP[s]
        raise AsmError(line_no, f"unknown register: {s}")

    # -- Instruction assembly --

    def _assemble_instruction(self, mn: str, args: list[str],
                              pc: int, ln: int) -> int:
        """Assemble a single instruction mnemonic + args → 32-bit word."""

        # Helper for relative offset (in words) from pc to target
        def rel_offset(target: int) -> int:
            return (target - pc) // 4

        # ---------------------------------------------------------------
        # Raw arithmetic: ADD, SUB, MUL, DIV, MOD
        # ---------------------------------------------------------------
        if mn == 'ADD':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_RAW, rd, rs1, rs2, FUNC_ADD)
        if mn == 'SUB':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_RAW, rd, rs1, rs2, FUNC_SUB)
        if mn == 'MUL':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_RAW, rd, rs1, rs2, FUNC_MUL)
        if mn == 'DIV':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_RAW, rd, rs1, rs2, FUNC_DIV)
        if mn == 'MOD':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_RAW, rd, rs1, rs2, FUNC_MOD)

        # ---------------------------------------------------------------
        # Bitwise: AND, OR, XOR, SHL, SHR, ASR, NOT
        # ---------------------------------------------------------------
        if mn == 'AND':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.BITWISE, rd, rs1, rs2, FUNC_AND)
        if mn == 'OR':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.BITWISE, rd, rs1, rs2, FUNC_OR)
        if mn == 'XOR':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.BITWISE, rd, rs1, rs2, FUNC_XOR)
        if mn == 'SHL':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.BITWISE, rd, rs1, rs2, FUNC_SHL)
        if mn == 'SHR':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.BITWISE, rd, rs1, rs2, FUNC_SHR)
        if mn == 'ASR':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.BITWISE, rd, rs1, rs2, FUNC_ASR)
        if mn == 'NOT':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            return encode_r(Op.BITWISE, rd, rs1, 0, FUNC_NOT)

        # ---------------------------------------------------------------
        # Tagged arithmetic: ADD.FIX, SUB.FIX, MUL.FIX, DIV.FIX
        # ---------------------------------------------------------------
        if mn == 'ADD.FIX':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_FIX, rd, rs1, rs2, FUNC_ADD_FIX)
        if mn == 'SUB.FIX':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_FIX, rd, rs1, rs2, FUNC_SUB_FIX)
        if mn == 'MUL.FIX':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_FIX, rd, rs1, rs2, FUNC_MUL_FIX)
        if mn == 'DIV.FIX':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.ARITH_FIX, rd, rs1, rs2, FUNC_DIV_FIX)

        # ADD.FIX.IMM rd, rs1, imm16
        if mn == 'ADD.FIX.IMM':
            rd = self._reg(args[0], ln)
            rs1 = self._reg(args[1], ln)
            imm = self._eval_expr(args[2], ln)
            return encode_i(Op.ADD_FIX_IMM, rd, rs1, imm)

        # ---------------------------------------------------------------
        # Compare: CMP, EQ
        # ---------------------------------------------------------------
        if mn == 'CMP':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.CMP_TAGGED, rd, rs1, rs2, FUNC_CMP)
        if mn == 'EQ':
            rd, rs1, rs2 = self._reg(args[0], ln), self._reg(args[1], ln), self._reg(args[2], ln)
            return encode_r(Op.CMP_TAGGED, rd, rs1, rs2, FUNC_EQ)

        # ---------------------------------------------------------------
        # Type tests: TST.FIX, TST.REF, TST.CONS, TST.SPECIAL
        # ---------------------------------------------------------------
        if mn == 'TST.FIX':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            return encode_i(Op.TST, rd, rs1, 0)
        if mn == 'TST.REF':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            return encode_i(Op.TST, rd, rs1, 1)
        if mn == 'TST.CONS':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            return encode_i(Op.TST, rd, rs1, 2)
        if mn == 'TST.SPECIAL':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            return encode_i(Op.TST, rd, rs1, 3)
        if mn == 'TST.SHAPE':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            shape = self._eval_expr(args[2], ln)
            return encode_i(Op.TST_SHAPE, rd, rs1, shape)

        # ---------------------------------------------------------------
        # Loads/stores (raw): LDR, STR
        # ---------------------------------------------------------------
        if mn == 'LDR':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_i(Op.LDR, rd, rs1, off)
        if mn == 'STR':
            # Format S: rs=base, rt=value, imm11=offset
            rs = self._reg(args[0], ln)
            rt = self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_s(Op.STR, rs, rt, 0, off)

        # ---------------------------------------------------------------
        # Sub-word loads/stores: LDB, LDH, LDW, STB, STH, STW
        # ---------------------------------------------------------------
        if mn == 'LDB':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_i(Op.LDB, rd, rs1, off)
        if mn == 'LDH':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_i(Op.LDH, rd, rs1, off)
        if mn == 'LDW':
            rd, rs1 = self._reg(args[0], ln), self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_i(Op.LDW, rd, rs1, off)
        if mn == 'STB':
            rs = self._reg(args[0], ln)
            rt = self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_s(Op.STB, rs, rt, 0, off)
        if mn == 'STH':
            rs = self._reg(args[0], ln)
            rt = self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_s(Op.STH, rs, rt, 0, off)
        if mn == 'STW':
            rs = self._reg(args[0], ln)
            rt = self._reg(args[1], ln)
            off = self._eval_expr(args[2], ln) if len(args) > 2 else 0
            return encode_s(Op.STW, rs, rt, 0, off)

        # ---------------------------------------------------------------
        # LI, LUI
        # ---------------------------------------------------------------
        if mn == 'LI':
            rd = self._reg(args[0], ln)
            imm = self._eval_expr(args[1], ln)
            if -32768 <= imm <= 32767:
                return encode_i(Op.LI, rd, 0, imm)
            else:
                # Auto-expand to LI32 (two-word instruction)
                return (encode_i(Op.LI32, rd, 0, 0), imm & 0xFFFF_FFFF)
        if mn == 'LI32':
            rd = self._reg(args[0], ln)
            imm = self._eval_expr(args[1], ln)
            return (encode_i(Op.LI32, rd, 0, 0), imm & 0xFFFF_FFFF)
        if mn == 'LUI':
            rd = self._reg(args[0], ln)
            imm = self._eval_expr(args[1], ln)
            return encode_i(Op.LUI, rd, 0, imm)

        # ---------------------------------------------------------------
        # Branches: BR, BR.T, BR.NIL, BR.FIX.LT, BR.FIX.EQ, BR.FIX.GT, BR.EQ
        # ---------------------------------------------------------------
        if mn == 'BR':
            target = self._eval_expr(args[0], ln)
            return encode_b(Op.BR, 0, 0, rel_offset(target))
        if mn == 'BR.T':
            rs1 = self._reg(args[0], ln)
            target = self._eval_expr(args[1], ln)
            return encode_b(Op.BR_COND, rs1, BR_T, rel_offset(target))
        if mn == 'BR.NIL':
            rs1 = self._reg(args[0], ln)
            target = self._eval_expr(args[1], ln)
            return encode_b(Op.BR_COND, rs1, BR_NIL, rel_offset(target))
        if mn == 'BR.FIX.LT':
            rs1 = self._reg(args[0], ln)
            target = self._eval_expr(args[1], ln)
            return encode_b(Op.BR_COND, rs1, BR_FIX_LT, rel_offset(target))
        if mn == 'BR.FIX.EQ':
            rs1 = self._reg(args[0], ln)
            target = self._eval_expr(args[1], ln)
            return encode_b(Op.BR_COND, rs1, BR_FIX_EQ, rel_offset(target))
        if mn == 'BR.FIX.GT':
            rs1 = self._reg(args[0], ln)
            target = self._eval_expr(args[1], ln)
            return encode_b(Op.BR_COND, rs1, BR_FIX_GT, rel_offset(target))
        if mn == 'BR.EQ':
            rs1 = self._reg(args[0], ln)
            target = self._eval_expr(args[1], ln)
            return encode_b(Op.BR_COND, rs1, BR_EQ, rel_offset(target))

        # ---------------------------------------------------------------
        # Stack: PUSH, POP, PUSH.MULTI, POP.MULTI
        # ---------------------------------------------------------------
        if mn == 'PUSH':
            rd = self._reg(args[0], ln)
            return encode_r(Op.PUSH_POP, rd, 0, 0, FUNC_PUSH)
        if mn == 'POP':
            rd = self._reg(args[0], ln)
            return encode_r(Op.PUSH_POP, rd, 0, 0, FUNC_POP)
        if mn == 'PUSH.MULTI':
            bank = self._eval_expr(args[0], ln)
            mask = self._eval_expr(args[1], ln)
            return encode_i(Op.PUSH_MULTI, bank, 0, mask)
        if mn == 'POP.MULTI':
            bank = self._eval_expr(args[0], ln)
            mask = self._eval_expr(args[1], ln)
            return encode_i(Op.POP_MULTI, bank, 0, mask)

        # ---------------------------------------------------------------
        # Allocation: ALLOC, ALLOC.CONS, ALLOCV, ALLOC.CLOSURE
        # ---------------------------------------------------------------
        if mn == 'ALLOC':
            rd = self._reg(args[0], ln)
            n_words = self._eval_expr(args[1], ln)
            tmpl = self._eval_expr(args[2], ln)
            payload = ((rd & 0x1F) << 21) | ((n_words & 0x1F) << 16) | (tmpl & 0xFFFF)
            return encode_x(Op.ALLOC, payload)
        if mn == 'ALLOC.CONS':
            rd = self._reg(args[0], ln)
            rs1 = self._reg(args[1], ln)
            rs2 = self._reg(args[2], ln)
            return encode_r(Op.ALLOC_CONS, rd, rs1, rs2, 0)
        if mn == 'ALLOCV':
            rd = self._reg(args[0], ln)
            rs1 = self._reg(args[1], ln)  # size register
            tmpl = self._eval_expr(args[2], ln)
            return encode_i(Op.ALLOCV, rd, rs1, tmpl)
        if mn == 'ALLOC.CLOSURE':
            # ALLOC.CLOSURE rd, rs_code, env_size
            # Executor: bits 25:21=rd, 20:16=rs_code, 15:11=env_size
            rd = self._reg(args[0], ln)
            rs_code = self._reg(args[1], ln)
            env_size = self._eval_expr(args[2], ln)
            payload = ((rd & 0x1F) << 21) | ((rs_code & 0x1F) << 16) | ((env_size & 0x1F) << 11)
            return encode_x(Op.ALLOC_CLOSURE, payload)

        # ---------------------------------------------------------------
        # Field access: LD.FLD, LD.CAR, LD.CDR, ST.FLD, ST.WB, ST.CAR, ST.CDR
        # ---------------------------------------------------------------
        if mn == 'LD.FLD':
            rd = self._reg(args[0], ln)
            rs1 = self._reg(args[1], ln)
            fld = self._eval_expr(args[2], ln)
            return encode_i(Op.LD, rd, rs1, fld)
        if mn == 'LD.CAR':
            rd = self._reg(args[0], ln)
            rs1 = self._reg(args[1], ln)
            return encode_i(Op.LD_CAR_CDR, rd, rs1, 0)
        if mn == 'LD.CDR':
            rd = self._reg(args[0], ln)
            rs1 = self._reg(args[1], ln)
            return encode_i(Op.LD_CAR_CDR, rd, rs1, 1)
        if mn == 'ST.FLD':
            rs = self._reg(args[0], ln)   # base (object ref)
            rt = self._reg(args[1], ln)   # value
            fld = self._eval_expr(args[2], ln)
            return encode_s(Op.ST, rs, rt, fld)
        if mn == 'ST.WB':
            rs = self._reg(args[0], ln)
            rt = self._reg(args[1], ln)
            fld = self._eval_expr(args[2], ln)
            return encode_s(Op.ST_WB, rs, rt, fld)
        if mn == 'ST.CAR':
            rs = self._reg(args[0], ln)
            rt = self._reg(args[1], ln)
            return encode_s(Op.ST_CAR_CDR, rs, rt, 0)
        if mn == 'ST.CDR':
            rs = self._reg(args[0], ln)
            rt = self._reg(args[1], ln)
            return encode_s(Op.ST_CAR_CDR, rs, rt, 1)

        # ---------------------------------------------------------------
        # Dispatch: CALL.IC, IC.INSTALL, CALL.DIRECT, CALL.CLOSURE, RET,
        #           TAILCALL.IC, TAILCALL.DIRECT
        # ---------------------------------------------------------------
        if mn == 'CALL.IC':
            rd = self._reg(args[0], ln)  # receiver
            return encode_i(Op.CALL_IC, rd, 0, 0)
        if mn == 'IC.INSTALL':
            rd = self._reg(args[0], ln)   # callsite PC reg
            rs1 = self._reg(args[1], ln)  # receiver reg
            rs2 = self._reg(args[2], ln)  # code entry reg
            return encode_r(Op.IC_INSTALL, rd, rs1, rs2, 0)
        if mn == 'CALL.DIRECT':
            target = self._eval_expr(args[0], ln)
            return encode_i(Op.CALL_DIRECT, 0, 0, rel_offset(target))
        if mn == 'CALL.CLOSURE':
            rd = self._reg(args[0], ln)
            return encode_i(Op.CALL_CLOSURE, rd, 0, 0)
        if mn == 'RET':
            return encode_x(Op.RET, 0)
        if mn == 'TAILCALL.IC':
            rd = self._reg(args[0], ln)
            return encode_i(Op.TAILCALL_IC, rd, 0, 0)
        if mn == 'TAILCALL.DIRECT':
            target = self._eval_expr(args[0], ln)
            return encode_i(Op.TAILCALL_DIR, 0, 0, rel_offset(target))

        # ---------------------------------------------------------------
        # Messaging: SEND, RECV, TRY.RECV
        # ---------------------------------------------------------------
        if mn == 'SEND':
            rs_q = self._reg(args[0], ln)   # queue desc
            rt_v = self._reg(args[1], ln)   # value
            return encode_s(Op.SEND, rs_q, rt_v, 0)
        if mn == 'RECV':
            rd = self._reg(args[0], ln)
            rs1 = self._reg(args[1], ln)
            return encode_i(Op.RECV, rd, rs1, 0)
        if mn == 'TRY.RECV':
            rd = self._reg(args[0], ln)     # value dest
            rd2 = self._reg(args[1], ln)    # status dest
            rs1 = self._reg(args[2], ln)    # queue
            return encode_r(Op.RECV, rd, rs1, 0, rd2)

        # ---------------------------------------------------------------
        # Atomics: CAS.TAGGED, FAA, FENCE.GC
        # ---------------------------------------------------------------
        if mn == 'CAS.TAGGED':
            rd = self._reg(args[0], ln)
            rs_addr = self._reg(args[1], ln)
            rs_exp = self._reg(args[2], ln)
            rt_new = self._reg(args[3], ln)
            return encode_r(Op.CAS_TAGGED, rd, rs_addr, rs_exp, rt_new)
        if mn == 'FAA':
            rd = self._reg(args[0], ln)
            rs_addr = self._reg(args[1], ln)
            rs_delta = self._reg(args[2], ln)
            return encode_r(Op.FAA_FENCE, rd, rs_addr, rs_delta, 0)
        if mn == 'FENCE.GC':
            return encode_r(Op.FAA_FENCE, 0, 0, 0, 0x1F)

        # ---------------------------------------------------------------
        # System: TRAP, ERET, TILE.ID, THREAD.ID, CYCLE, HALT, NOP
        # ---------------------------------------------------------------
        if mn == 'TRAP':
            code = self._eval_expr(args[0], ln)
            return encode_x(Op.TRAP, code & 0x03FF_FFFF)
        if mn == 'ERET':
            return encode_x(Op.ERET, 0)
        if mn == 'TILE.ID':
            rd = self._reg(args[0], ln)
            return encode_r(Op.SYS_INFO, rd, 0, 0, FUNC_TILE_ID)
        if mn == 'THREAD.ID':
            rd = self._reg(args[0], ln)
            return encode_r(Op.SYS_INFO, rd, 0, 0, FUNC_THREAD_ID)
        if mn == 'CYCLE':
            rd = self._reg(args[0], ln)
            return encode_r(Op.SYS_INFO, rd, 0, 0, FUNC_CYCLE)
        if mn == 'TRAP.CAUSE':
            rd = self._reg(args[0], ln)
            return encode_r(Op.SYS_INFO, rd, 0, 0, FUNC_TRAP_CAUSE)
        if mn == 'TRAP.PC':
            rd = self._reg(args[0], ln)
            return encode_r(Op.SYS_INFO, rd, 0, 0, FUNC_TRAP_PC)
        if mn == 'HALT':
            return encode_x(Op.HALT_NOP, 0)
        if mn == 'NOP':
            return encode_x(Op.HALT_NOP, 1 << 21)

        # ---------------------------------------------------------------
        # Pseudo-instructions
        # ---------------------------------------------------------------
        if mn == 'MOV':
            # MOV rd, rs → ADD rd, rs, r0
            rd = self._reg(args[0], ln)
            rs = self._reg(args[1], ln)
            return encode_r(Op.ARITH_RAW, rd, rs, 0, FUNC_ADD)
        if mn == 'JR':
            # JR rs → jump to address in register (uses rd field for register)
            rs = self._reg(args[0], ln)
            return encode_x(Op.JR, rs << 21)

        raise AsmError(ln, f"unknown mnemonic: {mn}")
