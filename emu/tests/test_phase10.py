"""Phase 10 tests — Crystal Desktop Window Manager.

Tests the AES window manager, window operations (create, close,
raise, lower, move, resize), menu bar, event dispatch, and
the built-in crystallites (terminal, calculator, clock) — all
headless using the VDI framebuffer.
"""

from lm1.testing.harness import test
from lm1.vdi import (
    VDI, CHAR_W, CHAR_H,
    EVT_NONE, EVT_KEY_DOWN, EVT_KEY_UP,
    EVT_MOUSE_MOVE, EVT_MOUSE_DOWN, EVT_MOUSE_UP, EVT_QUIT,
)
from lm1.desktop import (
    AES, Window, Colors, Menu, MenuItem,
    TITLE_BAR_H, BORDER_W, MENU_BAR_H, MIN_WIN_W, MIN_WIN_H,
    WIN_CLOSEABLE, WIN_MOVEABLE, WIN_RESIZABLE,
    TerminalCrystallite, ClockCrystallite, CalculatorCrystallite,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _make_aes(w=320, h=240):
    """Create a headless AES for testing."""
    vdi = VDI(width=w, height=h, headless=True)
    return AES(vdi)


# -------------------------------------------------------------------
# Window management
# -------------------------------------------------------------------

@test("desktop_create_window", batch="phase10")
def test_create_window():
    aes = _make_aes()
    win = aes.create_window("Test", 10, 30, 200, 150)
    assert win.wid == 1
    assert win.title == "Test"
    assert win.x == 10 and win.y == 30
    assert win.w == 200 and win.h == 150
    assert aes._focused is win
    assert win in aes._windows


@test("desktop_multiple_windows", batch="phase10")
def test_multiple_windows():
    aes = _make_aes()
    w1 = aes.create_window("Win1", 10, 30, 100, 80)
    w2 = aes.create_window("Win2", 50, 60, 100, 80)
    w3 = aes.create_window("Win3", 90, 90, 100, 80)
    assert len(aes._windows) == 3
    # Last created is focused and on top
    assert aes._focused is w3
    assert aes._windows[-1] is w3


@test("desktop_raise_window", batch="phase10")
def test_raise_window():
    aes = _make_aes()
    w1 = aes.create_window("Win1", 10, 30, 100, 80)
    w2 = aes.create_window("Win2", 50, 60, 100, 80)
    # w2 is on top. Raise w1
    aes.raise_window(w1)
    assert aes._windows[-1] is w1
    assert aes._focused is w1


@test("desktop_lower_window", batch="phase10")
def test_lower_window():
    aes = _make_aes()
    w1 = aes.create_window("Win1", 10, 30, 100, 80)
    w2 = aes.create_window("Win2", 50, 60, 100, 80)
    # Lower w2 to bottom
    aes.lower_window(w2)
    assert aes._windows[0] is w2
    assert aes._windows[-1] is w1


@test("desktop_close_window", batch="phase10")
def test_close_window():
    aes = _make_aes()
    w1 = aes.create_window("Win1", 10, 30, 100, 80)
    w2 = aes.create_window("Win2", 50, 60, 100, 80)
    aes.close_window(w2)
    assert w2 not in aes._windows
    assert aes._focused is w1
    assert len(aes._windows) == 1


@test("desktop_close_cancel", batch="phase10")
def test_close_cancel():
    """Window can cancel its own close."""
    aes = _make_aes()
    win = aes.create_window("Sticky", 10, 30, 100, 80,
                             on_close=lambda w: False)
    aes.close_window(win)
    # Close was cancelled — window should still exist
    assert win in aes._windows


@test("desktop_find_window_at", batch="phase10")
def test_find_window_at():
    aes = _make_aes()
    w1 = aes.create_window("Win1", 10, 30, 100, 80)
    w2 = aes.create_window("Win2", 50, 60, 100, 80)
    # Point inside w2 only
    found = aes.find_window_at(120, 100)
    assert found is w2
    # Point inside w1 only (not overlapping w2)
    found = aes.find_window_at(15, 35)
    assert found is w1
    # Point outside all windows
    found = aes.find_window_at(5, 5)
    assert found is None


@test("desktop_client_rect", batch="phase10")
def test_client_rect():
    aes = _make_aes()
    win = aes.create_window("Test", 10, 30, 200, 150)
    cx, cy, cw, ch = win.client_rect()
    assert cx == 10 + BORDER_W
    assert cy == 30 + TITLE_BAR_H
    assert cw == 200 - 2 * BORDER_W
    assert ch == 150 - TITLE_BAR_H - BORDER_W


# -------------------------------------------------------------------
# Rendering
# -------------------------------------------------------------------

@test("desktop_redraw_smoke", batch="phase10")
def test_redraw_smoke():
    """AES.redraw() runs without error and produces non-blank fb."""
    aes = _make_aes()
    aes.create_window("Test", 10, 30, 200, 150)
    aes.redraw()
    # Framebuffer should have multiple different colors across all rows
    fb = aes.vdi.fb
    unique = set(fb)
    assert len(unique) > 1, "Framebuffer looks blank after redraw"


@test("desktop_window_content_callback", batch="phase10")
def test_window_content_callback():
    """on_redraw callback is invoked during redraw."""
    aes = _make_aes()
    called = [False]

    def my_draw(vdi, win):
        called[0] = True
        cx, cy, cw, ch = win.client_rect()
        vdi.fill_rect(cx, cy, cw, ch, Colors.RED)

    aes.create_window("Custom", 10, 30, 200, 150, on_redraw=my_draw)
    aes.redraw()
    assert called[0], "on_redraw callback was not invoked"


# -------------------------------------------------------------------
# Event dispatch
# -------------------------------------------------------------------

@test("desktop_focus_on_click", batch="phase10")
def test_focus_on_click():
    """Clicking a window raises and focuses it."""
    aes = _make_aes(400, 300)
    w1 = aes.create_window("Win1", 10, 30, 100, 80)
    w2 = aes.create_window("Win2", 150, 30, 100, 80)
    assert aes._focused is w2
    # Click on w1's title bar
    aes.handle_event(EVT_MOUSE_DOWN, 50, 35)
    assert aes._focused is w1


@test("desktop_close_via_button", batch="phase10")
def test_close_via_button():
    """Clicking the close button closes the window."""
    aes = _make_aes()
    win = aes.create_window("Closeme", 10, 30, 200, 150)
    # Close button is at (win.x + 3, win.y + 2) approximately
    aes.handle_event(EVT_MOUSE_DOWN, 15, 35)
    assert win not in aes._windows


@test("desktop_drag_window", batch="phase10")
def test_drag_window():
    """Dragging a title bar moves the window."""
    aes = _make_aes(400, 300)
    win = aes.create_window("Draggable", 50, 50, 200, 150)
    # Mouse down on title bar
    aes.handle_event(EVT_MOUSE_DOWN, 100, 55)
    assert aes._dragging is win
    # Move
    aes.handle_event(EVT_MOUSE_MOVE, 150, 75)
    assert win.x != 50 or win.y != 50, "Window should have moved"
    # Release
    aes.handle_event(EVT_MOUSE_UP, 150, 75)
    assert aes._dragging is None


@test("desktop_resize_window", batch="phase10")
def test_resize_window():
    """Dragging the resize grip resizes the window."""
    aes = _make_aes(400, 300)
    win = aes.create_window("Resizable", 10, 30, 200, 150)
    orig_w, orig_h = win.w, win.h
    # Mouse down on resize grip (bottom-right corner)
    grip_x = win.x + win.w - 5
    grip_y = win.y + win.h - 5
    aes.handle_event(EVT_MOUSE_DOWN, grip_x, grip_y)
    assert aes._resizing is win
    # Drag to enlarge
    aes.handle_event(EVT_MOUSE_MOVE, grip_x + 30, grip_y + 20)
    assert win.w > orig_w or win.h > orig_h, "Window should have resized"
    aes.handle_event(EVT_MOUSE_UP, grip_x + 30, grip_y + 20)
    assert aes._resizing is None


@test("desktop_key_dispatch", batch="phase10")
def test_key_dispatch():
    """Key events dispatch to the focused window's on_key callback."""
    aes = _make_aes()
    keys_received = []

    def my_key(win, key, mod):
        keys_received.append(key)

    aes.create_window("KeyWin", 10, 30, 200, 150, on_key=my_key)
    aes.handle_event(EVT_KEY_DOWN, 65, 0)  # 'A'
    assert keys_received == [65]


@test("desktop_quit_event", batch="phase10")
def test_quit_event():
    """EVT_QUIT sets _running to False."""
    aes = _make_aes()
    assert aes._running
    aes.handle_event(EVT_QUIT, 0, 0)
    assert not aes._running


# -------------------------------------------------------------------
# Menu bar
# -------------------------------------------------------------------

@test("desktop_menu_bar", batch="phase10")
def test_menu_bar():
    """Menu bar is drawn and can be hit-tested."""
    aes = _make_aes()
    # System menu should always be present
    menus = aes._get_active_menus()
    assert len(menus) >= 1
    assert menus[0].label == "Crystal"

    # Hit test on the first menu label
    idx = aes._menu_hit_test(12, 5)
    assert idx == 0

    # Hit test outside menu bar
    idx = aes._menu_hit_test(12, MENU_BAR_H + 5)
    assert idx == -1


@test("desktop_menu_open_close", batch="phase10")
def test_menu_open_close():
    """Clicking a menu label opens it; clicking again closes."""
    aes = _make_aes()
    # Click on Crystal menu
    aes.handle_event(EVT_MOUSE_DOWN, 12, 5)
    assert aes._menu_open == 0
    # Click again to close
    aes.handle_event(EVT_MOUSE_DOWN, 12, 5)
    assert aes._menu_open == -1


# -------------------------------------------------------------------
# Built-in crystallites
# -------------------------------------------------------------------

@test("desktop_terminal_crystallite", batch="phase10")
def test_terminal_crystallite():
    """Terminal crystallite can receive text and execute commands."""
    aes = _make_aes(640, 480)
    term = TerminalCrystallite(aes)
    assert term.win in aes._windows

    # Simulate typing "hello" (without pygame, pass ASCII codes)
    for ch in "hello":
        term._on_key(term.win, ord(ch), 0)
    assert term.input_buf == "hello"

    # Fake Enter key (13)
    term._execute("hello")
    assert any("Hello" in line for line in term.lines)


@test("desktop_terminal_lisp_eval", batch="phase10")
def test_terminal_lisp_eval():
    """Terminal can evaluate simple Lisp expressions."""
    aes = _make_aes(640, 480)
    term = TerminalCrystallite(aes)
    term._execute("(+ 1 2)")
    assert any("3" in line for line in term.lines)

    term._execute("(fact 5)")
    assert any("120" in line for line in term.lines)


@test("desktop_calculator", batch="phase10")
def test_calculator():
    """Calculator processes basic arithmetic."""
    aes = _make_aes(640, 480)
    calc = CalculatorCrystallite(aes)
    assert calc.win in aes._windows

    # 7 + 3 = 10
    calc._press('7')
    assert calc.display == "7"
    calc._press('+')
    calc._press('3')
    assert calc.display == "3"
    calc._press('=')
    assert calc.display == "10"

    # Clear
    calc._press('C')
    assert calc.display == "0"


@test("desktop_calculator_multiply", batch="phase10")
def test_calculator_multiply():
    """Calculator handles multiplication chain."""
    aes = _make_aes(640, 480)
    calc = CalculatorCrystallite(aes)
    # 6 * 7 = 42
    calc._press('6')
    calc._press('*')
    calc._press('7')
    calc._press('=')
    assert calc.display == "42"


@test("desktop_clock", batch="phase10")
def test_clock():
    """Clock crystallite renders time."""
    aes = _make_aes(640, 480)
    clock = ClockCrystallite(aes)
    assert clock.win in aes._windows
    # Redraw should set _last_time
    aes.redraw()
    assert clock._last_time != ""


@test("desktop_full_desktop_redraw", batch="phase10")
def test_full_desktop_redraw():
    """Full desktop with all crystallites redraws correctly."""
    aes = _make_aes(640, 480)
    TerminalCrystallite(aes)
    ClockCrystallite(aes)
    CalculatorCrystallite(aes)
    aes.redraw()
    # Should not crash and fb should have content
    fb = aes.vdi.fb
    unique = set(fb)
    assert len(unique) > 2, "Desktop looks blank after full redraw"
