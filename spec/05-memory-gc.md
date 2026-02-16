# LM-1 Memory Model, GC Invariants, and Barrier Protocol

**Spec ID:** LM1-SPEC-05  
**Revision:** 0.1-draft  
**Date:** 2026-02-16

---

## 1. Scope

This document specifies the memory model, garbage collection invariants, write-barrier protocol, and GC algorithmic framework for the LM-1 architecture. These are ISA-level contracts: any conforming implementation MUST uphold these invariants regardless of the specific GC algorithm chosen.

## 2. Memory Hierarchy from the GC Perspective

```
┌─────────────────────────────────────────────────────┐
│                    GC View of Memory                 │
│                                                      │
│  ┌─────────────┐   ┌─────────────┐   ┌───────────┐  │
│  │   Nursery   │   │  Old-Gen    │   │ Cold Heap │  │
│  │  (per-tile) │   │(per-cluster)│   │  (HBM)    │  │
│  │             │   │             │   │           │  │
│  │ Gen 0       │   │ Gen 1       │   │ Gen 2     │  │
│  │ Bump alloc  │   │ Free-list / │   │ Mark-     │  │
│  │ Copy-collect│   │ copy-compact│   │ compact   │  │
│  └──────┬──────┘   └──────┬──────┘   └─────┬─────┘  │
│         │                 │                 │        │
│    tile SRAM        cluster SRAM          HBM       │
│   (256 KiB)          (2 MiB)          (32 GiB)      │
└─────────────────────────────────────────────────────┘
```

### 2.1 Generation Boundaries

| Generation | Location | Typical Size | Collection Strategy |
|:----------:|----------|:------------:|-------------------|
| **Gen 0** (Nursery) | Tile SRAM | 64 KiB per tile | Copying (bump evacuate to Gen 1) |
| **Gen 1** (Old-Gen) | Cluster shared SRAM | 1.5 MiB per cluster | Copy-compact (within cluster) or promote to Gen 2 |
| **Gen 2** (Cold Heap) | HBM | Up to 32 GiB | Mark-compact (rare, incremental) |

## 3. Fundamental GC Invariants

These are the rules the **hardware** enforces and the **runtime** depends upon.

### Invariant 1: Precise Pointer Identification

> **Every word in the heap, stack, and registers can be unambiguously classified as a pointer or non-pointer by inspecting the tag bits.**

This is guaranteed by the tagged word model ([01-object-model.md](01-object-model.md)). The GC never needs conservative scanning.

### Invariant 2: Object Header Integrity

> **Every live heap object starts with a valid header word (tag `111`) or a forwarding pointer (tag `111`, GC bits = `0xFF`). The header encodes the object's size and layout.**

This allows the GC (and movement engines) to walk the heap linearly, determining object boundaries and pointer-containing fields.

### Invariant 3: Write Barrier Coverage

> **Every store of a ref into a heap object field that is already reachable MUST use `ST.WB` (or an equivalent barriered operation).**

Unbarriered stores (`ST`) are only permitted for:
- Initializing fields of a freshly allocated, not-yet-reachable object (between `ALLOC` and the point where the ref to the new object is stored into another reachable object)
- Storing non-ref values (fixnums, specials, characters)
- Stores to stack locations (the stack is scanned as a root, not via barriers)

### Invariant 4: Nursery Isolation

> **A nursery object is only reachable from: (a) nursery-local refs, (b) stack roots, (c) register roots. No old-gen object may point to a nursery object unless the corresponding card/remembered-set entry exists.**

This allows nursery collection to scan only roots + remembered sets, not the entire old-gen.

### Invariant 5: Forwarding Pointer Protocol

> **During GC, when an object is moved, a forwarding pointer MUST be installed at the old location. Any access to the old location MUST follow the forwarding pointer to the new location before the mutation cycle resumes.**

The movement engines handle this automatically. The fixup pass (via `ENQ.FIXUP`) resolves all forwarding pointers in a region.

### Invariant 6: GC-Safe Points

> **All trap entries (except `TRAP_NURSERY_OVERFLOW`) are GC-safe points: the runtime may perform GC before returning from a trap handler.**

This means all live pointers must be in registers or on the stack at trap boundaries. The compiler MUST NOT keep untagged derived pointers across potential trap points.

---

## 4. Write Barrier Protocol

### 4.1 Barrier Semantics

When `ST.WB Rs, #field, Rt` executes:

```
store(untag_ref(Rs) + (field+1)*8, Rt)

if is_ref(Rt):
    src_gen = generation(Rs)    // from ref metadata or region lookup
    dst_gen = generation(Rt)    // from ref metadata or region lookup
    if dst_gen < src_gen:       // younger-to-older pointer: cross-gen ref
        card_mark(effective_addr(Rs), field)
```

**"Cross-generation" means:** The *stored ref* (Rt) points to a *younger* generation than the *container object* (Rs). This is the interesting case because the nursery collector needs to know about old→young pointers.

### 4.2 Card Table Structure

Each tile maintains a **card table** for its local SRAM. Each cluster maintains card tables for the cluster shared SRAM.

| Parameter | Value |
|-----------|-------|
| Card size | 64 bytes (8 words) |
| Card table entry | 1 byte |
| Entry values | `0x00` = clean, `0xFF` = dirty |
| Tile card table size | 256 KiB / 64 = 4096 entries = 4 KiB |
| Cluster card table size | 2 MiB / 64 = 32768 entries = 32 KiB |

**Card-mark operation:**

```
card_index = (store_address - region_base) >> 6    // divide by card size
card_table[card_index] = 0xFF
```

This is computed by the hardware's barrier logic in the MA stage and written as a byte store.

### 4.3 Remembered Sets (Optional Enhancement)

In addition to card tables, an implementation MAY maintain **per-object remembered sets** — precise lists of incoming cross-generation pointers. This trades space for a faster nursery scan (no need to re-scan entire dirty cards).

Remembered sets are stored in tile or cluster SRAM. The `ST.WB` barrier logic appends entries to a remembered-set log buffer:

```
rs_log[rs_log_top++] = { container_ref: Rs, field: field }
```

If the log buffer overflows, `TRAP_BARRIER_OVERFLOW` fires, and the runtime flushes the log to a more compact structure.

### 4.4 Barrier Filtering

The hardware barrier applies **two fast filters** before marking:

1. **Non-ref filter:** If `Rt` is not a ref (bit 0 = 0, or tag ≠ `x01`), no barrier needed. This is the most common filter — most stores are fixnums or specials.
2. **Same-generation filter:** If the generation bits in `Rs` and `Rt` metadata match, no barrier needed. This is the second most common case — most pointers are to same-generation objects.

Only when both filters fail (cross-generation ref store) does the card-mark/RS-log write occur. Empirically, this happens in <5% of all stores in typical Lisp programs.

---

## 5. GC Algorithmic Framework

The ISA and movement engines support multiple GC algorithms. This section describes the **reference algorithm**: a generational, mostly-concurrent, mostly-copying collector.

### 5.1 Nursery Collection (Gen 0 → Gen 1)

**Trigger:** `TRAP_NURSERY_OVERFLOW` on a tile.

**Algorithm:** Stop-the-tile copying collection.

```
1. PAUSE the tile's mutator threads (other tiles continue)
2. SCAN ROOTS:
   - All registers and stack frames of the tile's 4 HW threads
   - The tile's card table (for old→young pointers from cluster SRAM)
   - The tile's remembered-set log (if applicable)
3. For each root pointer into the nursery:
   a. If already forwarded: update root to forwarding address
   b. Else: copy object to cluster old-gen (via DMA or direct write)
          Install forwarding pointer at old location
          Recursively scan the copied object's fields
4. RESET nursery: np = nursery_base
5. CLEAR card table
6. RESUME the tile's mutator threads
```

**Latency:** Proportional to the number of live nursery objects. With a 64 KiB nursery and typical Lisp survival rates (~5–15%), this is tens of microseconds.

**Concurrency:** Only the affected tile pauses. All other tiles continue executing. The cluster crossbar handles the DMA of survivors to shared SRAM concurrently with other tiles' memory traffic.

### 5.2 Old-Gen Collection (Gen 1)

**Trigger:** Cluster old-gen region usage exceeds a threshold (e.g., 75% full).

**Algorithm:** Concurrent mark-copy using movement engines.

```
Phase 1: MARK (concurrent with mutators)
   a. Enqueue ENQ.SCAN for the old-gen region
   b. Scanner engine walks all objects, identifies live refs
   c. Mutator barriers (ST.WB) ensure new cross-gen refs are captured

Phase 2: COPY (mostly concurrent)
   a. FENCE.GC on all cluster tiles (brief pause to drain in-flight barriers)
   b. Enqueue ENQ.COPY for the old-gen region → new old-gen region
   c. Copier engine copies live objects, installs forwarding pointers

Phase 3: FIXUP (concurrent)
   a. Enqueue ENQ.FIXUP with the pointer list from Phase 1 + forwarding table from Phase 2
   b. Fixup engine updates all pointers in tile nurseries, stacks, and the new old-gen region
   c. Tile nursery card tables are rescanned to update any old→old-gen pointers

Phase 4: RECLAIM
   a. Old old-gen region is freed
   b. Long-surviving objects may be promoted to Gen 2 (HBM)
```

**Pause time:** The only synchronous pause is the `FENCE.GC` in Phase 2, which takes a few hundred nanoseconds (drain store buffers). All other work is concurrent.

### 5.3 Full-Heap Collection (Gen 2)

**Trigger:** HBM heap usage exceeds a threshold, or system-wide memory pressure.

**Algorithm:** Incremental mark-compact.

```
1. Incremental marking:
   - Run on dedicated GC tiles (or steal cycles from idle tiles)
   - Walk all objects in HBM, marking live objects via a bitmap
   - Use snapshot-at-the-beginning (SATB) write barrier:
     ST.WB logs the old value before overwriting, so the marker doesn't miss objects

2. Compaction (optional):
   - Slide live objects toward the beginning of the region
   - Update all pointers (full-heap fixup)
   - This is expensive and done rarely

3. Sweeping (alternative to compaction):
   - Build a free list from unmarked objects
   - Faster than compaction but causes fragmentation
```

**Frequency:** Rare (minutes to hours between full collections, depending on live-set size and allocation rate).

### 5.4 Cross-Tile Pointer Handling

When a tile stores a ref to another tile's nursery object, the write barrier fires (cross-generation). The remembered-set entry identifies the cross-tile pointer.

During nursery collection of the target tile:
1. The collecting tile scans its remembered sets.
2. For cross-tile entries, it sends a **pointer-update message** to the owning tile after forwarding.
3. The owning tile processes the update (either immediately via an interrupt, or lazily via a queue).

This is the main coordination cost of the distributed heap model. The system biases against cross-tile nursery pointers by preferring tile-local allocation for short-lived objects.

---

## 6. Memory Ordering

### 6.1 Intra-Tile Ordering

Within a single tile (and a single hardware thread), memory accesses are **sequentially consistent** — loads and stores are seen in program order. This is natural for an in-order core.

Across hardware threads on the **same tile**, memory accesses to shared SRAM are sequentially consistent (single-port SRAM with cycle-level arbitration).

### 6.2 Inter-Tile Ordering

Across tiles, the memory model is **relaxed** with explicit synchronization:

- **No global coherence.** Writes to one tile's SRAM are not automatically visible to other tiles.
- **Message ordering.** Messages sent via `SEND`/`RECV` have FIFO ordering per queue. A `SEND` followed by a `RECV` establishes happens-before.
- **DMA ordering.** DMA transfers are ordered with respect to the initiating tile: a DMA completion signal establishes happens-before between the DMA source and the initiating tile.
- **Atomics.** `CAS.TAGGED` and `FAA` on shared SRAM (cluster shared SRAM or HBM) provide SC-per-location semantics.
- **FENCE.GC.** Ensures all pending stores and barrier metadata updates are globally visible. Used only during GC phase transitions.

### 6.3 Implications for Lisp

For most Lisp code, the memory model is invisible:
- Single-threaded Lisp code runs on a single hardware thread, seeing sequential consistency.
- Actor-style message passing (the recommended concurrency model) uses `SEND`/`RECV`, which provide happens-before.
- Shared mutable state (which is rare in well-written Lisp) requires explicit atomics or locks.

---

## 7. Safepoint Protocol

### 7.1 Definition

A **safepoint** is a program point where:
1. All live refs are in registers or on the stack (no derived untagged pointers).
2. No half-completed allocation is in progress (either `ALLOC` completed, or it hasn't started).
3. The runtime is free to perform GC without data loss.

### 7.2 Automatic Safepoints

The following are automatic safepoints (no compiler annotation needed):
- Every trap entry
- Every `CALL.IC`, `CALL.DIRECT`, `CALL.CLOSURE` (before the call)
- Every `RET`
- Every `SEND`, `RECV`
- Every backward branch (loop back-edges)

### 7.3 Safepoint Polling

For long-running scalar loops without calls or backward branches (rare in Lisp), the compiler SHOULD insert explicit safepoint polls:

```asm
    LD.NL r_scratch, [tp, #gc_request]   ; load GC-request flag
    BR.T  r_scratch, gc_safepoint         ; if set, branch to safepoint handler
```

Alternatively, the runtime can trigger a safepoint by setting the tile's `nl` register to zero, causing the next `ALLOC` to trap.

---

## 8. Root Enumeration

At a safepoint, the GC must find all root pointers. These are:

### 8.1 Register Roots

All 32 GPRs per hardware thread. The hardware tags make scanning trivial: check each register's tag bits, and trace if it's a ref.

### 8.2 Stack Roots

The stack is walked frame-by-frame using the frame pointer chain. Each word on the stack is tag-checked. Since LM-1 uses tagged words everywhere, the stack is precisely scannable without stack maps.

**Note:** This is simpler than conventional architectures where stack maps (generated by the compiler) must identify which stack slots contain pointers. The tagged word model eliminates stack maps entirely.

### 8.3 Global Roots

- The symbol table (cluster shared SRAM)
- The method cache (may contain refs to code objects)
- Finalization queues
- I/O buffers containing refs

These are registered with the runtime and scanned during cluster-level or full-heap collections.

---

## 9. Finalization and Weak References

### 9.1 Weak References

Objects with header subtype `01010` (weak reference) contain fields that the GC does **not** trace for liveness. During collection:

1. If the referent is otherwise live, the weak ref is unchanged.
2. If the referent is dead, the weak ref field is replaced with `nil`.
3. If the referent was forwarded, the weak ref field is updated to the new address.

### 9.2 Finalizers

Objects may be registered with the runtime for finalization (running cleanup code before reclamation). The GC:

1. Identifies finalizable objects that are otherwise dead.
2. Marks them as live (to prevent premature reclamation).
3. Enqueues them on a finalization queue.
4. A finalizer thread runs the registered cleanup functions.
5. After finalization, the object becomes eligible for reclamation in the next cycle.

This is a **two-phase** reclamation model, similar to Java's finalization protocol.

---

## 10. GC Metrics and Monitoring

Each tile and cluster maintain hardware performance counters:

| Counter | Description |
|---------|-------------|
| `alloc_count` | Number of `ALLOC` instructions executed |
| `alloc_bytes` | Total bytes allocated |
| `nursery_collections` | Number of nursery overflows / minor GCs |
| `nursery_survivors` | Bytes surviving nursery collection |
| `barrier_fires` | Number of `ST.WB` instructions that triggered a card mark |
| `barrier_filtered` | Number of `ST.WB` instructions filtered (no action needed) |
| `ic_hits` | Number of `CALL.IC` hits |
| `ic_misses` | Number of `CALL.IC` misses |
| `engine_scan_words` | Words scanned by movement engine |
| `engine_copy_words` | Words copied by movement engine |
| `engine_fixup_count` | Pointers fixed up by movement engine |
| `gc_pause_cycles` | Total cycles spent in GC pauses (per tile) |

These counters are readable via `TILE.COUNTER Rd, #counter_id` (a supplementary system instruction).
