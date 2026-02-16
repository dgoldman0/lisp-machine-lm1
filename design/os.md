# LM-1 Operating System Design — "Lispos"

**Status:** Design sketch  
**Date:** 2026-02-16

---

## 1. What Kind of OS Is This?

Not Unix. Not a microkernel. Not a hypervisor. Lispos is a **single-language, single-address-space, object-based operating system** in the tradition of the Symbolics Genera and Xerox Interlisp-D environments, redesigned for a many-tile fabric.

Key properties:

- **No kernel/user mode split.** All code runs in the same address space. Protection comes from the tagged word model (you can't forge a pointer) and optionally from capability mode.
- **No processes in the Unix sense.** The unit of execution is the **actor** (a closure + mailbox + state). Actors are scheduled across tiles by the runtime.
- **No file system in the Unix sense.** Persistent storage is a **persistent object store**. "Files" are named objects.
- **No shell in the Unix sense.** The user interface is the **REPL** (and a Listener, and an Inspector, and a graphical environment eventually).
- **The OS is the language runtime.** There is no boundary between "the OS" and "the Lisp system." They are the same thing.

## 2. Architectural Layers

```
┌──────────────────────────────────────────────────────────────┐
│                     User Programs                             │
│         (Lisp applications, editors, servers, tools)          │
├──────────────────────────────────────────────────────────────┤
│                      Language Layer                           │
│   (REPL, compiler, debugger, inspector, package system)       │
├──────────────────────────────────────────────────────────────┤
│                     Object System                             │
│   (shapes, classes, generic functions, method dispatch,       │
│    class hierarchy, metaclasses)                               │
├──────────────────────────────────────────────────────────────┤
│                    OS Services Layer                           │
│   (scheduler, GC, networking, storage, device I/O)            │
├──────────────────────────────────────────────────────────────┤
│                  Tile Runtime Layer                            │
│   (trap handlers, nursery GC, IC management, queue drivers,   │
│    tile init, thread management)                               │
├──────────────────────────────────────────────────────────────┤
│                     BIOS (bootstrap only)                     │
├──────────────────────────────────────────────────────────────┤
│                     LM-1 Hardware / Emulator                  │
└──────────────────────────────────────────────────────────────┘
```

## 3. Tile Runtime Layer

This is the first thing the OS installs, replacing the BIOS's trap handlers and GC with production-quality versions.

### 3.1 Trap Handlers

The OS installs a new trap table on each tile. Key handlers:

| Trap | Handler |
|------|---------|
| `TRAP_NOT_FIXNUM` | Invoke generic arithmetic. `(+ fixnum bignum)` → call the generic `+` function. |
| `TRAP_FIXNUM_OVERFLOW` | Promote to bignum. Allocate a bignum, store the overflowed result, return the bignum ref. |
| `TRAP_NURSERY_OVERFLOW` | Run the production GC (see § 5). |
| `TRAP_IC_MISS` | Run the production method lookup (see § 4). |
| `TRAP_QUEUE_FULL` | Back-pressure: yield the current thread, retry later. |
| `TRAP_QUEUE_EMPTY` | No message: yield the thread, schedule another actor. |
| `TRAP_STACK_OVERFLOW` | Grow the stack (allocate a new stack segment) or signal a condition. |

The key insight: **traps are the OS's system call mechanism.** There are no `SYSCALL` instructions. The ISA's natural slow paths *are* the entry points into the OS.

### 3.2 Per-Tile State

Each tile maintains a **tile-local environment** pointed to by the `tp` register:

```lisp
(defstruct tile-env
  tile-id             ; fixnum
  thread-contexts     ; vector of 4 thread context structs
  nursery-base        ; raw address
  nursery-limit       ; raw address
  card-table-base     ; raw address
  trap-table          ; raw address
  header-templates    ; vector of header template words
  ic-table            ; ref to the IC table structure
  local-actor-queue   ; queue of actors waiting to run on this tile
  gc-state            ; current GC phase, stats
  method-cache        ; ref to cluster method cache
  symbol-table        ; ref to cluster symbol table
)
```

### 3.3 Thread Management

Each of the 4 hardware threads per tile is either:

- **Running an actor** — executing a Lisp closure
- **Running GC** — one thread can be dedicated to GC work during collection
- **Idle** — waiting for work

The tile scheduler is tiny — it's a loop that:
1. Dequeues an actor from the local queue (or steals from the cluster).
2. Restores the actor's continuation (a saved register set or a closure + args).
3. Runs it until it yields (returns a result, sends a message, or is preempted).
4. Saves the actor's state if suspended, or drops it if complete.

## 4. Object System

### 4.1 Shapes and Classes

Everything is an object. The shape/class system is the foundation:

```
           ┌──────────┐
           │   <t>    │         ; the root class, like CL's T
           └────┬─────┘
                │
     ┌──────────┼──────────┐
     │          │          │
┌────▼───┐ ┌───▼────┐ ┌───▼─────┐
│<number>│ │<symbol>│ │<sequence>│
└────┬───┘ └────────┘ └────┬────┘
     │                     │
 ┌───▼────┐           ┌────▼───┐
 │<integer>│          │<vector>│
 └───┬────┘           └────────┘
     │
┌────▼───┐  ┌────────┐
│<fixnum>│  │<bignum>│
└────────┘  └────────┘
```

The class hierarchy is a normal Lisp data structure (objects in the heap). Classes have:

```lisp
(defstruct class
  name              ; symbol
  shape-id          ; the 32-bit shape ID used in headers
  superclasses      ; list of class refs (for CPL computation)
  cpl               ; class precedence list (cached, computed once)
  slots             ; slot descriptors
  direct-methods    ; alist of (generic-function . method) for this class
)
```

### 4.2 Generic Functions and Methods

Dispatch is CLOS-style:

```lisp
(defgeneric add (a b))

(defmethod add ((a <fixnum>) (b <fixnum>))
  ;; This is the fast path — but the ISA handles it via ADD.FIX.
  ;; This method is only called via TRAP_NOT_FIXNUM when one arg isn't a fixnum.
  (+ a b))

(defmethod add ((a <fixnum>) (b <bignum>))
  (bignum-add (fixnum->bignum a) b))

(defmethod add ((a <bignum>) (b <bignum>))
  (bignum-add a b))
```

The method lookup algorithm:

1. Get the receiver's shape → class.
2. Get the generic function from the callsite.
3. Walk the CPL, find the most specific applicable method.
4. If method combination (`:before`, `:after`, `:around`), build the effective method.
5. Cache: install in the IC table *and* in the cluster method cache.

### 4.3 Condition System (Error Handling)

No exceptions-as-control-flow. Instead, the **condition system** (as in CL):

```lisp
(handler-bind ((division-by-zero
                 (lambda (c)
                   (invoke-restart 'use-value 0))))
  (/ x y))
```

Conditions are objects. Handlers are dynamically bound. Restarts are established by the code that *knows how to recover*. The handler *decides which recovery strategy to use*.

This is implemented via the dynamic environment (a chain of handler frames on the stack), not via unwinding. No stack unwinding happens until a restart is actually invoked.

## 5. Garbage Collector (Production)

The OS replaces the BIOS's trivial GC with a proper generational, mostly-concurrent collector as described in [spec/05-memory-gc.md](../spec/05-memory-gc.md). The implementation:

### 5.1 Nursery Collection (Per-Tile)

Same algorithm as the BIOS GC (Cheney's copy), but written in Lisp (compiled to LM-1) instead of assembly, and more careful about:

- Handling all header subtypes (the BIOS only knows cons and vectors)
- Updating the IC table if any code objects moved
- Tracking GC metrics (allocation rate, survival rate)
- Adaptive nursery sizing (grow/shrink nursery based on survival rate)

### 5.2 Old-Gen Collection (Per-Cluster)

Uses the movement engines:

```lisp
(defun collect-old-gen (cluster)
  ;; Phase 1: Mark
  (let ((scan-result (enq-scan (cluster-old-gen-region cluster))))
    (await-engine scan-result)
    
    ;; Phase 2: Copy survivors to new region
    (let* ((new-region (allocate-cluster-region cluster))
           (copy-result (enq-copy (cluster-old-gen-region cluster) new-region)))
      (await-engine copy-result)
      
      ;; Phase 3: Fix up all pointers
      ;; (All tiles in the cluster briefly fence)
      (broadcast-gc-fence cluster)
      (let ((fixup-result (enq-fixup (scan-result-pointers scan-result)
                                      (copy-result-forwarding copy-result))))
        (await-engine fixup-result))
      
      ;; Phase 4: Swap regions
      (release-cluster-region cluster (cluster-old-gen-region cluster))
      (setf (cluster-old-gen-region cluster) new-region)
      (broadcast-gc-resume cluster))))
```

### 5.3 Full-Heap Collection (HBM — Rare)

Incremental mark-compact, run on dedicated "GC tiles" (tiles temporarily repurposed from the mutator pool).

## 6. Scheduler

### 6.1 Actor Queue

```lisp
(defstruct actor
  id                ; unique ID (fixnum)
  mailbox           ; ref to message queue (or hardware queue ID)
  behavior          ; closure: the actor's message handler
  state             ; the actor's private mutable state
  priority          ; fixnum: scheduling priority
  affinity          ; tile or cluster ID, or nil for any
  status)           ; :running, :waiting, :suspended, :dead
```

### 6.2 Scheduling Algorithm

```
for each tile:
    loop:
        actor = dequeue(tile.local_queue)
               OR steal(cluster.queue)
               OR steal(neighbor_cluster.queue)
               OR idle()
        
        msg = recv(actor.mailbox)   ; blocks if no message → thread yields
        result = call(actor.behavior, actor.state, msg)
        
        match result:
            (:continue new-state)      → actor.state = new-state; re-enqueue
            (:stop)                     → actor.status = :dead
            (:send target msg+)        → send(target, msg); re-enqueue
            (:become new-behavior)     → actor.behavior = new-behavior; re-enqueue
            (:suspend)                 → actor.status = :suspended
```

### 6.3 Priority Levels

| Priority | Name | Use |
|:--------:|------|-----|
| 0 | **System** | GC, scheduler itself, trap handlers |
| 1 | **Interactive** | REPL, user input, UI |
| 2 | **Normal** | Application actors |
| 3 | **Background** | Compilation, indexing, maintenance |
| 4 | **Idle** | Housekeeping, metrics |

## 7. Storage: Persistent Object Store

### 7.1 Model

No files. No directories. The storage layer presents a **named, versioned, persistent object graph**.

```lisp
;; Store an object
(store "my-data" my-list)

;; Retrieve it (loads from disk if not in memory)
(fetch "my-data")   ; => the saved list, fully live in the heap

;; It's just an object — you can cons onto it, map over it, etc.
(store "my-data" (cons 'new-item (fetch "my-data")))
```

Under the hood:

- **Names** are strings that map to object refs via a persistent index (a B-tree or hash table stored on the block device).
- **Serialization** uses the same format as the system image ([spec/06-runtime.md § 9](../spec/06-runtime.md)): tagged words with relocation. But individual objects are serialized, not the whole heap.
- **Versioning:** Each `store` creates a new version. Old versions are retained until explicitly purged or until storage pressure triggers GC.
- **Lazy loading:** Objects are loaded into HBM (cold heap) on first access. The pager moves hot objects into cluster/tile SRAM.

### 7.2 Collections and Namespaces

```lisp
;; Collections are like directories
(make-collection "projects")
(store "projects/lm1/spec" spec-doc)
(store "projects/lm1/tests" test-suite)

;; List a collection
(list-collection "projects/lm1")   ; => ("spec" "tests")

;; The collection itself is just an object (a hash table of names → refs)
```

### 7.3 Block-Level Layer

The persistent store talks to block devices via:

```lisp
(defgeneric block-read (device block-number buffer))
(defgeneric block-write (device block-number buffer))
```

On the emulator, the "block device" dispatches to the emulator's host-file I/O traps. On real hardware, it would dispatch to an NVMe driver.

The block layer manages:
- **Space allocation:** bitmap allocator for 4K blocks
- **Write journaling:** log-structured writes for crash safety
- **Caching:** recently read blocks cached in HBM

## 8. Networking

### 8.1 Model

Networking is just message passing that crosses a chip or machine boundary.

```lisp
;; Create a network-accessible actor
(defactor echo-server ()
  (:echo (sender message)
    (send sender message)))

;; The network layer routes messages to/from remote machines
(register-service "echo" (make-actor 'echo-server))

;; Remote call from another machine:
;; (ask (remote "machine2" "echo") :echo "hello")
```

### 8.2 Protocol Stack

```
┌─────────────────┐
│  Actor Messages  │   Lisp objects
├─────────────────┤
│  Serialization   │   Object → bytes (tagged word format)
├─────────────────┤
│  Framing         │   Length-prefixed frames
├─────────────────┤
│  Transport       │   TCP or custom reliable protocol
├─────────────────┤
│  Link            │   Ethernet (100GbE on real HW, TCP socket on emulator)
└─────────────────┘
```

The serialization layer is the same code used for the persistent store — serialize a Lisp object to bytes, deserialize on the other end. References to shared objects (symbols, classes) are resolved via the global symbol table.

## 9. The REPL and User Interface

### 9.1 The Listener

The primary user interface is the **Listener** — a REPL that is also an actor:

```lisp
(defactor listener ((package *user-package*)
                     (history '())
                     (prompt "LM-1> "))
  (:input (line)
    (let* ((form (read-from-string line))
           (result (eval form)))
      (push (cons form result) history)
      (send *console* :print (format nil "~A~%" result))
      (send *console* :print prompt))))
```

`eval` compiles the form to LM-1 code (using the compiler, which is itself a Lisp program running on LM-1), executes it, and returns the result.

### 9.2 The Inspector

```lisp
(inspect some-object)
;; Prints:
;;   #<VECTOR {shape: 0x0042} length: 3>
;;     [0]: 42
;;     [1]: "hello"
;;     [2]: #<CLOSURE add-one>
;;   
;;   Commands: (i N) inspect element, (m) show methods, (s) show shape, (q) quit
```

The inspector uses the tagged word model: it reads the header, determines the shape, walks the fields, and pretty-prints everything. It's ~100 lines of Lisp.

### 9.3 The Debugger

When an unhandled condition occurs, the **debugger** is invoked:

```
Error: DIVISION-BY-ZERO in function FOO at line 42
  
  0: [ABORT]      Return to Listener
  1: [USE-VALUE]  Supply a value to use instead
  2: [RETRY]      Retry the division with new arguments

  Backtrace:
    0: (/ X Y) where X = 10, Y = 0
    1: (FOO 10 0)
    2: (BAR)  
    3: (LISTENER :input "(bar)")

Debug> 
```

The debugger uses:
- Stack walking (FP chain) for the backtrace
- Register inspection (all values are tagged, so printable)
- The condition system's restart mechanism for recovery options
- Direct memory access for `(inspect)` on any value in any frame

## 10. Built-In Tools (Shipped with OS)

| Tool | Description |
|------|-------------|
| **Listener** | REPL with history, tab-completion |
| **Inspector** | Object structure viewer |
| **Debugger** | Condition handler with restarts, backtrace, frame inspection |
| **Compiler** | Lisp → LM-1 compiler (the primary development tool) |
| **Disassembler** | LM-1 code → human-readable assembly |
| **Profiler** | Per-function allocation, dispatch, and GC metrics |
| **Tracer** | Trace function calls (installs IC wrappers) |
| **Stepper** | Step through Lisp forms (source-level stepping) |
| **Editor** | Structural editor (operates on S-expressions, not text lines) |
| **Package browser** | Navigate the symbol/package namespace |
| **Class browser** | Navigate the class hierarchy |
| **GC dashboard** | Real-time nursery fill rates, promotion rates, pause times |
| **Tile monitor** | Show per-tile utilization, queue depths, actor counts |
| **Network monitor** | Active connections, message rates |
| **Store browser** | Navigate the persistent object store |

## 11. OS Size Estimate

| Component | Lines of Lisp (est.) |
|-----------|:--------------------:|
| Tile runtime (trap handlers, GC) | 2,000 |
| Object system (shapes, classes, dispatch) | 1,500 |
| Condition system | 500 |
| Scheduler (actors, work stealing) | 1,000 |
| Storage (persistent object store) | 1,500 |
| Networking (serialization, transport) | 1,000 |
| Compiler (reader + expander + codegen) | 3,000 |
| REPL / Listener | 300 |
| Inspector | 200 |
| Debugger | 500 |
| Other tools | 1,000 |
| **Total** | **~12,500** |

That's a small, readable system. For comparison, Genera's OS was ~1M lines — but it included a window system, a document system, email, and 30 years of features. Our starting point is deliberately minimal.

## 12. Boot Sequence (Full)

```
Power-on / Emulator start
    │
    ▼
BIOS (assembly)
    ├── Init tile 0: stack, nursery, card table
    ├── Install BIOS trap table
    ├── Install header templates
    ├── Load OS image from block storage into HBM
    └── CALL.DIRECT os-entry
         │
         ▼
OS init (Lisp — first code to run in the language)
    ├── Replace trap table with OS handlers
    ├── Initialize production GC
    ├── Initialize object system (shape table, class hierarchy)
    ├── Build symbol table (intern core symbols)
    ├── Initialize compiler
    ├── Initialize scheduler (create actor queues)
    ├── Bring up additional tiles:
    │     for each tile 1..N:
    │       ├── Configure SRAM regions
    │       ├── Install trap table (via message to tile)
    │       ├── Start tile scheduler thread
    │       └── Add tile to the available-tile pool
    ├── Initialize storage layer
    ├── Initialize network layer (if available)
    ├── Start system actors:
    │     ├── GC monitor actor
    │     ├── Tile monitor actor
    │     └── Storage manager actor
    └── Start Listener actor
         │
         ▼
"LM-1 Lispos v0.1 on 256 tiles (1024 threads)
 Type (help) for help.
 
 LM-1> "
```

## 13. Development Roadmap

| Milestone | What | Enables |
|:---------:|------|---------|
| **OS-0** | Boot to Listener on 1 tile. eval, print, read work. No GC (just fill nursery and die). | "Hello world" |
| **OS-1** | + production nursery GC. Programs can allocate freely. | Real Lisp programs |
| **OS-2** | + object system (defclass, defgeneric, defmethod). IC miss handler does real lookup. | CLOS-style dispatch |
| **OS-3** | + condition system. Errors are catchable, not fatal. | Robust REPL |
| **OS-4** | + multi-tile scheduler. Spawn actors across tiles. | Parallelism |
| **OS-5** | + persistent object store. Store/fetch objects across sessions. | State survives reboot |
| **OS-6** | + compiler self-hosting on LM-1. No longer need cross-compilation. | Self-sustaining system |
| **OS-7** | + debugger, inspector, profiler, editor. | Productive development environment |
| **OS-8** | + networking. Multi-machine operation. | Distributed system |
