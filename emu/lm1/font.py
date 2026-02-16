"""LM-1 Font system — loadable anti-aliased bitmap fonts.

Each .lmfont file contains a monospace bitmap font with 8-bit alpha
per pixel, compiled from a TrueType/OpenType source via tools/compile_font.py.

Binary format (.lmfont):
  Header (8 bytes):
    magic     : b'LMF1'   (4 bytes)
    char_w    : uint8      glyph cell width in pixels
    char_h    : uint8      glyph cell height in pixels
    num_ranges: uint8      number of codepoint ranges
    flags     : uint8      bit 0: 1=alpha (8bpp), 0=bitmap (1bpp)

  Range table (4 bytes per range):
    first     : uint16 LE  first codepoint in range
    count     : uint16 LE  number of consecutive codepoints

  Glyph data:
    For alpha mode : char_w * char_h bytes per glyph (row-major)
    For bitmap mode: ceil(char_w/8) * char_h bytes per glyph
    Glyphs are stored contiguously in range order.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional


_MAGIC = b'LMF1'
_HEADER_FMT = '<4sBBBB'   # magic, char_w, char_h, num_ranges, flags
_RANGE_FMT = '<HH'        # first codepoint, count
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)   # 8
_RANGE_SIZE = struct.calcsize(_RANGE_FMT)     # 4

FLAG_ALPHA = 0x01  # 8-bit alpha per pixel (vs 1-bit bitmap)


class Font:
    """A monospace bitmap font with per-glyph alpha data.

    Glyphs are stored as flat bytes arrays: for alpha fonts each glyph
    is char_w * char_h bytes, one byte per pixel (0=transparent, 255=opaque).
    """

    __slots__ = ('char_w', 'char_h', '_glyphs', '_fallback')

    def __init__(self, char_w: int, char_h: int,
                 glyphs: dict[int, bytes], *,
                 fallback: Optional[bytes] = None):
        self.char_w = char_w
        self.char_h = char_h
        self._glyphs = glyphs
        # Fallback glyph: filled rectangle with 1-pixel border
        if fallback is not None:
            self._fallback = fallback
        else:
            self._fallback = self._make_fallback()

    def _make_fallback(self) -> bytes:
        """Generate a fallback glyph — a hollow rectangle."""
        w, h = self.char_w, self.char_h
        rows = []
        for y in range(h):
            row = bytearray(w)
            for x in range(w):
                if y <= 1 or y >= h - 2 or x <= 1 or x >= w - 2:
                    row[x] = 200
            rows.append(bytes(row))
        return b''.join(rows)

    def get_glyph(self, codepoint: int) -> bytes:
        """Return the alpha bitmap for *codepoint*, or fallback."""
        return self._glyphs.get(codepoint, self._fallback)

    def has_glyph(self, codepoint: int) -> bool:
        return codepoint in self._glyphs

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> 'Font':
        """Load a .lmfont file."""
        data = Path(path).read_bytes()
        return cls.from_bytes(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'Font':
        """Deserialize from raw .lmfont bytes."""
        magic, char_w, char_h, num_ranges, flags = struct.unpack_from(
            _HEADER_FMT, data, 0)
        if magic != _MAGIC:
            raise ValueError(f"Bad font magic: {magic!r}")

        is_alpha = bool(flags & FLAG_ALPHA)
        if is_alpha:
            glyph_size = char_w * char_h
        else:
            glyph_size = ((char_w + 7) // 8) * char_h

        offset = _HEADER_SIZE
        ranges: list[tuple[int, int]] = []
        for _ in range(num_ranges):
            first, count = struct.unpack_from(_RANGE_FMT, data, offset)
            ranges.append((first, count))
            offset += _RANGE_SIZE

        glyphs: dict[int, bytes] = {}
        for first, count in ranges:
            for i in range(count):
                cp = first + i
                raw = data[offset:offset + glyph_size]
                if is_alpha:
                    glyphs[cp] = raw
                else:
                    # Expand 1-bit bitmap to 8-bit alpha
                    glyphs[cp] = _expand_bitmap(raw, char_w, char_h)
                offset += glyph_size

        return cls(char_w, char_h, glyphs)

    def save(self, path: str | Path) -> None:
        """Serialize to a .lmfont file."""
        Path(path).write_bytes(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize to .lmfont format."""
        # Collect ranges: group consecutive codepoints
        codepoints = sorted(self._glyphs.keys())
        ranges = _group_into_ranges(codepoints)

        parts: list[bytes] = []
        # Header
        parts.append(struct.pack(_HEADER_FMT,
                                 _MAGIC, self.char_w, self.char_h,
                                 len(ranges), FLAG_ALPHA))
        # Range table
        for first, count in ranges:
            parts.append(struct.pack(_RANGE_FMT, first, count))
        # Glyph data
        for first, count in ranges:
            for i in range(count):
                cp = first + i
                parts.append(self._glyphs[cp])

        return b''.join(parts)


def _expand_bitmap(data: bytes, char_w: int, char_h: int) -> bytes:
    """Expand 1-bit-per-pixel bitmap to 8-bit alpha."""
    bpr = (char_w + 7) // 8  # bytes per row
    out = bytearray(char_w * char_h)
    for y in range(char_h):
        for x in range(char_w):
            byte_idx = y * bpr + (x >> 3)
            bit = (data[byte_idx] >> (7 - (x & 7))) & 1
            out[y * char_w + x] = 255 if bit else 0
    return bytes(out)


def _group_into_ranges(cps: list[int]) -> list[tuple[int, int]]:
    """Group sorted codepoints into contiguous ranges."""
    if not cps:
        return []
    ranges: list[tuple[int, int]] = []
    start = cps[0]
    prev = cps[0]
    for cp in cps[1:]:
        if cp == prev + 1:
            prev = cp
        else:
            ranges.append((start, prev - start + 1))
            start = cp
            prev = cp
    ranges.append((start, prev - start + 1))
    return ranges


# ------------------------------------------------------------------
# Default font loading
# ------------------------------------------------------------------

_FONTS_DIR = Path(__file__).parent / 'fonts'

_default_font: Optional[Font] = None


def get_default_font() -> Font:
    """Return the default UI font (cached).

    Falls back to a minimal built-in font if no .lmfont files exist.
    """
    global _default_font
    if _default_font is None:
        _default_font = _load_default()
    return _default_font


def _load_default() -> Font:
    """Try to load the default font from the fonts directory."""
    # Preferred: noto_mono_16.lmfont
    for name in ('noto_mono_16.lmfont', 'noto_mono_14.lmfont'):
        path = _FONTS_DIR / name
        if path.exists():
            return Font.load(path)

    # Fallback: any .lmfont file
    if _FONTS_DIR.exists():
        for path in sorted(_FONTS_DIR.glob('*.lmfont')):
            return Font.load(path)

    # Last resort: built-in minimal font
    return _make_builtin_font()


def load_font(name: str) -> Font:
    """Load a named font from the fonts directory."""
    path = _FONTS_DIR / name
    if not path.suffix:
        path = path.with_suffix('.lmfont')
    return Font.load(path)


def _make_builtin_font() -> Font:
    """Create a minimal fallback font (block glyphs for printable ASCII)."""
    from lm1 import vdi as _vdi_module
    old_font = _vdi_module._make_minimal_font()
    # Convert 1-bit 8x16 glyphs to 8-bit alpha
    glyphs: dict[int, bytes] = {}
    for cp in range(128):
        raw = old_font[cp]
        glyphs[cp] = _expand_bitmap(raw, 8, 16)
    return Font(8, 16, glyphs)
