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
from typing import Callable, Optional
import time

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


# ===================================================================
# Desktop launcher
# ===================================================================

def launch_desktop(width: int = 640, height: int = 480, scale: int = 2) -> None:
    """Launch the Crystal Desktop interactively.

    This is the main entry point for running the desktop.
    """
    vdi = VDI(width=width, height=height, headless=False, scale=scale)
    aes = AES(vdi)

    # Create default crystallites
    terminal = TerminalCrystallite(aes, x=20, y=40, w=400, h=300)
    clock = ClockCrystallite(aes, x=440, y=30)
    calc = CalculatorCrystallite(aes, x=440, y=120)

    # Add system menu items for launching crystallites
    aes._system_menus[0].items.extend([
        MenuItem("New Terminal", callback=lambda: TerminalCrystallite(aes)),
        MenuItem("Calculator", callback=lambda: CalculatorCrystallite(aes)),
        MenuItem("Clock", callback=lambda: ClockCrystallite(aes)),
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
