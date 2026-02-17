"""Crystal Desktop — GEM-inspired window manager for LM-1.

Host-side implementation using VDI for all rendering.
Phase 12+ will port this to native Lisp running on the emulator.

Architecture:
  - AES (Application Environment Services): manages windows, z-order,
    event dispatch, menu bar
  - Each window has: position, size, title, decorations, content callback
  - Click-to-focus, overlapping windows, move/resize/raise/lower
  - Global menu bar (GEM-style: active app's menu)
  - Desktop root window with background pattern
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional, Any
import time
import os

from .vdi import (
    VDI, GRAD_VERTICAL, BG_TRANSPARENT,
    EVT_NONE, EVT_KEY_DOWN, EVT_KEY_UP,
    EVT_MOUSE_MOVE, EVT_MOUSE_DOWN, EVT_MOUSE_UP, EVT_QUIT, EVT_TIMER,
)


# ===================================================================
# Color scheme
# ===================================================================

class Colors:
    """0xRRGGBB color constants — retro-futuristic Crystal Desktop theme."""
    BLACK       = 0x1A1A2E
    WHITE       = 0xF0F0F5
    LIGHT_GRAY  = 0xC8C8D0
    MID_GRAY    = 0x888898
    DARK_GRAY   = 0x505068

    # Accent palette
    BLUE        = 0x3A7BD5
    CYAN        = 0x00D2FF
    GREEN       = 0x00E676
    RED         = 0xE53935
    YELLOW      = 0xFFD740
    MAGENTA     = 0xD500F9

    # Desktop background gradient
    DESKTOP_BG           = 0x0F1B2D   # deep navy
    DESKTOP_BG_END       = 0x1A2744   # slightly lighter navy at bottom

    # Title bars
    TITLE_BAR_ACTIVE     = 0x3A7BD5   # bright blue
    TITLE_BAR_ACTIVE_END = 0x1E4B8C   # deeper blue gradient end
    TITLE_BAR_INACTIVE   = 0x3A3A50   # muted dark
    TITLE_BAR_INACTIVE_END = 0x2A2A3E
    TITLE_TEXT            = 0xFFFFFF
    TITLE_TEXT_SHADOW     = 0x0A0A20   # dark shadow behind title

    # Window
    WINDOW_BG            = 0xF8F8FC
    WINDOW_BORDER        = 0x3A3A50   # soft dark border, not black

    # Close button
    CLOSE_BTN_BG         = 0xE53935   # red
    CLOSE_BTN_BG_ALT     = 0xC62828   # darker red for gradient
    CLOSE_BTN_X          = 0xFFFFFF   # white ×

    # Menu bar
    MENU_BAR_BG          = 0x1C2840   # dark navy to match desktop
    MENU_BAR_BG_END      = 0x243352
    MENU_BAR_TEXT         = 0xC8D0E0   # light cool gray
    MENU_BAR_SEPARATOR   = 0x3A4A66   # subtle line
    MENU_HIGHLIGHT       = 0x3A7BD5
    MENU_HI_TEXT          = 0xFFFFFF

    # Dropdown menus
    DROPDOWN_BG          = 0x1E2A44
    DROPDOWN_BORDER      = 0x3A4A66
    DROPDOWN_TEXT         = 0xD0D8E8
    DROPDOWN_SEPARATOR   = 0x2A3A56

    # Buttons/widgets
    BUTTON_BG            = 0x2A3450
    BUTTON_BG_END        = 0x1E2840
    BUTTON_BORDER        = 0x4A5A78
    BUTTON_TEXT           = 0xD0D8E8

    # Scrollbar
    SCROLLBAR_BG         = 0x1A2540
    SCROLLBAR_FG         = 0x3A4A66

    # Shadows
    SHADOW_COLOR         = 0x000000
    SHADOW_ALPHA         = 90

    # Resize grip
    GRIP_DOT             = 0x4A5A78


# ===================================================================
# Scrap (Clipboard) System
# ===================================================================

@dataclass
class ScrapEntry:
    """A single clipboard entry with type and data."""
    scrap_type: str          # MIME-like type: 'text/plain', 'lisp/form', etc.
    data: Any                # the payload
    timestamp: float = 0.0   # when it was placed

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class Scrap:
    """Typed structured clipboard with history.

    Supports multiple data types and maintains a history ring.
    Type negotiation: a consumer can request a preferred type,
    and the scrap will attempt conversion.
    """

    MAX_HISTORY = 16

    def __init__(self):
        self._history: list[ScrapEntry] = []

    def put(self, data: Any, scrap_type: str = 'text/plain') -> None:
        """Place data on the scrap with the given type."""
        entry = ScrapEntry(scrap_type=scrap_type, data=data)
        self._history.append(entry)
        if len(self._history) > self.MAX_HISTORY:
            self._history.pop(0)

    def get(self, preferred_type: str = 'text/plain') -> Optional[ScrapEntry]:
        """Get the most recent scrap entry, with optional type negotiation.

        If the top entry doesn't match preferred_type, attempts conversion:
          - lisp/form → text/plain: uses print_form
          - text/plain → lisp/form: attempts parse
        Returns None if the scrap is empty.
        """
        if not self._history:
            return None
        entry = self._history[-1]
        if entry.scrap_type == preferred_type:
            return entry
        # Type negotiation / conversion
        converted = self._convert(entry, preferred_type)
        return converted if converted else entry

    def _convert(self, entry: ScrapEntry, target_type: str) -> Optional[ScrapEntry]:
        """Attempt to convert a scrap entry to a different type."""
        if entry.scrap_type == 'lisp/form' and target_type == 'text/plain':
            return ScrapEntry('text/plain', _print_form(entry.data),
                              entry.timestamp)
        if entry.scrap_type == 'text/plain' and target_type == 'lisp/form':
            try:
                from .compiler import parse
                forms = parse(str(entry.data))
                if len(forms) == 1:
                    return ScrapEntry('lisp/form', forms[0], entry.timestamp)
                return ScrapEntry('lisp/form', forms, entry.timestamp)
            except Exception:
                return None
        return None

    @property
    def history(self) -> list[ScrapEntry]:
        """Return the full history (oldest first)."""
        return list(self._history)

    @property
    def empty(self) -> bool:
        return len(self._history) == 0

    def clear(self) -> None:
        """Clear all scrap history."""
        self._history.clear()


def _print_form(form: Any) -> str:
    """Convert a Lisp form to its string representation."""
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
# Resource System — menus/dialogs/alerts as Lisp data
# ===================================================================

class ResourceDB:
    """Resource database: stores UI definitions as Lisp-style data.

    Resources are identified by (type, id) pairs:
      - 'menu'   → menu bar definition as list
      - 'dialog' → dialog layout as list
      - 'alert'  → alert text and buttons as list
      - 'string' → localized string table

    Resources can be loaded from Lisp source files, edited at runtime,
    and serialized back to files — enabling live UI customization.
    """

    def __init__(self):
        self._resources: dict[tuple[str, str], Any] = {}

    def put(self, res_type: str, res_id: str, data: Any) -> None:
        """Store a resource."""
        self._resources[(res_type, res_id)] = data

    def get(self, res_type: str, res_id: str) -> Optional[Any]:
        """Retrieve a resource, or None if not found."""
        return self._resources.get((res_type, res_id))

    def delete(self, res_type: str, res_id: str) -> bool:
        """Remove a resource. Returns True if it existed."""
        return self._resources.pop((res_type, res_id), None) is not None

    def list_resources(self, res_type: Optional[str] = None
                       ) -> list[tuple[str, str]]:
        """List all resource keys, optionally filtered by type."""
        if res_type is None:
            return list(self._resources.keys())
        return [(t, i) for t, i in self._resources if t == res_type]

    def load_from_lisp(self, source: str) -> int:
        """Load resources from Lisp source text.

        Format: (resource <type> <id> <data>)
        Returns number of resources loaded.
        """
        from .compiler import parse
        forms = parse(source)
        count = 0
        for form in forms:
            if (isinstance(form, list) and len(form) >= 4 and
                    form[0] == 'resource'):
                res_type = str(form[1])
                res_id = str(form[2])
                self._resources[(res_type, res_id)] = form[3]
                count += 1
        return count

    def to_lisp(self) -> str:
        """Serialize all resources to Lisp source text."""
        lines = []
        for (res_type, res_id), data in sorted(self._resources.items()):
            lines.append(f"(resource {res_type} {res_id} {_print_form(data)})")
        return "\n".join(lines)

    def build_menu(self, res_id: str,
                   callbacks: Optional[dict[str, Callable]] = None
                   ) -> Optional[list[Menu]]:
        """Build Menu objects from a menu resource definition.

        Resource format:
          (menu <id>
            (menu-item <label> <callback-key>)
            (separator)
            (submenu <label>
              (menu-item <label> <callback-key>)
              ...))

        Returns list of Menu objects.
        """
        data = self.get('menu', res_id)
        if data is None:
            return None
        callbacks = callbacks or {}
        return self._parse_menu_list(data, callbacks)

    def _parse_menu_list(self, data: Any,
                         callbacks: dict[str, Callable]) -> list[Menu]:
        """Parse a list of menu/submenu forms into Menu objects."""
        if not isinstance(data, list):
            return []
        menus = []
        for item in data:
            if not isinstance(item, list) or len(item) < 2:
                continue
            tag = item[0]
            if tag == 'submenu' or tag == 'menu':
                label = str(item[1])
                items = self._parse_menu_items(item[2:], callbacks)
                menus.append(Menu(label, items))
        return menus

    def _parse_menu_items(self, items: list,
                          callbacks: dict[str, Callable]) -> list[MenuItem]:
        """Parse menu item forms."""
        result = []
        for item in items:
            if not isinstance(item, list) or not item:
                continue
            tag = item[0]
            if tag == 'separator':
                result.append(MenuItem("", separator=True))
            elif tag == 'menu-item' and len(item) >= 3:
                label = str(item[1])
                cb_key = str(item[2])
                cb = callbacks.get(cb_key)
                result.append(MenuItem(label, callback=cb))
        return result

    def show_alert(self, aes: 'AES', res_id: str) -> Optional['Window']:
        """Show an alert dialog from a resource definition.

        Resource format: (alert <id> <title> <message-line1> <line2> ...)
        """
        data = self.get('alert', res_id)
        if data is None:
            return None
        if not isinstance(data, list) or len(data) < 2:
            return None
        title = str(data[0]) if data else "Alert"
        message_lines = [str(x) for x in data[1:]]

        cw_f = aes.vdi.font.char_w
        ch_f = aes.vdi.font.char_h
        max_line = max(len(line) for line in message_lines) if message_lines else 10
        w = max(200, max_line * cw_f + 32)
        h = TITLE_BAR_H + len(message_lines) * (ch_f + 2) + 24
        x = (aes.vdi.width - w) // 2
        y = (aes.vdi.height - h) // 2

        def draw_alert(vdi: VDI, win: Window):
            cx, cy, cw, ch = win.client_rect()
            vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)
            for i, line in enumerate(message_lines):
                tx = cx + (cw - len(line) * vdi.font.char_w) // 2
                ty = cy + 8 + i * (vdi.font.char_h + 2)
                vdi.draw_string(tx, ty, line,
                                 Colors.DROPDOWN_TEXT, Colors.DROPDOWN_BG)

        return aes.create_window(title, x, y, w, h,
                                  flags=WIN_CLOSEABLE | WIN_MOVEABLE,
                                  on_redraw=draw_alert)


# ===================================================================
# Desktop Profile — serialize/deserialize desktop state
# ===================================================================

class DesktopProfile:
    """Serialize and restore desktop state as Lisp forms.

    Profile format (Lisp):
      (desktop-profile
        (resolution <width> <height>)
        (windows
          (window <title> <x> <y> <w> <h> <type>)
          ...))
    """

    @staticmethod
    def save(aes: 'AES') -> str:
        """Serialize current desktop state to a Lisp form string."""
        lines = ["(desktop-profile"]
        lines.append(f"  (resolution {aes.vdi.width} {aes.vdi.height})")
        lines.append("  (windows")
        for win in aes._windows:
            # Determine crystallite type by title convention
            win_type = DesktopProfile._infer_type(win)
            title = win.title.replace('"', '\\"')
            lines.append(f'    (window "{title}" {win.x} {win.y}'
                         f' {win.w} {win.h} {win_type})')
        lines.append("  ))")
        return "\n".join(lines)

    @staticmethod
    def load(aes: 'AES', source: str) -> int:
        """Restore desktop state from a Lisp form string.

        Returns the number of windows restored.
        Side effect: creates windows via the appropriate crystallite
        constructors.
        """
        from .compiler import parse
        forms = parse(source)
        count = 0
        for form in forms:
            if not isinstance(form, list) or not form:
                continue
            if form[0] != 'desktop-profile':
                continue
            for item in form[1:]:
                if not isinstance(item, list) or not item:
                    continue
                if item[0] == 'windows':
                    for wdef in item[1:]:
                        if DesktopProfile._restore_window(aes, wdef):
                            count += 1
        return count

    @staticmethod
    def _infer_type(win: 'Window') -> str:
        """Infer crystallite type from window title."""
        t = win.title.lower()
        if 'terminal' in t:
            return 'terminal'
        if 'clock' in t:
            return 'clock'
        if 'calculator' in t or 'calc' in t:
            return 'calculator'
        if 'inspector' in t:
            return 'inspector'
        if 'control' in t or 'panel' in t:
            return 'control-panel'
        if 'file' in t or 'folder' in t:
            return 'file-manager'
        return 'generic'

    @staticmethod
    def _restore_window(aes: 'AES', wdef: Any) -> bool:
        """Restore a single window from a (window ...) form."""
        if (not isinstance(wdef, list) or len(wdef) < 6 or
                wdef[0] != 'window'):
            return False
        title = str(wdef[1]).strip('"')
        x, y, w, h = int(wdef[2]), int(wdef[3]), int(wdef[4]), int(wdef[5])
        win_type = str(wdef[6]) if len(wdef) > 6 else 'generic'

        if win_type == 'terminal':
            TerminalCrystallite(aes, x=x, y=y, w=w, h=h)
        elif win_type == 'clock':
            ClockCrystallite(aes, x=x, y=y)
        elif win_type == 'calculator':
            CalculatorCrystallite(aes, x=x, y=y)
        elif win_type == 'inspector':
            InspectorCrystallite(aes, x=x, y=y)
        elif win_type == 'control-panel':
            ControlPanelCrystallite(aes, x=x, y=y)
        elif win_type == 'file-manager':
            FileManagerCrystallite(aes, path=".", x=x, y=y, w=w, h=h)
        else:
            aes.create_window(title, x, y, w, h)
        return True

    @staticmethod
    def save_to_file(aes: 'AES', path: str) -> None:
        """Save desktop profile to a file."""
        with open(path, 'w') as f:
            f.write(DesktopProfile.save(aes))

    @staticmethod
    def load_from_file(aes: 'AES', path: str) -> int:
        """Load desktop profile from a file."""
        with open(path, 'r') as f:
            return DesktopProfile.load(aes, f.read())


# ===================================================================
# Window
# ===================================================================

# Window flags
WIN_CLOSEABLE   = 0x01
WIN_MOVEABLE    = 0x02
WIN_RESIZABLE   = 0x04
WIN_FULLABLE    = 0x08
WIN_HAS_VSCROLL = 0x10
WIN_HAS_HSCROLL = 0x20

TITLE_BAR_H = 26
BORDER_W    = 1
MENU_BAR_H  = 26
MIN_WIN_W   = 100
MIN_WIN_H   = 80

# Close button
CLOSE_BTN_W = 18
CLOSE_BTN_H = 16

# Resize grip
GRIP_SIZE = 14


@dataclass
class Window:
    """A managed window in the Crystal Desktop."""
    wid: int                          # unique window ID
    title: str
    x: int
    y: int
    w: int
    h: int
    flags: int = WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE
    visible: bool = True
    # Content area (in window-local coords)
    content_x: int = 0
    content_y: int = 0
    content_w: int = 0
    content_h: int = 0
    # Callbacks
    on_redraw: Optional[Callable] = None   # (vdi, window) → None
    on_key: Optional[Callable] = None      # (window, key, mod) → None
    on_click: Optional[Callable] = None    # (window, x, y, button) → None
    on_close: Optional[Callable] = None    # (window) → bool (False to cancel)
    on_resize: Optional[Callable] = None   # (window, new_w, new_h) → None
    # App menu (list of (label, [(item_label, callback), ...]))
    menu: list = field(default_factory=list)
    # Scroll state
    scroll_x: int = 0
    scroll_y: int = 0
    doc_w: int = 0   # virtual document size
    doc_h: int = 0

    def client_rect(self) -> tuple[int, int, int, int]:
        """Return (x, y, w, h) of the content area in screen coords."""
        cx = self.x + BORDER_W
        cy = self.y + TITLE_BAR_H
        cw = self.w - 2 * BORDER_W
        ch = self.h - TITLE_BAR_H - BORDER_W
        return cx, cy, cw, ch

    def contains(self, sx: int, sy: int) -> bool:
        """Check if screen point (sx, sy) is inside this window."""
        return (self.x <= sx < self.x + self.w and
                self.y <= sy < self.y + self.h)

    def in_title_bar(self, sx: int, sy: int) -> bool:
        """Check if point is in the title bar."""
        return (self.x <= sx < self.x + self.w and
                self.y <= sy < self.y + TITLE_BAR_H)

    def in_close_button(self, sx: int, sy: int) -> bool:
        """Check if point is on the close button."""
        if not (self.flags & WIN_CLOSEABLE):
            return False
        bx = self.x + 2
        by = self.y + 2
        return bx <= sx < bx + CLOSE_BTN_W and by <= sy < by + CLOSE_BTN_H

    def in_resize_grip(self, sx: int, sy: int) -> bool:
        """Check if point is on the resize grip (bottom-right corner)."""
        if not (self.flags & WIN_RESIZABLE):
            return False
        gx = self.x + self.w - GRIP_SIZE
        gy = self.y + self.h - GRIP_SIZE
        return sx >= gx and sy >= gy


# ===================================================================
# Menu Bar
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
# AES — Application Environment Services
# ===================================================================

class AES:
    """Crystal Desktop window manager.

    Manages windows, z-order, menu bar, focus, drag operations.
    All rendering goes through a VDI instance.
    """

    def __init__(self, vdi: VDI):
        self.vdi = vdi
        self._windows: list[Window] = []   # z-order: last = topmost
        self._next_wid = 1
        self._focused: Optional[Window] = None
        self._dragging: Optional[Window] = None
        self._drag_offset = (0, 0)
        self._resizing: Optional[Window] = None
        self._resize_offset = (0, 0)
        self._menu_open: int = -1          # index of open top-level menu
        self._menu_highlight: int = -1     # highlighted item in open menu
        self._running = True
        self._dirty = True

        # Scrap (clipboard) system
        self.scrap = Scrap()

        # System menu (always present)
        self._system_menus: list[Menu] = [
            Menu("Crystal", [
                MenuItem("About Crystal Desktop...",
                         callback=lambda: self._show_about()),
                MenuItem("", separator=True),
            ]),
        ]

        # Desktop background pattern
        self._init_desktop_pattern()

    def _init_desktop_pattern(self) -> None:
        """No-op — palette-free system, colors are direct RGB."""
        pass

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def create_window(self, title: str, x: int, y: int, w: int, h: int,
                      flags: int = WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE,
                      **kwargs) -> Window:
        """Create and register a new window."""
        win = Window(
            wid=self._next_wid,
            title=title,
            x=x, y=y, w=w, h=h,
            flags=flags,
            **kwargs,
        )
        self._next_wid += 1
        self._windows.append(win)
        self._focused = win
        self._dirty = True
        return win

    def close_window(self, win: Window) -> None:
        """Close and remove a window."""
        if win.on_close:
            if win.on_close(win) is False:
                return  # close was cancelled
        if win in self._windows:
            self._windows.remove(win)
        if self._focused is win:
            self._focused = self._windows[-1] if self._windows else None
        self._dirty = True

    def raise_window(self, win: Window) -> None:
        """Bring a window to the top of the z-order."""
        if win in self._windows:
            self._windows.remove(win)
            self._windows.append(win)
            self._focused = win
            self._dirty = True

    def lower_window(self, win: Window) -> None:
        """Send a window to the bottom of the z-order."""
        if win in self._windows:
            self._windows.remove(win)
            self._windows.insert(0, win)
            self._dirty = True

    def find_window_at(self, sx: int, sy: int) -> Optional[Window]:
        """Find the topmost window at screen coordinates."""
        for win in reversed(self._windows):
            if win.visible and win.contains(sx, sy):
                return win
        return None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def redraw(self) -> None:
        """Redraw the entire desktop."""
        vdi = self.vdi

        # 1. Desktop background — smooth vertical gradient
        vdi.grad_rect(0, 0, vdi.width, vdi.height,
                       Colors.DESKTOP_BG, Colors.DESKTOP_BG_END,
                       GRAD_VERTICAL)

        # 2. Windows (bottom to top) — draw shadows first, then windows
        for win in self._windows:
            if win.visible:
                vdi.shadow_rect(win.x, win.y, win.w, win.h,
                                 radius=6, alpha=Colors.SHADOW_ALPHA)

        for win in self._windows:
            if win.visible:
                self._draw_window(win)

        # 3. Menu bar (on top of everything)
        self._draw_menu_bar()

        # 4. Open dropdown menu
        if self._menu_open >= 0:
            self._draw_dropdown()

        vdi.present()
        self._dirty = False

    def _draw_window(self, win: Window) -> None:
        """Draw a single window with modern retro-futuristic decorations."""
        vdi = self.vdi
        is_active = (win is self._focused)

        # Outer border (soft dark, not hard black)
        vdi.fill_rect(win.x, win.y, win.w, win.h, Colors.WINDOW_BORDER)

        # Title bar background — gradient
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

        # Close button — red circle with white ×
        if win.flags & WIN_CLOSEABLE:
            btn_cx = win.x + 3 + CLOSE_BTN_W // 2
            btn_cy = win.y + 2 + CLOSE_BTN_H // 2
            btn_r = min(CLOSE_BTN_W, CLOSE_BTN_H) // 2 - 1
            vdi.fill_circle(btn_cx, btn_cy, btn_r, Colors.CLOSE_BTN_BG)
            # White × mark
            xr = btn_r - 3
            vdi.draw_line(btn_cx - xr, btn_cy - xr,
                           btn_cx + xr, btn_cy + xr, Colors.CLOSE_BTN_X)
            vdi.draw_line(btn_cx + xr, btn_cy - xr,
                           btn_cx - xr, btn_cy + xr, Colors.CLOSE_BTN_X)

        # Title text with drop shadow
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        text_x = win.x + (win.w - len(win.title) * cw_f) // 2
        text_y = win.y + (TITLE_BAR_H - ch_f) // 2
        # Shadow first (1px offset down-right)
        vdi.draw_string(text_x + 1, text_y + 1, win.title,
                        Colors.TITLE_TEXT_SHADOW, BG_TRANSPARENT)
        vdi.draw_string(text_x, text_y, win.title,
                        Colors.TITLE_TEXT, BG_TRANSPARENT)

        # Client area background
        cx, cy, cw, ch = win.client_rect()
        vdi.fill_rect(cx, cy, cw, ch, Colors.WINDOW_BG)

        # Subtle separator line between title bar and content
        vdi.draw_line(win.x + 1, win.y + TITLE_BAR_H,
                       win.x + win.w - 2, win.y + TITLE_BAR_H,
                       Colors.WINDOW_BORDER)

        # Resize grip — dot pattern (bottom-right corner)
        if win.flags & WIN_RESIZABLE:
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

        # Draw window content
        if win.on_redraw:
            win.on_redraw(vdi, win)

    def _draw_menu_bar(self) -> None:
        """Draw the global menu bar — dark, modern."""
        vdi = self.vdi

        # Menu bar gradient background
        vdi.grad_rect(0, 0, vdi.width, MENU_BAR_H,
                       Colors.MENU_BAR_BG, Colors.MENU_BAR_BG_END,
                       GRAD_VERTICAL)
        # Subtle bottom separator
        vdi.draw_line(0, MENU_BAR_H - 1, vdi.width - 1, MENU_BAR_H - 1,
                       Colors.MENU_BAR_SEPARATOR)

        # Draw menu labels
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

        # Calculate dropdown position
        x = 8
        for i in range(self._menu_open):
            x += len(menus[i].label) * cw_f + 12

        # Dropdown dimensions
        max_label_w = max(len(item.label) for item in menu.items
                          if not item.separator) if menu.items else 8
        drop_w = max_label_w * cw_f + 16
        drop_h = sum(8 if item.separator else ch_f + 4
                     for item in menu.items) + 4
        drop_x = x - 4
        drop_y = MENU_BAR_H

        # Background and border — dark dropdown
        vdi.shadow_rect(drop_x, drop_y, drop_w, drop_h,
                         radius=4, alpha=60)
        vdi.fill_rect(drop_x, drop_y, drop_w, drop_h, Colors.DROPDOWN_BG)
        # Border
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

        # Items
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
        """Return the current menu bar items (system + focused app menus)."""
        menus = list(self._system_menus)
        if self._focused and self._focused.menu:
            menus.extend(self._focused.menu)
        return menus

    def _menu_hit_test(self, sx: int, sy: int) -> int:
        """Return index of menu label at screen position, or -1."""
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
        """Return index of menu item under cursor, or -1."""
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
        # Check menu bar first
        if my < MENU_BAR_H:
            idx = self._menu_hit_test(mx, my)
            if idx >= 0:
                if self._menu_open == idx:
                    self._menu_open = -1  # toggle off
                else:
                    self._menu_open = idx
                    self._menu_highlight = -1
                self._dirty = True
                return

        # Check dropdown menu
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

        # Find window under cursor
        win = self.find_window_at(mx, my)
        if win is None:
            return

        # Raise and focus
        if win is not self._focused:
            self.raise_window(win)
            self._dirty = True

        # Close button?
        if win.in_close_button(mx, my):
            self.close_window(win)
            return

        # Title bar drag?
        if win.in_title_bar(mx, my) and (win.flags & WIN_MOVEABLE):
            self._dragging = win
            self._drag_offset = (mx - win.x, my - win.y)
            return

        # Resize grip?
        if win.in_resize_grip(mx, my):
            self._resizing = win
            self._resize_offset = (mx - win.w, my - win.h)
            return

        # Click in client area
        cx, cy, cw, ch = win.client_rect()
        if cx <= mx < cx + cw and cy <= my < cy + ch:
            if win.on_click:
                win.on_click(win, mx - cx, my - cy, button)

    def _on_mouse_up(self, mx: int, my: int, button: int) -> None:
        """Handle mouse button release."""
        if self._dragging:
            self._dragging = None
            self._dirty = True
        if self._resizing:
            self._resizing = None
            self._dirty = True

    def _on_mouse_move(self, mx: int, my: int) -> None:
        """Handle mouse movement."""
        if self._dragging:
            win = self._dragging
            ox, oy = self._drag_offset
            win.x = max(0, min(mx - ox, self.vdi.width - 20))
            win.y = max(MENU_BAR_H, min(my - oy, self.vdi.height - 20))
            self._dirty = True
            return

        if self._resizing:
            win = self._resizing
            ox, oy = self._resize_offset
            new_w = max(MIN_WIN_W, mx - ox)
            new_h = max(MIN_WIN_H, my - oy)
            win.w = min(new_w, self.vdi.width - win.x)
            win.h = min(new_h, self.vdi.height - win.y)
            if win.on_resize:
                win.on_resize(win, win.w, win.h)
            self._dirty = True
            return

        # Update menu highlight if dropdown is open
        if self._menu_open >= 0:
            # Check if mouse moved to a different top-level menu
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

        # Update cursor position
        self.vdi.set_cursor(mx, my, True)

    def _on_key_down(self, key: int, mod: int) -> None:
        """Handle key press — dispatch to focused window."""
        if self._focused and self._focused.on_key:
            self._focused.on_key(self._focused, key, mod)

    # ------------------------------------------------------------------
    # About dialog
    # ------------------------------------------------------------------

    def _show_about(self) -> None:
        """Show the About Crystal Desktop dialog."""
        w = 280
        h = 140
        x = (self.vdi.width - w) // 2
        y = (self.vdi.height - h) // 2

        def draw_about(vdi: VDI, win: Window):
            cx, cy, cw, ch = win.client_rect()
            cw_f = vdi.font.char_w
            ch_f = vdi.font.char_h
            # Dark background for about
            vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)
            lines = [
                "Crystal Desktop v1.0",
                "",
                "LM-1 List Machine",
                "Window Manager",
                "",
                "Click to close",
            ]
            for i, line in enumerate(lines):
                tx = cx + (cw - len(line) * cw_f) // 2
                ty = cy + 8 + i * (ch_f + 2)
                fg = Colors.CYAN if i == 0 else Colors.DROPDOWN_TEXT
                vdi.draw_string(tx, ty, line, fg, Colors.DROPDOWN_BG)

        win = self.create_window("About", x, y, w, h,
                                  flags=WIN_CLOSEABLE | WIN_MOVEABLE,
                                  on_redraw=draw_about)
        self._dirty = True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, fps: int = 30) -> None:
        """Run the desktop event loop."""
        import pygame

        frame_time = 1.0 / fps
        clock = pygame.time.Clock()

        while self._running:
            # Poll events
            evt_type, d1, d2 = self.vdi.read_event()
            while evt_type != EVT_NONE:
                self.handle_event(evt_type, d1, d2)
                evt_type, d1, d2 = self.vdi.read_event()

            # Redraw if needed
            if self._dirty:
                self.redraw()

            clock.tick(fps)

        self.vdi.close()


# ===================================================================
# Built-in Crystallites
# ===================================================================

class TerminalCrystallite:
    """A simple terminal/REPL window."""

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
        }

        self.win = aes.create_window(
            "Terminal", x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE,
            on_redraw=self._redraw,
            on_key=self._on_key,
            menu=[
                Menu("Edit", [
                    MenuItem("Clear", callback=self._cmd_clear),
                ]),
            ],
        )

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        # Fill background
        vdi.fill_rect(cx, cy, cw, ch, Colors.BLACK)

        # Calculate visible lines
        max_lines = ch // ch_f
        display_lines = self.lines[-(max_lines - 1):]

        # Draw history
        for i, line in enumerate(display_lines):
            vdi.draw_string(cx + 2, cy + 2 + i * ch_f,
                             line[:cw // cw_f],
                             Colors.GREEN, Colors.BLACK)

        # Draw current input line with prompt
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
                # Handle shift symbols
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

        # Try built-in commands
        parts = cmd.split()
        cmd_name = parts[0].lower()
        if cmd_name in self._commands:
            self._commands[cmd_name]()
        else:
            # Try to evaluate as a simple Lisp expression
            self._eval_lisp(cmd)

    def _eval_lisp(self, expr: str) -> None:
        """Simple Lisp evaluator for the terminal."""
        try:
            from .compiler import parse
            forms = parse(expr)
            for form in forms:
                result = self._eval_form(form)
                self._output(self._print_form(result))
        except Exception as e:
            self._output(f"Error: {e}")

    def _eval_form(self, form):
        """Evaluate a Lisp form directly in Python."""
        if isinstance(form, int):
            return form
        if isinstance(form, str):
            if form == 'nil' or form is None:
                return None
            if form == 't' or form is True:
                return True
            if form == 'pi':
                return 3.14159
            return form  # symbol
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
        self._output("Lisp: (+ 1 2), (fact 10), (list 1 2 3)")

    def _cmd_clear(self) -> None:
        self.lines.clear()

    def _cmd_windows(self) -> None:
        for win in self.aes._windows:
            self._output(f"  [{win.wid}] {win.title} ({win.w}x{win.h})")

    def _cmd_time(self) -> None:
        import time
        self._output(time.strftime("%Y-%m-%d %H:%M:%S"))


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
        # Schedule periodic updates
        self._update_interval = 1.0
        self._last_update = 0.0

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)
        time_str = time.strftime("%H:%M:%S")
        date_str = time.strftime("%Y-%m-%d")
        # Big time display
        tx = cx + (cw - len(time_str) * cw_f) // 2
        vdi.draw_string(tx, cy + 4, time_str, Colors.CYAN, Colors.DROPDOWN_BG)
        # Date below
        dx = cx + (cw - len(date_str) * cw_f) // 2
        vdi.draw_string(dx, cy + 4 + ch_f + 2, date_str,
                         Colors.DROPDOWN_TEXT, Colors.DROPDOWN_BG)
        self._last_time = time_str

    def tick(self) -> None:
        """Called periodically to update the clock."""
        now = time.time()
        if now - self._last_update >= self._update_interval:
            self._last_update = now
            new_time = time.strftime("%H:%M:%S")
            if new_time != self._last_time:
                self.aes._dirty = True


class CalculatorCrystallite:
    """A simple desktop calculator."""

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

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h

        # Calculator body background
        vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)

        # Display field — dark inset
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
                # Modern flat button with subtle gradient
                vdi.grad_rect(bx, by, btn_w, btn_h,
                               Colors.BUTTON_BG, Colors.BUTTON_BG_END,
                               GRAD_VERTICAL)
                # Border
                vdi.draw_line(bx, by, bx + btn_w - 1, by, Colors.BUTTON_BORDER)
                vdi.draw_line(bx, by, bx, by + btn_h - 1, Colors.BUTTON_BORDER)
                vdi.draw_line(bx + btn_w - 1, by,
                               bx + btn_w - 1, by + btn_h - 1, Colors.BUTTON_BORDER)
                vdi.draw_line(bx, by + btn_h - 1,
                               bx + btn_w - 1, by + btn_h - 1, Colors.BUTTON_BORDER)
                # Label
                lx = bx + (btn_w - len(label) * cw_f) // 2
                ly = by + (btn_h - ch_f) // 2
                vdi.draw_string(lx, ly, label,
                                 Colors.BUTTON_TEXT, BG_TRANSPARENT)

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        """Handle click in calculator content area."""
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
    """Inspector — shows window properties, z-order, and pixel info.

    Displays live information about the focused window and the
    desktop state. Useful for debugging and development.
    """

    def __init__(self, aes: AES, x: int = 20, y: int = 200,
                 w: int = 260, h: int = 260):
        self.aes = aes
        self._inspect_pixel = (0, 0)
        self._pixel_color = 0
        self.win = aes.create_window(
            "Inspector", x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE,
            on_redraw=self._redraw,
            on_click=self._on_click,
            menu=[
                Menu("View", [
                    MenuItem("Refresh", callback=lambda: setattr(
                        aes, '_dirty', True)),
                ]),
            ],
        )

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
            _line(f" {marker}[{w.wid}] {w.title}")

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

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        """Right click could set inspect pixel (using screen coords)."""
        # Clicking within inspector doesn't change pixel probe
        self.aes._dirty = True

    def set_pixel_probe(self, x: int, y: int) -> None:
        """Set the pixel to inspect (screen coords)."""
        self._inspect_pixel = (x, y)
        self.aes._dirty = True


class FileManagerCrystallite:
    """Spatial file manager — folder = window, with file listing.

    Displays the contents of a directory. Each file/folder is shown
    as a text entry with an icon character. Clicking a folder opens
    a new FileManagerCrystallite. Clicking a file attempts to open it.
    """

    ICON_FOLDER = "\u25B6"  # ▶ (triangle for folder)
    ICON_FILE   = "\u2022"  # • (bullet for file)

    def __init__(self, aes: AES, path: str = ".",
                 x: int = 40, y: int = 60, w: int = 320, h: int = 280):
        self.aes = aes
        self.path = os.path.abspath(path)
        self._entries: list[tuple[str, bool]] = []  # (name, is_dir)
        self._scroll_offset = 0
        self._selected = -1
        self._refresh()

        title = os.path.basename(self.path) or self.path
        self.win = aes.create_window(
            title, x, y, w, h,
            flags=WIN_CLOSEABLE | WIN_MOVEABLE | WIN_RESIZABLE,
            on_redraw=self._redraw,
            on_click=self._on_click,
            on_key=self._on_key,
            menu=[
                Menu("File", [
                    MenuItem("Refresh", callback=self._refresh),
                    MenuItem("Parent Folder", callback=self._go_parent),
                ]),
            ],
        )

    def _refresh(self) -> None:
        """Re-read directory contents."""
        self._entries = []
        try:
            entries = sorted(os.listdir(self.path))
            # Directories first, then files
            dirs = [(e, True) for e in entries
                    if os.path.isdir(os.path.join(self.path, e))]
            files = [(e, False) for e in entries
                     if not os.path.isdir(os.path.join(self.path, e))]
            self._entries = dirs + files
        except OSError:
            self._entries = []
        self._selected = -1
        if hasattr(self, 'aes'):
            self.aes._dirty = True

    def _redraw(self, vdi: VDI, win: Window) -> None:
        cx, cy, cw, ch = win.client_rect()
        cw_f = vdi.font.char_w
        ch_f = vdi.font.char_h
        vdi.fill_rect(cx, cy, cw, ch, Colors.DROPDOWN_BG)

        # Path bar
        path_text = self.path
        max_chars = cw // cw_f - 1
        if len(path_text) > max_chars:
            path_text = "..." + path_text[-(max_chars - 3):]
        vdi.fill_rect(cx, cy, cw, ch_f + 4, Colors.BLACK)
        vdi.draw_string(cx + 4, cy + 2, path_text,
                         Colors.CYAN, Colors.BLACK)

        # File listing
        list_y = cy + ch_f + 6
        max_lines = (ch - ch_f - 8) // (ch_f + 2)

        for i in range(max_lines):
            idx = self._scroll_offset + i
            if idx >= len(self._entries):
                break
            name, is_dir = self._entries[idx]
            ey = list_y + i * (ch_f + 2)

            # Highlight selected
            if idx == self._selected:
                vdi.fill_rect(cx + 1, ey, cw - 2, ch_f + 2,
                               Colors.MENU_HIGHLIGHT)
                fg = Colors.MENU_HI_TEXT
                bg = Colors.MENU_HIGHLIGHT
            else:
                fg = Colors.CYAN if is_dir else Colors.DROPDOWN_TEXT
                bg = Colors.DROPDOWN_BG

            # Icon + name
            icon = "+" if is_dir else " "
            display = f" {icon} {name}"[:max_chars]
            vdi.draw_string(cx + 4, ey + 1, display, fg, bg)

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        cw_f = self.aes.vdi.font.char_w
        ch_f = self.aes.vdi.font.char_h
        # Path bar height
        list_y = ch_f + 6
        if cy < list_y:
            return

        idx = self._scroll_offset + (cy - list_y) // (ch_f + 2)
        if 0 <= idx < len(self._entries):
            if self._selected == idx:
                # Double-click: open
                self._open_entry(idx)
            else:
                self._selected = idx
                self.aes._dirty = True

    def _on_key(self, win: Window, key: int, mod: int) -> None:
        import pygame
        ch_f = self.aes.vdi.font.char_h
        _, _, _, ch = win.client_rect()
        max_lines = (ch - ch_f - 8) // (ch_f + 2)

        if key == pygame.K_UP and self._selected > 0:
            self._selected -= 1
            if self._selected < self._scroll_offset:
                self._scroll_offset = self._selected
            self.aes._dirty = True
        elif key == pygame.K_DOWN and self._selected < len(self._entries) - 1:
            self._selected += 1
            if self._selected >= self._scroll_offset + max_lines:
                self._scroll_offset = self._selected - max_lines + 1
            self.aes._dirty = True
        elif key == pygame.K_RETURN and 0 <= self._selected < len(self._entries):
            self._open_entry(self._selected)
        elif key == pygame.K_BACKSPACE:
            self._go_parent()

    def _open_entry(self, idx: int) -> None:
        """Open selected entry — folder opens new window, file is info."""
        if idx < 0 or idx >= len(self._entries):
            return
        name, is_dir = self._entries[idx]
        full_path = os.path.join(self.path, name)

        if is_dir:
            # Open a new file manager window for the subdirectory
            FileManagerCrystallite(
                self.aes, path=full_path,
                x=self.win.x + 20, y=self.win.y + 20,
                w=self.win.w, h=self.win.h,
            )
        else:
            # Put file info on scrap
            try:
                size = os.path.getsize(full_path)
                info = f"{name} ({size} bytes)"
            except OSError:
                info = name
            self.aes.scrap.put(info, 'text/plain')
            self.aes._dirty = True

    def _go_parent(self) -> None:
        """Navigate to parent directory."""
        parent = os.path.dirname(self.path)
        if parent and parent != self.path:
            self.path = parent
            self.win.title = os.path.basename(self.path) or self.path
            self._refresh()


class ControlPanelCrystallite:
    """Control Panel — theme info and desktop settings.

    Shows current theme colors and provides basic desktop
    configuration. In future will support live theme switching.
    """

    def __init__(self, aes: AES, x: int = 200, y: int = 100):
        self.aes = aes
        self._section = 0  # 0=colors, 1=info

        w = 280
        h = 300
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
            # Draw small color swatch
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
            _line("Crystal Desktop v1.0", Colors.CYAN)
            _line("LM-1 List Machine")

    def _on_click(self, win: Window, cx: int, cy: int, button: int) -> None:
        # Toggle section on click
        self._section = (self._section + 1) % 2
        self.aes._dirty = True


# ===================================================================
# Desktop launcher
# ===================================================================

def launch_desktop(width: int = 640, height: int = 480, scale: int = 2) -> None:
    """Launch the Crystal Desktop interactively.

    This is the main entry point for running the desktop.
    """
    vdi = VDI(width=width, height=height, headless=False, scale=scale)
    aes = AES(vdi)

    # Resource DB — can be loaded from Lisp files
    aes.resources = ResourceDB()

    # Create default crystallites
    terminal = TerminalCrystallite(aes, x=20, y=40, w=400, h=300)
    clock = ClockCrystallite(aes, x=440, y=30)
    calc = CalculatorCrystallite(aes, x=440, y=120)

    # Add system menu items for launching crystallites
    aes._system_menus[0].items.extend([
        MenuItem("New Terminal", callback=lambda: TerminalCrystallite(aes)),
        MenuItem("Calculator", callback=lambda: CalculatorCrystallite(aes)),
        MenuItem("Clock", callback=lambda: ClockCrystallite(aes)),
        MenuItem("Inspector", callback=lambda: InspectorCrystallite(aes)),
        MenuItem("File Manager",
                 callback=lambda: FileManagerCrystallite(aes, path=".")),
        MenuItem("Control Panel",
                 callback=lambda: ControlPanelCrystallite(aes)),
        MenuItem("", separator=True),
        MenuItem("Save Profile",
                 callback=lambda: DesktopProfile.save_to_file(
                     aes, "crystal.profile")),
        MenuItem("", separator=True),
        MenuItem("Quit", callback=lambda: setattr(aes, '_running', False)),
    ])

    # Override the main loop to include clock ticking
    import pygame
    aes.redraw()

    while aes._running:
        evt_type, d1, d2 = vdi.read_event()
        while evt_type != EVT_NONE:
            aes.handle_event(evt_type, d1, d2)
            evt_type, d1, d2 = vdi.read_event()

        clock.tick()

        if aes._dirty:
            aes.redraw()

        pygame.time.Clock().tick(30)

    vdi.close()
