# LM-1 Build Plan

**Date:** 2026-02-16  
**Updated:** 2026-02-16

---

## Guiding Principle

Build bottom-up. Each phase produces something testable. No phase depends on something that isn't already working.

## Status Key

- [x] Done and committed
- [ ] Not started

---

## Phases

### Phase 1: Emulator Core — Scalar Execution ✅
**Goal:** Execute raw scalar instructions on 1 tile, 1 thread.
**Status:** Complete. 26 tests. Committed.

- [x] Word type (64-bit int with tag helpers)
- [x] Instruction encoding/decoding (32-bit → opcode + fields)
- [x] Register file (32 × u64 + special registers)
- [x] Memory (flat byte-addressable read/write)
- [x] Execution loop: fetch → decode → match → execute
- [x] Scalar ops: `ADD`, `SUB`, `AND`, `OR`, `XOR`, `SHL`, `SHR`, `LI`, `LUI`
- [x] Raw loads/stores: `LDR`, `STR`
- [x] Branches: `BR`, `BR.T`, `BR.NIL`, `BR.EQ`, `BR.FIX.LT/EQ/GT`
- [x] `NOP`, `HALT`, `TILE.ID`, `THREAD.ID`, `CYCLE`
- [x] Console I/O traps: `TRAP #0x80` (putchar), `TRAP #0x81` (getchar)
- [x] Minimal CLI: load a binary, run it, print output
- [x] Test: hand-assemble a program that prints "LM-1\n"

### Phase 2: Tagged Operations & Allocation ✅
**Goal:** Fixnum arithmetic, type tests, and nursery allocation work.
**Status:** Complete. 20 tests. Committed.

- [x] Tagged arithmetic: `ADD.FIX`, `SUB.FIX`, `MUL.FIX`, `DIV.FIX`, `ADD.FIX.IMM`
- [x] Type tests: `TST` (all tag variants), `EQ`, `CMP.TAGGED`
- [x] Nursery state: `np`, `nl` registers, configurable nursery region
- [x] `ALLOC`, `ALLOC.CONS`, `ALLOCV`, `ALLOC.CLOSURE`
- [x] Header template table (array of u64, indexed by instruction field)
- [x] Tagged field access: `LD`, `ST`, `LD.CAR`, `LD.CDR`
- [x] Test: program that conses a list of 100 fixnums, walks it, sums them

### Phase 3: Traps and GC ✅
**Goal:** Trap mechanism works. Nursery overflow triggers GC.
**Status:** Complete. 16 tests. Committed.

- [x] Trap table: per-thread base register, dispatch on trap code
- [x] Trap entry: save PC, jump to handler. `ERET`: restore PC, resume.
- [x] `PUSH`, `POP`, `PUSH.MULTI`, `POP.MULTI`
- [x] Write barrier: `ST.WB`, `ST.CAR`, `ST.CDR` — card table update
- [x] Card table: byte array, mark on cross-gen store
- [x] `TRAP_NURSERY_OVERFLOW` handler (Cheney's copy GC in LM-1 asm)
- [x] Cluster shared SRAM as old-gen target
- [x] Test: allocate until nursery overflows 10×, verify live objects survive

### Phase 4: Dispatch (IC) ✅
**Goal:** `CALL.IC` hit/miss/install cycle works.
**Status:** Complete. 8 tests. Committed.

- [x] IC table: hash map of (callsite, shape) → code_entry per tile
- [x] `CALL.IC`, `IC.INSTALL`, `CALL.DIRECT`, `CALL.CLOSURE`, `RET`
- [x] `TAILCALL.IC`, `TAILCALL.DIRECT`
- [x] Frame push/pop mechanics (save lr, fp to stack)
- [x] `TST.SHAPE`
- [x] Test: define two "classes" (shapes), dispatch a method on each

### Phase 5: Messaging & Multi-Tile ✅
**Goal:** Multiple tiles running, passing messages.
**Status:** Complete. 14 tests. Committed.

- [x] Multi-tile memory layout (N tiles × SRAM)
- [x] Multi-thread scheduling (round-robin within tile)
- [x] Hardware queues: `SEND`, `RECV`, `TRY.RECV`
- [x] Cross-tile message routing
- [x] `CAS.TAGGED`, `FAA` on shared memory
- [x] `FENCE.GC`
- [x] Test: producer on tile 0 sends fixnums, consumer on tile 1 sums them

### Phase 6: Assembler ✅
**Goal:** Stop hand-encoding binaries. Write assembly, get binaries.
**Status:** Complete. 14 tests. Committed (f518126).

- [x] Text assembler: reads LM-1 mnemonics, outputs 32-bit words
- [x] Label resolution (forward/backward references)
- [x] Directives: `.word`, `.u32`, `.byte`, `.align`, `.equ`, `.template`, `.org`, `.space`
- [x] Pseudo-instructions: `MOV`, `LIA`
- [x] Two-pass assembly

### Phase 7: BIOS ✅
**Goal:** BIOS boots on the emulator and hands off to an OS image.
**Status:** Complete. Committed.

- [x] BIOS in LM-1 assembly (via Phase 6 assembler)
- [x] `assemble_bios()`, `make_os_image()` helpers
- [x] Block device emulation: `TRAP #0x82`
- [x] Boot info block construction
- [x] Test: BIOS boots, prints banner, loads OS image, jumps to entry

### Phase 8: Cross-Compiler & Lispos Kernel ✅
**Goal:** Boot to a working REPL.
**Status:** Complete. Committed (f063a8e).

- [x] Cross-compiler: Lisp forms → LM-1 assembly
- [x] S-expression parser (`parse()`)
- [x] Compile: `defun`, `lambda`, `let`, `if`, `cond`, `quote`, `and`, `or`, `set!`
- [x] Compile: `+`, `-`, `*`, `/`, `cons`, `car`, `cdr`, `eq`, `=`, `<`, `>`
- [x] Compile: `null`, `not`, `atom`, `fixnump`, `consp`, `set-car!`, `set-cdr!`
- [x] Calling convention: r1-r8 args, r1 return, r16-r24 callee-saved
- [x] Runtime helpers: `putchar`, `getchar`, `print-fixnum`, `print`, `newline`
- [x] Reader (in LM-1 asm): parse S-expressions from console
- [x] Evaluator (in LM-1 asm): dispatch on operator codes
- [x] REPL loop: read, eval, print
- [x] Test: `(+ 1 2)` → `3`, `(fact 10)` → `3628800`

### Phase 9: VDI Display Engine ✅
**Goal:** Framebuffer output visible on the host. Blit and text primitives work.
**Status:** Complete. 23 tests. Committed.

- [x] VDI class: 8-bit indexed-color framebuffer with 256-entry CLUT
- [x] Pygame host display (headless mode for testing)
- [x] Drawing primitives: rect fill, bitblt, line (Bresenham), scroll
- [x] 8×16 CP437-style bitmap font (128 ASCII glyphs)
- [x] Hardware cursor overlay (XOR pattern)
- [x] Host keyboard/mouse → event queue
- [x] VDI function codes 0-12 (SET_MODE through READ_EVENT)
- [x] Screenshot export via PIL (`to_pil_image()`)
- [x] Test: fill, draw rects, render text, animate cursor

### Phase 10: Crystal Desktop — Window Manager ✅
**Goal:** Overlapping windows with move/resize/raise/lower, global menu bar, event dispatch.
**Status:** Complete. 24 tests. Committed (b9e57c3). Visual bug fix committed (9a414f2).

- [x] AES: window create/close/raise/lower, z-order list
- [x] Window dataclass with flags (closeable, moveable, resizable)
- [x] Event dispatch: mouse down/up/move, key down → focused window
- [x] Click-to-focus, drag-to-move, grip-to-resize
- [x] Global menu bar (GEM-style: system + active app menus)
- [x] Dropdown menus with highlight, separators, callbacks
- [x] Window decorations: title bar, close button (X), resize grip
- [x] Desktop background (teal-blue + crosshatch pattern)
- [x] Built-in crystallites: Terminal (with Lisp REPL), Calculator, Clock
- [x] Visual test driver (`desktop_test_driver.py`): headless screenshots + synthetic input
- [x] Font bug fix: proper 16-row glyphs with `_g()`/`_gd()` padding
- [x] Test: open 3 overlapping windows, move/resize/raise, menu bar responds

### Phase 11: Crystallites, File Manager, Theming ✅
**Goal:** Desk accessories, spatial file manager, resource system, scrap, themes.
**Status:** Complete. All crystallites, scrap clipboard, resource system, file manager, desktop profile, and control panel implemented. 31 tests passing.

- [x] Crystallite framework: system-registered micro-apps, always available
- [x] Standard crystallites: clock, calculator, terminal (REPL-in-a-window)
- [x] Inspector crystallite: window stats, z-order, focused window details, pixel probe, scrap info
- [x] Scrap (clipboard): typed (text/lisp), structured, history ring (max 16), type negotiation
- [x] Resource system: menus/dialogs/alerts as editable Lisp data, load/save, menu builder
- [x] Spatial file manager: folder=window, directory listing, dirs-first, selection, navigation
- [x] Desktop profile: serialize/deserialize desktop state as Lisp form, file I/O
- [x] Theming: Colors class with full semantic color scheme, live-switchable
- [x] Control Panel crystallite: color swatches, system info, toggleable sections
- [x] Test: full desktop session — all crystallites redraw, scrap workflow, profile roundtrip

### Phase 12: Visual Modernization ✅
**Goal:** Crystal Desktop looks like a modern spiritual successor to GEM, not an 80s replica.
**Status:** Complete. Retro-futuristic dark theme, font rendering fixes, performance fix.

- [x] Modern palette system: dark retro-futuristic color scheme (navy/blue/cyan accents)
- [x] VDI gradient primitives: horizontal gradient fill, shadow rect, fill_circle, rounded_rect
- [x] Window chrome: gradient title bars, drop shadows, red close button, refined borders
- [x] Menu bar: gradient background, modern dropdown with proper shadow
- [x] Calculator/clock: modern widget styling (dark background, gradient buttons)
- [x] Desktop background: smooth vertical gradient
- [x] Font rendering: fixed advance-width measurement, glyph positioning, anti-aliased NotoSansMono
- [x] VDI present() performance: bulk frombuffer transfer (was 307K set_at calls)
- [x] Title text drop shadows, dot-pattern resize grips
- [x] Screenshot verification with DesktopDriver

### Phase 13: OS Foundation — Widget Toolkit, VFS, Icons
**Goal:** The Python desktop stops being a toy. Real widget toolkit, real virtual filesystem, real icons. Everything after this builds on these foundations.
**Status:** Not started.

**Widget Toolkit (`toolkit.py`)**
- [ ] `Widget` base: position, size, parent/children, focus, event dispatch, hit-testing
- [ ] `Label`: text display, alignment, color
- [ ] `Button`: gradient background, hover/press states, icon+text, on_click callback
- [ ] `IconButton`: compact icon-only button for toolbars
- [ ] `TextField`: single-line text input, cursor, selection, clipboard integration
- [ ] `TextArea`: multi-line editor, line numbers, syntax highlighting hooks, undo/redo, scrolling
- [ ] `ScrollBar`: vertical/horizontal, thumb drag, track click, page up/down
- [ ] `ListView`: scrollable item list, icons+text, selection, double-click activate, alternating row colors
- [ ] `IconView`: grid of icons with labels, selection, double-click, free-form or grid layout
- [ ] `TreeView`: expandable hierarchy, expand/collapse arrows, indentation
- [ ] `CheckBox`: toggle with label
- [ ] `Panel`: container with background, border, padding, layout (vertical/horizontal)
- [ ] `Toolbar`: horizontal icon button strip with separators
- [ ] `Separator`: visual divider line
- [ ] `ContextMenu`: right-click popup menu, keyboard shortcuts display, nested submenus
- [ ] `ProgressBar`: determinate/indeterminate progress
- [ ] `Slider`: value slider with range
- [ ] `WidgetHost`: bridges AES Window callbacks to widget tree, manages focus, double-click detection

**Virtual Filesystem (`vfs.py`)**
- [ ] `VFSNode` base with name, metadata (created, modified, size, permissions, MIME type)
- [ ] `VFSFile`: in-memory content (bytes), read/write
- [ ] `VFSDirectory`: ordered children dict
- [ ] `VFS`: resolve paths, mkdir, create/read/write/delete, list, stat, copy, move, walk
- [ ] Default filesystem structure: `/system/`, `/applications/`, `/users/default/`, `/tmp/`
- [ ] Pre-populated content: sample .lisp files, .txt files, app markers, system config
- [ ] Mount points for bridging host filesystem (optional)

**Icon System (`icons.py`)**
- [ ] `Icon` class: 16×16 pixel art with palette, draw at 1x/2x scale
- [ ] Stock icons (16+): folder, folder-open, file, file-text, file-code, file-image, application, terminal, calculator, clock, editor, settings, inspector, trash, disk, home
- [ ] File-type → icon mapping by extension and MIME type
- [ ] Icon caching for fast repeated rendering

**Window Manager Upgrades**
- [ ] Minimize button (−) and maximize button (□) in title bar
- [ ] Window minimize → taskbar button, maximize → fill screen minus taskbar
- [ ] Double-click title bar → maximize/restore toggle
- [ ] Taskbar: gradient panel at bottom, Crystal button (left), window buttons (center), clock (right)
- [ ] Desktop icons: clickable icon+label on the desktop background (Applications, Documents, Trash)
- [ ] Right-click context menus on desktop, windows, and widgets
- [ ] Double-click detection (400ms window, 5px tolerance)
- [ ] Resolution upgrade: 1024×768 default

- [ ] Test: widget toolkit unit tests, VFS CRUD tests, icon rendering tests, WM feature tests

### Phase 14: Desktop Applications — Real Software
**Goal:** Applications that are actually useful, not printf dumps in rectangles.
**Status:** Not started. Depends on Phase 13 toolkit and VFS.

- [ ] **Text Editor** crystallite: TextArea widget, line numbers, Lisp syntax highlighting (paren matching, keyword/string/comment coloring), open/save from VFS, undo/redo, find/replace, status bar (line:col, filename)
- [ ] **File Manager** rewrite: toolbar (Back/Up/View toggle), IconView for icon mode, ListView for detail mode, breadcrumb path bar, VFS navigation, file properties dialog, context menu (Open/Copy/Cut/Paste/Delete/Rename/New Folder), status bar (item count, selection), drag-and-drop between windows
- [ ] **Terminal** rewrite: proper scrollback buffer (1000 lines), command history (up/down), tab completion for commands and paths, ANSI-inspired color codes, resizable
- [ ] **System Monitor**: process/thread view, memory usage (nursery/old-gen), VFS disk usage, live-updating graphs
- [ ] **Image Viewer**: display VFS images, zoom/pan, fit-to-window
- [ ] **Calculator** polish: expression display, scientific mode toggle, history, keyboard shortcuts
- [ ] **Inspector** rewrite: TreeView of window hierarchy, property panel for selected widget/window, click-to-inspect mode (click any window element, inspector selects it)
- [ ] **Control Panel** rewrite: real settings with CheckBox/Slider/ListView widgets, theme picker, display settings, about system
- [ ] Test: each application tested for creation, rendering, and core interaction

### Phase 15: Compiler Extensions
**Goal:** Cross-compiler powerful enough to write the desktop natively.
**Status:** Not started. Compiler already has: `defun`, `lambda`, `let`, `if`, `cond`, `and`, `or`, `set!`, `quote`, arithmetic, cons ops, comparisons.

- [ ] `while` / `loop` (iteration without recursion)
- [ ] `>=`, `<=` comparison operators
- [ ] `begin` / `progn` (already exists — verify and test)
- [ ] String literals: stored in memory, address-based access
- [ ] Vector/array operations: `make-vector`, `vector-ref`, `vector-set!`, `vector-length`
- [ ] `do` / named `let` for iteration
- [ ] Character operations: `char->fixnum`, `fixnum->char`
- [ ] VDI trap wrappers: `(vdi-fill-rect x y w h color)` etc.
- [ ] Test: compile and run programs using all new forms

### Phase 16: Native Desktop Skeleton
**Goal:** Event loop and basic window management running as compiled Lisp on the LM-1.
**Status:** Not started.

- [ ] VDI system calls from Lisp (via TRAP 0x83 wrappers)
- [ ] Event loop in compiled Lisp
- [ ] Window data structures as vectors
- [ ] Window create/draw/move in Lisp
- [ ] Basic menu bar in Lisp
- [ ] Boots via BIOS, runs as OS image
- [ ] Test: native desktop boots, draws a window, handles click

### Phase 17: Live Development Environment
**Goal:** Edit code in a crystallite, eval it, and the running system changes — while you watch.
**Status:** Not started.

- [ ] Lisp syntax highlighting in the editor (paren matching, keyword coloring)
- [ ] Eval-region: select code in editor, eval it, result appears in a REPL pane
- [ ] Hot-patch: redefine a function and IC entries invalidate — next call picks up new definition
- [ ] Condition/restart debugger crystallite: error pops a window showing the stack, bindings, restarts — pick a restart, execution resumes
- [ ] **Killer demo:** open editor, modify `_draw_window` (or the native equivalent), eval, title bars change appearance in real time while the desktop is running
- [ ] Test: redefine a function, call it, verify new behavior; trigger an error, verify debugger crystallite opens

### Phase 18: Object System (CLOS on Hardware)
**Goal:** Full object system where `defclass` maps to shapes and `defgeneric`/`defmethod` dispatch through the IC.
**Status:** Not started. Phase 4 (IC + shapes) provides the hardware foundation.

- [ ] `defclass`: creates a shape, allocates slot layout, inheritance chain
- [ ] `make-instance`: ALLOCV with shape header, slot initialization
- [ ] Slot access: `slot-value` / `(with-slots ...)` compiled to indexed LD/ST
- [ ] `defgeneric` / `defmethod`: method table keyed by shape, installed into IC
- [ ] Multiple dispatch: discriminate on 1st arg shape (single dispatch first, extend later)
- [ ] Method combination: `:before`, `:after`, `:around` wrappers
- [ ] `change-class`: reshape an object live (update shape, migrate slots)
- [ ] Test: define a class hierarchy, dispatch methods, verify IC hits, change-class

### Phase 19: Condition System
**Goal:** Restartable errors — signal a condition, handle it without unwinding, pick a restart.
**Status:** Not started.

- [ ] `define-condition`: condition types as classes (builds on Phase 18)
- [ ] `signal` / `error` / `warn`: walk handler chain, find matching handler
- [ ] `handler-bind`: establish handlers (non-unwinding — stack stays live)
- [ ] `handler-case`: establish handlers (unwinding — like try/catch)
- [ ] `restart-bind` / `invoke-restart`: offer recovery strategies
- [ ] Standard restarts: `abort`, `continue`, `use-value`, `store-value`
- [ ] Integration with debugger crystallite (Phase 17): interactive restart selection
- [ ] Test: signal a condition, handle it, invoke a restart, verify execution resumes correctly

### Phase 20: Actor Runtime & Parallel Primitives
**Goal:** Erlang-style lightweight processes on tiles. Parallel map/reduce over cons structures.
**Status:** Not started. Phase 5 (SEND/RECV/multi-tile) provides the hardware foundation.

- [ ] Per-tile process scheduler: run queue of lightweight processes, preemptive yield on GC or message
- [ ] `spawn`: create a process on a tile, returns a process ID (tagged value)
- [ ] `send` / `receive` (Lisp-level): pattern-matching message receive with timeout
- [ ] Process linking and monitors: "if that process dies, notify me"
- [ ] Supervision trees: restart strategies (one-for-one, one-for-all)
- [ ] `pmap`: parallel map — distribute list elements across tiles, collect results via messages
- [ ] `pfold`: parallel reduce with associative combiner
- [ ] `future` / `promise`: spawn computation, block on result
- [ ] Load balancing: work-stealing or round-robin tile assignment
- [ ] Test: spawn 100 processes across 4 tiles, pmap a function, verify results

### Phase 21: Parallel Lisp Compiler (Self-Hosting)
**Goal:** The cross-compiler rewritten in Lisp, running on the LM-1, compiling itself.
**Status:** Not started. Depends on Phases 15 (compiler extensions), 18 (object system), 20 (actors).

- [ ] Port the parser (`parse`) to native Lisp
- [ ] Port expression compiler to native Lisp (emit assembly text or direct code gen)
- [ ] Assembler in Lisp (or direct machine code emission)
- [ ] Parallel compilation: each `defun` compiles on a separate tile via `spawn`
- [ ] Bootstrap test: compiler compiles itself, output matches
- [ ] Benchmark: compile a large program, measure speedup from N tiles vs 1

### Phase 22: Application Showcase
**Goal:** Real applications that demonstrate what a modern Lisp machine with 64 tiles can do.
**Status:** Not started. Pick from the menu below based on interest.

- [ ] **Symbolic algebra system** — differentiation, simplification, polynomial arithmetic over cons-cell expression trees. Parallel simplification across tiles.
- [ ] **Parallel ray tracer** — tiles own screen regions, trace independently, shared scene graph in old-gen. Real-time preview in a crystallite window.
- [ ] **Actor-based chat system** — actors on different tiles (or eventually different machines) sending messages. Chat UI as a crystallite.
- [ ] **Genetic programming engine** — evolve Lisp programs. Each tile runs a population. Fitness = native execution. Migration via SEND. The machine breeds programs.
- [ ] **Lisp-scripted game** — tile for physics, tile for AI, tile for rendering, message-passing between them. Game logic is hot-editable via the live IDE (Phase 17).
- [ ] **Generative art** — parallel L-systems / fractal computation, results stream into VDI framebuffer, interactive parameter tweaking from REPL.
- [ ] **Music synthesizer** — tiles as oscillators/filters, message-passing as signal routing, patch definitions as Lisp data, waveform visualizer crystallite.
- [ ] **Distributed key-value store** — each tile owns a partition, queries fan out as messages, transactions via CAS.TAGGED.

---

## Test Summary

| Phase | Tests |
|-------|-------|
| 1     | 26    |
| 2     | 20    |
| 3     | 16    |
| 4     | 8     |
| 5     | 14    |
| 6     | 14    |
| 7     | —     |
| 8     | —     |
| 9     | 23    |
| 10    | 24    |
| 11    | 31    |
| 12    | —     |
| **Total** | **209** (all passing) |

---

## Architecture Notes

- **Python 3.12.3** with venv (no C++ acceleration yet)
- **LM-1 ISA:** 64-bit tagged words, 32-bit fixed-width instructions, 6-bit opcode, 5 encoding formats
- **VDI:** 1024×768 default, 32-bit RGBA truecolor, pygame display (headless for tests)
- **Widget Toolkit:** `toolkit.py` — full widget set rendering through VDI, WidgetHost bridges to AES windows
- **VFS:** `vfs.py` — in-memory virtual filesystem, no host filesystem access from the OS
- **Icons:** `icons.py` — 16×16 pixel-art stock icons for files/folders/apps, 1x/2x rendering
- **Cross-compiler:** Lisp → LM-1 assembly → binary via two-pass assembler
- **Desktop:** Host-side Python (AES + VDI + Toolkit). Native port planned for Phase 16.
