#!/usr/bin/env python3
"""Compile a TrueType/OpenType font into the LM-1 .lmfont bitmap format.

Usage:
    python tools/compile_font.py <ttf_path> <pixel_size> <output.lmfont> [--ranges RANGES]

Example:
    python tools/compile_font.py /usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf 16 emu/lm1/fonts/noto_mono_16.lmfont

    python tools/compile_font.py NotoSansMono-Regular.ttf 14 fonts/noto_mono_14.lmfont \\
        --ranges "0x20-0x7E,0xA0-0xFF,0x2500-0x257F"

Default ranges cover:
    - Basic Latin (0x0020–0x007E)
    - Latin-1 Supplement (0x00A0–0x00FF)
    - General Punctuation subset (0x2010–0x2027)
    - Arrows (0x2190–0x21FF)
    - Mathematical Operators subset (0x2200–0x227F)
    - Box Drawing (0x2500–0x257F)
    - Block Elements (0x2580–0x259F)
    - Geometric Shapes subset (0x25A0–0x25C7)
    - Miscellaneous Symbols subset (0x2600–0x2667)

Requires: Pillow (pip install Pillow)
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Error: Pillow is required.  pip install Pillow")


# Default Unicode ranges
DEFAULT_RANGES: list[tuple[int, int]] = [
    (0x0020, 0x007E),   # Basic Latin (printable ASCII)
    (0x00A0, 0x00FF),   # Latin-1 Supplement
    (0x2010, 0x2027),   # General Punctuation (dashes, quotes, ellipsis)
    (0x2190, 0x21FF),   # Arrows
    (0x2200, 0x227F),   # Mathematical Operators (subset)
    (0x2500, 0x257F),   # Box Drawing
    (0x2580, 0x259F),   # Block Elements
    (0x25A0, 0x25C7),   # Geometric Shapes (subset)
    (0x2600, 0x2667),   # Miscellaneous Symbols (subset)
]


def determine_cell_size(font: ImageFont.FreeTypeFont,
                        size: int) -> tuple[int, int]:
    """Determine the monospace cell size for a font.

    For monospaced fonts all glyphs share the same advance width.
    We measure a reference set and take the maximum bounding box.
    """
    # Measure advance width from a reference character
    ref_chars = "ABCDMWmw0123456789"
    widths = set()
    for ch in ref_chars:
        bbox = font.getbbox(ch)
        if bbox:
            widths.add(bbox[2] - bbox[0])

    # For a true monospace font, all widths should be identical,
    # but we take the max to be safe
    char_w = max(widths) if widths else size // 2

    # Cell height: use font metrics
    ascent, descent = font.getmetrics()
    char_h = ascent + descent

    return char_w, char_h


def render_glyph(font: ImageFont.FreeTypeFont,
                 codepoint: int,
                 char_w: int, char_h: int,
                 ascent: int) -> bytes | None:
    """Render a single glyph to an 8-bit alpha bitmap.

    Returns char_w * char_h bytes (row-major, 0=transparent, 255=opaque),
    or None if the font does not contain this codepoint.
    """
    ch = chr(codepoint)

    # Check if the font has this glyph (Pillow doesn't expose cmap
    # directly, so we render and check for blank)
    img = Image.new('L', (char_w, char_h), 0)
    draw = ImageDraw.Draw(img)

    # Position glyph: left-aligned, baseline at `ascent` from top
    bbox = font.getbbox(ch)
    if bbox is None:
        return None

    # Calculate x offset to center the glyph in the cell
    glyph_w = bbox[2] - bbox[0]
    x_off = (char_w - glyph_w) // 2 - bbox[0]

    # y offset: align baseline
    y_off = -bbox[1]

    draw.text((x_off, y_off), ch, fill=255, font=font)

    # Check if glyph is entirely blank (font doesn't have it)
    pixels = img.tobytes()

    # For space character (0x20), allow blank
    if codepoint != 0x20 and all(b == 0 for b in pixels):
        return None

    return pixels


def compile_font(ttf_path: str, pixel_size: int,
                 ranges: list[tuple[int, int]]) -> bytes:
    """Compile a TTF into .lmfont binary data."""
    font = ImageFont.truetype(ttf_path, pixel_size)
    char_w, char_h = determine_cell_size(font, pixel_size)
    ascent, descent = font.getmetrics()

    print(f"Font: {Path(ttf_path).name}")
    print(f"Size: {pixel_size}px → cell {char_w}×{char_h} "
          f"(ascent={ascent}, descent={descent})")

    # Render all glyphs
    all_glyphs: dict[int, bytes] = {}
    for range_start, range_end in ranges:
        rendered = 0
        for cp in range(range_start, range_end + 1):
            data = render_glyph(font, cp, char_w, char_h, ascent)
            if data is not None:
                all_glyphs[cp] = data
                rendered += 1
        total = range_end - range_start + 1
        print(f"  U+{range_start:04X}–U+{range_end:04X}: "
              f"{rendered}/{total} glyphs")

    print(f"Total: {len(all_glyphs)} glyphs, "
          f"{len(all_glyphs) * char_w * char_h:,} bytes of glyph data")

    # Build the binary format
    # Group glyphs into contiguous ranges
    codepoints = sorted(all_glyphs.keys())
    out_ranges = _group_ranges(codepoints)

    parts: list[bytes] = []

    # Header
    if len(out_ranges) > 255:
        raise ValueError(f"Too many ranges: {len(out_ranges)} (max 255)")
    parts.append(struct.pack('<4sBBBB',
                             b'LMF1', char_w, char_h,
                             len(out_ranges), 0x01))  # flag: alpha

    # Range table
    for first, count in out_ranges:
        if first > 0xFFFF or count > 0xFFFF:
            raise ValueError(f"Range too large for uint16: {first}+{count}")
        parts.append(struct.pack('<HH', first, count))

    # Glyph data
    blank = bytes(char_w * char_h)
    for first, count in out_ranges:
        for i in range(count):
            cp = first + i
            parts.append(all_glyphs.get(cp, blank))

    return b''.join(parts)


def _group_ranges(cps: list[int]) -> list[tuple[int, int]]:
    """Group sorted codepoints into contiguous (first, count) ranges.

    Small gaps (≤4 missing glyphs) are filled with blank glyphs to
    reduce the number of ranges.
    """
    if not cps:
        return []

    ranges: list[tuple[int, int]] = []
    start = cps[0]
    prev = cps[0]

    for cp in cps[1:]:
        if cp <= prev + 5:  # allow small gaps
            prev = cp
        else:
            ranges.append((start, prev - start + 1))
            start = cp
            prev = cp
    ranges.append((start, prev - start + 1))
    return ranges


def parse_ranges(range_str: str) -> list[tuple[int, int]]:
    """Parse a range string like '0x20-0x7E,0x100-0x1FF'."""
    ranges = []
    for part in range_str.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            ranges.append((int(a, 0), int(b, 0)))
        else:
            cp = int(part, 0)
            ranges.append((cp, cp))
    return ranges


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile a TTF font into LM-1 .lmfont bitmap format")
    parser.add_argument('ttf', help="Path to TrueType/OpenType font file")
    parser.add_argument('size', type=int, help="Pixel size to render at")
    parser.add_argument('output', help="Output .lmfont file path")
    parser.add_argument('--ranges', default=None,
                        help="Comma-separated codepoint ranges "
                             "(e.g., '0x20-0x7E,0xA0-0xFF')")
    args = parser.parse_args()

    if args.ranges:
        ranges = parse_ranges(args.ranges)
    else:
        ranges = DEFAULT_RANGES

    data = compile_font(args.ttf, args.size, ranges)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)

    print(f"\nWrote {len(data):,} bytes → {out_path}")


if __name__ == '__main__':
    main()
