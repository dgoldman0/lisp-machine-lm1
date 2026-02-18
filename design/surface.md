# LM-1 Desktop Environment — "Surface"

**Status:** Design sketch — replaces Crystal  
**Date:** 2026-02-18

---

## 0. Why Not Crystal

Crystal is GEM with Lisp objects. It's a 1985 desktop metaphor with s-expression syntax
bolted to the resource declarations. Windows contain applications. Applications display
data. The user interacts through widgets. The Lisp-ness is incidental — you could
implement Crystal on any language runtime and lose almost nothing.

That's the problem. A Lisp Machine deserves an interface paradigm that **could not exist
on any other kind of system.** One where the homoiconicity, the live heap, the
everything-is-an-object nature of the runtime isn't a bolted-on feature but the *reason
the interface works the way it does.*

Surface is that interface.

---

## 1. The Core Idea: The Desktop Is a Living Expression

In Surface, the screen is not a collection of windows containing applications. The screen
is a **single, nested, evaluated expression tree** — a living document that renders itself.

Every pixel on the screen traces back to a node in this tree. Every node is a Lisp
object. You can select any region of the screen and ask "what expression produced this?"
and get a live, editable answer.

```
The screen IS this:

(surface
  (bar :dock bottom
    (launcher)
    (clock :format "%H:%M")
    (system-tray (battery) (network) (volume)))

  (pane :split vertical :ratio 0.7
    (portal *current-project* :lens source-tree)
    (pane :split horizontal :ratio 0.5
      (portal (find-function 'factorial) :lens editor)
      (portal (find-function 'factorial) :lens trace))))
```

This isn't a configuration file. This isn't a layout DSL. This is the **actual running
program** that produces the display. Edit any node and the display updates. The expression
*is* the interface.

---

## 2. Design Principles

### 2.1 From GEM's Spirit

| GEM Principle | Surface Equivalent |
|---|---|
| **Clarity** — clean AES/VDI split | VDI layer survives unchanged. The compositor replaces the AES with something better |
| **Lightness** — 64KB total on an ST | Surface nodes are tiny (a portal is ~5 tagged words). The tree is lean because Lisp is lean |
| **Elegance** — everything fits | There is ONE concept (the expression tree) instead of windows + menus + dialogs + desk accessories + clipboard + file manager |
| **Openness** — documented, hackable | The interface IS its source code. You cannot get more open than that |
| **Resourcefulness** — do more with less | No widget toolkit, no layout engine, no theme engine as separate systems. The language IS all of these |

### 2.2 What Only a Lisp Machine Can Do

1. **Identity between interface and program.** The tree you see on screen is not a
   *description* of the interface. It is the interface. Editing it edits the running
   system. No compilation, no restart, no "apply."

2. **Uniform manipulation.** Every object — a function, a variable, a running actor, a
   network socket, a pixel on screen — responds to the same protocol: inspect, portal,
   transform, connect. There are no second-class citizens.

3. **Structural awareness.** The system knows the type, shape, and relationships of
   everything. A portal to a function doesn't just show text — it can show the call
   graph, the type signature, the live profiling data, the diff from last edit —
   because it *knows it's a function.*

4. **Reactive propagation.** Because every value in the system is a tagged object in a
   GC'd heap, and because the expression tree holds references (not copies), changes
   propagate automatically. Modify a value from the REPL. Every portal viewing it
   updates. No event bus, no observer pattern, no pub/sub. Just pointer equality and
   re-evaluation.

5. **The boundary between using and programming dissolves.** Arranging portals on the
   surface is composing an expression. Connecting two portals with a thread is piping
   data. Recording your actions is collecting a list. Parameterizing a recording is
   wrapping it in a lambda. There is no "user mode" and "developer mode." There is
   only the Lisp.

---

## 3. Fundamental Concepts

### 3.1 The Surface

The surface is the root of the expression tree. It is an infinite, pannable, zoomable
2D plane. What you see on screen is a viewport into the surface.

```lisp
(defstruct surface
  root          ; the root expression node
  viewport      ; (x y width height zoom) — what's visible
  bindings      ; global key/mouse bindings
  watchers      ; list of reactive subscriptions
  history)      ; undo/redo: a zipper of past surface states
```

The entire desktop state is one serializable form. Save it, restore it, diff it, branch
it, merge it. Your desktop is version-controlled the same way your code is, because
it's the same kind of thing: a Lisp expression.

### 3.2 Portals

A portal is a **live lens onto a Lisp object.** Not a window containing an application
displaying data. A direct, structural view of the thing itself.

```lisp
(defstruct portal
  target        ; ref to the object being viewed
  lens          ; symbol or function: how to render it
  position      ; (x . y) on the surface
  size          ; (w . h)
  bindings      ; local key/mouse bindings (lens-specific)
  connections)  ; threads to/from other portals
```

The same object can have many portals. A function might be viewed through:

| Lens | What It Shows |
|------|--------------|
| `:source` | The s-expression source, syntax-highlighted, editable |
| `:disasm` | The compiled LM-1 assembly |
| `:trace` | A live execution trace (call count, args, results) |
| `:profile` | CPU time, allocation rate, GC pressure |
| `:graph` | Call graph — who calls this, what it calls |
| `:doc` | Docstring, type signature, examples |
| `:diff` | Changes since last version |
| `:test` | Associated test cases and their pass/fail status |

A list might be viewed through:

| Lens | What It Shows |
|------|--------------|
| `:tree` | Nested tree view with expand/collapse |
| `:table` | Tabular view (if the list contains uniform records) |
| `:chart` | Bar/line/scatter if the data is numeric |
| `:raw` | The tagged words in memory |
| `:pretty` | Pretty-printed s-expression |

**A lens is just a function** `(object, rect) → drawing commands`. Users write new lenses
the same way they write any function. There is no "plugin API" because lenses are not
plugins — they're just functions.

```lisp
(deflens :flame-chart (fn rect)
  "Render a function's profile data as a flame chart."
  (let ((samples (profile:samples fn)))
    (draw-flame-chart rect samples)))

;; Now use it:
(portal #'my-hot-loop :lens :flame-chart)
```

### 3.3 Threads (Data Connections)

A thread is a **live data-flow connection** between portals. Drag from one portal's
output to another's input, and data flows continuously.

```lisp
(defstruct thread
  source        ; (portal . output-slot)
  sink          ; (portal . input-slot)
  transform     ; optional: function applied to data in transit
  live?)        ; if true, re-evaluates when source changes
```

Threads make composition visible and tangible:

```
┌───────────────┐            ┌──────────────────┐
│ VFS directory │   thread   │   File list      │
│  "/src/"      │───────────►│  :lens :table     │
│  :lens :tree  │            │  (filtered: .lisp)│
└───────────────┘            └────────┬─────────┘
                                      │ thread
                                      ▼
                             ┌──────────────────┐
                             │  Source editor    │
                             │  :lens :source    │
                             │  (selected file)  │
                             └──────────────────┘
```

This isn't a visual programming language. This is the **actual data flow of the
system**, made visible. The threads exist whether or not you display them — showing them
just makes the plumbing inspectable.

### 3.4 Panes (Spatial Composition)

A pane is a recursive spatial partitioning of the surface — like a tiling window manager,
but expressed as an s-expression and freely editable.

```lisp
(pane :split vertical :ratio 0.6
  (portal *buffer* :lens :source)        ; left 60%: code editor
  (pane :split horizontal :ratio 0.5
    (portal *buffer* :lens :trace)       ; right-top: live trace
    (portal *repl* :lens :terminal)))    ; right-bottom: REPL
```

Panes can be:
- **Split** (vertical or horizontal, with a draggable divider)
- **Tabbed** (stack multiple portals, switch with tabs)
- **Floating** (breakout to an absolutely positioned overlay — this is the "window" escape hatch)
- **Maximized** (a pane temporarily fills the viewport)

```lisp
;; A tabbed group
(pane :tabs
  (portal *log* :lens :stream :label "Log")
  (portal *repl* :lens :terminal :label "REPL")
  (portal *profiler* :lens :dashboard :label "Perf"))

;; A floating breakout (like a traditional window, but still a pane)
(pane :float :position (200 . 100) :size (400 . 300)
  (portal *calculator* :lens :interactive))
```

The key insight: **panes are expressions.** Rearranging your layout is editing a list.
Save your layout? It's already a form. Share it? Send the form. The "tiling window
manager vs. floating window manager" debate is meaningless — both are just different
expressions in the same tree.

### 3.5 Facets (Multi-Scale Rendering)

Every object in the system has a **facet protocol** — it knows how to render itself at
multiple scales. This enables semantic zooming.

```lisp
(defgeneric render-facet (object scale rect))

;; A function at different scales:
(defmethod render-facet ((fn <function>) (scale (eql :glyph)) rect)
  ;; 32×32: just an icon with the function name
  (draw-icon :function rect)
  (draw-label (function-name fn) rect :size :tiny))

(defmethod render-facet ((fn <function>) (scale (eql :card)) rect)
  ;; 200×100: name, arg list, docstring first line, status indicator
  (draw-card rect
    :title (function-name fn)
    :subtitle (format-arglist (function-args fn))
    :body (first-line (function-doc fn))
    :badge (if (function-compiled? fn) :compiled :interpreted)))

(defmethod render-facet ((fn <function>) (scale (eql :full)) rect)
  ;; Full size: complete interactive editor/inspector
  (render-source-editor fn rect))
```

As you zoom into the surface, objects transition smoothly from glyphs to cards to full
interactive views. Zoom out and your screen becomes a map of your system — every
function, every data structure, every running actor, visible as a glyph you can zoom
into.

---

## 4. Reactive Propagation

Surface is **reactive by default.** Every portal watches its target object. When the
object changes, the portal re-renders.

This isn't implemented with an event bus or observer pattern. It exploits the hardware:

1. **Write barriers are already in the ISA.** Every `ST.WB`, `ST.CAR`, `ST.CDR`
   instruction marks a card in the card table. The GC uses this for generational
   collection.

2. **Surface piggybacks on the card table.** When a watched object's card is marked, the
   Surface compositor knows that object may have changed and schedules a re-render of
   the relevant portal.

3. **Cost: essentially zero** for unwatched objects (the write barrier runs anyway for GC).
   For watched objects, the additional cost is one check per GC card scan — a bitmap test.

```lisp
;; Watch a reactive value
(defreactive *server-status* :idle)

;; Portal auto-updates when *server-status* changes
(portal *server-status* :lens :status-badge)

;; From any code, any tile, any actor:
(setf *server-status* :overloaded)
;; → the status badge turns red, immediately
```

For values that change at high frequency (e.g., a counter incrementing in a loop), the
compositor rate-limits re-renders to the display refresh rate. You never see tearing or
stale renders — but you also never block the computation.

---

## 5. The Dissolving Boundary

### 5.1 Inspection Is Navigation

Right-click any pixel on the screen. Surface traces backwards through the expression
tree to find the node that produced that pixel. It opens an inspector portal to that
node.

```
User right-clicks the "23" in the clock display.
 └─ Surface traces: clock face → (format-time ...) → hour component
    └─ Inspector shows: fixnum 23, produced by (hour (current-time))
       └─ User can: edit the expression, change the format, thread it
          to another portal, trace its call chain, profile it...
```

This is not "View Source." This is "grab the live object that made this pixel and
interact with it." There is no distinction between the rendered interface and the
objects behind it.

### 5.2 Macro Recording Is List Collection

Every user interaction is an s-expression:

```lisp
(:click portal-17 :position (42 . 108))
(:key portal-17 :char #\a)
(:drag portal-17 :from (10 . 20) :to (50 . 80))
(:command :split-horizontal portal-17)
```

Recording a macro is `(push event *recording*)`. Playing it back is `(mapc #'execute
*recording*)`. Editing it is editing a list. Parameterizing it is abstracting over it:

```lisp
;; Raw recording:
((open-portal "/src/foo.lisp" :lens :source)
 (send-keys "hello")
 (save-portal))

;; User edits it into a reusable function:
(defun insert-header (path text)
  (open-portal path :lens :source)
  (send-keys text)
  (save-portal))

;; Bind it:
(bind-key "C-S-h" (lambda () (insert-header (current-file) (read-string "Header: "))))
```

There is no macro editor application. The recording IS the program. Edit it like any
other expression.

### 5.3 Configuration Is Programming (And Vice Versa)

There are no configuration files, no settings dialogs, no preference panes. Your surface
expression IS your configuration.

Want a dark color scheme? Your surface expression includes `(:theme dark-blue)`.  
Want a different font? `(:font "NotoMono" :size 14)`.  
Want your clock in 24-hour format? The clock portal's expression says `:format "%H:%M"`.

Change any of these and the system updates live. Save your surface expression and you've
saved your entire environment — layout, theme, open files, running computations, data
connections, everything.

```lisp
;; This IS the "dotfiles" — but it's also the running system:
(surface :theme 'solarized-dark :font '("NotoMono" 14 :hinting t)
  (bar :dock bottom :height 32
    (launcher :favorites '(terminal editor file-browser))
    (spacer)
    (clock :format "%H:%M · %a %b %d"))

  (pane :split horizontal :ratio 0.25
    (portal (vfs "/") :lens :tree :filter ".lisp$")
    (pane :tabs
      (portal (find-function 'compile) :lens :source :label "compile.lisp")
      (portal *scratch* :lens :source :label "*scratch*"))))
```

### 5.4 Everything Has a REPL

Any portal can become a REPL. Press a universal key (say, `M-:`) and a command line
appears at the bottom of the focused portal. Type an expression. It evaluates in the
context of that portal's target object.

```
┌─ Portal: *user-list* (:lens :table) ────────────────────┐
│ Name          │ Email              │ Role               │
│───────────────┼────────────────────┼────────────────────│
│ Alice         │ alice@example.com  │ admin              │
│ Bob           │ bob@example.com    │ user               │
│ Carol         │ carol@example.com  │ user               │
├─────────────────────────────────────────────────────────┤
│ λ (filter (lambda (u) (eq (role u) 'admin)) self)      │
│ → ((Alice alice@example.com admin))                     │
└─────────────────────────────────────────────────────────┘
```

`self` in the REPL always refers to the portal's target. You're not "running a command."
You're **transforming the live object you're looking at.** The result can replace the
current view, open a new portal, or just flash in the status line.

---

## 6. Compositor Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Expression Tree                          │
│     (surface (bar ...) (pane ... (portal ...) ...))           │
├──────────────────────────────────────────────────────────────┤
│                      Compositor                               │
│   Walks the tree. Lays out panes. Renders portals.            │
│   Manages focus, input routing, dirty regions.                │
├──────────────────────────────────────────────────────────────┤
│                      Reactive Engine                          │
│   Watches card table for changes to portal targets.           │
│   Schedules re-render of dirty portals. Rate-limits.          │
├──────────────────────────────────────────────────────────────┤
│                         VDI                                   │
│   Drawing primitives. Unchanged from current implementation.  │
├──────────────────────────────────────────────────────────────┤
│                    VDI Device Driver                           │
│   MMIO → framebuffer. Unchanged.                              │
└──────────────────────────────────────────────────────────────┘
```

The compositor is an actor. It:

1. **Evaluates the expression tree** to produce a layout (position and size of each pane
   and portal).
2. **Calls each portal's lens function** to render into its allocated rectangle.
3. **Composites** the results into the framebuffer via VDI.
4. **Listens for input events** and routes them to the focused portal's bindings.
5. **Monitors the reactive engine** for change notifications and schedules partial
   re-renders.

### 6.1 Dirty Rectangle Tracking

Because each portal's bounds are known, and because the reactive engine identifies
*which* portals need re-rendering, the compositor only repaints what changed. A keystroke
in one portal doesn't redraw the clock. A clock tick doesn't redraw the editor.

### 6.2 Focus Model

Focus follows the expression tree:

- The surface has a **focus path**: a sequence of indices into the tree identifying the
  focused portal. E.g., `(1 0)` means "second child of root, first child of that."
- Pressing `Tab` / `Shift-Tab` moves focus to the next/previous sibling.
- Pressing a "zoom" key (e.g., `Super-Enter`) maximizes the focused pane. Press again
  to restore.
- Pressing `Super-←` / `Super-→` navigates the focus path up/down the tree.

### 6.3 Input Routing

Input events are s-expressions. They flow down the focus path:

```
Physical keystroke 'a'
  → (:key :char #\a :modifiers ())
  → Surface global bindings: no match
  → Focused pane bindings: no match
  → Focused portal bindings: match! (:insert-char #\a)
  → Portal's lens handles it (insert 'a' into the buffer)
```

Mouse events include the target portal (resolved by hit-testing the layout tree):

```
Physical click at (342, 217)
  → compositor resolves to portal-5, local coords (42, 17)
  → (:mouse :click :button left :position (42 . 17) :portal portal-5)
  → portal-5's lens handles it
```

---

## 7. Built-In Lenses

Surface ships with a set of lenses that cover the common cases. Users extend this set
by writing functions.

### 7.1 Universal Lenses (work on any object)

| Lens | Description |
|------|-------------|
| `:inspect` | Slot-by-slot inspector. Shows type, tag, shape, fields. Always available. |
| `:raw` | Raw tagged words in memory. Hex view with tag coloring. |
| `:pretty` | Pretty-printed s-expression. |
| `:identity` | Just the object's printed representation, one line. |

### 7.2 Function Lenses

| Lens | Description |
|------|-------------|
| `:source` | Syntax-highlighted editable source. Paredit-aware. Eval-in-place. |
| `:disasm` | LM-1 assembly listing with address annotations. |
| `:trace` | Live trace: call count, last args, last result, call frequency. |
| `:profile` | CPU/memory profiling flame chart. |
| `:graph` | Call graph visualization (callers + callees). |
| `:test` | Test cases associated with this function, pass/fail status. |

### 7.3 Collection Lenses

| Lens | Description |
|------|-------------|
| `:tree` | Expand/collapse tree view. Works on lists, nested vectors, VFS dirs. |
| `:table` | Tabular view for lists of uniform records. Sortable columns. |
| `:chart` | Data visualization (bar, line, scatter, pie) for numeric data. |
| `:stream` | Append-only log view. New items appear at the bottom. Auto-scroll. |

### 7.4 System Lenses

| Lens | Description |
|------|-------------|
| `:terminal` | Interactive REPL. Scrollback, history, completion. |
| `:dashboard` | System monitor: tile utilization, memory, GC stats, message throughput. |
| `:actors` | Actor supervision tree. Status, message queue depth, restart count. |
| `:editor` | Full-featured text editor (paredit, syntax highlight, undo, multiple cursors). |

### 7.5 Composite Lenses

A **composite lens** combines multiple lenses into one view:

```lisp
(deflens :dev-view (obj rect)
  "Editor + trace + REPL in a vertical split."
  (let* ((r1 (rect-top-fraction rect 0.6))
         (r2 (rect-mid-fraction rect 0.6 0.8))
         (r3 (rect-bottom-fraction rect 0.8)))
    (render-lens :source obj r1)
    (render-lens :trace obj r2)
    (render-lens :terminal *repl* r3)))
```

---

## 8. The Object Bar (Replaces Taskbar / Dock)

The bar at the edge of the screen is not a taskbar. It's the **object bar** — a shelf
where you place objects for quick access.

```lisp
(bar :dock bottom :height 32
  ;; Launcher: a set of portals that open when clicked
  (launcher :favorites '(terminal editor file-browser inspector))

  ;; Live objects: anything you've "pinned" to the bar
  (pin *server* :lens :status-badge)     ; green/red dot
  (pin *build-log* :lens :progress)      ; progress bar
  (pin *inbox* :lens :count-badge)       ; number of unread

  ;; System tray
  (spacer)
  (system-tray
    (portal *battery* :lens :icon)
    (portal *network* :lens :icon)
    (portal *audio* :lens :icon))
  (clock :format "%H:%M"))
```

Pinned objects are mini-portals. They render at glyph scale on the bar. Click to zoom
in (open a full portal). They update reactively — a build progress bar fills up as the
build runs, without polling.

---

## 9. The Scrapbook (Replaces Clipboard)

The clipboard is a **scrapbook** — a persistent, typed, browsable collection of objects.

```lisp
(scrapbook:snip object)           ; add an object to the scrapbook
(scrapbook:paste :type :text)     ; retrieve most recent text entry
(scrapbook:paste :type :sexp)     ; retrieve as s-expression
(scrapbook:browse)                ; open a portal to the scrapbook itself
```

Because it stores *objects* (not byte streams), the scrapbook understands structure.
Copy a function? Paste it as source, or as a reference, or as a closure. Copy a table
row? Paste it as a record, or as CSV, or as an s-expression.

The scrapbook has a browsable history (it's just a list). Open a portal to it and you
see every object you've ever snipped, organized by time, type, and source.

---

## 10. Session Management: Worlds

A **world** is a saved surface state — the entire expression tree plus the state of every
object referenced by it. Like a Smalltalk image, but structured:

```lisp
(save-world "~/worlds/friday-debugging-session.world")
(load-world "~/worlds/friday-debugging-session.world")
(branch-world "experiment-1")   ; fork the current world
(merge-world "experiment-1")    ; merge changes back
(diff-world "experiment-1" "main") ; see what changed
```

Worlds are version-controlled. You can branch your entire working environment, try
something risky, and merge it back — or throw it away. This isn't just undo. This is
**branching your entire computing reality.**

---

## 11. How Common Tasks Work

### 11.1 "Opening a file" → Creating a portal

```lisp
;; GEM: open a window, launch an editor app, load file into app
;; Surface: create a portal to the file object
(portal (vfs "/src/main.lisp") :lens :editor)
```

### 11.2 "Browsing the file system" → Portal with :tree lens

```lisp
;; No file manager app. Just a portal to a directory with a lens.
(portal (vfs "/") :lens :tree)
;; Click a file → thread opens a portal to it with an appropriate lens
```

### 11.3 "Running a program" → Spawn + portal to result

```lisp
;; Spawn the computation, get a future, open a portal to the future
(let ((f (future (render-scene *my-scene*))))
  (portal f :lens :progress))
;; Portal shows "computing..." then the result when done
```

### 11.4 "Debugging" → Portals to multiple facets

```lisp
;; No debugger application. Just multiple lenses on the same function:
(pane :split horizontal :ratio 0.5
  (portal #'broken-function :lens :source)  ; see the code
  (portal #'broken-function :lens :trace))  ; see what it's doing

;; Set a breakpoint by editing the source (it's live):
;; (break) inserts a yield point. The trace lens shows the suspended state.
```

### 11.5 "System administration" → Portal to system objects

```lisp
;; No control panel app. Just portals to the system objects.
(pane :tabs
  (portal *tile-manager* :lens :dashboard :label "Tiles")
  (portal *gc-stats* :lens :chart :label "GC")
  (portal *actor-registry* :lens :actors :label "Actors")
  (portal *network* :lens :connections :label "Network"))
```

### 11.6 "Custom workflow" → Just compose

```lisp
;; A live-coding music environment? Sure:
(pane :split horizontal :ratio 0.6
  (portal *synth-patch* :lens :source)       ; edit the synthesizer code
  (pane :split vertical :ratio 0.5
    (portal *audio-out* :lens :waveform)     ; see the waveform
    (portal *audio-out* :lens :spectrum)))   ; see the frequency spectrum

;; Every edit to *synth-patch* hot-reloads into the running synth.
;; The waveform and spectrum portals update reactively.
```

---

## 12. What This Gives You That Nothing Else Does

1. **Zero-friction inspection.** In any other system, to understand what produced a UI
   element, you need to find the source file, grep for the relevant code, set
   breakpoints, reproduce the state. In Surface, you right-click and you're there.
   The thing on screen IS the object. The object IS the code.

2. **Composition without integration.** In conventional systems, making two apps work
   together requires APIs, file formats, clipboard formats, or plugins. In Surface,
   you draw a thread between two portals. Done. The data flows, typed and structured,
   through the Lisp runtime.

3. **Total mutability.** Don't like how something looks? Change its lens. Don't like how
   something behaves? Edit its source — live, in place, without stopping anything.
   The interface is never frozen, never opaque, never "someone else's code you can't
   touch."

4. **Time travel.** Worlds give you branching undo across your entire environment. Not
   just "undo the last edit" — undo the last hour. Fork your reality. Try two
   approaches simultaneously. Merge the winner.

5. **Flat learning curve, infinite ceiling.** A new user sees portals and panes — they
   look like windows and tiles. They click, they type, it works. But there is no wall.
   Every interaction they learn to do by pointing and clicking has an s-expression
   equivalent they can automate, parameterize, and compose. The UI *is* the
   programming language. The programming language *is* the UI.

---

## 13. Implementation Strategy

Surface builds on everything that already works:

| Existing Layer | Role in Surface |
|---|---|
| VDI (Phase 9) | Drawing primitives, framebuffer, compositing |
| Widget toolkit (Phase 13) | Lenses use widgets internally for complex views |
| VFS (Phase 13) | The file objects that portals view |
| Icons (Phase 13) | Glyph-scale rendering of objects |
| Event system (Phase 10) | Input events, routed through the expression tree instead of through AES |
| Desktop profile (Phase 11) | Replaced by world save/load |
| Scrap (Phase 11) | Extended into the scrapbook |

The compositor replaces the AES window manager. Windows, menus, and Crystallites are
gone — replaced by portals, panes, lenses, and the object bar. The VDI survives
unchanged.

### 13.1 Implementation Phases

1. **Portal + Pane core.** Expression tree evaluator, basic layout, portal rendering
   with `:inspect` and `:pretty` lenses. Replaces the window manager.
2. **Built-in lenses.** `:source`, `:tree`, `:table`, `:terminal`. Enough to do real
   work.
3. **Reactive engine.** Card-table-piggyback reactive updates. Watched objects trigger
   portal re-renders.
4. **Threads.** Data-flow connections between portals. Drag-to-connect UI.
5. **Object bar.** Pinned objects, launcher, system tray, clock.
6. **Scrapbook.** Typed clipboard with history and browsing.
7. **Facets.** Multi-scale rendering, semantic zoom.
8. **Worlds.** Save/load/branch/merge entire surface states.
9. **Inline REPL.** `M-:` in any portal, eval in target context.
10. **Macro recording.** Event collection, replay, parameterization.

---

## 14. Aesthetics

Surface inherits the visual direction already established in Phase 12: the dark,
retro-futuristic palette (deep navy backgrounds, cyan/teal accents, warm amber
highlights). But the visual language evolves:

- **No window chrome.** Portals don't have title bars, close buttons, or resize grips.
  They have subtle edges (1px luminance shift), a tiny label in the top-left corner,
  and respond to gestures at their borders for resize. The content fills the space.
  
- **Focus glow.** The focused portal has a subtle animated edge glow (1-2px, accent
  color). Everything else is muted.

- **Thread lines.** Data connections are drawn as curved lines with a subtle pulse
  animation showing data flow direction. Color-coded by data type.

- **Semantic color.** Objects are tinted by type: functions are cyan, data structures
  are amber, actors are green, errors are red. This permeates everything — glyph
  backgrounds, thread lines, bar pins, inspector labels.

- **Depth through darkness.** Instead of drop shadows and gradients to simulate depth,
  Surface uses luminance: deeper nesting = darker background. Your focus point is
  always the brightest region on screen.

---

## 15. What This Is Not

- **Not a visual programming language.** You CAN compose programs by connecting portals,
  but that's a side effect, not the point. The point is that the data flow of your
  computing environment is visible and editable.

- **Not a tiling window manager.** Panes look like tiling, but they're expressions. You
  can nest them, template them, parameterize them, share them. i3 can't do that.

- **Not a notebook.** Notebooks are linear sequences of cells producing output. Surface
  is a 2D spatial composition of live objects connected by data flow. Notebooks are a
  special case (a single pane of sequential portal cells).

- **Not Emacs.** Emacs has buffers and modes and text-centric everything. Surface has
  objects and lenses and structural everything. But yes, the spirit is similar: the
  user interface IS the programming language.

---

*The best interface for a Lisp Machine is not a desktop with Lisp underneath. It's Lisp
itself, rendered spatially, reactively, and beautifully, all the way down.*
