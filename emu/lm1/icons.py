"""Crystal Desktop Icon System.

16x16 pixel-art icons for files, folders, applications, and system items.
Each icon stores pixel data as row strings with single-character palette
codes.  Transparent pixels are spaces.

Icons render through VDI at 1x or 2x scale.

Usage:
    from .icons import get_icon, icon_for_name
    folder = get_icon('folder')
    folder.draw(vdi, 100, 50, scale=2)   # 32x32 on screen

    name = icon_for_name('hello.lisp')   # -> 'file_code'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Icon:
    """A pixel-art icon with palette."""
    name: str
    width: int
    height: int
    palette: dict[str, int]      # char -> 0xRRGGBB
    pixels: tuple[str, ...]      # rows of palette chars, ' ' = transparent

    def draw(self, vdi, x: int, y: int, scale: int = 1) -> None:
        """Render icon to VDI framebuffer.

        scale=1 draws at native 16x16.
        scale=2 draws at 32x32 (each pixel becomes 2x2 block).
        """
        fb = vdi.fb
        sw = vdi.width
        sh = vdi.height
        pal = self.palette

        if scale == 1:
            for row_i, row in enumerate(self.pixels):
                py = y + row_i
                if py < 0 or py >= sh:
                    continue
                base = py * sw
                for col_i, ch in enumerate(row):
                    if ch == ' ':
                        continue
                    px = x + col_i
                    if 0 <= px < sw:
                        color = pal.get(ch)
                        if color is not None:
                            fb[base + px] = color
        else:
            for row_i, row in enumerate(self.pixels):
                for col_i, ch in enumerate(row):
                    if ch == ' ':
                        continue
                    color = pal.get(ch)
                    if color is None:
                        continue
                    bx = x + col_i * scale
                    by = y + row_i * scale
                    # Fill scale x scale block
                    for dy in range(scale):
                        py = by + dy
                        if py < 0 or py >= sh:
                            continue
                        base = py * sw
                        for dx in range(scale):
                            px = bx + dx
                            if 0 <= px < sw:
                                fb[base + px] = color
        vdi._dirty = True

    def draw_centered(self, vdi, x: int, y: int, w: int, h: int,
                      scale: int = 1) -> None:
        """Draw icon centered within a rectangle."""
        iw = self.width * scale
        ih = self.height * scale
        ox = x + (w - iw) // 2
        oy = y + (h - ih) // 2
        self.draw(vdi, ox, oy, scale)


# ===================================================================
# Stock icon definitions
# ===================================================================

def _icon(name: str, palette: dict[str, int], *rows: str) -> Icon:
    """Build an Icon from palette and 16 row strings."""
    assert len(rows) == 16, f"Icon {name}: expected 16 rows, got {len(rows)}"
    for i, r in enumerate(rows):
        assert len(r) == 16, f"Icon {name} row {i}: expected 16 chars, got {len(r)}"
    return Icon(name, 16, 16, palette, tuple(rows))


def _build_stock_icons() -> dict[str, Icon]:
    """Create all stock icons."""
    icons: dict[str, Icon] = {}

    # ── Folder (closed) ─────────────────────────────────────────
    icons['folder'] = _icon('folder',
        {'#': 0xFFB74D, 'H': 0xFFCC80, 'y': 0xE69940, 'o': 0xBF7020, 'T': 0xFFA726},
        '                ',
        '  TTTTTT        ',
        ' THHHHHT        ',
        ' T##############',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' ##############o',
        ' yyyyyyyyyyyyyyo',
        '  oooooooooooooo',
        '                ',
    )

    # ── Folder (open) ───────────────────────────────────────────
    icons['folder_open'] = _icon('folder_open',
        {'#': 0xFFB74D, 'H': 0xFFCC80, 'y': 0xE69940, 'o': 0xBF7020,
         'T': 0xFFA726, 'L': 0xFFE0A0},
        '                ',
        '  TTTTTT        ',
        ' THHHHHT        ',
        ' T##############',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' TTTTTTTTTTT HHo',
        ' TLLLLLLLLLTH#Ho',
        '  TLLLLLLLLTH##o',
        '  TLLLLLLLTH###o',
        '   TLLLLLLTH##oo',
        '   TLLLLLTH##ooo',
        '    TTTTTTTH#ooo',
        '     yyyyyyy ooo',
        '      ooooooooo ',
        '                ',
    )

    # ── File (generic) ──────────────────────────────────────────
    icons['file'] = _icon('file',
        {'B': 0x6080A0, 'W': 0xF0F0F5, 'p': 0xA0B8D0, 's': 0x405060},
        '                ',
        '    BBBBBBB     ',
        '    BWWWWBpB    ',
        '    BWWWBppB    ',
        '    BWWWBBBB    ',
        '    BWWWWWWB    ',
        '    BWWWWWWB    ',
        '    BWWWWWWB    ',
        '    BWWWWWWB    ',
        '    BWWWWWWB    ',
        '    BWWWWWWB    ',
        '    BWWWWWWB    ',
        '    BWWWWWWB    ',
        '    BBBBBBBB    ',
        '     ssssssss   ',
        '                ',
    )

    # ── File (text) ─────────────────────────────────────────────
    icons['file_text'] = _icon('file_text',
        {'B': 0x6080A0, 'W': 0xF0F0F5, 'p': 0xA0B8D0, 's': 0x405060,
         'L': 0x8898B0},
        '                ',
        '    BBBBBBB     ',
        '    BWWWWBpB    ',
        '    BWWWBppB    ',
        '    BWWWBBBB    ',
        '    BWLLLWWB    ',
        '    BWLLLLLB    ',
        '    BWWWWWWB    ',
        '    BWLLLLWB    ',
        '    BWLLLWWB    ',
        '    BWWWWWWB    ',
        '    BWLLLLLB    ',
        '    BWLLWWWB    ',
        '    BBBBBBBB    ',
        '     ssssssss   ',
        '                ',
    )

    # ── File (code) ─────────────────────────────────────────────
    icons['file_code'] = _icon('file_code',
        {'B': 0x6080A0, 'W': 0xF0F0F5, 'p': 0xA0B8D0, 's': 0x405060,
         'G': 0x00E676, 'C': 0x00D2FF, 'Y': 0xFFD740},
        '                ',
        '    BBBBBBB     ',
        '    BWWWWBpB    ',
        '    BWWWBppB    ',
        '    BWWWBBBB    ',
        '    BYCGWWWB    ',
        '    BWYCGwWB    ',
        '    BWWYCwWB    ',
        '    BWYCGwWB    ',
        '    BYCGWWWB    ',
        '    BWWWWWWB    ',
        '    BYCGWWWB    ',
        '    BWYCwWWB    ',
        '    BBBBBBBB    ',
        '     ssssssss   ',
        '                ',
    )

    # ── File (image) ────────────────────────────────────────────
    icons['file_image'] = _icon('file_image',
        {'B': 0x6080A0, 'W': 0xF0F0F5, 'p': 0xA0B8D0, 's': 0x405060,
         'G': 0x4CAF50, 'K': 0x2E7D32, 'S': 0x87CEEB, 'Y': 0xFFD740},
        '                ',
        '    BBBBBBB     ',
        '    BWWWWBpB    ',
        '    BWWWBppB    ',
        '    BWWWBBBB    ',
        '    BSSSSSSB    ',
        '    BSSYSSSB    ',
        '    BSSSSSSB    ',
        '    BSGGSSSB    ',
        '    BSGGKGSB    ',
        '    BGGGKGGB    ',
        '    BGKKKKGB    ',
        '    BKKKKKGB    ',
        '    BBBBBBBB    ',
        '     ssssssss   ',
        '                ',
    )

    # ── Application (crystal/diamond) ───────────────────────────
    icons['app'] = _icon('app',
        {'D': 0x3A7BD5, 'L': 0x5B9AEF, 'K': 0x1E4B8C, 'C': 0x00D2FF,
         'W': 0xE0E8FF, 's': 0x0A1A30},
        '                ',
        '       CC       ',
        '      CLLC      ',
        '     CLWWLC     ',
        '    CLWWWWLC    ',
        '   CLWWWWWWLC   ',
        '  CLLWWWWWWLLC  ',
        ' CLLLWWWWWWLLLC ',
        '  KDDDDDDDDDDK  ',
        '   KDDDDDDDDK   ',
        '    KDDDDDDK    ',
        '     KDDDDK     ',
        '      KDDK      ',
        '       KK       ',
        '                ',
        '                ',
    )

    # ── Terminal ────────────────────────────────────────────────
    icons['terminal'] = _icon('terminal',
        {'F': 0x3A3A50, 'B': 0x1A1A2E, 'G': 0x00E676, 'T': 0x4A5A78,
         'g': 0x00C853, 'W': 0xD0D8E8},
        '                ',
        ' FFFFFFFFFFFFFF ',
        ' FTTTTTTTTTTTTF ',
        ' FTBBBBBBBBBTF  ',
        ' FTBGgBBBBBBTF  ',
        ' FTBBGgBBBBBTF  ',
        ' FTBBBGgBBBBTF  ',
        ' FTBBGgBBBBBTF  ',
        ' FTBGgBBBBBBTF  ',
        ' FTBBBBBBBBBTF  ',
        ' FTBGGGGBWWBTF  ',
        ' FTBBBBBBBBBTF  ',
        ' FTTTTTTTTTTTTF ',
        ' FFFFFFFFFFFFFF ',
        '  ssssssssssss  ',
        '                ',
    )

    # ── Calculator ──────────────────────────────────────────────
    icons['calculator'] = _icon('calculator',
        {'F': 0x3A3A50, 'B': 0x1A1A2E, 'C': 0x00D2FF, 'T': 0x4A5A78,
         'W': 0xD0D8E8, 'b': 0x2A3450},
        '                ',
        '  FFFFFFFFFFFF  ',
        '  FTTTTTTTTTTF  ',
        '  FTCCCCCCCTF   ',
        '  FTTTTTTTTTTF  ',
        '  FTbWbWbWbTF   ',
        '  FTTTTTTTTTTF  ',
        '  FTbWbWbWbTF   ',
        '  FTTTTTTTTTTF  ',
        '  FTbWbWbWbTF   ',
        '  FTTTTTTTTTTF  ',
        '  FTbWbWbCbTF   ',
        '  FTTTTTTTTTTF  ',
        '  FFFFFFFFFFFF  ',
        '   ssssssssss   ',
        '                ',
    )

    # ── Clock ───────────────────────────────────────────────────
    icons['clock'] = _icon('clock',
        {'R': 0x3A4A66, 'W': 0xF0F0F5, 'B': 0x1A1A2E, 'C': 0x00D2FF,
         'H': 0xE53935, 'D': 0x6080A0},
        '                ',
        '     RRRRRR     ',
        '    RWWWWWWR    ',
        '   RWWWCWWWWR   ',
        '  RWWWWCWWWWR   ',
        '  RWWWWCWWWWR   ',
        '  RWWWWCWWWWR   ',
        '  RWWWWCCCCWR   ',
        '  RWWWWWWWHWR   ',
        '  RWWWWWWWWWR   ',
        '  RWWWWWWWWWR   ',
        '   RWWWWWWWWR   ',
        '    RWWWWWWR    ',
        '     RRRRRR     ',
        '                ',
        '                ',
    )

    # ── Editor (notepad with pencil) ────────────────────────────
    icons['editor'] = _icon('editor',
        {'B': 0x6080A0, 'W': 0xF0F0F5, 'L': 0x8898B0, 'Y': 0xFFD740,
         'y': 0xE6A800, 'O': 0xFF8F00, 'P': 0xFDD835, 's': 0x405060},
        '                ',
        '    BBBBBBBYY   ',
        '    BWWWWWByYY  ',
        '    BWLLLWB YO  ',
        '    BWLLLWB YO  ',
        '    BWWWWWBYO   ',
        '    BWLLLWyo    ',
        '    BWLLLyo     ',
        '    BWWWyo B    ',
        '    BWLyo WB    ',
        '    BWyo LLWB   ',
        '    Byo WLWWB   ',
        '    yo  WWWWB   ',
        '    B  BBBBB    ',
        '     ssssss     ',
        '                ',
    )

    # ── Settings (gear) ─────────────────────────────────────────
    icons['settings'] = _icon('settings',
        {'G': 0x90A0B0, 'D': 0x60708A, 'L': 0xC0CCD8, 'B': 0x404858,
         'H': 0x1A1A2E},
        '                ',
        '      GGG       ',
        '     GLLG       ',
        '   GGLLLGG      ',
        '  GLLLLLLG      ',
        '  GLLHHLLDGG    ',
        '   GLHHHLDG     ',
        ' GGLLHHHLLGG    ',
        ' GLLLLHLLLG     ',
        '  GGLHHHLDG     ',
        '  GLLHHLLDGG    ',
        '  GLLLLLLG      ',
        '   GGLLLGG      ',
        '     GDDG       ',
        '      GGG       ',
        '                ',
    )

    # ── Inspector (magnifying glass) ────────────────────────────
    icons['inspector'] = _icon('inspector',
        {'R': 0x6080A0, 'G': 0xC0D0E0, 'B': 0x3A7BD5, 'W': 0xF0F0F5,
         'D': 0x405060, 'H': 0x1E4B8C},
        '                ',
        '     RRRRR      ',
        '    RGGGGGR     ',
        '   RGWWWWWGR    ',
        '   RGWWWWWGR    ',
        '   RGWWWWWGR    ',
        '   RGWWWWWGR    ',
        '    RGGGGGR     ',
        '     RRRRRR     ',
        '         RHR    ',
        '          RHR   ',
        '           RHR  ',
        '            RR  ',
        '                ',
        '                ',
        '                ',
    )

    # ── Trash (can) ─────────────────────────────────────────────
    icons['trash'] = _icon('trash',
        {'F': 0x757575, 'D': 0x424242, 'L': 0x9E9E9E, 'B': 0x1A1A2E,
         'R': 0xE53935, 'H': 0xBDBDBD},
        '                ',
        '      HHH       ',
        '   FFFFFFFFFFF  ',
        '   LLLLLLLLLLL  ',
        '     DDDDDDDDD  ',
        '     DFLFLFLFD  ',
        '     DFLFLFLFD  ',
        '     DFLFLFLFD  ',
        '     DFLFLFLFD  ',
        '     DFLFLFLFD  ',
        '     DFLFLFLFD  ',
        '     DFLFLFLFD  ',
        '     DFLFLFLFD  ',
        '     DDDDDDDDD  ',
        '                ',
        '                ',
    )

    # ── Disk (hard drive) ───────────────────────────────────────
    icons['disk'] = _icon('disk',
        {'F': 0x4A5A78, 'D': 0x2A3450, 'L': 0x8898B0, 'G': 0x00E676,
         'W': 0xD0D8E8, 'B': 0x1A1A2E},
        '                ',
        '                ',
        '  FFFFFFFFFFFF  ',
        '  FDDDDDDDDDDF  ',
        '  FDDDDDDDDDDF  ',
        '  FDDDDDDDDDDF  ',
        '  FDDDDDDDDDDF  ',
        '  FLLLLLLLLLLF  ',
        '  FFFFFFFFFFFF  ',
        '  FLLLLLLLLLLF  ',
        '  FWWF    FGBF  ',
        '  FLLLLLLLLLLF  ',
        '  FFFFFFFFFFFF  ',
        '                ',
        '                ',
        '                ',
    )

    # ── Home (house) ────────────────────────────────────────────
    icons['home'] = _icon('home',
        {'R': 0xE53935, 'W': 0xF0F0F5, 'B': 0x795548, 'D': 0x5D4037,
         'Y': 0xFFD740, 'K': 0x1A1A2E, 'G': 0x4CAF50},
        '                ',
        '       RR       ',
        '      RRRR      ',
        '     RRRRRR     ',
        '    RRRRRRRR    ',
        '   RRRRRRRRRR   ',
        '  RRRRRRRRRRRR  ',
        '   BBBBBBBBBB   ',
        '   BWWBKKBWWB   ',
        '   BWWBKKBWWB   ',
        '   BBBBDDBBBB   ',
        '   BWWBDDBWWB   ',
        '   BWWBDDBWWB   ',
        '   BBBBDDBBBB   ',
        '  GGGGGGGGGGGG  ',
        '                ',
    )

    # ── File Manager (folder with arrow) ────────────────────────
    icons['file_manager'] = _icon('file_manager',
        {'#': 0xFFB74D, 'H': 0xFFCC80, 'y': 0xE69940, 'o': 0xBF7020,
         'T': 0xFFA726, 'A': 0x00D2FF, 'a': 0x0080B0},
        '                ',
        '  TTTTTT        ',
        ' THHHHHT        ',
        ' T##############',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHAAHHHHHHH#o',
        ' #HHHAAHAAAHHHH#',
        ' #HHAAAAAAAHHHH#',
        ' #HHHAAHAAAHHHH#',
        ' #HHHAAHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' ##############o',
        ' yyyyyyyyyyyyyyo',
        '  oooooooooooooo',
        '                ',
    )

    # ── New Folder ──────────────────────────────────────────────
    icons['new_folder'] = _icon('new_folder',
        {'#': 0xFFB74D, 'H': 0xFFCC80, 'y': 0xE69940, 'o': 0xBF7020,
         'T': 0xFFA726, 'P': 0x00E676},
        '                ',
        '  TTTTTT        ',
        ' THHHHHT        ',
        ' T##############',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHPHHHHHH#o',
        ' #HHHHHPHHHHHH#o',
        ' #HHHPPPPPHHHH#o',
        ' #HHHHHPHHHHHH#o',
        ' #HHHHHPHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' #HHHHHHHHHHHH#o',
        ' ##############o',
        ' yyyyyyyyyyyyyyo',
        '  oooooooooooooo',
        '                ',
    )

    # ── Refresh (circular arrows) ───────────────────────────────
    icons['refresh'] = _icon('refresh',
        {'A': 0x00D2FF, 'D': 0x0080B0},
        '                ',
        '                ',
        '     AAAAA      ',
        '    AA   AAA    ',
        '   AA     AA    ',
        '   A     AAAA   ',
        '   A       A    ',
        '                ',
        '                ',
        '    A       A   ',
        '   AAAA     A   ',
        '    AA     AA   ',
        '    AAA   AA    ',
        '      AAAAA     ',
        '                ',
        '                ',
    )

    # ── Arrow Up (navigate up) ──────────────────────────────────
    icons['arrow_up'] = _icon('arrow_up',
        {'A': 0x00D2FF, 'D': 0x0080B0},
        '                ',
        '                ',
        '       AA       ',
        '      AAAA      ',
        '     AAAAAA     ',
        '    AAAAAAAA    ',
        '       AA       ',
        '       AA       ',
        '       AA       ',
        '       AA       ',
        '       AA       ',
        '       AA       ',
        '                ',
        '                ',
        '                ',
        '                ',
    )

    # ── Arrow Back ──────────────────────────────────────────────
    icons['arrow_back'] = _icon('arrow_back',
        {'A': 0x00D2FF, 'D': 0x0080B0},
        '                ',
        '                ',
        '                ',
        '     AA         ',
        '    AA          ',
        '   AAAAAAAAAA   ',
        '  AAAAAAAAAAA   ',
        ' AAAAAAAAAAAA   ',
        '  AAAAAAAAAAA   ',
        '   AAAAAAAAAA   ',
        '    AA          ',
        '     AA         ',
        '                ',
        '                ',
        '                ',
        '                ',
    )

    # ── View Icons ──────────────────────────────────────────────
    icons['view_icons'] = _icon('view_icons',
        {'A': 0x00D2FF, 'F': 0x3A4A66},
        '                ',
        '                ',
        '  FFFF   FFFF   ',
        '  FAAF   FAAF   ',
        '  FAAF   FAAF   ',
        '  FFFF   FFFF   ',
        '   AA     AA    ',
        '                ',
        '  FFFF   FFFF   ',
        '  FAAF   FAAF   ',
        '  FAAF   FAAF   ',
        '  FFFF   FFFF   ',
        '   AA     AA    ',
        '                ',
        '                ',
        '                ',
    )

    # ── View List ───────────────────────────────────────────────
    icons['view_list'] = _icon('view_list',
        {'A': 0x00D2FF, 'F': 0x3A4A66, 'L': 0x6080A0},
        '                ',
        '                ',
        '  FF LLLLLLLL   ',
        '  FF LLLLLLLL   ',
        '                ',
        '  FF LLLLLLLL   ',
        '  FF LLLLLLLL   ',
        '                ',
        '  FF LLLLLLLL   ',
        '  FF LLLLLLLL   ',
        '                ',
        '  FF LLLLLLLL   ',
        '  FF LLLLLLLL   ',
        '                ',
        '                ',
        '                ',
    )

    # ── Save ────────────────────────────────────────────────────
    icons['save'] = _icon('save',
        {'F': 0x3A7BD5, 'W': 0xF0F0F5, 'D': 0x1E4B8C, 'B': 0x1A1A2E,
         'L': 0x6080A0},
        '                ',
        '  FFFFFFFFFFL   ',
        '  FWWWWWWWFFL   ',
        '  FWWWWWWWFFL   ',
        '  FWWWWWWWFFL   ',
        '  FFFFFFFFFFFFFL',
        '  FDDDDDDDDDDDFL',
        '  FDDDDDDDDDDDFL',
        '  FDDDDDDDDDDDFL',
        '  FDDBBBBBBBDFL ',
        '  FDDBWWWWWBDFL ',
        '  FDDBWWWWWBDFL ',
        '  FDDBWWWWWBDFL ',
        '  FFFFFFFFFFL   ',
        '   LLLLLLLLL    ',
        '                ',
    )

    return icons


# ===================================================================
# File type → icon mapping
# ===================================================================

_EXT_TO_ICON: dict[str, str] = {
    # Code
    'lisp': 'file_code', 'lsp': 'file_code', 'scm': 'file_code',
    'py': 'file_code', 'js': 'file_code', 'ts': 'file_code',
    'c': 'file_code', 'h': 'file_code', 'cpp': 'file_code',
    'rs': 'file_code', 'go': 'file_code', 'java': 'file_code',
    'html': 'file_code', 'css': 'file_code', 'json': 'file_code',
    'xml': 'file_code', 'sh': 'file_code',
    # Text
    'txt': 'file_text', 'md': 'file_text', 'log': 'file_text',
    'conf': 'file_text', 'cfg': 'file_text', 'ini': 'file_text',
    'csv': 'file_text',
    # Image
    'png': 'file_image', 'jpg': 'file_image', 'jpeg': 'file_image',
    'bmp': 'file_image', 'gif': 'file_image', 'svg': 'file_image',
    # Application
    'app': 'app',
    # Font
    'lmfont': 'file',
}


def icon_for_name(filename: str) -> str:
    """Return the stock icon name for a filename based on extension."""
    if '.' in filename:
        ext = filename.rsplit('.', 1)[1].lower()
        return _EXT_TO_ICON.get(ext, 'file')
    return 'file'


def icon_for_mime(mime_type: str) -> str:
    """Return the stock icon name for a MIME type."""
    if 'x-lisp' in mime_type or 'x-python' in mime_type or 'javascript' in mime_type:
        return 'file_code'
    if mime_type.startswith('text/'):
        return 'file_text'
    if mime_type.startswith('image/'):
        return 'file_image'
    if 'crystal-app' in mime_type:
        return 'app'
    return 'file'


# ===================================================================
# Lazy-loaded icon registry
# ===================================================================

_STOCK: dict[str, Icon] | None = None


def get_icon(name: str) -> Icon | None:
    """Get a stock icon by name. Returns None if not found."""
    global _STOCK
    if _STOCK is None:
        _STOCK = _build_stock_icons()
    return _STOCK.get(name)


def get_icon_names() -> list[str]:
    """List all available stock icon names."""
    global _STOCK
    if _STOCK is None:
        _STOCK = _build_stock_icons()
    return list(_STOCK.keys())
