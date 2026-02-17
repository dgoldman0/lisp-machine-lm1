"""Phase 8 tests — Lispos Kernel.

Tests the cross-compiler, compiled Lisp programs running on the
emulator, and the REPL pipeline:
  reader → eval → printer → (+ 1 2) = 3
  (defun fact ...) → (fact 10) = 3628800
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


def _compile_and_run(lisp_forms, max_instructions=50_000):
    """Cross-compile Lisp forms, assemble, boot via BIOS, run, return output."""
    cc = Compiler()

    # OS entry point: setup, then jump over function bodies
    cc._emit("; ===== LM-1 Lispos Runtime =====")
    cc._emit_label("_os_entry")
    cc._emit_instr("LI sp, 0x3FF8")
    cc._emit_instr("LI fp, 0")
    cc._emit_instr("BR _call_main")
    cc._emit("")

    # Emit runtime helpers (function bodies)
    cc.emit_putchar()
    cc.emit_print_fixnum()
    cc.emit_print_value()
    cc.emit_newline()

    # Compile user forms
    cc.compile_toplevel(lisp_forms)

    # Call main and halt
    cc._emit_label("_call_main")
    cc.emit_call_main('main')

    asm_source = cc.get_output()

    # Assemble to words
    asm = Assembler()
    os_words = asm.assemble_to_words(asm_source)

    # Package as OS image
    os_image = make_os_image(os_words, entry_offset=32)

    # Write block device
    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as f:
        fname = f.name
        # Pad to at least 4K block boundary
        f.write(os_image)
        rest = (4096 - len(os_image) % 4096) % 4096
        f.write(b'\x00' * rest)

    try:
        stdout = io.StringIO()
        emu = Emulator(
            mem_size=1024 * 1024,
            nursery_base=NURSERY_BASE,
            nursery_size=NURSERY_SIZE,
            oldgen_base=OLDGEN_BASE,
            oldgen_size=OLDGEN_SIZE,
            stdout=stdout,
            block_device=fname,
        )
        words, labels = assemble_bios()
        emu.load_bios(words, base=0)
        emu.run(max_instructions=max_instructions)
        return stdout.getvalue(), emu
    finally:
        os.unlink(fname)


def _compile_direct(lisp_forms, max_instructions=50_000):
    """Cross-compile and run directly (no BIOS), for faster testing."""
    cc = Compiler()

    # Emit a simple entry point that jumps over function defs
    cc._emit_label("_start")
    cc._emit_instr("LI sp, 0x3FF8")
    cc._emit_instr("LI fp, 0")
    cc._emit_instr("BR _call_main")
    cc._emit("")

    # Emit runtime helpers (these are function bodies, not entry code)
    cc.emit_putchar()
    cc.emit_print_fixnum()
    cc.emit_print_value()
    cc.emit_newline()

    # Compile user forms (defuns become labeled function bodies)
    cc.compile_toplevel(lisp_forms)

    # Call main and halt
    cc._emit_label("_call_main")
    cc.emit_call_main('main')

    asm_source = cc.get_output()

    # Assemble
    asm = Assembler()
    os_words = asm.assemble_to_words(asm_source)

    # Run directly
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


# ===================================================================
# Batch: phase8_compiler — Cross-compiler basics
# ===================================================================

@test("compiler_parse_simple", batch="phase8_compiler")
def test_compiler_parse_simple():
    """Parser handles basic Lisp forms."""
    # Integers
    assert parse("42") == [42]
    assert parse("-3") == [-3]
    # Lists
    assert parse("(1 2 3)") == [[1, 2, 3]]
    # Nested
    assert parse("(+ 1 (- 3 2))") == [['+', 1, ['-', 3, 2]]]
    # Multiple forms
    assert parse("1 2 3") == [1, 2, 3]
    # nil and t
    assert parse("nil") == [None]
    assert parse("t") == [True]
    # Quote
    assert parse("'x") == [['quote', 'x']]


@test("compiler_emits_fixnum", batch="phase8_compiler")
def test_compiler_emits_fixnum():
    """Compiler emits correct LI for fixnum literals."""
    cc = Compiler()
    cc._compile_expr(42, {}, dest=1)
    lines = [l.strip() for l in cc.lines if l.strip()]
    assert any("LI r1, 84" in l for l in lines), f"Expected LI r1, 84: {lines}"


@test("compiler_emits_nil_t", batch="phase8_compiler")
def test_compiler_emits_nil_t():
    """Compiler emits correct immediates for nil and t."""
    cc = Compiler()
    cc._compile_expr(None, {}, dest=1)
    cc._compile_expr(True, {}, dest=2)
    lines = [l.strip() for l in cc.lines if l.strip()]
    assert any("LI r1, 5" in l for l in lines)
    assert any("LI r2, 13" in l for l in lines)


@test("compiler_emits_add", batch="phase8_compiler")
def test_compiler_emits_add():
    """Compiler emits ADD.FIX for (+ a b)."""
    cc = Compiler(optimize=False)
    cc._compile_expr(['+', 1, 2], {}, dest=1)
    src = cc.get_output()
    assert "ADD.FIX" in src, f"Expected ADD.FIX in:\n{src}"


@test("compiler_emits_if", batch="phase8_compiler")
def test_compiler_emits_if():
    """Compiler emits branch structure for (if test then else)."""
    cc = Compiler(optimize=False)
    cc._compile_expr(['if', True, 1, 2], {}, dest=1)
    src = cc.get_output()
    assert "BR.NIL" in src
    assert "BR " in src  # unconditional branch


@test("compiler_emits_defun", batch="phase8_compiler")
def test_compiler_emits_defun():
    """Compiler emits labeled function for defun."""
    cc = Compiler()
    cc._compile_toplevel_form(['defun', 'add1', ['x'], ['+', 'x', 1]])
    src = cc.get_output()
    assert "_fn_add1:" in src
    assert "RET" in src
    assert "ADD.FIX" in src


# ===================================================================
# Batch: phase8_exec — Compiled programs running on the emulator
# ===================================================================

@test("exec_add_1_2", batch="phase8_exec")
def test_exec_add_1_2():
    """(+ 1 2) → 3: compile, run, print result."""
    forms = [
        ['defun', 'main', [],
            ['print-fixnum', ['+', 1, 2]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    # Strip any leading banner/whitespace
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "3", f"Expected '3', got: {output!r}"


@test("exec_nested_arith", batch="phase8_exec")
def test_exec_nested_arith():
    """(* (+ 2 3) (- 10 4)) → 30."""
    forms = [
        ['defun', 'main', [],
            ['print-fixnum', ['*', ['+', 2, 3], ['-', 10, 4]]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "30", f"Expected '30', got: {output!r}"


@test("exec_if_true", batch="phase8_exec")
def test_exec_if_true():
    """(if t 42 99) → 42."""
    forms = [
        ['defun', 'main', [],
            ['print-fixnum', ['if', True, 42, 99]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "42", f"Expected '42', got: {output!r}"


@test("exec_if_nil", batch="phase8_exec")
def test_exec_if_nil():
    """(if nil 42 99) → 99."""
    forms = [
        ['defun', 'main', [],
            ['print-fixnum', ['if', None, 42, 99]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "99", f"Expected '99', got: {output!r}"


@test("exec_defun_call", batch="phase8_exec")
def test_exec_defun_call():
    """(defun double (x) (+ x x)) (double 21) → 42."""
    forms = [
        ['defun', 'double', ['x'], ['+', 'x', 'x']],
        ['defun', 'main', [],
            ['print-fixnum', ['double', 21]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "42", f"Expected '42', got: {output!r}"


@test("exec_recursive_fact", batch="phase8_exec")
def test_exec_recursive_fact():
    """(defun fact (n) ...) (fact 10) → 3628800."""
    forms = [
        ['defun', 'fact', ['n'],
            ['if', ['eq', 'n', 0],
                1,
                ['*', 'n', ['fact', ['-', 'n', 1]]]]],
        ['defun', 'main', [],
            ['print-fixnum', ['fact', 10]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms, max_instructions=500_000)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "3628800", f"Expected '3628800', got: {output!r}"


@test("exec_print_list", batch="phase8_exec")
def test_exec_print_list():
    """Print a cons list: (cons 1 (cons 2 nil)) → (1 2)."""
    forms = [
        ['defun', 'main', [],
            ['print', ['cons', 1, ['cons', 2, None]]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "(1 2)", f"Expected '(1 2)', got: {output!r}"


# ===================================================================
# Batch: phase8_bios_boot — Full BIOS boot to compiled OS
# ===================================================================

@test("bios_boot_add", batch="phase8_bios_boot")
def test_bios_boot_add():
    """Full pipeline: BIOS boots → OS prints (+ 1 2) = 3."""
    forms = [
        ['defun', 'main', [],
            ['print-fixnum', ['+', 1, 2]],
            ['newline']],
    ]
    output, emu = _compile_and_run(forms)
    # Output includes BIOS banner + OS output
    assert "3" in output, f"Expected '3' in output: {output!r}"


@test("bios_boot_fact", batch="phase8_bios_boot")
def test_bios_boot_fact():
    """Full pipeline: BIOS boots → OS computes (fact 10) = 3628800."""
    forms = [
        ['defun', 'fact', ['n'],
            ['if', ['eq', 'n', 0],
                1,
                ['*', 'n', ['fact', ['-', 'n', 1]]]]],
        ['defun', 'main', [],
            ['print-fixnum', ['fact', 10]],
            ['newline']],
    ]
    output, emu = _compile_and_run(forms, max_instructions=500_000)
    assert "3628800" in output, f"Expected '3628800' in output: {output!r}"


@test("exec_eq_true", batch="phase8_exec")
def test_exec_eq_true():
    """(eq 5 5) → T → print T."""
    forms = [
        ['defun', 'main', [],
            ['print', ['eq', 5, 5]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "T", f"Expected 'T', got: {output!r}"


@test("exec_eq_false", batch="phase8_exec")
def test_exec_eq_false():
    """(eq 5 3) → NIL → print NIL."""
    forms = [
        ['defun', 'main', [],
            ['print', ['eq', 5, 3]],
            ['newline']],
    ]
    output, emu = _compile_direct(forms)
    lines = [l for l in output.strip().split('\n') if l.strip()]
    assert lines[-1].strip() == "NIL", f"Expected 'NIL', got: {output!r}"
