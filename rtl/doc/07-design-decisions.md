# LM-1 Design Decisions & Architectural Comparisons

## 1. Multi-Cycle FSM vs Pipelined Core

### The Decision

The LM-1 uses a multi-cycle FSM (46+ states) instead of a pipelined
architecture. Each instruction occupies the full datapath for its entire
execution.

### Why

1. **Area**: A 5-stage pipeline requires pipeline registers, hazard detection,
   forwarding muxes, and branch prediction logic. The FSM core has none
   of these. At 64 tiles per chip, area savings compound enormously.

2. **Verification**: FSMs are easier to formally verify. There are no
   hazard corner cases, no pipeline flush bugs, no speculative execution
   security issues (Spectre/Meltdown are architecturally impossible).

3. **FGMT masks latency**: With 4 hardware threads interleaving round-robin,
   effective throughput approaches that of a simple pipeline. The FSM's
   multi-cycle penalty is hidden by thread switching.

4. **Power**: No speculative work is done, ever. No branch predictor power.
   No pipeline register switching activity on NOP bubbles.

5. **Determinism**: Instruction timing is perfectly deterministic. No
   cache-timing side channels. No pipeline-state-dependent latency variations.

### What's Lost

- **Single-thread IPC**: A simple instruction (ADD) takes 4-5 cycles vs 1 in a
  pipelined design. Single-thread performance is 4-5× worse.
- **Frequency**: The critical path through the FSM's combinational logic may be
  longer than a pipelined stage, limiting clock frequency.

### Comparison: RISC-V Cores

| Aspect | LM-1 | PicoRV32 (multi-cycle) | Rocket (5-stage) | BOOM (OoO) |
|--------|------|------------------------|------------------|------------|
| Pipeline | FSM | FSM | 5-stage in-order | 6+ stage OoO |
| IPC (single thread) | ~0.2 | ~0.25 | ~0.8 | ~2+ |
| Area (relative) | 1× | ~1× | ~5× | ~20× |
| Hazard logic | None | None | Forwarding | Full scoreboard |
| Threads | 4 FGMT | 1 | 1 | 1 |
| Effective IPC | ~0.8 | ~0.25 | ~0.8 | ~2+ |

The LM-1's effective throughput with FGMT is competitive with simple in-order
pipelines, at a fraction of the area per core.

---

## 2. Tagged Words vs Conventional Types

### The Decision

Every 64-bit value carries a 3-bit type tag. The hardware interprets these
tags on every arithmetic and memory operation.

### Why

1. **No type erasure**: In conventional CPUs running dynamic languages,
   types are lost at the ISA level. Every operation must be preceded by
   a software type check. In the LM-1, type checking is hardware-native.

2. **Single-cycle dispatch**: `is_fixnum(w)` is a single AND gate
   (`!w[0]`). `is_ref(w)` is two gates. No memory lookup, no branch.

3. **GC-aware**: The hardware can distinguish references from non-references
   without consulting a type map or object header. The scanner, copier,
   and write barrier all use `is_any_ref()` — a 2-gate test.

4. **Safety**: It is architecturally impossible to use a fixnum as a pointer
   or dereference a non-reference. The hardware traps on type violations.

### Comparison: Other Tagged Architectures

| Architecture | Tag Width | Location | Tag Scheme |
|-------------|-----------|----------|------------|
| **LM-1** | 3 bits | Low bits of word | Fixnum (1-bit), ref/cons/special/header (3-bit) |
| **SPARC M7 (ADI)** | 4 bits | Metadata per 16 bytes | Pointer coloring for use-after-free |
| **CHERI** | 1 bit | Out-of-band capability tag | Fat pointers (128/256-bit capabilities) |
| **Lisp Machines** (MIT CADR) | 5+ bits | Separate tag bus | Full type tag per word |
| **ARM MTE** | 4 bits | Top-byte metadata | Memory safety tags |
| **Mill CPU** | 2 bits | Metadata per operand | NaR (Not-a-Result) + None |
| **JavaScript engines** (NaN-boxing) | varies | Encoded in NaN payload | 64-bit float NaN = 51-bit payload |
| **Lua/Ruby** (tagged union) | varies | In-band union discriminant | Software-only, not ISA-visible |

**Key differences from Lisp Machines:**
- The LM-1 uses a 3-bit in-band tag (stealing bits from fixnum range),
  while CADR used a separate 5-bit tag bus. The LM-1 approach is simpler
  (no extra memory width) but limits fixnum range to 63 bits.
- The LM-1 does not have microcode. CADR had extensive type-dispatching
  microcode. The LM-1 uses hardwired FSM logic.
- The LM-1 separates GC into dedicated engines. CADR ran GC on the main
  processor.

**Key differences from CHERI:**
- CHERI tags are 1-bit per capability (pointer), enforcing spatial memory
  safety. The LM-1 tags are 3-bit per word, enforcing type safety for
  a GC'd language runtime. Different goals: CHERI secures C/C++; the
  LM-1 accelerates Lisp/Smalltalk/JS.

---

## 3. Hardware GC Engines vs Software GC

### The Decision

Three dedicated FSM engines (scanner, copier, fixup) perform GC operations
on a separate SRAM port, running concurrently with mutator threads.

### Why

1. **Concurrent execution**: The dual-port SRAM allows GC to run without
   stalling any CPU core. Software GC on a shared-memory system must
   either stop the world or use complex concurrent algorithms with
   read/write barriers on every memory access.

2. **Predictable cost**: Each GC operation has deterministic cycle count
   (proportional to region size). No cache miss variability, no TLB miss,
   no OS scheduling interference.

3. **No GC thread**: GC doesn't consume a CPU thread. All 256 threads
   are available for application work.

4. **Bandwidth**: The scanner reads memory at line rate (1 word per 2 cycles).
   A software scanner would need to execute load + compare + branch
   per word (~8+ cycles each on this FSM core).

### Comparison: Azul Vega

The closest commercial precedent is Azul Systems' Vega processor (2005),
designed for Java:

| Aspect | LM-1 | Azul Vega |
|--------|------|-----------|
| Target language | Lisp/Smalltalk/JS | Java |
| GC hardware | 3 fixed-function engines | Read barrier in load pipeline |
| Approach | Offload GC to dedicated engines | Augment CPU loads with GC checks |
| Concurrent? | Yes (dual-port SRAM) | Yes (read barrier intercepts) |
| Write barrier | Hardware (ST.WB + card table) | Software (compiler-inserted) |
| Cores | 64 (FGMT ×4 = 256 threads) | 54 cores |
| SRAM design | Dual-port for GC | Main memory + cache |

**Key difference**: Vega intercepted every load with a GC read barrier
(checking if the loaded reference points to a relocated object). The LM-1
instead does bulk fixup after copying, which is less incrementally concurrent
but simpler in hardware. Vega's approach allowed truly pauseless GC; the
LM-1's approach has brief pauses for command sequencing but pushes the
bulk work to hardware engines.

---

## 4. FGMT vs Other Multithreading Approaches

### The Decision

4 hardware threads per core with round-robin switching at every instruction
boundary.

### Comparison

| Approach | Examples | Switch Point | HW Cost | Latency Hiding |
|----------|---------|-------------|---------|----------------|
| **FGMT** (LM-1) | SPARC T1/T2, Tera MTA, LM-1 | Every cycle/instruction | N × register file | Best |
| **SMT** | Intel HT, IBM POWER | Superscalar dispatch | Shared + per-thread | Moderate |
| **Coarse-grained MT** | Itanium, some GPUs | On stall (cache miss) | N × register file | Stall-only |
| **Single-thread** | ARM Cortex-M, RISC-V CV32 | N/A | 1× register file | None |

The LM-1 follows the SPARC T-series philosophy: many simple threads per core
instead of a few complex cores. But the LM-1 takes it further by eliminating
the pipeline entirely — the FGMT switching is at FSM instruction boundaries,
not pipeline stages.

**SPARC T1 comparison:**
- T1: 8 cores × 4 FGMT threads = 32 threads, 6-stage pipeline
- LM-1: 64 cores × 4 FGMT threads = 256 threads, multi-cycle FSM
- T1 has higher single-thread IPC but fewer total threads
- LM-1 has lower single-thread IPC but 8× the thread density

### Why Not SMT?

SMT (Simultaneous Multithreading, like Intel Hyper-Threading) shares a
superscalar pipeline between threads. The LM-1 has no superscalar execution,
no out-of-order logic, no rename registers — there's nothing to share. FGMT
is the natural choice for a simple FSM core.

---

## 5. Allocation as a Primitive

### The Decision

Object allocation (ALLOC, ALLOC_CONS, ALLOCV, ALLOC_CLOSURE) is a first-class
ISA operation, not a library call.

### Why

1. **Speed**: A 4-word allocation takes ~16 cycles in hardware. A software
   allocator would need: nursery pointer load, size computation, limit check,
   header store, zero fill loop, pointer update — dozens to hundreds of cycles.

2. **Atomicity**: The hardware allocation sequence is non-interruptible.
   No thread can see a partially-initialized object. This eliminates an
   entire class of concurrent GC bugs.

3. **GC integration**: The nursery overflow trap is hardware-triggered,
   directly initiating GC. No polling, no safepoints needed.

4. **Template table**: Pre-formed headers avoid runtime construction.
   The shape_id, size, and subtype are pre-computed at class loading time.

### Comparison: Conventional Architectures

On x86/ARM/RISC-V, allocation is:
```c
// Software allocator (simplified bump allocator)
ptr = nursery_ptr;                 // Load global pointer
nursery_ptr += size;               // Advance
if (nursery_ptr > nursery_limit)   // Check overflow
    gc_collect();                  // Major overhead: function call
return ptr;                        // Return
// ... then initialize header and fields separately
```

On the LM-1:
```
ALLOC r1, #template_idx
; Done. Object created, header written, fields zeroed.
; GC triggered automatically if nursery full.
```

### Comparison: JVM Hardware

Various JVM acceleration chips (picoJava, Jazelle) had bytecode-level
allocation support. The LM-1 differs by being designed for dynamically-typed
languages (not Java's static types) and by integrating allocation with
the GC hardware (template table + nursery overflow trap + GC engines).

---

## 6. No Flags Register

### The Decision

The LM-1 has no condition codes / flags register (no carry, zero, negative,
overflow flags).

### Why

1. **Tagged arithmetic**: Overflow is a trap, not a flag. `ADD.FIX` either
   succeeds (result is a valid fixnum) or traps. There's no need to check
   flags after the fact.

2. **Comparison results**: `CMP_TAGGED` returns a tagged value (-1/0/+1),
   not flags. `BR.COND` tests registers directly (truthy? nil? fixnum<0?).

3. **Thread switch simplicity**: No flags to save/restore on thread switch.
   The only per-thread state is PC + 32 registers.

4. **Determinism**: No implicit state that could leak information between
   operations.

### Comparison

| Architecture | Condition Mechanism |
|-------------|-------------------|
| x86 | FLAGS register (CF, ZF, SF, OF) — set by most ALU ops |
| ARM | CPSR condition codes — set by S-suffix instructions |
| RISC-V | No flags — branch instructions compare registers directly |
| **LM-1** | No flags — tagged traps for overflow, register tests for branches |
| MIPS | No flags — branch-on-register, HI/LO for mul/div |
| Mill | No flags — predication via NaR metadata |

The LM-1 is closest to RISC-V and MIPS in this regard, but goes further
by eliminating even the implicit overflow detection (replaced by traps).

---

## 7. No Virtual Memory / No TLB

### The Decision

The LM-1 uses physical addressing with no virtual memory, no page tables,
and no TLB.

### Why

1. **GC manages memory**: In a GC'd runtime, the GC is responsible for
   memory allocation and reclamation. Virtual memory protection (preventing
   use-after-free, enforcing isolation) is redundant when the runtime
   already guarantees these properties through type safety and GC.

2. **Deterministic timing**: TLB misses cause unpredictable latency stalls.
   The LM-1's SRAM access latency is always exactly 1 cycle.

3. **Area**: A TLB with its CAM (content-addressable memory) and page
   table walker is a significant area cost. With 64 tiles, this savings
   is substantial.

4. **No OS**: The LM-1 is designed for a bare-metal language runtime,
   not a general-purpose OS. Inter-process isolation is provided by the
   language runtime (capability bits in references), not by hardware
   page tables.

### What's Lost

- No demand paging (all code/data must fit in SRAM)
- No memory-mapped I/O (dedicated I/O interface needed)
- No OS-level process isolation (single runtime only)
- No DRAM support without external memory controller

### Comparison: GPU Memory Models

GPUs similarly use physical addressing within their local memory spaces
(shared memory, local memory). The LM-1's tile SRAM is analogous to
GPU shared memory — fast, local, physically addressed. Cluster SRAM
is analogous to GPU L2 cache — shared, physically addressed, higher
latency.

---

## 8. Inline Caching in Hardware

### The Decision

A 64-entry fully-associative IC table provides hardware-accelerated method
dispatch for dynamically-typed languages.

### Why

Dynamic languages (Smalltalk, JavaScript, Ruby, Python) resolve method
calls at runtime based on the receiver object's type. Without caching,
each call requires a hash table lookup or vtable indirection.

The LM-1's IC table caches `(callsite_pc, shape_id) → target_address`.
A cache hit resolves dispatch in ~4 cycles (read header + lookup). A miss
traps to software for full resolution (~100+ cycles), then installs the
result for future hits.

### Comparison: Software IC in V8/SpiderMonkey

Modern JavaScript engines implement inline caching in JIT-compiled code:

```javascript
// Software IC (JIT-generated machine code):
cmp [obj + shape_offset], cached_shape  // Compare shape
jne ic_miss_handler                       // Branch on mismatch
call cached_target                        // Fast path
```

This requires:
1. A JIT compiler to generate the comparison code
2. Self-modifying code (patching the cached shape/target)
3. Branch prediction to avoid pipeline stalls on the compare

The LM-1 replaces all of this with a hardware lookup table:
- No JIT compiler needed
- No self-modifying code
- No branch prediction (deterministic FSM)
- 64 entries × 4 bytes each = 256 bytes of on-chip storage

### Monomorphic vs Polymorphic

The LM-1's IC table is **polymorphic** — multiple `(pc, shape)` pairs can
coexist for the same callsite. If a callsite sees objects of 3 different
shapes, all 3 entries can be cached. This is equivalent to a polymorphic
inline cache (PIC) in software JIT terminology.

---

## 9. No Cache Coherence

### The Decision

There is no cache coherence protocol between tiles or clusters.

### Why

1. **Shared-nothing per tile**: Each tile has private SRAM. There is no
   data cache (only I-Cache), so there's nothing to keep coherent.

2. **Cluster SRAM is uncached**: Tile accesses to cluster SRAM go through
   the crossbar directly to SRAM. No caching, no stale copies.

3. **Message passing**: Inter-tile communication uses hardware message
   queues, not shared memory. The programming model is actor-style, not
   shared-state.

4. **I-Cache is read-only**: Code is assumed immutable after loading.
   No coherence needed for instruction caches.

### What This Means

- No MESI/MOESI protocol
- No snooping or directory-based coherence
- No cache line invalidation broadcasts
- No false sharing performance pitfalls
- Vastly simpler interconnect (crossbar instead of coherent mesh)

### Comparison

| Architecture | Coherence | Programming Model |
|-------------|-----------|-------------------|
| x86/ARM SMP | Full (MESI) | Shared memory |
| GPU (NVIDIA) | L2 coherent, L1 per-SM | SIMT + shared mem |
| **LM-1** | None | Message passing + GC'd heap |
| Epiphany (Adapteva) | None | Shared global memory, local scratchpad |
| Tilera TILE-Gx | Directory-based | Shared memory |

The LM-1 is closest to the Epiphany model: local scratchpad per core,
shared memory via crossbar, no coherence. But the LM-1 adds hardware
GC to manage the shared heap.

---

## 10. Comparison Summary

### vs. RISC-V (RV64I)

| Feature | RISC-V | LM-1 |
|---------|--------|------|
| Word width | 64-bit untyped | 64-bit tagged |
| Registers | 32 (x0=0) | 32 (no zero reg) × 4 threads |
| Pipeline | Varies (1-stage to OoO) | Multi-cycle FSM |
| Flags | None | None |
| Virtual memory | Sv39/48/57 | None |
| Cache coherence | Optional (RISC-V SMP) | None |
| Allocation | Software | Hardware (ALLOC instruction) |
| GC support | None | Dedicated engines |
| Method dispatch | Software | Hardware IC table |

### vs. x86-64

| Feature | x86-64 | LM-1 |
|---------|--------|------|
| ISA philosophy | CISC, backward-compatible | Domain-specific, clean-sheet |
| Type system | Untyped registers | Tagged words |
| Memory model | Strongly ordered (TSO) | Sequentially consistent (per tile) |
| Speculation | Deep (Spectre-vulnerable) | None (no speculation) |
| GC | Software (JVM/CLR) | Hardware |
| Area per core | ~10 mm² | ~0.1 mm² (estimated) |
| Threads per core | 2 (SMT) | 4 (FGMT) |

### vs. GPU (NVIDIA CUDA Core)

| Feature | GPU Core | LM-1 Core |
|---------|----------|-----------|
| Typing | Untyped (float/int by instruction) | Tagged (hardware type system) |
| Threading | SIMT (32-wide warps) | FGMT (4 independent threads) |
| Memory | Hierarchy (registers → shared → L2 → DRAM) | Flat (SRAM only) |
| Control flow | Warp divergence penalty | Independent per thread |
| GC | None (manual memory) | Hardware engines |
| Workload | Data-parallel | Task-parallel (actor model) |

### vs. Lisp Machines (Symbolics 3600, MIT CADR)

| Feature | Lisp Machine | LM-1 |
|---------|-------------|------|
| Era | 1980s | 2024 |
| Tagged words | Yes (5+ bit separate tag) | Yes (3-bit in-band tag) |
| Microcode | Yes (extensive) | No (hardwired FSM) |
| GC | Software (on same CPU) | Hardware (dedicated engines) |
| Multiprocessing | None/limited | 256 threads, 64 cores |
| Memory | Virtual (Genera OS) | Physical only |
| Allocation | Microcode-assisted | Hardware FSM |
| Instruction set | Stack-based | Register-based (32 GPRs) |

The LM-1 is spiritually the descendant of Lisp Machines, updated for
modern silicon realities: many simple cores instead of one complex one,
hardware GC instead of software, and a register ISA instead of a stack ISA.

### vs. SPARC T-Series (T1/T2/T4)

| Feature | SPARC T-series | LM-1 |
|---------|---------------|------|
| FGMT threads | 4-8 per core | 4 per core |
| Pipeline | 6-stage in-order | Multi-cycle FSM |
| Target | General-purpose server | GC'd language runtime |
| Typing | Untyped | Tagged |
| GC support | None | Hardware engines |
| Cores | 8-16 | 64 |
| Process node | 65-28 nm | Target: modern FinFET |

The SPARC T-series pioneered throughput-oriented FGMT design for servers.
The LM-1 applies the same philosophy but specializes for language runtime
acceleration with tagged words and GC hardware.

---

## 11. What Architectures Influenced the LM-1?

| Influence | What Was Taken | What Was Changed |
|-----------|---------------|-----------------|
| **Lisp Machines** | Tagged words, type-directed execution | Removed microcode; added hardware GC |
| **SPARC T-series** | FGMT threading model | Simplified to FSM core (no pipeline) |
| **Azul Vega** | Hardware GC concept | Separate GC engines instead of read barriers |
| **RISC-V** | Clean register ISA, no flags | Added tags, allocation, IC table |
| **GPUs** | Many-core throughput design | Independent threads (not SIMT) |
| **Erlang/Actor model** | Message-passing concurrency | Hardware message queues |

The LM-1 synthesizes these influences into something that doesn't exist elsewhere:
a throughput-oriented, tagged-word, GC-hardware-equipped, many-core processor
designed from the ground up for garbage-collected dynamic languages.
