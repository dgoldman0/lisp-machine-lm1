# LM-1 Architecture Overview

**Spec ID:** LM1-SPEC-00  
**Revision:** 0.1-draft  
**Date:** 2026-02-16

---

## 1. Scope

This document defines the architectural overview and design rationale for the LM-1 many-tile dynamic-object processor. It establishes the vocabulary, design constraints, and system-level structure that all subsequent specification documents reference.

## 2. Motivation

### 2.1 The Problem with General-Purpose Hardware for Dynamic Languages

Modern general-purpose CPUs are designed around assumptions that poorly match dynamic-language workloads:

- **Static typing at the hardware level.** ALU operands have no type metadata; every dynamic type check becomes a multi-instruction software sequence.
- **Allocation is a library concern.** `malloc` is hundreds of instructions; Lisp allocates on nearly every function call.
- **GC is invisible to the ISA.** Write barriers, remembered sets, and object scanning are pure software overhead.
- **Dispatch is a branch.** Dynamic method lookup goes through software hash tables, IC stubs, and branch mispredictions.
- **Pointer chasing dominates.** Cons cells, closures, environments—the memory access pattern is inherently pointer-following, which defeats spatial prefetchers.

The result: dynamic languages on commodity hardware spend the majority of their cycles on *language overhead*, not *user computation*.

### 2.2 Why Not One Big Core?

A single large core with Lisp-aware extensions (the classic Lisp machine approach) faces diminishing returns:

- Die area spent on OoO windows, deep branch predictors, and wide decoders does not proportionally benefit pointer-chasing, allocation-heavy workloads.
- Memory latency dominates; a single core can only have so many outstanding misses.
- GC pauses scale with heap size; a single mutator thread stops entirely during collection.

### 2.3 The Tile Approach

Instead, LM-1 distributes work across **many small tiles**, each with:

- Enough compute for a few hardware threads running Lisp
- Enough local SRAM for a nursery, hot working set, and message queues
- A fast path to its cluster's shared SRAM and the global NoC

This matches demonstrated production architectures:

- Graphcore GC200: 1472 tiles × 624 KiB SRAM, no caches, explicit BSP programming model
- Tenstorrent Wormhole/Blackhole: many RISC-V cores × local SRAM, explicit NOCI movement
- Cerebras WSE: 850,000 cores × 48 KiB SRAM, fabric interconnect

LM-1 applies this structural insight to a *different workload*: not tensor math, but dynamic-object processing.

## 3. Architectural Layers

The LM-1 architecture is specified in layers:

```
┌─────────────────────────────────────────────┐
│           Runtime / Language Layer           │
│  (scheduler, method lookup, GC policy,      │
│   module system, REPL, debugger)             │
├─────────────────────────────────────────────┤
│              ISA Contract Layer              │
│  (object word model, instruction families,   │
│   trap conventions, GC invariants)           │
├─────────────────────────────────────────────┤
│         Core Microarchitecture Layer         │
│  (pipeline, tag unit, IC unit, nursery       │
│   allocator, barrier logic, thread contexts) │
├─────────────────────────────────────────────┤
│            SoC / Fabric Layer                │
│  (tiles, clusters, NoC, movement engines,    │
│   HBM controllers, I/O)                     │
└─────────────────────────────────────────────┘
```

Each layer defines a *contract* with the layer above:

- The **ISA contract** is what compilers and runtimes target.
- The **core microarchitecture** is one *implementation* of that ISA contract.
- The **SoC fabric** is one *arrangement* of those cores.

Multiple implementations of each layer are expected over time.

## 4. Key Architectural Decisions

### 4.1 64-bit Tagged Word

Every machine word is 64 bits. The low bits carry a type tag. This is the foundational decision: *the hardware always knows whether a value is a fixnum, a pointer, a character, or a special constant*. Details in [01-object-model.md](01-object-model.md).

### 4.2 Semantic Instruction Families (not opcode soup)

The ISA is organized into ~8 families that correspond to language-level operations, not microarchitectural operations. A `CALL.IC` is one instruction, not "load from IC table, compare shape, branch, push frame." Details in [02-isa.md](02-isa.md).

### 4.3 Allocation as a Primitive

`ALLOC` is an instruction, not a function call. The hardware maintains a bump-pointer nursery per tile. Overflow traps to the runtime. This makes cons/closure/vector allocation a ~1-cycle fast path. Details in [02-isa.md](02-isa.md) § 2.2.

### 4.4 Write Barriers in the Store Pipeline

`ST.WB` is a barriered store instruction. The hardware updates GC metadata (card tables, remembered sets) as part of the store pipeline. The mutator never manually calls barrier functions. Details in [05-memory-gc.md](05-memory-gc.md).

### 4.5 Inline Caches as Architectural State

Each `CALL.IC` site has an associated hardware inline-cache entry. On a hit (receiver shape matches), dispatch is a direct jump. On a miss, the hardware traps to the runtime's method-lookup slow path, which installs/updates the cache. Details in [02-isa.md](02-isa.md) § 2.4.

### 4.6 In-Order Cores, Many Threads

Cores are in-order with fine-grained multithreading (FGMT). Stalls on memory accesses are hidden by switching to another hardware thread on the same core, not by speculative execution. This trades single-thread latency for throughput and area efficiency. Details in [03-core.md](03-core.md).

### 4.7 Distributed Heap, Not Global Coherence

Each tile owns a local nursery. Clusters share an old-generation region. Cross-tile pointers are allowed but biased toward locality. There is no hardware cache coherence protocol across all tiles; instead, explicit message passing and DMA move data. Details in [04-soc.md](04-soc.md) and [05-memory-gc.md](05-memory-gc.md).

### 4.8 Movement Engines for GC

Dedicated non-programmable engines handle GC's bulk work: scanning regions for pointers, copying live objects, updating forwarding pointers. These run concurrently with mutator tiles. Details in [04-soc.md](04-soc.md) § 4.2 and [05-memory-gc.md](05-memory-gc.md).

## 5. Glossary

| Term | Definition |
|------|------------|
| **DOP** | Dynamic-Object Processor. A core designed for dynamic-language workloads. |
| **Tile** | The smallest autonomous unit: one DOP core + local SRAM + NoC port. |
| **Cluster** | A group of tiles sharing a larger SRAM region and movement engines. |
| **Nursery** | Per-tile young-generation heap region for bump-pointer allocation. |
| **Tag** | Low bits of a machine word encoding the value's dynamic type. |
| **Shape** | A descriptor for an object's layout (field count, types, class). Used as IC key. |
| **IC** | Inline Cache. Hardware-accelerated dispatch cache keyed by (callsite, shape). |
| **Write Barrier** | A store-time action that notifies the GC of a pointer mutation. |
| **Movement Engine** | Dedicated hardware for bulk memory operations: scan, copy, compact, fixup. |
| **NoC** | Network-on-Chip. The inter-tile and inter-cluster communication fabric. |
| **HBM** | High-Bandwidth Memory. Off-chip DRAM used as the cold heap and bulk storage. |
| **FGMT** | Fine-Grained Multithreading. Cycle-by-cycle interleaving of hardware threads. |
| **Ref** | A tagged pointer to a heap-allocated object. |
| **Fixnum** | An immediate integer encoded directly in a tagged word (no heap allocation). |
| **Forwarding Pointer** | A marker left in a moved object's old location pointing to its new location. |
| **Remembered Set** | A set of old→young pointers tracked by the GC to avoid full-heap scanning. |
| **Card Table** | A coarse-grained bitmap marking memory regions that contain modified pointers. |
| **BSP** | Bulk Synchronous Parallel. A programming model with compute/communicate phases. |

## 6. Conformance Levels

An LM-1 implementation may conform at one of three levels:

| Level | Requirements |
|-------|-------------|
| **LM-1 Minimal** | Full ISA (all 8 families), ≥1 tile, nursery allocation, software GC, software IC |
| **LM-1 Standard** | + hardware IC unit, + hardware write barriers, + ≥1 movement engine, ≥16 tiles |
| **LM-1 Full** | + capability mode, + SIMD/tensor sidekick tiles, + full QoS NoC, ≥256 tiles |

## 7. Document Conventions

- **MUST**, **SHALL**, **REQUIRED**: absolute requirements
- **SHOULD**, **RECOMMENDED**: strong preference, deviation requires justification
- **MAY**, **OPTIONAL**: truly optional
- Bit numbering: bit 0 is the least significant bit (LSB)
- Address sizes: 48-bit virtual, 44-bit physical (unless otherwise noted)
- All instruction mnemonics are illustrative; canonical encoding is in [07-encoding.md](07-encoding.md)

## 8. References

1. Graphcore GC200 IPU architecture. https://docs.graphcore.ai
2. Arm Memory Tagging Extension (MTE). https://source.android.com/docs/security/test/memory-safety/arm-mte
3. CHERI: Capability Hardware Enhanced RISC Instructions. https://www.cl.cam.ac.uk/research/security/ctsrd/cheri/
4. Tenstorrent TT-Metal. https://docs.tenstorrent.com
5. Symbolics 3600 Technical Summary (historical reference)
6. Deutsch & Schiffman, "Efficient Implementation of the Smalltalk-80 System," POPL 1984 (inline caches)
7. Ungar, "Generation Scavenging," SOSP 1984 (generational GC)
