"""Crystal Desktop — Window Manager and Applications for the LM-1 List Machine.

Phase 13: OS Foundation rewrite.  Integrates the widget toolkit, virtual
filesystem, and icon system to deliver a modern desktop experience with
taskbar, desktop icons, minimize/maximize, and real applications.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .vdi import (
    VDI, BG_TRANSPARENT, GRAD_VERTICAL,
    EVT_NONE, EVT_KEY_DOWN, EVT_KEY_UP,
    EVT_MOUSE_MOVE, EVT_MOUSE_DOWN, EVT_MOUSE_UP, EVT_QUIT,
)
from .vfs import VFS, VFSFile, VFSDirectory
from .icons import Icon, get_icon, icon_for_name, icon_for_mime


# Helper: normalize a VFS path string
def _vfs_normalize(path: str) -> str:
    """Normalize a VFS path to canonical form."""
    parts: list[str] = []
    for p in path.strip('/').split('/'):
        if not p or p == '.':
            continue
        if p == '..':
            if parts:
                parts.pop()
        else:
            parts.append(p)
    return '/' + '/'.join(parts) if parts else '/'


# ===================================================================
# Window flags
# ===================================================================

WIN_CLOSEABLE   = 0x01
WIN_MOVEABLE    = 0x02
WIN_RESIZABLE   = 0x04
WIN_MAXIMIZABLE = 0x08
WIN_MINIMIZABLE = 0x10

# ===================================================================
# Layout constants
# ===================================================================

TITLE_BAR_H  = 28
BORDER_W     = 2
MENU_BAR_H   = 22
TASKBAR_H    = 34
MIN_WIN_W    = 120
MIN_WIN_H    = 80
CLOSE_BTN_W  = 16
CLOSE_BTN_H  = 16
MAX_BTN_W    = 14
MAX_BTN_H    = 14
MIN_BTN_W    = 14
MIN_BTN_H    = 14
GRIP_SIZE    = 12


# ===================================================================
# Color theme — dark retro-futuristic
# ===================================================================

class Colors:
    """Central color palette — dark retro-futuristic theme."""

    # Desktop
    DESKTOP_BG       = 0x1A1A2E
    DESKTOP_BG_END   = 0x16213E
    DESKTOP_ICON_TXT = 0xCCCCDD
    DESKTOP_ICON_SEL = 0x335588

    # Window chrome
    TITLE_BAR_ACTIVE     = 0x2A3A5C
    TITLE_BAR_ACTIVE_END = 0x1E2D4A
    TITLE_BAR_INACTIVE     = 0x2A2A3A
    TITLE_BAR_INACTIVE_END = 0x1E1E2E
    TITLE_TEXT        = 0xDDDDEE
    TITLE_TEXT_SHADOW = 0x0A0A14
    WINDOW_BG         = 0x1E1E2E
    WINDOW_BORDER     = 0x333355

    # Close / min / max buttons
    CLOSE_BTN_BG = 0xCC4444
    CLOSE_BTN_X  = 0xFFFFFF
    MAX_BTN_BG   = 0x44AA44
    MAX_BTN_FG   = 0xFFFFFF
    MIN_BTN_BG   = 0xDDAA22
    MIN_BTN_FG   = 0xFFFFFF

    # Menu bar
    MENU_BAR_BG       = 0x14141E
    MENU_BAR_BG_END   = 0x1A1A28
    MENU_BAR_TEXT      = 0xBBBBCC
    MENU_BAR_SEPARATOR = 0x333355
    MENU_HIGHLIGHT     = 0x335588
    MENU_HI_TEXT       = 0xFFFFFF

    # Dropdown menu
    DROPDOWN_BG        = 0x1A1A28
    DROPDOWN_BORDER    = 0x444466
    DROPDOWN_TEXT      = 0xCCCCDD
    DROPDOWN_SEPARATOR = 0x333355

    # Buttons
    BUTTON_BG     = 0x2A2A3E
    BUTTON_BG_END = 0x222236
    BUTTON_BORDER = 0x444466
    BUTTON_TEXT   = 0xCCCCDD

    # Taskbar
    TASKBAR_BG       = 0x101018
    TASKBAR_BG_END   = 0x181824
    TASKBAR_BTN_BG   = 0x222236
    TASKBAR_BTN_ACT  = 0x335588
    TASKBAR_BTN_TXT  = 0xBBBBCC
    TASKBAR_SEP      = 0x333355
    TASKBAR_CLOCK    = 0x88BBDD

    # Resize grip
    GRIP_DOT     = 0x555577
    SHADOW_ALPHA = 40

    # Accent colors
    BLACK      = 0x000000
    WHITE      = 0xFFFFFF
    RED        = 0xFF4444
    GREEN      = 0x44FF88
    BLUE       = 0x4488FF
    CYAN       = 0x44DDFF
    YELLOW     = 0xFFDD44
    MAGENTA    = 0xDD44FF
    ORANGE     = 0xFF8844
    DARK_GRAY  = 0x666688
    LIGHT_GRAY = 0xAAAABB


# ===================================================================
# Scrap (Clipboard) system
# ===================================================================

@dataclass
class ScrapEntry:
    """A single scrap (clipboard) entry.

    Attributes:
        data:        The payload — text, Lisp form, binary, etc.
        scrap_type:  MIME-style type tag ('text/plain', 'lisp/form', …).
        timestamp:   When this entry was created.
    """
    data: object
    scrap_type: str
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class Scrap:
    """Desktop clipboard with type negotiation and history ring.

    Scrap supports multiple data types and can convert between them
    (e.g. text/plain ↔ lisp/form) using type negotiation.
    """
    MAX_HISTORY = 16

    def __init__(self):
        self._current: Optional[ScrapEntry] = None
        self.history: list[ScrapEntry] = []

    @property
    def empty(self) -> bool:
        return self._current is None

    def put(self, data: object, scrap_type: str = "text/plain") -> None:
        """Place data on the scrap."""
        entry = ScrapEntry(data=data, scrap_type=scrap_type)
        self._current = entry
        self.history.append(entry)
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[-self.MAX_HISTORY:]

    def get(self, requested_type: str | None = None) -> ScrapEntry | None:
        """Get current scrap, optionally converting to requested_type."""
        if self._current is None:
            return None
        if requested_type is None or requested_type == self._current.scrap_type:
            return self._current
        return self._convert(self._current, requested_type)

    def clear(self) -> None:
        """Clear the scrap."""
        self._current = None
        self.history.clear()

    def _convert(self, entry: ScrapEntry, target: str) -> ScrapEntry | None:
        """Type negotiation — convert between data types."""
        if entry.scrap_type == "lisp/form" and target == "text/plain":
            return ScrapEntry(data=_print_form(entry.data),
                              scrap_type="text/plain",
                              timestamp=entry.timestamp)
        if entry.scrap_type == "text/plain" and target == "lisp/form":
            try:
                from .compiler import parse
                forms = parse(str(entry.data))
                result = forms[0] if len(forms) == 1 else forms
                return ScrapEntry(data=result,
                                  scrap_type="lisp/form",
                                  timestamp=entry.timestamp)
            except Exception:
                return None
        return None


# ===================================================================
# Helper: print a Lisp form as a string
# ===================================================================

def _print_form(form) -> str:
    """Convert a Lisp form to a readable string."""
    if form is None:
        return "nil"
    if form is True:
        return "t"
    if form is False:
        return "nil"
    if isinstance(form, list):
        return "(" + " ".join(_print_form(x) for x in form) + ")"
    return str(form)


# ===================================================================
# Resource Database
# ===================================================================

class ResourceDB:
    """Resource database — stores UI resources (strings, menus, alerts)
    by (type, name) key.  Supports Lisp-format loading and serialization.
    """

    def __init__(self):
        self._resources: dict[tuple[str, str], object] = {}

    # -- CRUD --------------------------------------------------------

    def put(self, rtype: str, name: str, data: object) -> None:
        self._resources[(rtype, name)] = data

    def get(self, rtype: str, name: str) -> object | None:
        return self._resources.get((rtype, name))

    def delete(self, rtype: str, name: str) -> bool:
        return self._resources.pop((rtype, name), None) is not None

    def list_resources(self) -> list[tuple[str, str]]:
        return list(self._resources.keys())

    # -- Lisp serialization ------------------------------------------

    def load_from_lisp(self, source: str) -> int:
        """Load resources from Lisp source text.  Returns count loaded."""
        from .compiler import parse
        forms = parse(source)
        count = 0
        for form in forms:
            if isinstance(form, list) and len(form) >= 4 and form[0] == 'resource':
                rtype = str(form[1])
                name = str(form[2])
                data = form[3:]
                if len(data) == 1:
                    data = data[0]
                self.put(rtype, name, data)
                count += 1
        return count

    def to_lisp(self) -> str:
        """Serialize all resources to Lisp source."""
        lines: list[str] = []
        for (rtype, name), data in sorted(self._resources.items()):
            lines.append(f"(resource {rtype} {name} {_print_form(data)})")
        return "\n".join(lines) + "\n"

    # -- Menu builder ------------------------------------------------

    def build_menu(self, name: str,
                   commands: dict[str, Callable] | None = None
                   ) -> list[Menu] | None:
        """Build Menu objects from a resource definition."""
        data = self.get("menu", name)
        if data is None or not isinstance(data, list):
            return None
        commands = commands or {}
        menus: list[Menu] = []
        for submenu in data:
            if not isinstance(submenu, list) or submenu[0] != 'submenu':
                continue
            label = str(submenu[1])
            items: list[MenuItem] = []
            for entry in submenu[2:]:
                if isinstance(entry, list):
                    if entry[0] == 'menu-item':
                        item_label = str(entry[1])
                        cmd = str(entry[2]) if len(entry) > 2 else ""
                        cb = commands.get(cmd)
                        items.append(MenuItem(label=item_label, callback=cb))
                    elif entry[0] == 'separator':
                        items.append(MenuItem(label="", separator=True))
            menus.append(Menu(label=label, items=items))
        return menus

    # -- Alert helper ------------------------------------------------

    def show_alert(self, aes: AES, name: str) -> Window | None:
        """Show an alert dialog from a stored resource."""
        data = self.get("alert", name)
        if data is None or not isinstance(data, list):
            return None
        title = str(data[0]) if data else "Alert"
        body = [str(x) for x in data[1:]]

        w = 300
        h = 80 + len(body) * 25
        x = (aes.vdi.width - w) // 2
        y = (aes.vdi.height - h) // 2

        def draw_alert(vdi: VDI, win: Window):
            cx, cy, cw, ch = win.client_rect()
            cw_f = vdi.font.char_w
            ch_f = vdi.font.char_h
            vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)
            for i, line in enumerate(body):
                tx = cx + (cw - len(line) * cw_f) // 2
                ty = cy + 10 + i * (ch_f + 4)
                vdi.draw_string(tx, ty, line, Colors.DROPDOWN_TEXT,
                                Colors.DROPDOWN_BG)

        return aes.create_window(title, x, y, w, h,
                                 flags=WIN_CLOSEABLE | WIN_MOVEABLE,
                                 on_redraw=draw_alert)


# ===================================================================
# Desktop Profile  (save / load window layout)
# ===================================================================

class DesktopProfile:
    """Serialize and restore desktop window layouts."""

    @staticmethod
    def save(aes: AES) -> str:
        """Serialize current desktop to Lisp."""
        from .compiler import parse
        lines = ["(desktop-profile"]
        for win in aes._windows:
            wtype = DesktopProfile._infer_type(win)
            lines.append(
                f"  (window {wtype} {win.x} {win.y} {win.w} {win.h})"
            )
        lines.append(")")
        return "\n".join(lines)

    @staticmethod
    def load(aes: AES, source: str) -> int:
        """Restore desktop from Lisp source.  Returns window count."""
        from .compiler import parse
        forms = parse(source)
        count = 0
        for form in forms:
            if isinstance(form, list) and form[0] == 'desktop-profile':
                for entry in form[1:]:
                    if isinstance(entry, list) and entry[0] == 'window':
                        if DesktopProfile._restore_window(aes, entry):
                            count += 1
        return count

    @staticmethod
    def save_to_file(aes: AES, path: str) -> None:
        data = DesktopProfile.save(aes)
        with open(path, 'w') as f:
            f.write(data)

    @staticmethod
    def load_from_file(aes: AES, path: str) -> int:
        with open(path) as f:
            return DesktopProfile.load(aes, f.read())

    @staticmethod
    def _infer_type(win: Window) -> str:
        """Infer crystallite type from window properties."""
        ct = getattr(win, '_cryst_type', '')
        if ct:
            return ct
        title = win.title.lower()
        if 'terminal' in title:
            return 'terminal'
        if 'clock' in title:
            return 'clock'
        if 'calculator' in title:
            return 'calculator'
        if 'inspector' in title:
            return 'inspector'
        if 'control' in title:
            return 'control-panel'
        if 'editor' in title:
            return 'editor'
        return 'window'

    @staticmethod
    def _restore_window(aes: AES, entry: list) -> bool:
        """Restore a single window from a profile entry."""
        if len(entry) < 6:
            return False
        wtype = str(entry[1])
        x, y, w, h = int(entry[2]), int(entry[3]), int(entry[4]), int(entry[5])
        if wtype == 'terminal':
            TerminalCrystallite(aes, x=x, y=y, w=w, h=h)
        elif wtype == 'clock':
            ClockCrystallite(aes, x=x, y=y)
        elif wtype == 'calculator':
            CalculatorCrystallite(aes, x=x, y=y)
        elif wtype == 'inspector':
            InspectorCrystallite(aes, x=x, y=y, w=w, h=h)
        elif wtype == 'control-panel':
            ControlPanelCrystallite(aes, x=x, y=y)
        elif wtype == 'editor':
            TextEditorCrystallite(aes, x=x, y=y, w=w, h=h)
        else:
            aes.create_window(wtype, x, y, w, h)
        return True


# ===================================================================
# Window
# ===================================================================

@dataclass
class Window:
    """A desktop window managed by the AES."""
    wid: int
    title: str
    x: int
    y: int
    w: int
    h: int
    flags: int = WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE
    visible: bool = True
    on_redraw: Optional[Callable] = None
    on_click: Optional[Callable] = None
    on_key: Optional[Callable] = None
    on_close: Optional[Callable] = None
    on_resize: Optional[Callable] = None
    menu: Optional[list] = None
    scroll_x: int = 0
    scroll_y: int = 0
    minimized: bool = False
    maximized: bool = False
    _pre_max_rect: Optional[tuple] = None

    def client_rect(self) -> tuple[int, int, int, int]:
        """Return (cx, cy, cw, ch) of the content area."""
        return (self.x + BORDER_W,
                self.y + TITLE_BAR_H,
                self.w - 2 * BORDER_W,
                self.h - TITLE_BAR_H - BORDER_W)

    def contains(self, sx: int, sy: int) -> bool:
        return (self.x <= sx < self.x + self.w and
                self.y <= sy < self.y + self.h)

    def in_title_bar(self, mx: int, my: int) -> bool:
        return (self.x <= mx < self.x + self.w and
                self.y <= my < self.y + TITLE_BAR_H)

    def in_close_button(self, mx: int, my: int) -> bool:
        if not (self.flags & WIN_CLOSEABLE):
            return False
        bx, by = self.x + 3, self.y + 2
        return (bx <= mx < bx + CLOSE_BTN_W and
                by <= my < by + CLOSE_BTN_H)

    def in_maximize_button(self, mx: int, my: int) -> bool:
        if not (self.flags & WIN_MAXIMIZABLE):
            return False
        bx = self.x + self.w - 3 - MAX_BTN_W
        by = self.y + (TITLE_BAR_H - MAX_BTN_H) // 2
        return (bx <= mx < bx + MAX_BTN_W and
                by <= my < by + MAX_BTN_H)

    def in_minimize_button(self, mx: int, my: int) -> bool:
        if not (self.flags & WIN_MINIMIZABLE):
            return False
        bx = self.x + self.w - 3 - MAX_BTN_W - 4 - MIN_BTN_W
        by = self.y + (TITLE_BAR_H - MIN_BTN_H) // 2
        return (bx <= mx < bx + MIN_BTN_W and
                by <= my < by + MIN_BTN_H)

    def in_resize_grip(self, mx: int, my: int) -> bool:
        if not (self.flags & WIN_RESIZABLE):
            return False
        gx = self.x + self.w - GRIP_SIZE
        gy = self.y + self.h - GRIP_SIZE
        return gx <= mx < self.x + self.w and gy <= my < self.y + self.h


# ===================================================================
# Menu
# ===================================================================

@dataclass
class MenuItem:
    label: str
    callback: Optional[Callable] = None
    separator: bool = False
    enabled: bool = True

@dataclass
class Menu:
    label: str
    items: list[MenuItem] = field(default_factory=list)


# ===================================================================
# Desktop icon (on the wallpaper)
# ===================================================================

@dataclass
class DesktopIcon:
    """An icon on the desktop background."""
    label: str
    icon_name: str
    action: Optional[Callable] = None
    grid_row: int = 0
    grid_col: int = 0


# ===================================================================
# AES — Application Environment Services (Window Manager)
# ===================================================================

class AES:
    """Crystal Desktop window manager.

    Manages windows, menus, taskbar, desktop icons, and the global
    event loop.  Integrates the virtual filesystem and icon system.
    """

    def __init__(self, vdi: VDI):
        self.vdi = vdi

        # Virtual filesystem
        self.vfs = VFS()
        self.vfs.populate_default()

        # Scrap (clipboard)
        self.scrap = Scrap()

        # Resource database
        self.resources: Optional[ResourceDB] = None

        # Window management
        self._windows: list[Window] = []
        self._focused: Optional[Window] = None
        self._next_wid = 1

        # Drag / resize state
        self._dragging: Optional[Window] = None
        self._drag_offset: tuple[int, int] = (0, 0)
        self._resizing: Optional[Window] = None
        self._resize_offset: tuple[int, int] = (0, 0)

        # Menus
        self._system_menus: list[Menu] = [
            Menu("Crystal", [
                MenuItem("About Crystal Desktop",
                         callback=self._show_about),
            ]),
        ]
        self._menu_open: int = -1
        self._menu_highlight: int = -1

        # Desktop icons
        self._desktop_icons: list[DesktopIcon] = []

        # Taskbar state
        self._taskbar_buttons: list[Window] = []   # refreshed each redraw

        # Tick objects (crystallites with tick() method)
        self._tick_objects: list = []

        # Runtime
        self._running = True
        self._dirty = True

        # Double-click detection for desktop icons
        self._last_desktop_click_time: float = 0.0
        self._last_desktop_click_pos: tuple[int, int] = (0, 0)
        self._last_desktop_click_idx: int = -1

    # ------------------------------------------------------------------
    # Window CRUD
    # ------------------------------------------------------------------

    def create_window(self, title: str, x: int, y: int, w: int, h: int,
                      flags: int = WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE,
                      on_redraw: Callable | None = None,
                      on_click: Callable | None = None,
                      on_key: Callable | None = None,
                      on_close: Callable | None = None,
                      on_resize: Callable | None = None,
                      menu: list[Menu] | None = None) -> Window:
        """Create and register a new window."""
        win = Window(wid=self._next_wid, title=title,
                     x=x, y=y, w=w, h=h, flags=flags,
                     on_redraw=on_redraw, on_click=on_click,
                     on_key=on_key, on_close=on_close,
                     on_resize=on_resize, menu=menu)
        self._next_wid += 1
        self._windows.append(win)
        self._focused = win
        self._dirty = True
        return win

    def close_window(self, win: Window) -> None:
        """Close (remove) a window.  Calls on_close; if it returns False, cancel."""
        if win.on_close and win.on_close(win) is False:
            return
        if win in self._windows:
            self._windows.remove(win)
        if self._focused is win:
            self._focused = self._windows[-1] if self._windows else None
        # Remove any tick objects associated with this window
        self._tick_objects = [t for t in self._tick_objects
                              if not (hasattr(t, 'win') and t.win is win)]
        self._dirty = True

    def raise_window(self, win: Window) -> None:
        """Bring window to front."""
        if win in self._windows:
            self._windows.remove(win)
            self._windows.append(win)
        self._focused = win
        self._dirty = True

    def lower_window(self, win: Window) -> None:
        """Send window to back."""
        if win in self._windows:
            self._windows.remove(win)
            self._windows.insert(0, win)
        if self._focused is win and self._windows:
            self._focused = self._windows[-1]
        self._dirty = True

    def find_window_at(self, sx: int, sy: int) -> Window | None:
        """Find topmost visible, non-minimized window at screen position."""
        for win in reversed(self._windows):
            if win.visible and not win.minimized and win.contains(sx, sy):
                return win
        return None

    # ------------------------------------------------------------------
    # Minimize / Maximize / Restore
    # ------------------------------------------------------------------

    def minimize_window(self, win: Window) -> None:
        """Minimize a window (hide, show in taskbar)."""
        win.minimized = True
        if self._focused is win:
            # Focus next visible window
            self._focused = None
            for w in reversed(self._windows):
                if w.visible and not w.minimized:
                    self._focused = w
                    break
        self._dirty = True

    def maximize_window(self, win: Window) -> None:
        """Toggle maximize: fill work area or restore."""
        if win.maximized:
            self.restore_window(win)
        else:
            win._pre_max_rect = (win.x, win.y, win.w, win.h)
            win.x = 0
            win.y = MENU_BAR_H
            win.w = self.vdi.width
            win.h = self.vdi.height - MENU_BAR_H - TASKBAR_H
            win.maximized = True
            if win.on_resize:
                win.on_resize(win, win.w, win.h)
            self._dirty = True

    def restore_window(self, win: Window) -> None:
        """Restore a minimized or maximized window."""
        if win.maximized and win._pre_max_rect:
            win.x, win.y, win.w, win.h = win._pre_max_rect
            win.maximized = False
            if win.on_resize:
                win.on_resize(win, win.w, win.h)
        win.minimized = False
        self.raise_window(win)
        self._dirty = True

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def redraw(self) -> None:
        """Full desktop redraw."""
        vdi = self.vdi

        # 1. Desktop background gradient
        vdi.grad_rect(0, 0, vdi.width, vdi.height,
                      Colors.DESKTOP_BG, Colors.DESKTOP_BG_END,
                      GRAD_VERTICAL)

        # 2. Desktop icons
        self._draw_desktop_icons()

        # 3. Window shadows (non-minimized)
        for win in self._windows:
            if win.visible and not win.minimized:
                vdi.shadow_rect(win.x, win.y, win.w, win.h,
                                radius=6, alpha=Colors.SHADOW_ALPHA)

        # 4. Windows (bottom to top, non-minimized)
        for win in self._windows:
            if win.visible and not win.minimized:
                self._draw_window(win)

        # 5. Menu bar (on top of everything)
        self._draw_menu_bar()

        # 6. Open dropdown menu
        if self._menu_open >= 0:
            self._draw_dropdown()

        # 7. Taskbar (on top of everything)
        self._draw_taskbar()

        vdi.present()
        self._dirty = False

    def _draw_window(self, win: Window) -> None:
        """Draw a single window with modern decorations."""
        vdi = self.vdi
        is_active = (win is self._focused)

        # Outer border
        vdi.fill_rect(win.x, win.y, win.w, win.h, Colors.WINDOW_BORDER)

        # Title bar gradient
        if is_active:
            vdi.grad_rect(win.x + 1, win.y + 1,
                          win.w - 2, TITLE_BAR_H - 1,
                          Colors.TITLE_BAR_ACTIVE,
                          Colors.TITLE_BAR_ACTIVE_END,
                          GRAD_VERTICAL)
        else:
            vdi.grad_rect(win.x + 1, win.y + 1,
                          win.w - 2, TITLE_BAR_H - 1,
                          Colors.TITLE_BAR_INACTIVE,
                          Colors.TITLE_BAR_INACTIVE_END,
                          GRAD_VERTICAL)

        # Close button — red circle with × (left side)
        if win.flags & WIN_CLOSEABLE:
            btn_cx = win.x + 3 + CLOSE_BTN_W // 2
            btn_cy = win.y + 2 + CLOSE_BTN_H // 2
            btn_r = min(CLOSE_BTN_W, CLOSE_BTN_H) // 2 - 1
            vdi.fill_circle(btn_cx, btn_cy, btn_r, Colors.CLOSE_BTN_BG)
            xr = btn_r - 3
            vdi.draw_line(btn_cx - xr, btn_cy - xr,
                          btn_cx + xr, btn_cy + xr, Colors.CLOSE_BTN_X)
            vdi.draw_line(btn_cx + xr, btn_cy - xr,
                          btn_cx - xr, btn_cy + xr, Colors.CLOSE_BTN_X)

        # Maximize button — green circle with □ (right side)
        if win.flags & WIN_MAXIMIZABLE:
            mbx = win.x + win.w - 3 - MAX_BTN_W
            mby = win.y + (TITLE_BAR_H - MAX_BTN_H) // 2
            mcx = mbx + MAX_BTN_W // 2
            mcy = mby + MAX_BTN_H // 2
            mr = min(MAX_BTN_W, MAX_BTN_H) // 2 - 1
            vdi.fill_circle(mcx, mcy, mr, Colors.MAX_BTN_BG)
            # Draw □ or filled indicator
            sr = mr - 3
            if win.maximized:
                vdi.fill_rect(mcx - sr, mcy - sr, sr * 2, sr * 2,
                              Colors.MAX_BTN_FG)
            else:
                for dx in range(-sr, sr + 1):
                    vdi.fb[(mcy - sr) * vdi.width + mcx + dx] = Colors.MAX_BTN_FG
                    vdi.fb[(mcy + sr) * vdi.width + mcx + dx] = Colors.MAX_BTN_FG
                for dy in range(-sr, sr + 1):
                    vdi.fb[(mcy + dy) * vdi.width + mcx - sr] = Colors.MAX_BTN_FG
                    vdi.fb[(mcy + dy) * vdi.width + mcx + sr] = Colors.MAX_BTN_FG

        # Minimize button — yellow circle with — (left of maximize)
        if win.flags & WIN_MINIMIZABLE:
            nbx = win.x + win.w - 3 - MAX_BTN_W - 4 - MIN_BTN_W
            nby = win.y + (TITLE_BAR_H - MIN_BTN_H) // 2
            ncx = nbx + MIN_BTN_W // 2
            ncy = nby + MIN_BTN_H // 2
            nr = min(MIN_BTN_W, MIN_BTN_H) // 2 - 1
            vdi.fill_circle(ncx, ncy, nr, Colors.MIN_BTN_BG)
            lr = nr - 3
            vdi.draw_line(ncx - lr, ncy, ncx + lr, ncy, Colors.MIN_BTN_FG)

        # Title text (centered, avoiding buttons)
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        text_x = win.x + (win.w - len(win.title) * cw_f) // 2
        text_y = win.y + (TITLE_BAR_H - ch_f) // 2
        vdi.draw_string(text_x + 1, text_y + 1, win.title,
                        Colors.TITLE_TEXT_SHADOW, BG_TRANSPARENT)
        vdi.draw_string(text_x, text_y, win.title,
                        Colors.TITLE_TEXT, BG_TRANSPARENT)

        # Client area background
        cx, cy, cw, ch = win.client_rect()
        vdi.fill_rect(cx, cy, cw, ch, Colors.WINDOW_BG)

        # Separator between title and content
        vdi.draw_line(win.x + 1, win.y + TITLE_BAR_H,
                      win.x + win.w - 2, win.y + TITLE_BAR_H,
                      Colors.WINDOW_BORDER)

        # Resize grip
        if win.flags & WIN_RESIZABLE and not win.maximized:
            gx = win.x + win.w - GRIP_SIZE
            gy = win.y + win.h - GRIP_SIZE
            for row in range(3):
                for col in range(3 - row):
                    dx = GRIP_SIZE - 4 - col * 4
                    dy = GRIP_SIZE - 4 - row * 4
                    px, py = gx + dx, gy + dy
                    if 0 <= px < vdi.width and 0 <= py < vdi.height:
                        vdi.fb[py * vdi.width + px] = Colors.GRIP_DOT
                    if 0 <= px + 1 < vdi.width and 0 <= py < vdi.height:
                        vdi.fb[py * vdi.width + px + 1] = Colors.GRIP_DOT
                    if 0 <= px < vdi.width and 0 <= py + 1 < vdi.height:
                        vdi.fb[(py + 1) * vdi.width + px] = Colors.GRIP_DOT
                    if 0 <= px + 1 < vdi.width and 0 <= py + 1 < vdi.height:
                        vdi.fb[(py + 1) * vdi.width + px + 1] = Colors.GRIP_DOT

        # Window content callback
        if win.on_redraw:
            win.on_redraw(vdi, win)

    # ------------------------------------------------------------------
    # Taskbar
    # ------------------------------------------------------------------

    def _draw_taskbar(self) -> None:
        """Draw the taskbar at the bottom of the screen."""
        vdi = self.vdi
        ty = vdi.height - TASKBAR_H
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h

        # Taskbar background
        vdi.grad_rect(0, ty, vdi.width, TASKBAR_H,
                      Colors.TASKBAR_BG, Colors.TASKBAR_BG_END,
                      GRAD_VERTICAL)
        # Top separator
        vdi.draw_line(0, ty, vdi.width - 1, ty, Colors.TASKBAR_SEP)

        # Crystal button (left)
        crystal_w = 8 * cw_f + 12
        vdi.grad_rect(2, ty + 2, crystal_w, TASKBAR_H - 4,
                      Colors.MENU_HIGHLIGHT, 0x224466, GRAD_VERTICAL)
        vdi.draw_string(8, ty + (TASKBAR_H - ch_f) // 2,
                        "Crystal", Colors.WHITE, BG_TRANSPARENT)

        # Window buttons
        bx = crystal_w + 8
        self._taskbar_buttons = []
        for win in self._windows:
            if not win.visible:
                continue
            max_chars = 16
            btn_label = win.title[:max_chars]
            btn_w = len(btn_label) * cw_f + 12
            if bx + btn_w > vdi.width - 90:
                break

            bg = Colors.TASKBAR_BTN_ACT if win is self._focused and not win.minimized else Colors.TASKBAR_BTN_BG
            vdi.fill_rect(bx, ty + 3, btn_w, TASKBAR_H - 6, bg)
            # Border
            vdi.draw_line(bx, ty + 3, bx + btn_w - 1, ty + 3,
                          Colors.TASKBAR_SEP)
            vdi.draw_line(bx, ty + 3, bx, ty + TASKBAR_H - 4,
                          Colors.TASKBAR_SEP)
            vdi.draw_line(bx + btn_w - 1, ty + 3,
                          bx + btn_w - 1, ty + TASKBAR_H - 4,
                          Colors.TASKBAR_SEP)
            vdi.draw_line(bx, ty + TASKBAR_H - 4,
                          bx + btn_w - 1, ty + TASKBAR_H - 4,
                          Colors.TASKBAR_SEP)

            fg = Colors.WHITE if win is self._focused else Colors.TASKBAR_BTN_TXT
            if win.minimized:
                fg = Colors.DARK_GRAY
            vdi.draw_string(bx + 6, ty + (TASKBAR_H - ch_f) // 2,
                            btn_label, fg, BG_TRANSPARENT)
            self._taskbar_buttons.append(win)
            bx += btn_w + 3

        # Clock (right side)
        time_str = time.strftime("%H:%M")
        tw = len(time_str) * cw_f
        vdi.draw_string(vdi.width - tw - 8,
                        ty + (TASKBAR_H - ch_f) // 2,
                        time_str, Colors.TASKBAR_CLOCK, BG_TRANSPARENT)

    # ------------------------------------------------------------------
    # Desktop icons
    # ------------------------------------------------------------------

    def _draw_desktop_icons(self) -> None:
        """Draw desktop shortcut icons."""
        vdi = self.vdi
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h

        cell_w = 80
        cell_h = 72
        start_x = 16
        start_y = MENU_BAR_H + 12

        for idx, di in enumerate(self._desktop_icons):
            ix = start_x + di.grid_col * cell_w
            iy = start_y + di.grid_row * cell_h

            # Get icon
            icon = get_icon(di.icon_name)
            if icon:
                # Draw icon at 2x (32x32)
                icon_x = ix + (cell_w - 32) // 2
                icon_y = iy
                icon.draw(vdi, icon_x, icon_y, scale=2)

            # Label below icon
            label = di.label
            if len(label) * cw_f > cell_w:
                max_ch = cell_w // cw_f
                label = label[:max_ch - 1] + "\u2026"
            lx = ix + (cell_w - len(label) * cw_f) // 2
            ly = iy + 36
            vdi.draw_string(lx, ly, label,
                            Colors.DESKTOP_ICON_TXT, BG_TRANSPARENT)

    def _desktop_icon_hit(self, sx: int, sy: int) -> int:
        """Return index of desktop icon at screen position, or -1."""
        cw_f = self.vdi.font.char_w
        cell_w = 80
        cell_h = 72
        start_x = 16
        start_y = MENU_BAR_H + 12

        for idx, di in enumerate(self._desktop_icons):
            ix = start_x + di.grid_col * cell_w
            iy = start_y + di.grid_row * cell_h
            if ix <= sx < ix + cell_w and iy <= sy < iy + cell_h:
                return idx
        return -1

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _draw_menu_bar(self) -> None:
        """Draw the global menu bar."""
        vdi = self.vdi
        vdi.grad_rect(0, 0, vdi.width, MENU_BAR_H,
                      Colors.MENU_BAR_BG, Colors.MENU_BAR_BG_END,
                      GRAD_VERTICAL)
        vdi.draw_line(0, MENU_BAR_H - 1, vdi.width - 1, MENU_BAR_H - 1,
                      Colors.MENU_BAR_SEPARATOR)

        x = 8
        menus = self._get_active_menus()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        for i, menu in enumerate(menus):
            label_w = len(menu.label) * cw_f + 12
            if i == self._menu_open:
                vdi.fill_rect(x - 4, 1, label_w, MENU_BAR_H - 2,
                              Colors.MENU_HIGHLIGHT)
                vdi.draw_string(x, (MENU_BAR_H - ch_f) // 2, menu.label,
                                Colors.MENU_HI_TEXT, Colors.MENU_HIGHLIGHT)
            else:
                vdi.draw_string(x, (MENU_BAR_H - ch_f) // 2, menu.label,
                                Colors.MENU_BAR_TEXT, BG_TRANSPARENT)
            x += label_w

    def _draw_dropdown(self) -> None:
        """Draw the currently open dropdown menu."""
        vdi = self.vdi
        menus = self._get_active_menus()
        if self._menu_open < 0 or self._menu_open >= len(menus):
            return
        menu = menus[self._menu_open]
        if not menu.items:
            return

        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h

        x = 8
        for i in range(self._menu_open):
            x += len(menus[i].label) * cw_f + 12

        max_label_w = max(len(item.label) for item in menu.items
                          if not item.separator) if menu.items else 8
        drop_w = max_label_w * cw_f + 16
        drop_h = sum(8 if item.separator else ch_f + 4
                     for item in menu.items) + 4
        drop_x = x - 4
        drop_y = MENU_BAR_H

        vdi.shadow_rect(drop_x, drop_y, drop_w, drop_h, radius=4, alpha=60)
        vdi.fill_rect(drop_x, drop_y, drop_w, drop_h, Colors.DROPDOWN_BG)
        vdi.draw_line(drop_x, drop_y, drop_x + drop_w - 1, drop_y,
                      Colors.DROPDOWN_BORDER)
        vdi.draw_line(drop_x, drop_y, drop_x, drop_y + drop_h - 1,
                      Colors.DROPDOWN_BORDER)
        vdi.draw_line(drop_x + drop_w - 1, drop_y,
                      drop_x + drop_w - 1, drop_y + drop_h - 1,
                      Colors.DROPDOWN_BORDER)
        vdi.draw_line(drop_x, drop_y + drop_h - 1,
                      drop_x + drop_w - 1, drop_y + drop_h - 1,
                      Colors.DROPDOWN_BORDER)

        iy = drop_y + 2
        for idx, item in enumerate(menu.items):
            if item.separator:
                vdi.draw_line(drop_x + 4, iy + 3,
                              drop_x + drop_w - 5, iy + 3,
                              Colors.DROPDOWN_SEPARATOR)
                iy += 8
            else:
                if idx == self._menu_highlight:
                    vdi.fill_rect(drop_x + 1, iy, drop_w - 2, ch_f + 4,
                                  Colors.MENU_HIGHLIGHT)
                    vdi.draw_string(drop_x + 8, iy + 2, item.label,
                                    Colors.MENU_HI_TEXT, Colors.MENU_HIGHLIGHT)
                else:
                    fg = Colors.DROPDOWN_TEXT if item.enabled else Colors.DARK_GRAY
                    vdi.draw_string(drop_x + 8, iy + 2, item.label,
                                    fg, Colors.DROPDOWN_BG)
                iy += ch_f + 4

    def _get_active_menus(self) -> list[Menu]:
        menus = list(self._system_menus)
        if self._focused and self._focused.menu:
            menus.extend(self._focused.menu)
        return menus

    def _menu_hit_test(self, sx: int, sy: int) -> int:
        if sy >= MENU_BAR_H:
            return -1
        x = 8
        menus = self._get_active_menus()
        cw_f = self.vdi.font.char_w
        for i, menu in enumerate(menus):
            label_w = len(menu.label) * cw_f + 12
            if x - 4 <= sx < x - 4 + label_w:
                return i
            x += label_w
        return -1

    def _dropdown_hit_test(self, sx: int, sy: int) -> int:
        menus = self._get_active_menus()
        if self._menu_open < 0 or self._menu_open >= len(menus):
            return -1
        menu = menus[self._menu_open]
        cw_f = self.vdi.font.char_w
        ch_f = self.vdi.font.char_h
        x = 8
        for i in range(self._menu_open):
            x += len(menus[i].label) * cw_f + 12
        max_label_w = max(len(item.label) for item in menu.items
                          if not item.separator) if menu.items else 8
        drop_w = max_label_w * cw_f + 16
        drop_x = x - 4
        drop_y = MENU_BAR_H

        if not (drop_x <= sx < drop_x + drop_w and sy >= drop_y):
            return -1

        iy = drop_y + 2
        for idx, item in enumerate(menu.items):
            item_h = 8 if item.separator else ch_f + 4
            if iy <= sy < iy + item_h:
                if not item.separator and item.enabled:
                    return idx
                return -1
            iy += item_h
        return -1

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def handle_event(self, evt_type: int, data1: int, data2: int) -> None:
        """Process a single input event."""
        if evt_type == EVT_QUIT:
            self._running = False
            return
        if evt_type == EVT_MOUSE_DOWN:
            mx, my = data1, data2 & 0xFFFF
            button = (data2 >> 16) & 0xFF
            self._on_mouse_down(mx, my, button)
        elif evt_type == EVT_MOUSE_UP:
            mx, my = data1, data2 & 0xFFFF
            button = (data2 >> 16) & 0xFF
            self._on_mouse_up(mx, my, button)
        elif evt_type == EVT_MOUSE_MOVE:
            mx, my = data1, data2
            self._on_mouse_move(mx, my)
        elif evt_type == EVT_KEY_DOWN:
            key, mod = data1, data2
            self._on_key_down(key, mod)

    def _on_mouse_down(self, mx: int, my: int, button: int) -> None:
        """Handle mouse button press."""
        # 1. Menu bar
        if my < MENU_BAR_H:
            idx = self._menu_hit_test(mx, my)
            if idx >= 0:
                if self._menu_open == idx:
                    self._menu_open = -1
                else:
                    self._menu_open = idx
                    self._menu_highlight = -1
                self._dirty = True
                return

        # 2. Dropdown menu
        if self._menu_open >= 0:
            item_idx = self._dropdown_hit_test(mx, my)
            if item_idx >= 0:
                menus = self._get_active_menus()
                item = menus[self._menu_open].items[item_idx]
                self._menu_open = -1
                self._menu_highlight = -1
                self._dirty = True
                if item.callback:
                    item.callback()
                return
            else:
                self._menu_open = -1
                self._menu_highlight = -1
                self._dirty = True

        # 3. Taskbar area
        if my >= self.vdi.height - TASKBAR_H:
            self._on_taskbar_click(mx, my)
            return

        # 4. Find window under cursor
        win = self.find_window_at(mx, my)
        if win is None:
            # Desktop icon?
            icon_idx = self._desktop_icon_hit(mx, my)
            if icon_idx >= 0:
                self._on_desktop_icon_click(icon_idx, mx, my)
            return

        # Raise and focus
        if win is not self._focused:
            self.raise_window(win)
            self._dirty = True

        # Close button?
        if win.in_close_button(mx, my):
            self.close_window(win)
            return

        # Minimize button?
        if win.in_minimize_button(mx, my):
            self.minimize_window(win)
            return

        # Maximize button?
        if win.in_maximize_button(mx, my):
            self.maximize_window(win)
            return

        # Title bar drag?
        if win.in_title_bar(mx, my) and (win.flags & WIN_MOVEABLE):
            if not win.maximized:
                self._dragging = win
                self._drag_offset = (mx - win.x, my - win.y)
            return

        # Resize grip?
        if win.in_resize_grip(mx, my) and not win.maximized:
            self._resizing = win
            self._resize_offset = (mx - win.w, my - win.h)
            return

        # Click in client area
        cx, cy, cw, ch = win.client_rect()
        if cx <= mx < cx + cw and cy <= my < cy + ch:
            if win.on_click:
                win.on_click(win, mx - cx, my - cy, button)

    def _on_mouse_up(self, mx: int, my: int, button: int) -> None:
        if self._dragging:
            self._dragging = None
            self._dirty = True
        if self._resizing:
            self._resizing = None
            self._dirty = True

    def _on_mouse_move(self, mx: int, my: int) -> None:
        if self._dragging:
            win = self._dragging
            ox, oy = self._drag_offset
            win.x = max(0, min(mx - ox, self.vdi.width - 20))
            win.y = max(MENU_BAR_H, min(my - oy,
                                        self.vdi.height - TASKBAR_H - 20))
            self._dirty = True
            return

        if self._resizing:
            win = self._resizing
            ox, oy = self._resize_offset
            new_w = max(MIN_WIN_W, mx - ox)
            new_h = max(MIN_WIN_H, my - oy)
            win.w = min(new_w, self.vdi.width - win.x)
            win.h = min(new_h, self.vdi.height - TASKBAR_H - win.y)
            if win.on_resize:
                win.on_resize(win, win.w, win.h)
            self._dirty = True
            return

        # Menu highlight
        if self._menu_open >= 0:
            if my < MENU_BAR_H:
                idx = self._menu_hit_test(mx, my)
                if idx >= 0 and idx != self._menu_open:
                    self._menu_open = idx
                    self._menu_highlight = -1
                    self._dirty = True
                    return
            item_idx = self._dropdown_hit_test(mx, my)
            if item_idx != self._menu_highlight:
                self._menu_highlight = item_idx
                self._dirty = True

        self.vdi.set_cursor(mx, my, True)

    def _on_key_down(self, key: int, mod: int) -> None:
        if self._focused and self._focused.on_key:
            self._focused.on_key(self._focused, key, mod)

    def _on_taskbar_click(self, mx: int, my: int) -> None:
        """Handle click on the taskbar."""
        cw_f = self.vdi.font.char_w
        crystal_w = 8 * cw_f + 12

        # Crystal button click → toggle Crystal menu
        if mx < crystal_w + 4:
            if self._menu_open == 0:
                self._menu_open = -1
            else:
                self._menu_open = 0
                self._menu_highlight = -1
            self._dirty = True
            return

        # Window buttons
        bx = crystal_w + 8
        for win in self._windows:
            if not win.visible:
                continue
            max_chars = 16
            btn_label = win.title[:max_chars]
            btn_w = len(btn_label) * cw_f + 12
            if bx + btn_w > self.vdi.width - 90:
                break
            if bx <= mx < bx + btn_w:
                if win.minimized:
                    self.restore_window(win)
                elif win is self._focused:
                    self.minimize_window(win)
                else:
                    self.raise_window(win)
                return
            bx += btn_w + 3

    def _on_desktop_icon_click(self, idx: int, mx: int, my: int) -> None:
        """Handle click on a desktop icon — double-click to activate."""
        now = time.time()
        if (self._last_desktop_click_idx == idx and
                now - self._last_desktop_click_time < 0.4 and
                abs(mx - self._last_desktop_click_pos[0]) < 5 and
                abs(my - self._last_desktop_click_pos[1]) < 5):
            # Double-click → launch
            di = self._desktop_icons[idx]
            if di.action:
                di.action()
            self._last_desktop_click_idx = -1
        else:
            self._last_desktop_click_idx = idx
            self._last_desktop_click_time = now
            self._last_desktop_click_pos = (mx, my)
        self._dirty = True

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------

    def _show_about(self) -> None:
        w = 320
        h = 160
        x = (self.vdi.width - w) // 2
        y = (self.vdi.height - h) // 2

        def draw_about(vdi: VDI, win: Window):
            cx, cy, cw, ch = win.client_rect()
            cw_f = vdi.font.char_w
            ch_f = vdi.font.char_h
            vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)
            lines = [
                "Crystal Desktop v2.0",
                "",
                "LM-1 List Machine",
                "with Virtual Filesystem",
                "",
                "Click to close",
            ]
            for i, line in enumerate(lines):
                tx = cx + (cw - len(line) * cw_f) // 2
                ty = cy + 8 + i * (ch_f + 2)
                fg = Colors.CYAN if i == 0 else Colors.DROPDOWN_TEXT
                vdi.draw_string(tx, ty, line, fg, Colors.DROPDOWN_BG)

        self.create_window("About", x, y, w, h,
                           flags=WIN_CLOSEABLE | WIN_MOVEABLE,
                           on_redraw=draw_about)
        self._dirty = True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, fps: int = 30) -> None:
        """Run the desktop event loop."""
        import pygame
        clock = pygame.time.Clock()

        while self._running:
            evt_type, d1, d2 = self.vdi.read_event()
            while evt_type != EVT_NONE:
                self.handle_event(evt_type, d1, d2)
                evt_type, d1, d2 = self.vdi.read_event()

            if self._dirty:
                self.redraw()

            clock.tick(fps)

        self.vdi.close()


# ===================================================================
# Built-in Crystallites
# ===================================================================

class TerminalCrystallite:
    """A terminal/REPL window with Lisp evaluation."""

    def __init__(self, aes: AES, x: int = 60, y: int = 50,
                 w: int = 480, h: int = 320):
        self.aes = aes
        self.lines: list[str] = ["Crystal Terminal v1.0",
                                  "Type 'help' for commands.", ""]
        self.input_buf: str = ""
        self.prompt = "> "
        self.cursor_on = True
        self._cursor_timer = 0.0
        self._commands: dict[str, Callable] = {
            'help': self._cmd_help,
            'clear': self._cmd_clear,
            'about': lambda: aes._show_about(),
            'windows': self._cmd_windows,
            'quit': lambda: setattr(aes, '_running', False),
            'hello': lambda: self._output("Hello from Crystal Desktop!"),
            'time': self._cmd_time,
            'ls': self._cmd_ls,
            'cat': self._cmd_cat,
            'pwd': self._cmd_pwd,
        }

        self.win = aes.create_window(
            "Terminal", x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE
                  | WIN_MAXIMIZABLE | WIN_MINIMIZABLE,
            on_redraw=self._redraw,
            on_key=self._on_key,
            menu=[
                Menu("Edit", [
                    MenuItem("Clear", callback=self._cmd_clear),
                ]),
            ],
        )
        self.win._cryst_type = 'terminal'
        self._cwd = "/"

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, Colors.BLACK)

        max_lines = ch // ch_f
        display_lines = self.lines[-(max_lines - 1):]

        for i, line in enumerate(display_lines):
            vdi.draw_string(cx + 2, cy + 2 + i * ch_f,
                            line[:cw // cw_f],
                            Colors.GREEN, Colors.BLACK)

        input_y = cy + 2 + len(display_lines) * ch_f
        input_line = self.prompt + self.input_buf
        if self.cursor_on:
            input_line += "_"
        vdi.draw_string(cx + 2, input_y,
                        input_line[:cw // cw_f],
                        Colors.GREEN, Colors.BLACK)

    def _on_key(self, win: Window, key: int, mod: int) -> None:
        import pygame
        if key == pygame.K_RETURN:
            self._execute(self.input_buf)
            self.input_buf = ""
            self.aes._dirty = True
        elif key == pygame.K_BACKSPACE:
            if self.input_buf:
                self.input_buf = self.input_buf[:-1]
                self.aes._dirty = True
        elif key == pygame.K_ESCAPE:
            self.input_buf = ""
            self.aes._dirty = True
        elif 32 <= key <= 126:
            ch = chr(key)
            if mod & pygame.KMOD_SHIFT:
                ch = ch.upper()
                shift_map = {
                    '1': '!', '2': '@', '3': '#', '4': '$', '5': '%',
                    '6': '^', '7': '&', '8': '*', '9': '(', '0': ')',
                    '-': '_', '=': '+', '[': '{', ']': '}', '\\': '|',
                    ';': ':', "'": '"', ',': '<', '.': '>', '/': '?',
                    '`': '~',
                }
                if chr(key) in shift_map:
                    ch = shift_map[chr(key)]
            self.input_buf += ch
            self.aes._dirty = True

    def _execute(self, cmd: str) -> None:
        cmd = cmd.strip()
        self.lines.append(self.prompt + cmd)
        if not cmd:
            return

        parts = cmd.split()
        cmd_name = parts[0].lower()

        # Commands that take arguments
        if cmd_name == 'cd' and len(parts) > 1:
            self._cmd_cd(parts[1])
            return
        if cmd_name == 'ls':
            target = parts[1] if len(parts) > 1 else None
            self._cmd_ls(target)
            return
        if cmd_name == 'cat' and len(parts) > 1:
            self._cmd_cat(parts[1])
            return

        if cmd_name in self._commands:
            self._commands[cmd_name]()
        else:
            self._eval_lisp(cmd)

    def _eval_lisp(self, expr: str) -> None:
        try:
            from .compiler import parse
            forms = parse(expr)
            for form in forms:
                result = self._eval_form(form)
                self._output(self._print_form(result))
        except Exception as e:
            self._output(f"Error: {e}")

    def _eval_form(self, form):
        if isinstance(form, int):
            return form
        if isinstance(form, str):
            if form == 'nil' or form is None:
                return None
            if form == 't' or form is True:
                return True
            if form == 'pi':
                return 3.14159
            return form
        if isinstance(form, list):
            if not form:
                return None
            op = form[0]
            if op == 'quote':
                return form[1] if len(form) > 1 else None
            if op == '+':
                args = [self._eval_form(a) for a in form[1:]]
                return sum(a for a in args if isinstance(a, (int, float)))
            if op == '-':
                args = [self._eval_form(a) for a in form[1:]]
                if len(args) == 1:
                    return -args[0]
                return args[0] - sum(args[1:])
            if op == '*':
                args = [self._eval_form(a) for a in form[1:]]
                result = 1
                for a in args:
                    result *= a
                return result
            if op == '/':
                a = self._eval_form(form[1])
                b = self._eval_form(form[2])
                if b == 0:
                    raise ValueError("division by zero")
                return a // b if isinstance(a, int) and isinstance(b, int) else a / b
            if op == 'if':
                cond = self._eval_form(form[1])
                if cond and cond is not None:
                    return self._eval_form(form[2])
                elif len(form) > 3:
                    return self._eval_form(form[3])
                return None
            if op == 'list':
                return [self._eval_form(a) for a in form[1:]]
            if op == 'car':
                lst = self._eval_form(form[1])
                return lst[0] if isinstance(lst, list) and lst else None
            if op == 'cdr':
                lst = self._eval_form(form[1])
                return lst[1:] if isinstance(lst, list) and len(lst) > 1 else None
            if op == 'cons':
                a = self._eval_form(form[1])
                b = self._eval_form(form[2])
                if isinstance(b, list):
                    return [a] + b
                return [a, b]
            if op == 'eq':
                return self._eval_form(form[1]) == self._eval_form(form[2])
            if op == 'fact':
                n = self._eval_form(form[1])
                r = 1
                for i in range(2, n + 1):
                    r *= i
                return r
            raise ValueError(f"unknown function: {op}")
        return form

    def _print_form(self, form) -> str:
        if form is None:
            return "nil"
        if form is True:
            return "t"
        if form is False:
            return "nil"
        if isinstance(form, list):
            return "(" + " ".join(self._print_form(x) for x in form) + ")"
        return str(form)

    def _output(self, text: str) -> None:
        for line in text.split('\n'):
            self.lines.append(line)

    def _cmd_help(self) -> None:
        self._output("Commands: help, clear, about, windows, quit, time")
        self._output("         ls [path], cd <path>, cat <file>, pwd")
        self._output("Lisp: (+ 1 2), (fact 10), (list 1 2 3)")

    def _cmd_clear(self) -> None:
        self.lines.clear()

    def _cmd_windows(self) -> None:
        for win in self.aes._windows:
            st = " [min]" if win.minimized else ""
            self._output(f"  [{win.wid}] {win.title} ({win.w}x{win.h}){st}")

    def _cmd_time(self) -> None:
        self._output(time.strftime("%Y-%m-%d %H:%M:%S"))

    def _cmd_pwd(self) -> None:
        self._output(self._cwd)

    def _cmd_ls(self, path: str | None = None) -> None:
        """List VFS directory."""
        target = path or self._cwd
        if not target.startswith('/'):
            target = self._cwd.rstrip('/') + '/' + target
        try:
            children = self.aes.vfs.list_dir(target)
            for node in children:
                suffix = '/' if node.is_dir else ''
                self._output(f"  {node.name}{suffix}")
            if not children:
                self._output("  (empty)")
        except Exception as e:
            self._output(f"ls: {e}")

    def _cmd_cat(self, path: str) -> None:
        """Display file contents from VFS."""
        if not path.startswith('/'):
            path = self._cwd.rstrip('/') + '/' + path
        try:
            text = self.aes.vfs.read_text(path)
            self._output(text)
        except Exception as e:
            self._output(f"cat: {e}")

    def _cmd_cd(self, path: str) -> None:
        """Change current directory in VFS."""
        if not path.startswith('/'):
            path = self._cwd.rstrip('/') + '/' + path
        try:
            self.aes.vfs.resolve_dir(path)
            self._cwd = _vfs_normalize(path)
        except Exception as e:
            self._output(f"cd: {e}")


class ClockCrystallite:
    """A simple clock desk accessory."""

    def __init__(self, aes: AES, x: int = 480, y: int = 30):
        self.aes = aes
        self._last_time = ""
        self.win = aes.create_window(
            "Clock", x, y, 180, 80,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE,
            on_redraw=self._redraw,
        )
        self.win._cryst_type = 'clock'
        self._update_interval = 1.0
        self._last_update = 0.0
        aes._tick_objects.append(self)

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)
        time_str = time.strftime("%H:%M:%S")
        date_str = time.strftime("%Y-%m-%d")
        tx = cx + (cw - len(time_str) * cw_f) // 2
        vdi.draw_string(tx, cy + 4, time_str, Colors.CYAN, Colors.DROPDOWN_BG)
        dx = cx + (cw - len(date_str) * cw_f) // 2
        vdi.draw_string(dx, cy + 4 + ch_f + 2, date_str,
                        Colors.DROPDOWN_TEXT, Colors.DROPDOWN_BG)
        self._last_time = time_str

    def tick(self) -> None:
        now = time.time()
        if now - self._last_update >= self._update_interval:
            self._last_update = now
            new_time = time.strftime("%H:%M:%S")
            if new_time != self._last_time:
                self.aes._dirty = True


class CalculatorCrystallite:
    """A desktop calculator."""

    def __init__(self, aes: AES, x: int = 300, y: int = 100):
        self.aes = aes
        self.display = "0"
        self._accumulator = 0
        self._operand = ""
        self._operator = ""
        self._new_input = True
        self._buttons = [
            ['7', '8', '9', '/'],
            ['4', '5', '6', '*'],
            ['1', '2', '3', '-'],
            ['0', 'C', '=', '+'],
        ]
        btn_w = 40
        btn_h = 32
        pad = 4
        w = 4 * btn_w + 5 * pad + 2 * BORDER_W
        h = TITLE_BAR_H + 34 + 4 * btn_h + 5 * pad + BORDER_W

        self.win = aes.create_window(
            "Calculator", x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE,
            on_redraw=self._redraw,
            on_click=self._on_click,
            menu=[
                Menu("Edit", [
                    MenuItem("Clear", callback=self._clear),
                ]),
            ],
        )
        self.win._cryst_type = 'calculator'

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h

        vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)

        # Display
        vdi.fill_rect(cx + 4, cy + 4, cw - 8, 24, Colors.BLACK)
        vdi.draw_line(cx + 4, cy + 4, cx + cw - 5, cy + 4,
                      Colors.DROPDOWN_BORDER)
        vdi.draw_line(cx + 4, cy + 4, cx + 4, cy + 27,
                      Colors.DROPDOWN_BORDER)
        display_text = self.display[-cw // cw_f:]
        tx = cx + cw - 6 - len(display_text) * cw_f
        vdi.draw_string(tx, cy + 7, display_text, Colors.CYAN, Colors.BLACK)

        # Buttons
        btn_w, btn_h, pad = 40, 32, 4
        by_start = cy + 34
        for row_idx, row in enumerate(self._buttons):
            for col_idx, label in enumerate(row):
                bx = cx + pad + col_idx * (btn_w + pad)
                by = by_start + row_idx * (btn_h + pad)
                vdi.grad_rect(bx, by, btn_w, btn_h,
                              Colors.BUTTON_BG, Colors.BUTTON_BG_END,
                              GRAD_VERTICAL)
                vdi.draw_line(bx, by, bx + btn_w - 1, by, Colors.BUTTON_BORDER)
                vdi.draw_line(bx, by, bx, by + btn_h - 1, Colors.BUTTON_BORDER)
                vdi.draw_line(bx + btn_w - 1, by,
                              bx + btn_w - 1, by + btn_h - 1, Colors.BUTTON_BORDER)
                vdi.draw_line(bx, by + btn_h - 1,
                              bx + btn_w - 1, by + btn_h - 1, Colors.BUTTON_BORDER)
                lx = bx + (btn_w - len(label) * cw_f) // 2
                ly = by + (btn_h - ch_f) // 2
                vdi.draw_string(lx, ly, label,
                                Colors.BUTTON_TEXT, BG_TRANSPARENT)

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        btn_w, btn_h, pad = 40, 32, 4
        by_start = 34
        for row_idx, row in enumerate(self._buttons):
            for col_idx, label in enumerate(row):
                bx = pad + col_idx * (btn_w + pad)
                by = by_start + row_idx * (btn_h + pad)
                if bx <= cx < bx + btn_w and by <= cy < by + btn_h:
                    self._press(label)
                    self.aes._dirty = True
                    return

    def _press(self, label: str) -> None:
        if label.isdigit():
            if self._new_input:
                self.display = label
                self._new_input = False
            else:
                if self.display == "0":
                    self.display = label
                else:
                    self.display += label
        elif label == 'C':
            self._clear()
        elif label == '=':
            self._compute()
            self._operator = ""
        elif label in '+-*/':
            if self._operator and not self._new_input:
                self._compute()
            self._accumulator = int(self.display) if self.display.lstrip('-').isdigit() else 0
            self._operator = label
            self._new_input = True

    def _compute(self) -> None:
        try:
            val = int(self.display)
            if self._operator == '+':
                self.display = str(self._accumulator + val)
            elif self._operator == '-':
                self.display = str(self._accumulator - val)
            elif self._operator == '*':
                self.display = str(self._accumulator * val)
            elif self._operator == '/':
                self.display = str(self._accumulator // val) if val != 0 else "Error"
            self._accumulator = int(self.display) if self.display.lstrip('-').isdigit() else 0
            self._new_input = True
        except Exception:
            self.display = "Error"
            self._new_input = True

    def _clear(self) -> None:
        self.display = "0"
        self._accumulator = 0
        self._operator = ""
        self._new_input = True


class InspectorCrystallite:
    """Inspector — shows window properties, z-order, pixel info, and VFS stats."""

    def __init__(self, aes: AES, x: int = 20, y: int = 200,
                 w: int = 280, h: int = 300):
        self.aes = aes
        self._inspect_pixel = (0, 0)
        self._pixel_color = 0
        self.win = aes.create_window(
            "Inspector", x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE
                  | WIN_MAXIMIZABLE | WIN_MINIMIZABLE,
            on_redraw=self._redraw,
            on_click=self._on_click,
            menu=[
                Menu("View", [
                    MenuItem("Refresh", callback=lambda: setattr(
                        aes, '_dirty', True)),
                ]),
            ],
        )
        self.win._cryst_type = 'inspector'

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)

        y = cy + 4
        max_cols = cw // cw_f

        def _line(text: str, fg: int = Colors.DROPDOWN_TEXT):
            nonlocal y
            vdi.draw_string(cx + 4, y, text[:max_cols],
                            fg, Colors.DROPDOWN_BG)
            y += ch_f + 1

        _line("--- Desktop ---", Colors.CYAN)
        _line(f"Windows: {len(self.aes._windows)}")
        _line(f"Resolution: {vdi.width}x{vdi.height}")

        # Z-order
        _line("")
        _line("--- Z-Order ---", Colors.CYAN)
        for i, w in enumerate(self.aes._windows):
            marker = "*" if w is self.aes._focused else " "
            st = " [min]" if w.minimized else ""
            _line(f" {marker}[{w.wid}] {w.title}{st}")

        # Focused window details
        fw = self.aes._focused
        if fw and fw is not win:
            _line("")
            _line("--- Focused ---", Colors.CYAN)
            _line(f"Title: {fw.title}")
            _line(f"Pos:   {fw.x},{fw.y}")
            _line(f"Size:  {fw.w}x{fw.h}")
            _line(f"Flags: 0x{fw.flags:02x}")

        # Pixel probe
        _line("")
        _line("--- Pixel ---", Colors.CYAN)
        px, py = self._inspect_pixel
        c = vdi.read_pixel(px, py)
        r, g, b = (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF
        _line(f"({px},{py}): #{c:06X}")
        _line(f"  R={r} G={g} B={b}")

        # Scrap info
        scrap = self.aes.scrap
        _line("")
        _line("--- Scrap ---", Colors.CYAN)
        if scrap.empty:
            _line("(empty)")
        else:
            entry = scrap.get()
            _line(f"Type: {entry.scrap_type}")
            data_str = str(entry.data)[:max_cols - 6]
            _line(f"Data: {data_str}")
            _line(f"History: {len(scrap.history)} items")

        # VFS info
        _line("")
        _line("--- VFS ---", Colors.CYAN)
        vfs = self.aes.vfs
        file_count = 0
        dir_count = 0
        for _dp, dirs, files in vfs.walk("/"):
            dir_count += len(dirs)
            file_count += len(files)
        _line(f"Dirs: {dir_count}  Files: {file_count}")

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        self.aes._dirty = True

    def set_pixel_probe(self, x: int, y: int) -> None:
        self._inspect_pixel = (x, y)
        self.aes._dirty = True


class FileManagerCrystallite:
    """Spatial file manager using the virtual filesystem.

    Displays VFS directory contents with icons.  Each folder is shown
    with a folder icon and files with type-appropriate icons.
    Clicking a folder opens a new window.  Clicking a file puts info
    on the scrap. Double-click a .lisp or .txt file to open in editor.
    """

    def __init__(self, aes: AES, path: str = "/",
                 x: int = 40, y: int = 60, w: int = 420, h: int = 340):
        self.aes = aes
        self.path = _vfs_normalize(path)
        self._entries: list[tuple[str, bool]] = []   # (name, is_dir)
        self._scroll_offset = 0
        self._selected = -1
        self._view_mode = 'icons'   # 'icons' or 'list'
        self._refresh()

        title = self.path.rstrip('/').rsplit('/', 1)[-1] or "/"
        self.win = aes.create_window(
            title, x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE
                  | WIN_MAXIMIZABLE | WIN_MINIMIZABLE,
            on_redraw=self._redraw,
            on_click=self._on_click,
            on_key=self._on_key,
            menu=[
                Menu("File", [
                    MenuItem("Refresh", callback=self._refresh),
                    MenuItem("Parent Folder", callback=self._go_parent),
                ]),
                Menu("View", [
                    MenuItem("Icon View", callback=lambda: self._set_view('icons')),
                    MenuItem("List View", callback=lambda: self._set_view('list')),
                ]),
            ],
        )
        self.win._cryst_type = 'file-manager'

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        self.aes._dirty = True

    def _refresh(self) -> None:
        """Re-read VFS directory contents."""
        self._entries = []
        try:
            children = self.aes.vfs.list_dir(self.path)
            dirs = [(n.name, True) for n in children if n.is_dir]
            files = [(n.name, False) for n in children if not n.is_dir]
            self._entries = dirs + files
        except Exception:
            self._entries = []
        self._selected = -1
        if hasattr(self, 'aes'):
            self.aes._dirty = True

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, Colors.WINDOW_BG)

        # Toolbar area (top 30px)
        toolbar_h = 30
        vdi.grad_rect(cx, cy, cw, toolbar_h,
                      0x222236, 0x1E1E2E, GRAD_VERTICAL)
        vdi.draw_line(cx, cy + toolbar_h - 1, cx + cw - 1, cy + toolbar_h - 1,
                      Colors.WINDOW_BORDER)

        # Toolbar buttons with icons
        tbx = cx + 4
        tby = cy + 3
        tb_btn_h = 24
        tb_btns = [
            ("arrow_back", self._go_parent),
            ("arrow_up", self._go_parent),
            ("home", lambda: self._navigate("/users/default")),
            ("refresh", self._refresh),
        ]
        for icon_name, _action in tb_btns:
            icon = get_icon(icon_name)
            if icon:
                icon.draw(vdi, tbx + 4, tby + 4, scale=1)
            tbx += 28

        # View toggle icons
        tbx += 8
        for vname in ('view_icons', 'view_list'):
            icon = get_icon(vname)
            if icon:
                is_active = (vname == 'view_icons' and self._view_mode == 'icons') or \
                            (vname == 'view_list' and self._view_mode == 'list')
                if is_active:
                    vdi.fill_rect(tbx, tby, 24, 24, Colors.MENU_HIGHLIGHT)
                icon.draw(vdi, tbx + 4, tby + 4, scale=1)
            tbx += 28

        # Path bar (below toolbar)
        path_h = ch_f + 6
        path_y = cy + toolbar_h
        vdi.fill_rect(cx, path_y, cw, path_h, Colors.BLACK)
        path_text = self.path
        max_chars = cw // cw_f - 2
        if len(path_text) > max_chars:
            path_text = "..." + path_text[-(max_chars - 3):]
        vdi.draw_string(cx + 4, path_y + 3, path_text,
                        Colors.CYAN, Colors.BLACK)

        # Content area
        content_y = path_y + path_h + 2
        content_h = ch - toolbar_h - path_h - 2 - (ch_f + 6)  # minus status bar

        if self._view_mode == 'icons':
            self._draw_icon_view(vdi, cx, content_y, cw, content_h)
        else:
            self._draw_list_view(vdi, cx, content_y, cw, content_h)

        # Status bar
        status_y = cy + ch - ch_f - 6
        vdi.draw_line(cx, status_y, cx + cw - 1, status_y,
                      Colors.WINDOW_BORDER)
        dirs = sum(1 for _, d in self._entries if d)
        files = len(self._entries) - dirs
        status_text = f" {dirs} folders, {files} files"
        vdi.draw_string(cx + 4, status_y + 3, status_text,
                        Colors.DARK_GRAY, Colors.WINDOW_BG)

    def _draw_icon_view(self, vdi, cx, cy, cw, ch):
        """Draw entries as a grid of icons with labels."""
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        cell_w = 80
        cell_h = 56
        cols = max(1, cw // cell_w)
        max_rows = ch // cell_h

        for idx, (name, is_dir) in enumerate(self._entries):
            row = idx // cols - self._scroll_offset
            col = idx % cols
            if row < 0:
                continue
            if row >= max_rows:
                break

            ix = cx + col * cell_w + 4
            iy = cy + row * cell_h + 4

            # Selection highlight
            if idx == self._selected:
                vdi.fill_rect(ix, iy, cell_w - 8, cell_h - 4,
                              Colors.MENU_HIGHLIGHT)

            # Icon (2x = 32px)
            icon_name = 'folder' if is_dir else icon_for_name(name)
            icon = get_icon(icon_name)
            if icon:
                icon_x = ix + (cell_w - 8 - 32) // 2
                icon.draw(vdi, icon_x, iy + 2, scale=2)

            # Label
            max_lbl = (cell_w - 8) // cw_f
            label = name[:max_lbl]
            lx = ix + (cell_w - 8 - len(label) * cw_f) // 2
            ly = iy + 36

            if idx == self._selected:
                vdi.draw_string(lx, ly, label,
                                Colors.WHITE, Colors.MENU_HIGHLIGHT)
            else:
                fg = Colors.CYAN if is_dir else Colors.DROPDOWN_TEXT
                vdi.draw_string(lx, ly, label, fg, BG_TRANSPARENT)

    def _draw_list_view(self, vdi, cx, cy, cw, ch):
        """Draw entries as a text list with small icons."""
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        row_h = ch_f + 4
        max_lines = ch // row_h

        for i in range(max_lines):
            idx = self._scroll_offset + i
            if idx >= len(self._entries):
                break
            name, is_dir = self._entries[idx]
            ey = cy + i * row_h

            # Alternating row background
            if idx == self._selected:
                vdi.fill_rect(cx + 1, ey, cw - 2, row_h,
                              Colors.MENU_HIGHLIGHT)
                fg = Colors.WHITE
                bg = Colors.MENU_HIGHLIGHT
            elif i % 2 == 1:
                vdi.fill_rect(cx + 1, ey, cw - 2, row_h, 0x1A1A2A)
                fg = Colors.CYAN if is_dir else Colors.DROPDOWN_TEXT
                bg = 0x1A1A2A
            else:
                fg = Colors.CYAN if is_dir else Colors.DROPDOWN_TEXT
                bg = Colors.WINDOW_BG

            # Small icon (1x = 16px)
            icon_name_str = 'folder' if is_dir else icon_for_name(name)
            icon = get_icon(icon_name_str)
            if icon:
                icon.draw(vdi, cx + 4, ey + (row_h - 16) // 2, scale=1)

            # Name
            max_chars = (cw - 28) // cw_f
            display_name = name[:max_chars]
            vdi.draw_string(cx + 24, ey + 2, display_name, fg, bg)

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        cw_f = self.aes.vdi.font.char_w
        ch_f = self.aes.vdi.font.char_h
        _, _, win_cw, win_ch = win.client_rect()

        toolbar_h = 30
        path_h = ch_f + 6

        # Toolbar button clicks
        if cy < toolbar_h:
            tbx = 4
            btn_actions = [
                self._go_parent,
                self._go_parent,
                lambda: self._navigate("/users/default"),
                self._refresh,
            ]
            for i, action in enumerate(btn_actions):
                if tbx <= cx < tbx + 28:
                    action()
                    return
                tbx += 28
            # View toggle
            tbx += 8
            for vname in ('icons', 'list'):
                if tbx <= cx < tbx + 28:
                    self._set_view(vname)
                    return
                tbx += 28
            return

        # Content area click
        content_cy = toolbar_h + path_h + 2
        if cy < content_cy:
            return

        rel_y = cy - content_cy

        if self._view_mode == 'icons':
            cell_w = 80
            cell_h = 56
            cols = max(1, win_cw // cell_w)
            col = cx // cell_w
            row = rel_y // cell_h + self._scroll_offset
            idx = row * cols + col
        else:
            row_h = ch_f + 4
            idx = self._scroll_offset + rel_y // row_h

        if 0 <= idx < len(self._entries):
            if self._selected == idx:
                self._open_entry(idx)
            else:
                self._selected = idx
                self.aes._dirty = True

    def _on_key(self, win: Window, key: int, mod: int) -> None:
        import pygame
        ch_f = self.aes.vdi.font.char_h
        _, _, _, wch = win.client_rect()
        toolbar_h = 30
        path_h = ch_f + 6
        content_h = wch - toolbar_h - path_h - 2 - (ch_f + 6)

        if self._view_mode == 'list':
            row_h = ch_f + 4
            max_lines = content_h // row_h
        else:
            cell_h = 56
            max_lines = content_h // cell_h * max(1, (wch // 80))

        if key == pygame.K_UP and self._selected > 0:
            self._selected -= 1
            self.aes._dirty = True
        elif key == pygame.K_DOWN and self._selected < len(self._entries) - 1:
            self._selected += 1
            self.aes._dirty = True
        elif key == pygame.K_RETURN and 0 <= self._selected < len(self._entries):
            self._open_entry(self._selected)
        elif key == pygame.K_BACKSPACE:
            self._go_parent()

    def _open_entry(self, idx: int) -> None:
        """Open selected entry: folder → new window, file → scrap/editor."""
        if idx < 0 or idx >= len(self._entries):
            return
        name, is_dir = self._entries[idx]
        full_path = self.path.rstrip('/') + '/' + name

        if is_dir:
            FileManagerCrystallite(
                self.aes, path=full_path,
                x=self.win.x + 20, y=self.win.y + 20,
                w=self.win.w, h=self.win.h,
            )
        else:
            # Text/code files → open in editor
            ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
            if ext in ('txt', 'lisp', 'md', 'profile', 'app'):
                TextEditorCrystallite(
                    self.aes, vfs_path=full_path,
                    x=self.win.x + 30, y=self.win.y + 30,
                )
            else:
                # Put file info on scrap
                try:
                    stat = self.aes.vfs.stat(full_path)
                    info = f"{name} ({stat.get('size', '?')} bytes)"
                except Exception:
                    info = name
                self.aes.scrap.put(info, 'text/plain')
                self.aes._dirty = True

    def _go_parent(self) -> None:
        """Navigate to parent directory."""
        if self.path == '/':
            return
        parent = self.path.rstrip('/').rsplit('/', 1)[0] or '/'
        self._navigate(parent)

    def _navigate(self, path: str) -> None:
        """Navigate to a new VFS path."""
        try:
            self.aes.vfs.resolve_dir(path)
            self.path = _vfs_normalize(path)
            title = self.path.rstrip('/').rsplit('/', 1)[-1] or "/"
            self.win.title = title
            self._scroll_offset = 0
            self._refresh()
        except Exception:
            pass


class ControlPanelCrystallite:
    """Control Panel — theme colors, system info, and VFS stats."""

    def __init__(self, aes: AES, x: int = 200, y: int = 100):
        self.aes = aes
        self._section = 0   # 0=colors, 1=info

        w = 300
        h = 340
        self.win = aes.create_window(
            "Control Panel", x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE,
            on_redraw=self._redraw,
            on_click=self._on_click,
            menu=[
                Menu("View", [
                    MenuItem("Colors", callback=lambda: self._set_section(0)),
                    MenuItem("System Info", callback=lambda: self._set_section(1)),
                ]),
            ],
        )
        self.win._cryst_type = 'control-panel'

    def _set_section(self, s: int) -> None:
        self._section = s
        self.aes._dirty = True

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)

        y = cy + 4
        max_cols = cw // cw_f - 1

        def _line(text: str, fg: int = Colors.DROPDOWN_TEXT):
            nonlocal y
            vdi.draw_string(cx + 4, y, text[:max_cols],
                            fg, Colors.DROPDOWN_BG)
            y += ch_f + 1

        def _color_swatch(label: str, color: int):
            nonlocal y
            sw_x = cx + 4
            sw_y = y + 1
            vdi.fill_rect(sw_x, sw_y, 12, ch_f - 2, color)
            vdi.draw_string(cx + 20, y,
                            f"{label}: #{color:06X}"[:max_cols],
                            Colors.DROPDOWN_TEXT, Colors.DROPDOWN_BG)
            y += ch_f + 1

        if self._section == 0:
            _line("Theme Colors", Colors.CYAN)
            _line("")
            _color_swatch("Desktop", Colors.DESKTOP_BG)
            _color_swatch("Title Active", Colors.TITLE_BAR_ACTIVE)
            _color_swatch("Title Inactive", Colors.TITLE_BAR_INACTIVE)
            _color_swatch("Window BG", Colors.WINDOW_BG)
            _color_swatch("Window Border", Colors.WINDOW_BORDER)
            _color_swatch("Menu BG", Colors.MENU_BAR_BG)
            _color_swatch("Close Button", Colors.CLOSE_BTN_BG)
            _color_swatch("Taskbar BG", Colors.TASKBAR_BG)
            _color_swatch("Accent Blue", Colors.BLUE)
            _color_swatch("Accent Cyan", Colors.CYAN)
            _color_swatch("Accent Green", Colors.GREEN)
            _color_swatch("Accent Red", Colors.RED)
        else:
            _line("System Info", Colors.CYAN)
            _line("")
            _line(f"Resolution: {vdi.width}x{vdi.height}")
            _line(f"Font: {vdi.font.char_w}x{vdi.font.char_h}")
            _line(f"Windows: {len(self.aes._windows)}")
            _line(f"Scrap items: {len(self.aes.scrap.history)}")
            _line("")
            # VFS stats
            _line("Virtual Filesystem", Colors.CYAN)
            file_count = 0
            dir_count = 0
            total_bytes = 0
            for _dp, dirs, files in self.aes.vfs.walk("/"):
                dir_count += len(dirs)
                file_count += len(files)
                for fname in files:
                    fnode = self.aes.vfs.resolve_file(f"{_dp.rstrip('/')}/{fname}")
                    if fnode:
                        total_bytes += len(fnode.content)
            _line(f"  Directories: {dir_count}")
            _line(f"  Files: {file_count}")
            _line(f"  Total size: {total_bytes} bytes")
            _line("")
            _line("Crystal Desktop v2.0", Colors.CYAN)
            _line("LM-1 List Machine")

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        self._section = (self._section + 1) % 2
        self.aes._dirty = True


# ===================================================================
# Text Editor Crystallite
# ===================================================================

class TextEditorCrystallite:
    """A text editor that reads/writes files on the virtual filesystem.

    Features syntax highlighting for .lisp files, line numbers,
    undo/redo, and save functionality.
    """

    def __init__(self, aes: AES, vfs_path: str | None = None,
                 x: int = 80, y: int = 50, w: int = 520, h: int = 380):
        self.aes = aes
        self.vfs_path = vfs_path
        self._lines: list[str] = [""]
        self._cursor_line = 0
        self._cursor_col = 0
        self._scroll_y = 0
        self._scroll_x = 0
        self._modified = False
        self._undo_stack: list[tuple[list[str], int, int]] = []
        self._redo_stack: list[tuple[list[str], int, int]] = []
        self._is_lisp = False

        if vfs_path:
            fname = vfs_path.rsplit('/', 1)[-1]
            self._is_lisp = fname.endswith('.lisp')
            try:
                text = aes.vfs.read_text(vfs_path)
                self._lines = text.split('\n')
                if self._lines and self._lines[-1] == '':
                    self._lines = self._lines[:-1] or [""]
            except Exception:
                self._lines = [""]
            title = fname
        else:
            title = "Untitled"

        self.win = aes.create_window(
            title, x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE
                  | WIN_MAXIMIZABLE | WIN_MINIMIZABLE,
            on_redraw=self._redraw,
            on_key=self._on_key,
            on_click=self._on_click,
            menu=[
                Menu("File", [
                    MenuItem("Save", callback=self._save),
                    MenuItem("Save As...", callback=self._save),
                ]),
                Menu("Edit", [
                    MenuItem("Undo", callback=self._undo),
                    MenuItem("Redo", callback=self._redo),
                ]),
            ],
        )
        self.win._cryst_type = 'editor'

    def _push_undo(self) -> None:
        """Save current state for undo."""
        self._undo_stack.append(
            ([l for l in self._lines], self._cursor_line, self._cursor_col)
        )
        if len(self._undo_stack) > 100:
            self._undo_stack = self._undo_stack[-100:]
        self._redo_stack.clear()

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(
            ([l for l in self._lines], self._cursor_line, self._cursor_col)
        )
        lines, cl, cc = self._undo_stack.pop()
        self._lines = lines
        self._cursor_line = cl
        self._cursor_col = cc
        self._modified = True
        self.aes._dirty = True

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(
            ([l for l in self._lines], self._cursor_line, self._cursor_col)
        )
        lines, cl, cc = self._redo_stack.pop()
        self._lines = lines
        self._cursor_line = cl
        self._cursor_col = cc
        self._modified = True
        self.aes._dirty = True

    def _save(self) -> None:
        """Save to VFS."""
        if self.vfs_path:
            text = '\n'.join(self._lines) + '\n'
            try:
                self.aes.vfs.write(self.vfs_path, text)
                self._modified = False
                fname = self.vfs_path.rsplit('/', 1)[-1]
                self.win.title = fname
                self.aes._dirty = True
            except Exception:
                pass

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, 0x0E0E1A)

        # Gutter width (line numbers)
        gutter_w = max(4, len(str(len(self._lines)))) * cw_f + 8

        # Status bar at bottom
        status_h = ch_f + 6
        status_y = cy + ch - status_h
        edit_h = ch - status_h

        # Visible lines
        max_vis = edit_h // ch_f
        text_w = cw - gutter_w

        for i in range(max_vis):
            line_idx = self._scroll_y + i
            if line_idx >= len(self._lines):
                break

            y = cy + i * ch_f
            line = self._lines[line_idx]

            # Current line highlight
            if line_idx == self._cursor_line:
                vdi.fill_rect(cx, y, cw, ch_f, 0x1A1A30)

            # Line number in gutter
            ln_str = str(line_idx + 1).rjust(gutter_w // cw_f - 1)
            ln_fg = Colors.CYAN if line_idx == self._cursor_line else Colors.DARK_GRAY
            vdi.draw_string(cx + 2, y, ln_str, ln_fg, BG_TRANSPARENT)

            # Gutter separator
            vdi.draw_line(cx + gutter_w - 2, y,
                          cx + gutter_w - 2, y + ch_f - 1,
                          0x333355)

            # Text content with optional syntax highlighting
            text_x = cx + gutter_w
            visible_chars = text_w // cw_f
            display_line = line[self._scroll_x:self._scroll_x + visible_chars]

            if self._is_lisp:
                spans = self._lisp_highlight(line, line_idx)
                # Draw the full line char by char with colors
                for ci, ch_char in enumerate(display_line):
                    abs_ci = ci + self._scroll_x
                    fg = Colors.DROPDOWN_TEXT
                    for start, end, color in spans:
                        if start <= abs_ci < end:
                            fg = color
                            break
                    vdi.draw_char(text_x + ci * cw_f, y, ch_char,
                                 fg, BG_TRANSPARENT)
            else:
                vdi.draw_string(text_x, y, display_line,
                                Colors.DROPDOWN_TEXT, BG_TRANSPARENT)

            # Cursor
            if line_idx == self._cursor_line:
                cur_screen_col = self._cursor_col - self._scroll_x
                if 0 <= cur_screen_col < visible_chars:
                    cur_x = text_x + cur_screen_col * cw_f
                    vdi.fill_rect(cur_x, y, 2, ch_f, Colors.CYAN)

        # Status bar
        vdi.fill_rect(cx, status_y, cw, status_h, 0x181828)
        vdi.draw_line(cx, status_y, cx + cw - 1, status_y, Colors.WINDOW_BORDER)
        mod_marker = " [modified]" if self._modified else ""
        status_text = f" Ln {self._cursor_line + 1}, Col {self._cursor_col + 1}{mod_marker}"
        vdi.draw_string(cx + 4, status_y + 3, status_text,
                        Colors.DARK_GRAY, 0x181828)

        # Right side: file path
        if self.vfs_path:
            path_text = self.vfs_path
            max_path = (cw - len(status_text) * cw_f - 16) // cw_f
            if len(path_text) > max_path:
                path_text = "..." + path_text[-(max_path - 3):]
            vdi.draw_string(cx + cw - len(path_text) * cw_f - 4,
                            status_y + 3, path_text,
                            Colors.DARK_GRAY, 0x181828)

    def _on_key(self, win: Window, key: int, mod: int) -> None:
        import pygame

        ctrl = mod & pygame.KMOD_CTRL
        shift = mod & pygame.KMOD_SHIFT

        if ctrl:
            if key == ord('z'):
                self._undo()
                return
            if key == ord('y'):
                self._redo()
                return
            if key == ord('s'):
                self._save()
                return

        if key == pygame.K_RETURN:
            self._push_undo()
            line = self._lines[self._cursor_line]
            before = line[:self._cursor_col]
            after = line[self._cursor_col:]
            self._lines[self._cursor_line] = before
            self._lines.insert(self._cursor_line + 1, after)
            self._cursor_line += 1
            self._cursor_col = 0
            self._modified = True

        elif key == pygame.K_BACKSPACE:
            if self._cursor_col > 0:
                self._push_undo()
                line = self._lines[self._cursor_line]
                self._lines[self._cursor_line] = line[:self._cursor_col - 1] + line[self._cursor_col:]
                self._cursor_col -= 1
                self._modified = True
            elif self._cursor_line > 0:
                self._push_undo()
                prev = self._lines[self._cursor_line - 1]
                curr = self._lines.pop(self._cursor_line)
                self._cursor_line -= 1
                self._cursor_col = len(prev)
                self._lines[self._cursor_line] = prev + curr
                self._modified = True

        elif key == pygame.K_DELETE:
            line = self._lines[self._cursor_line]
            if self._cursor_col < len(line):
                self._push_undo()
                self._lines[self._cursor_line] = line[:self._cursor_col] + line[self._cursor_col + 1:]
                self._modified = True
            elif self._cursor_line < len(self._lines) - 1:
                self._push_undo()
                next_line = self._lines.pop(self._cursor_line + 1)
                self._lines[self._cursor_line] = line + next_line
                self._modified = True

        elif key == pygame.K_LEFT:
            if ctrl:
                # Word jump
                line = self._lines[self._cursor_line]
                p = self._cursor_col - 1
                while p > 0 and not line[p - 1].isalnum():
                    p -= 1
                while p > 0 and line[p - 1].isalnum():
                    p -= 1
                self._cursor_col = max(0, p)
            elif self._cursor_col > 0:
                self._cursor_col -= 1
            elif self._cursor_line > 0:
                self._cursor_line -= 1
                self._cursor_col = len(self._lines[self._cursor_line])

        elif key == pygame.K_RIGHT:
            line = self._lines[self._cursor_line]
            if ctrl:
                p = self._cursor_col
                while p < len(line) and not line[p].isalnum():
                    p += 1
                while p < len(line) and line[p].isalnum():
                    p += 1
                self._cursor_col = p
            elif self._cursor_col < len(line):
                self._cursor_col += 1
            elif self._cursor_line < len(self._lines) - 1:
                self._cursor_line += 1
                self._cursor_col = 0

        elif key == pygame.K_UP:
            if self._cursor_line > 0:
                self._cursor_line -= 1
                self._cursor_col = min(self._cursor_col,
                                       len(self._lines[self._cursor_line]))

        elif key == pygame.K_DOWN:
            if self._cursor_line < len(self._lines) - 1:
                self._cursor_line += 1
                self._cursor_col = min(self._cursor_col,
                                       len(self._lines[self._cursor_line]))

        elif key == pygame.K_HOME:
            self._cursor_col = 0
        elif key == pygame.K_END:
            self._cursor_col = len(self._lines[self._cursor_line])

        elif key == pygame.K_TAB:
            self._push_undo()
            line = self._lines[self._cursor_line]
            self._lines[self._cursor_line] = line[:self._cursor_col] + "  " + line[self._cursor_col:]
            self._cursor_col += 2
            self._modified = True

        elif 32 <= key <= 126:
            ch_char = chr(key)
            if shift:
                ch_char = ch_char.upper()
                shift_map = {
                    '1': '!', '2': '@', '3': '#', '4': '$', '5': '%',
                    '6': '^', '7': '&', '8': '*', '9': '(', '0': ')',
                    '-': '_', '=': '+', '[': '{', ']': '}', '\\': '|',
                    ';': ':', "'": '"', ',': '<', '.': '>', '/': '?',
                    '`': '~',
                }
                if chr(key) in shift_map:
                    ch_char = shift_map[chr(key)]
            self._push_undo()
            line = self._lines[self._cursor_line]
            self._lines[self._cursor_line] = line[:self._cursor_col] + ch_char + line[self._cursor_col:]
            self._cursor_col += 1
            self._modified = True

        # Scroll to keep cursor visible
        self._ensure_cursor_visible()
        self.aes._dirty = True

    def _on_click(self, win: Window, rx: int, ry: int, button: int) -> None:
        """Click to position cursor."""
        cw_f = self.aes.vdi.font.char_w
        ch_f = self.aes.vdi.font.char_h
        _, _, cw, ch = win.client_rect()
        gutter_w = max(4, len(str(len(self._lines)))) * cw_f + 8

        if rx < gutter_w:
            return  # Clicked in gutter

        text_col = (rx - gutter_w) // cw_f + self._scroll_x
        text_line = ry // ch_f + self._scroll_y

        if 0 <= text_line < len(self._lines):
            self._cursor_line = text_line
            self._cursor_col = min(text_col, len(self._lines[text_line]))
            self.aes._dirty = True

    def _ensure_cursor_visible(self) -> None:
        """Scroll to keep cursor in view."""
        _, _, cw, ch = self.win.client_rect()
        cw_f = self.aes.vdi.font.char_w
        ch_f = self.aes.vdi.font.char_h
        gutter_w = max(4, len(str(len(self._lines)))) * cw_f + 8
        status_h = ch_f + 6
        edit_h = ch - status_h
        max_vis = edit_h // ch_f
        text_w = cw - gutter_w
        visible_chars = text_w // cw_f

        if self._cursor_line < self._scroll_y:
            self._scroll_y = self._cursor_line
        elif self._cursor_line >= self._scroll_y + max_vis:
            self._scroll_y = self._cursor_line - max_vis + 1

        if self._cursor_col < self._scroll_x:
            self._scroll_x = self._cursor_col
        elif self._cursor_col >= self._scroll_x + visible_chars:
            self._scroll_x = self._cursor_col - visible_chars + 1

    # Lisp syntax highlighting
    _LISP_KEYWORDS = frozenset([
        'defun', 'defmacro', 'lambda', 'let', 'let*', 'letrec',
        'if', 'cond', 'when', 'unless', 'and', 'or', 'not',
        'begin', 'progn', 'do', 'loop', 'while',
        'define', 'set!', 'setq', 'quote', 'quasiquote',
    ])
    _LISP_BUILTINS = frozenset([
        'cons', 'car', 'cdr', 'list', 'append', 'reverse', 'length',
        'map', 'filter', 'reduce', 'apply', 'eval',
        'eq', 'equal', 'null', 'atom', 'pair',
        '+', '-', '*', '/', 'mod', 'rem',
        '=', '<', '>', '<=', '>=', '/=',
        'print', 'display', 'newline', 'format',
        'read', 'write', 'load',
    ])

    def _lisp_highlight(self, line: str, line_no: int) -> list[tuple[int, int, int]]:
        """Return [(start, end, color), ...] for syntax highlighting."""
        spans: list[tuple[int, int, int]] = []
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if ch == ';':
                spans.append((i, n, Colors.DARK_GRAY))
                break
            if ch == '"':
                j = i + 1
                while j < n and line[j] != '"':
                    if line[j] == '\\':
                        j += 1
                    j += 1
                spans.append((i, min(j + 1, n), Colors.GREEN))
                i = j + 1
                continue
            if ch in '()[]':
                spans.append((i, i + 1, 0x6688AA))
                i += 1
                continue
            if ch.isdigit() or (ch == '-' and i + 1 < n and line[i + 1].isdigit()):
                j = i + 1
                while j < n and (line[j].isdigit() or line[j] == '.'):
                    j += 1
                spans.append((i, j, Colors.YELLOW))
                i = j
                continue
            if ch not in ' \t()[]";\n':
                j = i + 1
                while j < n and line[j] not in ' \t()[]";\n':
                    j += 1
                word = line[i:j]
                if word in self._LISP_KEYWORDS:
                    spans.append((i, j, Colors.MAGENTA))
                elif word in self._LISP_BUILTINS:
                    spans.append((i, j, Colors.CYAN))
                i = j
                continue
            i += 1
        return spans


# ===================================================================
# Desktop launcher
# ===================================================================

def launch_desktop(width: int = 1024, height: int = 768,
                   scale: int = 1) -> None:
    """Launch the Crystal Desktop interactively.

    Resolution defaults to 1024x768.
    """
    vdi = VDI(width=width, height=height, headless=False, scale=scale)
    aes = AES(vdi)

    aes.resources = ResourceDB()

    # Create default crystallites
    terminal = TerminalCrystallite(aes, x=180, y=60, w=500, h=360)
    clock = ClockCrystallite(aes, x=720, y=30)

    # Desktop icons
    icon_defs = [
        ("Terminal", "terminal", lambda: TerminalCrystallite(aes)),
        ("Files", "file_manager", lambda: FileManagerCrystallite(aes, path="/")),
        ("Editor", "editor", lambda: TextEditorCrystallite(aes)),
        ("Calculator", "calculator", lambda: CalculatorCrystallite(aes)),
        ("Clock", "clock", lambda: ClockCrystallite(aes)),
        ("Inspector", "inspector", lambda: InspectorCrystallite(aes)),
        ("Settings", "settings", lambda: ControlPanelCrystallite(aes)),
    ]
    for i, (label, icon_name, action) in enumerate(icon_defs):
        aes._desktop_icons.append(DesktopIcon(
            label=label, icon_name=icon_name, action=action,
            grid_row=i, grid_col=0,
        ))

    # System menu items
    aes._system_menus[0].items.extend([
        MenuItem("", separator=True),
        MenuItem("New Terminal", callback=lambda: TerminalCrystallite(aes)),
        MenuItem("File Manager",
                 callback=lambda: FileManagerCrystallite(aes, path="/")),
        MenuItem("Text Editor", callback=lambda: TextEditorCrystallite(aes)),
        MenuItem("Calculator", callback=lambda: CalculatorCrystallite(aes)),
        MenuItem("Clock", callback=lambda: ClockCrystallite(aes)),
        MenuItem("Inspector", callback=lambda: InspectorCrystallite(aes)),
        MenuItem("Control Panel",
                 callback=lambda: ControlPanelCrystallite(aes)),
        MenuItem("", separator=True),
        MenuItem("Save Profile",
                 callback=lambda: DesktopProfile.save_to_file(
                     aes, "crystal.profile")),
        MenuItem("", separator=True),
        MenuItem("Quit", callback=lambda: setattr(aes, '_running', False)),
    ])

    # Main loop with clock ticking
    import pygame
    aes.redraw()

    pg_clock = pygame.time.Clock()
    while aes._running:
        evt_type, d1, d2 = vdi.read_event()
        while evt_type != EVT_NONE:
            aes.handle_event(evt_type, d1, d2)
            evt_type, d1, d2 = vdi.read_event()

        for ticker in aes._tick_objects:
            ticker.tick()

        if aes._dirty:
            aes.redraw()

        pg_clock.tick(30)

    vdi.close()
