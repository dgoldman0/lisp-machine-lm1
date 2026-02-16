"""LM-1 VDI (Virtual Device Interface) Display Engine.

Provides a framebuffer-backed graphics system inspired by GEM VDI.
Features:
  - 8-bit indexed-color framebuffer with 256-entry CLUT
  - Drawing primitives: rect fill, bitblt, line, character/string
  - Hardware cursor overlay
  - Host display via pygame (optional headless mode for testing)
  - Event injection: keyboard/mouse from host → emulator

VDI functions are invoked via TRAP 0x83 with:
  r1 = function code (fixnum)
  r2..r8 = arguments (fixnums)
  Return values in r1 (and sometimes r2, r3)

Function codes:
  0  VDI_SET_MODE     r2=width, r3=height  → set resolution
  1  VDI_FILL_RECT    r2=x, r3=y, r4=w, r5=h, r6=color_index
  2  VDI_BLIT         r2=src_x, r3=src_y, r4=dst_x, r5=dst_y, r6=w, r7=h
  3  VDI_SET_PALETTE  r2=index, r3=r, r4=g, r5=b
  4  VDI_DRAW_CHAR    r2=x, r3=y, r4=char_code, r5=fg, r6=bg
  5  VDI_DRAW_STRING  r2=x, r3=y, r4=str_addr, r5=len, r6=fg, r7=bg
  6  VDI_SET_CURSOR   r2=x, r3=y, r4=visible(0/1)
  7  VDI_READ_PIXEL   r2=x, r3=y  → r1=color_index
  8  VDI_DRAW_LINE    r2=x1, r3=y1, r4=x2, r5=y2, r6=color
  9  VDI_GET_MODE     → r1=width, r2=height
  10 VDI_SCROLL       r2=x, r3=y, r4=w, r5=h, r6=dx, r7=dy
  11 VDI_PRESENT      Force display update (no-op in headless mode)
  12 VDI_READ_EVENT   → r1=event_type, r2=data1, r3=data2
"""

from __future__ import annotations

import array
from typing import Optional


# VDI function codes
VDI_SET_MODE     = 0
VDI_FILL_RECT    = 1
VDI_BLIT         = 2
VDI_SET_PALETTE  = 3
VDI_DRAW_CHAR    = 4
VDI_DRAW_STRING  = 5
VDI_SET_CURSOR   = 6
VDI_READ_PIXEL   = 7
VDI_DRAW_LINE    = 8
VDI_GET_MODE     = 9
VDI_SCROLL       = 10
VDI_PRESENT      = 11
VDI_READ_EVENT   = 12

# Event types
EVT_NONE         = 0
EVT_KEY_DOWN     = 1
EVT_KEY_UP       = 2
EVT_MOUSE_MOVE   = 3
EVT_MOUSE_DOWN   = 4
EVT_MOUSE_UP     = 5
EVT_QUIT         = 6
EVT_TIMER        = 7

# Default palette: CGA-inspired 16 colors + 240 grayscale ramp
_DEFAULT_PALETTE = [
    (0x00, 0x00, 0x00),  # 0  black
    (0x00, 0x00, 0xAA),  # 1  dark blue
    (0x00, 0xAA, 0x00),  # 2  dark green
    (0x00, 0xAA, 0xAA),  # 3  dark cyan
    (0xAA, 0x00, 0x00),  # 4  dark red
    (0xAA, 0x00, 0xAA),  # 5  dark magenta
    (0xAA, 0x55, 0x00),  # 6  brown
    (0xAA, 0xAA, 0xAA),  # 7  light gray
    (0x55, 0x55, 0x55),  # 8  dark gray
    (0x55, 0x55, 0xFF),  # 9  blue
    (0x55, 0xFF, 0x55),  # 10 green
    (0x55, 0xFF, 0xFF),  # 11 cyan
    (0xFF, 0x55, 0x55),  # 12 red
    (0xFF, 0x55, 0xFF),  # 13 magenta
    (0xFF, 0xFF, 0x55),  # 14 yellow
    (0xFF, 0xFF, 0xFF),  # 15 white
]
# Fill remaining 240 indices with grayscale ramp
for i in range(240):
    v = (i * 255) // 239
    _DEFAULT_PALETTE.append((v, v, v))


# Built-in 8×16 bitmap font (CP437-style, first 128 ASCII chars)
# Each character is 8 pixels wide, 16 pixels tall = 16 bytes
# Stored as list of 128 entries, each a tuple of 16 bytes (row bitmaps)
# For space and brevity, we generate a minimal font with box-drawing approach
def _make_minimal_font() -> list[bytes]:
    """Generate a minimal 8x16 bitmap font for printable ASCII."""
    font = []
    for ch in range(128):
        if 33 <= ch <= 126:
            # Render a crude blocky glyph — each ASCII char gets a
            # recognizable shape. For a real system we'd load a font file.
            rows = _render_ascii_glyph(ch)
        else:
            rows = bytes(16)  # blank for non-printable
        font.append(rows)
    return font


def _g(data: list[int]) -> bytes:
    """Pad an 8-row glyph to 16 rows, centered vertically.

    3 rows top padding + 8 rows glyph + 5 rows bottom padding.
    """
    top_pad = 3
    return bytes([0x00] * top_pad + data +
                 [0x00] * (16 - top_pad - len(data)))


def _gd(data: list[int]) -> bytes:
    """Build 16-row glyph for chars with descenders (g, j, p, q, y).

    3 rows top padding + up to 10 rows of body (including descender).
    """
    top_pad = 3
    return bytes([0x00] * top_pad + data +
                 [0x00] * (16 - top_pad - len(data)))


def _render_ascii_glyph(ch: int) -> bytes:
    """Render a single ASCII character as 8x16 bitmap.

    Proper 16-row glyphs: design in top 8-10 rows, blank padding below.
    Descender characters (g, j, p, q, y) extend into rows 8-9.
    """
    _GLYPH_DATA = {
        # Digits — 8 rows of design, padded to 16
        ord('0'): _g([0x00,0x3C,0x66,0x6E,0x76,0x66,0x3C,0x00]),
        ord('1'): _g([0x00,0x18,0x38,0x18,0x18,0x18,0x7E,0x00]),
        ord('2'): _g([0x00,0x3C,0x66,0x0C,0x18,0x30,0x7E,0x00]),
        ord('3'): _g([0x00,0x3C,0x66,0x1C,0x06,0x66,0x3C,0x00]),
        ord('4'): _g([0x00,0x0C,0x1C,0x2C,0x4C,0x7E,0x0C,0x00]),
        ord('5'): _g([0x00,0x7E,0x60,0x7C,0x06,0x66,0x3C,0x00]),
        ord('6'): _g([0x00,0x3C,0x60,0x7C,0x66,0x66,0x3C,0x00]),
        ord('7'): _g([0x00,0x7E,0x06,0x0C,0x18,0x18,0x18,0x00]),
        ord('8'): _g([0x00,0x3C,0x66,0x3C,0x66,0x66,0x3C,0x00]),
        ord('9'): _g([0x00,0x3C,0x66,0x3E,0x06,0x0C,0x38,0x00]),
        # Uppercase — 8 rows of design, padded to 16
        ord('A'): _g([0x00,0x18,0x3C,0x66,0x7E,0x66,0x66,0x00]),
        ord('B'): _g([0x00,0x7C,0x66,0x7C,0x66,0x66,0x7C,0x00]),
        ord('C'): _g([0x00,0x3C,0x66,0x60,0x60,0x66,0x3C,0x00]),
        ord('D'): _g([0x00,0x78,0x6C,0x66,0x66,0x6C,0x78,0x00]),
        ord('E'): _g([0x00,0x7E,0x60,0x7C,0x60,0x60,0x7E,0x00]),
        ord('F'): _g([0x00,0x7E,0x60,0x7C,0x60,0x60,0x60,0x00]),
        ord('G'): _g([0x00,0x3C,0x66,0x60,0x6E,0x66,0x3E,0x00]),
        ord('H'): _g([0x00,0x66,0x66,0x7E,0x66,0x66,0x66,0x00]),
        ord('I'): _g([0x00,0x3C,0x18,0x18,0x18,0x18,0x3C,0x00]),
        ord('J'): _g([0x00,0x1E,0x0C,0x0C,0x0C,0x6C,0x38,0x00]),
        ord('K'): _g([0x00,0x66,0x6C,0x78,0x78,0x6C,0x66,0x00]),
        ord('L'): _g([0x00,0x60,0x60,0x60,0x60,0x60,0x7E,0x00]),
        ord('M'): _g([0x00,0x63,0x77,0x7F,0x6B,0x63,0x63,0x00]),
        ord('N'): _g([0x00,0x66,0x76,0x7E,0x7E,0x6E,0x66,0x00]),
        ord('O'): _g([0x00,0x3C,0x66,0x66,0x66,0x66,0x3C,0x00]),
        ord('P'): _g([0x00,0x7C,0x66,0x66,0x7C,0x60,0x60,0x00]),
        ord('Q'): _g([0x00,0x3C,0x66,0x66,0x6E,0x3C,0x0E,0x00]),
        ord('R'): _g([0x00,0x7C,0x66,0x66,0x7C,0x6C,0x66,0x00]),
        ord('S'): _g([0x00,0x3C,0x60,0x3C,0x06,0x66,0x3C,0x00]),
        ord('T'): _g([0x00,0x7E,0x18,0x18,0x18,0x18,0x18,0x00]),
        ord('U'): _g([0x00,0x66,0x66,0x66,0x66,0x66,0x3C,0x00]),
        ord('V'): _g([0x00,0x66,0x66,0x66,0x66,0x3C,0x18,0x00]),
        ord('W'): _g([0x00,0x63,0x63,0x6B,0x7F,0x77,0x63,0x00]),
        ord('X'): _g([0x00,0x66,0x3C,0x18,0x3C,0x66,0x66,0x00]),
        ord('Y'): _g([0x00,0x66,0x66,0x3C,0x18,0x18,0x18,0x00]),
        ord('Z'): _g([0x00,0x7E,0x0C,0x18,0x30,0x60,0x7E,0x00]),
        # Lowercase — baseline at row 2, x-height ~5 rows
        ord('a'): _g([0x00,0x00,0x00,0x3C,0x06,0x3E,0x66,0x3E]),
        ord('b'): _g([0x00,0x60,0x60,0x7C,0x66,0x66,0x7C,0x00]),
        ord('c'): _g([0x00,0x00,0x00,0x3C,0x66,0x60,0x66,0x3C]),
        ord('d'): _g([0x00,0x06,0x06,0x3E,0x66,0x66,0x3E,0x00]),
        ord('e'): _g([0x00,0x00,0x00,0x3C,0x66,0x7E,0x60,0x3C]),
        ord('f'): _g([0x00,0x1C,0x30,0x7C,0x30,0x30,0x30,0x00]),
        ord('g'): _gd([0x00,0x00,0x00,0x3E,0x66,0x66,0x3E,0x06,0x3C,0x00]),
        ord('h'): _g([0x00,0x60,0x60,0x7C,0x66,0x66,0x66,0x00]),
        ord('i'): _g([0x00,0x18,0x00,0x38,0x18,0x18,0x3C,0x00]),
        ord('j'): _gd([0x00,0x0C,0x00,0x0C,0x0C,0x0C,0x0C,0x6C,0x38,0x00]),
        ord('k'): _g([0x00,0x60,0x60,0x66,0x6C,0x78,0x6C,0x66]),
        ord('l'): _g([0x00,0x38,0x18,0x18,0x18,0x18,0x3C,0x00]),
        ord('m'): _g([0x00,0x00,0x00,0x66,0x7F,0x7F,0x6B,0x63]),
        ord('n'): _g([0x00,0x00,0x00,0x7C,0x66,0x66,0x66,0x00]),
        ord('o'): _g([0x00,0x00,0x00,0x3C,0x66,0x66,0x3C,0x00]),
        ord('p'): _gd([0x00,0x00,0x00,0x7C,0x66,0x66,0x7C,0x60,0x60,0x00]),
        ord('q'): _gd([0x00,0x00,0x00,0x3E,0x66,0x66,0x3E,0x06,0x06,0x00]),
        ord('r'): _g([0x00,0x00,0x00,0x7C,0x66,0x60,0x60,0x00]),
        ord('s'): _g([0x00,0x00,0x00,0x3E,0x60,0x3C,0x06,0x7C]),
        ord('t'): _g([0x00,0x30,0x30,0x7C,0x30,0x30,0x1C,0x00]),
        ord('u'): _g([0x00,0x00,0x00,0x66,0x66,0x66,0x3E,0x00]),
        ord('v'): _g([0x00,0x00,0x00,0x66,0x66,0x3C,0x18,0x00]),
        ord('w'): _g([0x00,0x00,0x00,0x63,0x6B,0x7F,0x36,0x00]),
        ord('x'): _g([0x00,0x00,0x00,0x66,0x3C,0x3C,0x66,0x00]),
        ord('y'): _gd([0x00,0x00,0x00,0x66,0x66,0x66,0x3E,0x06,0x3C,0x00]),
        ord('z'): _g([0x00,0x00,0x00,0x7E,0x0C,0x18,0x30,0x7E]),
        # Symbols
        ord('!'): _g([0x00,0x18,0x18,0x18,0x18,0x00,0x18,0x00]),
        ord('"'): _g([0x00,0x66,0x66,0x24,0x00,0x00,0x00,0x00]),
        ord('#'): _g([0x00,0x24,0x7E,0x24,0x24,0x7E,0x24,0x00]),
        ord('$'): _g([0x18,0x3E,0x60,0x3C,0x06,0x7C,0x18,0x00]),
        ord('%'): _g([0x00,0x62,0x66,0x0C,0x18,0x66,0x46,0x00]),
        ord('&'): _g([0x00,0x38,0x6C,0x38,0x76,0xCC,0x76,0x00]),
        ord("'"): _g([0x00,0x18,0x18,0x30,0x00,0x00,0x00,0x00]),
        ord('('): _g([0x00,0x0C,0x18,0x30,0x30,0x18,0x0C,0x00]),
        ord(')'): _g([0x00,0x30,0x18,0x0C,0x0C,0x18,0x30,0x00]),
        ord('*'): _g([0x00,0x66,0x3C,0xFF,0x3C,0x66,0x00,0x00]),
        ord('+'): _g([0x00,0x00,0x18,0x18,0x7E,0x18,0x18,0x00]),
        ord(','): _gd([0x00,0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x30,0x00]),
        ord('-'): _g([0x00,0x00,0x00,0x00,0x7E,0x00,0x00,0x00]),
        ord('.'): _g([0x00,0x00,0x00,0x00,0x00,0x00,0x18,0x00]),
        ord('/'): _g([0x00,0x02,0x06,0x0C,0x18,0x30,0x60,0x00]),
        ord(':'): _g([0x00,0x00,0x18,0x18,0x00,0x18,0x18,0x00]),
        ord(';'): _gd([0x00,0x00,0x00,0x18,0x18,0x00,0x18,0x18,0x30,0x00]),
        ord('<'): _g([0x00,0x06,0x0C,0x18,0x30,0x18,0x0C,0x06]),
        ord('='): _g([0x00,0x00,0x00,0x7E,0x00,0x7E,0x00,0x00]),
        ord('>'): _g([0x00,0x60,0x30,0x18,0x0C,0x18,0x30,0x60]),
        ord('?'): _g([0x00,0x3C,0x66,0x06,0x0C,0x18,0x00,0x18]),
        ord('@'): _g([0x00,0x3C,0x66,0x6E,0x6E,0x60,0x3C,0x00]),
        ord('['): _g([0x00,0x3C,0x30,0x30,0x30,0x30,0x3C,0x00]),
        ord('\\'): _g([0x00,0x40,0x60,0x30,0x18,0x0C,0x06,0x02]),
        ord(']'): _g([0x00,0x3C,0x0C,0x0C,0x0C,0x0C,0x3C,0x00]),
        ord('^'): _g([0x00,0x18,0x3C,0x66,0x00,0x00,0x00,0x00]),
        ord('_'): bytes([0x00]*14 + [0x7E, 0x00]),
        ord('`'): _g([0x00,0x18,0x18,0x0C,0x00,0x00,0x00,0x00]),
        ord('{'): _g([0x00,0x0E,0x18,0x18,0x70,0x18,0x18,0x0E]),
        ord('|'): _g([0x18,0x18,0x18,0x18,0x18,0x18,0x18,0x18]),
        ord('}'): _g([0x00,0x70,0x18,0x18,0x0E,0x18,0x18,0x70]),
        ord('~'): _g([0x00,0x76,0xDC,0x00,0x00,0x00,0x00,0x00]),
    }
    if ch in _GLYPH_DATA:
        return _GLYPH_DATA[ch]
    # Fallback: filled rectangle
    return bytes([0x00, 0x00, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E,
                  0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x00, 0x00, 0x00])


# Global font (lazily initialized)
_FONT: Optional[list[bytes]] = None

def _get_font() -> list[bytes]:
    global _FONT
    if _FONT is None:
        _FONT = _make_minimal_font()
    return _FONT


CHAR_W = 8
CHAR_H = 16


class VDI:
    """Virtual Device Interface — framebuffer graphics engine.

    Can operate in headless mode (for testing) or with a pygame
    display window (for interactive use).
    """

    def __init__(self, width: int = 640, height: int = 480,
                 headless: bool = True, scale: int = 1):
        self.width = width
        self.height = height
        self.scale = scale
        self.headless = headless

        # 8-bit indexed framebuffer (row-major)
        self.fb = bytearray(width * height)

        # 256-entry palette: [(r, g, b), ...]
        self.palette = list(_DEFAULT_PALETTE)

        # Hardware cursor
        self.cursor_x = 0
        self.cursor_y = 0
        self.cursor_visible = False

        # Event queue (keyboard/mouse events from host)
        self._events: list[tuple[int, int, int]] = []  # (type, data1, data2)

        # Pygame surface (only if not headless)
        self._screen = None
        self._surface = None
        self._dirty = True

        if not headless:
            self._init_display()

    def _init_display(self) -> None:
        """Initialize pygame display."""
        import pygame
        pygame.init()
        pygame.display.set_caption("LM-1 Crystal Desktop")
        self._screen = pygame.display.set_mode(
            (self.width * self.scale, self.height * self.scale))
        self._surface = pygame.Surface((self.width, self.height))

    def close(self) -> None:
        """Shut down display."""
        if self._screen is not None:
            import pygame
            pygame.quit()
            self._screen = None

    # ------------------------------------------------------------------
    # Drawing primitives
    # ------------------------------------------------------------------

    def set_mode(self, width: int, height: int) -> None:
        """Reinitialize framebuffer at new resolution."""
        self.width = width
        self.height = height
        self.fb = bytearray(width * height)
        self._dirty = True
        if self._screen is not None:
            import pygame
            self._screen = pygame.display.set_mode(
                (width * self.scale, height * self.scale))
            self._surface = pygame.Surface((width, height))

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        """Fill a rectangle with a palette index."""
        color = color & 0xFF
        # Clip
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.width, x + w)
        y1 = min(self.height, y + h)
        for row in range(y0, y1):
            start = row * self.width + x0
            end = row * self.width + x1
            self.fb[start:end] = bytes([color]) * (x1 - x0)
        self._dirty = True

    def read_pixel(self, x: int, y: int) -> int:
        """Read a single pixel's color index."""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.fb[y * self.width + x]
        return 0

    def draw_line(self, x1: int, y1: int, x2: int, y2: int, color: int) -> None:
        """Draw a line using Bresenham's algorithm."""
        color = color & 0xFF
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy

        while True:
            if 0 <= x1 < self.width and 0 <= y1 < self.height:
                self.fb[y1 * self.width + x1] = color
            if x1 == x2 and y1 == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x1 += sx
            if e2 < dx:
                err += dx
                y1 += sy
        self._dirty = True

    def blit(self, src_x: int, src_y: int, dst_x: int, dst_y: int,
             w: int, h: int) -> None:
        """Copy a rectangular region within the framebuffer."""
        # Handle overlapping regions with direction-aware copy
        if dst_y < src_y or (dst_y == src_y and dst_x < src_x):
            rows = range(h)
        else:
            rows = range(h - 1, -1, -1)
        for row in rows:
            sy = src_y + row
            dy = dst_y + row
            if 0 <= sy < self.height and 0 <= dy < self.height:
                s_start = max(0, src_x)
                s_end = min(self.width, src_x + w)
                d_start = max(0, dst_x)
                d_end = min(self.width, dst_x + w)
                length = min(s_end - s_start, d_end - d_start)
                if length > 0:
                    src_off = sy * self.width + s_start
                    dst_off = dy * self.width + d_start
                    self.fb[dst_off:dst_off + length] = self.fb[src_off:src_off + length]
        self._dirty = True

    def scroll(self, x: int, y: int, w: int, h: int,
               dx: int, dy: int) -> None:
        """Scroll a rectangular region by (dx, dy) pixels."""
        self.blit(x, y, x + dx, y + dy, w, h)
        # Clear exposed area
        if dy > 0:
            self.fill_rect(x, y, w, min(dy, h), 0)
        elif dy < 0:
            self.fill_rect(x, max(y, y + h + dy), w, min(-dy, h), 0)
        if dx > 0:
            self.fill_rect(x, y, min(dx, w), h, 0)
        elif dx < 0:
            self.fill_rect(max(x, x + w + dx), y, min(-dx, w), h, 0)

    def set_palette_entry(self, index: int, r: int, g: int, b: int) -> None:
        """Set a single palette entry."""
        if 0 <= index < 256:
            self.palette[index] = (r & 0xFF, g & 0xFF, b & 0xFF)
            self._dirty = True

    def draw_char(self, x: int, y: int, ch: int, fg: int, bg: int) -> None:
        """Draw a single character using the built-in 8x16 font."""
        font = _get_font()
        if ch < 0 or ch >= len(font):
            ch = 0
        glyph = font[ch]
        fg = fg & 0xFF
        bg = bg & 0xFF

        for row in range(CHAR_H):
            if y + row < 0 or y + row >= self.height:
                continue
            bits = glyph[row]
            base = (y + row) * self.width
            for col in range(CHAR_W):
                px = x + col
                if 0 <= px < self.width:
                    if bits & (0x80 >> col):
                        self.fb[base + px] = fg
                    else:
                        self.fb[base + px] = bg
        self._dirty = True

    def draw_string(self, x: int, y: int, text: str, fg: int, bg: int) -> None:
        """Draw a string of characters."""
        for i, ch in enumerate(text):
            self.draw_char(x + i * CHAR_W, y, ord(ch), fg, bg)

    def draw_string_from_mem(self, x: int, y: int,
                              mem_read_fn, addr: int, length: int,
                              fg: int, bg: int) -> None:
        """Draw a string from emulator memory."""
        for i in range(length):
            ch = mem_read_fn(addr + i)
            self.draw_char(x + i * CHAR_W, y, ch, fg, bg)

    def set_cursor(self, x: int, y: int, visible: bool) -> None:
        """Set hardware cursor position and visibility."""
        self.cursor_x = x
        self.cursor_y = y
        self.cursor_visible = visible
        self._dirty = True

    # ------------------------------------------------------------------
    # Display presentation
    # ------------------------------------------------------------------

    def present(self) -> None:
        """Update the display with current framebuffer contents."""
        if self._screen is None or not self._dirty:
            return
        import pygame

        # Convert indexed framebuffer to RGB surface
        for y in range(self.height):
            for x in range(self.width):
                idx = self.fb[y * self.width + x]
                color = self.palette[idx]
                self._surface.set_at((x, y), color)

        # Draw cursor overlay
        if self.cursor_visible:
            cx, cy = self.cursor_x, self.cursor_y
            for dy in range(16):
                for dx in range(8):
                    px, py = cx + dx, cy + dy
                    if 0 <= px < self.width and 0 <= py < self.height:
                        # Simple arrow cursor pattern
                        if dx <= dy and dx < 8:
                            # XOR the pixel
                            r, g, b, _ = self._surface.get_at((px, py))
                            self._surface.set_at((px, py), (r ^ 255, g ^ 255, b ^ 255))

        # Scale and blit to screen
        if self.scale == 1:
            self._screen.blit(self._surface, (0, 0))
        else:
            scaled = pygame.transform.scale(
                self._surface,
                (self.width * self.scale, self.height * self.scale))
            self._screen.blit(scaled, (0, 0))
        pygame.display.flip()
        self._dirty = False

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def pump_events(self) -> None:
        """Poll host events and add to queue."""
        if self._screen is None:
            return
        import pygame
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._events.append((EVT_QUIT, 0, 0))
            elif event.type == pygame.KEYDOWN:
                self._events.append((EVT_KEY_DOWN, event.key, event.mod))
            elif event.type == pygame.KEYUP:
                self._events.append((EVT_KEY_UP, event.key, event.mod))
            elif event.type == pygame.MOUSEMOTION:
                x, y = event.pos
                self._events.append((EVT_MOUSE_MOVE,
                                      x // self.scale, y // self.scale))
            elif event.type == pygame.MOUSEBUTTONDOWN:
                x, y = event.pos
                self._events.append((EVT_MOUSE_DOWN,
                                      x // self.scale,
                                      (y // self.scale) | (event.button << 16)))
            elif event.type == pygame.MOUSEBUTTONUP:
                x, y = event.pos
                self._events.append((EVT_MOUSE_UP,
                                      x // self.scale,
                                      (y // self.scale) | (event.button << 16)))

    def read_event(self) -> tuple[int, int, int]:
        """Pop the next event from the queue."""
        self.pump_events()
        if self._events:
            return self._events.pop(0)
        return (EVT_NONE, 0, 0)

    def push_event(self, evt_type: int, data1: int, data2: int) -> None:
        """Manually push an event (for testing)."""
        self._events.append((evt_type, data1, data2))

    # ------------------------------------------------------------------
    # Snapshot (for testing)
    # ------------------------------------------------------------------

    def snapshot(self) -> bytes:
        """Return a copy of the framebuffer."""
        return bytes(self.fb)

    def to_pil_image(self):
        """Convert framebuffer to a PIL Image (for debugging/export)."""
        from PIL import Image
        img = Image.new('RGB', (self.width, self.height))
        pixels = img.load()
        for y in range(self.height):
            for x in range(self.width):
                idx = self.fb[y * self.width + x]
                pixels[x, y] = self.palette[idx]
        return img
