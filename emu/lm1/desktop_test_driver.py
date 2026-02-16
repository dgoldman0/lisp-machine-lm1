"""Crystal Desktop visual testing harness.

Provides headless screenshot capture and synthetic input for
multimodal visual testing of the desktop.

Usage:
    from lm1.desktop_test_driver import DesktopDriver

    d = DesktopDriver(640, 480)
    d.screenshot("initial.png")       # save framebuffer as PNG
    d.click(100, 50)                  # synthetic click
    d.type_text("hello")              # synthetic typing
    d.screenshot("after_typing.png")  # capture again
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .vdi import (
    VDI, CHAR_W, CHAR_H,
    EVT_NONE, EVT_KEY_DOWN, EVT_KEY_UP,
    EVT_MOUSE_MOVE, EVT_MOUSE_DOWN, EVT_MOUSE_UP, EVT_QUIT,
)
from .desktop import (
    AES, Window, Colors, Menu, MenuItem,
    TITLE_BAR_H, BORDER_W, MENU_BAR_H,
    WIN_CLOSEABLE, WIN_MOVEABLE, WIN_RESIZABLE,
    TerminalCrystallite, ClockCrystallite, CalculatorCrystallite,
)


# Default output directory for screenshots
SCREENSHOT_DIR = Path(__file__).parent.parent / "test_screenshots"


class DesktopDriver:
    """Headless desktop driver for visual testing.

    Creates a fully functional Crystal Desktop in headless mode,
    provides screenshot capture and synthetic input injection.
    """

    def __init__(self, width: int = 640, height: int = 480,
                 screenshot_dir: Optional[Path] = None):
        self.vdi = VDI(width=width, height=height, headless=True)
        self.aes = AES(self.vdi)
        self.screenshot_dir = screenshot_dir or SCREENSHOT_DIR
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._shot_count = 0

    # ------------------------------------------------------------------
    # Screenshot capture
    # ------------------------------------------------------------------

    def screenshot(self, name: Optional[str] = None) -> Path:
        """Render the desktop and save as PNG.

        Returns the path to the saved image.
        """
        self.aes.redraw()
        img = self.vdi.to_pil_image()

        if name is None:
            self._shot_count += 1
            name = f"shot_{self._shot_count:04d}.png"
        if not name.endswith('.png'):
            name += '.png'

        path = self.screenshot_dir / name
        img.save(str(path))
        return path

    def screenshot_scaled(self, name: Optional[str] = None,
                          scale: int = 2) -> Path:
        """Save screenshot at scaled resolution."""
        self.aes.redraw()
        img = self.vdi.to_pil_image()
        scaled = img.resize(
            (self.vdi.width * scale, self.vdi.height * scale),
            resample=0,  # nearest neighbor
        )

        if name is None:
            self._shot_count += 1
            name = f"shot_{self._shot_count:04d}_x{scale}.png"
        if not name.endswith('.png'):
            name += '.png'

        path = self.screenshot_dir / name
        scaled.save(str(path))
        return path

    # ------------------------------------------------------------------
    # Synthetic input
    # ------------------------------------------------------------------

    def click(self, x: int, y: int, button: int = 1) -> None:
        """Simulate a mouse click at (x, y) in framebuffer coords."""
        # Move to position first
        self.aes.handle_event(EVT_MOUSE_MOVE, x, y)
        # Button down
        self.aes.handle_event(EVT_MOUSE_DOWN, x, y | (button << 16))
        # Button up
        self.aes.handle_event(EVT_MOUSE_UP, x, y | (button << 16))

    def mouse_down(self, x: int, y: int, button: int = 1) -> None:
        """Simulate mouse button press."""
        self.aes.handle_event(EVT_MOUSE_MOVE, x, y)
        self.aes.handle_event(EVT_MOUSE_DOWN, x, y | (button << 16))

    def mouse_up(self, x: int, y: int, button: int = 1) -> None:
        """Simulate mouse button release."""
        self.aes.handle_event(EVT_MOUSE_UP, x, y | (button << 16))

    def mouse_move(self, x: int, y: int) -> None:
        """Simulate mouse movement."""
        self.aes.handle_event(EVT_MOUSE_MOVE, x, y)

    def drag(self, x1: int, y1: int, x2: int, y2: int,
             steps: int = 5, button: int = 1) -> None:
        """Simulate a mouse drag from (x1,y1) to (x2,y2)."""
        self.mouse_down(x1, y1, button)
        for i in range(1, steps + 1):
            t = i / steps
            x = int(x1 + (x2 - x1) * t)
            y = int(y1 + (y2 - y1) * t)
            self.mouse_move(x, y)
        self.mouse_up(x2, y2, button)

    def type_text(self, text: str) -> None:
        """Simulate typing a string (key down/up for each char)."""
        for ch in text:
            key = ord(ch)
            mod = 0
            if ch.isupper():
                mod = 1  # shift
                key = ord(ch.lower())
            self.aes.handle_event(EVT_KEY_DOWN, key, mod)
            self.aes.handle_event(EVT_KEY_UP, key, mod)

    def press_key(self, key: int, mod: int = 0) -> None:
        """Simulate a single key press."""
        self.aes.handle_event(EVT_KEY_DOWN, key, mod)
        self.aes.handle_event(EVT_KEY_UP, key, mod)

    def press_enter(self) -> None:
        """Simulate pressing Enter."""
        self.press_key(13)  # pygame.K_RETURN

    def press_backspace(self) -> None:
        """Simulate pressing Backspace."""
        self.press_key(8)  # pygame.K_BACKSPACE

    # ------------------------------------------------------------------
    # Convenience: setup standard desktop
    # ------------------------------------------------------------------

    def setup_default_desktop(self) -> dict:
        """Create the default desktop with terminal, clock, calculator.

        Returns dict of crystallite instances.
        """
        terminal = TerminalCrystallite(self.aes, x=20, y=40, w=400, h=300)
        clock = ClockCrystallite(self.aes, x=440, y=30)
        calc = CalculatorCrystallite(self.aes, x=440, y=120)
        return {
            'terminal': terminal,
            'clock': clock,
            'calculator': calc,
        }

    # ------------------------------------------------------------------
    # Pixel inspection
    # ------------------------------------------------------------------

    def get_pixel(self, x: int, y: int) -> int:
        """Get framebuffer palette index at (x, y)."""
        if 0 <= x < self.vdi.width and 0 <= y < self.vdi.height:
            return self.vdi.fb[y * self.vdi.width + x]
        return -1

    def get_pixel_rgb(self, x: int, y: int) -> tuple[int, int, int]:
        """Get RGB color at (x, y)."""
        idx = self.get_pixel(x, y)
        if idx >= 0:
            return self.vdi.palette[idx]
        return (0, 0, 0)

    def region_colors(self, x: int, y: int, w: int, h: int) -> set[int]:
        """Get the set of palette indices used in a rectangular region."""
        colors = set()
        for py in range(y, y + h):
            for px in range(x, x + w):
                if 0 <= px < self.vdi.width and 0 <= py < self.vdi.height:
                    colors.add(self.vdi.fb[py * self.vdi.width + px])
        return colors
