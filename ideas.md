# LM-1 Ideas & Future Directions

Collected during development. Not committed to — just possibilities.

---

## Visual Modernization (Phase 12)

- **Gradient title bars:** Horizontal gradient fill (e.g., dark blue → medium blue for active, gray tones for inactive). Requires a VDI `fill_rect_gradient` primitive or palette ramp allocation.
- **Drop shadows on windows:** 2-4px semi-transparent shadow below/right of each window. Could be a dedicated shadow palette index (dark with alpha blending) or just dark gray offset rects.
- **Modern palette allocation:** Dedicate palette ranges for gradients:
  - 32-47: active title bar gradient (16 shades)
  - 48-63: inactive title bar gradient
  - 64-79: desktop background gradient
  - 80-87: shadow levels
  - 88-95: button/widget gradients
  - 96-111: accent colors
- **Rounded corners:** Approximate with corner pixel masks (2-3px radius). Cheap to implement in indexed-color.
- **Desktop wallpaper gradient:** Vertical gradient from dark to light instead of flat fill + dots.
- **Anti-aliased text:** Sub-pixel hinting is overkill for 8-bit indexed, but 2-color smoothing at glyph edges could help. Or just a higher-quality font.
- **Better font:** Load a real bitmap font (BDF/PSF format) rather than hand-coded glyphs. 8×16 is fine but the glyph quality matters.
- **Window border refinement:** Subtle 1px inner highlight (white) + 1px outer shadow (dark) instead of flat black border.
- **Menu bar polish:** Subtle bottom border, slight gradient, hover highlight transitions.
- **Button styling:** Rounded button look, pressed state inversion, hover highlight.
- **Scrollbar modernization:** Thumb with gradient, arrow buttons, track shading.

## Crystallite Ideas

- **Inspector:** Object inspector for Lisp values — browse cons trees, examine tagged words. Essential for debugging native code.
- **Text Editor:** Simple buffer-based editor. Core features: insert/delete, cursor movement, save/load. Could start as a line editor and grow.
- **Hex Viewer:** Display raw memory regions. Useful for debugging the emulator itself.
- **System Monitor:** Show tile utilization, GC stats, memory usage, message queue depths.
- **Sketch Pad:** Simple pixel drawing tool using VDI primitives. Good stress-test for the graphics system.
- **Help Viewer:** Hypertext documentation browser. Lisp-structured help entries.
- **Settings / Control Panel:** Theme picker, mouse speed, display resolution.

## Compiler Extensions (Phase 13)

- **`while` / `loop`:** Essential for iteration without recursion overhead. `(while test body...)` and `(loop body...)` with `(break)` / `(break value)`.
- **`do`:** Scheme-style iteration: `(do ((var init step) ...) (test result) body...)`.
- **Named `let`:** `(let loop ((x 0)) (if (< x 10) (loop (+ x 1)) x))` — compiles to a loop.
- **Multiple return values:** `(values a b)` and `(receive (x y) (func) body)`. Maps to multiple registers.
- **Tail-call optimization:** Detect tail position and emit `TAILCALL` instead of `CALL` + `RET`.
- **String operations:** `string-ref`, `string-set!`, `string-length`, `string-append`, `substring`. Need string representation (header + byte array).
- **Vector operations:** `make-vector`, `vector-ref`, `vector-set!`, `vector-length`. Maps to ALLOCV + indexed field access.
- **Character type:** `char->integer`, `integer->char`, `char=?`, `char<?`. Could use a tagged special subtype.
- **Macro system:** `defmacro` with compile-time expansion. Even simple `syntax-rules` would be powerful.
- **`letrec`:** Mutually recursive local bindings. Needed for complex algorithms.
- **Pattern matching:** `(match expr (pattern body) ...)` — extremely useful, compiles to nested conditionals.

## Native Desktop (Phase 14+)

- **VDI trap wrappers:** `(vdi-fill-rect x y w h color)`, `(vdi-draw-string x y text fg bg)`, etc. Thin Lisp wrappers around TRAP 0x83.
- **Event loop in Lisp:** `(event-loop (lambda (type d1 d2) ...))`. Replaces the Python pygame loop.
- **Window as vector:** `[wid title x y w h flags ...]`. All window ops become vector-ref/set!.
- **Redraw by message:** AES sends `(:redraw wid)` message to the owning crystallite. Crystallite redraws via VDI calls.
- **Menu definition in Lisp:** `(defmenu "File" ("Open" open-handler) ("Save" save-handler) ("---") ("Quit" quit-handler))`.
- **Desktop as OS image:** The entire desktop compiles to an LM-1 binary that boots via BIOS. No Python in the rendering path.
- **Hot-reload:** Recompile a Lisp function and patch it into the running system. The cross-compiler could emit patches.

## Performance

- **C++ acceleration:** The inner execution loop (`fetch → decode → execute`) is the hot path. A Cython or pybind11 extension could 10-100x the emulator speed.
- **JIT for VDI `present()`:** The per-pixel palette lookup in `present()` is O(width × height) per frame. A numpy/Cython inner loop would help.
- **Framebuffer as numpy array:** Replace `bytearray` with `numpy.ndarray` for vectorized fill/blit operations.
- **Dirty rectangles:** Track which screen regions changed and only re-render those in `present()`.
- **SDL2 instead of pygame:** Direct SDL2 via ctypes or pysdl2 for better frame pacing and GPU texture upload.

## System Architecture

- **Object system:** Full CLOS-style: `defclass`, `defgeneric`, `defmethod`, multiple dispatch, method combination. Shapes in the IC system map directly to class layouts.
- **Condition system:** `handler-bind`, `handler-case`, `signal`, `invoke-restart`. Non-local exits without stack unwinding (restartable conditions).
- **Package system:** Namespace management for symbols. Prevents name collisions across modules.
- **Foreign function interface:** Call host Python functions from Lisp code running on the emulator. Useful for development/testing.
- **Persistence:** Snapshot the entire heap to disk and restore it. A Smalltalk-style "image" system.
- **Networking:** Emulated network device for inter-machine communication. Could enable distributed crystallites.
- **Sound:** Simple tone generator / PCM playback device. Audio crystallite for music/effects.

## Fun Stuff

- **Boot animation:** Crystal logo drawing itself line-by-line during BIOS POST.
- **Screen saver:** Bouncing logo or starfield crystallite that activates on idle.
- **Game crystallite:** Tetris, Snake, or a Lisp-scripted adventure game running in a window.
- **Lisp syntax highlighting:** Terminal crystallite could colorize parentheses and keywords.
- **Desktop icons:** Spatial icons on the desktop background, launchable with double-click.
- **Drag and drop:** Between windows. The scrap system mediates the data transfer.
- **Multi-tile desktop:** Different tiles could run different crystallites. Message-passing between UI and computation tiles.

## Applications (see BUILD-PLAN Phases 19-20)

These are the real payoff — things that use the hardware for actual work:

- **Self-hosting compiler:** The cross-compiler rewritten in Lisp, running on the machine, compiling itself. Parallel compilation across tiles. The ultimate bootstrap test.
- **Symbolic algebra:** Not a toy CAS — a system that can differentiate, integrate, and simplify real expressions. The tagged-word ISA was literally built for tree-walking over symbolic data.
- **Parallel ray tracer:** Each tile traces a screen region. Demonstrates that per-tile GC doesn't cause pauses in other tiles. Interactive — change scene from REPL, see result.
- **Actor chat system:** Actors as the natural concurrency model. Supervision trees, message delivery guarantees, fault tolerance. Could span multiple emulated machines once networking exists.
- **GP engine:** The machine breeding Lisp programs is peak "Lisp machine." Fitness evaluation runs native code, not interpreted.
- **Game with hot-editable logic:** The live IDE (Phase 15) shines here — change enemy AI code while the game is running, see it take effect next frame.
- **Benchmark suite:** Compare LM-1 Lisp against CPython, SBCL, Erlang BEAM on equivalent workloads. Especially interesting for GC-heavy symbolic workloads and actor message throughput.
