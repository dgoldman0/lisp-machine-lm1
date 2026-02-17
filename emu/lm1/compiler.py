"""LM-1 Lisp Cross-Compiler.

Compiles Lisp forms (Python data structures) to LM-1 assembly text.
Runs on the host (Python), producing assembly that the Phase 6 assembler
turns into LM-1 machine code.

Lisp representation in Python:
  - int         → fixnum literal
  - str         → symbol name
  - list        → compound form: [op, arg1, arg2, ...]
  - None        → nil
  - True        → t

Calling convention:
  - Arguments in r1..r8 (max 8 args)
  - Return value in r1
  - r9..r15: caller-saved scratch
  - r16..r24: callee-saved (must be preserved across calls)
  - sp (r30), fp (r29), lr (r28): frame management
  - tp (r27): thread-local pointer (reserved)
  - np (r26), nl (r25): nursery pointers (reserved)

Stack frame layout (same as Phase 4):
  high addr  → [arg N] (pushed by caller before CALL)
                ...
  fp+16    → [saved reg area]
  fp+8     → [saved LR]
  fp+0     → [saved FP]  ← FP points here
  sp       → [locals / temps]

The compiler emits assembly text with labels. Functions become labeled
blocks. The Assembler class resolves all labels in its two-pass process.
"""

from __future__ import annotations
from typing import Any

from .word import tag_fixnum, NIL, T, UNBOUND


# Parse S-expression strings into Python data structures
def parse(source: str) -> list:
    """Parse a Lisp source string into a list of forms.

    Returns a list of top-level forms (each form is a Python
    int, str, None, True, or list).
    """
    tokens = _tokenize(source)
    forms = []
    while tokens:
        forms.append(_parse_expr(tokens))
    return forms


def _tokenize(source: str) -> list[str]:
    """Tokenize Lisp source into a flat list of tokens."""
    tokens = []
    i = 0
    while i < len(source):
        ch = source[i]
        if ch in ' \t\n\r':
            i += 1
        elif ch == ';':
            while i < len(source) and source[i] != '\n':
                i += 1
        elif ch in '()\'':
            tokens.append(ch)
            i += 1
        elif ch == '"':
            # String literal
            j = i + 1
            while j < len(source) and source[j] != '"':
                if source[j] == '\\':
                    j += 1
                j += 1
            tokens.append(source[i:j+1])
            i = j + 1
        else:
            j = i
            while j < len(source) and source[j] not in ' \t\n\r();':
                j += 1
            tokens.append(source[i:j])
            i = j
    return tokens


def _parse_expr(tokens: list[str]) -> Any:
    """Parse one expression, consuming tokens from the front."""
    if not tokens:
        raise SyntaxError("unexpected EOF")
    tok = tokens.pop(0)
    if tok == '(':
        lst = []
        while tokens and tokens[0] != ')':
            lst.append(_parse_expr(tokens))
        if not tokens:
            raise SyntaxError("unmatched (")
        tokens.pop(0)  # consume ')'
        return lst
    elif tok == ')':
        raise SyntaxError("unexpected )")
    elif tok == "'":
        return ['quote', _parse_expr(tokens)]
    elif tok == 'nil':
        return None
    elif tok == 't':
        return True
    elif tok.startswith('"'):
        # String literal — store as ['__string__', chars...]
        s = tok[1:-1]
        # Handle escape sequences
        s = s.replace('\\n', '\n').replace('\\t', '\t').replace('\\\\', '\\')
        return ['__string__', s]
    else:
        # Try integer
        try:
            if tok.startswith('0x') or tok.startswith('0X'):
                return int(tok, 16)
            return int(tok)
        except ValueError:
            return tok  # symbol


class CompilerError(Exception):
    pass


class Compiler:
    """Compile Lisp forms to LM-1 assembly text.

    Usage:
        cc = Compiler()
        cc.compile_toplevel(forms)
        assembly_source = cc.get_output()
    """

    # Registers for argument passing (r1..r8)
    ARG_REGS = list(range(1, 9))
    # Return value register
    RET_REG = 1
    # Scratch registers (caller-saved)
    SCRATCH_REGS = list(range(9, 16))
    # Callee-saved registers
    SAVED_REGS = list(range(16, 25))

    def __init__(self):
        self.lines: list[str] = []      # accumulated assembly output
        self._label_counter = 0
        self._string_table: list[tuple[str, str]] = []  # (label, string)
        self._globals: dict[str, str] = {}  # symbol_name → asm_label
        self._functions: dict[str, str] = {}  # func_name → asm_label
        self._builtins: set[str] = set()
        self._inline_builtins: dict[str, str] = {}  # name → handler method name
        self._current_fn_saved_regs: list[int] = []  # callee-saved regs pushed by current defun

        # Register inline builtins
        for name in ('car', 'cdr', 'cons', '+', '-', '*', '/', 'eq',
                      'null', 'atom', 'not', 'fixnump', 'consp',
                      'set-car!', 'set-cdr!', '=', '<', '>',
                      '>=', '<=', 'mod', 'rem',
                      'print-fixnum', 'print', 'newline',
                      'putchar',
                      'string-length', 'string-ref', 'string-set!',
                      'print-string', 'char->fixnum', 'fixnum->char',
                      'make-vector', 'vector-ref', 'vector-set!',
                      'vector-length', 'funcall'):
            self._builtins.add(name)

    def _label(self, prefix: str = "L") -> str:
        self._label_counter += 1
        return f"__{prefix}_{self._label_counter}"

    @staticmethod
    def _sanitize_label(name: str) -> str:
        """Sanitize a symbol name for use as an assembly label."""
        return name.replace('-', '_').replace('?', '_p').replace('!', '_bang')

    def _emit(self, line: str) -> None:
        self.lines.append(line)

    def _emit_label(self, label: str) -> None:
        self._emit(f"{label}:")

    def _emit_instr(self, *parts: str) -> None:
        self._emit("    " + " ".join(parts))

    def _emit_comment(self, text: str) -> None:
        self._emit(f"    ; {text}")

    def get_output(self) -> str:
        """Return accumulated assembly text, including data section."""
        result = list(self.lines)
        # Emit string table data section
        if self._string_table:
            result.append("")
            result.append("; ===== String Data =====")
            for label, text in self._string_table:
                result.append(f"{label}:")
                # Encode as: length word (tagged fixnum), then character bytes, then padding
                encoded = text.encode('utf-8')
                result.append(f"    .WORD {tag_fixnum(len(encoded))}")
                for ch in encoded:
                    result.append(f"    .BYTE {ch}")
                result.append(f"    .BYTE 0")  # null terminator
                result.append(f"    .ALIGN 8")
        # Emit global variable storage
        if self._globals:
            result.append("")
            result.append("; ===== Global Variables =====")
            for name, label in self._globals.items():
                result.append(f"{label}:")
                result.append(f"    .WORD 5")  # initialized to NIL
        return '\n'.join(result) + '\n'

    # ------------------------------------------------------------------
    # Top-level compilation
    # ------------------------------------------------------------------

    def compile_toplevel(self, forms: list) -> None:
        """Compile a list of top-level forms."""
        for form in forms:
            self._compile_toplevel_form(form)

    def _compile_toplevel_form(self, form) -> None:
        """Compile one top-level form."""
        if isinstance(form, list) and len(form) >= 1:
            op = form[0]
            if op == 'defun':
                self._compile_defun(form)
                return
            elif op == 'defvar':
                self._compile_defvar(form)
                return
        # Expression at top level: compile and discard result
        env = {}
        self._compile_expr(form, env, dest=self.RET_REG)

    # ------------------------------------------------------------------
    # defun
    # ------------------------------------------------------------------

    def _compile_defun(self, form) -> None:
        """(defun name (params...) body...)"""
        if len(form) < 4:
            raise CompilerError(f"defun requires name, params, body: {form}")
        name = form[1]
        params = form[2]
        body = form[3:]

        fn_label = f"_fn_{self._sanitize_label(name)}"
        self._functions[name] = fn_label

        self._emit_comment(f"defun {name}")
        self._emit_label(fn_label)

        # Prologue: save callee-saved registers we'll use for params
        env = {}
        used_saved = []
        for i, param in enumerate(params):
            if i >= len(self.ARG_REGS):
                raise CompilerError(f"Too many parameters (max {len(self.ARG_REGS)})")
            save_reg = self.SAVED_REGS[i]
            used_saved.append(save_reg)

        # Push callee-saved registers
        for reg in used_saved:
            self._emit_instr(f"PUSH r{reg}")

        # Move args from arg registers to callee-saved registers
        for i, param in enumerate(params):
            save_reg = self.SAVED_REGS[i]
            self._emit_instr(f"MOV r{save_reg}, r{self.ARG_REGS[i]}")
            env[param] = save_reg

        # Track saved regs for tail-call optimization
        self._current_fn_saved_regs = list(used_saved)

        # Compile body forms (result of last goes to r1, in tail position)
        for i, expr in enumerate(body):
            is_last = (i == len(body) - 1)
            dest = self.RET_REG if is_last else self.SCRATCH_REGS[0]
            self._compile_expr(expr, env, dest=dest, tail=is_last)

        self._current_fn_saved_regs = []

        # Epilogue: restore callee-saved registers and return
        for reg in reversed(used_saved):
            self._emit_instr(f"POP r{reg}")
        self._emit_instr("RET")
        self._emit("")

    # ------------------------------------------------------------------
    # defvar
    # ------------------------------------------------------------------

    def _compile_defvar(self, form) -> None:
        """(defvar name [initial-value])"""
        name = form[1]
        label = f"_var_{name}"
        self._globals[name] = label
        # Don't emit storage here — we'll emit it in the data section

    # ------------------------------------------------------------------
    # Expression compilation
    # ------------------------------------------------------------------

    def _compile_expr(self, expr, env: dict, dest: int = 1, tail: bool = False) -> None:
        """Compile an expression, putting the result in register `dest`."""
        if expr is None:
            self._emit_instr(f"LI r{dest}, 5")  # NIL
        elif expr is True:
            self._emit_instr(f"LI r{dest}, 13")  # T
        elif isinstance(expr, int):
            tagged = tag_fixnum(expr)
            if -32768 <= tagged <= 32767:
                self._emit_instr(f"LI r{dest}, {tagged}")
            else:
                # Need LUI + ADD for large fixnums
                lo = tagged & 0xFFFF
                hi = (tagged >> 16) & 0xFFFF
                self._emit_instr(f"LUI r{dest}, {hi}")
                if lo != 0:
                    self._emit_instr(f"LI r{self.SCRATCH_REGS[0]}, {lo}")
                    self._emit_instr(f"ADD r{dest}, r{dest}, r{self.SCRATCH_REGS[0]}")
        elif isinstance(expr, str):
            self._compile_symbol_ref(expr, env, dest)
        elif isinstance(expr, list):
            if len(expr) == 0:
                self._emit_instr(f"LI r{dest}, 5")  # NIL = empty list
            else:
                self._compile_form(expr, env, dest, tail=tail)
        else:
            raise CompilerError(f"Unknown expression type: {type(expr)}: {expr}")

    def _compile_symbol_ref(self, name: str, env: dict, dest: int) -> None:
        """Compile a symbol reference (variable lookup)."""
        if name in env:
            reg = env[name]
            if reg != dest:
                self._emit_instr(f"MOV r{dest}, r{reg}")
        elif name in self._globals:
            # Global variable — load from memory
            label = self._globals[name]
            self._emit_instr(f"LI r{dest}, {label}")
            self._emit_instr(f"LDR r{dest}, r{dest}, 0")
        else:
            raise CompilerError(f"Undefined variable: {name}")

    def _compile_form(self, form: list, env: dict, dest: int, tail: bool = False) -> None:
        """Compile a compound form (operator + arguments)."""
        op = form[0]

        # Special forms
        if op == 'quote':
            return self._compile_quote(form[1], dest)
        if op == 'if':
            return self._compile_if(form, env, dest, tail=tail)
        if op == 'cond':
            return self._compile_cond(form, env, dest, tail=tail)
        if op == 'let':
            return self._compile_let(form, env, dest, tail=tail)
        if op == 'progn' or op == 'begin':
            for i, e in enumerate(form[1:]):
                is_last = (i == len(form) - 2)
                d = dest if is_last else self.SCRATCH_REGS[0]
                self._compile_expr(e, env, dest=d, tail=(tail and is_last))
            return
        if op == 'lambda':
            return self._compile_lambda(form, env, dest)
        if op == 'and':
            return self._compile_and(form, env, dest)
        if op == 'or':
            return self._compile_or(form, env, dest)
        if op == 'set!':
            return self._compile_set(form, env, dest)
        if op == 'while':
            return self._compile_while(form, env, dest)
        if op == 'let*':
            return self._compile_let_star(form, env, dest, tail=tail)
        if op == 'when':
            # (when test body...) → (if test (progn body...) nil)
            return self._compile_if(['if', form[1], ['progn'] + form[2:]], env, dest, tail=tail)
        if op == 'unless':
            # (unless test body...) → (if test nil (progn body...))
            return self._compile_if(['if', form[1], None, ['progn'] + form[2:]], env, dest, tail=tail)
        if op == 'dotimes':
            return self._compile_dotimes(form, env, dest)
        if op == '__string__':
            return self._compile_string_literal(form, env, dest)

        # Inline builtins (arithmetic, cons, car, cdr, etc.)
        if isinstance(op, str) and op in self._builtins:
            return self._compile_builtin(op, form[1:], env, dest)

        # Function call — only calls can be tail-optimized
        self._compile_call(form, env, dest, tail=tail)

    # ------------------------------------------------------------------
    # Special forms
    # ------------------------------------------------------------------

    def _compile_quote(self, value, dest: int) -> None:
        """(quote x) — return literal value."""
        if value is None:
            self._emit_instr(f"LI r{dest}, 5")
        elif value is True:
            self._emit_instr(f"LI r{dest}, 13")
        elif isinstance(value, int):
            self._compile_expr(value, {}, dest)
        elif isinstance(value, str):
            # Quoted symbol — return as an interned symbol object
            # For now, symbols are just tagged ints (symbol ID << 3 | TAG_SPECIAL)
            # We'll use a simple symbol table
            sym_id = self._intern_symbol(value)
            tagged = (sym_id << 8) | 0x45  # sub=01000, tag=101 → symbol special
            if -32768 <= tagged <= 32767:
                self._emit_instr(f"LI r{dest}, {tagged}")
            else:
                lo = tagged & 0xFFFF
                hi = (tagged >> 16) & 0xFFFF
                self._emit_instr(f"LUI r{dest}, {hi}")
                if lo != 0:
                    self._emit_instr(f"LI r{self.SCRATCH_REGS[0]}, {lo}")
                    self._emit_instr(f"ADD r{dest}, r{dest}, r{self.SCRATCH_REGS[0]}")
        elif isinstance(value, list):
            # Quoted list — build cons cells
            self._compile_quoted_list(value, dest)
        else:
            raise CompilerError(f"Cannot quote: {value}")

    def _compile_quoted_list(self, lst: list, dest: int) -> None:
        """Build a quoted list from cons cells."""
        if not lst:
            self._emit_instr(f"LI r{dest}, 5")  # NIL
            return
        # Build from end: (quote (a b c)) → cons(a, cons(b, cons(c, nil)))
        self._compile_quote(lst[-1], dest=self.SCRATCH_REGS[1])
        self._emit_instr(f"LI r{self.SCRATCH_REGS[2]}, 5")  # nil for cdr of last
        self._emit_instr(f"ALLOC.CONS r{dest}, r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[2]}")
        for i in range(len(lst) - 2, -1, -1):
            self._compile_quote(lst[i], dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"ALLOC.CONS r{dest}, r{self.SCRATCH_REGS[1]}, r{dest}")

    def _compile_if(self, form: list, env: dict, dest: int, tail: bool = False) -> None:
        """(if test then [else])"""
        else_label = self._label("else")
        end_label = self._label("endif")

        # Compile test → r_dest
        self._compile_expr(form[1], env, dest=dest)
        # Branch if nil
        self._emit_instr(f"BR.NIL r{dest}, {else_label}")
        # Then branch (in tail position if the if itself is)
        self._compile_expr(form[2], env, dest=dest, tail=tail)
        self._emit_instr(f"BR {end_label}")
        # Else branch
        self._emit_label(else_label)
        if len(form) > 3:
            self._compile_expr(form[3], env, dest=dest, tail=tail)
        else:
            self._emit_instr(f"LI r{dest}, 5")  # nil
        self._emit_label(end_label)

    def _compile_cond(self, form: list, env: dict, dest: int, tail: bool = False) -> None:
        """(cond (test1 body1...) (test2 body2...) ...)"""
        end_label = self._label("endcond")
        for clause in form[1:]:
            test = clause[0]
            body = clause[1:]
            if test is True or test == 't':
                # Default clause
                for i, e in enumerate(body):
                    is_last = (i == len(body) - 1)
                    d = dest if is_last else self.SCRATCH_REGS[0]
                    self._compile_expr(e, env, dest=d, tail=(tail and is_last))
                self._emit_instr(f"BR {end_label}")
            else:
                next_label = self._label("cond_next")
                self._compile_expr(test, env, dest=dest)
                self._emit_instr(f"BR.NIL r{dest}, {next_label}")
                for i, e in enumerate(body):
                    is_last = (i == len(body) - 1)
                    d = dest if is_last else self.SCRATCH_REGS[0]
                    self._compile_expr(e, env, dest=d, tail=(tail and is_last))
                self._emit_instr(f"BR {end_label}")
                self._emit_label(next_label)
        # No clause matched → nil
        self._emit_instr(f"LI r{dest}, 5")
        self._emit_label(end_label)

    def _compile_let(self, form: list, env: dict, dest: int, tail: bool = False) -> None:
        """(let ((var1 val1) (var2 val2) ...) body...)"""
        bindings = form[1]
        body = form[2:]
        new_env = dict(env)

        for binding in bindings:
            var = binding[0]
            val_expr = binding[1]
            # Allocate a saved register for this variable
            reg = self._alloc_save_reg(new_env)
            self._compile_expr(val_expr, new_env, dest=reg)
            new_env[var] = reg

        for i, e in enumerate(body):
            is_last = (i == len(body) - 1)
            d = dest if is_last else self.SCRATCH_REGS[0]
            self._compile_expr(e, new_env, dest=d, tail=(tail and is_last))

    # Closure register — the register where the closure ref is passed
    CLOSURE_REG = 15  # r15 (last scratch register)

    def _free_vars(self, exprs: list, params: set, env: dict) -> list[str]:
        """Find free variables: names used in exprs that are in env but not in params."""
        free = []
        seen = set()

        def walk(expr):
            if isinstance(expr, str):
                if expr in env and expr not in params and expr not in seen:
                    seen.add(expr)
                    free.append(expr)
            elif isinstance(expr, list) and expr:
                op = expr[0]
                if op == 'quote':
                    return  # don't walk into quoted data
                if op == 'lambda':
                    # Walk lambda body but add its params to the "don't capture" set
                    inner_params = set(expr[1]) if len(expr) > 1 else set()
                    for e in expr[2:]:
                        walk_with_params(e, params | inner_params)
                    return
                for e in expr:
                    walk(e)

        def walk_with_params(expr, ps):
            if isinstance(expr, str):
                if expr in env and expr not in ps and expr not in seen:
                    seen.add(expr)
                    free.append(expr)
            elif isinstance(expr, list) and expr:
                op = expr[0]
                if op == 'quote':
                    return
                if op == 'lambda':
                    inner_params = set(expr[1]) if len(expr) > 1 else set()
                    for e in expr[2:]:
                        walk_with_params(e, ps | inner_params)
                    return
                for e in expr:
                    walk_with_params(e, ps)

        for e in exprs:
            walk(e)
        return free

    def _compile_lambda(self, form: list, env: dict, dest: int) -> None:
        """(lambda (params...) body...)

        Compiles as a closure: allocates a closure object with captured
        free variables from the enclosing scope.
        """
        params = form[1]
        body = form[2:]
        fn_label = self._label("lambda")
        end_label = self._label("endlambda")

        # Find free variables
        param_set = set(params)
        free = self._free_vars(body, param_set, env)

        # Skip over the lambda body in the instruction stream
        self._emit_instr(f"BR {end_label}")

        # Emit the lambda body
        self._emit_label(fn_label)
        fn_env = {}

        # Save callee-saved regs we'll use
        used_saved = []

        # Set up params (from arg registers)
        for i, param in enumerate(params):
            if i >= len(self.ARG_REGS):
                raise CompilerError(f"Too many lambda parameters")
            save_reg = self.SAVED_REGS[len(used_saved)]
            used_saved.append(save_reg)
            fn_env[param] = save_reg

        # Push callee-saved regs
        for reg in used_saved:
            self._emit_instr(f"PUSH r{reg}")

        # Move args to callee-saved regs
        for i, param in enumerate(params):
            self._emit_instr(f"MOV r{fn_env[param]}, r{self.ARG_REGS[i]}")

        # Load captured variables from closure env slots
        # Closure ref is in CLOSURE_REG (r15)
        if free:
            # Save closure ref to a callee-saved reg to access env slots
            closure_save = self.SAVED_REGS[len(used_saved)]
            used_saved.append(closure_save)
            self._emit_instr(f"PUSH r{closure_save}")
            self._emit_instr(f"MOV r{closure_save}, r{self.CLOSURE_REG}")
            for i, var in enumerate(free):
                env_reg = self.SAVED_REGS[len(used_saved)]
                used_saved.append(env_reg)
                self._emit_instr(f"PUSH r{env_reg}")
                # Load env slot i+1 (slot 0 is code pointer) via LD.FLD
                self._emit_instr(f"LD.FLD r{env_reg}, r{closure_save}, {i + 1}")
                fn_env[var] = env_reg

        # Track saved regs for potential tail calls inside lambda
        old_saved = self._current_fn_saved_regs
        self._current_fn_saved_regs = list(used_saved)

        # Compile body
        for i, e in enumerate(body):
            is_last = (i == len(body) - 1)
            d = self.RET_REG if is_last else self.SCRATCH_REGS[0]
            self._compile_expr(e, fn_env, dest=d, tail=is_last)

        self._current_fn_saved_regs = old_saved

        # Epilogue
        for reg in reversed(used_saved):
            self._emit_instr(f"POP r{reg}")
        self._emit_instr("RET")

        self._emit_label(end_label)

        # Allocate closure object at the call site
        if free:
            # Load code address into a scratch register
            self._emit_instr(f"LI r{self.SCRATCH_REGS[0]}, {fn_label}")
            # ALLOC.CLOSURE dest, rs_code, env_size
            self._emit_instr(f"ALLOC.CLOSURE r{dest}, r{self.SCRATCH_REGS[0]}, {len(free)}")
            # Store captured variables into env slots
            for i, var in enumerate(free):
                var_reg = env[var]
                # ST.FLD closure, value, field_idx — field 1..N are env slots
                self._emit_instr(f"ST.FLD r{dest}, r{var_reg}, {i + 1}")
        else:
            # No captures — just load the code address as a "closure"
            # Still allocate a proper closure object for CALL.CLOSURE to work
            self._emit_instr(f"LI r{self.SCRATCH_REGS[0]}, {fn_label}")
            self._emit_instr(f"ALLOC.CLOSURE r{dest}, r{self.SCRATCH_REGS[0]}, 0")

    def _compile_and(self, form: list, env: dict, dest: int) -> None:
        """(and expr1 expr2 ...) — short-circuit"""
        end_label = self._label("endand")
        for i, e in enumerate(form[1:]):
            self._compile_expr(e, env, dest=dest)
            if i < len(form) - 2:
                self._emit_instr(f"BR.NIL r{dest}, {end_label}")
        self._emit_label(end_label)

    def _compile_or(self, form: list, env: dict, dest: int) -> None:
        """(or expr1 expr2 ...) — short-circuit"""
        end_label = self._label("endor")
        for i, e in enumerate(form[1:]):
            self._compile_expr(e, env, dest=dest)
            if i < len(form) - 2:
                # Branch if NOT nil (truthy)
                self._emit_instr(f"BR.T r{dest}, {end_label}")
        self._emit_label(end_label)

    def _compile_set(self, form: list, env: dict, dest: int) -> None:
        """(set! var value)"""
        var = form[1]
        if var in env:
            reg = env[var]
            self._compile_expr(form[2], env, dest=reg)
            if reg != dest:
                self._emit_instr(f"MOV r{dest}, r{reg}")
        elif var in self._globals:
            label = self._globals[var]
            self._compile_expr(form[2], env, dest=dest)
            self._emit_instr(f"LI r{self.SCRATCH_REGS[0]}, {label}")
            self._emit_instr(f"STR r{self.SCRATCH_REGS[0]}, r{dest}, 0")
        else:
            raise CompilerError(f"set!: undefined variable: {var}")

    def _compile_while(self, form: list, env: dict, dest: int) -> None:
        """(while test body...) — iteration without recursion."""
        top_label = self._label("while_top")
        end_label = self._label("while_end")

        self._emit_label(top_label)
        # Evaluate test
        self._compile_expr(form[1], env, dest=dest)
        self._emit_instr(f"BR.NIL r{dest}, {end_label}")
        # Body
        for e in form[2:]:
            self._compile_expr(e, env, dest=dest)
        self._emit_instr(f"BR {top_label}")
        self._emit_label(end_label)
        # while returns nil
        self._emit_instr(f"LI r{dest}, 5")

    def _compile_let_star(self, form: list, env: dict, dest: int, tail: bool = False) -> None:
        """(let* ((var1 val1) (var2 val2) ...) body...)

        Like let, but each binding is visible to subsequent bindings.
        """
        bindings = form[1]
        body = form[2:]
        new_env = dict(env)

        for binding in bindings:
            var = binding[0]
            val_expr = binding[1]
            reg = self._alloc_save_reg(new_env)
            self._compile_expr(val_expr, new_env, dest=reg)
            new_env[var] = reg

        for i, e in enumerate(body):
            is_last = (i == len(body) - 1)
            d = dest if is_last else self.SCRATCH_REGS[0]
            self._compile_expr(e, new_env, dest=d, tail=(tail and is_last))

    def _compile_dotimes(self, form: list, env: dict, dest: int) -> None:
        """(dotimes (var count) body...)

        Iterates var from 0 to count-1.
        """
        var_spec = form[1]  # (var count)
        var = var_spec[0]
        count_expr = var_spec[1]
        body = form[2:]

        new_env = dict(env)
        # Allocate registers for counter and limit
        counter_reg = self._alloc_save_reg(new_env)
        new_env[var] = counter_reg
        limit_reg = self._alloc_save_reg(new_env)

        # Initialize counter to fixnum 0
        self._emit_instr(f"LI r{counter_reg}, 0")
        # Evaluate count
        self._compile_expr(count_expr, env, dest=limit_reg)

        top_label = self._label("dotimes_top")
        body_label = self._label("dotimes_body")
        end_label = self._label("dotimes_end")

        self._emit_label(top_label)
        # Compare counter < limit
        self._emit_instr(f"CMP r{self.SCRATCH_REGS[0]}, r{counter_reg}, r{limit_reg}")
        self._emit_instr(f"BR.FIX.LT r{self.SCRATCH_REGS[0]}, {body_label}")
        self._emit_instr(f"BR {end_label}")
        self._emit_label(body_label)

        # Body
        for e in body:
            self._compile_expr(e, new_env, dest=self.SCRATCH_REGS[0])

        # Increment counter (add fixnum 1 = tagged 2)
        self._emit_instr(f"ADD.FIX.IMM r{counter_reg}, r{counter_reg}, 2")
        self._emit_instr(f"BR {top_label}")
        self._emit_label(end_label)
        # dotimes returns nil
        self._emit_instr(f"LI r{dest}, 5")

    def _compile_string_literal(self, form: list, env: dict, dest: int) -> None:
        """Compile a string literal ['__string__', 'text'].

        Stores the string in the data section and loads its address.
        String format: length (8 bytes) followed by character data.
        """
        text = form[1]
        label = self._label("str")
        self._string_table.append((label, text))
        # Load address of string data
        self._emit_instr(f"LI r{dest}, {label}")

    # ------------------------------------------------------------------
    # Built-in operations (inlined)
    # ------------------------------------------------------------------

    def _compile_builtin(self, op: str, args: list, env: dict, dest: int) -> None:
        """Compile an inline builtin operation."""
        if op == '+':
            self._compile_arith('ADD.FIX', args, env, dest)
        elif op == '-':
            if len(args) == 1:
                # Unary negation: (- x) → (0 - x)
                self._emit_instr(f"LI r{self.SCRATCH_REGS[0]}, 0")
                self._compile_expr(args[0], env, dest=self.SCRATCH_REGS[1])
                self._emit_instr(f"SUB.FIX r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            else:
                self._compile_arith('SUB.FIX', args, env, dest)
        elif op == '*':
            self._compile_arith('MUL.FIX', args, env, dest)
        elif op == '/':
            self._compile_arith('DIV.FIX', args, env, dest)
        elif op == 'car':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"LD.CAR r{dest}, r{dest}")
        elif op == 'cdr':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"LD.CDR r{dest}, r{dest}")
        elif op == 'cons':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"ALLOC.CONS r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
        elif op == 'eq' or op == '=':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"EQ r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
        elif op == '<':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"CMP r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            # CMP returns fixnum(-1) if <, fixnum(0) if =, fixnum(1) if >
            # Convert: if < 0 → T, else NIL
            lt_label = self._label("lt_t")
            end_label = self._label("lt_end")
            self._emit_instr(f"BR.FIX.LT r{dest}, {lt_label}")
            self._emit_instr(f"LI r{dest}, 5")  # NIL
            self._emit_instr(f"BR {end_label}")
            self._emit_label(lt_label)
            self._emit_instr(f"LI r{dest}, 13")  # T
            self._emit_label(end_label)
        elif op == '>':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"CMP r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            gt_label = self._label("gt_t")
            end_label = self._label("gt_end")
            self._emit_instr(f"BR.FIX.GT r{dest}, {gt_label}")
            self._emit_instr(f"LI r{dest}, 5")
            self._emit_instr(f"BR {end_label}")
            self._emit_label(gt_label)
            self._emit_instr(f"LI r{dest}, 13")
            self._emit_label(end_label)
        elif op == 'null' or op == 'not':
            self._compile_expr(args[0], env, dest=dest)
            # null? → EQ with NIL
            self._emit_instr(f"LI r{self.SCRATCH_REGS[0]}, 5")
            self._emit_instr(f"EQ r{dest}, r{dest}, r{self.SCRATCH_REGS[0]}")
        elif op == 'atom':
            # atom → not a cons
            self._compile_expr(args[0], env, dest=dest)
            atom_label = self._label("atom_t")
            end_label = self._label("atom_end")
            # Check if it's a cons ref (low 3 bits = 011)
            self._emit_instr(f"TST.CONS r{self.SCRATCH_REGS[0]}, r{dest}")
            self._emit_instr(f"BR.NIL r{self.SCRATCH_REGS[0]}, {atom_label}")
            self._emit_instr(f"LI r{dest}, 5")  # cons → not atom → NIL
            self._emit_instr(f"BR {end_label}")
            self._emit_label(atom_label)
            self._emit_instr(f"LI r{dest}, 13")  # not cons → atom → T
            self._emit_label(end_label)
        elif op == 'fixnump':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"TST.FIX r{dest}, r{dest}")
        elif op == 'consp':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"TST.CONS r{dest}, r{dest}")
        elif op == 'set-car!':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"ST.CAR r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            self._emit_instr(f"MOV r{dest}, r{self.SCRATCH_REGS[1]}")
        elif op == 'set-cdr!':
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"ST.CDR r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            self._emit_instr(f"MOV r{dest}, r{self.SCRATCH_REGS[1]}")
        elif op == '>=':
            # (>= a b) → NOT (< a b) → CMP; if LT → NIL, else T
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"CMP r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            lt_label = self._label("ge_no")
            end_label = self._label("ge_end")
            self._emit_instr(f"BR.FIX.LT r{dest}, {lt_label}")
            self._emit_instr(f"LI r{dest}, 13")  # T (>=)
            self._emit_instr(f"BR {end_label}")
            self._emit_label(lt_label)
            self._emit_instr(f"LI r{dest}, 5")   # NIL (<)
            self._emit_label(end_label)
        elif op == '<=':
            # (<= a b) → NOT (> a b) → CMP; if GT → NIL, else T
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"CMP r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            gt_label = self._label("le_no")
            end_label = self._label("le_end")
            self._emit_instr(f"BR.FIX.GT r{dest}, {gt_label}")
            self._emit_instr(f"LI r{dest}, 13")  # T (<=)
            self._emit_instr(f"BR {end_label}")
            self._emit_label(gt_label)
            self._emit_instr(f"LI r{dest}, 5")   # NIL (>)
            self._emit_label(end_label)
        elif op == 'mod' or op == 'rem':
            # (mod a b) → a - (a / b) * b (integer modulo via tagged arith)
            # Eval a, push; eval b
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"PUSH r{dest}")
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
            # scratch[0] = a, scratch[1] = b
            # scratch[2] = a / b
            self._emit_instr(f"DIV.FIX r{self.SCRATCH_REGS[2]}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            # scratch[2] = (a / b) * b
            self._emit_instr(f"MUL.FIX r{self.SCRATCH_REGS[2]}, r{self.SCRATCH_REGS[2]}, r{self.SCRATCH_REGS[1]}")
            # dest = a - (a / b) * b
            self._emit_instr(f"SUB.FIX r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[2]}")
        elif op in ('print-fixnum', 'print', 'newline', 'putchar'):
            # These are inlined as CALL.DIRECT to the runtime functions
            self._compile_call([op] + args, env, dest)
            return
        elif op == 'string-length':
            # (string-length str) → load length word from str address
            # String format: [tagged-fixnum-length] [bytes...]
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"LDR r{dest}, r{dest}, 0")
        elif op == 'string-ref':
            # (string-ref str idx) → byte at str + 8 + idx
            # Uses TRAP 0x84: r1=0(load), r2=addr+8, r3=offset(tagged idx)
            self._compile_expr(args[0], env, dest=self.SCRATCH_REGS[0])
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            # addr + 8 to skip the length word
            self._emit_instr(f"LI r{self.SCRATCH_REGS[3]}, 8")
            self._emit_instr(f"ADD r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[3]}")
            self._emit_instr(f"LI r1, 0")       # sub=0 (load byte)
            self._emit_instr(f"MOV r2, r{self.SCRATCH_REGS[0]}")  # base = str + 8
            self._emit_instr(f"MOV r3, r{self.SCRATCH_REGS[1]}")  # offset = idx (tagged)
            self._emit_instr("TRAP 0x84")
            if dest != 1:
                self._emit_instr(f"MOV r{dest}, r1")  # result in r1
        elif op == 'string-set!':
            # (string-set! str idx val) → store byte
            self._compile_expr(args[0], env, dest=self.SCRATCH_REGS[0])
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
            self._compile_expr(args[2], env, dest=self.SCRATCH_REGS[2])
            self._emit_instr(f"LI r{self.SCRATCH_REGS[3]}, 8")
            self._emit_instr(f"ADD r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[3]}")
            self._emit_instr(f"LI r1, {tag_fixnum(1)}")  # sub=1 (store byte)
            self._emit_instr(f"MOV r2, r{self.SCRATCH_REGS[0]}")
            self._emit_instr(f"MOV r3, r{self.SCRATCH_REGS[1]}")
            self._emit_instr(f"MOV r4, r{self.SCRATCH_REGS[2]}")
            self._emit_instr("TRAP 0x84")
            self._emit_instr(f"LI r{dest}, 5")  # return NIL
        elif op == 'print-string':
            # (print-string str) → TRAP 0x9F (debug print: r1=addr+8, r2=length)
            self._compile_expr(args[0], env, dest=self.SCRATCH_REGS[0])
            # Load length (tagged fixnum at str+0)
            self._emit_instr(f"LDR r2, r{self.SCRATCH_REGS[0]}, 0")
            # Untag length: >>1 (SHR r2, r2, 1)
            self._emit_instr(f"LI r{self.SCRATCH_REGS[1]}, 1")
            self._emit_instr(f"SHR r2, r2, r{self.SCRATCH_REGS[1]}")
            # Address of chars = str + 8
            self._emit_instr(f"LI r{self.SCRATCH_REGS[2]}, 8")
            self._emit_instr(f"ADD r1, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[2]}")
            self._emit_instr("TRAP 0x9F")
            self._emit_instr(f"LI r{dest}, 5")  # return NIL
        elif op == 'char->fixnum':
            # Characters are represented as fixnums — identity
            self._compile_expr(args[0], env, dest=dest)
        elif op == 'fixnum->char':
            # Identity
            self._compile_expr(args[0], env, dest=dest)
        elif op == 'make-vector':
            # (make-vector n) → ALLOCV with template 2 (vector)
            # Elements initialized to 0 (fixnum 0)
            self._compile_expr(args[0], env, dest=self.SCRATCH_REGS[0])
            self._emit_instr(f"ALLOCV r{dest}, r{self.SCRATCH_REGS[0]}, 2")
        elif op == 'vector-length':
            # (vector-length vec) → field 0 = length (tagged fixnum)
            self._compile_expr(args[0], env, dest=dest)
            self._emit_instr(f"LD.FLD r{dest}, r{dest}, 0")
        elif op == 'vector-ref':
            # (vector-ref vec idx) → element at field (idx+1)
            # addr = ref_address(vec) + (untag(idx) + 2) * 8
            #      = (vec & ~7) + idx_tagged * 4 + 16
            self._compile_expr(args[0], env, dest=self.SCRATCH_REGS[0])  # vec
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])  # idx
            # Strip ref tag
            self._emit_instr(f"LI r{self.SCRATCH_REGS[2]}, -8")
            self._emit_instr(f"AND r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[2]}")
            # offset = idx_tagged * 4 + 16
            self._emit_instr(f"LI r{self.SCRATCH_REGS[2]}, 4")
            self._emit_instr(f"MUL r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[2]}")
            self._emit_instr(f"LI r{self.SCRATCH_REGS[2]}, 16")
            self._emit_instr(f"ADD r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[2]}")
            # Load element
            self._emit_instr(f"ADD r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            self._emit_instr(f"LDR r{dest}, r{self.SCRATCH_REGS[0]}, 0")
        elif op == 'vector-set!':
            # (vector-set! vec idx val) → store element
            self._compile_expr(args[0], env, dest=self.SCRATCH_REGS[0])  # vec
            self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])  # idx
            self._compile_expr(args[2], env, dest=self.SCRATCH_REGS[2])  # val
            # Strip ref tag
            self._emit_instr(f"LI r{self.SCRATCH_REGS[3]}, -8")
            self._emit_instr(f"AND r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[3]}")
            # offset = idx_tagged * 4 + 16
            self._emit_instr(f"LI r{self.SCRATCH_REGS[3]}, 4")
            self._emit_instr(f"MUL r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[3]}")
            self._emit_instr(f"LI r{self.SCRATCH_REGS[3]}, 16")
            self._emit_instr(f"ADD r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[1]}, r{self.SCRATCH_REGS[3]}")
            # Store
            self._emit_instr(f"ADD r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")
            self._emit_instr(f"STR r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[2]}, 0")
            self._emit_instr(f"LI r{dest}, 5")  # return NIL
        elif op == 'funcall':
            # (funcall closure-expr arg1 arg2 ...)
            # Evaluate closure, put in CLOSURE_REG; eval args into r1..rN;
            # CALL.CLOSURE CLOSURE_REG
            closure_expr = args[0]
            call_args = args[1:]
            if len(call_args) > len(self.ARG_REGS) - 1:
                raise CompilerError(f"Too many arguments for funcall")
            # Save env registers
            used_saved = sorted(set(r for r in env.values() if r in self.SAVED_REGS))
            for reg in used_saved:
                self._emit_instr(f"PUSH r{reg}")
            # Evaluate closure
            self._compile_expr(closure_expr, env, dest=self.CLOSURE_REG)
            self._emit_instr(f"PUSH r{self.CLOSURE_REG}")
            # Evaluate args
            for i, arg in enumerate(call_args):
                self._compile_expr(arg, env, dest=self.SCRATCH_REGS[0])
                if i < len(call_args) - 1:
                    self._emit_instr(f"PUSH r{self.SCRATCH_REGS[0]}")
            # Pop args into arg registers
            if call_args:
                last_idx = len(call_args) - 1
                self._emit_instr(f"MOV r{self.ARG_REGS[last_idx]}, r{self.SCRATCH_REGS[0]}")
                for i in range(last_idx - 1, -1, -1):
                    self._emit_instr(f"POP r{self.ARG_REGS[i]}")
            # Pop closure ref
            self._emit_instr(f"POP r{self.CLOSURE_REG}")
            # Call
            self._emit_instr(f"CALL.CLOSURE r{self.CLOSURE_REG}")
            if dest != self.RET_REG:
                self._emit_instr(f"MOV r{dest}, r{self.RET_REG}")
            # Restore saved registers
            for reg in reversed(used_saved):
                self._emit_instr(f"POP r{reg}")
        else:
            raise CompilerError(f"Unknown builtin: {op}")

    def _compile_arith(self, instr: str, args: list, env: dict, dest: int) -> None:
        """Compile binary arithmetic: (op a b).
        Uses stack to preserve first arg across second arg evaluation.
        """
        if len(args) != 2:
            raise CompilerError(f"{instr} requires 2 arguments")
        # Evaluate first arg, push to preserve across potential calls
        self._compile_expr(args[0], env, dest=dest)
        self._emit_instr(f"PUSH r{dest}")
        # Evaluate second arg
        self._compile_expr(args[1], env, dest=self.SCRATCH_REGS[1])
        # Pop first arg
        self._emit_instr(f"POP r{self.SCRATCH_REGS[0]}")
        self._emit_instr(f"{instr} r{dest}, r{self.SCRATCH_REGS[0]}, r{self.SCRATCH_REGS[1]}")

    # ------------------------------------------------------------------
    # Function calls
    # ------------------------------------------------------------------

    def _compile_call(self, form: list, env: dict, dest: int, tail: bool = False) -> None:
        """Compile a function call (func arg1 arg2 ...).

        When tail=True and we're inside a defun, emit TAILCALL.DIRECT
        instead of CALL.DIRECT — reusing the current stack frame.
        """
        func_name = form[0]
        args = form[1:]

        if len(args) > len(self.ARG_REGS):
            raise CompilerError(f"Too many arguments: {len(args)}")

        # Can only do tail call if we're inside a defun
        do_tail = tail and len(self._current_fn_saved_regs) > 0

        if do_tail:
            self._emit_comment(f"tailcall {func_name}")
            # Evaluate arguments (no env save needed — we're leaving this frame)
            for i, arg in enumerate(args):
                self._compile_expr(arg, env, dest=self.SCRATCH_REGS[0])
                if i < len(args) - 1:
                    self._emit_instr(f"PUSH r{self.SCRATCH_REGS[0]}")

            # Pop args into arg registers
            if args:
                last_idx = len(args) - 1
                self._emit_instr(f"MOV r{self.ARG_REGS[last_idx]}, r{self.SCRATCH_REGS[0]}")
                for i in range(last_idx - 1, -1, -1):
                    self._emit_instr(f"POP r{self.ARG_REGS[i]}")

            # Restore defun's callee-saved registers before tail jump
            for reg in reversed(self._current_fn_saved_regs):
                self._emit_instr(f"POP r{reg}")

            # Resolve function label
            if isinstance(func_name, str) and func_name in self._functions:
                fn_label = self._functions[func_name]
            elif isinstance(func_name, str):
                fn_label = f"_fn_{self._sanitize_label(func_name)}"
                self._functions[func_name] = fn_label
            else:
                raise CompilerError(f"Cannot call non-symbol: {func_name}")

            self._emit_instr(f"TAILCALL.DIRECT {fn_label}")
        else:
            # Normal call — save env registers around the call
            used_saved = sorted(set(r for r in env.values() if r in self.SAVED_REGS))
            for reg in used_saved:
                self._emit_instr(f"PUSH r{reg}")

            # Evaluate arguments onto stack
            for i, arg in enumerate(args):
                self._compile_expr(arg, env, dest=self.SCRATCH_REGS[0])
                if i < len(args) - 1:
                    self._emit_instr(f"PUSH r{self.SCRATCH_REGS[0]}")

            # Pop args into arg registers
            if args:
                last_idx = len(args) - 1
                self._emit_instr(f"MOV r{self.ARG_REGS[last_idx]}, r{self.SCRATCH_REGS[0]}")
                for i in range(last_idx - 1, -1, -1):
                    self._emit_instr(f"POP r{self.ARG_REGS[i]}")

            # Resolve function label
            if isinstance(func_name, str) and func_name in self._functions:
                fn_label = self._functions[func_name]
            elif isinstance(func_name, str):
                fn_label = f"_fn_{self._sanitize_label(func_name)}"
                self._functions[func_name] = fn_label
            else:
                raise CompilerError(f"Cannot call non-symbol: {func_name}")

            self._emit_instr(f"CALL.DIRECT {fn_label}")

            # Move result to dest if needed
            if dest != self.RET_REG:
                self._emit_instr(f"MOV r{dest}, r{self.RET_REG}")

            # Restore saved registers
            for reg in reversed(used_saved):
                self._emit_instr(f"POP r{reg}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _alloc_save_reg(self, env: dict) -> int:
        """Find a free callee-saved register not in use by env."""
        used = set(env.values())
        for r in self.SAVED_REGS:
            if r not in used:
                return r
        raise CompilerError("Out of callee-saved registers")

    _symbol_table: dict[str, int] = {}
    _next_symbol_id = 0

    def _intern_symbol(self, name: str) -> int:
        """Return the symbol ID for a name, interning it if new."""
        if name not in self._symbol_table:
            self._symbol_table[name] = self._next_symbol_id
            self._next_symbol_id += 1
        return self._symbol_table[name]

    # ------------------------------------------------------------------
    # OS/REPL generation
    # ------------------------------------------------------------------

    def emit_runtime_header(self) -> None:
        """Emit the OS entry point and runtime setup."""
        self._emit("; ===== LM-1 Lispos Runtime =====")
        self._emit_label("_os_entry")
        self._emit_comment("OS entry point (called by BIOS)")
        # r1 = boot info (image base address)
        self._emit_instr("MOV r16, r1")  # save boot info
        self._emit_instr("LI sp, 0x3FF8")
        self._emit_instr("LI fp, 0")
        self._emit("")

    def emit_call_main(self, main_fn: str = "main") -> None:
        """Emit call to the main function and halt after return."""
        if main_fn in self._functions:
            self._emit_instr(f"CALL.DIRECT {self._functions[main_fn]}")
        else:
            self._emit_instr(f"CALL.DIRECT _fn_{self._sanitize_label(main_fn)}")
        self._emit_instr("HALT")
        self._emit("")

    def emit_putchar(self) -> None:
        """Emit a putchar helper: r1 = fixnum char code."""
        self._emit_label("_fn_putchar")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("RET")
        self._emit("")
        self._functions['putchar'] = '_fn_putchar'

    def emit_getchar(self) -> None:
        """Emit a getchar helper: returns fixnum char code in r1."""
        self._emit_label("_fn_getchar")
        self._emit_instr("TRAP 0x81")
        self._emit_instr("MOV r1, r0")  # result in r0 → r1
        self._emit_instr("RET")
        self._emit("")
        self._functions['getchar'] = '_fn_getchar'

    def emit_print_fixnum(self) -> None:
        """Emit a print-fixnum function that prints a fixnum as a decimal number."""
        self._emit_label("_fn_print_fixnum")
        self._emit_comment("Print fixnum in r1 as decimal")
        # Move arg to saved register
        self._emit_instr("MOV r16, r1")

        # Check if negative
        neg_label = self._label("neg")
        pos_label = self._label("pos")
        self._emit_instr(f"BR.FIX.LT r16, {neg_label}")
        self._emit_instr(f"BR {pos_label}")

        self._emit_label(neg_label)
        # Print '-'
        self._emit_instr(f"LI r1, {tag_fixnum(ord('-'))}")
        self._emit_instr("TRAP 0x80")
        # Negate: (0 - r16)
        self._emit_instr("LI r9, 0")
        self._emit_instr("SUB.FIX r16, r9, r16")

        self._emit_label(pos_label)
        # Convert to string: push digits onto stack, then pop and print
        # Divide by 10 repeatedly, push remainder
        digit_loop = self._label("digit_loop")
        print_loop = self._label("print_loop")

        self._emit_instr("LI r17, 0")  # digit count
        self._emit_instr(f"LI r18, {tag_fixnum(10)}")  # divisor (fixnum 10)

        self._emit_label(digit_loop)
        self._emit_instr("DIV.FIX r19, r16, r18")  # quotient
        self._emit_instr("MUL.FIX r20, r19, r18")  # q * 10
        self._emit_instr("SUB.FIX r20, r16, r20")  # remainder = n - q*10
        # Push remainder (fixnum digit)
        self._emit_instr("PUSH r20")
        self._emit_instr(f"LI r9, {tag_fixnum(1)}")
        self._emit_instr("ADD.FIX r17, r17, r9")  # count++
        self._emit_instr("MOV r16, r19")  # n = quotient
        self._emit_instr(f"BR.FIX.GT r16, {digit_loop}")
        # Also handle if quotient IS 0 but we already have digits
        # (BR.FIX.GT won't loop if quotient == 0, which is correct)

        # Print digits from stack
        self._emit_label(print_loop)
        self._emit_instr(f"BR.FIX.EQ r17, _pfn_done_{self._label_counter}")
        self._emit_instr("POP r1")
        # Convert fixnum digit to ASCII: digit + '0'
        self._emit_instr(f"LI r9, {tag_fixnum(ord('0'))}")
        self._emit_instr("ADD.FIX r1, r1, r9")
        self._emit_instr("TRAP 0x80")
        self._emit_instr(f"LI r9, {tag_fixnum(1)}")
        self._emit_instr("SUB.FIX r17, r17, r9")
        self._emit_instr(f"BR {print_loop}")
        self._emit_label(f"_pfn_done_{self._label_counter}")
        self._emit_instr("RET")
        self._emit("")
        self._functions['print-fixnum'] = '_fn_print_fixnum'

    def emit_print_value(self) -> None:
        """Emit a print function that handles fixnums, nil, t, and cons."""
        self._emit_label("_fn_print")
        self._emit_instr("MOV r16, r1")

        # Check type
        # nil?
        nil_label = self._label("print_nil")
        t_label = self._label("print_t")
        fix_label = self._label("print_fix")
        cons_label = self._label("print_cons")
        unknown_label = self._label("print_unk")

        self._emit_instr(f"LI r9, 5")  # NIL
        self._emit_instr(f"EQ r9, r16, r9")
        self._emit_instr(f"BR.T r9, {nil_label}")

        self._emit_instr(f"LI r9, 13")  # T
        self._emit_instr(f"EQ r9, r16, r9")
        self._emit_instr(f"BR.T r9, {t_label}")

        # fixnum?
        self._emit_instr(f"TST.FIX r9, r16")
        self._emit_instr(f"BR.T r9, {fix_label}")

        # cons?
        self._emit_instr(f"TST.CONS r9, r16")
        self._emit_instr(f"BR.T r9, {cons_label}")

        # unknown
        self._emit_instr(f"BR {unknown_label}")

        # Print nil
        self._emit_label(nil_label)
        self._emit_instr(f"LI r1, {tag_fixnum(ord('N'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr(f"LI r1, {tag_fixnum(ord('I'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr(f"LI r1, {tag_fixnum(ord('L'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("RET")

        # Print t
        self._emit_label(t_label)
        self._emit_instr(f"LI r1, {tag_fixnum(ord('T'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("RET")

        # Print fixnum
        self._emit_label(fix_label)
        self._emit_instr("MOV r1, r16")
        self._emit_instr("CALL.DIRECT _fn_print_fixnum")
        self._emit_instr("RET")

        # Print cons (as list)
        self._emit_label(cons_label)
        self._emit_instr(f"LI r1, {tag_fixnum(ord('('))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("PUSH r16")  # save cons ref

        # Print car
        self._emit_instr("LD.CAR r1, r16")
        self._emit_instr("CALL.DIRECT _fn_print")

        # Check cdr
        self._emit_instr("POP r16")
        self._emit_instr("LD.CDR r16, r16")

        cons_loop = self._label("cons_loop")
        cons_end = self._label("cons_end")
        cons_dot = self._label("cons_dot")

        self._emit_label(cons_loop)
        # if cdr is nil, just close paren
        self._emit_instr(f"LI r9, 5")
        self._emit_instr(f"EQ r9, r16, r9")
        self._emit_instr(f"BR.T r9, {cons_end}")
        # if cdr is cons, print space + car, continue
        self._emit_instr(f"TST.CONS r9, r16")
        self._emit_instr(f"BR.NIL r9, {cons_dot}")
        # It's a cons: print " ", car, continue with cdr
        self._emit_instr(f"LI r1, {tag_fixnum(ord(' '))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("PUSH r16")
        self._emit_instr("LD.CAR r1, r16")
        self._emit_instr("CALL.DIRECT _fn_print")
        self._emit_instr("POP r16")
        self._emit_instr("LD.CDR r16, r16")
        self._emit_instr(f"BR {cons_loop}")

        # Dotted pair: print " . " cdr
        self._emit_label(cons_dot)
        self._emit_instr(f"LI r1, {tag_fixnum(ord(' '))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr(f"LI r1, {tag_fixnum(ord('.'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr(f"LI r1, {tag_fixnum(ord(' '))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("MOV r1, r16")
        self._emit_instr("CALL.DIRECT _fn_print")

        self._emit_label(cons_end)
        self._emit_instr(f"LI r1, {tag_fixnum(ord(')'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("RET")

        # Unknown type
        self._emit_label(unknown_label)
        self._emit_instr(f"LI r1, {tag_fixnum(ord('?'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("RET")

        self._emit("")
        self._functions['print'] = '_fn_print'

    def emit_newline(self) -> None:
        """Emit a print-newline helper."""
        self._emit_label("_fn_newline")
        self._emit_instr(f"LI r1, {tag_fixnum(10)}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr("RET")
        self._emit("")
        self._functions['newline'] = '_fn_newline'

    def emit_reader(self) -> None:
        """Emit a reader that parses S-expressions from console input.

        Reads one S-expression and returns it as tagged values
        (fixnums, cons-list structure).

        Grammar:
          expr := number | '(' list ')'
          list := ')' | expr list
          number := ['-'] digit+
        """
        # We'll implement: read-expr dispatches on first char
        self._emit("; ===== Reader =====")
        self._emit_label("_fn_read")
        self._emit_comment("Read one S-expression from stdin → r1")

        # Skip whitespace, get first char
        self._emit_instr("CALL.DIRECT _fn_skip_ws")
        # r1 = first non-ws char (fixnum)

        # Dispatch on char
        read_list = self._label("read_list")
        read_num = self._label("read_num")

        # '(' → list
        self._emit_instr(f"LI r9, {tag_fixnum(ord('('))}")
        self._emit_instr(f"EQ r9, r1, r9")
        self._emit_instr(f"BR.T r9, {read_list}")

        # digit or '-' → number
        self._emit_instr(f"BR {read_num}")

        # Read list
        self._emit_label(read_list)
        self._emit_instr("CALL.DIRECT _fn_read_list")
        self._emit_instr("RET")

        # Read number
        self._emit_label(read_num)
        self._emit_instr("CALL.DIRECT _fn_read_number")
        self._emit_instr("RET")
        self._emit("")

        # skip_ws: skip spaces/newlines, return first non-ws char in r1
        self._emit_label("_fn_skip_ws")
        skip_loop = self._label("skip_loop")
        skip_done = self._label("skip_done")
        self._emit_label(skip_loop)
        self._emit_instr("TRAP 0x81")  # getchar → r0
        self._emit_instr("MOV r1, r0")
        # Check for space (32), newline (10), tab (9), CR (13)
        self._emit_instr(f"LI r9, {tag_fixnum(32)}")
        self._emit_instr(f"EQ r9, r1, r9")
        self._emit_instr(f"BR.T r9, {skip_loop}")
        self._emit_instr(f"LI r9, {tag_fixnum(10)}")
        self._emit_instr(f"EQ r9, r1, r9")
        self._emit_instr(f"BR.T r9, {skip_loop}")
        self._emit_instr(f"LI r9, {tag_fixnum(9)}")
        self._emit_instr(f"EQ r9, r1, r9")
        self._emit_instr(f"BR.T r9, {skip_loop}")
        self._emit_instr(f"LI r9, {tag_fixnum(13)}")
        self._emit_instr(f"EQ r9, r1, r9")
        self._emit_instr(f"BR.T r9, {skip_loop}")
        # Non-whitespace
        self._emit_instr("RET")
        self._emit("")

        # read_number: parse digits, return fixnum
        # r1 = first char (already read)
        self._emit_label("_fn_read_number")
        self._emit_instr("MOV r16, r1")  # save first char
        self._emit_instr("LI r17, 0")    # accumulator (fixnum)
        self._emit_instr("LI r18, 0")    # negative flag (0 or 1)

        # Check for '-'
        neg_check = self._label("neg_check")
        num_loop = self._label("num_loop")
        num_digit = self._label("num_digit")
        num_done = self._label("num_done")
        num_neg = self._label("num_neg")

        self._emit_instr(f"LI r9, {tag_fixnum(ord('-'))}")
        self._emit_instr(f"EQ r9, r16, r9")
        self._emit_instr(f"BR.NIL r9, {num_digit}")
        # It's '-': set neg flag, read next char
        self._emit_instr(f"LI r18, {tag_fixnum(1)}")
        self._emit_instr("TRAP 0x81")
        self._emit_instr("MOV r16, r0")

        # Process digit
        self._emit_label(num_digit)
        # digit = char - '0'
        self._emit_instr(f"LI r9, {tag_fixnum(ord('0'))}")
        self._emit_instr("SUB.FIX r19, r16, r9")  # digit
        self._emit_instr(f"LI r9, {tag_fixnum(10)}")
        self._emit_instr("MUL.FIX r17, r17, r9")    # acc * 10
        self._emit_instr("ADD.FIX r17, r17, r19")    # acc + digit

        # Read next char
        self._emit_label(num_loop)
        self._emit_instr("TRAP 0x81")
        self._emit_instr("MOV r16, r0")
        # Check if digit (>= '0' and <= '9')
        self._emit_instr(f"LI r9, {tag_fixnum(ord('0'))}")
        self._emit_instr("CMP r9, r16, r9")
        self._emit_instr(f"BR.FIX.LT r9, {num_done}")
        self._emit_instr(f"LI r9, {tag_fixnum(ord('9') + 1)}")
        self._emit_instr("CMP r9, r16, r9")
        self._emit_instr(f"BR.FIX.LT r9, {num_digit}")
        # else: not a digit, stop (char is consumed but we can't unread)

        self._emit_label(num_done)
        # Apply negation if needed
        self._emit_instr(f"BR.FIX.EQ r18, {num_neg}")
        self._emit_instr("LI r9, 0")
        self._emit_instr("SUB.FIX r17, r9, r17")
        self._emit_label(num_neg)
        self._emit_instr("MOV r1, r17")
        self._emit_instr("RET")
        self._emit("")

        # read_list: parse list elements until ')'
        # Builds cons list: (a b c) → cons(a, cons(b, cons(c, nil)))
        self._emit_label("_fn_read_list")
        list_loop = self._label("list_loop")
        list_close = self._label("list_close")

        # Start with nil
        self._emit_instr("LI r16, 5")  # result list (reversed)

        self._emit_label(list_loop)
        # Skip whitespace, peek at char
        self._emit_instr("CALL.DIRECT _fn_skip_ws")
        # r1 = next non-ws char
        # Check for ')'
        self._emit_instr(f"LI r9, {tag_fixnum(ord(')'))}")
        self._emit_instr(f"EQ r9, r1, r9")
        self._emit_instr(f"BR.T r9, {list_close}")

        # Not ')': read an element
        # But the char is already consumed by skip_ws → getchar.
        # We need to "unread" it or pass it to read.
        # For simplicity: check if it's '(' and recurse, else read number
        self._emit_instr(f"LI r9, {tag_fixnum(ord('('))}")
        self._emit_instr(f"EQ r9, r1, r9")
        inner_list = self._label("inner_list")
        inner_num = self._label("inner_num")
        after_elem = self._label("after_elem")
        self._emit_instr(f"BR.T r9, {inner_list}")

        # Number: r1 has first char
        self._emit_label(inner_num)
        self._emit_instr("PUSH r16")
        self._emit_instr("CALL.DIRECT _fn_read_number")
        self._emit_instr("POP r16")
        self._emit_instr(f"BR {after_elem}")

        # Nested list
        self._emit_label(inner_list)
        self._emit_instr("PUSH r16")
        self._emit_instr("CALL.DIRECT _fn_read_list")
        self._emit_instr("POP r16")

        self._emit_label(after_elem)
        # r1 = element, r16 = accumulated list
        # Cons element onto front: list = cons(element, list)
        self._emit_instr(f"ALLOC.CONS r16, r1, r16")
        self._emit_instr(f"BR {list_loop}")

        self._emit_label(list_close)
        # Reverse the list
        self._emit_instr("MOV r1, r16")
        self._emit_instr("CALL.DIRECT _fn_reverse")
        self._emit_instr("RET")
        self._emit("")

        # reverse: reverse a list
        self._emit_label("_fn_reverse")
        self._emit_instr("MOV r16, r1")  # input
        self._emit_instr("LI r17, 5")    # acc = nil
        rev_loop = self._label("rev_loop")
        rev_done = self._label("rev_done")
        self._emit_label(rev_loop)
        self._emit_instr(f"LI r9, 5")
        self._emit_instr(f"EQ r9, r16, r9")
        self._emit_instr(f"BR.T r9, {rev_done}")
        self._emit_instr("LD.CAR r18, r16")
        self._emit_instr("LD.CDR r16, r16")
        self._emit_instr("ALLOC.CONS r17, r18, r17")
        self._emit_instr(f"BR {rev_loop}")
        self._emit_label(rev_done)
        self._emit_instr("MOV r1, r17")
        self._emit_instr("RET")
        self._emit("")

        self._functions['read'] = '_fn_read'
        self._functions['reverse'] = '_fn_reverse'

    def emit_eval(self) -> None:
        """Emit a simple eval that handles:
        - fixnum → self-evaluating
        - nil → nil
        - t → t
        - cons (list form) → evaluate as function application
          Special forms: quote, if, +, -, *, /, cons, car, cdr, eq, defun

        The evaluator uses a global environment (simple alist).
        """
        self._emit("; ===== Evaluator =====")
        self._emit_label("_fn_eval")
        self._emit_instr("MOV r16, r1")  # save expr

        eval_nil = self._label("eval_nil")
        eval_t = self._label("eval_t")
        eval_fix = self._label("eval_fix")
        eval_list = self._label("eval_list")

        # nil?
        self._emit_instr("LI r9, 5")
        self._emit_instr("EQ r9, r16, r9")
        self._emit_instr(f"BR.T r9, {eval_nil}")
        # t?
        self._emit_instr("LI r9, 13")
        self._emit_instr("EQ r9, r16, r9")
        self._emit_instr(f"BR.T r9, {eval_t}")
        # fixnum?
        self._emit_instr("TST.FIX r9, r16")
        self._emit_instr(f"BR.T r9, {eval_fix}")
        # cons? (list form)
        self._emit_instr("TST.CONS r9, r16")
        self._emit_instr(f"BR.T r9, {eval_list}")
        # Unknown → return as-is
        self._emit_instr("MOV r1, r16")
        self._emit_instr("RET")

        self._emit_label(eval_nil)
        self._emit_instr("LI r1, 5")
        self._emit_instr("RET")

        self._emit_label(eval_t)
        self._emit_instr("LI r1, 13")
        self._emit_instr("RET")

        self._emit_label(eval_fix)
        self._emit_instr("MOV r1, r16")
        self._emit_instr("RET")

        # Evaluate as list form: (op args...)
        self._emit_label(eval_list)
        self._emit_instr("LD.CAR r17, r16")  # r17 = operator (fixnum tag)
        self._emit_instr("LD.CDR r18, r16")  # r18 = args list

        # Dispatch on operator
        # We use fixnum tags for known operators:
        # The operator in the list is a fixnum code:
        #   tag_fixnum(1) = '+', tag_fixnum(2) = '-', etc.
        # But actually, the reader reads them as numbers.
        # For (+ 1 2), the reader produces: cons(fixnum(+?), cons(1, cons(2, nil)))
        # But '+' is not a number! The reader won't handle symbols.
        #
        # SIMPLIFICATION: For the initial REPL, the user types expressions
        # in a simplified prefix form where operators are small integers:
        # Actually, let's handle this properly. The reader reads '(' and
        # then elements. If the first element after '(' is a number like 43
        # (ASCII for '+'), that's not right.
        #
        # Better approach: The eval function recognizes fixnum operators
        # by comparing against known operator codes. The REPL will precompile
        # known functions. For now, eval handles:
        #   fixnum applications: (+42 1 2) where 42 = ASCII '+'... no.
        #
        # Actually, the simplest approach: since we don't have symbols in
        # the reader yet, let's make eval handle the raw list structure
        # produced by a COMPILER, not a reader. The reader will be used
        # only for reading data, and a pre-compiled eval handles
        # compiled function calls.
        #
        # For the REPL milestone, I'll use a DIFFERENT approach:
        # Pre-compile a set of eval functions using the cross-compiler.
        # The eval dispatches on the car of the form using numeric codes.

        # For simplicity: compile eval to handle these operator fixnums:
        # 0 = quote, 1 = if, 2 = +, 3 = -, 4 = *, 5 = /,
        # 6 = cons, 7 = car, 8 = cdr, 9 = eq, 10 = print, 11 = defun

        op_quote = self._label("eval_quote")
        op_if = self._label("eval_if")
        op_add = self._label("eval_add")
        op_sub = self._label("eval_sub")
        op_mul = self._label("eval_mul")
        op_div = self._label("eval_div")
        op_cons = self._label("eval_cons")
        op_car = self._label("eval_car")
        op_cdr = self._label("eval_cdr")
        op_eq = self._label("eval_eq")
        op_print_val = self._label("eval_print")
        eval_unknown = self._label("eval_unknown")

        # quote (0)
        self._emit_instr(f"LI r9, 0")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_quote}")

        # if (2)
        self._emit_instr(f"LI r9, {tag_fixnum(1)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_if}")

        # + (4)
        self._emit_instr(f"LI r9, {tag_fixnum(2)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_add}")

        # - (6)
        self._emit_instr(f"LI r9, {tag_fixnum(3)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_sub}")

        # * (8)
        self._emit_instr(f"LI r9, {tag_fixnum(4)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_mul}")

        # / (10)
        self._emit_instr(f"LI r9, {tag_fixnum(5)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_div}")

        # cons (12)
        self._emit_instr(f"LI r9, {tag_fixnum(6)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_cons}")

        # car (14)
        self._emit_instr(f"LI r9, {tag_fixnum(7)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_car}")

        # cdr (16)
        self._emit_instr(f"LI r9, {tag_fixnum(8)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_cdr}")

        # eq (18)
        self._emit_instr(f"LI r9, {tag_fixnum(9)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_eq}")

        # print (20)
        self._emit_instr(f"LI r9, {tag_fixnum(10)}")
        self._emit_instr(f"EQ r9, r17, r9")
        self._emit_instr(f"BR.T r9, {op_print_val}")

        # unknown
        self._emit_instr(f"BR {eval_unknown}")

        # --- quote: (0 x) → x ---
        self._emit_label(op_quote)
        self._emit_instr("LD.CAR r1, r18")  # first arg, unevaluated
        self._emit_instr("RET")

        # --- if: (1 test then else) ---
        self._emit_label(op_if)
        if_else = self._label("if_else")
        if_end = self._label("if_end")
        # Eval test
        self._emit_instr("PUSH r18")
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("POP r18")
        # If nil → else branch
        self._emit_instr(f"BR.NIL r1, {if_else}")
        # Then: eval second element
        self._emit_instr("LD.CDR r18, r18")
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr(f"BR {if_end}")
        self._emit_label(if_else)
        # Else: eval third element
        self._emit_instr("LD.CDR r18, r18")
        self._emit_instr("LD.CDR r18, r18")
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_label(if_end)
        self._emit_instr("RET")

        # --- Binary ops: eval both args, apply ---
        for label, op_name, instr in [
            (op_add, "+", "ADD.FIX"),
            (op_sub, "-", "SUB.FIX"),
            (op_mul, "*", "MUL.FIX"),
            (op_div, "/", "DIV.FIX"),
        ]:
            self._emit_label(label)
            # Eval first arg
            self._emit_instr("PUSH r18")
            self._emit_instr("LD.CAR r1, r18")
            self._emit_instr("CALL.DIRECT _fn_eval")
            self._emit_instr("POP r18")
            self._emit_instr("PUSH r1")  # save arg1 result
            # Eval second arg
            self._emit_instr("LD.CDR r18, r18")
            self._emit_instr("LD.CAR r1, r18")
            self._emit_instr("CALL.DIRECT _fn_eval")
            self._emit_instr(f"MOV r{self.SCRATCH_REGS[1]}, r1")
            self._emit_instr("POP r9")  # restore arg1
            self._emit_instr(f"{instr} r1, r9, r{self.SCRATCH_REGS[1]}")
            self._emit_instr("RET")

        # --- cons: (6 x y) → eval both, cons ---
        self._emit_label(op_cons)
        self._emit_instr("PUSH r18")
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("POP r18")
        self._emit_instr("PUSH r1")
        self._emit_instr("LD.CDR r18, r18")
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("POP r9")
        self._emit_instr("ALLOC.CONS r1, r9, r1")
        self._emit_instr("RET")

        # --- car: (7 x) → eval, car ---
        self._emit_label(op_car)
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("LD.CAR r1, r1")
        self._emit_instr("RET")

        # --- cdr: (8 x) → eval, cdr ---
        self._emit_label(op_cdr)
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("LD.CDR r1, r1")
        self._emit_instr("RET")

        # --- eq: (9 x y) → eval both, eq ---
        self._emit_label(op_eq)
        self._emit_instr("PUSH r18")
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("POP r18")
        self._emit_instr("PUSH r1")
        self._emit_instr("LD.CDR r18, r18")
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("POP r9")
        self._emit_instr("EQ r1, r9, r1")
        self._emit_instr("RET")

        # --- print: (10 x) → eval and print ---
        self._emit_label(op_print_val)
        self._emit_instr("LD.CAR r1, r18")
        self._emit_instr("CALL.DIRECT _fn_eval")
        self._emit_instr("CALL.DIRECT _fn_print")
        self._emit_instr("CALL.DIRECT _fn_newline")
        self._emit_instr("LI r1, 5")  # return nil
        self._emit_instr("RET")

        # --- unknown operator ---
        self._emit_label(eval_unknown)
        self._emit_instr("LI r1, 5")  # nil
        self._emit_instr("RET")

        self._emit("")
        self._functions['eval'] = '_fn_eval'

    def emit_repl(self) -> None:
        """Emit a simple REPL: read expr, eval, print result, loop."""
        self._emit("; ===== REPL =====")
        self._emit_label("_fn_repl")
        repl_loop = self._label("repl_loop")

        self._emit_label(repl_loop)
        # Print prompt
        self._emit_instr(f"LI r1, {tag_fixnum(ord('>'))}")
        self._emit_instr("TRAP 0x80")
        self._emit_instr(f"LI r1, {tag_fixnum(ord(' '))}")
        self._emit_instr("TRAP 0x80")

        # Read
        self._emit_instr("CALL.DIRECT _fn_read")
        # Eval
        self._emit_instr("CALL.DIRECT _fn_eval")
        # Print
        self._emit_instr("CALL.DIRECT _fn_print")
        self._emit_instr("CALL.DIRECT _fn_newline")

        self._emit_instr(f"BR {repl_loop}")

        self._emit("")
        self._functions['repl'] = '_fn_repl'
