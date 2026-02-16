"""LM-1 VDI (Virtual Device Interface) Display Engine.

32-bit RGBA truecolor framebuffer graphics system.
Spiritual successor to GEM VDI — modern, no palette limitations.

Features:
  - 32-bit RGBA framebuffer (0xRRGGBB colors, alpha blending)
  - Drawing primitives: rect fill, gradient rect, shadow rect, bitblt,
    line, character/string
  - Hardware cursor overlay
  - Host display via pygame (optional headless mode for testing)
  - Event injection: keyboard/mouse from host → emulator

VDI functions are invoked via TRAP 0x83 with:
  r1 = function code (fixnum)
  r2..r8 = arguments (fixnums)
  Return values in r1 (and sometimes r2, r3)

Function codes:
  0  VDI_SET_MODE     r2=width, r3=height  → set resolution
  1  VDI_FILL_RECT    r2=x, r3=y, r4=w, r5=h, r6=color(0xRRGGBB)
  2  VDI_BLIT         r2=src_x, r3=src_y, r4=dst_x, r5=dst_y, r6=w, r7=h
  3  (reserved)
  4  VDI_DRAW_CHAR    r2=x, r3=y, r4=char_code, r5=fg, r6=bg
  5  VDI_DRAW_STRING  r2=x, r3=y, r4=str_addr, r5=len, r6=fg, r7=bg
  6  VDI_SET_CURSOR   r2=x, r3=y, r4=visible(0/1)
  7  VDI_READ_PIXEL   r2=x, r3=y  → r1=color(0xRRGGBB)
  8  VDI_DRAW_LINE    r2=x1, r3=y1, r4=x2, r5=y2, r6=color
  9  VDI_GET_MODE     → r1=width, r2=height
  10 VDI_SCROLL       r2=x, r3=y, r4=w, r5=h, r6=dx, r7=dy
  11 VDI_PRESENT      Force display update
  12 VDI_READ_EVENT   → r1=event_type, r2=data1, r3=data2
  13 VDI_GRAD_RECT    r2=x, r3=y, r4=w, r5=h, r6=color1, r7=color2, r8=dir
  14 VDI_SHADOW_RECT  r2=x, r3=y, r4=w, r5=h, r6=radius, r7=alpha
"""

from __future__ import annotations

import array
from typing import Optional

from lm1.font import Font, get_default_font, load_font


# VDI function codes
VDI_SET_MODE     = 0
VDI_FILL_RECT    = 1
VDI_BLIT         = 2
# 3 = reserved (was VDI_SET_PALETTE)
VDI_DRAW_CHAR    = 4
VDI_DRAW_STRING  = 5
VDI_SET_CURSOR   = 6
VDI_READ_PIXEL   = 7
VDI_DRAW_LINE    = 8
VDI_GET_MODE     = 9
VDI_SCROLL       = 10
VDI_PRESENT      = 11
VDI_READ_EVENT   = 12
VDI_GRAD_RECT    = 13
VDI_SHADOW_RECT  = 14

# Event types
EVT_NONE         = 0
EVT_KEY_DOWN     = 1
EVT_KEY_UP       = 2
EVT_MOUSE_MOVE   = 3
EVT_MOUSE_DOWN   = 4
EVT_MOUSE_UP     = 5
EVT_QUIT         = 6
EVT_TIMER        = 7


# --- Color helpers ---

def rgb(r: int, g: int, b: int) -> int:
    """Pack RGB bytes into a 0xRRGGBB int."""
    return ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)

def unpack_rgb(color: int) -> tuple[int, int, int]:
    """Unpack a 0xRRGGBB int to (r, g, b)."""
    return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)

def lerp_color(c1: int, c2: int, t: float) -> int:
    """Linear interpolation between two RGB colors. t in [0, 1]."""
    r1, g1, b1 = unpack_rgb(c1)
    r2, g2, b2 = unpack_rgb(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return rgb(r, g, b)

def alpha_blend(fg: int, bg: int, alpha: int) -> int:
    """Blend fg over bg with alpha (0=transparent, 255=opaque)."""
    if alpha >= 255:
        return fg
    if alpha <= 0:
        return bg
    fr, fg_c, fb = unpack_rgb(fg)
    br, bg_c, bb = unpack_rgb(bg)
    a = alpha / 255.0
    ia = 1.0 - a
    return rgb(int(fr * a + br * ia), int(fg_c * a + bg_c * ia), int(fb * a + bb * ia))


# Built-in 8×16 bitmap font (CP437-style, first 128 ASCII chars)
# Each character is 8 pixels wide, 16 pixels tall = 16 bytes
def _make_minimal_font() -> list[bytes]:
    """Generate a minimal 8x16 bitmap font for printable ASCII."""
    font = []
    for ch in range(128):
        if 33 <= ch <= 126:
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
    """Render a single ASCII character as 8x16 bitmap."""
    _GLYPH_DATA = {
        # Digits
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
        # Uppercase
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
        # Lowercase
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


# Legacy constants — prefer vdi.font.char_w / vdi.font.char_h
CHAR_W = 8
CHAR_H = 16

# Sentinel: pass as bg to draw text without filling background pixels.
# The glyph is alpha-blended over whatever is already in the framebuffer.
BG_TRANSPARENT = -1

# Gradient directions for VDI_GRAD_RECT
GRAD_HORIZONTAL = 0
GRAD_VERTICAL   = 1


class VDI:
    """Virtual Device Interface — 32-bit RGBA truecolor framebuffer.

    Colors are 0xRRGGBB integers throughout. No palette.
    Can operate in headless mode (for testing) or with a pygame
    display window (for interactive use).
    """

    def __init__(self, width: int = 640, height: int = 480,
                 headless: bool = True, scale: int = 1):
        self.width = width
        self.height = height
        self.scale = scale
        self.headless = headless

        # 32-bit RGB framebuffer — stored as flat array of ints (0xRRGGBB)
        self.fb = array.array('I', [0] * (width * height))

        # Hardware cursor
        self.cursor_x = 0
        self.cursor_y = 0
        self.cursor_visible = False

        # Font
        self.font: Font = get_default_font()

        # Event queue
        self._events: list[tuple[int, int, int]] = []

        # Pygame state
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
        self.fb = array.array('I', [0] * (width * height))
        self._dirty = True
        if self._screen is not None:
            import pygame
            self._screen = pygame.display.set_mode(
                (width * self.scale, height * self.scale))
            self._surface = pygame.Surface((width, height))

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        """Fill a rectangle with an RGB color (0xRRGGBB)."""
        color = color & 0xFFFFFF
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.width, x + w)
        y1 = min(self.height, y + h)
        row_w = x1 - x0
        if row_w <= 0:
            return
        row = array.array('I', [color] * row_w)
        for ry in range(y0, y1):
            start = ry * self.width + x0
            self.fb[start:start + row_w] = row
        self._dirty = True

    def grad_rect(self, x: int, y: int, w: int, h: int,
                  color1: int, color2: int, direction: int = GRAD_VERTICAL) -> None:
        """Fill a rectangle with a linear gradient between two RGB colors."""
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.width, x + w)
        y1 = min(self.height, y + h)
        rw = x1 - x0
        rh = y1 - y0
        if rw <= 0 or rh <= 0:
            return

        if direction == GRAD_VERTICAL:
            span = max(1, h - 1)
            for ry in range(y0, y1):
                t = (ry - y) / span
                c = lerp_color(color1, color2, t)
                start = ry * self.width + x0
                self.fb[start:start + rw] = array.array('I', [c] * rw)
        else:  # GRAD_HORIZONTAL
            span = max(1, w - 1)
            for ry in range(y0, y1):
                base = ry * self.width
                for rx in range(x0, x1):
                    t = (rx - x) / span
                    self.fb[base + rx] = lerp_color(color1, color2, t)
        self._dirty = True

    def shadow_rect(self, x: int, y: int, w: int, h: int,
                    radius: int = 4, alpha: int = 80) -> None:
        """Draw a drop shadow behind a rectangle.

        Draws a soft shadow offset to the bottom-right.
        The shadow fills the region (x, y) → (x+w+radius, y+h+radius)
        with the shadow body at (x+radius, y+radius, w, h).
        """
        shadow_color = 0x000000
        # Fill the shadow body
        for sy in range(y + radius, min(y + radius + h, self.height)):
            for sx in range(x + radius, min(x + radius + w, self.width)):
                if 0 <= sx < self.width and 0 <= sy < self.height:
                    bg = self.fb[sy * self.width + sx]
                    self.fb[sy * self.width + sx] = alpha_blend(shadow_color, bg, alpha)
        self._dirty = True

    def read_pixel(self, x: int, y: int) -> int:
        """Read a single pixel's RGB color (0xRRGGBB)."""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.fb[y * self.width + x]
        return 0

    def draw_line(self, x1: int, y1: int, x2: int, y2: int, color: int) -> None:
        """Draw a line using Bresenham's algorithm."""
        color = color & 0xFFFFFF
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
        if dy > 0:
            self.fill_rect(x, y, w, min(dy, h), 0)
        elif dy < 0:
            self.fill_rect(x, max(y, y + h + dy), w, min(-dy, h), 0)
        if dx > 0:
            self.fill_rect(x, y, min(dx, w), h, 0)
        elif dx < 0:
            self.fill_rect(max(x, x + w + dx), y, min(-dx, w), h, 0)

    def draw_char(self, x: int, y: int, ch: int, fg: int, bg: int,
                  *, font: Font | None = None) -> None:
        """Draw a single character using the current font.

        If *bg* is ``BG_TRANSPARENT`` (-1), glyph pixels are alpha-blended
        over the existing framebuffer contents (no background fill).
        Otherwise the background is filled with *bg* colour.
        """
        fnt = font or self.font
        glyph = fnt.get_glyph(ch)
        cw, ch_h = fnt.char_w, fnt.char_h
        fg = fg & 0xFFFFFF
        transparent = (bg == BG_TRANSPARENT)
        if not transparent:
            bg = bg & 0xFFFFFF

        fb = self.fb
        w = self.width
        h = self.height

        for row in range(ch_h):
            py = y + row
            if py < 0 or py >= h:
                continue
            base = py * w
            row_off = row * cw
            for col in range(cw):
                px = x + col
                if px < 0 or px >= w:
                    continue
                alpha = glyph[row_off + col]
                idx = base + px
                if alpha >= 255:
                    fb[idx] = fg
                elif alpha == 0:
                    if not transparent:
                        fb[idx] = bg
                else:
                    # Alpha blend
                    bg_px = fb[idx] if transparent else bg
                    fb[idx] = alpha_blend(fg, bg_px, alpha)
        self._dirty = True

    def draw_string(self, x: int, y: int, text: str, fg: int, bg: int,
                    *, font: Font | None = None) -> None:
        """Draw a string of characters."""
        fnt = font or self.font
        cw = fnt.char_w
        for i, ch in enumerate(text):
            self.draw_char(x + i * cw, y, ord(ch), fg, bg, font=fnt)

    def draw_string_from_mem(self, x: int, y: int,
                              mem_read_fn, addr: int, length: int,
                              fg: int, bg: int,
                              *, font: Font | None = None) -> None:
        """Draw a string from emulator memory."""
        fnt = font or self.font
        cw = fnt.char_w
        for i in range(length):
            ch = mem_read_fn(addr + i)
            self.draw_char(x + i * cw, y, ch, fg, bg, font=fnt)

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

        # Write RGB directly to surface
        for y in range(self.height):
            for x in range(self.width):
                c = self.fb[y * self.width + x]
                self._surface.set_at((x, y),
                    ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF))

        # Draw cursor overlay
        if self.cursor_visible:
            cx, cy = self.cursor_x, self.cursor_y
            for dy in range(16):
                for dx in range(8):
                    px, py = cx + dx, cy + dy
                    if 0 <= px < self.width and 0 <= py < self.height:
                        if dx <= dy and dx < 8:
                            r, g, b, _ = self._surface.get_at((px, py))
                            self._surface.set_at((px, py), (r ^ 255, g ^ 255, b ^ 255))

        # Scale and blit
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

    def snapshot(self) -> array.array:
        """Return a copy of the framebuffer."""
        return array.array('I', self.fb)

    def to_pil_image(self):
        """Convert framebuffer to a PIL Image (for debugging/export)."""
        from PIL import Image
        img = Image.new('RGB', (self.width, self.height))
        pixels = img.load()
        for y in range(self.height):
            for x in range(self.width):
                c = self.fb[y * self.width + x]
                pixels[x, y] = ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
        return img
