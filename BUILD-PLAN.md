# LM-1 Build Plan

**Date:** 2026-02-16

---

## Guiding Principle

Build bottom-up. Each phase produces something testable. No phase depends on something that isn't already working.

## Phases

### Phase 1: Emulator Core — Scalar Execution
**Goal:** Execute raw scalar instructions on 1 tile, 1 thread.

- [ ] Project setup (Python package with `pyproject.toml`, C++ extension stub)
- [ ] Word type (64-bit int with tag helpers: `is_fixnum`, `is_ref`, `tag_fixnum`, `untag_fixnum`, etc.)
- [ ] Instruction encoding/decoding (32-bit → opcode + fields)
- [ ] Register file (32 × u64 + special registers)
- [ ] Memory (flat `Vec<u64>` with byte-addressable read/write)
- [ ] Execution loop: fetch → decode → match → execute
- [ ] Implement scalar ops: `ADD`, `SUB`, `AND`, `OR`, `XOR`, `SHL`, `SHR`, `LI`, `LUI`
- [ ] Implement raw loads/stores: `LDR`, `STR`
- [ ] Implement branches: `BR`, `BR.T`, `BR.NIL`, `BR.EQ`, `BR.FIX.LT/EQ/GT`
- [ ] Implement `NOP`, `HALT`, `TILE.ID`, `THREAD.ID`, `CYCLE`
- [ ] Console I/O traps: `TRAP #0x80` (putchar), `TRAP #0x81` (getchar)
- [ ] Minimal CLI: load a binary, run it, print output
- [ ] Test: hand-assemble a program that prints "LM-1\n"

### Phase 2: Tagged Operations & Allocation
**Goal:** Fixnum arithmetic, type tests, and nursery allocation work.

- [ ] Tagged arithmetic: `ADD.FIX`, `SUB.FIX`, `MUL.FIX`, `DIV.FIX`, `ADD.FIX.IMM`
- [ ] Type tests: `TST` (all tag variants), `EQ`, `CMP.TAGGED`
- [ ] Nursery state: `np`, `nl` registers, configurable nursery region
- [ ] `ALLOC`, `ALLOC.CONS`, `ALLOCV`, `ALLOC.CLOSURE`
- [ ] Header template table (array of u64, indexed by instruction field)
- [ ] Tagged field access: `LD`, `ST`, `LD.CAR`, `LD.CDR`
- [ ] Test: program that conses a list of 100 fixnums, walks it, sums them

### Phase 3: Traps and GC
**Goal:** Trap mechanism works. Nursery overflow triggers GC.

- [ ] Trap table: per-thread base register, dispatch on trap code
- [ ] Trap entry: save PC, jump to handler. `ERET`: restore PC, resume.
- [ ] `PUSH`, `POP`, `PUSH.MULTI`, `POP.MULTI` for saving/restoring in handlers
- [ ] Write barrier: `ST.WB`, `ST.CAR`, `ST.CDR` — card table update logic
- [ ] Card table: byte array, mark on cross-gen store
- [ ] Implement `TRAP_NURSERY_OVERFLOW` handler (Cheney's copy GC in LM-1 asm)
- [ ] Cluster shared SRAM as old-gen target
- [ ] Test: allocate until nursery overflows 10×, verify live objects survive

### Phase 4: Dispatch (IC)
**Goal:** `CALL.IC` hit/miss/install cycle works.

- [ ] IC table: hash map of (callsite, shape) → code_entry per tile
- [ ] `CALL.IC`: probe IC, hit → direct jump, miss → `TRAP_IC_MISS`
- [ ] `IC.INSTALL`: populate IC entry
- [ ] `CALL.DIRECT`, `CALL.CLOSURE`, `RET`
- [ ] `TAILCALL.IC`, `TAILCALL.DIRECT`
- [ ] Frame push/pop mechanics (save lr, fp to stack)
- [ ] `TST.SHAPE` (check shape hint, fall back to header load)
- [ ] Test: define two "classes" (shapes), dispatch a method on each

### Phase 5: Messaging & Multi-Tile
**Goal:** Multiple tiles running, passing messages.

- [ ] Multi-tile memory layout (N tiles × SRAM)
- [ ] Multi-thread scheduling (round-robin within tile)
- [ ] Hardware queues: `SEND`, `RECV`, `TRY.RECV`
- [ ] Cross-tile message routing (direct function call in emulator)
- [ ] `CAS.TAGGED`, `FAA` on shared memory
- [ ] `FENCE.GC`
- [ ] Test: producer on tile 0 sends fixnums, consumer on tile 1 sums them

### Phase 6: Assembler
**Goal:** Stop hand-encoding binaries. Write assembly, get binaries.

- [ ] Text assembler: reads LM-1 mnemonics, outputs 32-bit words
- [ ] Label resolution (forward/backward references)
- [ ] `.data` directives for constants and header templates
- [ ] Outputs flat binary or `.lmo` object format
- [ ] Re-express all prior tests as assembly source files

### Phase 7: BIOS
**Goal:** BIOS boots on the emulator and hands off to an OS image.

- [ ] Write BIOS in LM-1 assembly (using the assembler from Phase 6)
- [ ] Phase 1–5 BIOS code (init, traps, GC, IC, console, image loader)
- [ ] Block device emulation: `TRAP #0x82` reads/writes host file
- [ ] Boot info block construction
- [ ] Test: BIOS boots, prints banner, loads a trivial OS image, jumps to it

### Phase 8: Lispos Kernel (OS-0 through OS-2)
**Goal:** Boot to a working REPL with an object system.

- [ ] Bootstrap compiler: enough to compile Lisp to LM-1 (cross-compiler, runs on host)
- [ ] OS init: replace trap table, init GC, init symbol table
- [ ] Reader: `read` parses S-expressions from console input
- [ ] Printer: `print` outputs tagged values to console
- [ ] Eval: compile-and-run a form (initially: interpret or very simple codegen)
- [ ] REPL loop: `(loop (print (eval (read))))`
- [ ] `defun`, `lambda`, `let`, `if`, `cond`, `quote`, `cons`, `car`, `cdr`, `eq`, `+`, `-`, `*`, `/`
- [ ] Object system: `defclass`, `defgeneric`, `defmethod`, shape creation
- [ ] Condition system: `handler-bind`, `signal`, `invoke-restart`
- [ ] Test: `(+ 1 2)` → `3` at the REPL. `(defun fact (n) (if (eq n 0) 1 (* n (fact (- n 1))))) (fact 10)` → `3628800`

### Phase 9: VDI Display Engine (Emulator)
**Goal:** Framebuffer output visible on the host. Blit and text primitives work.

- [ ] VDI device model: framebuffer-backed MMIO (VDI_MODE, VDI_FB_BASE, VDI_PALETTE, etc.)
- [ ] SDL2 host window (or Pygame fallback): present framebuffer as texture at 60 Hz
- [ ] VDI drawing primitives: rect fill, bitblt with ROP, color expansion (font rendering)
- [ ] 8-bit indexed-color palette with CLUT
- [ ] Hardware cursor overlay
- [ ] Host keyboard/mouse → LM-1 event injection
- [ ] Emulator trap for VDI commands: `TRAP #0x83` (vdi_call, r1=function, r2..=args)
- [ ] Test: fill screen with a color, draw rectangles, render text, animate cursor

### Phase 10: Crystal Desktop — Window Manager
**Goal:** Overlapping windows with move/resize/raise/lower, global menu bar, event dispatch.

- [ ] Crystal AES: window open/close/move/resize, z-order management
- [ ] Redraw protocol: AES sends redraw events, app repaints via VDI
- [ ] Event loop: keyboard, mouse, timer events routed to focused window
- [ ] Global menu bar (GEM-style: active app's menu merges with system menu)
- [ ] Menus as Lisp lists — define, display, dispatch
- [ ] Window decorations: title bar, close/full/iconify gadgets, resize handle
- [ ] Click-to-focus policy
- [ ] Desktop root window (background color/pattern, desktop icons)
- [ ] Test: open 3 overlapping windows, move/resize/raise, menu bar responds

### Phase 11: Crystal Desktop — Crystalets, File Manager, Theming
**Goal:** Desk accessories, spatial file manager, resource system, scrap, themes.

- [ ] Crystalet framework: system-registered micro-apps, always available
- [ ] Standard Crystalets: clock, calculator, terminal (REPL-in-a-window), inspector
- [ ] Scrap (clipboard): typed, structured, with history and type negotiation
- [ ] Resource system: menus/dialogs/alerts as editable Lisp data
- [ ] Spatial file manager: folder=window, icons, drag-and-drop, file associations
- [ ] Desktop profile: serialize/deserialize desktop state as Lisp form
- [ ] Theming: theme objects (colors, fonts, metrics, icons), live switching
- [ ] Control Panel Crystalet: theme picker, mouse speed, resolution
- [ ] Test: full desktop session — open file manager, launch editor, use clock, switch theme

---

## What to Build First

Phases 1-2 are the foundation. Everything else depends on them. Phase 6 (assembler) could arguably come earlier — but hand-encoding a few small test programs is faster than building an assembler when we only need 10-20 instructions for initial tests.

Phases 9-11 (graphics and desktop) depend on Phase 8 (Lispos kernel) for the object system and REPL. Phase 9 (VDI engine) can be prototyped in parallel with earlier phases by using emulator traps for VDI calls.

**Start now: Phase 1.**
