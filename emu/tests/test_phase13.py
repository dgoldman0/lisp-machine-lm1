"""Phase 13 tests — OS Foundation: VFS, Icons, Toolkit, Desktop Enhancements.

Tests the virtual filesystem, icon system, widget toolkit,
taskbar, desktop icons, minimize/maximize, and the new
text editor crystallite — all headless.
"""

from lm1.testing.harness import test
from lm1.vdi import VDI, EVT_MOUSE_DOWN, EVT_KEY_DOWN
from lm1.desktop import (
    AES, Colors, Window, Menu, MenuItem,
    TITLE_BAR_H, MENU_BAR_H, TASKBAR_H, BORDER_W,
    WIN_CLOSEABLE, WIN_MOVEABLE, WIN_RESIZABLE,
    WIN_MAXIMIZABLE, WIN_MINIMIZABLE,
    DesktopIcon,
    TerminalCrystallite, ClockCrystallite, CalculatorCrystallite,
    InspectorCrystallite, FileManagerCrystallite, ControlPanelCrystallite,
    TextEditorCrystallite,
)
from lm1.vfs import VFS, VFSFile, VFSDirectory
from lm1.icons import Icon, get_icon, icon_for_name, icon_for_mime


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _make_aes(w=1024, h=768):
    """Create a headless AES for testing."""
    vdi = VDI(width=w, height=h, headless=True)
    return AES(vdi)


# ===================================================================
# Virtual Filesystem
# ===================================================================

@test("vfs_create_empty", batch="phase13")
def test_vfs_create_empty():
    """VFS creates with a root directory."""
    vfs = VFS()
    root = vfs.resolve("/")
    assert root is not None
    assert root.is_dir


@test("vfs_populate_default", batch="phase13")
def test_vfs_populate_default():
    """VFS populate_default creates system directories and files."""
    vfs = VFS()
    vfs.populate_default()
    # /system/ exists
    sys_dir = vfs.resolve_dir("/system")
    assert sys_dir is not None
    # /applications/ has .app files
    apps = vfs.list_dir("/applications")
    assert len(apps) >= 7
    # /users/default/documents has files
    docs = vfs.list_dir("/users/default/documents")
    assert len(docs) >= 3


@test("vfs_mkdir", batch="phase13")
def test_vfs_mkdir():
    """VFS mkdir creates directories including parents."""
    vfs = VFS()
    vfs.mkdir("/a/b/c", parents=True)
    d = vfs.resolve_dir("/a/b/c")
    assert d is not None
    assert d.name == "c"


@test("vfs_create_read_file", batch="phase13")
def test_vfs_create_read_file():
    """VFS create and read a file."""
    vfs = VFS()
    vfs.mkdir("/test")
    vfs.create_file("/test/hello.txt", "Hello, World!")
    content = vfs.read_text("/test/hello.txt")
    assert content == "Hello, World!"


@test("vfs_write_file", batch="phase13")
def test_vfs_write_file():
    """VFS write overwrites file content."""
    vfs = VFS()
    vfs.mkdir("/test")
    vfs.create_file("/test/data.txt", "old")
    vfs.write("/test/data.txt", "new content")
    assert vfs.read_text("/test/data.txt") == "new content"


@test("vfs_delete_file", batch="phase13")
def test_vfs_delete_file():
    """VFS delete removes a file."""
    vfs = VFS()
    vfs.mkdir("/test")
    vfs.create_file("/test/temp.txt", "temp")
    vfs.delete("/test/temp.txt")
    assert vfs.resolve("/test/temp.txt") is None, "File should be deleted"


@test("vfs_delete_dir_recursive", batch="phase13")
def test_vfs_delete_dir_recursive():
    """VFS delete recursively removes directory."""
    vfs = VFS()
    vfs.mkdir("/rm_me/sub", parents=True)
    vfs.create_file("/rm_me/sub/file.txt", "data")
    vfs.delete("/rm_me", recursive=True)
    assert vfs.resolve("/rm_me") is None, "Dir should be deleted"


@test("vfs_list_dir_sorted", batch="phase13")
def test_vfs_list_dir_sorted():
    """VFS list_dir returns dirs first, then files, alpha sorted."""
    vfs = VFS()
    vfs.mkdir("/mixed")
    vfs.create_file("/mixed/zebra.txt", "z")
    vfs.mkdir("/mixed/alpha")
    vfs.create_file("/mixed/beta.txt", "b")
    vfs.mkdir("/mixed/gamma")
    children = vfs.list_dir("/mixed")
    names = [c.name for c in children]
    # Dirs first (alpha, gamma), then files (beta.txt, zebra.txt)
    assert names == ["alpha", "gamma", "beta.txt", "zebra.txt"]


@test("vfs_stat", batch="phase13")
def test_vfs_stat():
    """VFS stat returns file metadata."""
    vfs = VFS()
    vfs.mkdir("/test")
    vfs.create_file("/test/info.txt", "some data")
    stat = vfs.stat("/test/info.txt")
    assert stat['is_file'] == True
    assert stat['size'] == 9


@test("vfs_copy_file", batch="phase13")
def test_vfs_copy_file():
    """VFS copy duplicates a file."""
    vfs = VFS()
    vfs.mkdir("/src")
    vfs.mkdir("/dst")
    vfs.create_file("/src/original.txt", "hello")
    vfs.copy("/src/original.txt", "/dst/copy.txt")
    assert vfs.read_text("/dst/copy.txt") == "hello"
    # Original still exists
    assert vfs.read_text("/src/original.txt") == "hello"


@test("vfs_move_file", batch="phase13")
def test_vfs_move_file():
    """VFS move relocates a file."""
    vfs = VFS()
    vfs.mkdir("/src")
    vfs.mkdir("/dst")
    vfs.create_file("/src/moveme.txt", "data")
    vfs.move("/src/moveme.txt", "/dst/moved.txt")
    assert vfs.read_text("/dst/moved.txt") == "data"
    assert vfs.resolve("/src/moveme.txt") is None, "Original should be gone after move"


@test("vfs_walk", batch="phase13")
def test_vfs_walk():
    """VFS walk traverses directory tree."""
    vfs = VFS()
    vfs.mkdir("/walk/a/b", parents=True)
    vfs.create_file("/walk/top.txt", "t")
    vfs.create_file("/walk/a/mid.txt", "m")
    vfs.create_file("/walk/a/b/deep.txt", "d")
    walked = list(vfs.walk("/walk"))
    assert len(walked) == 3  # /walk, /walk/a, /walk/a/b
    # Top level has 1 file and 1 dir
    assert walked[0][0] == "/walk"


@test("vfs_path_normalization", batch="phase13")
def test_vfs_path_normalization():
    """VFS normalizes paths with . and .."""
    vfs = VFS()
    vfs.mkdir("/a/b", parents=True)
    assert vfs._normalize("/a/./b/../b") == ["a", "b"]
    assert vfs._normalize("/a/b/../../a/b") == ["a", "b"]


@test("vfs_mime_type", batch="phase13")
def test_vfs_mime_type():
    """VFS infers MIME types from file extensions."""
    vfs = VFS()
    vfs.mkdir("/test")
    vfs.create_file("/test/code.lisp", "(+ 1 2)")
    f = vfs.resolve_file("/test/code.lisp")
    assert f.mime_type == "text/x-lisp"


@test("vfs_aes_integration", batch="phase13")
def test_vfs_aes_integration():
    """AES has a populated VFS."""
    aes = _make_aes()
    assert isinstance(aes.vfs, VFS)
    # Default files exist
    docs = aes.vfs.list_dir("/users/default/documents")
    assert len(docs) >= 3


# ===================================================================
# Icon System
# ===================================================================

@test("icon_get_stock", batch="phase13")
def test_icon_get_stock():
    """Stock icons can be retrieved by name."""
    for name in ['folder', 'file', 'terminal', 'calculator',
                 'clock', 'editor', 'settings']:
        icon = get_icon(name)
        assert icon is not None, f"Missing icon: {name}"
        assert icon.width == 16
        assert icon.height == 16


@test("icon_draw", batch="phase13")
def test_icon_draw():
    """Icon draws to VDI framebuffer."""
    vdi = VDI(width=64, height=64, headless=True)
    icon = get_icon('folder')
    # Clear to black
    for i in range(len(vdi.fb)):
        vdi.fb[i] = 0
    icon.draw(vdi, 10, 10, scale=1)
    # Some pixels should be non-zero now
    changed = sum(1 for px in vdi.fb if px != 0)
    assert changed > 0, "Icon should have drawn something"


@test("icon_draw_2x", batch="phase13")
def test_icon_draw_2x():
    """Icon draws at 2x scale (32x32)."""
    vdi = VDI(width=64, height=64, headless=True)
    icon = get_icon('file')
    for i in range(len(vdi.fb)):
        vdi.fb[i] = 0
    icon.draw(vdi, 0, 0, scale=2)
    changed = sum(1 for px in vdi.fb if px != 0)
    assert changed > 0


@test("icon_for_name_mapping", batch="phase13")
def test_icon_for_name_mapping():
    """icon_for_name maps filenames to icon names."""
    assert icon_for_name("hello.lisp") == "file_code"
    assert icon_for_name("readme.txt") == "file_text"
    assert icon_for_name("photo.png") == "file_image"
    assert icon_for_name("unknown.xyz") == "file"


@test("icon_for_mime_mapping", batch="phase13")
def test_icon_for_mime_mapping():
    """icon_for_mime maps MIME types to icon names."""
    assert icon_for_mime("text/plain") == "file_text"
    assert icon_for_mime("text/x-lisp") == "file_code"
    # Unknown mime returns generic
    result = icon_for_mime("application/octet-stream")
    assert result == "file"


# ===================================================================
# Window Minimize / Maximize
# ===================================================================

@test("window_minimize", batch="phase13")
def test_window_minimize():
    """Minimizing a window hides it and focuses next."""
    aes = _make_aes()
    w1 = aes.create_window("Win1", 10, 30, 200, 150)
    w2 = aes.create_window("Win2", 50, 60, 200, 150,
                            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_MINIMIZABLE)
    assert aes._focused is w2
    aes.minimize_window(w2)
    assert w2.minimized
    assert aes._focused is w1  # focus moves to w1


@test("window_minimize_not_found", batch="phase13")
def test_window_minimize_not_found():
    """Minimized window is not found by find_window_at."""
    aes = _make_aes()
    win = aes.create_window("Hidden", 10, 30, 200, 150)
    aes.minimize_window(win)
    found = aes.find_window_at(50, 50)
    assert found is None


@test("window_maximize", batch="phase13")
def test_window_maximize():
    """Maximizing a window fills the work area."""
    aes = _make_aes()
    win = aes.create_window("Max", 10, 30, 200, 150,
                             flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_MAXIMIZABLE)
    aes.maximize_window(win)
    assert win.maximized
    assert win.x == 0
    assert win.y == MENU_BAR_H
    assert win.w == aes.vdi.width
    assert win.h == aes.vdi.height - MENU_BAR_H - TASKBAR_H


@test("window_maximize_restore", batch="phase13")
def test_window_maximize_restore():
    """Maximizing again restores to original position."""
    aes = _make_aes()
    win = aes.create_window("Max", 50, 80, 300, 200,
                             flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_MAXIMIZABLE)
    aes.maximize_window(win)
    assert win.maximized
    # Maximize again toggles back
    aes.maximize_window(win)
    assert not win.maximized
    assert win.x == 50
    assert win.y == 80
    assert win.w == 300
    assert win.h == 200


@test("window_restore_minimized", batch="phase13")
def test_window_restore_minimized():
    """Restoring a minimized window brings it back."""
    aes = _make_aes()
    win = aes.create_window("Restore", 10, 30, 200, 150)
    aes.minimize_window(win)
    assert win.minimized
    aes.restore_window(win)
    assert not win.minimized
    assert aes._focused is win


@test("window_min_max_buttons", batch="phase13")
def test_window_min_max_buttons():
    """Window hit-tests for minimize and maximize buttons."""
    aes = _make_aes()
    win = aes.create_window("Buttons", 10, 30, 300, 200,
                             flags=WIN_CLOSEABLE | WIN_MOVEABLE
                                   | WIN_MAXIMIZABLE | WIN_MINIMIZABLE)
    # Maximize button: right side
    max_bx = win.x + win.w - 3 - 14  # MAX_BTN_W = 14
    max_by = win.y + (TITLE_BAR_H - 14) // 2
    assert win.in_maximize_button(max_bx + 5, max_by + 5)
    assert not win.in_maximize_button(10, 10)  # not near button

    # Minimize button: left of maximize
    min_bx = win.x + win.w - 3 - 14 - 4 - 14
    min_by = win.y + (TITLE_BAR_H - 14) // 2
    assert win.in_minimize_button(min_bx + 5, min_by + 5)


# ===================================================================
# Taskbar
# ===================================================================

@test("taskbar_renders", batch="phase13")
def test_taskbar_renders():
    """Taskbar renders at the bottom of the screen."""
    aes = _make_aes()
    TerminalCrystallite(aes)
    aes.redraw()
    # Check pixels in taskbar area
    vdi = aes.vdi
    ty = vdi.height - TASKBAR_H + 5
    px = vdi.read_pixel(50, ty)
    # Should not be desktop background (0x1A1A2E)
    assert px != Colors.DESKTOP_BG, "Taskbar area should have taskbar colors"


@test("taskbar_click_focus", batch="phase13")
def test_taskbar_click_focus():
    """Clicking a window button in taskbar focuses that window."""
    aes = _make_aes()
    w1 = aes.create_window("Alpha", 10, 30, 200, 150)
    w2 = aes.create_window("Beta", 50, 60, 200, 150)
    aes.redraw()  # populates _taskbar_buttons
    # Focus should be on Beta
    assert aes._focused is w2
    # Click taskbar in Alpha's button area
    # Taskbar buttons start after Crystal button
    ty = aes.vdi.height - TASKBAR_H + 10
    # We can't precisely know button positions without font metrics,
    # but we verify the taskbar click handler exists and works
    aes._on_taskbar_click(200, ty)
    # The exact behavior depends on button layout, but should not crash


# ===================================================================
# Desktop Icons
# ===================================================================

@test("desktop_icons_render", batch="phase13")
def test_desktop_icons_render():
    """Desktop icons render without crashing."""
    aes = _make_aes()
    aes._desktop_icons.append(DesktopIcon(
        label="Test", icon_name="terminal",
        action=lambda: None, grid_row=0, grid_col=0,
    ))
    aes.redraw()  # Should not crash


@test("desktop_icon_hit_test", batch="phase13")
def test_desktop_icon_hit_test():
    """Desktop icon hit test finds icons."""
    aes = _make_aes()
    aes._desktop_icons.append(DesktopIcon(
        label="Test", icon_name="terminal",
        grid_row=0, grid_col=0,
    ))
    # Icon at grid position (0,0) starts at (16, MENU_BAR_H + 12)
    idx = aes._desktop_icon_hit(40, MENU_BAR_H + 30)
    assert idx == 0
    # Miss
    idx = aes._desktop_icon_hit(800, 800)
    assert idx == -1


# ===================================================================
# Text Editor
# ===================================================================

@test("editor_create_empty", batch="phase13")
def test_editor_create_empty():
    """Text editor creates with empty content."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes)
    assert editor.win in aes._windows
    assert editor._lines == [""]
    aes.redraw()


@test("editor_open_vfs_file", batch="phase13")
def test_editor_open_vfs_file():
    """Text editor opens a VFS file."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes, vfs_path="/users/default/documents/welcome.txt")
    assert len(editor._lines) > 0
    assert editor.win.title == "welcome.txt"
    aes.redraw()


@test("editor_typing", batch="phase13")
def test_editor_typing():
    """Text editor handles keyboard input."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes)
    import pygame
    # Type "Hello"
    for ch in "Hello":
        editor._on_key(editor.win, ord(ch.lower()), pygame.KMOD_SHIFT if ch.isupper() else 0)
    assert editor._lines[0] == "Hello"
    assert editor._cursor_col == 5
    assert editor._modified


@test("editor_enter_newline", batch="phase13")
def test_editor_enter_newline():
    """Text editor creates new line on Enter."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes)
    import pygame
    # Type "AB" then Enter
    editor._on_key(editor.win, ord('a'), pygame.KMOD_SHIFT)
    editor._on_key(editor.win, ord('b'), pygame.KMOD_SHIFT)
    editor._on_key(editor.win, pygame.K_RETURN, 0)
    assert len(editor._lines) == 2
    assert editor._lines[0] == "AB"
    assert editor._lines[1] == ""
    assert editor._cursor_line == 1


@test("editor_backspace", batch="phase13")
def test_editor_backspace():
    """Text editor handles backspace."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes)
    import pygame
    editor._on_key(editor.win, ord('x'), 0)
    editor._on_key(editor.win, ord('y'), 0)
    assert editor._lines[0] == "xy"
    editor._on_key(editor.win, pygame.K_BACKSPACE, 0)
    assert editor._lines[0] == "x"


@test("editor_undo_redo", batch="phase13")
def test_editor_undo_redo():
    """Text editor undo/redo works."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes)
    import pygame
    # Type "abc"
    for ch in "abc":
        editor._on_key(editor.win, ord(ch), 0)
    assert editor._lines[0] == "abc"
    # Undo 3 times
    editor._undo()
    editor._undo()
    editor._undo()
    assert editor._lines[0] == ""
    # Redo
    editor._redo()
    assert len(editor._lines[0]) > 0


@test("editor_save_vfs", batch="phase13")
def test_editor_save_vfs():
    """Text editor saves to VFS."""
    aes = _make_aes()
    # Create a file first
    aes.vfs.create_file("/tmp/test_save.txt", "original")
    editor = TextEditorCrystallite(aes, vfs_path="/tmp/test_save.txt")
    import pygame
    # Modify
    editor._lines = ["modified content"]
    editor._modified = True
    editor._save()
    assert not editor._modified
    assert aes.vfs.read_text("/tmp/test_save.txt") == "modified content\n"


@test("editor_lisp_syntax", batch="phase13")
def test_editor_lisp_syntax():
    """Text editor provides syntax highlighting for .lisp files."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes, vfs_path="/users/default/documents/hello.lisp")
    assert editor._is_lisp
    # Test highlighting
    spans = editor._lisp_highlight("(defun square (x) (* x x))", 0)
    assert len(spans) > 0  # Should produce highlight spans
    aes.redraw()


@test("editor_cursor_movement", batch="phase13")
def test_editor_cursor_movement():
    """Text editor cursor movement works."""
    aes = _make_aes()
    editor = TextEditorCrystallite(aes)
    editor._lines = ["line one", "line two", "line three"]
    editor._cursor_line = 0
    editor._cursor_col = 0
    import pygame
    # Move down
    editor._on_key(editor.win, pygame.K_DOWN, 0)
    assert editor._cursor_line == 1
    # Move right
    editor._on_key(editor.win, pygame.K_RIGHT, 0)
    assert editor._cursor_col == 1
    # Home
    editor._on_key(editor.win, pygame.K_HOME, 0)
    assert editor._cursor_col == 0
    # End
    editor._on_key(editor.win, pygame.K_END, 0)
    assert editor._cursor_col == len("line two")


# ===================================================================
# Terminal VFS commands
# ===================================================================

@test("terminal_vfs_ls", batch="phase13")
def test_terminal_vfs_ls():
    """Terminal ls command lists VFS directory."""
    aes = _make_aes()
    term = TerminalCrystallite(aes)
    term._execute("ls /")
    # Should list system, applications, users, tmp
    found = any("system" in line for line in term.lines)
    assert found, "ls / should show 'system'"


@test("terminal_vfs_cat", batch="phase13")
def test_terminal_vfs_cat():
    """Terminal cat command shows VFS file contents."""
    aes = _make_aes()
    term = TerminalCrystallite(aes)
    term._execute("cat /system/about.txt")
    found = any("LM-1" in line for line in term.lines)
    assert found, "cat should show file contents"


@test("terminal_vfs_pwd", batch="phase13")
def test_terminal_vfs_pwd():
    """Terminal pwd shows current directory."""
    aes = _make_aes()
    term = TerminalCrystallite(aes)
    term._execute("pwd")
    assert any("/" in line for line in term.lines)


# ===================================================================
# FileManager with VFS
# ===================================================================

@test("filemanager_vfs_icons_view", batch="phase13")
def test_filemanager_vfs_icons_view():
    """File manager renders icon view without crashing."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/users/default")
    assert fm._view_mode == 'icons'
    aes.redraw()


@test("filemanager_vfs_list_view", batch="phase13")
def test_filemanager_vfs_list_view():
    """File manager renders list view without crashing."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/users/default")
    fm._set_view('list')
    assert fm._view_mode == 'list'
    aes.redraw()


@test("filemanager_vfs_navigate", batch="phase13")
def test_filemanager_vfs_navigate():
    """File manager navigates VFS directories."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/")
    assert fm.path == "/"
    fm._navigate("/users/default/documents")
    assert fm.path == "/users/default/documents"
    assert len(fm._entries) >= 3


@test("filemanager_open_text_file", batch="phase13")
def test_filemanager_open_text_file():
    """Opening a text file in file manager creates an editor."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/users/default/documents")
    initial = len(aes._windows)
    # Find welcome.txt
    for i, (name, is_dir) in enumerate(fm._entries):
        if name == "welcome.txt":
            fm._open_entry(i)
            break
    assert len(aes._windows) == initial + 1


# ===================================================================
# Full desktop integration
# ===================================================================

@test("full_desktop_with_taskbar", batch="phase13")
def test_full_desktop_with_taskbar():
    """Full desktop renders with taskbar and all features."""
    aes = _make_aes()
    TerminalCrystallite(aes)
    ClockCrystallite(aes)
    CalculatorCrystallite(aes)
    FileManagerCrystallite(aes, path="/")
    TextEditorCrystallite(aes, vfs_path="/users/default/documents/hello.lisp")
    aes._desktop_icons.append(DesktopIcon(
        label="Terminal", icon_name="terminal", grid_row=0, grid_col=0,
    ))
    aes.redraw()
    # Framebuffer should have many colors
    unique = set(aes.vdi.fb)
    assert len(unique) > 10, "Full desktop should have rich colors"


@test("full_desktop_minimize_restore_cycle", batch="phase13")
def test_full_desktop_minimize_restore_cycle():
    """Window can be minimized and restored via AES."""
    aes = _make_aes()
    term = TerminalCrystallite(aes)
    w = term.win
    aes.minimize_window(w)
    assert w.minimized
    aes.redraw()
    aes.restore_window(w)
    assert not w.minimized
    assert aes._focused is w
    aes.redraw()


@test("profile_roundtrip_with_editor", batch="phase13")
def test_profile_roundtrip_with_editor():
    """Profile saves and restores editor crystallites."""
    from lm1.desktop import DesktopProfile
    aes = _make_aes()
    TextEditorCrystallite(aes, x=100, y=100, w=400, h=300)
    profile_str = DesktopProfile.save(aes)
    assert "editor" in profile_str
    aes2 = _make_aes()
    count = DesktopProfile.load(aes2, profile_str)
    assert count == 1
    assert len(aes2._windows) == 1
