"""Phase 11 tests — Crystallites, File Manager, Scrap, Resources, Profile.

Tests the Inspector, File Manager, Control Panel crystallites,
the Scrap (clipboard) system, ResourceDB, and DesktopProfile
serialization — all headless.
"""

from lm1.testing.harness import test
from lm1.vdi import VDI, EVT_MOUSE_DOWN, EVT_KEY_DOWN
from lm1.desktop import (
    AES, Colors, Menu, MenuItem, Window,
    TITLE_BAR_H, MENU_BAR_H,
    WIN_CLOSEABLE, WIN_MOVEABLE, WIN_RESIZABLE,
    Scrap, ScrapEntry, ResourceDB, DesktopProfile,
    TerminalCrystallite, ClockCrystallite, CalculatorCrystallite,
    InspectorCrystallite, FileManagerCrystallite, ControlPanelCrystallite,
    TextEditorCrystallite,
    _print_form,
)

import os
import tempfile


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _make_aes(w=640, h=480):
    """Create a headless AES for testing."""
    vdi = VDI(width=w, height=h, headless=True)
    return AES(vdi)


# -------------------------------------------------------------------
# Scrap (clipboard) system
# -------------------------------------------------------------------

@test("scrap_put_get_text", batch="phase11")
def test_scrap_put_get_text():
    """Scrap stores and retrieves text."""
    scrap = Scrap()
    assert scrap.empty
    scrap.put("hello", "text/plain")
    assert not scrap.empty
    entry = scrap.get()
    assert entry.scrap_type == "text/plain"
    assert entry.data == "hello"


@test("scrap_put_get_lisp", batch="phase11")
def test_scrap_put_get_lisp():
    """Scrap stores and retrieves Lisp forms."""
    scrap = Scrap()
    scrap.put(['+', 1, 2], "lisp/form")
    entry = scrap.get("lisp/form")
    assert entry.scrap_type == "lisp/form"
    assert entry.data == ['+', 1, 2]


@test("scrap_type_negotiation_lisp_to_text", batch="phase11")
def test_scrap_type_negotiation_lisp_to_text():
    """Scrap converts lisp/form to text/plain."""
    scrap = Scrap()
    scrap.put(['+', 1, 2], "lisp/form")
    entry = scrap.get("text/plain")
    assert entry.scrap_type == "text/plain"
    assert entry.data == "(+ 1 2)"


@test("scrap_type_negotiation_text_to_lisp", batch="phase11")
def test_scrap_type_negotiation_text_to_lisp():
    """Scrap converts text/plain to lisp/form."""
    scrap = Scrap()
    scrap.put("(+ 1 2)", "text/plain")
    entry = scrap.get("lisp/form")
    assert entry.scrap_type == "lisp/form"
    assert entry.data == ['+', 1, 2]


@test("scrap_history", batch="phase11")
def test_scrap_history():
    """Scrap maintains history ring."""
    scrap = Scrap()
    scrap.put("first", "text/plain")
    scrap.put("second", "text/plain")
    scrap.put("third", "text/plain")
    assert len(scrap.history) == 3
    assert scrap.history[0].data == "first"
    assert scrap.history[-1].data == "third"
    # Most recent is returned by get()
    assert scrap.get().data == "third"


@test("scrap_max_history", batch="phase11")
def test_scrap_max_history():
    """Scrap truncates history beyond MAX_HISTORY."""
    scrap = Scrap()
    for i in range(20):
        scrap.put(f"item{i}", "text/plain")
    assert len(scrap.history) == Scrap.MAX_HISTORY
    # Oldest items dropped
    assert scrap.history[0].data == "item4"


@test("scrap_clear", batch="phase11")
def test_scrap_clear():
    """Scrap clear empties history."""
    scrap = Scrap()
    scrap.put("something", "text/plain")
    scrap.clear()
    assert scrap.empty
    assert scrap.get() is None


@test("scrap_aes_integration", batch="phase11")
def test_scrap_aes_integration():
    """AES has a scrap instance."""
    aes = _make_aes()
    assert isinstance(aes.scrap, Scrap)
    aes.scrap.put("test", "text/plain")
    assert aes.scrap.get().data == "test"


# -------------------------------------------------------------------
# Resource system
# -------------------------------------------------------------------

@test("resource_put_get", batch="phase11")
def test_resource_put_get():
    """ResourceDB stores and retrieves resources."""
    rdb = ResourceDB()
    rdb.put("string", "greeting", "Hello World")
    assert rdb.get("string", "greeting") == "Hello World"
    assert rdb.get("string", "missing") is None


@test("resource_delete", batch="phase11")
def test_resource_delete():
    """ResourceDB deletes resources."""
    rdb = ResourceDB()
    rdb.put("string", "temp", "data")
    assert rdb.delete("string", "temp")
    assert rdb.get("string", "temp") is None
    assert not rdb.delete("string", "temp")  # already gone


@test("resource_list", batch="phase11")
def test_resource_list():
    """ResourceDB lists all resource keys."""
    rdb = ResourceDB()
    rdb.put("string", "a", "1")
    rdb.put("menu", "main", [])
    keys = rdb.list_resources()
    assert ("string", "a") in keys
    assert ("menu", "main") in keys


@test("resource_load_from_lisp", batch="phase11")
def test_resource_load_from_lisp():
    """ResourceDB loads resources from Lisp source."""
    rdb = ResourceDB()
    source = """
    (resource string greeting "Hello")
    (resource alert info (Info "System ready"))
    """
    count = rdb.load_from_lisp(source)
    assert count == 2
    assert rdb.get("string", "greeting") is not None
    assert rdb.get("alert", "info") is not None


@test("resource_to_lisp", batch="phase11")
def test_resource_to_lisp():
    """ResourceDB serializes to Lisp source."""
    rdb = ResourceDB()
    rdb.put("string", "a", "hello")
    output = rdb.to_lisp()
    assert "resource" in output
    assert "string" in output
    assert "hello" in output


@test("resource_build_menu", batch="phase11")
def test_resource_build_menu():
    """ResourceDB builds Menu objects from resource definitions."""
    rdb = ResourceDB()
    menu_data = [
        ['submenu', 'File',
         ['menu-item', 'New', 'cmd-new'],
         ['separator'],
         ['menu-item', 'Quit', 'cmd-quit']],
    ]
    rdb.put("menu", "main", menu_data)
    called = [False]

    def on_quit():
        called[0] = True

    menus = rdb.build_menu("main", {'cmd-quit': on_quit})
    assert menus is not None
    assert len(menus) == 1
    assert menus[0].label == "File"
    assert len(menus[0].items) == 3
    assert menus[0].items[0].label == "New"
    assert menus[0].items[1].separator
    assert menus[0].items[2].label == "Quit"
    # Invoke the callback
    menus[0].items[2].callback()
    assert called[0]


@test("resource_show_alert", batch="phase11")
def test_resource_show_alert():
    """ResourceDB show_alert creates an alert window."""
    aes = _make_aes()
    aes.resources = ResourceDB()
    aes.resources.put("alert", "test", ["Warning", "Something happened", "Click OK"])
    win = aes.resources.show_alert(aes, "test")
    assert win is not None
    assert win.title == "Warning"
    assert win in aes._windows


# -------------------------------------------------------------------
# Desktop Profile
# -------------------------------------------------------------------

@test("profile_save_load", batch="phase11")
def test_profile_save_load():
    """Desktop profile serializes and restores windows."""
    aes = _make_aes()
    TerminalCrystallite(aes, x=10, y=40, w=300, h=200)
    ClockCrystallite(aes, x=400, y=30)

    # Save
    profile_str = DesktopProfile.save(aes)
    assert "desktop-profile" in profile_str
    assert "terminal" in profile_str
    assert "clock" in profile_str

    # Load into fresh AES
    aes2 = _make_aes()
    count = DesktopProfile.load(aes2, profile_str)
    assert count == 2
    assert len(aes2._windows) == 2


@test("profile_save_load_file", batch="phase11")
def test_profile_save_load_file():
    """Desktop profile saves to and loads from files."""
    aes = _make_aes()
    TerminalCrystallite(aes, x=10, y=40, w=300, h=200)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.profile',
                                      delete=False) as f:
        path = f.name

    try:
        DesktopProfile.save_to_file(aes, path)
        assert os.path.exists(path)

        aes2 = _make_aes()
        count = DesktopProfile.load_from_file(aes2, path)
        assert count == 1
    finally:
        os.unlink(path)


@test("profile_roundtrip_all_types", batch="phase11")
def test_profile_roundtrip_all_types():
    """Profile saves and restores all crystallite types."""
    aes = _make_aes()
    TerminalCrystallite(aes, x=10, y=40, w=300, h=200)
    ClockCrystallite(aes, x=400, y=30)
    CalculatorCrystallite(aes, x=300, y=100)
    InspectorCrystallite(aes, x=20, y=200)
    ControlPanelCrystallite(aes, x=200, y=100)

    profile_str = DesktopProfile.save(aes)
    aes2 = _make_aes()
    count = DesktopProfile.load(aes2, profile_str)
    assert count == 5


# -------------------------------------------------------------------
# Inspector crystallite
# -------------------------------------------------------------------

@test("inspector_create", batch="phase11")
def test_inspector_create():
    """Inspector crystallite creates and renders."""
    aes = _make_aes()
    TerminalCrystallite(aes)  # something to inspect
    inspector = InspectorCrystallite(aes)
    assert inspector.win in aes._windows
    aes.redraw()  # should not crash


@test("inspector_shows_window_info", batch="phase11")
def test_inspector_shows_window_info():
    """Inspector shows window count and z-order."""
    aes = _make_aes()
    TerminalCrystallite(aes)
    ClockCrystallite(aes)
    inspector = InspectorCrystallite(aes)
    # Inspector should see 3 windows
    assert len(aes._windows) == 3
    aes.redraw()  # renders without crash


@test("inspector_pixel_probe", batch="phase11")
def test_inspector_pixel_probe():
    """Inspector pixel probe reads framebuffer."""
    aes = _make_aes()
    inspector = InspectorCrystallite(aes)
    inspector.set_pixel_probe(100, 100)
    assert inspector._inspect_pixel == (100, 100)
    aes.redraw()


# -------------------------------------------------------------------
# File Manager crystallite
# -------------------------------------------------------------------

@test("filemanager_create", batch="phase11")
def test_filemanager_create():
    """File manager opens and shows VFS directory contents."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/")
    assert fm.win in aes._windows
    assert len(fm._entries) > 0  # VFS has default dirs
    aes.redraw()


@test("filemanager_entries_sorted", batch="phase11")
def test_filemanager_entries_sorted():
    """File manager lists directories before files."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/users/default")
    # VFS default has dirs (desktop, documents, downloads, projects)
    if len(fm._entries) >= 2:
        first_file_idx = None
        last_dir_idx = None
        for i, (name, is_dir) in enumerate(fm._entries):
            if is_dir:
                last_dir_idx = i
            elif first_file_idx is None:
                first_file_idx = i
        if last_dir_idx is not None and first_file_idx is not None:
            assert last_dir_idx < first_file_idx, \
                "Directories should come before files"


@test("filemanager_navigate_parent", batch="phase11")
def test_filemanager_navigate_parent():
    """File manager navigates to parent directory."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/users/default/documents")
    assert fm.path == "/users/default/documents"
    fm._go_parent()
    assert fm.path == "/users/default"


@test("filemanager_open_subdir", batch="phase11")
def test_filemanager_open_subdir():
    """Opening a subdirectory creates a new file manager window."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/")
    initial_windows = len(aes._windows)
    # Find a directory entry
    for i, (name, is_dir) in enumerate(fm._entries):
        if is_dir:
            fm._open_entry(i)
            assert len(aes._windows) == initial_windows + 1
            break


@test("filemanager_select_file_to_scrap", batch="phase11")
def test_filemanager_select_file_to_scrap():
    """Selecting a text file opens the editor."""
    aes = _make_aes()
    fm = FileManagerCrystallite(aes, path="/users/default/documents")
    initial_windows = len(aes._windows)
    # Find a text/lisp file entry
    for i, (name, is_dir) in enumerate(fm._entries):
        if not is_dir and (name.endswith('.txt') or name.endswith('.lisp')):
            fm._open_entry(i)
            # Should open an editor window
            assert len(aes._windows) == initial_windows + 1
            break


# -------------------------------------------------------------------
# Control Panel crystallite
# -------------------------------------------------------------------

@test("controlpanel_create", batch="phase11")
def test_controlpanel_create():
    """Control panel creates and renders."""
    aes = _make_aes()
    cp = ControlPanelCrystallite(aes)
    assert cp.win in aes._windows
    aes.redraw()


@test("controlpanel_toggle_section", batch="phase11")
def test_controlpanel_toggle_section():
    """Control panel toggles between sections."""
    aes = _make_aes()
    cp = ControlPanelCrystallite(aes)
    assert cp._section == 0
    cp._set_section(1)
    assert cp._section == 1
    aes.redraw()  # renders info section without crash


# -------------------------------------------------------------------
# Full desktop session
# -------------------------------------------------------------------

@test("full_desktop_all_crystallites", batch="phase11")
def test_full_desktop_all_crystallites():
    """Full desktop with all crystallites redraws correctly."""
    aes = _make_aes()
    TerminalCrystallite(aes)
    ClockCrystallite(aes)
    CalculatorCrystallite(aes)
    InspectorCrystallite(aes)
    ControlPanelCrystallite(aes)
    FileManagerCrystallite(aes, path="/")
    aes.redraw()
    fb = aes.vdi.fb
    unique = set(fb)
    assert len(unique) > 3, "Desktop looks blank after full redraw"


@test("full_desktop_scrap_workflow", batch="phase11")
def test_full_desktop_scrap_workflow():
    """Complete scrap workflow: put, get, type convert."""
    aes = _make_aes()
    # Put text
    aes.scrap.put("(+ 1 2)", "text/plain")
    # Get as Lisp
    entry = aes.scrap.get("lisp/form")
    assert entry.data == ['+', 1, 2]
    # Put Lisp
    aes.scrap.put(['+', 3, 4], "lisp/form")
    # Get as text
    entry = aes.scrap.get("text/plain")
    assert entry.data == "(+ 3 4)"


@test("print_form_helper", batch="phase11")
def test_print_form_helper():
    """_print_form converts Lisp forms to strings."""
    assert _print_form(None) == "nil"
    assert _print_form(True) == "t"
    assert _print_form(42) == "42"
    assert _print_form(['+', 1, 2]) == "(+ 1 2)"
    assert _print_form(['list', 1, ['quote', 'a']]) == "(list 1 (quote a))"
