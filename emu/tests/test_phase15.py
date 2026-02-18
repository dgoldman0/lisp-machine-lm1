"""Phase 15 tests — Crystal Desktop: Expression-Tree Compositor.

Tests the Crystal expression-tree desktop: portals, panes, lenses,
the crystal bar, scrapbook, focus model, input routing, and
tree manipulation — all headless (no pygame display).
"""

from lm1.testing.harness import test
from lm1.vdi import VDI, EVT_MOUSE_DOWN, EVT_KEY_DOWN, EVT_QUIT, BG_TRANSPARENT
from lm1.crystal import (
    Crystal, Portal, Pane, Rect, SplitDir,
    Scrapbook, ScrapEntry, BarItem, BarClock,
    C, BAR_H, DIVIDER_W, PORTAL_LABEL_H, PORTAL_PAD,
    InspectLens, PrettyLens, TerminalLens, EditorLens,
    TreeLens, StreamLens, TimeLens, InteractiveLens,
    get_lens, register_lens,
    _build_default_crystal, launch_crystal,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _make_crystal(w=640, h=480):
    """Create a headless Crystal desktop for testing."""
    vdi = VDI(width=w, height=h, headless=True)
    return Crystal(vdi)


def _make_crystal_with_layout(w=640, h=480):
    """Create a headless Crystal with default layout."""
    crystal = _make_crystal(w, h)
    _build_default_crystal(crystal)
    return crystal


# ===================================================================
# Rect
# ===================================================================

@test("crystal_rect_contains", batch="phase15")
def test_rect_contains():
    r = Rect(10, 20, 100, 50)
    assert r.contains(10, 20),   "top-left inclusive"
    assert r.contains(50, 40),   "interior"
    assert r.contains(109, 69),  "bottom-right inclusive"
    assert not r.contains(110, 20), "right edge exclusive"
    assert not r.contains(10, 70),  "bottom edge exclusive"
    assert not r.contains(9, 20),   "left edge exclusive"


@test("crystal_rect_shrink", batch="phase15")
def test_rect_shrink():
    r = Rect(10, 20, 100, 50)
    s = r.shrink(t=5, r=3, b=2, l=4)
    assert s.x == 14
    assert s.y == 25
    assert s.w == 93      # 100 - 4 - 3
    assert s.h == 43      # 50 - 5 - 2


@test("crystal_rect_split_v", batch="phase15")
def test_rect_split_v():
    r = Rect(0, 0, 200, 100)
    left, right = r.split_v(0.5)
    assert left.x == 0
    assert left.w + DIVIDER_W + right.w == r.w
    assert left.h == r.h
    assert right.h == r.h


@test("crystal_rect_split_h", batch="phase15")
def test_rect_split_h():
    r = Rect(0, 0, 200, 100)
    top, bottom = r.split_h(0.5)
    assert top.y == 0
    assert top.h + DIVIDER_W + bottom.h == r.h
    assert top.w == r.w
    assert bottom.w == r.w


@test("crystal_rect_dividers", batch="phase15")
def test_rect_dividers():
    r = Rect(0, 0, 200, 100)
    dv = r.divider_v(0.5)
    assert dv.w == DIVIDER_W
    assert dv.h == r.h
    dh = r.divider_h(0.5)
    assert dh.h == DIVIDER_W
    assert dh.w == r.w


# ===================================================================
# Portal
# ===================================================================

@test("crystal_create_portal", batch="phase15")
def test_create_portal():
    crystal = _make_crystal()
    p = crystal.create_portal("hello", lens_name='pretty', label='Test')
    assert p.target == "hello"
    assert p.lens_name == 'pretty'
    assert p.label == 'Test'
    assert p.pid >= 1
    assert '_crystal' in p.state  # injected ref


@test("crystal_portal_content_rect", batch="phase15")
def test_portal_content_rect():
    p = Portal(target=None, rect=Rect(10, 20, 200, 150))
    cr = p.content_rect()
    assert cr.y == 20 + PORTAL_LABEL_H
    assert cr.x == 10 + PORTAL_PAD
    assert cr.w == 200 - 2 * PORTAL_PAD
    assert cr.h == 150 - PORTAL_LABEL_H - PORTAL_PAD


# ===================================================================
# Pane — tree structure
# ===================================================================

@test("crystal_pane_leaf", batch="phase15")
def test_pane_leaf():
    p = Portal(target=42)
    pane = Pane(portal=p)
    assert pane.is_leaf()
    assert pane.all_portals() == [p]


@test("crystal_pane_split", batch="phase15")
def test_pane_split():
    p1 = Portal(target=1)
    p2 = Portal(target=2)
    left = Pane(portal=p1)
    right = Pane(portal=p2)
    root = Pane(split=SplitDir.VERTICAL, ratio=0.5,
                children=[left, right])
    assert not root.is_leaf()
    portals = root.all_portals()
    assert len(portals) == 2
    assert p1 in portals and p2 in portals


@test("crystal_pane_tabs", batch="phase15")
def test_pane_tabs():
    p1, p2, p3 = Portal(target='a'), Portal(target='b'), Portal(target='c')
    tab_pane = Pane(tab_mode=True, active_tab=1,
                    children=[Pane(portal=p1), Pane(portal=p2), Pane(portal=p3)])
    portals = tab_pane.all_portals()
    assert len(portals) == 3
    assert set(p.target for p in portals) == {'a', 'b', 'c'}


@test("crystal_pane_deep_tree", batch="phase15")
def test_pane_deep_tree():
    """Three-level nested panes."""
    p1, p2, p3 = Portal(target='x'), Portal(target='y'), Portal(target='z')
    inner = Pane(split=SplitDir.HORIZONTAL, ratio=0.5,
                 children=[Pane(portal=p2), Pane(portal=p3)])
    root = Pane(split=SplitDir.VERTICAL, ratio=0.3,
                children=[Pane(portal=p1), inner])
    portals = root.all_portals()
    assert len(portals) == 3
    assert {p.target for p in portals} == {'x', 'y', 'z'}


# ===================================================================
# Crystal — core operations
# ===================================================================

@test("crystal_init", batch="phase15")
def test_crystal_init():
    crystal = _make_crystal()
    assert crystal.vdi is not None
    assert crystal.vfs is not None
    assert crystal.scrapbook is not None
    assert crystal._running


@test("crystal_set_root_portal", batch="phase15")
def test_set_root_portal():
    crystal = _make_crystal()
    p = crystal.set_root_portal("test data", 'inspect', 'Root')
    assert crystal.root.portal is p
    assert crystal._focused_portal is p
    assert p.target == "test data"
    assert p.label == 'Root'


@test("crystal_split_vertical", batch="phase15")
def test_split_vertical():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("left", 'inspect', 'Left')
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5,
                               new_target="right", new_lens='inspect',
                               new_label='Right')
    assert crystal.root.split == SplitDir.VERTICAL
    assert len(crystal.root.children) == 2
    portals = crystal.root.all_portals()
    assert len(portals) == 2
    assert p1 in portals and p2 in portals
    assert p2.target == "right"
    assert p2.label == "Right"


@test("crystal_split_horizontal", batch="phase15")
def test_split_horizontal():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("top", 'inspect')
    p2 = crystal.split_portal(p1, SplitDir.HORIZONTAL, 0.3,
                               new_target="bottom")
    assert crystal.root.split == SplitDir.HORIZONTAL
    assert crystal.root.ratio == 0.3


@test("crystal_add_tab", batch="phase15")
def test_add_tab():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("tab1", 'inspect', 'Tab1')
    p2 = crystal.add_tab(p1, target="tab2", lens_name='pretty', label='Tab2')
    assert crystal.root.tab_mode
    assert len(crystal.root.children) == 2
    assert crystal.root.active_tab == 1  # newly added tab is active
    assert p2.lens_name == 'pretty'


@test("crystal_add_multiple_tabs", batch="phase15")
def test_add_multiple_tabs():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("t1", label='T1')
    p2 = crystal.add_tab(p1, target="t2", label='T2')
    # add_tab(p1,...) finds the leaf pane containing p1 (now inside
    # root's tab group) and nests another tab group there.
    p3 = crystal.add_tab(p1, target="t3", label='T3')
    # All 3 portals should be reachable
    portals = crystal.root.all_portals()
    assert len(portals) == 3
    assert {p.target for p in portals} == {'t1', 't2', 't3'}


@test("crystal_close_split_portal", batch="phase15")
def test_close_split_portal():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("a", 'inspect', 'A')
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5,
                               new_target="b", new_label='B')
    crystal.close_portal(p2)
    # Root should collapse back to single portal
    assert crystal.root.portal is p1
    assert crystal.root.split is None
    assert crystal.root.children == []


@test("crystal_close_root_portal", batch="phase15")
def test_close_root_portal():
    crystal = _make_crystal()
    p = crystal.set_root_portal("x", 'inspect')
    crystal.close_portal(p)
    assert crystal.root.portal is None
    assert crystal._focused_portal is None


@test("crystal_close_updates_focus", batch="phase15")
def test_close_updates_focus():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("a")
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5, new_target="b")
    crystal._focused_portal = p2
    crystal.close_portal(p2)
    # Focus should move to remaining portal
    assert crystal._focused_portal is p1


@test("crystal_close_tab", batch="phase15")
def test_close_tab():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("t1", label='T1')
    p2 = crystal.add_tab(p1, target="t2", label='T2')
    p3 = crystal.add_tab(p2, target="t3", label='T3')
    crystal.close_portal(p2)
    assert len(crystal.root.children) == 2
    portals = crystal.root.all_portals()
    assert len(portals) == 2


# ===================================================================
# Layout
# ===================================================================

@test("crystal_layout_single", batch="phase15")
def test_layout_single():
    crystal = _make_crystal(200, 100)
    p = crystal.set_root_portal("data")
    crystal._layout()
    assert p.rect.w == 200
    assert p.rect.h == 100 - BAR_H


@test("crystal_layout_split_v", batch="phase15")
def test_layout_split_v():
    crystal = _make_crystal(400, 300)
    p1 = crystal.set_root_portal("left")
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5)
    crystal._layout()
    assert p1.rect.w > 0 and p2.rect.w > 0
    assert p1.rect.h > 0 and p2.rect.h > 0
    assert p1.rect.w + DIVIDER_W + p2.rect.w == 400


@test("crystal_layout_split_h", batch="phase15")
def test_layout_split_h():
    crystal = _make_crystal(400, 300)
    p1 = crystal.set_root_portal("top")
    p2 = crystal.split_portal(p1, SplitDir.HORIZONTAL, 0.5)
    crystal._layout()
    work_h = 300 - BAR_H
    assert p1.rect.h + DIVIDER_W + p2.rect.h == work_h


@test("crystal_layout_default", batch="phase15")
def test_layout_default():
    crystal = _make_crystal_with_layout(640, 480)
    crystal._layout()
    portals = crystal.root.all_portals()
    for p in portals:
        assert p.rect.w > 0, f"portal {p.label!r} has zero width"
        assert p.rect.h > 0, f"portal {p.label!r} has zero height"


# ===================================================================
# Rendering
# ===================================================================

@test("crystal_redraw_basic", batch="phase15")
def test_redraw_basic():
    crystal = _make_crystal()
    crystal.set_root_portal("test")
    crystal.redraw()
    # Bar area should have bar background color
    vdi = crystal.vdi
    bar_y = vdi.height - BAR_H + 5
    pixel = vdi.read_pixel(vdi.width // 2, bar_y)
    assert pixel != 0xFFFFFF, "bar area should not be white"


@test("crystal_redraw_draws_portal_edge", batch="phase15")
def test_redraw_draws_portal_edge():
    crystal = _make_crystal(200, 150)
    p = crystal.set_root_portal("data", label='Focused')
    crystal._focused_portal = p
    crystal.redraw()
    # Focused portal edge should be FOCUS_GLOW color
    pixel = crystal.vdi.read_pixel(p.rect.x, p.rect.y)
    assert pixel == C.FOCUS_GLOW, \
        f"expected focus glow 0x{C.FOCUS_GLOW:06X}, got 0x{pixel:06X}"


@test("crystal_redraw_unfocused_edge", batch="phase15")
def test_redraw_unfocused_edge():
    crystal = _make_crystal(400, 300)
    p1 = crystal.set_root_portal("left")
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5,
                               new_target="right")
    crystal._focused_portal = p1
    crystal.redraw()
    # p2 edge should be PORTAL_EDGE (not focus glow)
    pixel = crystal.vdi.read_pixel(p2.rect.x, p2.rect.y)
    assert pixel == C.PORTAL_EDGE


@test("crystal_redraw_divider", batch="phase15")
def test_redraw_divider():
    crystal = _make_crystal(400, 300)
    p1 = crystal.set_root_portal("left")
    crystal.split_portal(p1, SplitDir.VERTICAL, 0.5)
    crystal.redraw()
    # Divider should be at the boundary
    dr = crystal.root.rect.divider_v(0.5)
    pixel = crystal.vdi.read_pixel(dr.x, dr.y + dr.h // 2)
    assert pixel == C.DIVIDER


@test("crystal_redraw_default_layout", batch="phase15")
def test_redraw_default_layout():
    """Full default layout renders without error."""
    crystal = _make_crystal_with_layout(800, 600)
    crystal.redraw()
    vdi = crystal.vdi
    # Verify framebuffer has non-trivial content
    non_zero = sum(1 for i in range(0, len(vdi.fb), 500) if vdi.fb[i] != 0)
    assert non_zero > 10, "should have substantial pixel content"


# ===================================================================
# Focus
# ===================================================================

@test("crystal_focus_cycle", batch="phase15")
def test_focus_cycle():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("a", label='A')
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5,
                               new_target="b", new_label='B')
    initial = crystal._focused_portal
    crystal._cycle_focus(1)
    assert crystal._focused_portal is not initial
    crystal._cycle_focus(1)
    assert crystal._focused_portal is initial  # wraps around


@test("crystal_focus_cycle_many", batch="phase15")
def test_focus_cycle_many():
    crystal = _make_crystal_with_layout()
    portals = crystal.root.all_portals()
    assert len(portals) >= 3
    seen = set()
    for _ in range(len(portals)):
        seen.add(crystal._focused_portal.pid)
        crystal._cycle_focus(1)
    assert len(seen) == len(portals), "should visit every portal"


@test("crystal_focus_reverse", batch="phase15")
def test_focus_reverse():
    crystal = _make_crystal()
    p1 = crystal.set_root_portal("a")
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5, new_target="b")
    crystal._focused_portal = p1
    crystal._cycle_focus(-1)
    assert crystal._focused_portal is p2
    crystal._cycle_focus(-1)
    assert crystal._focused_portal is p1


# ===================================================================
# Lens registry
# ===================================================================

@test("crystal_lens_registry_builtins", batch="phase15")
def test_lens_registry_builtins():
    for name in ('inspect', 'pretty', 'terminal', 'editor',
                 'tree', 'stream', 'time', 'interactive'):
        lens = get_lens(name)
        assert lens is not None, f"lens '{name}' not in registry"
        assert lens.name == name


@test("crystal_lens_registry_default", batch="phase15")
def test_lens_registry_default():
    """get_lens for unknown name falls back to inspect."""
    lens = get_lens('nonexistent')
    assert lens is not None
    assert lens.name == 'inspect'


@test("crystal_register_custom_lens", batch="phase15")
def test_register_custom_lens():
    class MyLens:
        name = '_test_custom'
        def render(self, vdi, target, rect, focused, state): pass
        def on_key(self, target, key, mod, state): return False
        def on_click(self, target, rx, ry, button, state): return False
        def on_mouse_move(self, target, rx, ry, state): return False
    register_lens(MyLens())
    assert get_lens('_test_custom').name == '_test_custom'


# ===================================================================
# InspectLens
# ===================================================================

@test("crystal_inspect_dict", batch="phase15")
def test_inspect_dict():
    vdi = VDI(200, 100, headless=True)
    lens = InspectLens()
    lens.render(vdi, {'a': 1, 'b': 'hi'}, Rect(0, 0, 200, 100), True, {})
    has_px = any(vdi.fb[i] != 0 for i in range(0, len(vdi.fb), 100))
    assert has_px, "should render dict content"


@test("crystal_inspect_list", batch="phase15")
def test_inspect_list():
    vdi = VDI(200, 100, headless=True)
    lens = InspectLens()
    lens.render(vdi, [1, 2, 3], Rect(0, 0, 200, 100), True, {})
    has_px = any(vdi.fb[i] != 0 for i in range(0, len(vdi.fb), 100))
    assert has_px


@test("crystal_inspect_string", batch="phase15")
def test_inspect_string():
    vdi = VDI(200, 100, headless=True)
    lens = InspectLens()
    lens.render(vdi, "hello world", Rect(0, 0, 200, 100), True, {})
    has_px = any(vdi.fb[i] != 0 for i in range(0, len(vdi.fb), 100))
    assert has_px


@test("crystal_inspect_int", batch="phase15")
def test_inspect_int():
    vdi = VDI(200, 100, headless=True)
    lens = InspectLens()
    lens.render(vdi, 42, Rect(0, 0, 200, 100), True, {})
    has_px = any(vdi.fb[i] != 0 for i in range(0, len(vdi.fb), 100))
    assert has_px


# ===================================================================
# PrettyLens
# ===================================================================

@test("crystal_pretty_sexp", batch="phase15")
def test_pretty_sexp():
    vdi = VDI(300, 100, headless=True)
    lens = PrettyLens()
    target = [1, 2, [3, 4]]
    lens.render(vdi, target, Rect(0, 0, 300, 100), True, {})
    has_px = any(vdi.fb[i] != 0 for i in range(0, len(vdi.fb), 100))
    assert has_px


@test("crystal_pretty_format", batch="phase15")
def test_pretty_format():
    lens = PrettyLens()
    assert lens._pretty(None) == 'nil'
    assert lens._pretty(42) == '42'
    assert lens._pretty("hi") == '"hi"'
    assert lens._pretty([]) == '()'
    assert '(' in lens._pretty([1, 2, 3])


@test("crystal_pretty_scroll", batch="phase15")
def test_pretty_scroll():
    import pygame
    lens = PrettyLens()
    state = {'scroll_y': 0}
    assert lens.on_key(None, pygame.K_DOWN, 0, state)
    assert state['scroll_y'] == 1
    assert lens.on_key(None, pygame.K_UP, 0, state)
    assert state['scroll_y'] == 0
    assert lens.on_key(None, pygame.K_PAGEDOWN, 0, state)
    assert state['scroll_y'] == 10


# ===================================================================
# TerminalLens
# ===================================================================

@test("crystal_terminal_typing", batch="phase15")
def test_terminal_typing():
    import pygame
    lens = TerminalLens()
    state = {'lines': [], 'input_buf': ''}
    lens.on_key(None, ord('h'), 0, state)
    lens.on_key(None, ord('i'), 0, state)
    assert state['input_buf'] == 'hi'


@test("crystal_terminal_enter", batch="phase15")
def test_terminal_enter():
    import pygame
    lens = TerminalLens()
    state = {'lines': [], 'input_buf': ''}
    for ch in 'help':
        lens.on_key(None, ord(ch), 0, state)
    lens.on_key(None, pygame.K_RETURN, 0, state)
    assert state['input_buf'] == ''
    assert len(state['lines']) > 1  # help output


@test("crystal_terminal_backspace", batch="phase15")
def test_terminal_backspace():
    import pygame
    lens = TerminalLens()
    state = {'lines': [], 'input_buf': 'abc'}
    lens.on_key(None, pygame.K_BACKSPACE, 0, state)
    assert state['input_buf'] == 'ab'


@test("crystal_terminal_escape", batch="phase15")
def test_terminal_escape():
    import pygame
    lens = TerminalLens()
    state = {'lines': [], 'input_buf': 'abc'}
    lens.on_key(None, pygame.K_ESCAPE, 0, state)
    assert state['input_buf'] == ''


@test("crystal_terminal_lisp_add", batch="phase15")
def test_terminal_lisp_add():
    import pygame
    lens = TerminalLens()
    state = {'lines': [], 'input_buf': ''}
    for ch in '(+ 2 3)':
        lens.on_key(None, ord(ch), 0, state)
    lens.on_key(None, pygame.K_RETURN, 0, state)
    assert any('5' in line for line in state['lines']), \
        f"expected '5' in output: {state['lines']}"


@test("crystal_terminal_lisp_list", batch="phase15")
def test_terminal_lisp_list():
    import pygame
    lens = TerminalLens()
    state = {'lines': [], 'input_buf': ''}
    for ch in '(list 1 2 3)':
        lens.on_key(None, ord(ch), 0, state)
    lens.on_key(None, pygame.K_RETURN, 0, state)
    assert any('(1 2 3)' in line for line in state['lines'])


@test("crystal_terminal_clear", batch="phase15")
def test_terminal_clear():
    import pygame
    lens = TerminalLens()
    state = {'lines': ['old output'], 'input_buf': ''}
    for ch in 'clear':
        lens.on_key(None, ord(ch), 0, state)
    lens.on_key(None, pygame.K_RETURN, 0, state)
    assert len(state['lines']) == 0


@test("crystal_terminal_time", batch="phase15")
def test_terminal_time():
    import pygame, time
    lens = TerminalLens()
    state = {'lines': [], 'input_buf': ''}
    for ch in 'time':
        lens.on_key(None, ord(ch), 0, state)
    lens.on_key(None, pygame.K_RETURN, 0, state)
    # Output should contain a date or time string
    assert any(':' in line and '-' in line for line in state['lines'])


@test("crystal_terminal_vfs_ls", batch="phase15")
def test_terminal_vfs_ls():
    import pygame
    from lm1.vfs import VFS
    lens = TerminalLens()
    vfs = VFS()
    vfs.populate_default()
    state = {'lines': [], 'input_buf': '', '_vfs': vfs, '_cwd': '/'}
    for ch in 'ls':
        lens.on_key(None, ord(ch), 0, state)
    lens.on_key(None, pygame.K_RETURN, 0, state)
    assert len(state['lines']) > 1  # should list files


@test("crystal_terminal_render", batch="phase15")
def test_terminal_render():
    vdi = VDI(300, 200, headless=True)
    lens = TerminalLens()
    state = {'lines': ['test line'], 'input_buf': 'hi'}
    lens.render(vdi, None, Rect(0, 0, 300, 200), True, state)
    # Terminal draws text on black bg — check text area (top rows)
    has_px = any(vdi.fb[i] != 0 for i in range(0, 300 * 30))
    assert has_px, "terminal should render text pixels"


# ===================================================================
# EditorLens
# ===================================================================

@test("crystal_editor_init_from_string", batch="phase15")
def test_editor_init_from_string():
    vdi = VDI(300, 200, headless=True)
    lens = EditorLens()
    state = {}
    lens.render(vdi, "line 1\nline 2\nline 3", Rect(0, 0, 300, 200), True, state)
    assert state['lines'] == ['line 1', 'line 2', 'line 3']
    assert state['cursor_line'] == 0
    assert state['cursor_col'] == 0


@test("crystal_editor_type_char", batch="phase15")
def test_editor_type_char():
    lens = EditorLens()
    state = {'lines': [''], 'cursor_line': 0, 'cursor_col': 0,
             'scroll_y': 0, 'scroll_x': 0, 'modified': False,
             'undo': [], 'redo': []}
    lens.on_key("", ord('a'), 0, state)
    assert state['lines'][0] == 'a'
    assert state['cursor_col'] == 1
    assert state['modified']


@test("crystal_editor_newline", batch="phase15")
def test_editor_newline():
    import pygame
    lens = EditorLens()
    state = {'lines': ['hello'], 'cursor_line': 0, 'cursor_col': 3,
             'scroll_y': 0, 'scroll_x': 0, 'modified': False,
             'undo': [], 'redo': []}
    lens.on_key("", pygame.K_RETURN, 0, state)
    assert state['lines'] == ['hel', 'lo']
    assert state['cursor_line'] == 1
    assert state['cursor_col'] == 0


@test("crystal_editor_backspace", batch="phase15")
def test_editor_backspace():
    import pygame
    lens = EditorLens()
    state = {'lines': ['abc'], 'cursor_line': 0, 'cursor_col': 2,
             'scroll_y': 0, 'scroll_x': 0, 'modified': False,
             'undo': [], 'redo': []}
    lens.on_key("", pygame.K_BACKSPACE, 0, state)
    assert state['lines'][0] == 'ac'
    assert state['cursor_col'] == 1


@test("crystal_editor_undo", batch="phase15")
def test_editor_undo():
    import pygame
    lens = EditorLens()
    state = {'lines': ['abc'], 'cursor_line': 0, 'cursor_col': 3,
             'scroll_y': 0, 'scroll_x': 0, 'modified': False,
             'undo': [], 'redo': []}
    # Type 'x'
    lens.on_key("", ord('x'), 0, state)
    assert state['lines'][0] == 'abcx'
    # Undo
    lens.on_key("", ord('z'), pygame.KMOD_CTRL, state)
    assert state['lines'][0] == 'abc'


@test("crystal_editor_navigation", batch="phase15")
def test_editor_navigation():
    import pygame
    lens = EditorLens()
    state = {'lines': ['hello', 'world'], 'cursor_line': 0, 'cursor_col': 0,
             'scroll_y': 0, 'scroll_x': 0, 'modified': False,
             'undo': [], 'redo': []}
    lens.on_key("", pygame.K_END, 0, state)
    assert state['cursor_col'] == 5
    lens.on_key("", pygame.K_HOME, 0, state)
    assert state['cursor_col'] == 0
    lens.on_key("", pygame.K_DOWN, 0, state)
    assert state['cursor_line'] == 1
    lens.on_key("", pygame.K_UP, 0, state)
    assert state['cursor_line'] == 0


@test("crystal_editor_lisp_highlight", batch="phase15")
def test_editor_lisp_highlight():
    lens = EditorLens()
    spans = lens._lisp_highlight('(defun foo (x) (+ x 1))')
    assert len(spans) > 0
    # 'defun' should be highlighted as keyword
    keyword_spans = [(s, e, c) for s, e, c in spans if c == C.TYPE_KEYWORD]
    assert any(True for s, e, _ in keyword_spans), "defun should be highlighted"


# ===================================================================
# TreeLens
# ===================================================================

@test("crystal_tree_dict_target", batch="phase15")
def test_tree_dict_target():
    vdi = VDI(200, 200, headless=True)
    lens = TreeLens()
    state = {}
    target = {'docs': {}, 'src': {}, 'readme.txt': 'text'}
    lens.render(vdi, target, Rect(0, 0, 200, 200), True, state)
    entries = state.get('_entries', [])
    assert len(entries) == 3


@test("crystal_tree_vfs_target", batch="phase15")
def test_tree_vfs_target():
    from lm1.vfs import VFS
    vdi = VDI(200, 200, headless=True)
    lens = TreeLens()
    vfs = VFS()
    vfs.populate_default()
    state = {'_vfs': vfs, '_cwd': '/'}
    lens.render(vdi, None, Rect(0, 0, 200, 200), True, state)
    entries = state.get('_entries', [])
    assert len(entries) > 0, "VFS root should have entries"


@test("crystal_tree_navigate", batch="phase15")
def test_tree_navigate():
    import pygame
    lens = TreeLens()
    state = {'_entries': [('a', False, 0), ('b', False, 0), ('c', False, 0)],
             'selected': 0}
    lens.on_key(None, pygame.K_DOWN, 0, state)
    assert state['selected'] == 1
    lens.on_key(None, pygame.K_DOWN, 0, state)
    assert state['selected'] == 2
    lens.on_key(None, pygame.K_UP, 0, state)
    assert state['selected'] == 1


# ===================================================================
# StreamLens
# ===================================================================

@test("crystal_stream_renders", batch="phase15")
def test_stream_renders():
    vdi = VDI(200, 100, headless=True)
    lens = StreamLens()
    target = ['line 1', 'line 2', 'line 3']
    lens.render(vdi, target, Rect(0, 0, 200, 100), True, {})
    # Stream draws text on black bg — check text area
    has_px = any(vdi.fb[i] != 0 for i in range(0, 200 * 30))
    assert has_px, "stream should render text pixels"


# ===================================================================
# TimeLens
# ===================================================================

@test("crystal_time_renders", batch="phase15")
def test_time_renders():
    vdi = VDI(200, 100, headless=True)
    lens = TimeLens()
    lens.render(vdi, None, Rect(0, 0, 200, 100), True, {})
    has_px = any(vdi.fb[i] != 0 for i in range(0, len(vdi.fb), 100))
    assert has_px


# ===================================================================
# InteractiveLens (Calculator)
# ===================================================================

@test("crystal_calc_basic_addition", batch="phase15")
def test_calc_basic_addition():
    lens = InteractiveLens()
    state = {}
    lens._press('7', state)
    assert state['display'] == '7'
    lens._press('+', state)
    lens._press('3', state)
    lens._press('=', state)
    assert state['display'] == '10'


@test("crystal_calc_multiplication", batch="phase15")
def test_calc_multiplication():
    lens = InteractiveLens()
    state = {}
    lens._press('6', state)
    lens._press('*', state)
    lens._press('7', state)
    lens._press('=', state)
    assert state['display'] == '42'


@test("crystal_calc_clear", batch="phase15")
def test_calc_clear():
    lens = InteractiveLens()
    state = {}
    lens._press('5', state)
    lens._press('C', state)
    assert state['display'] == '0'
    assert state['accumulator'] == 0


@test("crystal_calc_renders", batch="phase15")
def test_calc_renders():
    vdi = VDI(200, 200, headless=True)
    lens = InteractiveLens()
    state = {'display': '42', 'accumulator': 0, 'operator': '',
             'new_input': True}
    lens.render(vdi, None, Rect(0, 0, 200, 200), True, state)
    has_px = any(vdi.fb[i] != 0 for i in range(0, len(vdi.fb), 100))
    assert has_px


# ===================================================================
# Scrapbook
# ===================================================================

@test("crystal_scrapbook_empty", batch="phase15")
def test_scrapbook_empty():
    sb = Scrapbook()
    assert sb.empty
    assert sb.paste() is None


@test("crystal_scrapbook_snip_paste", batch="phase15")
def test_scrapbook_snip_paste():
    sb = Scrapbook()
    sb.snip("hello", 'text/plain', source='test')
    assert not sb.empty
    entry = sb.paste()
    assert entry is not None
    assert entry.data == "hello"
    assert entry.scrap_type == 'text/plain'
    assert entry.source == 'test'


@test("crystal_scrapbook_typed_paste", batch="phase15")
def test_scrapbook_typed_paste():
    sb = Scrapbook()
    sb.snip("text data", 'text/plain')
    sb.snip([1, 2, 3], 'application/sexp')
    text = sb.paste('text/plain')
    sexp = sb.paste('application/sexp')
    assert text.data == "text data"
    assert sexp.data == [1, 2, 3]


@test("crystal_scrapbook_typed_miss", batch="phase15")
def test_scrapbook_typed_miss():
    sb = Scrapbook()
    sb.snip("hello", 'text/plain')
    assert sb.paste('image/png') is None


@test("crystal_scrapbook_history", batch="phase15")
def test_scrapbook_history():
    sb = Scrapbook()
    for i in range(10):
        sb.snip(f"item {i}")
    assert len(sb.entries) == 10
    assert sb.paste().data == "item 9"


@test("crystal_scrapbook_max_history", batch="phase15")
def test_scrapbook_max_history():
    sb = Scrapbook()
    sb.max_history = 5
    for i in range(20):
        sb.snip(f"item {i}")
    assert len(sb.entries) == 5
    assert sb.paste().data == "item 19"


# ===================================================================
# Crystal Bar
# ===================================================================

@test("crystal_bar_default_items", batch="phase15")
def test_bar_default_items():
    crystal = _make_crystal_with_layout()
    assert len(crystal.bar_items) >= 5
    labels = {item.label for item in crystal.bar_items}
    assert 'Terminal' in labels
    assert 'Files' in labels
    assert 'Editor' in labels


@test("crystal_bar_clock", batch="phase15")
def test_bar_clock():
    import time
    crystal = _make_crystal_with_layout()
    crystal.redraw()
    expected = time.strftime(crystal.bar_clock.format_str)
    assert crystal.bar_clock.last_str == expected


@test("crystal_bar_hit_test", batch="phase15")
def test_bar_hit_test():
    crystal = _make_crystal_with_layout(640, 480)
    crystal._layout()
    # Bar is at the bottom — clicking in bar area
    bar_y = 480 - BAR_H + 5
    idx = crystal._bar_item_at(20, bar_y)
    assert idx >= 0, "should hit a bar item at x=20"
    # Above bar should miss
    above_idx = crystal._bar_item_at(20, 480 - BAR_H - 5)
    assert above_idx == -1


# ===================================================================
# Default layout
# ===================================================================

@test("crystal_default_layout_portals", batch="phase15")
def test_default_layout_portals():
    crystal = _make_crystal_with_layout(640, 480)
    portals = crystal.root.all_portals()
    assert len(portals) == 3
    lens_names = {p.lens_name for p in portals}
    assert 'tree' in lens_names
    assert 'terminal' in lens_names
    assert 'inspect' in lens_names


@test("crystal_default_focus_on_terminal", batch="phase15")
def test_default_focus_on_terminal():
    crystal = _make_crystal_with_layout()
    assert crystal._focused_portal is not None
    assert crystal._focused_portal.lens_name == 'terminal'


# ===================================================================
# Event handling
# ===================================================================

@test("crystal_event_quit", batch="phase15")
def test_event_quit():
    crystal = _make_crystal()
    crystal.set_root_portal("test")
    crystal.handle_event(EVT_QUIT, 0, 0)
    assert not crystal._running


@test("crystal_key_dispatches_to_lens", batch="phase15")
def test_key_dispatches_to_lens():
    crystal = _make_crystal_with_layout()
    # Focus is on terminal — type a char
    crystal.handle_event(EVT_KEY_DOWN, ord('x'), 0)
    assert crystal._focused_portal.state['input_buf'] == 'x'


@test("crystal_click_focuses_portal", batch="phase15")
def test_click_focuses_portal():
    crystal = _make_crystal(400, 300)
    p1 = crystal.set_root_portal("left", 'inspect', 'Left')
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5,
                               new_target="right", new_label='Right')
    crystal._layout()
    crystal._focused_portal = p1
    # Click in the right portal
    rx = p2.rect.x + p2.rect.w // 2
    ry = p2.rect.y + p2.rect.h // 2
    crystal._on_mouse_down(rx, ry, 1)
    assert crystal._focused_portal is p2


@test("crystal_portal_at_hit_test", batch="phase15")
def test_portal_at_hit_test():
    crystal = _make_crystal(400, 300)
    p1 = crystal.set_root_portal("left")
    p2 = crystal.split_portal(p1, SplitDir.VERTICAL, 0.5, new_target="right")
    crystal._layout()
    # Left portal
    hit = crystal._portal_at(crystal.root, p1.rect.x + 5, p1.rect.y + 5)
    assert hit is p1
    # Right portal
    hit = crystal._portal_at(crystal.root, p2.rect.x + 5, p2.rect.y + 5)
    assert hit is p2


@test("crystal_divider_hit_test", batch="phase15")
def test_divider_hit_test():
    crystal = _make_crystal(400, 300)
    p1 = crystal.set_root_portal("left")
    crystal.split_portal(p1, SplitDir.VERTICAL, 0.5)
    crystal._layout()
    dr = crystal.root.rect.divider_v(0.5)
    result = crystal._divider_hit(crystal.root, dr.x + 1, dr.y + 10)
    assert result is not None
    pane, axis = result
    assert axis == 'v'


# ===================================================================
# Color palette sanity
# ===================================================================

@test("crystal_color_palette", batch="phase15")
def test_color_palette():
    """All palette entries are valid 24-bit colors."""
    palette = [
        C.CANVAS, C.PORTAL_BG, C.PORTAL_EDGE, C.FOCUS_GLOW,
        C.BAR_BG, C.BAR_TEXT, C.BAR_CLOCK, C.DIVIDER,
        C.TEXT, C.TEXT_DIM, C.TEXT_BRIGHT,
        C.TYPE_FN, C.TYPE_DATA, C.TYPE_ACTOR, C.TYPE_ERROR,
        C.TYPE_STRING, C.TYPE_NUMBER, C.TYPE_KEYWORD,
    ]
    for color in palette:
        assert 0 <= color <= 0xFFFFFF, f"color 0x{color:06X} out of range"


@test("crystal_dark_palette", batch="phase15")
def test_dark_palette():
    """Canvas is dark, text is light — modern dark theme."""
    # Canvas luminance < 0x30 in all channels
    cr = (C.CANVAS >> 16) & 0xFF
    cg = (C.CANVAS >> 8) & 0xFF
    cb = C.CANVAS & 0xFF
    assert max(cr, cg, cb) < 0x30, "canvas should be very dark"
    # Text is light (at least one channel > 0xC0)
    tr = (C.TEXT >> 16) & 0xFF
    tg = (C.TEXT >> 8) & 0xFF
    tb = C.TEXT & 0xFF
    assert max(tr, tg, tb) > 0xC0, "primary text should be light"


# ===================================================================
# Integration: Crystal launch alias
# ===================================================================

@test("crystal_launch_alias", batch="phase15")
def test_launch_alias():
    from lm1.crystal import launch_desktop
    assert launch_desktop is launch_crystal
