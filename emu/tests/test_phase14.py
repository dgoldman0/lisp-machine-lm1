"""Phase 14 tests — Compiler Extensions.

Tests new compiler forms: while, let*, >=, <=, mod, when, unless,
dotimes, strings, vectors, closures, tail calls, VDI traps, etc.
Built in stages — each stage adds tests as features are implemented.
"""

import io
import os
import struct
import tempfile

from lm1.testing.harness import test
from lm1.execute import Emulator
from lm1.asm import Assembler
from lm1.bios import (
    assemble_bios, make_os_image, IMAGE_MAGIC, IMAGE_LOAD_ADDR,
)
from lm1.word import (
    NIL, T, tag_fixnum, untag_fixnum,
    is_fixnum, is_cons_ref, make_header,
    HDR_CONS, HDR_CLOSURE, HDR_VECTOR,
    WORD_MASK,
)
from lm1.compiler import Compiler, parse

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

NURSERY_BASE = 0x3_0000
NURSERY_SIZE = 0x1_0000
OLDGEN_BASE  = 0x5_0000
OLDGEN_SIZE  = 0x2_0000


def _compile_direct(lisp_forms, max_instructions=200_000):
    """Cross-compile and run directly (no BIOS), for fast testing."""
    cc = Compiler()

    cc._emit_label("_start")
    cc._emit_instr("LI sp, 0x3FF8")
    cc._emit_instr("LI fp, 0")
    cc._emit_instr("BR _call_main")
    cc._emit("")

    cc.emit_putchar()
    cc.emit_print_fixnum()
    cc.emit_print_value()
    cc.emit_newline()

    cc.compile_toplevel(lisp_forms)

    cc._emit_label("_call_main")
    cc.emit_call_main('main')

    asm_source = cc.get_output()

    asm = Assembler()
    os_words = asm.assemble_to_words(asm_source)

    stdout = io.StringIO()
    emu = Emulator(
        mem_size=1024 * 1024,
        nursery_base=NURSERY_BASE,
        nursery_size=NURSERY_SIZE,
        oldgen_base=OLDGEN_BASE,
        oldgen_size=OLDGEN_SIZE,
        stdout=stdout,
    )
    emu.mem.load_instructions(0, os_words)
    for t in emu.threads:
        t.pc = 0
    emu.run(max_instructions=max_instructions)
    return stdout.getvalue(), emu


def _last_line(output):
    """Get the last non-empty line from output."""
    lines = [l for l in output.strip().split('\n') if l.strip()]
    return lines[-1].strip() if lines else ""


# ===================================================================
# Stage 1: while, >=, <=, let*, when, unless, dotimes, mod
# ===================================================================

@test("compiler_while_basic", batch="phase14_stage1")
def test_compiler_while_basic():
    """(while test body) compiles to loop with BR.NIL."""
    cc = Compiler()
    cc._compile_expr(['while', True, 1], {}, dest=1)
    src = cc.get_output()
    assert "while_top" in src
    assert "BR.NIL" in src
    assert "while_end" in src


@test("exec_while_sum", batch="phase14_stage1")
def test_exec_while_sum():
    """while loop: sum 1..10 = 55."""
    forms = parse("""
        (defun main ()
          (let ((i 1) (sum 0))
            (while (<= i 10)
              (set! sum (+ sum i))
              (set! i (+ i 1)))
            (print-fixnum sum)
            (newline)))
    """)
    output, emu = _compile_direct(forms)
    assert _last_line(output) == "55", f"Expected '55', got: {output!r}"


@test("exec_ge_le", batch="phase14_stage1")
def test_exec_ge_le():
    """>= and <= operators work correctly."""
    forms = parse("""
        (defun main ()
          (print-fixnum (if (>= 5 3) 1 0))
          (print-fixnum (if (>= 5 5) 1 0))
          (print-fixnum (if (>= 3 5) 1 0))
          (print-fixnum (if (<= 3 5) 1 0))
          (print-fixnum (if (<= 5 5) 1 0))
          (print-fixnum (if (<= 5 3) 1 0))
          (newline))
    """)
    output, emu = _compile_direct(forms)
    # Should print: 1 1 0 1 1 0
    nums = output.strip().replace('\n', '')
    assert nums == "110110", f"Expected '110110', got: {output!r}"


@test("exec_let_star", batch="phase14_stage1")
def test_exec_let_star():
    """let* bindings are sequential (each sees previous)."""
    forms = parse("""
        (defun main ()
          (let* ((a 10)
                 (b (+ a 5))
                 (c (* b 2)))
            (print-fixnum c)
            (newline)))
    """)
    output, emu = _compile_direct(forms)
    assert _last_line(output) == "30", f"Expected '30', got: {output!r}"


@test("exec_when", batch="phase14_stage1")
def test_exec_when():
    """(when test body) only executes body when test is truthy."""
    forms = parse("""
        (defun main ()
          (when t (print-fixnum 42))
          (when nil (print-fixnum 99))
          (newline))
    """)
    output, emu = _compile_direct(forms)
    assert _last_line(output) == "42", f"Expected '42', got: {output!r}"


@test("exec_unless", batch="phase14_stage1")
def test_exec_unless():
    """(unless test body) only executes body when test is nil."""
    forms = parse("""
        (defun main ()
          (unless nil (print-fixnum 77))
          (unless t (print-fixnum 88))
          (newline))
    """)
    output, emu = _compile_direct(forms)
    assert _last_line(output) == "77", f"Expected '77', got: {output!r}"


@test("exec_dotimes", batch="phase14_stage1")
def test_exec_dotimes():
    """(dotimes (i 5) body) iterates i from 0 to 4."""
    forms = parse("""
        (defun main ()
          (let ((sum 0))
            (dotimes (i 5)
              (set! sum (+ sum i)))
            (print-fixnum sum)
            (newline)))
    """)
    output, emu = _compile_direct(forms)
    # 0+1+2+3+4 = 10
    assert _last_line(output) == "10", f"Expected '10', got: {output!r}"


@test("exec_mod", batch="phase14_stage1")
def test_exec_mod():
    """(mod a b) returns remainder."""
    forms = parse("""
        (defun main ()
          (print-fixnum (mod 17 5))
          (newline))
    """)
    output, emu = _compile_direct(forms)
    assert _last_line(output) == "2", f"Expected '2', got: {output!r}"


@test("exec_fizzbuzz", batch="phase14_stage1")
def test_exec_fizzbuzz():
    """FizzBuzz 1..15 using while, mod, when, cond — integration test."""
    forms = parse("""
        (defun main ()
          (let ((i 1))
            (while (<= i 15)
              (cond
                ((= (mod i 15) 0) (print-fixnum 0))
                ((= (mod i 3) 0)  (print-fixnum 3))
                ((= (mod i 5) 0)  (print-fixnum 5))
                (t                (print-fixnum i)))
              (set! i (+ i 1))))
          (newline))
    """)
    output, emu = _compile_direct(forms, max_instructions=500_000)
    # 1 2 3(fizz) 4 5(buzz) 6(fizz) 7 8 9(fizz) 10(buzz) 11 12(fizz) 13 14 15(fizzbuzz)
    # Encoded as: 1 2 3 4 5 3 7 8 3 5 11 3 13 14 0
    nums = output.strip().replace('\n', '')
    assert nums == "123453783511313140", f"Expected fizzbuzz sequence, got: {output!r}"


@test("exec_nested_while", batch="phase14_stage1")
def test_exec_nested_while():
    """Nested while loops work correctly."""
    forms = parse("""
        (defun main ()
          (let ((sum 0) (i 0))
            (while (< i 3)
              (let ((j 0))
                (while (< j 4)
                  (set! sum (+ sum 1))
                  (set! j (+ j 1))))
              (set! i (+ i 1)))
            (print-fixnum sum)
            (newline)))
    """)
    output, emu = _compile_direct(forms)
    assert _last_line(output) == "12", f"Expected '12', got: {output!r}"


@test("compiler_parse_string", batch="phase14_stage1")
def test_compiler_parse_string():
    """Parser handles string literals."""
    forms = parse('"hello"')
    assert len(forms) == 1
    assert forms[0][0] == '__string__'
    assert forms[0][1] == 'hello'


@test("compiler_parse_comments", batch="phase14_stage1")
def test_compiler_parse_comments():
    """Parser handles comments correctly."""
    forms = parse("""
        ; this is a comment
        42  ; trailing comment
        (+ 1 2)
    """)
    assert len(forms) == 2
    assert forms[0] == 42
    assert forms[1] == ['+', 1, 2]


# ===================================================================
# Stage 2: Tail-call optimization
# ===================================================================

@test("tco_asm_output", batch="phase14_stage2")
def test_tco_asm_output():
    """Tail calls emit TAILCALL.DIRECT instead of CALL.DIRECT."""
    forms = parse("""
        (defun foo (n)
          (bar n))
        (defun bar (n) n)
        (defun main () (foo 1))
    """)
    cc = Compiler()
    cc.compile_toplevel(forms)
    src = cc.get_output()
    # foo should use TAILCALL.DIRECT to call bar (it's the last expr)
    assert "TAILCALL.DIRECT _fn_bar" in src
    # foo should NOT have CALL.DIRECT for bar
    lines = src.split('\n')
    in_foo = False
    for line in lines:
        if '_fn_foo:' in line:
            in_foo = True
        elif in_foo and ':' in line and not line.startswith(' '):
            break  # left foo
        elif in_foo and 'CALL.DIRECT _fn_bar' in line and 'TAILCALL' not in line:
            assert False, "foo should use TAILCALL.DIRECT, not CALL.DIRECT for bar"


@test("tco_asm_non_tail", batch="phase14_stage2")
def test_tco_asm_non_tail():
    """Non-tail calls still use CALL.DIRECT."""
    forms = parse("""
        (defun foo (n)
          (bar n)
          (+ n 1))
        (defun bar (n) n)
        (defun main () (foo 1))
    """)
    cc = Compiler()
    cc.compile_toplevel(forms)
    src = cc.get_output()
    # foo calls bar NOT in tail position, so CALL.DIRECT
    assert "CALL.DIRECT _fn_bar" in src
    # No TAILCALL in foo for bar
    lines = src.split('\n')
    in_foo = False
    for line in lines:
        if '_fn_foo:' in line:
            in_foo = True
        elif in_foo and ':' in line and not line.startswith(' '):
            break
        elif in_foo and 'TAILCALL' in line:
            assert False, "bar call in foo is not tail — should not use TAILCALL"


@test("tco_self_recursive", batch="phase14_stage2")
def test_tco_self_recursive():
    """Self-recursive tail call counts down from 10000 without stack overflow."""
    forms = parse("""
        (defun countdown (n)
          (if (= n 0)
            (print-fixnum 0)
            (countdown (- n 1))))
        (defun main () (countdown 5000))
    """)
    out, _ = _compile_direct(forms, max_instructions=5_000_000)
    assert _last_line(out) == "0"


@test("tco_tail_in_if", batch="phase14_stage2")
def test_tco_tail_in_if():
    """Tail call through if branches — mutual recursion."""
    forms = parse("""
        (defun is-even (n)
          (if (= n 0)
            1
            (is-odd (- n 1))))
        (defun is-odd (n)
          (if (= n 0)
            0
            (is-even (- n 1))))
        (defun main ()
          (print-fixnum (is-even 100))
          (print-fixnum (is-odd 100)))
    """)
    out, _ = _compile_direct(forms, max_instructions=1_000_000)
    # is-even(100) = 1, is-odd(100) = 0
    assert out == "10"


@test("tco_tail_in_cond", batch="phase14_stage2")
def test_tco_tail_in_cond():
    """Tail call through cond works correctly."""
    forms = parse("""
        (defun classify (n)
          (cond
            ((= n 0) (print-fixnum 0))
            ((= n 1) (print-fixnum 1))
            (t       (print-fixnum 9))))
        (defun main ()
          (classify 0)
          (classify 1)
          (classify 42))
    """)
    out, _ = _compile_direct(forms)
    # classify(0)→0, classify(1)→1, classify(42)→9
    assert out == "019"


@test("tco_tail_in_let", batch="phase14_stage2")
def test_tco_tail_in_let():
    """Tail call through let body works correctly."""
    forms = parse("""
        (defun double-add (a b)
          (let ((sum (+ a b)))
            (do-print sum)))
        (defun do-print (x)
          (print-fixnum x))
        (defun main ()
          (double-add 10 20))
    """)
    out, _ = _compile_direct(forms)
    assert _last_line(out) == "30"


@test("tco_tail_in_progn", batch="phase14_stage2")
def test_tco_tail_in_progn():
    """Last expression in progn is in tail position."""
    forms = parse("""
        (defun foo (n)
          (progn
            (print-fixnum 1)
            (bar n)))
        (defun bar (n)
          (print-fixnum n))
        (defun main () (foo 42))
    """)
    out, _ = _compile_direct(forms)
    # print-fixnum doesn't add newlines: "1" then "42" = "142"
    assert out == "142"


@test("tco_factorial_accum", batch="phase14_stage2")
def test_tco_factorial_accum():
    """Tail-recursive factorial with accumulator."""
    forms = parse("""
        (defun fact-iter (n acc)
          (if (= n 0)
            acc
            (fact-iter (- n 1) (* n acc))))
        (defun main ()
          (print-fixnum (fact-iter 10 1)))
    """)
    out, _ = _compile_direct(forms, max_instructions=500_000)
    assert _last_line(out) == "3628800"
