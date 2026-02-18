"""Crystal Desktop — Expression-Tree Desktop for the LM-1 List Machine.

The screen is a single, nested expression tree that renders itself.
Every pixel traces back to a node in this tree — a Lisp object you can
inspect, edit, and connect.

Core concepts:
  Crystal — root of the expression tree; the entire desktop state
  Portal  — a live lens onto any Lisp object
  Pane    — recursive spatial partitioning (splits, tabs, floats)
  Bar     — the crystal bar: launcher, pins, clock
  Lens    — a rendering function: (object, rect) → pixels

VDI provides drawing primitives (unchanged from prototype).
Toolkit widgets are reused inside lenses for rich interaction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional, Protocol

from .vdi import (
    VDI, BG_TRANSPARENT, GRAD_VERTICAL, GRAD_HORIZONTAL,
    EVT_NONE, EVT_KEY_DOWN, EVT_KEY_UP,
    EVT_MOUSE_MOVE, EVT_MOUSE_DOWN, EVT_MOUSE_UP, EVT_QUIT,
    rgb, lerp_color, alpha_blend,
)
from .vfs import VFS
from .icons import Icon, get_icon, icon_for_name


# ===================================================================
# Helpers
# ===================================================================

def _vfs_normalize(path: str) -> str:
    """Normalize a VFS path."""
    parts: list[str] = []
    for p in path.strip('/').split('/'):
        if not p or p == '.':
            continue
        if p == '..':
            if parts:
                parts.pop()
        else:
            parts.append(p)
    return '/' + '/'.join(parts) if parts else '/'


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


# ===================================================================
# Color Palette — Modern dark, beyond-retro
#
# Deep charcoal canvas.  Luminance encodes depth (deeper = darker).
# Focused elements glow with accent color.  No heavy chrome.
# Semantic tinting: functions=cyan, data=amber, actors=green, errors=red.
# ===================================================================

class C:
    """Crystal color palette."""

    # Canvas / surface
    CANVAS          = 0x0D0D14    # near-black indigo
    CANVAS_END      = 0x0A0A10

    # Pane / portal
    PORTAL_BG       = 0x13131E    # slightly lighter than canvas
    PORTAL_EDGE     = 0x252538    # subtle 1px luminance shift
    PORTAL_LABEL_FG = 0x667088    # muted label text
    FOCUS_GLOW      = 0x00B4D8    # bright teal glow for focus
    FOCUS_GLOW_DIM  = 0x004466    # outer glow

    # Pane dividers
    DIVIDER         = 0x1E1E2E
    DIVIDER_GRAB    = 0x3A3A55

    # Bar (crystal bar at bottom)
    BAR_BG          = 0x0A0A12
    BAR_EDGE        = 0x1E1E30
    BAR_TEXT         = 0x8898B0
    BAR_CLOCK       = 0x56B0CC
    BAR_HOVER       = 0x1A2A40

    # Launcher
    LAUNCHER_BG     = 0x151520
    LAUNCHER_HOVER  = 0x1E2A40
    LAUNCHER_TEXT   = 0xB0B8CC
    LAUNCHER_ACCENT = 0x00B4D8

    # Text / content
    TEXT            = 0xD0D8E8    # primary text
    TEXT_DIM        = 0x6878A0    # secondary / muted
    TEXT_BRIGHT     = 0xF0F4FF    # bright emphasis

    # Semantic type colors
    TYPE_FN         = 0x56CCE0    # functions — teal/cyan
    TYPE_DATA       = 0xDDA855    # data structures — warm amber
    TYPE_ACTOR      = 0x44CC88    # actors / processes — green
    TYPE_ERROR      = 0xDD4455    # errors — warm red
    TYPE_STRING     = 0x88BB66    # strings — muted green
    TYPE_NUMBER     = 0xCCA855    # numbers — gold
    TYPE_KEYWORD    = 0xBB77DD    # keywords — purple
    TYPE_COMMENT    = 0x555577    # comments — muted

    # Interactive
    BUTTON_BG       = 0x1A1A2A
    BUTTON_HOVER    = 0x222240
    BUTTON_BORDER   = 0x333355
    BUTTON_TEXT     = 0xC0C8DD

    # Scrapbook
    SCRAP_BG        = 0x151520
    SCRAP_BORDER    = 0x2A2A40

    # General UI
    BLACK           = 0x000000
    WHITE           = 0xFFFFFF
    SELECTION       = 0x1A3A66
    CURSOR          = 0x00B4D8


# ===================================================================
# Layout constants
# ===================================================================

BAR_H          = 28      # crystal bar height
DIVIDER_W      = 3       # pane divider thickness
PORTAL_LABEL_H = 22      # label strip at top of portal
PORTAL_PAD     = 2       # padding around portal content
FOCUS_GLOW_W   = 2       # focus glow thickness
MIN_PORTAL_W   = 80
MIN_PORTAL_H   = 60
CLOSE_BTN_W    = 18      # close button width in label strip


# ===================================================================
# Rect helper
# ===================================================================

@dataclass
class Rect:
    """Screen rectangle."""
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    def shrink(self, t: int = 0, r: int = 0, b: int = 0, l: int = 0) -> 'Rect':
        return Rect(self.x + l, self.y + t,
                    max(0, self.w - l - r), max(0, self.h - t - b))

    def split_v(self, ratio: float) -> tuple['Rect', 'Rect']:
        """Split vertically (left | right)."""
        lw = int(self.w * ratio) - DIVIDER_W // 2
        rw = self.w - lw - DIVIDER_W
        return (Rect(self.x, self.y, lw, self.h),
                Rect(self.x + lw + DIVIDER_W, self.y, rw, self.h))

    def split_h(self, ratio: float) -> tuple['Rect', 'Rect']:
        """Split horizontally (top / bottom)."""
        th = int(self.h * ratio) - DIVIDER_W // 2
        bh = self.h - th - DIVIDER_W
        return (Rect(self.x, self.y, self.w, th),
                Rect(self.x, self.y + th + DIVIDER_W, self.w, bh))

    def divider_v(self, ratio: float) -> 'Rect':
        """The divider rect for a vertical split."""
        lw = int(self.w * ratio) - DIVIDER_W // 2
        return Rect(self.x + lw, self.y, DIVIDER_W, self.h)

    def divider_h(self, ratio: float) -> 'Rect':
        """The divider rect for a horizontal split."""
        th = int(self.h * ratio) - DIVIDER_W // 2
        return Rect(self.x, self.y + th, self.w, DIVIDER_W)


# ===================================================================
# Lens protocol
# ===================================================================

class Lens(Protocol):
    """A lens renders an object into a rectangle."""

    name: str

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None: ...

    def on_key(self, target: Any, key: int, mod: int,
               state: dict) -> bool: ...

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool: ...

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool: ...


# ===================================================================
# Portal
# ===================================================================

@dataclass
class Portal:
    """A live lens onto a Lisp object."""
    target: Any                         # the object being viewed
    lens_name: str = 'inspect'          # which lens to use
    label: str = ''                     # tiny label shown at top
    state: dict = field(default_factory=dict)  # lens-private state
    rect: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    pid: int = 0                        # portal id

    def content_rect(self) -> Rect:
        """Rectangle available for the lens to draw in."""
        return self.rect.shrink(t=PORTAL_LABEL_H, l=PORTAL_PAD,
                                r=PORTAL_PAD, b=PORTAL_PAD)


# ===================================================================
# Pane — recursive spatial partitioning
# ===================================================================

class SplitDir(Enum):
    HORIZONTAL = auto()   # top / bottom
    VERTICAL   = auto()   # left | right


@dataclass
class Pane:
    """A recursive spatial region containing portals or child panes."""
    # Content: either a portal, or a split with two child panes, or tabs
    portal: Portal | None = None           # leaf node: a single portal
    split: SplitDir | None = None          # split direction
    ratio: float = 0.5                     # split position (0..1)
    children: list['Pane'] = field(default_factory=list)  # 2 for split, N for tabs
    tab_mode: bool = False                 # if True, children are tabs
    active_tab: int = 0                    # index of active tab (tab mode)
    rect: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    # Float mode (breakout from tiling)
    floating: bool = False
    float_pos: tuple[int, int] = (100, 100)
    float_size: tuple[int, int] = (400, 300)

    def is_leaf(self) -> bool:
        return self.portal is not None

    def all_portals(self) -> list[Portal]:
        """Collect all portals in this subtree."""
        if self.portal:
            return [self.portal]
        result: list[Portal] = []
        for child in self.children:
            result.extend(child.all_portals())
        return result


# ===================================================================
# Crystal Bar items
# ===================================================================

@dataclass
class BarItem:
    """An item on the crystal bar."""
    label: str
    icon_name: str = ''
    action: Callable | None = None
    portal_target: Any = None   # if set, this is a mini-portal pin


@dataclass
class BarClock:
    """Clock widget on the bar."""
    format_str: str = '%H:%M'
    last_str: str = ''


# ===================================================================
# Scrapbook (evolved clipboard)
# ===================================================================

@dataclass
class ScrapEntry:
    """A single scrapbook entry."""
    data: Any
    scrap_type: str = 'text/plain'
    timestamp: float = 0.0
    source: str = ''


class Scrapbook:
    """Typed clipboard with history."""

    def __init__(self):
        self.entries: list[ScrapEntry] = []
        self.max_history = 50

    @property
    def empty(self) -> bool:
        return len(self.entries) == 0

    def snip(self, data: Any, scrap_type: str = 'text/plain',
             source: str = '') -> None:
        entry = ScrapEntry(data=data, scrap_type=scrap_type,
                           timestamp=time.time(), source=source)
        self.entries.append(entry)
        if len(self.entries) > self.max_history:
            self.entries = self.entries[-self.max_history:]

    def paste(self, scrap_type: str | None = None) -> ScrapEntry | None:
        if not self.entries:
            return None
        if scrap_type:
            for e in reversed(self.entries):
                if e.scrap_type == scrap_type:
                    return e
            return None
        return self.entries[-1]


# ===================================================================
# Built-in Lenses
# ===================================================================

class InspectLens:
    """Generic slot-by-slot inspector.  Works on any object."""
    name = 'inspect'

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, C.PORTAL_BG)

        y = rect.y + 4
        max_cols = max(1, rect.w // cw - 1)

        def _line(text: str, fg: int = C.TEXT):
            nonlocal y
            if y + ch > rect.y + rect.h:
                return
            vdi.draw_string(rect.x + 6, y, text[:max_cols], fg, BG_TRANSPARENT)
            y += ch + 1

        # Type header
        type_name = type(target).__name__
        _line(f"<{type_name}>", C.TYPE_FN)
        _line("")

        if isinstance(target, dict):
            for k, v in target.items():
                ks = str(k)
                vs = repr(v)
                if len(vs) > max_cols - len(ks) - 4:
                    vs = vs[:max_cols - len(ks) - 7] + '...'
                _line(f"  {ks}: {vs}", C.TEXT)
        elif isinstance(target, (list, tuple)):
            for i, v in enumerate(target):
                vs = repr(v)
                if len(vs) > max_cols - 6:
                    vs = vs[:max_cols - 9] + '...'
                _line(f"  [{i}] {vs}", C.TEXT)
        elif isinstance(target, str):
            for line in target.split('\n'):
                _line(f"  {line}", C.TYPE_STRING)
        elif isinstance(target, (int, float)):
            _line(f"  value: {target}", C.TYPE_NUMBER)
            if isinstance(target, int):
                _line(f"  hex:   0x{target:X}", C.TYPE_NUMBER)
                _line(f"  bin:   0b{target:b}", C.TYPE_NUMBER)
        elif hasattr(target, '__dict__'):
            for k, v in vars(target).items():
                if k.startswith('_'):
                    continue
                vs = repr(v)
                if len(vs) > max_cols - len(k) - 4:
                    vs = vs[:max_cols - len(k) - 7] + '...'
                _line(f"  {k}: {vs}", C.TEXT)
        else:
            _line(f"  {repr(target)}", C.TEXT)

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        return False

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


class PrettyLens:
    """Pretty-printed s-expression / repr."""
    name = 'pretty'

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, C.PORTAL_BG)

        text = self._pretty(target, indent=0)
        lines = text.split('\n')
        scroll_y = state.get('scroll_y', 0)
        max_vis = rect.h // ch
        max_cols = max(1, rect.w // cw - 1)

        for i in range(max_vis):
            li = scroll_y + i
            if li >= len(lines):
                break
            line = lines[li]
            y = rect.y + 4 + i * ch
            # Colorize s-expression tokens
            self._draw_sexp_line(vdi, rect.x + 6, y, line[:max_cols])

    def _draw_sexp_line(self, vdi: VDI, x: int, y: int, text: str) -> None:
        """Draw a line with basic s-expression coloring."""
        cw = vdi.font.char_w
        i = 0
        px = x
        while i < len(text):
            ch = text[i]
            if ch in '()[]':
                fg = C.TEXT_DIM
            elif ch == ';':
                # Rest is comment
                vdi.draw_string(px, y, text[i:], C.TYPE_COMMENT, BG_TRANSPARENT)
                return
            elif ch == '"':
                # String literal
                j = i + 1
                while j < len(text) and text[j] != '"':
                    if text[j] == '\\':
                        j += 1
                    j += 1
                sstr = text[i:min(j + 1, len(text))]
                vdi.draw_string(px, y, sstr, C.TYPE_STRING, BG_TRANSPARENT)
                px += len(sstr) * cw
                i = j + 1
                continue
            elif ch.isdigit() or (ch == '-' and i + 1 < len(text)
                                  and text[i + 1].isdigit()):
                fg = C.TYPE_NUMBER
            elif ch not in ' \t\n':
                fg = C.TEXT
            else:
                fg = C.TEXT
            vdi.draw_char(px, y, ord(ch), fg, BG_TRANSPARENT)
            px += cw
            i += 1

    def _pretty(self, obj: Any, indent: int = 0) -> str:
        pad = '  ' * indent
        if obj is None:
            return f'{pad}nil'
        if isinstance(obj, bool):
            return f'{pad}{"t" if obj else "nil"}'
        if isinstance(obj, (int, float)):
            return f'{pad}{obj}'
        if isinstance(obj, str):
            return f'{pad}"{obj}"'
        if isinstance(obj, list):
            if not obj:
                return f'{pad}()'
            if len(obj) <= 4 and all(isinstance(x, (int, float, str, type(None)))
                                      for x in obj):
                inner = ' '.join(self._pretty(x, 0) for x in obj)
                return f'{pad}({inner})'
            lines = [f'{pad}(']
            for item in obj:
                lines.append(self._pretty(item, indent + 1))
            lines.append(f'{pad})')
            return '\n'.join(lines)
        if isinstance(obj, dict):
            if not obj:
                return f'{pad}(dict)'
            lines = [f'{pad}(dict']
            for k, v in obj.items():
                lines.append(f'{pad}  (:{k} {self._pretty(v, 0)})')
            lines.append(f'{pad})')
            return '\n'.join(lines)
        return f'{pad}{repr(obj)}'

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        import pygame
        ch_h = 16  # font height
        if key == pygame.K_UP:
            state['scroll_y'] = max(0, state.get('scroll_y', 0) - 1)
            return True
        if key == pygame.K_DOWN:
            state['scroll_y'] = state.get('scroll_y', 0) + 1
            return True
        if key == pygame.K_PAGEUP:
            state['scroll_y'] = max(0, state.get('scroll_y', 0) - 10)
            return True
        if key == pygame.K_PAGEDOWN:
            state['scroll_y'] = state.get('scroll_y', 0) + 10
            return True
        return False

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


class TerminalLens:
    """Interactive REPL with scrollback."""
    name = 'terminal'

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, C.BLACK)

        lines: list[str] = state.setdefault('lines', [
            'Crystal Terminal v3.0',
            'The desktop is a living expression.',
            "Type 'help' for commands.", ''
        ])
        input_buf: str = state.setdefault('input_buf', '')
        prompt = '> '

        max_vis = max(1, rect.h // ch - 1)  # -1 for input line
        max_cols = max(1, rect.w // cw - 1)
        display_lines = lines[-(max_vis):]

        for i, line in enumerate(display_lines):
            y = rect.y + 2 + i * ch
            vdi.draw_string(rect.x + 4, y, line[:max_cols],
                            C.TYPE_FN, C.BLACK)

        # Input line
        input_y = rect.y + 2 + len(display_lines) * ch
        input_line = prompt + input_buf
        # Cursor blink
        show_cursor = focused and (int(time.time() * 2) % 2 == 0)
        if show_cursor:
            input_line += '_'
        vdi.draw_string(rect.x + 4, input_y, input_line[:max_cols],
                        C.TYPE_FN, C.BLACK)

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        import pygame
        lines = state.setdefault('lines', [])
        input_buf = state.setdefault('input_buf', '')

        if key == pygame.K_RETURN:
            cmd = input_buf.strip()
            lines.append('> ' + cmd)
            state['input_buf'] = ''
            if cmd:
                self._execute(cmd, lines, state)
            return True
        elif key == pygame.K_BACKSPACE:
            if input_buf:
                state['input_buf'] = input_buf[:-1]
            return True
        elif key == pygame.K_ESCAPE:
            state['input_buf'] = ''
            return True
        elif 32 <= key <= 126:
            ch_char = chr(key)
            if mod & pygame.KMOD_SHIFT:
                ch_char = ch_char.upper()
                shift_map = {
                    '1': '!', '2': '@', '3': '#', '4': '$', '5': '%',
                    '6': '^', '7': '&', '8': '*', '9': '(', '0': ')',
                    '-': '_', '=': '+', '[': '{', ']': '}', '\\': '|',
                    ';': ':', "'": '"', ',': '<', '.': '>', '/': '?',
                    '`': '~',
                }
                if chr(key) in shift_map:
                    ch_char = shift_map[chr(key)]
            state['input_buf'] = input_buf + ch_char
            return True
        return False

    def _execute(self, cmd: str, lines: list[str], state: dict) -> None:
        parts = cmd.split()
        name = parts[0].lower()

        if name == 'help':
            lines.append('Commands: help, clear, time, windows, quit')
            lines.append('         ls [path], cd <path>, cat <file>, pwd')
            lines.append('Lisp: (+ 1 2), (list 1 2 3), (quote hello)')
        elif name == 'clear':
            lines.clear()
        elif name == 'time':
            lines.append(time.strftime('%Y-%m-%d %H:%M:%S'))
        elif name == 'quit':
            state['_quit'] = True
        elif name == 'pwd':
            lines.append(state.get('_cwd', '/'))
        elif name == 'ls':
            path = parts[1] if len(parts) > 1 else state.get('_cwd', '/')
            vfs = state.get('_vfs')
            if vfs:
                try:
                    if not path.startswith('/'):
                        path = state.get('_cwd', '/').rstrip('/') + '/' + path
                    children = vfs.list_dir(path)
                    for node in children:
                        suffix = '/' if node.is_dir else ''
                        lines.append(f'  {node.name}{suffix}')
                    if not children:
                        lines.append('  (empty)')
                except Exception as e:
                    lines.append(f'ls: {e}')
            else:
                lines.append('(no filesystem)')
        elif name == 'cd':
            if len(parts) > 1:
                path = parts[1]
                if not path.startswith('/'):
                    path = state.get('_cwd', '/').rstrip('/') + '/' + path
                vfs = state.get('_vfs')
                if vfs:
                    try:
                        vfs.resolve_dir(path)
                        state['_cwd'] = _vfs_normalize(path)
                    except Exception as e:
                        lines.append(f'cd: {e}')
                else:
                    lines.append('(no filesystem)')
        elif name == 'cat':
            if len(parts) > 1:
                path = parts[1]
                if not path.startswith('/'):
                    path = state.get('_cwd', '/').rstrip('/') + '/' + path
                vfs = state.get('_vfs')
                if vfs:
                    try:
                        text = vfs.read_text(path)
                        for line in text.split('\n'):
                            lines.append(line)
                    except Exception as e:
                        lines.append(f'cat: {e}')
            else:
                lines.append('usage: cat <file>')
        elif name == 'windows':
            crystal = state.get('_crystal')
            if crystal:
                portals = crystal.root.all_portals() if crystal.root else []
                for p in portals:
                    lines.append(f'  [{p.pid}] {p.label or p.lens_name} → {type(p.target).__name__}')
            else:
                lines.append('(no crystal)')
        else:
            # Try Lisp evaluation
            try:
                from .compiler import parse
                forms = parse(cmd)
                for form in forms:
                    result = self._eval_form(form)
                    lines.append(self._print_form(result))
            except Exception as e:
                lines.append(f'Error: {e}')

    def _eval_form(self, form: Any) -> Any:
        if isinstance(form, int):
            return form
        if isinstance(form, str):
            if form == 'nil':
                return None
            if form == 't':
                return True
            if form == 'pi':
                return 3.14159
            return form
        if isinstance(form, list):
            if not form:
                return None
            op = form[0]
            if op == 'quote':
                return form[1] if len(form) > 1 else None
            if op == '+':
                args = [self._eval_form(a) for a in form[1:]]
                return sum(a for a in args if isinstance(a, (int, float)))
            if op == '-':
                args = [self._eval_form(a) for a in form[1:]]
                return -args[0] if len(args) == 1 else args[0] - sum(args[1:])
            if op == '*':
                r = 1
                for a in form[1:]:
                    r *= self._eval_form(a)
                return r
            if op == '/':
                a, b = self._eval_form(form[1]), self._eval_form(form[2])
                if b == 0:
                    raise ValueError('division by zero')
                return a // b if isinstance(a, int) and isinstance(b, int) else a / b
            if op == 'if':
                cond = self._eval_form(form[1])
                if cond and cond is not None:
                    return self._eval_form(form[2])
                return self._eval_form(form[3]) if len(form) > 3 else None
            if op == 'list':
                return [self._eval_form(a) for a in form[1:]]
            if op == 'car':
                lst = self._eval_form(form[1])
                return lst[0] if isinstance(lst, list) and lst else None
            if op == 'cdr':
                lst = self._eval_form(form[1])
                return lst[1:] if isinstance(lst, list) and len(lst) > 1 else None
            if op == 'cons':
                a, b = self._eval_form(form[1]), self._eval_form(form[2])
                return [a] + b if isinstance(b, list) else [a, b]
            if op == 'eq':
                return self._eval_form(form[1]) == self._eval_form(form[2])
            if op == 'fact':
                n = self._eval_form(form[1])
                r = 1
                for i in range(2, n + 1):
                    r *= i
                return r
            raise ValueError(f'unknown function: {op}')
        return form

    def _print_form(self, form: Any) -> str:
        if form is None:
            return 'nil'
        if form is True:
            return 't'
        if form is False:
            return 'nil'
        if isinstance(form, list):
            return '(' + ' '.join(self._print_form(x) for x in form) + ')'
        return str(form)

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


class EditorLens:
    """Full-featured text editor with syntax highlighting, line numbers, undo."""
    name = 'editor'

    _LISP_KEYWORDS = frozenset([
        'defun', 'defmacro', 'lambda', 'let', 'let*', 'letrec',
        'if', 'cond', 'when', 'unless', 'and', 'or', 'not',
        'begin', 'progn', 'do', 'loop', 'while',
        'define', 'set!', 'setq', 'quote', 'quasiquote',
        'defstruct', 'defgeneric', 'defmethod', 'deflens',
        'defreactive',
    ])
    _LISP_BUILTINS = frozenset([
        'cons', 'car', 'cdr', 'list', 'append', 'reverse', 'length',
        'map', 'filter', 'reduce', 'apply', 'eval',
        'eq', 'equal', 'null', 'atom', 'pair',
        '+', '-', '*', '/', 'mod', 'rem',
        '=', '<', '>', '<=', '>=', '/=',
        'print', 'display', 'newline', 'format',
        'read', 'write', 'load', 'portal', 'pane', 'crystal',
    ])

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h

        # Initialize state from target
        if 'lines' not in state:
            if isinstance(target, str):
                state['lines'] = target.split('\n')
            elif isinstance(target, dict) and 'text' in target:
                state['lines'] = target['text'].split('\n')
            else:
                state['lines'] = [repr(target)]
            state.setdefault('cursor_line', 0)
            state.setdefault('cursor_col', 0)
            state.setdefault('scroll_y', 0)
            state.setdefault('scroll_x', 0)
            state.setdefault('modified', False)
            state.setdefault('undo', [])
            state.setdefault('redo', [])
            state.setdefault('is_lisp', False)

        lines = state['lines']
        cursor_line = state['cursor_line']
        cursor_col = state['cursor_col']
        scroll_y = state['scroll_y']
        scroll_x = state['scroll_x']
        is_lisp = state.get('is_lisp', False)

        # Background
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, 0x0E0E1A)

        # Gutter
        gutter_w = max(4, len(str(max(1, len(lines))))) * cw + 8
        status_h = ch + 6
        edit_h = rect.h - status_h
        max_vis = max(1, edit_h // ch)
        text_w = rect.w - gutter_w

        for i in range(max_vis):
            line_idx = scroll_y + i
            if line_idx >= len(lines):
                break
            y = rect.y + i * ch
            line = lines[line_idx]

            # Current line highlight
            if line_idx == cursor_line:
                vdi.fill_rect(rect.x, y, rect.w, ch, 0x1A1A30)

            # Line number
            ln_str = str(line_idx + 1).rjust(gutter_w // cw - 1)
            ln_fg = C.TYPE_FN if line_idx == cursor_line else C.TEXT_DIM
            vdi.draw_string(rect.x + 2, y, ln_str, ln_fg, BG_TRANSPARENT)

            # Gutter separator
            gx = rect.x + gutter_w - 2
            for gy in range(y, min(y + ch, rect.y + rect.h)):
                if 0 <= gx < vdi.width and 0 <= gy < vdi.height:
                    vdi.fb[gy * vdi.width + gx] = 0x252540

            # Text
            text_x = rect.x + gutter_w
            visible_chars = max(1, text_w // cw)
            display_line = line[scroll_x:scroll_x + visible_chars]

            if is_lisp:
                spans = self._lisp_highlight(line)
                for ci, c in enumerate(display_line):
                    abs_ci = ci + scroll_x
                    fg = C.TEXT
                    for start, end, color in spans:
                        if start <= abs_ci < end:
                            fg = color
                            break
                    vdi.draw_char(text_x + ci * cw, y, ord(c),
                                  fg, BG_TRANSPARENT)
            else:
                vdi.draw_string(text_x, y, display_line,
                                C.TEXT, BG_TRANSPARENT)

            # Cursor
            if line_idx == cursor_line and focused:
                cur_x = cursor_col - scroll_x
                if 0 <= cur_x < visible_chars:
                    cx = text_x + cur_x * cw
                    vdi.fill_rect(cx, y, 2, ch, C.CURSOR)

        # Status bar
        sy = rect.y + rect.h - status_h
        vdi.fill_rect(rect.x, sy, rect.w, status_h, 0x111120)
        vdi.draw_line(rect.x, sy, rect.x + rect.w - 1, sy, C.PORTAL_EDGE)
        mod_marker = ' [modified]' if state.get('modified') else ''
        status = f' Ln {cursor_line + 1}, Col {cursor_col + 1}{mod_marker}'
        vdi.draw_string(rect.x + 4, sy + 3, status, C.TEXT_DIM, 0x111120)

    def _lisp_highlight(self, line: str) -> list[tuple[int, int, int]]:
        spans: list[tuple[int, int, int]] = []
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if ch == ';':
                spans.append((i, n, C.TYPE_COMMENT))
                break
            if ch == '"':
                j = i + 1
                while j < n and line[j] != '"':
                    if line[j] == '\\':
                        j += 1
                    j += 1
                spans.append((i, min(j + 1, n), C.TYPE_STRING))
                i = j + 1
                continue
            if ch in '()[]':
                spans.append((i, i + 1, C.TEXT_DIM))
                i += 1
                continue
            if ch.isdigit() or (ch == '-' and i + 1 < n and line[i + 1].isdigit()):
                j = i + 1
                while j < n and (line[j].isdigit() or line[j] == '.'):
                    j += 1
                spans.append((i, j, C.TYPE_NUMBER))
                i = j
                continue
            if ch == ':':
                j = i + 1
                while j < n and line[j] not in ' \t()[]";\n':
                    j += 1
                spans.append((i, j, C.TYPE_KEYWORD))
                i = j
                continue
            if ch not in ' \t\n':
                j = i + 1
                while j < n and line[j] not in ' \t()[]";\n':
                    j += 1
                word = line[i:j]
                if word in self._LISP_KEYWORDS:
                    spans.append((i, j, C.TYPE_KEYWORD))
                elif word in self._LISP_BUILTINS:
                    spans.append((i, j, C.TYPE_FN))
                i = j
                continue
            i += 1
        return spans

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        import pygame
        lines = state.get('lines', [''])
        cl = state.get('cursor_line', 0)
        cc = state.get('cursor_col', 0)
        ctrl = mod & pygame.KMOD_CTRL

        if ctrl:
            if key == ord('z'):
                self._undo(state)
                return True
            if key == ord('y'):
                self._redo(state)
                return True
            if key == ord('s'):
                self._save(target, state)
                return True

        if key == pygame.K_RETURN:
            self._push_undo(state)
            line = lines[cl]
            lines[cl] = line[:cc]
            lines.insert(cl + 1, line[cc:])
            state['cursor_line'] = cl + 1
            state['cursor_col'] = 0
            state['modified'] = True
        elif key == pygame.K_BACKSPACE:
            if cc > 0:
                self._push_undo(state)
                line = lines[cl]
                lines[cl] = line[:cc - 1] + line[cc:]
                state['cursor_col'] = cc - 1
                state['modified'] = True
            elif cl > 0:
                self._push_undo(state)
                prev = lines[cl - 1]
                curr = lines.pop(cl)
                state['cursor_line'] = cl - 1
                state['cursor_col'] = len(prev)
                lines[cl - 1] = prev + curr
                state['modified'] = True
        elif key == pygame.K_DELETE:
            line = lines[cl]
            if cc < len(line):
                self._push_undo(state)
                lines[cl] = line[:cc] + line[cc + 1:]
                state['modified'] = True
            elif cl < len(lines) - 1:
                self._push_undo(state)
                lines[cl] = line + lines.pop(cl + 1)
                state['modified'] = True
        elif key == pygame.K_LEFT:
            if ctrl:
                p = cc - 1
                line = lines[cl]
                while p > 0 and not line[p - 1].isalnum():
                    p -= 1
                while p > 0 and line[p - 1].isalnum():
                    p -= 1
                state['cursor_col'] = max(0, p)
            elif cc > 0:
                state['cursor_col'] = cc - 1
            elif cl > 0:
                state['cursor_line'] = cl - 1
                state['cursor_col'] = len(lines[cl - 1])
        elif key == pygame.K_RIGHT:
            line = lines[cl]
            if ctrl:
                p = cc
                while p < len(line) and not line[p].isalnum():
                    p += 1
                while p < len(line) and line[p].isalnum():
                    p += 1
                state['cursor_col'] = p
            elif cc < len(line):
                state['cursor_col'] = cc + 1
            elif cl < len(lines) - 1:
                state['cursor_line'] = cl + 1
                state['cursor_col'] = 0
        elif key == pygame.K_UP:
            if cl > 0:
                state['cursor_line'] = cl - 1
                state['cursor_col'] = min(cc, len(lines[cl - 1]))
        elif key == pygame.K_DOWN:
            if cl < len(lines) - 1:
                state['cursor_line'] = cl + 1
                state['cursor_col'] = min(cc, len(lines[cl + 1]))
        elif key == pygame.K_HOME:
            state['cursor_col'] = 0
        elif key == pygame.K_END:
            state['cursor_col'] = len(lines[cl])
        elif key == pygame.K_TAB:
            self._push_undo(state)
            line = lines[cl]
            lines[cl] = line[:cc] + '  ' + line[cc:]
            state['cursor_col'] = cc + 2
            state['modified'] = True
        elif 32 <= key <= 126:
            ch_char = chr(key)
            shift = mod & pygame.KMOD_SHIFT
            if shift:
                ch_char = ch_char.upper()
                shift_map = {
                    '1': '!', '2': '@', '3': '#', '4': '$', '5': '%',
                    '6': '^', '7': '&', '8': '*', '9': '(', '0': ')',
                    '-': '_', '=': '+', '[': '{', ']': '}', '\\': '|',
                    ';': ':', "'": '"', ',': '<', '.': '>', '/': '?',
                    '`': '~',
                }
                if chr(key) in shift_map:
                    ch_char = shift_map[chr(key)]
            self._push_undo(state)
            line = lines[cl]
            lines[cl] = line[:cc] + ch_char + line[cc:]
            state['cursor_col'] = cc + 1
            state['modified'] = True
        else:
            return False

        self._ensure_visible(state)
        return True

    def _push_undo(self, state: dict) -> None:
        undo = state.setdefault('undo', [])
        undo.append(([l for l in state['lines']],
                      state['cursor_line'], state['cursor_col']))
        if len(undo) > 100:
            state['undo'] = undo[-100:]
        state['redo'] = []

    def _undo(self, state: dict) -> None:
        undo = state.get('undo', [])
        if not undo:
            return
        redo = state.setdefault('redo', [])
        redo.append(([l for l in state['lines']],
                      state['cursor_line'], state['cursor_col']))
        lines, cl, cc = undo.pop()
        state['lines'] = lines
        state['cursor_line'] = cl
        state['cursor_col'] = cc

    def _redo(self, state: dict) -> None:
        redo = state.get('redo', [])
        if not redo:
            return
        undo = state.setdefault('undo', [])
        undo.append(([l for l in state['lines']],
                      state['cursor_line'], state['cursor_col']))
        lines, cl, cc = redo.pop()
        state['lines'] = lines
        state['cursor_line'] = cl
        state['cursor_col'] = cc

    def _save(self, target: Any, state: dict) -> None:
        if isinstance(target, dict) and 'vfs' in state and 'path' in state:
            try:
                text = '\n'.join(state['lines']) + '\n'
                state['vfs'].write(state['path'], text)
                state['modified'] = False
            except Exception:
                pass

    def _ensure_visible(self, state: dict) -> None:
        cl = state.get('cursor_line', 0)
        cc = state.get('cursor_col', 0)
        scroll_y = state.get('scroll_y', 0)
        scroll_x = state.get('scroll_x', 0)
        # Approximate — real rect size not available here
        max_vis = 30
        visible_chars = 80

        if cl < scroll_y:
            state['scroll_y'] = cl
        elif cl >= scroll_y + max_vis:
            state['scroll_y'] = cl - max_vis + 1
        if cc < scroll_x:
            state['scroll_x'] = cc
        elif cc >= scroll_x + visible_chars:
            state['scroll_x'] = cc - visible_chars + 1

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        cw = 8  # font char width
        ch = 16
        lines = state.get('lines', [''])
        gutter_w = max(4, len(str(max(1, len(lines))))) * cw + 8
        if rx < gutter_w:
            return False
        text_col = (rx - gutter_w) // cw + state.get('scroll_x', 0)
        text_line = ry // ch + state.get('scroll_y', 0)
        if 0 <= text_line < len(lines):
            state['cursor_line'] = text_line
            state['cursor_col'] = min(text_col, len(lines[text_line]))
            return True
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


class TreeLens:
    """Expand/collapse tree view for VFS directories and nested data."""
    name = 'tree'

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, C.PORTAL_BG)

        scroll_y = state.get('scroll_y', 0)
        selected = state.get('selected', -1)
        max_vis = max(1, rect.h // (ch + 2))
        max_cols = max(1, rect.w // cw - 2)

        # Build display entries from target
        entries = state.get('_entries')
        if entries is None:
            entries = self._build_entries(target, state)
            state['_entries'] = entries

        for i in range(max_vis):
            idx = scroll_y + i
            if idx >= len(entries):
                break
            name, is_dir, depth = entries[idx]
            y = rect.y + 2 + i * (ch + 2)

            # Selection highlight
            if idx == selected:
                vdi.fill_rect(rect.x + 1, y, rect.w - 2, ch + 2, C.SELECTION)

            # Indent + icon prefix
            indent = depth * 2
            prefix = '> ' if is_dir else '  '
            label = ' ' * indent + prefix + name

            fg = C.TYPE_FN if is_dir else C.TEXT
            if idx == selected:
                fg = C.TEXT_BRIGHT
            vdi.draw_string(rect.x + 4, y + 1, label[:max_cols],
                            fg, BG_TRANSPARENT)

    def _build_entries(self, target: Any, state: dict) -> list[tuple[str, bool, int]]:
        """Build flat list of (name, is_dir, depth) from target."""
        vfs = state.get('_vfs')
        cwd = state.get('_cwd', '/')
        entries: list[tuple[str, bool, int]] = []
        if vfs:
            try:
                children = vfs.list_dir(cwd)
                dirs = [(n.name, True, 0) for n in children if n.is_dir]
                files = [(n.name, False, 0) for n in children if not n.is_dir]
                entries = dirs + files
            except Exception:
                pass
        elif isinstance(target, dict):
            for k, v in target.items():
                is_container = isinstance(v, (dict, list))
                entries.append((str(k), is_container, 0))
        elif isinstance(target, list):
            for i, v in enumerate(target):
                is_container = isinstance(v, (dict, list))
                entries.append((f'[{i}]', is_container, 0))
        return entries

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        import pygame
        entries = state.get('_entries', [])
        sel = state.get('selected', -1)
        if key == pygame.K_UP and sel > 0:
            state['selected'] = sel - 1
            return True
        if key == pygame.K_DOWN and sel < len(entries) - 1:
            state['selected'] = sel + 1
            return True
        if key == pygame.K_RETURN and 0 <= sel < len(entries):
            name, is_dir, _ = entries[sel]
            vfs = state.get('_vfs')
            cwd = state.get('_cwd', '/')
            if is_dir:
                if vfs:
                    new_path = cwd.rstrip('/') + '/' + name
                    try:
                        vfs.resolve_dir(new_path)
                        state['_cwd'] = _vfs_normalize(new_path)
                        state['_entries'] = None  # rebuild
                        state['selected'] = 0
                    except Exception:
                        pass
            else:
                # Open file in editor portal
                crystal = state.get('_crystal')
                if crystal and vfs:
                    fpath = cwd.rstrip('/') + '/' + name
                    try:
                        text = vfs.read_text(fpath)
                        # Find the right-side main area to open in
                        fp = crystal._focused_portal
                        all_p = crystal.root.all_portals()
                        # Find a non-tree portal to replace/tab, or split
                        target_portal = None
                        for p in all_p:
                            if p.lens_name in ('terminal', 'inspect', 'editor'):
                                target_portal = p
                                break
                        if target_portal:
                            new_p = crystal.add_tab(
                                target_portal, target=text,
                                lens_name='editor', label=name)
                            new_p.state['is_lisp'] = name.endswith('.lisp')
                            new_p.state['_vfs'] = vfs
                            new_p.state['path'] = fpath
                            crystal._focused_portal = new_p
                        crystal._dirty = True
                    except Exception:
                        pass
            return True
        if key == pygame.K_BACKSPACE:
            cwd = state.get('_cwd', '/')
            if cwd != '/':
                parent = cwd.rstrip('/').rsplit('/', 1)[0] or '/'
                state['_cwd'] = parent
                state['_entries'] = None
                state['selected'] = 0
            return True
        return False

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        ch = 18  # ch + 2
        idx = state.get('scroll_y', 0) + ry // ch
        entries = state.get('_entries', [])
        if 0 <= idx < len(entries):
            state['selected'] = idx
            return True
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


class StreamLens:
    """Append-only log view.  Auto-scrolls to bottom."""
    name = 'stream'

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, C.BLACK)

        lines: list[str] = []
        if isinstance(target, list):
            lines = [str(x) for x in target]
        elif isinstance(target, str):
            lines = target.split('\n')

        max_vis = max(1, rect.h // ch)
        max_cols = max(1, rect.w // cw - 1)
        # Auto-scroll to bottom
        start = max(0, len(lines) - max_vis)

        for i in range(max_vis):
            li = start + i
            if li >= len(lines):
                break
            y = rect.y + 2 + i * ch
            vdi.draw_string(rect.x + 4, y, lines[li][:max_cols],
                            C.TEXT_DIM, C.BLACK)

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        return False

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


class TimeLens:
    """Clock display for bar pin or portal."""
    name = 'time'

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, C.PORTAL_BG)

        time_str = time.strftime('%H:%M:%S')
        date_str = time.strftime('%Y-%m-%d')

        # Center the clock text
        tx = rect.x + max(0, (rect.w - len(time_str) * cw)) // 2
        ty = rect.y + max(0, (rect.h - 2 * ch - 4)) // 2
        vdi.draw_string(tx, ty, time_str, C.BAR_CLOCK, BG_TRANSPARENT)

        dx = rect.x + max(0, (rect.w - len(date_str) * cw)) // 2
        vdi.draw_string(dx, ty + ch + 4, date_str, C.TEXT_DIM, BG_TRANSPARENT)

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        return False

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


class InteractiveLens:
    """Calculator / interactive tool lens."""
    name = 'interactive'

    def render(self, vdi: VDI, target: Any, rect: Rect,
               focused: bool, state: dict) -> None:
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        vdi.fill_rect(rect.x, rect.y, rect.w, rect.h, C.PORTAL_BG)

        display = state.setdefault('display', '0')
        state.setdefault('accumulator', 0)
        state.setdefault('operator', '')
        state.setdefault('new_input', True)

        buttons = [
            ['7', '8', '9', '/'],
            ['4', '5', '6', '*'],
            ['1', '2', '3', '-'],
            ['0', 'C', '=', '+'],
        ]
        btn_w = min(44, (rect.w - 20) // 4)
        btn_h = min(32, (rect.h - 50) // 4)

        # Display
        disp_h = 28
        vdi.fill_rect(rect.x + 4, rect.y + 4, rect.w - 8, disp_h, C.BLACK)
        vdi.draw_line(rect.x + 4, rect.y + 4,
                      rect.x + rect.w - 5, rect.y + 4, C.PORTAL_EDGE)
        dtext = display[-(rect.w // cw - 2):]
        tx = rect.x + rect.w - 6 - len(dtext) * cw
        vdi.draw_string(tx, rect.y + 10, dtext, C.TYPE_FN, C.BLACK)

        # Buttons
        by_start = rect.y + disp_h + 8
        for row_idx, row in enumerate(buttons):
            for col_idx, label in enumerate(row):
                bx = rect.x + 4 + col_idx * (btn_w + 3)
                by = by_start + row_idx * (btn_h + 3)
                vdi.fill_rect(bx, by, btn_w, btn_h, C.BUTTON_BG)
                # Border
                for dx in range(btn_w):
                    if 0 <= bx + dx < vdi.width:
                        if 0 <= by < vdi.height:
                            vdi.fb[by * vdi.width + bx + dx] = C.BUTTON_BORDER
                        if 0 <= by + btn_h - 1 < vdi.height:
                            vdi.fb[(by + btn_h - 1) * vdi.width + bx + dx] = C.BUTTON_BORDER
                for dy in range(btn_h):
                    if 0 <= by + dy < vdi.height:
                        if 0 <= bx < vdi.width:
                            vdi.fb[(by + dy) * vdi.width + bx] = C.BUTTON_BORDER
                        if 0 <= bx + btn_w - 1 < vdi.width:
                            vdi.fb[(by + dy) * vdi.width + bx + btn_w - 1] = C.BUTTON_BORDER
                lx = bx + (btn_w - len(label) * cw) // 2
                ly = by + (btn_h - ch) // 2
                vdi.draw_string(lx, ly, label, C.BUTTON_TEXT, BG_TRANSPARENT)

    def on_key(self, target: Any, key: int, mod: int, state: dict) -> bool:
        if 48 <= key <= 57:  # 0-9
            self._press(chr(key), state)
            return True
        if key in (ord('+'), ord('-'), ord('*'), ord('/')):
            self._press(chr(key), state)
            return True
        if key in (ord('='), 13):  # = or Enter
            self._press('=', state)
            return True
        if key == ord('c') or key == ord('C'):
            self._press('C', state)
            return True
        return False

    def _press(self, label: str, state: dict) -> None:
        display = state.get('display', '0')
        new_input = state.get('new_input', True)
        acc = state.get('accumulator', 0)
        op = state.get('operator', '')

        if label.isdigit():
            if new_input:
                state['display'] = label
                state['new_input'] = False
            else:
                state['display'] = label if display == '0' else display + label
        elif label == 'C':
            state['display'] = '0'
            state['accumulator'] = 0
            state['operator'] = ''
            state['new_input'] = True
        elif label == '=':
            self._compute(state)
            state['operator'] = ''
        elif label in '+-*/':
            if op and not new_input:
                self._compute(state)
            state['accumulator'] = int(state.get('display', '0')) \
                if state.get('display', '0').lstrip('-').isdigit() else 0
            state['operator'] = label
            state['new_input'] = True

    def _compute(self, state: dict) -> None:
        try:
            val = int(state.get('display', '0'))
            acc = state.get('accumulator', 0)
            op = state.get('operator', '')
            if op == '+':
                state['display'] = str(acc + val)
            elif op == '-':
                state['display'] = str(acc - val)
            elif op == '*':
                state['display'] = str(acc * val)
            elif op == '/':
                state['display'] = str(acc // val) if val else 'Error'
            state['accumulator'] = int(state['display']) \
                if state['display'].lstrip('-').isdigit() else 0
            state['new_input'] = True
        except Exception:
            state['display'] = 'Error'
            state['new_input'] = True

    def on_click(self, target: Any, rx: int, ry: int, button: int,
                 state: dict) -> bool:
        btn_w = 44
        btn_h = 32
        disp_h = 28
        by_start = disp_h + 8
        buttons = [
            ['7', '8', '9', '/'],
            ['4', '5', '6', '*'],
            ['1', '2', '3', '-'],
            ['0', 'C', '=', '+'],
        ]
        for row_idx, row in enumerate(buttons):
            for col_idx, label in enumerate(row):
                bx = 4 + col_idx * (btn_w + 3)
                by = by_start + row_idx * (btn_h + 3)
                if bx <= rx < bx + btn_w and by <= ry < by + btn_h:
                    self._press(label, state)
                    return True
        return False

    def on_mouse_move(self, target: Any, rx: int, ry: int,
                      state: dict) -> bool:
        return False


# ===================================================================
# Lens Registry
# ===================================================================

_LENS_REGISTRY: dict[str, Lens] = {}


def register_lens(lens: Lens) -> None:
    _LENS_REGISTRY[lens.name] = lens


def get_lens(name: str) -> Lens:
    return _LENS_REGISTRY.get(name, _LENS_REGISTRY.get('inspect'))


# Register built-in lenses
register_lens(InspectLens())
register_lens(PrettyLens())
register_lens(TerminalLens())
register_lens(EditorLens())
register_lens(TreeLens())
register_lens(StreamLens())
register_lens(TimeLens())
register_lens(InteractiveLens())


# ===================================================================
# Crystal — the root of the expression tree
# ===================================================================

class Crystal:
    """Crystal Desktop — the expression tree that IS the interface.

    The screen is a single nested expression tree: Crystal → Pane → Portal.
    The compositor walks the tree, lays out panes, renders portals through
    their lenses, manages focus, and routes input down the focus path.
    """

    def __init__(self, vdi: VDI):
        self.vdi = vdi
        self.vfs = VFS()
        self.vfs.populate_default()
        self.scrapbook = Scrapbook()

        # The expression tree
        self.root: Pane = Pane()
        self._next_pid = 1

        # Bar items
        self.bar_items: list[BarItem] = []
        self.bar_clock = BarClock()
        self._bar_hover: int = -1

        # Focus — path of indices into the tree
        self._focus_path: list[int] = []
        self._focused_portal: Portal | None = None

        # Drag state for dividers
        self._drag_divider: tuple[Pane, str] | None = None  # (pane, axis)
        self._drag_offset: int = 0

        # Float drag
        self._drag_float: Pane | None = None
        self._drag_float_offset: tuple[int, int] = (0, 0)

        # Tick objects
        self._tick_objects: list[Callable] = []

        # State
        self._running = True
        self._dirty = True
        self._last_clock_str = ''

    # ------------------------------------------------------------------
    # Portal creation
    # ------------------------------------------------------------------

    def create_portal(self, target: Any, lens_name: str = 'inspect',
                      label: str = '') -> Portal:
        """Create a new portal to an object."""
        p = Portal(target=target, lens_name=lens_name, label=label,
                   pid=self._next_pid)
        self._next_pid += 1
        # Inject references into lens state
        p.state['_crystal'] = self
        p.state['_vfs'] = self.vfs
        return p

    # ------------------------------------------------------------------
    # Tree construction helpers
    # ------------------------------------------------------------------

    def set_root_portal(self, target: Any, lens_name: str = 'inspect',
                        label: str = '') -> Portal:
        """Set a single portal as the root content."""
        p = self.create_portal(target, lens_name, label)
        self.root = Pane(portal=p)
        self._focus_path = []
        self._focused_portal = p
        self._dirty = True
        return p

    def split_portal(self, portal: Portal, direction: SplitDir,
                     ratio: float = 0.5,
                     new_target: Any = None,
                     new_lens: str = 'inspect',
                     new_label: str = '') -> Portal:
        """Split the pane containing a portal into two side-by-side portals."""
        pane = self._find_pane_for_portal(self.root, portal)
        if pane is None or pane.portal is None:
            return portal

        new_portal = self.create_portal(
            new_target if new_target is not None else portal.target,
            new_lens, new_label)

        old_pane = Pane(portal=portal)
        new_pane = Pane(portal=new_portal)
        pane.portal = None
        pane.split = direction
        pane.ratio = ratio
        pane.children = [old_pane, new_pane]
        self._dirty = True
        return new_portal

    def add_tab(self, portal: Portal, target: Any = None,
                lens_name: str = 'inspect', label: str = '') -> Portal:
        """Add a tab to the pane containing a portal."""
        pane = self._find_pane_for_portal(self.root, portal)
        if pane is None:
            return portal

        new_portal = self.create_portal(
            target if target is not None else portal.target,
            lens_name, label)

        if pane.tab_mode:
            # Already tabbed — add another
            pane.children.append(Pane(portal=new_portal))
            pane.active_tab = len(pane.children) - 1
        else:
            # Convert to tabs
            old_pane = Pane(portal=portal)
            new_pane = Pane(portal=new_portal)
            pane.portal = None
            pane.tab_mode = True
            pane.children = [old_pane, new_pane]
            pane.active_tab = 1

        self._dirty = True
        return new_portal

    def close_portal(self, portal: Portal) -> None:
        """Remove a portal from the tree."""
        parent = self._find_parent_pane(self.root, portal)
        if parent is None:
            # Root portal — clear
            self.root = Pane()
            self._focused_portal = None
            self._dirty = True
            return

        # Find which child contains this portal
        child_idx = -1
        for i, child in enumerate(parent.children):
            if child.portal is portal:
                child_idx = i
                break

        if child_idx >= 0:
            parent.children.pop(child_idx)
            if len(parent.children) == 1:
                # Collapse: single remaining child replaces parent
                remaining = parent.children[0]
                parent.portal = remaining.portal
                parent.split = remaining.split
                parent.ratio = remaining.ratio
                parent.children = remaining.children
                parent.tab_mode = remaining.tab_mode
                parent.active_tab = remaining.active_tab
            elif parent.tab_mode:
                parent.active_tab = min(parent.active_tab,
                                        len(parent.children) - 1)

        # Update focus
        if self._focused_portal is portal:
            all_portals = self.root.all_portals()
            self._focused_portal = all_portals[0] if all_portals else None
        self._dirty = True

    def _find_pane_for_portal(self, pane: Pane, portal: Portal) -> Pane | None:
        if pane.portal is portal:
            return pane
        for child in pane.children:
            result = self._find_pane_for_portal(child, portal)
            if result:
                return result
        return None

    def _find_parent_pane(self, pane: Pane, portal: Portal) -> Pane | None:
        for child in pane.children:
            if child.portal is portal:
                return pane
            result = self._find_parent_pane(child, portal)
            if result:
                return result
        return None

    # ------------------------------------------------------------------
    # Layout — walk tree, compute rects
    # ------------------------------------------------------------------

    def _layout(self) -> None:
        """Compute layout rects for the entire expression tree."""
        work_rect = Rect(0, 0, self.vdi.width, self.vdi.height - BAR_H)
        self._layout_pane(self.root, work_rect)

    def _layout_pane(self, pane: Pane, rect: Rect) -> None:
        """Recursively lay out a pane within a given rect."""
        pane.rect = rect

        if pane.portal:
            pane.portal.rect = rect
            return

        if pane.tab_mode and pane.children:
            # Tab header takes 22px
            tab_h = 22
            for i, child in enumerate(pane.children):
                if i == pane.active_tab:
                    content_rect = Rect(rect.x, rect.y + tab_h,
                                        rect.w, rect.h - tab_h)
                    self._layout_pane(child, content_rect)
                else:
                    # Off-screen tabs still need a rect for focus
                    child.rect = Rect(0, 0, 0, 0)
                    if child.portal:
                        child.portal.rect = Rect(0, 0, 0, 0)
            return

        if pane.split and len(pane.children) == 2:
            if pane.split == SplitDir.VERTICAL:
                r1, r2 = rect.split_v(pane.ratio)
            else:
                r1, r2 = rect.split_h(pane.ratio)
            self._layout_pane(pane.children[0], r1)
            self._layout_pane(pane.children[1], r2)

    # ------------------------------------------------------------------
    # Rendering — walk tree, draw everything
    # ------------------------------------------------------------------

    def redraw(self) -> None:
        """Full desktop redraw."""
        vdi = self.vdi

        # Layout
        self._layout()

        # 1. Canvas — subtle vertical gradient (banded for speed)
        top_c = C.CANVAS
        bot_c = C.CANVAS_END
        tr = (top_c >> 16) & 0xFF; tg = (top_c >> 8) & 0xFF; tb = top_c & 0xFF
        br = (bot_c >> 16) & 0xFF; bg_ = (bot_c >> 8) & 0xFF; bb = bot_c & 0xFF
        h = vdi.height
        band = 16
        for y0 in range(0, h, band):
            bh = min(band, h - y0)
            t = y0 / max(1, h - 1)
            cr = int(tr + (br - tr) * t) & 0xFF
            cg = int(tg + (bg_ - tg) * t) & 0xFF
            cb = int(tb + (bb - tb) * t) & 0xFF
            vdi.fill_rect(0, y0, vdi.width, bh, (cr << 16) | (cg << 8) | cb)

        # 2. Render tree
        self._render_pane(self.root)

        # 3. Float overlays
        self._render_floats(self.root)

        # 4. Crystal bar
        self._draw_bar()

        vdi.present()
        self._dirty = False

    def _render_pane(self, pane: Pane) -> None:
        """Recursively render a pane subtree."""
        vdi = self.vdi
        r = pane.rect

        if r.w <= 0 or r.h <= 0:
            return

        if pane.portal:
            self._render_portal(pane.portal)
            return

        if pane.tab_mode and pane.children:
            # Draw tab headers
            self._draw_tab_headers(pane)
            # Draw active tab content
            if 0 <= pane.active_tab < len(pane.children):
                self._render_pane(pane.children[pane.active_tab])
            return

        if pane.split and len(pane.children) == 2:
            # Draw divider
            if pane.split == SplitDir.VERTICAL:
                dr = pane.rect.divider_v(pane.ratio)
            else:
                dr = pane.rect.divider_h(pane.ratio)
            vdi.fill_rect(dr.x, dr.y, dr.w, dr.h, C.DIVIDER)

            # Draw children
            self._render_pane(pane.children[0])
            self._render_pane(pane.children[1])

    def _render_portal(self, portal: Portal) -> None:
        """Render a single portal through its lens."""
        vdi = self.vdi
        r = portal.rect
        if r.w <= 0 or r.h <= 0:
            return

        is_focused = (portal is self._focused_portal)

        # Portal edge — subtle 1px border
        edge_color = C.FOCUS_GLOW if is_focused else C.PORTAL_EDGE
        # Top edge
        vdi.fill_rect(r.x, r.y, r.w, 1, edge_color)
        # Bottom edge
        vdi.fill_rect(r.x, r.y + r.h - 1, r.w, 1, edge_color)
        # Left edge
        vdi.fill_rect(r.x, r.y, 1, r.h, edge_color)
        # Right edge
        vdi.fill_rect(r.x + r.w - 1, r.y, 1, r.h, edge_color)

        # Focus glow — 2nd pixel ring for focused portal
        if is_focused:
            glow = C.FOCUS_GLOW_DIM
            vdi.fill_rect(r.x + 1, r.y + 1, r.w - 2, 1, glow)
            vdi.fill_rect(r.x + 1, r.y + r.h - 2, r.w - 2, 1, glow)
            vdi.fill_rect(r.x + 1, r.y + 1, 1, r.h - 2, glow)
            vdi.fill_rect(r.x + r.w - 2, r.y + 1, 1, r.h - 2, glow)

        # Portal label strip
        label = portal.label or portal.lens_name
        label_rect = Rect(r.x + PORTAL_PAD, r.y + 1,
                          r.w - 2 * PORTAL_PAD, PORTAL_LABEL_H)
        # Label background — subtle gradient effect via two-tone
        label_bg = 0x111120 if is_focused else 0x0C0C16
        vdi.fill_rect(label_rect.x, label_rect.y,
                      label_rect.w, label_rect.h, label_bg)
        # Bottom edge of label strip
        vdi.fill_rect(label_rect.x, label_rect.y + label_rect.h - 1,
                      label_rect.w, 1, edge_color if is_focused else 0x1A1A2A)
        # Label text
        max_lbl = max(1, (label_rect.w - CLOSE_BTN_W - 8) // vdi.font.char_w)
        label_fg = C.TEXT_BRIGHT if is_focused else C.TEXT_DIM
        ty = label_rect.y + (label_rect.h - vdi.font.char_h) // 2
        vdi.draw_string(label_rect.x + 6, ty,
                        label[:max_lbl], label_fg, BG_TRANSPARENT)
        # Close button [x]
        cx = label_rect.x + label_rect.w - CLOSE_BTN_W - 2
        cy = label_rect.y + (label_rect.h - vdi.font.char_h) // 2
        close_fg = C.TYPE_ERROR if is_focused else 0x444466
        vdi.draw_string(cx + 4, cy, 'x', close_fg, BG_TRANSPARENT)

        # Content rect for lens
        cr = portal.content_rect()
        # Content background — slightly lighter than canvas
        vdi.fill_rect(cr.x, cr.y, cr.w, cr.h, C.PORTAL_BG)
        lens = get_lens(portal.lens_name)
        if lens:
            lens.render(vdi, portal.target, cr, is_focused, portal.state)

    def _draw_tab_headers(self, pane: Pane) -> None:
        """Draw tab buttons at the top of a tabbed pane."""
        vdi = self.vdi
        r = pane.rect
        tab_h = 22
        cw = vdi.font.char_w
        ch = vdi.font.char_h

        # Tab bar background
        vdi.fill_rect(r.x, r.y, r.w, tab_h, C.CANVAS)
        vdi.fill_rect(r.x, r.y + tab_h - 1, r.w, 1, C.PORTAL_EDGE)

        tx = r.x + 2
        for i, child in enumerate(pane.children):
            label = ''
            if child.portal:
                label = child.portal.label or child.portal.lens_name
            elif child.children:
                label = f'pane {i}'
            tab_w = max(60, len(label) * cw + 16)

            is_active = (i == pane.active_tab)
            bg = C.PORTAL_BG if is_active else C.CANVAS
            fg = C.TEXT_BRIGHT if is_active else C.TEXT_DIM
            vdi.fill_rect(tx, r.y + 1, tab_w, tab_h - 2, bg)
            if is_active:
                vdi.fill_rect(tx, r.y + 1, tab_w, 2, C.FOCUS_GLOW)
            vdi.draw_string(tx + 8, r.y + (tab_h - ch) // 2,
                            label[:tab_w // cw - 2], fg, BG_TRANSPARENT)
            tx += tab_w + 2

    def _render_floats(self, pane: Pane) -> None:
        """Render floating panes as overlays."""
        for child in pane.children:
            if child.floating:
                fx, fy = child.float_pos
                fw, fh = child.float_size
                float_rect = Rect(fx, fy, fw, fh)
                # Shadow
                self.vdi.shadow_rect(fx, fy, fw, fh, radius=6, alpha=80)
                self._layout_pane(child, float_rect)
                self._render_pane(child)
            else:
                self._render_floats(child)

    # ------------------------------------------------------------------
    # Crystal Bar
    # ------------------------------------------------------------------

    def _draw_bar(self) -> None:
        """Draw the Crystal Bar at the bottom of the screen."""
        vdi = self.vdi
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        by = vdi.height - BAR_H

        # Bar background
        vdi.fill_rect(0, by, vdi.width, BAR_H, C.BAR_BG)
        # Top edge
        vdi.fill_rect(0, by, vdi.width, 1, C.BAR_EDGE)

        # Launcher items
        bx = 8
        for i, item in enumerate(self.bar_items):
            label = item.label
            item_w = len(label) * cw + 16
            # Hover
            if i == self._bar_hover:
                vdi.fill_rect(bx, by + 2, item_w, BAR_H - 4, C.BAR_HOVER)
            # Icon
            icon = get_icon(item.icon_name) if item.icon_name else None
            if icon:
                icon.draw(vdi, bx + 4, by + (BAR_H - 16) // 2, scale=1)
                vdi.draw_string(bx + 22, by + (BAR_H - ch) // 2,
                                label, C.BAR_TEXT, BG_TRANSPARENT)
            else:
                vdi.draw_string(bx + 4, by + (BAR_H - ch) // 2,
                                label, C.BAR_TEXT, BG_TRANSPARENT)
            bx += item_w + 4

        # Active portals indicator (right of launcher)
        bx += 8
        portals = self.root.all_portals()
        for portal in portals:
            plabel = portal.label or portal.lens_name
            plabel = plabel[:10]
            pw = len(plabel) * cw + 12
            if bx + pw > vdi.width - 80:
                break
            is_focused = (portal is self._focused_portal)
            if is_focused:
                vdi.fill_rect(bx, by + 4, pw, BAR_H - 8, C.SELECTION)
                vdi.fill_rect(bx, by + 2, pw, 2, C.FOCUS_GLOW)
            fg = C.TEXT_BRIGHT if is_focused else C.BAR_TEXT
            vdi.draw_string(bx + 6, by + (BAR_H - ch) // 2,
                            plabel, fg, BG_TRANSPARENT)
            bx += pw + 3

        # Clock (right)
        time_str = time.strftime(self.bar_clock.format_str)
        self.bar_clock.last_str = time_str
        tw = len(time_str) * cw
        vdi.draw_string(vdi.width - tw - 10, by + (BAR_H - ch) // 2,
                        time_str, C.BAR_CLOCK, BG_TRANSPARENT)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def handle_event(self, evt_type: int, data1: int, data2: int) -> None:
        if evt_type == EVT_QUIT:
            self._running = False
            return
        if evt_type == EVT_MOUSE_DOWN:
            mx, my = data1, data2 & 0xFFFF
            button = (data2 >> 16) & 0xFF
            self._on_mouse_down(mx, my, button)
        elif evt_type == EVT_MOUSE_UP:
            self._on_mouse_up(data1, data2 & 0xFFFF)
        elif evt_type == EVT_MOUSE_MOVE:
            self._on_mouse_move(data1, data2)
        elif evt_type == EVT_KEY_DOWN:
            self._on_key_down(data1, data2)

    def _on_mouse_down(self, mx: int, my: int, button: int) -> None:
        # Bar click?
        if my >= self.vdi.height - BAR_H:
            self._bar_click(mx, my, button)
            return

        # Divider hit?
        divider = self._divider_hit(self.root, mx, my)
        if divider:
            pane, axis = divider
            if axis == 'v':
                self._drag_offset = mx - int(pane.rect.x + pane.rect.w * pane.ratio)
            else:
                self._drag_offset = my - int(pane.rect.y + pane.rect.h * pane.ratio)
            self._drag_divider = (pane, axis)
            return

        # Tab header click?
        tab_pane = self._tab_header_hit(self.root, mx, my)
        if tab_pane:
            pane, tab_idx = tab_pane
            pane.active_tab = tab_idx
            # Focus the portal in that tab
            if 0 <= tab_idx < len(pane.children):
                portals = pane.children[tab_idx].all_portals()
                if portals:
                    self._focused_portal = portals[0]
            self._dirty = True
            return

        # Portal hit — find deepest portal at this position
        portal = self._portal_at(self.root, mx, my)
        if portal:
            if portal is not self._focused_portal:
                self._focused_portal = portal
                self._dirty = True
            # Close button hit?
            lr = Rect(portal.rect.x + PORTAL_PAD, portal.rect.y + 1,
                      portal.rect.w - 2 * PORTAL_PAD, PORTAL_LABEL_H)
            close_x = lr.x + lr.w - CLOSE_BTN_W - 2
            if (lr.y <= my < lr.y + lr.h and close_x <= mx < close_x + CLOSE_BTN_W):
                self.close_portal(portal)
                return
            # Dispatch click to lens
            cr = portal.content_rect()
            if cr.contains(mx, my):
                rx, ry = mx - cr.x, my - cr.y
                lens = get_lens(portal.lens_name)
                if lens and lens.on_click(portal.target, rx, ry, button,
                                          portal.state):
                    self._dirty = True
            return

        # Click on empty canvas
        self._dirty = True

    def _on_mouse_up(self, mx: int, my: int) -> None:
        if self._drag_divider:
            self._drag_divider = None
            self._dirty = True
        if self._drag_float:
            self._drag_float = None
            self._dirty = True

    def _on_mouse_move(self, mx: int, my: int) -> None:
        # Divider drag
        if self._drag_divider:
            pane, axis = self._drag_divider
            r = pane.rect
            if axis == 'v' and r.w > 0:
                new_ratio = _clamp(mx - r.x - self._drag_offset, 50, r.w - 50) / r.w
                pane.ratio = new_ratio
            elif axis == 'h' and r.h > 0:
                new_ratio = _clamp(my - r.y - self._drag_offset, 50, r.h - 50) / r.h
                pane.ratio = new_ratio
            self._dirty = True
            return

        # Bar hover
        old_hover = self._bar_hover
        self._bar_hover = self._bar_item_at(mx, my)
        if self._bar_hover != old_hover:
            self._dirty = True

        # Update cursor
        self.vdi.set_cursor(mx, my, True)

    def _on_key_down(self, key: int, mod: int) -> None:
        import pygame
        # Global hotkeys
        ctrl = mod & pygame.KMOD_CTRL
        alt = mod & pygame.KMOD_ALT

        # Super-Tab: cycle focus between portals
        if key == pygame.K_TAB and ctrl:
            self._cycle_focus(1 if not (mod & pygame.KMOD_SHIFT) else -1)
            self._dirty = True
            return

        # Ctrl-W: close focused portal
        if key == ord('w') and ctrl:
            if self._focused_portal:
                self.close_portal(self._focused_portal)
            return

        # Ctrl-\: split vertical
        if key == ord('\\') and ctrl:
            if self._focused_portal:
                self.split_portal(self._focused_portal, SplitDir.VERTICAL)
            return

        # Ctrl-/: split horizontal
        if key == ord('/') and ctrl:
            if self._focused_portal:
                self.split_portal(self._focused_portal, SplitDir.HORIZONTAL,
                                  new_lens='terminal',
                                  new_label='Terminal')
            return

        # Dispatch to focused portal's lens
        if self._focused_portal:
            lens = get_lens(self._focused_portal.lens_name)
            if lens and lens.on_key(self._focused_portal.target, key, mod,
                                     self._focused_portal.state):
                # Check for quit signal from terminal
                if self._focused_portal.state.get('_quit'):
                    self._running = False
                    return
                self._dirty = True
                return

    def _cycle_focus(self, direction: int = 1) -> None:
        """Cycle focus to next/prev portal in tree order."""
        portals = self.root.all_portals()
        if not portals:
            return
        if self._focused_portal in portals:
            idx = portals.index(self._focused_portal)
            idx = (idx + direction) % len(portals)
        else:
            idx = 0
        self._focused_portal = portals[idx]

    # ------------------------------------------------------------------
    # Hit testing
    # ------------------------------------------------------------------

    def _portal_at(self, pane: Pane, mx: int, my: int) -> Portal | None:
        """Find the portal at screen position (mx, my)."""
        if pane.portal and pane.rect.contains(mx, my):
            return pane.portal

        if pane.tab_mode and pane.children:
            if 0 <= pane.active_tab < len(pane.children):
                return self._portal_at(pane.children[pane.active_tab], mx, my)
            return None

        for child in pane.children:
            if child.rect.contains(mx, my):
                result = self._portal_at(child, mx, my)
                if result:
                    return result
        return None

    def _divider_hit(self, pane: Pane, mx: int, my: int) -> tuple[Pane, str] | None:
        """Check if (mx, my) hits a pane divider.  Returns (pane, 'v'|'h')."""
        if pane.split and len(pane.children) == 2:
            r = pane.rect
            if pane.split == SplitDir.VERTICAL:
                dr = r.divider_v(pane.ratio)
                # Expand hit area slightly
                hit = Rect(dr.x - 3, dr.y, dr.w + 6, dr.h)
                if hit.contains(mx, my):
                    return (pane, 'v')
            else:
                dr = r.divider_h(pane.ratio)
                hit = Rect(dr.x, dr.y - 3, dr.w, dr.h + 6)
                if hit.contains(mx, my):
                    return (pane, 'h')
            # Check children
            for child in pane.children:
                result = self._divider_hit(child, mx, my)
                if result:
                    return result
        elif not pane.is_leaf():
            for child in pane.children:
                result = self._divider_hit(child, mx, my)
                if result:
                    return result
        return None

    def _tab_header_hit(self, pane: Pane, mx: int, my: int) -> tuple[Pane, int] | None:
        """Check if we hit a tab header.  Returns (pane, tab_index)."""
        if pane.tab_mode and pane.children:
            r = pane.rect
            tab_h = 22
            if r.y <= my < r.y + tab_h and r.x <= mx < r.x + r.w:
                cw = self.vdi.font.char_w
                tx = r.x + 2
                for i, child in enumerate(pane.children):
                    label = ''
                    if child.portal:
                        label = child.portal.label or child.portal.lens_name
                    tab_w = max(60, len(label) * cw + 16)
                    if tx <= mx < tx + tab_w:
                        return (pane, i)
                    tx += tab_w + 2
            # Check active tab children
            if 0 <= pane.active_tab < len(pane.children):
                result = self._tab_header_hit(
                    pane.children[pane.active_tab], mx, my)
                if result:
                    return result
        elif not pane.is_leaf():
            for child in pane.children:
                result = self._tab_header_hit(child, mx, my)
                if result:
                    return result
        return None

    def _bar_click(self, mx: int, my: int, button: int) -> None:
        """Handle click on the crystal bar."""
        idx = self._bar_item_at(mx, my)
        if idx >= 0 and idx < len(self.bar_items):
            item = self.bar_items[idx]
            if item.action:
                item.action()
                self._dirty = True
            return

        # Click on portal indicator in bar?
        cw = self.vdi.font.char_w
        bx = 8
        for item in self.bar_items:
            bx += len(item.label) * cw + 20
        bx += 8
        portals = self.root.all_portals()
        for portal in portals:
            plabel = (portal.label or portal.lens_name)[:10]
            pw = len(plabel) * cw + 12
            if bx + pw > self.vdi.width - 80:
                break
            if bx <= mx < bx + pw:
                self._focused_portal = portal
                self._dirty = True
                return
            bx += pw + 3

    def _bar_item_at(self, mx: int, my: int) -> int:
        """Return bar item index at mouse position, or -1."""
        if my < self.vdi.height - BAR_H:
            return -1
        cw = self.vdi.font.char_w
        bx = 8
        for i, item in enumerate(self.bar_items):
            item_w = len(item.label) * cw + 16
            if bx <= mx < bx + item_w:
                return i
            bx += item_w + 4
        return -1

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, fps: int = 30) -> None:
        """Run the Crystal Desktop event loop."""
        import pygame
        clock = pygame.time.Clock()

        while self._running:
            # Process events
            evt_type, d1, d2 = self.vdi.read_event()
            while evt_type != EVT_NONE:
                self.handle_event(evt_type, d1, d2)
                evt_type, d1, d2 = self.vdi.read_event()

            # Tick objects
            for ticker in self._tick_objects:
                ticker()

            # Clock update
            new_time = time.strftime(self.bar_clock.format_str)
            if new_time != self._last_clock_str:
                self._last_clock_str = new_time
                self._dirty = True

            # Terminal cursor blink
            if self._focused_portal and self._focused_portal.lens_name == 'terminal':
                self._dirty = True  # Blink needs redraw

            if self._dirty:
                self.redraw()

            clock.tick(fps)

        self.vdi.close()


# ===================================================================
# Default crystal layout builder
# ===================================================================

def _build_default_crystal(crystal: Crystal) -> None:
    """Build the default Crystal Desktop layout.

    Creates the canonical initial layout:
      - Left: VFS tree browser
      - Right top: Terminal REPL
      - Right bottom: Inspector (system info)
      - Bar: launcher items, clock

    This IS the expression tree:

    (crystal
      (bar :dock bottom
        (launcher :items (Terminal Files Editor Calculator Inspector))
        (clock :format "%H:%M"))
      (pane :split vertical :ratio 0.25
        (portal vfs :lens tree :label "Files")
        (pane :split horizontal :ratio 0.65
          (portal repl :lens terminal :label "Terminal")
          (portal system :lens inspect :label "System"))))
    """

    # Create the root split: file tree | main area
    tree_portal = crystal.create_portal(
        crystal.vfs, lens_name='tree', label='Files')
    tree_portal.state['_cwd'] = '/'
    tree_portal.state['_entries'] = None
    tree_portal.state['_crystal'] = crystal

    terminal_portal = crystal.create_portal(
        None, lens_name='terminal', label='Terminal')
    terminal_portal.state['_cwd'] = '/'

    system_info = {
        'type': 'Crystal Desktop',
        'version': '3.0',
        'resolution': f'{crystal.vdi.width}x{crystal.vdi.height}',
        'architecture': 'LM-1 List Machine',
        'runtime': 'Python Emulator',
        'portals': 'expression tree compositor',
        'motto': 'The desktop IS a living expression.',
    }
    info_portal = crystal.create_portal(
        system_info, lens_name='inspect', label='System')

    # Build tree: vertical split 0.25 (tree | horizontal split 0.65 (terminal | info))
    tree_pane = Pane(portal=tree_portal)
    term_pane = Pane(portal=terminal_portal)
    info_pane = Pane(portal=info_portal)

    right_pane = Pane(split=SplitDir.HORIZONTAL, ratio=0.65,
                      children=[term_pane, info_pane])
    root_pane = Pane(split=SplitDir.VERTICAL, ratio=0.25,
                     children=[tree_pane, right_pane])

    crystal.root = root_pane
    crystal._focused_portal = terminal_portal

    # Bar launcher items — add as tabs next to focused portal
    def _open_terminal():
        if crystal._focused_portal:
            np = crystal.add_tab(crystal._focused_portal,
                                 target=None, lens_name='terminal',
                                 label='Terminal')
            np.state['_cwd'] = '/'
            crystal._focused_portal = np
            crystal._dirty = True

    def _open_files():
        if crystal._focused_portal:
            np = crystal.add_tab(crystal._focused_portal,
                                 target=crystal.vfs, lens_name='tree',
                                 label='Files')
            np.state['_cwd'] = '/'
            np.state['_entries'] = None
            crystal._focused_portal = np
            crystal._dirty = True

    def _open_editor():
        text = '(defun hello ()\n  (print "Hello, Crystal!"))\n\n(hello)\n'
        if crystal._focused_portal:
            np = crystal.add_tab(crystal._focused_portal,
                                 target=text, lens_name='editor',
                                 label='*scratch*')
            np.state['is_lisp'] = True
            crystal._focused_portal = np
            crystal._dirty = True

    def _open_calculator():
        if crystal._focused_portal:
            np = crystal.add_tab(crystal._focused_portal,
                                 target=None, lens_name='interactive',
                                 label='Calculator')
            crystal._focused_portal = np
            crystal._dirty = True

    def _open_inspector():
        info = {
            'type': 'Crystal Desktop',
            'version': '3.0',
            'resolution': f'{crystal.vdi.width}x{crystal.vdi.height}',
            'portals': len(crystal.root.all_portals()),
        }
        if crystal._focused_portal:
            np = crystal.add_tab(crystal._focused_portal,
                                 target=info, lens_name='inspect',
                                 label='Inspector')
            crystal._focused_portal = np
            crystal._dirty = True

    crystal.bar_items = [
        BarItem(label='Terminal', icon_name='terminal', action=_open_terminal),
        BarItem(label='Files', icon_name='file_manager', action=_open_files),
        BarItem(label='Editor', icon_name='editor', action=_open_editor),
        BarItem(label='Calc', icon_name='calculator', action=_open_calculator),
        BarItem(label='Inspect', icon_name='inspector', action=_open_inspector),
    ]


# ===================================================================
# Desktop launcher — entry point
# ===================================================================

def launch_crystal(width: int = 1024, height: int = 768,
                   scale: int = 1) -> None:
    """Launch the Crystal Desktop.

    The screen is a living expression tree.
    """
    vdi = VDI(width=width, height=height, headless=False, scale=scale)
    crystal = Crystal(vdi)
    _build_default_crystal(crystal)
    crystal.run()


# Legacy alias for compatibility
launch_desktop = launch_crystal
