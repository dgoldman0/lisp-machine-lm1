"""Crystal Desktop Widget Toolkit.

A real widget toolkit rendering through VDI.  All widgets follow the
Crystal Desktop dark retro-futuristic theme.  Widgets are composed into
trees and driven by a WidgetHost that bridges to AES Window callbacks.

Architecture:
  Widget        — base class: position, size, children, events, painting
  WidgetHost    — bridges AES Window to a widget tree, manages focus,
                  double-click detection, keyboard dispatch

Widgets:
  Label         — static text, alignment, color
  Button        — gradient bg, hover/press, icon+text, on_click
  IconButton    — compact icon-only button for toolbars
  TextField     — single-line text input, cursor, selection
  TextArea      — multi-line editor, line numbers, undo, syntax hooks
  ScrollBar     — vertical/horizontal, thumb drag, track click
  ListView      — scrollable list, icons, selection, activate
  IconView      — grid of icons with labels, selection, activate
  CheckBox      — toggle with label
  Panel         — container, background, border, layout
  Toolbar       — horizontal button strip with separators
  Separator     — visual divider line
  ContextMenu   — popup menu with keyboard shortcuts
  ProgressBar   — progress indicator
  StatusBar     — bottom info bar with sections
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Any
import time

from .icons import Icon, get_icon


# ===================================================================
# Key constants (matching pygame key codes)
# ===================================================================

K_BACKSPACE = 8
K_TAB       = 9
K_RETURN    = 13
K_ESCAPE    = 27
K_SPACE     = 32
K_DELETE    = 127
K_UP        = 273
K_DOWN      = 274
K_RIGHT     = 275
K_LEFT      = 276
K_HOME      = 278
K_END       = 279
K_PAGEUP    = 280
K_PAGEDOWN  = 281

# Modifier masks
KMOD_SHIFT  = 0x03   # LSHIFT | RSHIFT
KMOD_CTRL   = 0xC0   # LCTRL  | RCTRL

# Shift map for printable characters
_SHIFT_MAP = {
    '1': '!', '2': '@', '3': '#', '4': '$', '5': '%',
    '6': '^', '7': '&', '8': '*', '9': '(', '0': ')',
    '-': '_', '=': '+', '[': '{', ']': '}', '\\': '|',
    ';': ':', "'": '"', ',': '<', '.': '>', '/': '?',
    '`': '~',
}


def key_to_char(key: int, mod: int) -> str | None:
    """Convert a pygame key code + modifier to a printable character."""
    shift = bool(mod & KMOD_SHIFT)
    if 32 <= key < 127:
        ch = chr(key)
        if shift:
            return _SHIFT_MAP.get(ch, ch.upper())
        return ch
    return None


# ===================================================================
# Theme
# ===================================================================

class Theme:
    """Widget color theme — dark retro-futuristic."""

    # Backgrounds
    WIDGET_BG        = 0x1E2A44
    WIDGET_BG_HOVER  = 0x283450
    WIDGET_BG_PRESS  = 0x162036
    WIDGET_BORDER    = 0x3A4A66
    WIDGET_TEXT      = 0xD0D8E8
    WIDGET_TEXT_DIM  = 0x708090

    # Input fields
    INPUT_BG         = 0x0F1520
    INPUT_BORDER     = 0x3A4A66
    INPUT_FOCUS      = 0x3A7BD5
    INPUT_TEXT       = 0xE0E8F0
    INPUT_CURSOR     = 0x00D2FF
    INPUT_SELECTION  = 0x3A7BD5

    # Lists / trees
    LIST_BG          = 0x141C30
    LIST_ALT         = 0x1A2540
    LIST_SELECTED    = 0x2A5090
    LIST_SEL_TEXT    = 0xFFFFFF
    LIST_HOVER       = 0x1E3050

    # Buttons
    BUTTON_BG        = 0x2A3450
    BUTTON_BG_END    = 0x1E2840
    BUTTON_HOVER     = 0x344060
    BUTTON_PRESS     = 0x1A2438
    BUTTON_BORDER    = 0x4A5A78
    BUTTON_TEXT      = 0xD0D8E8

    # Scrollbar
    SCROLL_TRACK     = 0x141C30
    SCROLL_THUMB     = 0x3A4A66
    SCROLL_HOVER     = 0x4A5A78

    # Tabs
    TAB_ACTIVE       = 0x1E2A44
    TAB_INACTIVE     = 0x141C30
    TAB_BORDER       = 0x3A4A66

    # Checkbox
    CHECK_BG         = 0x0F1520
    CHECK_BORDER     = 0x3A4A66
    CHECK_MARK       = 0x00D2FF

    # Toolbar
    TOOLBAR_BG       = 0x1C2840
    TOOLBAR_SEP      = 0x3A4A66

    # Context menu
    MENU_BG          = 0x1E2A44
    MENU_BORDER      = 0x3A4A66
    MENU_TEXT        = 0xD0D8E8
    MENU_HIGHLIGHT   = 0x3A7BD5
    MENU_HI_TEXT     = 0xFFFFFF
    MENU_SEP         = 0x2A3A56
    MENU_SHORTCUT    = 0x708090

    # Accent
    ACCENT           = 0x3A7BD5
    ACCENT_LIGHT     = 0x00D2FF

    # Progress
    PROGRESS_BG      = 0x141C30
    PROGRESS_FILL    = 0x3A7BD5

    # StatusBar
    STATUS_BG        = 0x141C30
    STATUS_TEXT      = 0x8898B0
    STATUS_BORDER    = 0x3A4A66

    # IconView
    ICON_LABEL       = 0xD0D8E8
    ICON_SEL_BG      = 0x2A5090


# ===================================================================
# Drawing helpers
# ===================================================================

def draw_rect_outline(vdi, x: int, y: int, w: int, h: int, color: int) -> None:
    """Draw a 1px rectangle outline."""
    vdi.fill_rect(x, y, w, 1, color)
    vdi.fill_rect(x, y + h - 1, w, 1, color)
    vdi.fill_rect(x, y, 1, h, color)
    vdi.fill_rect(x + w - 1, y, 1, h, color)


def draw_inset_rect(vdi, x: int, y: int, w: int, h: int) -> None:
    """Draw an inset border (dark top-left, light bottom-right)."""
    vdi.fill_rect(x, y, w, 1, 0x0A0F1A)
    vdi.fill_rect(x, y, 1, h, 0x0A0F1A)
    vdi.fill_rect(x, y + h - 1, w, 1, Theme.WIDGET_BORDER)
    vdi.fill_rect(x + w - 1, y, 1, h, Theme.WIDGET_BORDER)


def text_width(vdi, text: str) -> int:
    """Calculate pixel width of text string."""
    return len(text) * vdi.font.char_w


def text_height(vdi) -> int:
    """Get font character height."""
    return vdi.font.char_h


def draw_text_clipped(vdi, x: int, y: int, w: int, text: str,
                      fg: int, bg: int = -1) -> None:
    """Draw text clipped to a maximum pixel width."""
    cw = vdi.font.char_w
    max_chars = max(0, w // cw) if cw > 0 else 0
    if len(text) > max_chars:
        if max_chars > 3:
            text = text[:max_chars - 3] + '...'
        else:
            text = text[:max_chars]
    vdi.draw_string(x, y, text, fg, bg)


# ===================================================================
# Widget base class
# ===================================================================

class Widget:
    """Base class for all UI widgets."""

    __slots__ = ('x', 'y', 'width', 'height', 'parent', 'children',
                 'visible', 'enabled', 'focusable', '_focused', 'tag')

    def __init__(self, x: int = 0, y: int = 0, w: int = 0, h: int = 0):
        self.x = x
        self.y = y
        self.width = w
        self.height = h
        self.parent: Widget | None = None
        self.children: list[Widget] = []
        self.visible = True
        self.enabled = True
        self.focusable = False
        self._focused = False
        self.tag: Any = None   # arbitrary user data

    def add(self, child: Widget) -> Widget:
        """Add a child widget. Returns the child for chaining."""
        child.parent = self
        self.children.append(child)
        return child

    def remove(self, child: Widget) -> None:
        if child in self.children:
            self.children.remove(child)
            child.parent = None

    def clear_children(self) -> None:
        for c in self.children:
            c.parent = None
        self.children.clear()

    # ── Drawing ───────────────────────────────────────────────────

    def draw(self, vdi, ax: int, ay: int) -> None:
        """Draw this widget and children. ax, ay = absolute screen pos."""
        if not self.visible:
            return
        self.paint(vdi, ax, ay)
        for child in self.children:
            child.draw(vdi, ax + child.x, ay + child.y)

    def paint(self, vdi, ax: int, ay: int) -> None:
        """Override to paint widget content. ax/ay = absolute position."""
        pass

    # ── Events ────────────────────────────────────────────────────

    def dispatch_click(self, rx: int, ry: int, button: int) -> bool:
        """Dispatch click through widget tree. rx/ry relative to this widget."""
        if not self.visible or not self.enabled:
            return False
        for child in reversed(self.children):
            cx, cy = rx - child.x, ry - child.y
            if 0 <= cx < child.width and 0 <= cy < child.height:
                if child.dispatch_click(cx, cy, button):
                    return True
        return self.on_click(rx, ry, button)

    def dispatch_double_click(self, rx: int, ry: int, button: int) -> bool:
        """Dispatch double-click through widget tree."""
        if not self.visible or not self.enabled:
            return False
        for child in reversed(self.children):
            cx, cy = rx - child.x, ry - child.y
            if 0 <= cx < child.width and 0 <= cy < child.height:
                if child.dispatch_double_click(cx, cy, button):
                    return True
        return self.on_double_click(rx, ry, button)

    def hit_test(self, rx: int, ry: int) -> Widget | None:
        """Find deepest widget at relative position."""
        if not self.visible or not self.enabled:
            return None
        for child in reversed(self.children):
            cx, cy = rx - child.x, ry - child.y
            if 0 <= cx < child.width and 0 <= cy < child.height:
                result = child.hit_test(cx, cy)
                if result:
                    return result
        return self

    def on_click(self, rx: int, ry: int, button: int) -> bool:
        """Handle click. rx/ry relative to this widget. Return True if handled."""
        return False

    def on_double_click(self, rx: int, ry: int, button: int) -> bool:
        """Handle double-click. Return True if handled."""
        return False

    def on_key(self, key: int, mod: int) -> bool:
        """Handle key press (only called when focused). Return True if handled."""
        return False

    def on_mouse_move(self, rx: int, ry: int) -> bool:
        return False


# ===================================================================
# Label
# ===================================================================

class Label(Widget):
    """Static text display."""

    ALIGN_LEFT = 0
    ALIGN_CENTER = 1
    ALIGN_RIGHT = 2

    def __init__(self, x=0, y=0, w=0, h=0, text='', color=Theme.WIDGET_TEXT,
                 align=0, bg=-1):
        super().__init__(x, y, w, h)
        self.text = text
        self.color = color
        self.align = align
        self.bg = bg

    def paint(self, vdi, ax, ay):
        if self.bg != -1:
            vdi.fill_rect(ax, ay, self.width, self.height, self.bg)
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        ty = ay + max(0, (self.height - ch) // 2)
        tw = len(self.text) * cw
        if self.align == Label.ALIGN_CENTER:
            tx = ax + max(0, (self.width - tw) // 2)
        elif self.align == Label.ALIGN_RIGHT:
            tx = ax + max(0, self.width - tw - 4)
        else:
            tx = ax + 4
        draw_text_clipped(vdi, tx, ty, self.width, self.text, self.color, -1)


# ===================================================================
# Button
# ===================================================================

class Button(Widget):
    """Clickable button with gradient background."""

    def __init__(self, x=0, y=0, w=80, h=0, text='', icon: Icon | None = None,
                 on_click: Callable | None = None):
        super().__init__(x, y, w, h)
        self.text = text
        self.icon = icon
        self.on_click_cb = on_click
        self._pressed = False
        self._hover = False

    def paint(self, vdi, ax, ay):
        h = self.height or (text_height(vdi) + 8)
        self.height = h
        if self._pressed and self.enabled:
            vdi.fill_rect(ax, ay, self.width, h, Theme.BUTTON_PRESS)
        elif self._hover and self.enabled:
            vdi.grad_rect(ax, ay, self.width, h,
                          Theme.BUTTON_HOVER, Theme.BUTTON_BG_END)
        else:
            vdi.grad_rect(ax, ay, self.width, h,
                          Theme.BUTTON_BG, Theme.BUTTON_BG_END)
        draw_rect_outline(vdi, ax, ay, self.width, h, Theme.BUTTON_BORDER)

        cw = vdi.font.char_w
        ch = vdi.font.char_h
        ty = ay + (h - ch) // 2
        ix = ax + 4
        if self.icon:
            icon_y = ay + (h - 16) // 2
            self.icon.draw(vdi, ix, icon_y)
            ix += 20
        if self.text:
            txt_color = Theme.BUTTON_TEXT if self.enabled else Theme.WIDGET_TEXT_DIM
            tw = len(self.text) * cw
            if self.icon:
                vdi.draw_string(ix, ty, self.text, txt_color, -1)
            else:
                # Center text
                tx = ax + (self.width - tw) // 2
                vdi.draw_string(tx, ty, self.text, txt_color, -1)

    def on_click(self, rx, ry, button):
        if self.enabled and self.on_click_cb:
            self.on_click_cb()
            return True
        return False


# ===================================================================
# IconButton (compact toolbar button)
# ===================================================================

class IconButton(Widget):
    """Small icon-only button for toolbars."""

    def __init__(self, x=0, y=0, size=28, icon: Icon | str | None = None,
                 tooltip: str = '', on_click: Callable | None = None):
        super().__init__(x, y, size, size)
        if isinstance(icon, str):
            self._icon = get_icon(icon)
        else:
            self._icon = icon
        self.tooltip = tooltip
        self.on_click_cb = on_click
        self._hover = False
        self._pressed = False

    def paint(self, vdi, ax, ay):
        if self._pressed:
            vdi.fill_rect(ax, ay, self.width, self.height, Theme.BUTTON_PRESS)
            draw_rect_outline(vdi, ax, ay, self.width, self.height, Theme.BUTTON_BORDER)
        elif self._hover:
            vdi.fill_rect(ax, ay, self.width, self.height, Theme.BUTTON_HOVER)
            draw_rect_outline(vdi, ax, ay, self.width, self.height, Theme.BUTTON_BORDER)
        if self._icon:
            self._icon.draw_centered(vdi, ax, ay, self.width, self.height)

    def on_click(self, rx, ry, button):
        if self.enabled and self.on_click_cb:
            self.on_click_cb()
            return True
        return False


# ===================================================================
# Separator
# ===================================================================

class Separator(Widget):
    """Visual divider line — horizontal or vertical."""

    HORIZONTAL = 0
    VERTICAL = 1

    def __init__(self, x=0, y=0, length=0, orientation=0):
        if orientation == Separator.HORIZONTAL:
            super().__init__(x, y, length, 2)
        else:
            super().__init__(x, y, 2, length)
        self.orientation = orientation

    def paint(self, vdi, ax, ay):
        if self.orientation == Separator.HORIZONTAL:
            vdi.fill_rect(ax, ay, self.width, 1, 0x0A0F1A)
            vdi.fill_rect(ax, ay + 1, self.width, 1, Theme.WIDGET_BORDER)
        else:
            vdi.fill_rect(ax, ay, 1, self.height, 0x0A0F1A)
            vdi.fill_rect(ax + 1, ay, 1, self.height, Theme.WIDGET_BORDER)


# ===================================================================
# Panel (container)
# ===================================================================

class Panel(Widget):
    """Container with optional background, border, and padding."""

    def __init__(self, x=0, y=0, w=0, h=0, bg=Theme.WIDGET_BG,
                 border: int | None = None, padding=0):
        super().__init__(x, y, w, h)
        self.bg = bg
        self.border = border
        self.padding = padding

    def paint(self, vdi, ax, ay):
        if self.bg is not None:
            vdi.fill_rect(ax, ay, self.width, self.height, self.bg)
        if self.border is not None:
            draw_rect_outline(vdi, ax, ay, self.width, self.height, self.border)


# ===================================================================
# CheckBox
# ===================================================================

class CheckBox(Widget):
    """Toggle checkbox with label."""

    def __init__(self, x=0, y=0, w=0, h=24, label='', checked=False,
                 on_change: Callable | None = None):
        super().__init__(x, y, w, h)
        self.label = label
        self.checked = checked
        self.on_change_cb = on_change

    def paint(self, vdi, ax, ay):
        ch = vdi.font.char_h
        box_size = min(16, ch)
        by = ay + (self.height - box_size) // 2
        bx = ax + 4

        # Box background
        vdi.fill_rect(bx, by, box_size, box_size, Theme.CHECK_BG)
        draw_rect_outline(vdi, bx, by, box_size, box_size, Theme.CHECK_BORDER)

        # Check mark
        if self.checked:
            m = box_size // 4
            for i in range(box_size - 2 * m):
                px = bx + m + i
                py = by + box_size // 2 + (i if i < box_size // 3 else box_size // 3 - (i - box_size // 3))
                if 0 <= py < by + box_size:
                    vdi.fill_rect(px, py, 1, 2, Theme.CHECK_MARK)

        # Label
        tx = bx + box_size + 8
        ty = ay + (self.height - ch) // 2
        vdi.draw_string(tx, ty, self.label, Theme.WIDGET_TEXT, -1)

    def on_click(self, rx, ry, button):
        self.checked = not self.checked
        if self.on_change_cb:
            self.on_change_cb(self.checked)
        return True


# ===================================================================
# ScrollBar
# ===================================================================

class ScrollBar(Widget):
    """Vertical or horizontal scrollbar."""

    VERTICAL = 0
    HORIZONTAL = 1

    def __init__(self, x=0, y=0, length=100, orientation=0, width=14,
                 on_scroll: Callable | None = None):
        if orientation == ScrollBar.VERTICAL:
            super().__init__(x, y, width, length)
        else:
            super().__init__(x, y, length, width)
        self.orientation = orientation
        self.value = 0          # current scroll position
        self.max_value = 100    # maximum scroll range
        self.page_size = 10     # visible page size
        self.on_scroll_cb = on_scroll
        self._dragging = False
        self._drag_offset = 0

    @property
    def _track_length(self) -> int:
        return self.height if self.orientation == ScrollBar.VERTICAL else self.width

    @property
    def _thumb_size(self) -> int:
        total = self.max_value + self.page_size
        if total <= 0:
            return self._track_length
        return max(20, int(self._track_length * self.page_size / total))

    @property
    def _thumb_pos(self) -> int:
        total = self.max_value + self.page_size
        if total <= 0 or self.max_value <= 0:
            return 0
        track_avail = self._track_length - self._thumb_size
        return int(track_avail * self.value / self.max_value)

    def paint(self, vdi, ax, ay):
        # Track
        vdi.fill_rect(ax, ay, self.width, self.height, Theme.SCROLL_TRACK)

        # Thumb
        ts = self._thumb_size
        tp = self._thumb_pos
        if self.orientation == ScrollBar.VERTICAL:
            vdi.fill_rect(ax + 1, ay + tp, self.width - 2, ts, Theme.SCROLL_THUMB)
            # Grip lines
            mid = ay + tp + ts // 2
            for i in range(-2, 3, 2):
                vdi.fill_rect(ax + 3, mid + i, self.width - 6, 1, Theme.SCROLL_HOVER)
        else:
            vdi.fill_rect(ax + tp, ay + 1, ts, self.height - 2, Theme.SCROLL_THUMB)

    def on_click(self, rx, ry, button):
        pos = ry if self.orientation == ScrollBar.VERTICAL else rx
        tp = self._thumb_pos
        ts = self._thumb_size
        if pos < tp:
            # Page up
            self.value = max(0, self.value - self.page_size)
        elif pos > tp + ts:
            # Page down
            self.value = min(self.max_value, self.value + self.page_size)
        else:
            # Start thumb drag (simplified: just set value)
            pass
        if self.on_scroll_cb:
            self.on_scroll_cb(self.value)
        return True

    def scroll_to(self, value: int) -> None:
        self.value = max(0, min(self.max_value, value))
        if self.on_scroll_cb:
            self.on_scroll_cb(self.value)


# ===================================================================
# TextField
# ===================================================================

class TextField(Widget):
    """Single-line text input with cursor."""

    def __init__(self, x=0, y=0, w=200, h=0, text='', placeholder='',
                 on_change: Callable | None = None,
                 on_submit: Callable | None = None):
        super().__init__(x, y, w, h)
        self.text = text
        self.placeholder = placeholder
        self.cursor_pos = len(text)
        self.scroll_x = 0     # horizontal scroll in chars
        self.on_change_cb = on_change
        self.on_submit_cb = on_submit
        self.focusable = True

    def paint(self, vdi, ax, ay):
        ch = vdi.font.char_h
        cw = vdi.font.char_w
        h = self.height or (ch + 8)
        self.height = h

        # Background
        vdi.fill_rect(ax, ay, self.width, h, Theme.INPUT_BG)
        border = Theme.INPUT_FOCUS if self._focused else Theme.INPUT_BORDER
        draw_rect_outline(vdi, ax, ay, self.width, h, border)

        # Text area
        tx = ax + 4
        ty = ay + (h - ch) // 2
        max_chars = max(0, (self.width - 8) // cw)

        # Ensure cursor is visible
        if self.cursor_pos < self.scroll_x:
            self.scroll_x = self.cursor_pos
        if self.cursor_pos > self.scroll_x + max_chars:
            self.scroll_x = self.cursor_pos - max_chars

        # Draw text or placeholder
        if self.text:
            visible = self.text[self.scroll_x:self.scroll_x + max_chars]
            vdi.draw_string(tx, ty, visible, Theme.INPUT_TEXT, -1)
        elif self.placeholder and not self._focused:
            vis = self.placeholder[:max_chars]
            vdi.draw_string(tx, ty, vis, Theme.WIDGET_TEXT_DIM, -1)

        # Cursor
        if self._focused:
            cx = tx + (self.cursor_pos - self.scroll_x) * cw
            if ax < cx < ax + self.width:
                # Blinking: show for 500ms, hide for 500ms
                if int(time.time() * 2) % 2 == 0:
                    vdi.fill_rect(cx, ty, 2, ch, Theme.INPUT_CURSOR)

    def on_click(self, rx, ry, button):
        # Position cursor
        cw = getattr(self, '_cw', 9)  # fallback
        char_pos = (rx - 4) // cw + self.scroll_x
        self.cursor_pos = max(0, min(len(self.text), char_pos))
        return True

    def on_key(self, key, mod):
        ctrl = bool(mod & KMOD_CTRL)

        if key == K_RETURN:
            if self.on_submit_cb:
                self.on_submit_cb(self.text)
            return True
        elif key == K_BACKSPACE:
            if self.cursor_pos > 0:
                self.text = self.text[:self.cursor_pos-1] + self.text[self.cursor_pos:]
                self.cursor_pos -= 1
                if self.on_change_cb:
                    self.on_change_cb(self.text)
            return True
        elif key == K_DELETE:
            if self.cursor_pos < len(self.text):
                self.text = self.text[:self.cursor_pos] + self.text[self.cursor_pos+1:]
                if self.on_change_cb:
                    self.on_change_cb(self.text)
            return True
        elif key == K_LEFT:
            if ctrl:
                # Word left
                while self.cursor_pos > 0 and self.text[self.cursor_pos-1] == ' ':
                    self.cursor_pos -= 1
                while self.cursor_pos > 0 and self.text[self.cursor_pos-1] != ' ':
                    self.cursor_pos -= 1
            else:
                self.cursor_pos = max(0, self.cursor_pos - 1)
            return True
        elif key == K_RIGHT:
            if ctrl:
                n = len(self.text)
                while self.cursor_pos < n and self.text[self.cursor_pos] != ' ':
                    self.cursor_pos += 1
                while self.cursor_pos < n and self.text[self.cursor_pos] == ' ':
                    self.cursor_pos += 1
            else:
                self.cursor_pos = min(len(self.text), self.cursor_pos + 1)
            return True
        elif key == K_HOME:
            self.cursor_pos = 0
            return True
        elif key == K_END:
            self.cursor_pos = len(self.text)
            return True
        else:
            ch = key_to_char(key, mod)
            if ch and not ctrl:
                self.text = self.text[:self.cursor_pos] + ch + self.text[self.cursor_pos:]
                self.cursor_pos += 1
                if self.on_change_cb:
                    self.on_change_cb(self.text)
                return True
        return False


# ===================================================================
# TextArea (multi-line editor)
# ===================================================================

class TextArea(Widget):
    """Multi-line text editor with line numbers and scrolling."""

    def __init__(self, x=0, y=0, w=400, h=300, text='',
                 show_line_numbers=True, read_only=False,
                 on_change: Callable | None = None,
                 syntax_fn: Callable | None = None):
        super().__init__(x, y, w, h)
        self.lines: list[str] = text.split('\n') if text else ['']
        self.cursor_line = 0
        self.cursor_col = 0
        self.scroll_y = 0      # first visible line
        self.scroll_x = 0      # horizontal scroll (chars)
        self.show_line_numbers = show_line_numbers
        self.read_only = read_only
        self.on_change_cb = on_change
        self.syntax_fn = syntax_fn   # (line_text, line_no) -> [(start, end, color)]
        self._undo_stack: list[tuple[list[str], int, int]] = []
        self._redo_stack: list[tuple[list[str], int, int]] = []
        self.focusable = True

    @property
    def text(self) -> str:
        return '\n'.join(self.lines)

    @text.setter
    def text(self, value: str) -> None:
        self.lines = value.split('\n') if value else ['']
        self.cursor_line = min(self.cursor_line, len(self.lines) - 1)
        self.cursor_col = min(self.cursor_col, len(self.lines[self.cursor_line]))

    def _save_undo(self):
        self._undo_stack.append(
            ([l for l in self.lines], self.cursor_line, self.cursor_col))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _gutter_width(self, vdi) -> int:
        if not self.show_line_numbers:
            return 0
        digits = max(3, len(str(len(self.lines))))
        return (digits + 1) * vdi.font.char_w

    def _visible_lines(self, vdi) -> int:
        return max(1, self.height // vdi.font.char_h)

    def _ensure_cursor_visible(self, vdi):
        vis = self._visible_lines(vdi)
        if self.cursor_line < self.scroll_y:
            self.scroll_y = self.cursor_line
        if self.cursor_line >= self.scroll_y + vis:
            self.scroll_y = self.cursor_line - vis + 1

    def paint(self, vdi, ax, ay):
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        gutter_w = self._gutter_width(vdi)
        vis_lines = self._visible_lines(vdi)
        text_area_w = self.width - gutter_w - 14  # 14 for scrollbar

        self._ensure_cursor_visible(vdi)

        # Background
        vdi.fill_rect(ax, ay, self.width, self.height, Theme.INPUT_BG)

        # Gutter
        if gutter_w > 0:
            vdi.fill_rect(ax, ay, gutter_w, self.height, 0x101828)
            vdi.fill_rect(ax + gutter_w - 1, ay, 1, self.height, Theme.WIDGET_BORDER)

        # Lines
        text_x = ax + gutter_w + 4
        max_chars = max(0, text_area_w // cw)

        for i in range(vis_lines):
            line_idx = self.scroll_y + i
            if line_idx >= len(self.lines):
                break
            ly = ay + i * ch
            line = self.lines[line_idx]

            # Current line highlight
            if line_idx == self.cursor_line and self._focused:
                vdi.fill_rect(ax + gutter_w, ly,
                              self.width - gutter_w - 14, ch, 0x1A2540)

            # Line number
            if gutter_w > 0:
                num_str = str(line_idx + 1).rjust(gutter_w // cw - 1)
                vdi.draw_string(ax + 2, ly, num_str, Theme.WIDGET_TEXT_DIM, -1)

            # Text with optional syntax highlighting
            visible = line[self.scroll_x:self.scroll_x + max_chars]
            if self.syntax_fn:
                spans = self.syntax_fn(line, line_idx)
                # Draw character by character with colors
                for ci, c in enumerate(visible):
                    actual_col = ci + self.scroll_x
                    color = Theme.INPUT_TEXT
                    for start, end, span_color in spans:
                        if start <= actual_col < end:
                            color = span_color
                            break
                    vdi.draw_char(text_x + ci * cw, ly, ord(c), color, -1)
            else:
                vdi.draw_string(text_x, ly, visible, Theme.INPUT_TEXT, -1)

        # Cursor
        if self._focused and not self.read_only:
            cursor_screen_line = self.cursor_line - self.scroll_y
            if 0 <= cursor_screen_line < vis_lines:
                cx = text_x + (self.cursor_col - self.scroll_x) * cw
                cy = ay + cursor_screen_line * ch
                if int(time.time() * 2) % 2 == 0:
                    vdi.fill_rect(cx, cy, 2, ch, Theme.INPUT_CURSOR)

        # Scrollbar track on right
        sb_x = ax + self.width - 14
        vdi.fill_rect(sb_x, ay, 14, self.height, Theme.SCROLL_TRACK)
        if len(self.lines) > vis_lines:
            thumb_h = max(20, int(self.height * vis_lines / len(self.lines)))
            thumb_y = int((self.height - thumb_h) * self.scroll_y /
                          max(1, len(self.lines) - vis_lines))
            vdi.fill_rect(sb_x + 1, ay + thumb_y, 12, thumb_h, Theme.SCROLL_THUMB)

        # Focus border
        border = Theme.INPUT_FOCUS if self._focused else Theme.WIDGET_BORDER
        draw_rect_outline(vdi, ax, ay, self.width, self.height, border)

    def on_click(self, rx, ry, button):
        # Click to position cursor
        cw = 9  # approximate
        ch = 23
        gutter_w = 0
        # Rough: we can't get VDI here, so estimate
        line = self.scroll_y + ry // ch
        col = max(0, (rx - gutter_w - 4) // cw) + self.scroll_x
        self.cursor_line = max(0, min(len(self.lines) - 1, line))
        self.cursor_col = max(0, min(len(self.lines[self.cursor_line]), col))
        return True

    def on_key(self, key, mod):
        ctrl = bool(mod & KMOD_CTRL)

        if ctrl:
            if key == ord('z') or key == ord('Z'):
                return self._undo()
            if key == ord('y') or key == ord('Y'):
                return self._redo()
            if key == ord('a') or key == ord('A'):
                # Select all — simplified: move to end
                self.cursor_line = len(self.lines) - 1
                self.cursor_col = len(self.lines[-1])
                return True

        if key == K_UP:
            if self.cursor_line > 0:
                self.cursor_line -= 1
                self.cursor_col = min(self.cursor_col, len(self.lines[self.cursor_line]))
            return True
        elif key == K_DOWN:
            if self.cursor_line < len(self.lines) - 1:
                self.cursor_line += 1
                self.cursor_col = min(self.cursor_col, len(self.lines[self.cursor_line]))
            return True
        elif key == K_LEFT:
            if self.cursor_col > 0:
                self.cursor_col -= 1
            elif self.cursor_line > 0:
                self.cursor_line -= 1
                self.cursor_col = len(self.lines[self.cursor_line])
            return True
        elif key == K_RIGHT:
            if self.cursor_col < len(self.lines[self.cursor_line]):
                self.cursor_col += 1
            elif self.cursor_line < len(self.lines) - 1:
                self.cursor_line += 1
                self.cursor_col = 0
            return True
        elif key == K_HOME:
            self.cursor_col = 0
            return True
        elif key == K_END:
            self.cursor_col = len(self.lines[self.cursor_line])
            return True
        elif key == K_PAGEUP:
            self.cursor_line = max(0, self.cursor_line - 20)
            self.cursor_col = min(self.cursor_col, len(self.lines[self.cursor_line]))
            return True
        elif key == K_PAGEDOWN:
            self.cursor_line = min(len(self.lines) - 1, self.cursor_line + 20)
            self.cursor_col = min(self.cursor_col, len(self.lines[self.cursor_line]))
            return True

        if self.read_only:
            return False

        if key == K_RETURN:
            self._save_undo()
            line = self.lines[self.cursor_line]
            self.lines[self.cursor_line] = line[:self.cursor_col]
            self.lines.insert(self.cursor_line + 1, line[self.cursor_col:])
            self.cursor_line += 1
            self.cursor_col = 0
            self._notify_change()
            return True
        elif key == K_BACKSPACE:
            if self.cursor_col > 0:
                self._save_undo()
                line = self.lines[self.cursor_line]
                self.lines[self.cursor_line] = line[:self.cursor_col-1] + line[self.cursor_col:]
                self.cursor_col -= 1
                self._notify_change()
            elif self.cursor_line > 0:
                self._save_undo()
                prev = self.lines[self.cursor_line - 1]
                self.cursor_col = len(prev)
                self.lines[self.cursor_line - 1] = prev + self.lines[self.cursor_line]
                self.lines.pop(self.cursor_line)
                self.cursor_line -= 1
                self._notify_change()
            return True
        elif key == K_DELETE:
            line = self.lines[self.cursor_line]
            if self.cursor_col < len(line):
                self._save_undo()
                self.lines[self.cursor_line] = line[:self.cursor_col] + line[self.cursor_col+1:]
                self._notify_change()
            elif self.cursor_line < len(self.lines) - 1:
                self._save_undo()
                self.lines[self.cursor_line] = line + self.lines[self.cursor_line + 1]
                self.lines.pop(self.cursor_line + 1)
                self._notify_change()
            return True
        elif key == K_TAB:
            self._save_undo()
            line = self.lines[self.cursor_line]
            self.lines[self.cursor_line] = line[:self.cursor_col] + '  ' + line[self.cursor_col:]
            self.cursor_col += 2
            self._notify_change()
            return True
        else:
            ch = key_to_char(key, mod)
            if ch and not ctrl:
                self._save_undo()
                line = self.lines[self.cursor_line]
                self.lines[self.cursor_line] = line[:self.cursor_col] + ch + line[self.cursor_col:]
                self.cursor_col += 1
                self._notify_change()
                return True
        return False

    def _notify_change(self):
        if self.on_change_cb:
            self.on_change_cb(self.text)

    def _undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(
            ([l for l in self.lines], self.cursor_line, self.cursor_col))
        lines, cl, cc = self._undo_stack.pop()
        self.lines = lines
        self.cursor_line = cl
        self.cursor_col = cc
        self._notify_change()
        return True

    def _redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(
            ([l for l in self.lines], self.cursor_line, self.cursor_col))
        lines, cl, cc = self._redo_stack.pop()
        self.lines = lines
        self.cursor_line = cl
        self.cursor_col = cc
        self._notify_change()
        return True


# ===================================================================
# ListView
# ===================================================================

@dataclass
class ListItem:
    """Item in a ListView."""
    text: str
    icon: Icon | None = None
    data: Any = None
    secondary: str = ''


class ListView(Widget):
    """Scrollable list with icons, selection, and activate."""

    def __init__(self, x=0, y=0, w=200, h=200,
                 on_select: Callable | None = None,
                 on_activate: Callable | None = None):
        super().__init__(x, y, w, h)
        self.items: list[ListItem] = []
        self.selected = -1
        self.scroll_y = 0
        self.on_select_cb = on_select
        self.on_activate_cb = on_activate
        self.focusable = True
        self._row_height = 24  # updated in paint

    def _visible_rows(self) -> int:
        return max(1, self.height // self._row_height)

    def _ensure_visible(self, index: int):
        vis = self._visible_rows()
        if index < self.scroll_y:
            self.scroll_y = index
        if index >= self.scroll_y + vis:
            self.scroll_y = index - vis + 1

    def paint(self, vdi, ax, ay):
        ch = vdi.font.char_h
        cw = vdi.font.char_w
        self._row_height = max(ch + 4, 24)
        rh = self._row_height
        vis = self._visible_rows()
        text_w = self.width - 14  # scrollbar

        # Background
        vdi.fill_rect(ax, ay, self.width, self.height, Theme.LIST_BG)

        # Items
        for i in range(vis):
            idx = self.scroll_y + i
            if idx >= len(self.items):
                break
            item = self.items[idx]
            iy = ay + i * rh

            # Row background
            if idx == self.selected:
                vdi.fill_rect(ax, iy, text_w, rh, Theme.LIST_SELECTED)
                txt_color = Theme.LIST_SEL_TEXT
            elif idx % 2 == 1:
                vdi.fill_rect(ax, iy, text_w, rh, Theme.LIST_ALT)
                txt_color = Theme.WIDGET_TEXT
            else:
                txt_color = Theme.WIDGET_TEXT

            # Icon
            ix = ax + 4
            if item.icon:
                icon_y = iy + (rh - 16) // 2
                item.icon.draw(vdi, ix, icon_y)
                ix += 20

            # Text
            ty = iy + (rh - ch) // 2
            draw_text_clipped(vdi, ix, ty, text_w - (ix - ax) - 4,
                              item.text, txt_color)

            # Secondary text (right-aligned, dimmer)
            if item.secondary:
                sw = len(item.secondary) * cw
                sx = ax + text_w - sw - 4
                vdi.draw_string(sx, ty, item.secondary, Theme.WIDGET_TEXT_DIM, -1)

        # Scrollbar
        sb_x = ax + self.width - 14
        vdi.fill_rect(sb_x, ay, 14, self.height, Theme.SCROLL_TRACK)
        if len(self.items) > vis:
            thumb_h = max(20, int(self.height * vis / len(self.items)))
            thumb_y = int((self.height - thumb_h) * self.scroll_y /
                          max(1, len(self.items) - vis))
            vdi.fill_rect(sb_x + 1, ay + thumb_y, 12, thumb_h, Theme.SCROLL_THUMB)

        # Border
        border = Theme.INPUT_FOCUS if self._focused else Theme.WIDGET_BORDER
        draw_rect_outline(vdi, ax, ay, self.width, self.height, border)

    def on_click(self, rx, ry, button):
        rh = self._row_height
        idx = self.scroll_y + ry // rh
        if 0 <= idx < len(self.items):
            self.selected = idx
            if self.on_select_cb:
                self.on_select_cb(idx, self.items[idx])
        return True

    def on_double_click(self, rx, ry, button):
        rh = self._row_height
        idx = self.scroll_y + ry // rh
        if 0 <= idx < len(self.items):
            self.selected = idx
            if self.on_activate_cb:
                self.on_activate_cb(idx, self.items[idx])
        return True

    def on_key(self, key, mod):
        if key == K_UP:
            if self.selected > 0:
                self.selected -= 1
                self._ensure_visible(self.selected)
                if self.on_select_cb:
                    self.on_select_cb(self.selected, self.items[self.selected])
            return True
        elif key == K_DOWN:
            if self.selected < len(self.items) - 1:
                self.selected += 1
                self._ensure_visible(self.selected)
                if self.on_select_cb:
                    self.on_select_cb(self.selected, self.items[self.selected])
            return True
        elif key == K_RETURN:
            if 0 <= self.selected < len(self.items) and self.on_activate_cb:
                self.on_activate_cb(self.selected, self.items[self.selected])
            return True
        elif key == K_PAGEUP:
            self.selected = max(0, self.selected - self._visible_rows())
            self._ensure_visible(self.selected)
            if self.on_select_cb and 0 <= self.selected < len(self.items):
                self.on_select_cb(self.selected, self.items[self.selected])
            return True
        elif key == K_PAGEDOWN:
            self.selected = min(len(self.items) - 1,
                                self.selected + self._visible_rows())
            self._ensure_visible(self.selected)
            if self.on_select_cb and 0 <= self.selected < len(self.items):
                self.on_select_cb(self.selected, self.items[self.selected])
            return True
        return False


# ===================================================================
# IconView
# ===================================================================

@dataclass
class IconItem:
    """Item in an IconView."""
    label: str
    icon: Icon | None = None
    data: Any = None


class IconView(Widget):
    """Grid of icons with labels. Items are arranged in a grid."""

    def __init__(self, x=0, y=0, w=400, h=300, icon_scale=2,
                 on_select: Callable | None = None,
                 on_activate: Callable | None = None):
        super().__init__(x, y, w, h)
        self.items: list[IconItem] = []
        self.selected = -1
        self.scroll_y = 0
        self.icon_scale = icon_scale   # 1 = 16px, 2 = 32px
        self.cell_w = 80 if icon_scale >= 2 else 56
        self.cell_h = 64 if icon_scale >= 2 else 44
        self.on_select_cb = on_select
        self.on_activate_cb = on_activate
        self.focusable = True

    def _cols(self) -> int:
        return max(1, (self.width - 14) // self.cell_w)  # 14 for scrollbar

    def _rows_visible(self) -> int:
        return max(1, self.height // self.cell_h)

    def _total_rows(self) -> int:
        cols = self._cols()
        return (len(self.items) + cols - 1) // cols if cols > 0 else 0

    def paint(self, vdi, ax, ay):
        cw = vdi.font.char_w
        ch = vdi.font.char_h
        cols = self._cols()
        vis_rows = self._rows_visible()
        icon_size = 16 * self.icon_scale

        # Background
        vdi.fill_rect(ax, ay, self.width, self.height, Theme.LIST_BG)

        # Items in grid
        for idx, item in enumerate(self.items):
            row = idx // cols
            col = idx % cols
            screen_row = row - self.scroll_y
            if screen_row < 0 or screen_row >= vis_rows:
                continue

            cx = ax + col * self.cell_w + 4
            cy = ay + screen_row * self.cell_h + 4

            # Selection highlight
            if idx == self.selected:
                vdi.rounded_rect(cx, cy, self.cell_w - 8, self.cell_h - 8,
                                 4, Theme.ICON_SEL_BG)

            # Icon
            if item.icon:
                item.icon.draw_centered(vdi, cx, cy, self.cell_w - 8,
                                        icon_size + 4, self.icon_scale)

            # Label (below icon, centered, clipped)
            label_y = cy + icon_size + 6
            label_w = self.cell_w - 8
            max_label_chars = max(0, label_w // cw)
            label = item.label
            if len(label) > max_label_chars:
                label = label[:max_label_chars - 2] + '..' if max_label_chars > 2 else label[:max_label_chars]
            tw = len(label) * cw
            lx = cx + (self.cell_w - 8 - tw) // 2
            color = Theme.LIST_SEL_TEXT if idx == self.selected else Theme.ICON_LABEL
            vdi.draw_string(lx, label_y, label, color, -1)

        # Scrollbar
        total_rows = self._total_rows()
        sb_x = ax + self.width - 14
        vdi.fill_rect(sb_x, ay, 14, self.height, Theme.SCROLL_TRACK)
        if total_rows > vis_rows:
            thumb_h = max(20, int(self.height * vis_rows / total_rows))
            thumb_y = int((self.height - thumb_h) * self.scroll_y /
                          max(1, total_rows - vis_rows))
            vdi.fill_rect(sb_x + 1, ay + thumb_y, 12, thumb_h, Theme.SCROLL_THUMB)

        # Border
        border = Theme.INPUT_FOCUS if self._focused else Theme.WIDGET_BORDER
        draw_rect_outline(vdi, ax, ay, self.width, self.height, border)

    def on_click(self, rx, ry, button):
        cols = self._cols()
        col = rx // self.cell_w
        row = ry // self.cell_h + self.scroll_y
        idx = row * cols + col
        if 0 <= idx < len(self.items) and col < cols:
            self.selected = idx
            if self.on_select_cb:
                self.on_select_cb(idx, self.items[idx])
        return True

    def on_double_click(self, rx, ry, button):
        cols = self._cols()
        col = rx // self.cell_w
        row = ry // self.cell_h + self.scroll_y
        idx = row * cols + col
        if 0 <= idx < len(self.items) and col < cols:
            self.selected = idx
            if self.on_activate_cb:
                self.on_activate_cb(idx, self.items[idx])
        return True

    def on_key(self, key, mod):
        cols = self._cols()
        if key == K_LEFT:
            self.selected = max(0, self.selected - 1)
        elif key == K_RIGHT:
            self.selected = min(len(self.items) - 1, self.selected + 1)
        elif key == K_UP:
            self.selected = max(0, self.selected - cols)
        elif key == K_DOWN:
            self.selected = min(len(self.items) - 1, self.selected + cols)
        elif key == K_RETURN:
            if 0 <= self.selected < len(self.items) and self.on_activate_cb:
                self.on_activate_cb(self.selected, self.items[self.selected])
            return True
        else:
            return False

        # Ensure visible
        row = self.selected // cols
        vis = self._rows_visible()
        if row < self.scroll_y:
            self.scroll_y = row
        if row >= self.scroll_y + vis:
            self.scroll_y = row - vis + 1

        if self.on_select_cb and 0 <= self.selected < len(self.items):
            self.on_select_cb(self.selected, self.items[self.selected])
        return True


# ===================================================================
# Toolbar
# ===================================================================

class Toolbar(Widget):
    """Horizontal toolbar with icon buttons and separators."""

    def __init__(self, x=0, y=0, w=0, h=32):
        super().__init__(x, y, w, h)
        self._next_x = 4

    def add_button(self, icon: str | Icon | None = None, tooltip: str = '',
                   on_click: Callable | None = None) -> IconButton:
        """Add an icon button to the toolbar."""
        btn = IconButton(x=self._next_x, y=2, size=self.height - 4,
                         icon=icon, tooltip=tooltip, on_click=on_click)
        self.add(btn)
        self._next_x += self.height - 2
        return btn

    def add_separator(self):
        """Add a vertical separator."""
        sep = Separator(x=self._next_x + 2, y=4,
                        length=self.height - 8,
                        orientation=Separator.VERTICAL)
        self.add(sep)
        self._next_x += 8

    def add_widget(self, child: Widget, width: int = 0) -> Widget:
        """Add an arbitrary widget to the toolbar."""
        child.x = self._next_x
        child.y = 2
        child.height = self.height - 4
        if width:
            child.width = width
        self.add(child)
        self._next_x += child.width + 4
        return child

    def paint(self, vdi, ax, ay):
        vdi.grad_rect(ax, ay, self.width, self.height,
                      Theme.TOOLBAR_BG, 0x141C30)
        vdi.fill_rect(ax, ay + self.height - 1, self.width, 1, Theme.TOOLBAR_SEP)


# ===================================================================
# StatusBar
# ===================================================================

class StatusBar(Widget):
    """Bottom status bar with text sections."""

    def __init__(self, x=0, y=0, w=0, h=22):
        super().__init__(x, y, w, h)
        self.sections: list[str] = []

    def paint(self, vdi, ax, ay):
        cw = vdi.font.char_w
        ch = vdi.font.char_h

        vdi.fill_rect(ax, ay, self.width, self.height, Theme.STATUS_BG)
        vdi.fill_rect(ax, ay, self.width, 1, Theme.STATUS_BORDER)

        tx = ax + 6
        ty = ay + (self.height - ch) // 2
        for i, section in enumerate(self.sections):
            if i > 0:
                # Separator
                vdi.fill_rect(tx, ay + 3, 1, self.height - 6, Theme.STATUS_BORDER)
                tx += 8
            vdi.draw_string(tx, ty, section, Theme.STATUS_TEXT, -1)
            tx += len(section) * cw + 12


# ===================================================================
# ProgressBar
# ===================================================================

class ProgressBar(Widget):
    """Progress indicator bar."""

    def __init__(self, x=0, y=0, w=200, h=16, value=0, max_value=100):
        super().__init__(x, y, w, h)
        self.value = value
        self.max_value = max_value

    def paint(self, vdi, ax, ay):
        vdi.fill_rect(ax, ay, self.width, self.height, Theme.PROGRESS_BG)
        draw_rect_outline(vdi, ax, ay, self.width, self.height, Theme.WIDGET_BORDER)
        if self.max_value > 0 and self.value > 0:
            fill_w = int((self.width - 2) * min(1.0, self.value / self.max_value))
            if fill_w > 0:
                vdi.grad_rect(ax + 1, ay + 1, fill_w, self.height - 2,
                              Theme.ACCENT, Theme.ACCENT_LIGHT)


# ===================================================================
# ContextMenu
# ===================================================================

@dataclass
class ContextMenuItem:
    label: str
    callback: Callable | None = None
    separator: bool = False
    enabled: bool = True
    shortcut: str = ''


class ContextMenu(Widget):
    """Popup context menu."""

    def __init__(self, items: list[ContextMenuItem] | None = None):
        super().__init__(0, 0, 0, 0)
        self.menu_items: list[ContextMenuItem] = items or []
        self.highlight = -1
        self._open = False

    def open_at(self, x: int, y: int, vdi) -> None:
        """Position and size the menu."""
        cw = vdi.font.char_w
        ch = vdi.font.char_h

        # Calculate size
        max_label = max((len(m.label) for m in self.menu_items if not m.separator),
                        default=0)
        max_short = max((len(m.shortcut) for m in self.menu_items if not m.separator),
                        default=0)
        item_w = (max_label + max_short + 4) * cw + 16
        item_h = sum(8 if m.separator else ch + 6 for m in self.menu_items) + 4

        self.x = min(x, vdi.width - item_w - 4)
        self.y = min(y, vdi.height - item_h - 4)
        self.width = item_w
        self.height = item_h
        self.highlight = -1
        self._open = True

    def paint(self, vdi, ax, ay):
        if not self._open:
            return
        ch = vdi.font.char_h
        cw = vdi.font.char_w

        # Shadow
        vdi.shadow_rect(ax, ay, self.width, self.height, 4, 60)
        # Background
        vdi.fill_rect(ax, ay, self.width, self.height, Theme.MENU_BG)
        draw_rect_outline(vdi, ax, ay, self.width, self.height, Theme.MENU_BORDER)

        iy = ay + 2
        for i, item in enumerate(self.menu_items):
            if item.separator:
                vdi.fill_rect(ax + 4, iy + 3, self.width - 8, 1, Theme.MENU_SEP)
                iy += 8
                continue

            item_h = ch + 6
            if i == self.highlight and item.enabled:
                vdi.fill_rect(ax + 2, iy, self.width - 4, item_h, Theme.MENU_HIGHLIGHT)
                txt_color = Theme.MENU_HI_TEXT
            else:
                txt_color = Theme.MENU_TEXT if item.enabled else Theme.WIDGET_TEXT_DIM

            ty = iy + 3
            vdi.draw_string(ax + 8, ty, item.label, txt_color, -1)
            if item.shortcut:
                sw = len(item.shortcut) * cw
                vdi.draw_string(ax + self.width - sw - 8, ty,
                                item.shortcut, Theme.MENU_SHORTCUT, -1)
            iy += item_h

    def on_click(self, rx, ry, button):
        if not self._open:
            return False
        ch = 23  # approximate
        iy = 2
        for i, item in enumerate(self.menu_items):
            if item.separator:
                iy += 8
                continue
            item_h = ch + 6
            if iy <= ry < iy + item_h:
                if item.enabled and item.callback:
                    self._open = False
                    item.callback()
                    return True
                return True
            iy += item_h
        self._open = False
        return True

    def close(self):
        self._open = False


# ===================================================================
# WidgetHost — bridges AES Window to widget tree
# ===================================================================

class WidgetHost:
    """Connects an AES Window's callbacks to a widget tree.

    Usage:
        root = Panel(...)
        root.add(Button(...))
        host = WidgetHost(root)
        win = aes.create_window("Title", ...,
            on_redraw=host.on_redraw,
            on_click=host.on_click,
            on_key=host.on_key)
    """

    def __init__(self, root: Widget):
        self.root = root
        self._focus: Widget | None = None
        self._last_click_time: float = 0
        self._last_click_pos: tuple[int, int] = (0, 0)
        self.DOUBLE_CLICK_MS = 400
        self.DOUBLE_CLICK_DIST = 5

    def focus(self, widget: Widget | None):
        """Set focused widget."""
        if self._focus is not None:
            self._focus._focused = False
        self._focus = widget
        if widget is not None:
            widget._focused = True

    @property
    def focused_widget(self) -> Widget | None:
        return self._focus

    def on_redraw(self, win, vdi):
        """Window redraw callback."""
        cr = win.client_rect()
        self.root.x = 0
        self.root.y = 0
        self.root.width = cr[2]
        self.root.height = cr[3]
        self.root.draw(vdi, cr[0], cr[1])

    def on_click(self, win, rx, ry, button):
        """Window click callback with double-click detection."""
        now = time.time() * 1000
        is_double = (
            now - self._last_click_time < self.DOUBLE_CLICK_MS and
            abs(rx - self._last_click_pos[0]) < self.DOUBLE_CLICK_DIST and
            abs(ry - self._last_click_pos[1]) < self.DOUBLE_CLICK_DIST
        )
        self._last_click_time = now
        self._last_click_pos = (rx, ry)

        # Update focus
        target = self.root.hit_test(rx, ry)
        if target and target.focusable:
            self.focus(target)
        elif target and not target.focusable:
            # Don't steal focus from current widget on non-focusable click
            pass

        if is_double:
            self.root.dispatch_double_click(rx, ry, button)
        else:
            self.root.dispatch_click(rx, ry, button)

    def on_key(self, win, key, mod):
        """Window key callback — dispatches to focused widget."""
        if self._focus and self._focus.on_key(key, mod):
            return
        # Fallback: try root
        self.root.on_key(key, mod)

    def on_resize(self, win, new_w, new_h):
        """Window resize callback — update root dimensions."""
        cr = win.client_rect()
        self.root.width = cr[2]
        self.root.height = cr[3]


# ===================================================================
# Lisp syntax highlighting
# ===================================================================

# Lisp keywords for syntax highlighting
_LISP_KEYWORDS = {
    'defun', 'lambda', 'let', 'let*', 'letrec', 'if', 'cond', 'when',
    'unless', 'and', 'or', 'not', 'set!', 'begin', 'progn', 'quote',
    'define', 'defmacro', 'defclass', 'defmethod', 'defgeneric',
    'while', 'loop', 'do', 'dotimes', 'dolist',
    'nil', 't', 'else',
}

_LISP_BUILTINS = {
    'cons', 'car', 'cdr', 'list', 'append', 'reverse', 'map', 'filter',
    'apply', 'funcall', 'eval', 'print', 'println', 'newline', 'read',
    '+', '-', '*', '/', '=', '<', '>', '<=', '>=', 'eq', 'equal',
    'null', 'atom', 'consp', 'fixnump', 'symbolp', 'stringp',
    'make-vector', 'vector-ref', 'vector-set!', 'vector-length',
    'set-car!', 'set-cdr!', 'format',
}

# Colors for syntax highlighting
SYN_KEYWORD  = 0xD500F9   # magenta
SYN_BUILTIN  = 0x00D2FF   # cyan
SYN_STRING   = 0x00E676   # green
SYN_COMMENT  = 0x607080   # dim gray
SYN_NUMBER   = 0xFFD740   # yellow
SYN_PAREN    = 0x6080A0   # blue-gray
SYN_DEFAULT  = 0xE0E8F0   # light


def lisp_syntax_highlight(line: str, line_no: int) -> list[tuple[int, int, int]]:
    """Return [(start_col, end_col, color), ...] for a line of Lisp code."""
    spans: list[tuple[int, int, int]] = []
    i = 0
    n = len(line)

    while i < n:
        c = line[i]

        # Comment
        if c == ';':
            spans.append((i, n, SYN_COMMENT))
            break

        # String
        if c == '"':
            j = i + 1
            while j < n and line[j] != '"':
                if line[j] == '\\':
                    j += 1
                j += 1
            if j < n:
                j += 1  # include closing quote
            spans.append((i, j, SYN_STRING))
            i = j
            continue

        # Parentheses
        if c in '()[]{}':
            spans.append((i, i + 1, SYN_PAREN))
            i += 1
            continue

        # Whitespace
        if c in ' \t':
            i += 1
            continue

        # Word (symbol, keyword, number)
        if c not in ' \t()[]{};"':
            j = i
            while j < n and line[j] not in ' \t()[]{};"':
                j += 1
            word = line[i:j]

            # Number
            if word.lstrip('-').isdigit():
                spans.append((i, j, SYN_NUMBER))
            elif word in _LISP_KEYWORDS:
                spans.append((i, j, SYN_KEYWORD))
            elif word in _LISP_BUILTINS:
                spans.append((i, j, SYN_BUILTIN))
            else:
                spans.append((i, j, SYN_DEFAULT))
            i = j
            continue

        i += 1

    return spans
