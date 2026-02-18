# LM-1 Build Plan

**Date:** 2026-02-16  
**Updated:** 2026-02-17

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

### Phase 10: Crystal Desktop — Window Manager ✅ (Legacy)
**Goal:** Overlapping windows with move/resize/raise/lower, global menu bar, event dispatch.
**Status:** Complete. 24 tests. Committed (b9e57c3). Superseded by Crystal v3 (see below) but code preserved in `desktop.py` for backward compatibility.

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

### Phase 13: OS Foundation — Widget Toolkit, VFS, Icons ✅
**Goal:** The Python desktop stops being a toy. Real widget toolkit, real virtual filesystem, real icons. Everything after this builds on these foundations.
**Status:** Complete. 258 tests (49 new). Committed (6b0cf27).

**Widget Toolkit (`toolkit.py`)**
- [x] `Widget` base: position, size, parent/children, focus, event dispatch, hit-testing
- [x] `Label`: text display, alignment, color
- [x] `Button`: gradient background, hover/press states, icon+text, on_click callback
- [x] `IconButton`: compact icon-only button for toolbars
- [x] `TextField`: single-line text input, cursor, selection, clipboard integration
- [x] `TextArea`: multi-line editor, line numbers, syntax highlighting hooks, undo/redo, scrolling
- [x] `ScrollBar`: vertical/horizontal, thumb drag, track click, page up/down
- [x] `ListView`: scrollable item list, icons+text, selection, double-click activate, alternating row colors
- [x] `IconView`: grid of icons with labels, selection, double-click, free-form or grid layout
- [x] `CheckBox`: toggle with label
- [x] `Panel`: container with background, border, padding, layout (vertical/horizontal)
- [x] `Toolbar`: horizontal icon button strip with separators
- [x] `Separator`: visual divider line
- [x] `ContextMenu`: right-click popup menu, keyboard shortcuts display, nested submenus
- [x] `ProgressBar`: determinate/indeterminate progress
- [x] `WidgetHost`: bridges AES Window callbacks to widget tree, manages focus, double-click detection

**Virtual Filesystem (`vfs.py`)**
- [x] `VFSNode` base with name, metadata (created, modified, size, permissions, MIME type)
- [x] `VFSFile`: in-memory content (bytes), read/write
- [x] `VFSDirectory`: ordered children dict
- [x] `VFS`: resolve paths, mkdir, create/read/write/delete, list, stat, copy, move, walk
- [x] Default filesystem structure: `/system/`, `/applications/`, `/users/default/`, `/tmp/`
- [x] Pre-populated content: sample .lisp files, .txt files, app markers, system config

**Icon System (`icons.py`)**
- [x] `Icon` class: 16×16 pixel art with palette, draw at 1x/2x scale
- [x] 21 stock icons: folder, folder-open, file, file-text, file-code, file-image, app, terminal, calculator, clock, editor, settings, inspector, trash, disk, home, file_manager, new_folder, refresh, arrow_up, arrow_back
- [x] File-type → icon mapping by extension and MIME type
- [x] Icon caching for fast repeated rendering

**Desktop Rewrite (`desktop.py`)**
- [x] Minimize button (−) and maximize button (□) in title bar
- [x] Window minimize → taskbar button, maximize → fill screen minus taskbar
- [x] Taskbar: gradient panel, Crystal button (left), window buttons (center), clock (right)
- [x] Desktop icons: clickable icon+label grid (double-click to launch)
- [x] Double-click detection (400ms window, 5px tolerance)
- [x] Resolution upgrade: 1024×768 default
- [x] Tick object registry: crystallites with tick() properly cleaned up on window close
- [x] TextEditorCrystallite: line numbers, Lisp syntax highlighting, undo/redo, VFS save
- [x] FileManagerCrystallite: icon/list views, toolbar, VFS navigation
- [x] Terminal: `ls`, `cat`, `pwd`, `cd` commands on VFS
- [x] ControlPanel: VFS statistics display

### Crystal v3 — Expression-Tree Desktop ✅
**Goal:** Replace the AES window manager (Phases 10-12) with a fundamentally Lisp-native desktop. The screen becomes a single nested expression tree — every pixel traces back to a node you can inspect, edit, and connect. No window chrome, no floating windows. Portals, panes, lenses, facets.
**Status:** Complete. 88 tests. `crystal.py` is the primary desktop; `desktop.py` preserved for backward compatibility.

**Expression Tree Compositor (`crystal.py`)**
- [x] `Crystal`: root of the expression tree — the entire desktop as one evaluable structure
- [x] `Portal`: a live lens onto any Lisp object (target + lens + label + state)
- [x] `Pane`: recursive spatial partitioning — vertical/horizontal splits, tab groups, floats
- [x] Focus model: `_cycle_focus()` walks the tree, single focused portal receives keys
- [x] Layout engine: recursive rect computation from tree structure
- [x] Compositor: walk tree → render portals through lenses → draw dividers → draw bar

**Built-in Lenses (8)**
- [x] `InspectLens`: generic slot-by-slot object viewer (dict, list, int, str, dataclass)
- [x] `PrettyLens`: s-expression pretty-printer with syntax coloring, scroll
- [x] `TerminalLens`: interactive REPL — Lisp eval, VFS commands (ls/cd/cat/pwd), help
- [x] `EditorLens`: full text editor — syntax highlighting, line numbers, undo/redo, cursor, save
- [x] `TreeLens`: expand/collapse tree for VFS directories and nested data
- [x] `StreamLens`: append-only log view, auto-scroll
- [x] `TimeLens`: clock display (time + date)
- [x] `InteractiveLens`: calculator with button grid and display

**Crystal Bar**
- [x] Bottom dock bar with launcher items (Terminal, Files, Editor, Calc, Inspector)
- [x] Active portal indicators with focus highlight
- [x] Clock display (right-aligned)
- [x] Bar item hover state, click → action callback

**Scrapbook**
- [x] Typed clipboard with history (max 50)
- [x] `snip()` / `paste()` with scrap_type filtering
- [x] Source tracking and timestamps

**Tree Manipulation**
- [x] `set_root_portal()`: single portal as root
- [x] `split_portal()`: split any portal into two (vertical or horizontal)
- [x] `add_tab()`: convert portal to tabbed pane
- [x] `close_portal()`: remove portal, collapse tree if single child remains
- [x] Ctrl-Tab: cycle focus, Ctrl-W: close, Ctrl-\\: split-v, Ctrl-/: split-h

**Visual Design**
- [x] Deep charcoal canvas (0x0D0D14) — near-black indigo
- [x] Semantic type coloring: functions=cyan, data=amber, actors=green, errors=red
- [x] Focus glow (teal 0x00B4D8) — 2px ring around focused portal
- [x] Subtle 1px portal edges, thin pane dividers
- [x] No window chrome — content fills space, labels are tiny

**Lens Registry**
- [x] `register_lens()` / `get_lens()` — extensible lens system
- [x] Fallback to InspectLens for unknown lens names

**Default Layout**
- [x] Vertical split 0.25: Files tree | Horizontal split 0.65 (Terminal | System Inspector)
- [x] Focus starts on Terminal, bar has 5 launcher items
- [x] `launch_crystal()` replaces `launch_desktop()` (alias preserved)


### Phase 14: Compiler Extensions — Full Cross-Compiler
**Goal:** Cross-compiler powerful enough to write the desktop natively on the LM-1. Currently the compiler handles only first-order Lisp with direct calls (no closures, no strings, no vectors, no iteration). This phase fills every gap.
**Status:** Not started.

**Current compiler state (Phase 8):** `defun`, `lambda` (no capture), `let`, `if`, `cond`, `and`, `or`, `set!`, `quote`, `progn`/`begin`, arithmetic (+−×÷), cons/car/cdr, eq/</>, null/not/atom/fixnump/consp, set-car!/set-cdr!. Calling convention: r1-r8 args, r1 return, r16-r24 callee-saved. CALL.DIRECT only.

**Iteration & Control Flow**
- [ ] `while` form: `(while test body...)` → label, BR.NIL test, body, BR back
- [ ] `do` / named `let` for counted iteration
- [ ] `>=`, `<=` comparison operators (CMP + BR.FIX.GT/EQ combo)
- [ ] `return` / `block` / `tagbody` for non-local exits
- [ ] Tail-call optimization: detect tail position, emit TAILCALL.DIRECT instead of CALL.DIRECT

**Closures & Higher-Order Functions**
- [ ] Closure capture: `lambda` with free variables → ALLOC.CLOSURE + environment vector
- [ ] CALL.CLOSURE code generation for indirect calls
- [ ] `funcall` / `apply`: call through a closure or function reference
- [ ] `letrec` / `let*` for sequential and recursive local bindings
- [ ] `map`, `filter`, `reduce` as compiled higher-order functions

**Data Types**
- [ ] String literals: data section allocation, null-terminated or length-prefixed byte arrays
- [ ] `string-ref`, `string-set!`, `string-length`, `string-append`, `substring`
- [ ] `make-vector`, `vector-ref`, `vector-set!`, `vector-length` → ALLOCV + indexed LD/ST
- [ ] `defstruct`: named record types → shape creation + ALLOC with field accessors
- [ ] Character operations: `char->fixnum`, `fixnum->char`, char literals `#\a`
- [ ] Global variable storage: `defvar` emits `.WORD` in data section, STR/LDR access

**System Interface**
- [ ] VDI trap wrappers: `(vdi-fill-rect x y w h color)`, `(vdi-draw-string x y str fg bg)`, etc. → TRAP 0x83 with function codes
- [ ] Block I/O wrappers: `(block-read n addr)`, `(block-write n addr)` → TRAP 0x82
- [ ] Event reading: `(vdi-read-event)` → returns event type + data
- [ ] Memory operations: `(peek addr)`, `(poke addr val)` for raw LDR/STR

**Compiler Infrastructure**
- [ ] Register spilling: when >9 live variables, spill to stack frames
- [ ] Constant folding: evaluate `(+ 1 2)` → `3` at compile time
- [ ] Dead code elimination: unreachable branches after `if` with constant test
- [ ] Macro system: `defmacro` with compile-time expansion (at minimum `when`, `unless`, `dotimes`, `dolist`)

- [ ] Test: compile and run programs using closures, strings, vectors, structs, VDI calls, iteration

### Phase 15: C Acceleration — Making the Emulator Fast
**Goal:** The C acceleration module (`_accel_ext.c`) currently covers only Phase 1 scalar ops and is **not connected** to the executor. This phase makes the emulator actually fast by accelerating the Lisp hot path.
**Status:** Not started. The `_accel_ext.c` exists but handles 0% of tagged operations (ARITH_FIX, ALLOC, LD/ST.CAR/CDR, CALL, TST — all pure Python).

**Integration**
- [ ] Wire `_accel.step_n()` into `execute.py` as the primary inner loop
- [ ] Fallback to Python for unhandled opcodes (trap_code=0xFE returns to Python dispatcher)
- [ ] Build system: `make build` compiles and links, auto-detect on import

**Tagged Operations in C**
- [ ] ARITH_FIX: ADD.FIX, SUB.FIX, MUL.FIX, DIV.FIX with overflow/type traps
- [ ] ADD_FIX_IMM: tagged add-immediate
- [ ] CMP_TAGGED: CMP + EQ with tag-aware comparison
- [ ] TST / TST_SHAPE: all type tests (fixnum, ref, cons, special, shape)

**Memory & Allocation in C**
- [ ] ALLOC / ALLOC_CONS / ALLOCV / ALLOC_CLOSURE: bump-pointer nursery allocation with overflow trap
- [ ] LD (field load), LD_CAR_CDR: tagged field access with type checks
- [ ] ST, ST_WB, ST_CAR_CDR: field store + card-table write barrier
- [ ] LI32: two-word immediate load

**Dispatch in C**
- [ ] CALL_DIRECT, RET: frame push/pop in C
- [ ] CALL_CLOSURE: closure environment setup
- [ ] TAILCALL_DIRECT, TAILCALL_IC: tail call without frame growth
- [ ] CALL_IC, IC_INSTALL: inline cache lookup/install
- [ ] PUSH_MULTI, POP_MULTI: bulk register save/restore

**VDI Acceleration**
- [ ] Fill rect: bulk pixel writes instead of Python loops
- [ ] Blit/scroll: memcpy-based buffer operations
- [ ] Font rendering: glyph blitting in C
- [ ] `present()` to pygame: direct buffer copy (partially done with `frombuffer`)

- [ ] Benchmark: time `(fact 20)` in pure Python vs C-accelerated (target: 10x+ speedup)
- [ ] Test: all existing tests pass identically with C acceleration enabled

### Phase 16: Native OS Kernel — LispOS on LM-1
**Goal:** The operating system boots and runs as compiled Lisp on the LM-1 hardware. Event loop, window management, and basic I/O all execute natively — not in Python. The Python desktop becomes a reference implementation and development tool.
**Status:** Not started. Depends on Phase 14 (compiler extensions) and Phase 15 (C acceleration for viable speed).

**Boot Sequence**
- [ ] BIOS loads OS image (existing Phase 7 infrastructure)
- [ ] Kernel init: set up nursery, trap table, stack, GC handler
- [ ] VDI init: `(vdi-set-mode 1024 768)` via TRAP 0x83
- [ ] Font loading: glyph data embedded in OS image data section

**Core OS Services (compiled Lisp)**
- [ ] Event loop: `(loop (let ((evt (vdi-read-event))) (dispatch evt)))`
- [ ] Window data structures: vectors with fields for x, y, w, h, flags, title, draw-fn, click-fn
- [ ] Window manager: create, close, raise, lower, z-order list
- [ ] Mouse event dispatch: hit-test windows, route to handlers
- [ ] Keyboard event dispatch: route to focused window
- [ ] Basic drawing: title bars, borders, close button, background fill

**Minimal Shell**
- [ ] Terminal crystallite in native Lisp: character input, scrolling text output
- [ ] Read-eval-print over the native Lisp evaluator
- [ ] VDI-based text rendering (draw-string trap)

**Bridge Mode**
- [ ] Python desktop can launch native OS image in a subprocess
- [ ] Screenshot comparison: Python reference vs native renders same output
- [ ] Shared VDI framebuffer for hybrid operation (Python host + native compute)

- [ ] Test: native OS boots, draws a window with title bar, handles click-to-close, REPL evaluates `(+ 1 2)`

### Phase 17: Native Filesystem — Crystal FS
**Goal:** Persistent block-based filesystem for the LM-1, replacing the in-memory Python VFS with something that survives across boots. Designed for a Lisp machine — not a Unix clone.
**Status:** Not started.

**Design Decision: Custom Log-Structured FS ("Crystal FS")**
Why not Btrfs: Btrfs is ~400K lines of C kernel code. It's a production Linux filesystem with features (RAID, subvolumes, checksumming, scrubbing, send/receive) that make no sense for an emulated single-machine with in-memory block device. Implementing even 10% of Btrfs would dwarf the rest of this project.

Why custom: A Lisp machine should have a Lisp-native filesystem. Crystal FS is:
- **Log-structured**: append-only writes, no in-place overwrites, natural COW semantics
- **S-expression metadata**: directory entries and inodes stored as serialized Lisp data
- **Content-addressed blocks**: SHA-256 hashed, deduplication for free
- **Simple to implement**: ~1000 lines of Lisp, ~500 lines of block-device support
- **Snapshot-friendly**: point-in-time snapshots by pinning the root log entry

**Block Device Layer**
- [ ] Block device abstraction: 4KB blocks, read/write via TRAP 0x82
- [ ] In-memory block cache (LRU, 256 blocks)
- [ ] Host-backed block device: Python side stores blocks in a flat file
- [ ] Block allocation bitmap

**Filesystem Structure**
- [ ] Superblock: magic, version, root inode, block count, free count
- [ ] Inode: type (file/dir/symlink), size, block list, timestamps, permissions
- [ ] Directory entries: sorted list of (name, inode-number) pairs
- [ ] File content: extent-based block ranges (contiguous allocation preferred)
- [ ] Free space tracking: bitmap or free-list

**Operations**
- [ ] `fs-open`, `fs-close`, `fs-read`, `fs-write`, `fs-seek`
- [ ] `fs-mkdir`, `fs-rmdir`, `fs-unlink`, `fs-rename`
- [ ] `fs-stat`, `fs-readdir`
- [ ] `fs-mount` / `fs-unmount` (single filesystem initially)
- [ ] Write-ahead log: crash recovery by replaying uncommitted log entries

**Integration**
- [ ] VFS adapter: Crystal FS backing the same VFS API the Python desktop uses
- [ ] `mkfs` tool: format a block device with Crystal FS
- [ ] `fsck` tool: verify and repair filesystem consistency
- [ ] Pre-populate: port `populate_default()` content into Crystal FS image

- [ ] Test: format, mount, create/read/write/delete files, unmount, remount, verify persistence

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
- [ ] Integration with debugger crystallite: interactive restart selection
- [ ] Test: signal a condition, handle it, invoke a restart, verify execution resumes correctly

### Phase 20: Live Development Environment
**Goal:** Edit code in a crystallite, eval it, and the running system changes — while you watch.
**Status:** Not started.

- [ ] Lisp syntax highlighting in the editor (paren matching, keyword coloring)
- [ ] Eval-region: select code in editor, eval it, result appears in a REPL pane
- [ ] Hot-patch: redefine a function and IC entries invalidate — next call picks up new definition
- [ ] Condition/restart debugger crystallite: error pops a window showing the stack, bindings, restarts — pick a restart, execution resumes
- [ ] **Killer demo:** open editor, modify `_draw_window` (or the native equivalent), eval, title bars change appearance in real time while the desktop is running
- [ ] Test: redefine a function, call it, verify new behavior; trigger an error, verify debugger crystallite opens

### Phase 21: Actor Runtime & Parallel Primitives
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

### Phase 22: Parallel Lisp Compiler (Self-Hosting)
**Goal:** The cross-compiler rewritten in Lisp, running on the LM-1, compiling itself.
**Status:** Not started. Depends on Phases 14 (compiler extensions), 18 (object system), 21 (actors).

- [ ] Port the parser (`parse`) to native Lisp
- [ ] Port expression compiler to native Lisp (emit assembly text or direct code gen)
- [ ] Assembler in Lisp (or direct machine code emission)
- [ ] Parallel compilation: each `defun` compiles on a separate tile via `spawn`
- [ ] Bootstrap test: compiler compiles itself, output matches
- [ ] Benchmark: compile a large program, measure speedup from N tiles vs 1

### Phase 23: Application Showcase
**Goal:** Real applications that demonstrate what a modern Lisp machine with 64 tiles can do.
**Status:** Not started. Pick from the menu below based on interest.

- [ ] **Symbolic algebra system** — differentiation, simplification, polynomial arithmetic over cons-cell expression trees. Parallel simplification across tiles.
- [ ] **Parallel ray tracer** — tiles own screen regions, trace independently, shared scene graph in old-gen. Real-time preview in a crystallite window.
- [ ] **Actor-based chat system** — actors on different tiles (or eventually different machines) sending messages. Chat UI as a crystallite.
- [ ] **Genetic programming engine** — evolve Lisp programs. Each tile runs a population. Fitness = native execution. Migration via SEND. The machine breeds programs.
- [ ] **Lisp-scripted game** — tile for physics, tile for AI, tile for rendering, message-passing between them. Game logic is hot-editable via the live IDE (Phase 20).
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
| 7     | 4     |
| 8     | 15    |
| 9     | 23    |
| 10    | 24    |
| 11    | 31    |
| 12    | —     |
| 13    | 49    |
| Crystal v3 | 88 |
| **Total** | **346** (all passing) |

---

## Architecture Notes

- **Python 3.12.3** with venv
- **LM-1 ISA:** 64-bit tagged words, 32-bit fixed-width instructions, 6-bit opcode, 44 opcodes, 5 encoding formats
- **C Acceleration:** `_accel_ext.c` exists but covers only Phase 1 scalar ops and is **not wired into** the executor. Tagged operations (the entire Lisp hot path) run in pure Python. Phase 15 addresses this.
- **Two Parallel Worlds:** The machine emulator (phases 1-8: execute.py, core.py, memory.py, compiler.py) and the desktop framework (phases 9-13+Crystal: crystal.py, vdi.py, vfs.py, toolkit.py, icons.py) are **completely disconnected**. The desktop runs as native Python objects, not as compiled Lisp on the emulator. Phase 14 (compiler extensions) and Phase 16 (native OS) bridge this gap.
- **VDI:** 1024×768 default, 32-bit RGBA truecolor, pygame display (headless for tests). TRAP 0x83 interface defined but unused — desktop calls VDI methods directly.
- **Cross-compiler:** Lisp → LM-1 assembly → binary. Currently first-order only (no closures, no strings, no vectors, no iteration beyond recursion). Phase 14 fills these gaps.
- **VFS:** `vfs.py` — in-memory virtual filesystem. Crystal FS (Phase 17) will provide block-device persistence.
- **Widget Toolkit:** `toolkit.py` — full widget set rendering through VDI, available for use inside lenses
- **Icons:** `icons.py` — 21 pixel-art 16×16 stock icons for files/folders/apps, 1x/2x rendering
- **Desktop:** Crystal v3 expression-tree compositor (`crystal.py`). Old AES window manager preserved in `desktop.py` for backward compat. Entry point: `python -m lm1 desktop` launches Crystal.

## The Simulator → Emulator Gap

The project currently has a **simulator** (Python desktop that looks like an OS) and a **separate emulator** (LM-1 machine that runs Lisp binaries headlessly). The path to a true emulator:

1. **Phase 14:** Compiler can express everything the desktop needs (closures, structs, strings, VDI calls)
2. **Phase 15:** C acceleration makes the emulator fast enough to run a desktop interactively
3. **Phase 16:** Desktop boots as compiled Lisp on the LM-1, driven by VDI traps
4. **Phase 17:** Persistent filesystem replaces in-memory VFS
5. **Phase 18+:** Object system, conditions, actors — the OS becomes a real Lisp environment
