# LM-1 Desktop Environment — "Crystal" (GEM Spiritual Successor)

**Status:** Design sketch  
**Date:** 2026-02-16

---

## 1. Heritage and Goals

Crystal is a desktop environment for Lispos, spiritually descended from Digital Research's **GEM** (Graphics Environment Manager) as used on the Atari ST. It inherits GEM's architectural clarity while exploiting the fact that LM-1 is a Lisp machine — every object on screen is a live Lisp object, inspectable, scriptable, and hot-swappable.

### 1.1 What We Take from GEM

| GEM Principle | Crystal Equivalent |
|---|---|
| **AES/VDI split** (UI policy vs. drawing) | Crystal AES (window/event manager) + VDI driver layer (drawing primitives, backed by HW blit engine) |
| **Desk accessories** (system-owned, always-available micro-apps) | **Crystalets** — first-class, system-registered, available from any context |
| **Clipboard as inter-app protocol** (.SCP scrap directory) | **Scrap** — structured clipboard with type negotiation, history, persistence |
| **UI defined as editable resources** (.RSC files) | **Resource objects** — menus, dialogs, alerts are Lisp data structures, live-editable, theme-swappable |
| **Desktop state in config artifacts** (DESKTOP.INF) | **Desktop profile** — a serialized Lisp form, version-controlled, sync-able |

### 1.2 What We Add

- **Live objects everywhere.** Every window, icon, menu item is a Lisp object with an inspector. `(inspect (window 3))` opens its slots live.
- **Macro recorder / scripting.** All AES events are first-class Lisp events. Record → replay → edit → bind to a key.
- **Theming as data.** Colors, fonts, widget metrics, icon sets are a theme object. Swap themes without restarting.
- **Hot code reload.** Redefine a widget class and all live instances update. No restart, no "apply."
- **Actor-based apps.** Each application is an actor (closure + mailbox). The desktop is the supervisor actor.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     User Applications                         │
│   (Editor, File Manager, Terminal, Browser, Games, …)         │
├──────────────────────────────────────────────────────────────┤
│                       Crystal AES                             │
│   (Window manager, event dispatch, menu bar, Crystalets,      │
│    drag & drop, focus policy, z-order, keyboard shortcuts)     │
├──────────────────────────────────────────────────────────────┤
│                      Resource System                          │
│   (Themes, icons, fonts, cursors, color palettes, RSC objs)   │
├──────────────────────────────────────────────────────────────┤
│                       Crystal VDI                             │
│   (Drawing primitives: lines, rects, circles, text, bitblt,   │
│    clipping, coordinate transforms, font rasterizer)          │
├──────────────────────────────────────────────────────────────┤
│                    VDI Device Driver                           │
│   (Talks to HW VDI engine: MMIO registers, blit commands,     │
│    framebuffer management, vsync, palette, cursor)            │
├──────────────────────────────────────────────────────────────┤
│                   Lispos / LM-1 Hardware                      │
└──────────────────────────────────────────────────────────────┘
```

### 2.1 Crystal VDI (Drawing Layer)

Mirrors GEM VDI's role: a **device-independent drawing API**. Applications never touch the framebuffer directly.

```lisp
;; VDI primitives (subset)
(vdi:open-workstation mode)           → workstation-handle
(vdi:close-workstation ws)
(vdi:set-clip ws x1 y1 x2 y2)
(vdi:polyline ws points)
(vdi:filled-rect ws x1 y1 x2 y2)
(vdi:text ws x y string)
(vdi:bitblt ws src-rect dst-rect rop)
(vdi:set-color ws index r g b)
(vdi:set-font ws font-id size)
(vdi:raster-copy ws src dst width height rop)
```

The VDI driver translates these into MMIO writes to the hardware blit engine for accelerated operations, or falls back to software rasterization for complex paths.

### 2.2 Crystal AES (Window/Event Manager)

Mirrors GEM AES: manages windows, menus, events, dialogs, and desk accessories.

#### 2.2.1 Windows

```lisp
(defclass crystal-window ()
  ((title     :initarg :title     :accessor window-title)
   (x         :initarg :x         :accessor window-x)
   (y         :initarg :y         :accessor window-y)
   (width     :initarg :width     :accessor window-w)
   (height    :initarg :height    :accessor window-h)
   (z-order   :initarg :z-order   :accessor window-z)
   (content   :initarg :content   :accessor window-content)
   (flags     :initarg :flags     :accessor window-flags)
   (redraw-fn :initarg :redraw    :accessor window-redraw-fn)
   (event-fn  :initarg :on-event  :accessor window-event-fn)
   (owner     :initarg :owner     :accessor window-owner)))
```

Window operations: `open`, `close`, `move`, `resize`, `raise`, `lower`, `full`, `iconify`.

Each window has a **redraw function** (called by the AES when the window needs repainting) and an **event function** (receives keyboard, mouse, menu events).

#### 2.2.2 Event Loop

```lisp
(defun app-main (app)
  "Standard application event loop."
  (loop
    (let ((event (aes:get-event app :types '(:keyboard :mouse :message :timer))))
      (case (event-type event)
        (:redraw    (funcall (window-redraw-fn (event-window event))
                             (event-window event) (event-rect event)))
        (:keyboard  (funcall (window-event-fn (event-window event))
                             :key event))
        (:mouse     (funcall (window-event-fn (event-window event))
                             :mouse event))
        (:message   (handle-message app (event-message event)))
        (:close     (return))))))
```

#### 2.2.3 Menu Bar

The menu bar is global (like classic Mac/GEM). The active application's menu merges with the system menu:

```
┌─ Crystal ─┬─ File ─┬─ Edit ─┬─ View ─┬─ (app menus…) ─┬─ Crystalets ─┐
│  About…    │ New    │ Cut    │ Icons  │                 │  Clock       │
│  Prefs…    │ Open…  │ Copy   │ List   │                 │  Calculator  │
│  Shutdown  │ Save   │ Paste  │ Sort…  │                 │  Terminal    │
│            │ Close  │ Scrap… │        │                 │  Inspector   │
└────────────┴────────┴────────┴────────┘                 └──────────────┘
```

Menus are Lisp lists. Editing a menu is editing a list.

```lisp
(aes:set-menu-bar
  `(("Crystal" (:item "About…"   :action about-crystal)
                (:item "Prefs…"   :action open-prefs)
                (:sep)
                (:item "Shutdown" :action shutdown))
    ("File"    (:item "New"       :action file-new    :key "^N")
               (:item "Open…"    :action file-open   :key "^O")
               (:item "Save"     :action file-save   :key "^S")
               (:item "Close"    :action file-close   :key "^W"))))
```

#### 2.2.4 Crystalets (Desk Accessories)

System-registered micro-apps that live in their own windows but are always accessible, even when another application has focus. Like GEM desk accessories, but implemented as actors.

```lisp
(defcrystalet clock
  :title "Clock"
  :size (120 . 40)
  :redraw (lambda (win rect)
            (vdi:filled-rect (window-vdi win) rect *bg-color*)
            (vdi:text (window-vdi win) 4 20
                      (format-time (get-universal-time))))
  :timer 1000)  ; redraw every second
```

Standard Crystalets:
- **Clock** — always-visible time
- **Calculator** — RPN or algebraic
- **Terminal** — REPL in a window
- **Inspector** — inspect any Lisp object
- **Scrap Viewer** — clipboard history
- **Control Panel** — theme, mouse speed, screen resolution

### 2.3 Resource System

All UI elements — menus, dialogs, alerts, icons, fonts — are **resource objects** stored as Lisp data. They can be:

- **Edited live** in the Inspector or a resource editor
- **Themed** by swapping the resource set
- **Localized** by swapping string tables
- **Serialized** to disk and loaded on boot

```lisp
;; A dialog resource (like a GEM .RSC dialog tree)
(defresource file-open-dialog
  :type :dialog
  :title "Open File"
  :items ((:label "Filename:" :x 10 :y 10)
          (:text-field :id :filename :x 100 :y 10 :width 200)
          (:button "OK"     :id :ok     :x 100 :y 50 :default t)
          (:button "Cancel" :id :cancel :x 200 :y 50)))
```

### 2.4 Scrap (Clipboard)

The scrap is a typed, structured clipboard with history:

```lisp
(scrap:put :text "Hello, world")
(scrap:put :sexp '(+ 1 2 3))
(scrap:put :bitmap <bitmap-object>)

(scrap:get :text)       → "Hello, world"
(scrap:get :sexp)       → (+ 1 2 3)
(scrap:history)         → list of past scrap entries
(scrap:get :text :ago 2) → the text from 2 copies ago
```

Applications negotiate types: if you paste into an S-expression editor, it prefers `:sexp`; into a text field, `:text`.

### 2.5 Desktop Profile (DESKTOP.INF successor)

The desktop state — icon positions, window positions, file associations, background color, dock contents — is a Lisp form:

```lisp
;; ~/.crystal/desktop.lisp
(desktop
  :background :color #x2F4F4F
  :icon-grid (16 . 16)
  :icons ((:name "System" :position (10 . 10) :type :folder)
          (:name "Documents" :position (10 . 80) :type :folder)
          (:name "Trash" :position (10 . 700) :type :trash))
  :windows ((:app terminal :position (200 . 100) :size (640 . 400)))
  :associations ((".lisp" . editor)
                 (".txt"  . editor)
                 (".png"  . image-viewer)))
```

Save/load/diff/sync are trivial because it's just data.

---

## 3. Desktop Manager ("The Desktop")

The desktop itself is an actor — the root of the UI actor tree.

```
                    ┌──────────┐
                    │ Desktop  │
                    │  Actor   │
                    └────┬─────┘
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         ┌────────┐ ┌────────┐ ┌────────┐
         │ Window │ │ Window │ │Crysta- │
         │ (App1) │ │ (App2) │ │ let    │
         └────┬───┘ └────┬───┘ └────────┘
              ▼          ▼
         App1 Actor  App2 Actor
```

The desktop actor:
1. Owns the root window (background, icons).
2. Manages the global menu bar.
3. Routes events to the focused window's actor.
4. Manages Crystalets (always-available accessories).
5. Handles file manager duties (icon grid, drag-and-drop, file associations).
6. Serializes/deserializes the desktop profile.

---

## 4. The File Manager

Crystal includes a built-in spatial file manager (like GEM Desktop / early Mac Finder):

- **Each folder is a window.** Opening a folder opens its window at its remembered position.
- **Icons are draggable.** Drag to move, drag to trash, drag to app icon to open-with.
- **Double-click opens.** Uses file associations from the desktop profile.
- **Path bar** at the top of each folder window for navigation.
- **Spatial memory.** Each folder remembers its window position, size, icon arrangement, and view mode (icons/list/details).

---

## 5. Standard Applications

Crystal ships with a small set of integrated apps, all written in Lisp:

| App | Description |
|-----|-------------|
| **Terminal** | REPL / listener window. Multiple tabs. |
| **Editor** | Lisp-aware text editor (Hemlock/Zmacs lineage). Syntax highlighting, paredit, eval-in-place. |
| **File Manager** | Spatial icon-based file browser (see § 4). |
| **Inspector** | Live object inspector. Select any object on screen and inspect its slots. |
| **Image Viewer** | View bitmaps, icons, resource images. |
| **Control Panel** | System settings: theme, colors, fonts, mouse, display, network. |
| **Help** | Hypertext help system with live examples. |

---

## 6. Theming

A theme is a Lisp object containing all visual parameters:

```lisp
(deftheme "Atari ST Classic"
  :colors (:background #xFFFFFF
           :foreground #x000000
           :title-bar  #x00AA00
           :title-text #xFFFFFF
           :selection  #x0000AA
           :selection-text #xFFFFFF
           :button-face #xCCCCCC
           :button-shadow #x888888
           :button-highlight #xFFFFFF)
  :fonts  (:system     (font "System" 10)
           :title      (font "System" 10 :bold)
           :mono       (font "Mono" 9)
           :menu       (font "System" 10))
  :metrics (:title-bar-height 20
            :border-width 1
            :scrollbar-width 16
            :icon-size 32
            :menu-height 20)
  :icons    (load-icon-set "classic-st"))
```

Switching themes: `(crystal:set-theme "Atari ST Classic")` — all windows redraw immediately.

---

## 7. Keyboard and Mouse Model

### 7.1 Keyboard

- Global keyboard shortcuts (menu accelerators) handled by AES.
- Window-local shortcuts handled by the window's event function.
- Modifier keys: Control, Alt/Meta, Shift. (No Windows/Super key — this isn't that kind of machine.)
- Dead keys and compose sequences for international input.

### 7.2 Mouse

- Hardware cursor (VDI engine overlay, zero-latency tracking).
- Single-click selects, double-click opens/activates.
- Right-click context menu (like GEM with a popup extension).
- Drag for move/resize/select. Rubber-band selection on desktop.
- Mouse focus follows GEM model: click-to-focus for windows (not sloppy focus).

---

## 8. Emulator Support

In the emulator, Crystal renders to:

1. **SDL window** on the host — the primary mode. The emulator opens an SDL2 window, maps the LM-1 framebuffer to an SDL texture, and presents at 60 Hz. Mouse/keyboard events from SDL are injected as LM-1 events.
2. **VNC server** — headless mode. The framebuffer is served over VNC for remote access.
3. **Web canvas** — experimental. WebSocket + Canvas for browser-based access.

The emulator's VDI device driver is a Python class that:
- Intercepts MMIO writes to `VDI_*` registers
- Executes blit/fill operations on a NumPy array (the framebuffer)
- Renders the framebuffer to SDL/VNC/web at vsync

---

## 9. Implementation Phases

Crystal is built incrementally alongside the Lispos kernel:

1. **VDI driver** — framebuffer, palette, text output, rect fill, bitblt (emulator: SDL/Pygame)
2. **Window manager** — window open/close/move/resize, z-order, redraw
3. **Event system** — keyboard, mouse, timer events routed to windows
4. **Menu bar** — global menu, menu item dispatch, keyboard shortcuts
5. **Crystalets** — clock, calculator, terminal
6. **File manager** — folder windows, icons, drag-and-drop
7. **Resource editor** — edit dialogs, menus, icons visually
8. **Theming** — theme objects, live switching
9. **Scrap** — clipboard with type negotiation and history
10. **Full app suite** — editor, inspector, control panel, help
