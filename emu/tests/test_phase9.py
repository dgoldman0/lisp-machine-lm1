"""Phase 9 tests — VDI Display Engine.

Tests the VDI drawing primitives (headless framebuffer),
emulator trap 0x83 wiring, font rendering, event system,
and a small animated-cursor demo via compiled Lisp.
"""

import io
import os
import struct
import tempfile

from lm1.testing.harness import test
from lm1.execute import Emulator
from lm1.asm import Assembler
from lm1.word import (
    NIL, T, tag_fixnum, untag_fixnum,
    is_fixnum, WORD_MASK,
)
from lm1.vdi import (
    VDI,
    VDI_SET_MODE, VDI_FILL_RECT, VDI_BLIT,
    VDI_DRAW_CHAR, VDI_DRAW_STRING, VDI_SET_CURSOR, VDI_READ_PIXEL,
    VDI_DRAW_LINE, VDI_GET_MODE, VDI_SCROLL, VDI_PRESENT,
    VDI_READ_EVENT, VDI_GRAD_RECT, VDI_SHADOW_RECT,
    EVT_NONE, EVT_KEY_DOWN, EVT_QUIT,
    CHAR_W, CHAR_H,
    GRAD_VERTICAL, GRAD_HORIZONTAL,
    rgb, unpack_rgb, lerp_color, alpha_blend,
)

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

NURSERY_BASE = 0x3_0000
NURSERY_SIZE = 0x1_0000
OLDGEN_BASE  = 0x5_0000
OLDGEN_SIZE  = 0x2_0000


def _make_emu_with_vdi(width=320, height=200):
    """Create an Emulator with a headless VDI attached."""
    vdi = VDI(width=width, height=height, headless=True)
    stdout = io.StringIO()
    emu = Emulator(
        mem_size=1024 * 1024,
        nursery_base=NURSERY_BASE,
        nursery_size=NURSERY_SIZE,
        oldgen_base=OLDGEN_BASE,
        oldgen_size=OLDGEN_SIZE,
        stdout=stdout,
        vdi=vdi,
    )
    return emu, vdi, stdout


def _asm_run(asm_source, max_instructions=10_000, width=320, height=200):
    """Assemble and run with VDI attached, return (vdi, emu, stdout_str)."""
    emu, vdi, stdout = _make_emu_with_vdi(width, height)
    asm = Assembler()
    words = asm.assemble_to_words(asm_source)
    emu.mem.load_instructions(0, words)
    for t in emu.threads:
        t.pc = 0
    emu.run(max_instructions=max_instructions)
    return vdi, emu, stdout.getvalue()


# ===================================================================
# Batch: phase9_vdi_unit — VDI module unit tests (no emulator)
# ===================================================================

@test("vdi_fill_rect", batch="phase9_vdi_unit")
def test_vdi_fill_rect():
    """fill_rect writes correct pixels to framebuffer."""
    vdi = VDI(width=64, height=64, headless=True)
    # Initially all black
    assert vdi.read_pixel(0, 0) == 0
    assert vdi.read_pixel(10, 10) == 0
    # Fill a 10x10 rect at (5,5) with color 15 (white)
    vdi.fill_rect(5, 5, 10, 10, 15)
    # Inside
    assert vdi.read_pixel(5, 5) == 15
    assert vdi.read_pixel(14, 14) == 15
    assert vdi.read_pixel(10, 10) == 15
    # Outside
    assert vdi.read_pixel(4, 5) == 0
    assert vdi.read_pixel(15, 5) == 0
    assert vdi.read_pixel(5, 4) == 0
    assert vdi.read_pixel(5, 15) == 0


@test("vdi_read_pixel_oob", batch="phase9_vdi_unit")
def test_vdi_read_pixel_oob():
    """read_pixel returns 0 for out-of-bounds coordinates."""
    vdi = VDI(width=32, height=32, headless=True)
    assert vdi.read_pixel(-1, 0) == 0
    assert vdi.read_pixel(0, -1) == 0
    assert vdi.read_pixel(32, 0) == 0
    assert vdi.read_pixel(0, 32) == 0


@test("vdi_draw_line", batch="phase9_vdi_unit")
def test_vdi_draw_line():
    """draw_line draws a horizontal and diagonal line correctly."""
    vdi = VDI(width=64, height=64, headless=True)
    # Horizontal line
    vdi.draw_line(0, 10, 20, 10, 4)
    for x in range(21):
        assert vdi.read_pixel(x, 10) == 4, f"pixel ({x},10) should be 4"
    assert vdi.read_pixel(21, 10) == 0
    # Diagonal line
    vdi.draw_line(0, 0, 10, 10, 9)
    for i in range(11):
        assert vdi.read_pixel(i, i) == 9, f"pixel ({i},{i}) should be 9"


@test("vdi_draw_char", batch="phase9_vdi_unit")
def test_vdi_draw_char():
    """draw_char renders a character with fg/bg colors."""
    vdi = VDI(width=64, height=64, headless=True)
    # Draw 'A' (0x41) at (0,0) with fg=15, bg=1
    vdi.draw_char(0, 0, ord('A'), 15, 1)
    # The character cell is 8x16 — check some pixels are fg or bg
    found_fg = False
    found_bg = False
    for y in range(CHAR_H):
        for x in range(CHAR_W):
            px = vdi.read_pixel(x, y)
            if px == 15:
                found_fg = True
            elif px == 1:
                found_bg = True
            else:
                assert False, f"Unexpected pixel value {px} at ({x},{y})"
    assert found_fg, "Character 'A' should have foreground pixels"
    assert found_bg, "Character 'A' should have background pixels"


@test("vdi_draw_string", batch="phase9_vdi_unit")
def test_vdi_draw_string():
    """draw_string renders multiple characters."""
    vdi = VDI(width=128, height=32, headless=True)
    vdi.draw_string(0, 0, "Hi", 15, 0)
    # Check that both character cells have some foreground pixels
    # First char at (0..7, 0..15)
    h_pixels = sum(1 for x in range(CHAR_W) for y in range(CHAR_H)
                   if vdi.read_pixel(x, y) == 15)
    assert h_pixels > 0, "'H' should have foreground pixels"
    # Second char at (8..15, 0..15)
    i_pixels = sum(1 for x in range(CHAR_W, 2 * CHAR_W) for y in range(CHAR_H)
                   if vdi.read_pixel(x, y) == 15)
    assert i_pixels > 0, "'i' should have foreground pixels"


@test("vdi_grad_rect", batch="phase9_vdi_unit")
def test_vdi_grad_rect():
    """grad_rect produces a vertical gradient between two colors."""
    vdi = VDI(width=16, height=16, headless=True)
    c1 = rgb(255, 0, 0)  # red
    c2 = rgb(0, 0, 255)  # blue
    vdi.grad_rect(0, 0, 16, 16, c1, c2, GRAD_VERTICAL)
    # Top row should be pure red
    assert vdi.read_pixel(0, 0) == c1
    # Bottom row should be pure blue
    assert vdi.read_pixel(0, 15) == c2
    # Middle rows should be blended
    mid = vdi.read_pixel(0, 7)
    assert mid != c1 and mid != c2, "Middle should be blended"


@test("vdi_blit_copy", batch="phase9_vdi_unit")
def test_vdi_blit_copy():
    """blit copies a rectangular region within the framebuffer."""
    vdi = VDI(width=64, height=64, headless=True)
    vdi.fill_rect(0, 0, 4, 4, 7)
    vdi.blit(0, 0, 10, 10, 4, 4)
    # Destination should now have color 7
    for y in range(10, 14):
        for x in range(10, 14):
            assert vdi.read_pixel(x, y) == 7, f"blit dest ({x},{y})"
    # Source should still have color 7
    assert vdi.read_pixel(0, 0) == 7


@test("vdi_scroll", batch="phase9_vdi_unit")
def test_vdi_scroll():
    """scroll moves pixels and clears exposed area."""
    vdi = VDI(width=32, height=32, headless=True)
    # Fill entire fb with color 5
    vdi.fill_rect(0, 0, 32, 32, 5)
    # Scroll down by 4 pixels (entire framebuffer region)
    vdi.scroll(0, 0, 32, 32, 0, 4)
    # Top 4 rows should be cleared (color 0)
    for y in range(4):
        assert vdi.read_pixel(16, y) == 0, f"scrolled-out row {y}"
    # Row 4+ should have original color
    assert vdi.read_pixel(16, 4) == 5


@test("vdi_set_mode", batch="phase9_vdi_unit")
def test_vdi_set_mode():
    """set_mode reinitializes framebuffer."""
    vdi = VDI(width=64, height=64, headless=True)
    vdi.fill_rect(0, 0, 64, 64, 3)
    vdi.set_mode(32, 32)
    assert vdi.width == 32
    assert vdi.height == 32
    assert vdi.read_pixel(0, 0) == 0  # new fb is cleared


@test("vdi_events", batch="phase9_vdi_unit")
def test_vdi_events():
    """Event push/read works correctly."""
    vdi = VDI(width=32, height=32, headless=True)
    # No events initially
    evt = vdi.read_event()
    assert evt == (EVT_NONE, 0, 0)
    # Push events
    vdi.push_event(EVT_KEY_DOWN, 65, 0)  # 'A' key
    vdi.push_event(EVT_QUIT, 0, 0)
    # Read them back in order
    evt = vdi.read_event()
    assert evt == (EVT_KEY_DOWN, 65, 0)
    evt = vdi.read_event()
    assert evt == (EVT_QUIT, 0, 0)
    # Queue now empty
    evt = vdi.read_event()
    assert evt == (EVT_NONE, 0, 0)


@test("vdi_cursor", batch="phase9_vdi_unit")
def test_vdi_cursor():
    """set_cursor updates cursor state."""
    vdi = VDI(width=32, height=32, headless=True)
    vdi.set_cursor(100, 50, True)
    assert vdi.cursor_x == 100
    assert vdi.cursor_y == 50
    assert vdi.cursor_visible is True
    vdi.set_cursor(0, 0, False)
    assert vdi.cursor_visible is False


@test("vdi_snapshot", batch="phase9_vdi_unit")
def test_vdi_snapshot():
    """snapshot returns a copy of the framebuffer."""
    vdi = VDI(width=4, height=4, headless=True)
    vdi.fill_rect(0, 0, 2, 2, 10)
    snap = vdi.snapshot()
    assert len(snap) == 16
    # Top-left 2x2 should be color 10
    assert snap[0] == 10
    assert snap[1] == 10
    assert snap[4] == 10
    assert snap[5] == 10
    # Bottom-right should be 0
    assert snap[15] == 0
    # Snapshot is independent copy
    vdi.fill_rect(0, 0, 4, 4, 0)
    assert snap[0] == 10  # original snapshot unchanged


# ===================================================================
# Batch: phase9_trap — VDI via emulator TRAP 0x83
# ===================================================================

@test("trap_vdi_fill_rect", batch="phase9_trap")
def test_trap_vdi_fill_rect():
    """TRAP 0x83 with VDI_FILL_RECT fills framebuffer."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    ; VDI_FILL_RECT: r1=func(1), r2=x, r3=y, r4=w, r5=h, r6=color
    LI r1, {VDI_FILL_RECT * 2}
    LI r2, {10 * 2}
    LI r3, {10 * 2}
    LI r4, {5 * 2}
    LI r5, {5 * 2}
    LI r6, {15 * 2}
    TRAP 0x83
    HALT
"""
    vdi, emu, out = _asm_run(asm)
    # Check framebuffer directly
    assert vdi.read_pixel(12, 12) == 15
    assert vdi.read_pixel(9, 12) == 0
    assert vdi.read_pixel(15, 12) == 0


@test("trap_vdi_read_pixel", batch="phase9_trap")
def test_trap_vdi_read_pixel():
    """TRAP 0x83 with VDI_READ_PIXEL returns correct color."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    ; Fill rect first
    LI r1, {VDI_FILL_RECT * 2}
    LI r2, {0 * 2}
    LI r3, {0 * 2}
    LI r4, {10 * 2}
    LI r5, {10 * 2}
    LI r6, {7 * 2}
    TRAP 0x83
    ; Read pixel at (5,5)
    LI r1, {VDI_READ_PIXEL * 2}
    LI r2, {5 * 2}
    LI r3, {5 * 2}
    TRAP 0x83
    ; r1 should now contain tag_fixnum(7) = 14
    HALT
"""
    vdi, emu, out = _asm_run(asm)
    # After VDI_READ_PIXEL, r1 should be tag_fixnum(7) = 14
    r1 = emu.thread.regs[1]
    assert is_fixnum(r1), f"r1 should be fixnum, got {r1:#x}"
    assert untag_fixnum(r1) == 7, f"Expected pixel=7, got {untag_fixnum(r1)}"


@test("trap_vdi_draw_line", batch="phase9_trap")
def test_trap_vdi_draw_line():
    """TRAP 0x83 with VDI_DRAW_LINE draws a line."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    ; Draw horizontal line from (0,5) to (20,5) color=4
    LI r1, {VDI_DRAW_LINE * 2}
    LI r2, {0 * 2}
    LI r3, {5 * 2}
    LI r4, {20 * 2}
    LI r5, {5 * 2}
    LI r6, {4 * 2}
    TRAP 0x83
    HALT
"""
    vdi, emu, out = _asm_run(asm)
    for x in range(21):
        assert vdi.read_pixel(x, 5) == 4, f"pixel ({x},5)"
    assert vdi.read_pixel(21, 5) == 0


@test("trap_vdi_get_mode", batch="phase9_trap")
def test_trap_vdi_get_mode():
    """TRAP 0x83 with VDI_GET_MODE returns width/height."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    LI r1, {VDI_GET_MODE * 2}
    TRAP 0x83
    ; r1=width, r2=height
    HALT
"""
    vdi, emu, out = _asm_run(asm, width=320, height=200)
    assert untag_fixnum(emu.thread.regs[1]) == 320
    assert untag_fixnum(emu.thread.regs[2]) == 200


@test("trap_vdi_draw_char", batch="phase9_trap")
def test_trap_vdi_draw_char():
    """TRAP 0x83 with VDI_DRAW_CHAR renders a character."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    ; Draw 'X' at (0,0) fg=15 bg=0
    LI r1, {VDI_DRAW_CHAR * 2}
    LI r2, {0 * 2}
    LI r3, {0 * 2}
    LI r4, {ord('X') * 2}
    LI r5, {15 * 2}
    LI r6, {0 * 2}
    TRAP 0x83
    HALT
"""
    vdi, emu, out = _asm_run(asm)
    # Some pixels in the 8x16 cell should be white (15)
    fg_count = sum(1 for y in range(CHAR_H) for x in range(CHAR_W)
                   if vdi.read_pixel(x, y) == 15)
    assert fg_count > 0, "Character 'X' should have foreground pixels"


@test("trap_vdi_draw_string_mem", batch="phase9_trap")
def test_trap_vdi_draw_string_mem():
    """TRAP 0x83 with VDI_DRAW_STRING reads string from emulator memory."""
    # We'll store "AB" at memory address 0x1000, then call VDI_DRAW_STRING
    text = "AB"
    str_addr = 0x1000
    asm = f"""\
_start:
    LI sp, 0x3FF8
    ; Draw string at (0,0) from addr={str_addr}, len={len(text)}, fg=15, bg=0
    LI r1, {VDI_DRAW_STRING * 2}
    LI r2, {0 * 2}
    LI r3, {0 * 2}
    LI r4, {str_addr * 2}
    LI r5, {len(text) * 2}
    LI r6, {15 * 2}
    LI r7, {0 * 2}
    TRAP 0x83
    HALT
"""
    emu, vdi, stdout = _make_emu_with_vdi(128, 32)
    # Store string bytes in memory
    for i, ch in enumerate(text):
        emu.mem.store_byte(str_addr + i, ord(ch))
    # Assemble and load
    asmr = Assembler()
    words = asmr.assemble_to_words(asm)
    emu.mem.load_instructions(0, words)
    for t in emu.threads:
        t.pc = 0
    emu.run(max_instructions=5000)
    # Check both character cells have foreground pixels
    a_fg = sum(1 for y in range(CHAR_H) for x in range(CHAR_W)
               if vdi.read_pixel(x, y) == 15)
    b_fg = sum(1 for y in range(CHAR_H) for x in range(CHAR_W, 2 * CHAR_W)
               if vdi.read_pixel(x, y) == 15)
    assert a_fg > 0, "'A' should have foreground pixels"
    assert b_fg > 0, "'B' should have foreground pixels"


@test("trap_vdi_event_read", batch="phase9_trap")
def test_trap_vdi_event_read():
    """TRAP 0x83 with VDI_READ_EVENT returns injected events."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    LI r1, {VDI_READ_EVENT * 2}
    TRAP 0x83
    ; r1=event_type, r2=data1, r3=data2
    HALT
"""
    emu, vdi, stdout = _make_emu_with_vdi()
    # Inject a key-down event before running
    vdi.push_event(EVT_KEY_DOWN, 65, 0)  # 'A' key
    asmr = Assembler()
    words = asmr.assemble_to_words(asm)
    emu.mem.load_instructions(0, words)
    for t in emu.threads:
        t.pc = 0
    emu.run(max_instructions=5000)
    assert untag_fixnum(emu.thread.regs[1]) == EVT_KEY_DOWN
    assert untag_fixnum(emu.thread.regs[2]) == 65
    assert untag_fixnum(emu.thread.regs[3]) == 0


@test("trap_vdi_no_event", batch="phase9_trap")
def test_trap_vdi_no_event():
    """VDI_READ_EVENT returns EVT_NONE when queue is empty."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    LI r1, {VDI_READ_EVENT * 2}
    TRAP 0x83
    HALT
"""
    vdi, emu, out = _asm_run(asm)
    assert untag_fixnum(emu.thread.regs[1]) == EVT_NONE


@test("trap_vdi_grad_rect", batch="phase9_trap")
def test_trap_vdi_grad_rect():
    """TRAP 0x83 with VDI_GRAD_RECT fills a gradient rectangle."""
    c1 = rgb(255, 0, 0)   # 0xFF0000
    c2 = rgb(0, 0, 255)   # 0x0000FF
    # LI auto-expands to LI32 for large values
    asm = f"""\
_start:
    LI sp, 0x3FF8
    ; VDI_GRAD_RECT: r1=func(13), r2=x, r3=y, r4=w, r5=h, r6=c1, r7=c2, r8=dir
    LI r1, {VDI_GRAD_RECT * 2}
    LI r2, {0 * 2}
    LI r3, {0 * 2}
    LI r4, {16 * 2}
    LI r5, {16 * 2}
    LI r6, {c1 * 2}
    LI r7, {c2 * 2}
    LI r8, {GRAD_VERTICAL * 2}
    TRAP 0x83
    HALT
"""
    vdi, emu, out = _asm_run(asm)
    # Top should be red, bottom should be blue
    assert vdi.read_pixel(0, 0) == c1
    assert vdi.read_pixel(0, 15) == c2


@test("trap_vdi_scroll_region", batch="phase9_trap")
def test_trap_vdi_scroll_region():
    """TRAP 0x83 with VDI_SCROLL scrolls a region."""
    # First fill, then scroll
    asm = f"""\
_start:
    LI sp, 0x3FF8
    ; Fill 0,0,32,32 with color 5
    LI r1, {VDI_FILL_RECT * 2}
    LI r2, 0
    LI r3, 0
    LI r4, {32 * 2}
    LI r5, {32 * 2}
    LI r6, {5 * 2}
    TRAP 0x83
    ; Scroll entire 32x32 region down by 8
    LI r1, {VDI_SCROLL * 2}
    LI r2, 0
    LI r3, 0
    LI r4, {32 * 2}
    LI r5, {32 * 2}
    LI r6, 0
    LI r7, {8 * 2}
    TRAP 0x83
    HALT
"""
    vdi, emu, out = _asm_run(asm)
    # Top 8 rows should be cleared
    for y in range(8):
        assert vdi.read_pixel(16, y) == 0, f"row {y} should be cleared"
    # Rows 8+ should have color 5
    assert vdi.read_pixel(16, 8) == 5
    assert vdi.read_pixel(16, 31) == 5


@test("trap_vdi_no_device", batch="phase9_trap")
def test_trap_vdi_no_device():
    """TRAP 0x83 without VDI attached returns -1."""
    asm = f"""\
_start:
    LI sp, 0x3FF8
    LI r1, {VDI_FILL_RECT * 2}
    LI r2, 0
    LI r3, 0
    LI r4, {10 * 2}
    LI r5, {10 * 2}
    LI r6, {7 * 2}
    TRAP 0x83
    HALT
"""
    # Create emulator WITHOUT VDI
    stdout = io.StringIO()
    emu = Emulator(mem_size=1024 * 1024, stdout=stdout)
    asmr = Assembler()
    words = asmr.assemble_to_words(asm)
    emu.mem.load_instructions(0, words)
    for t in emu.threads:
        t.pc = 0
    emu.run(max_instructions=1000)
    # r1 should be tag_fixnum(-1)
    assert untag_fixnum(emu.thread.regs[1]) == -1
