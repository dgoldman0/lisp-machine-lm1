# LM-1 Architecture Overview

## What Is the LM-1?

The LM-1 is a **Dynamic Object Processor** (DOP) — a CPU architecture designed from the ground up to execute dynamically-typed, garbage-collected languages efficiently in hardware. Where conventional CPUs treat memory as flat arrays of bytes and rely on software to manage object lifetimes, the LM-1 makes objects, tags, heaps, and garbage collection first-class citizens of the hardware.

The processor's fundamental data type is a **64-bit tagged word**, not a raw integer. Every word in the register file, every value on the stack, every field stored in memory carries type information in its low bits. The hardware understands what each word *is* — a fixnum, a heap pointer, a cons cell reference, a special value like nil — and uses that knowledge to accelerate operations that dominate dynamic language workloads.

## Architecture at a Glance

```
┌──────────────────────────── Cluster (×8 on chip) ──────────────────────────┐
│                                                                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐       ┌──────────┐                │
│  │  Tile 0   │ │  Tile 1   │ │  Tile 2   │ ···  │  Tile 7   │                │
│  │ ┌──────┐  │ │ ┌──────┐  │ │ ┌──────┐  │       │ ┌──────┐  │                │
│  │ │ Core │  │ │ │ Core │  │ │ │ Core │  │       │ │ Core │  │                │
│  │ │ 4×HWT│  │ │ │ 4×HWT│  │ │ │ 4×HWT│  │       │ │ 4×HWT│  │                │
│  │ └──────┘  │ │ └──────┘  │ │ └──────┘  │       │ └──────┘  │                │
│  │ 256KB SRAM│ │ 256KB SRAM│ │ 256KB SRAM│       │ 256KB SRAM│                │
│  │ MsgQueues │ │ MsgQueues │ │ MsgQueues │       │ MsgQueues │                │
│  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘       └─────┬─────┘                │
│        │              │              │                   │                     │
│        └──────────────┴──────────────┴───────────────────┘                     │
│                              │ Crossbar │                                      │
│                       ┌──────┴──────────┐                                      │
│                       │ Cluster SRAM    │    ┌──────────────┐                  │
│                       │ 2 MiB (dual-port)│◄──►│ GC Engines   │                  │
│                       └─────────────────┘    │ Scanner      │                  │
│                                              │ Copier       │                  │
│                                              │ Fixup        │                  │
│                                              └──────────────┘                  │
└────────────────────────────────────────────────────────────────────────────┘
```

**Reference configuration:** 8 clusters × 8 tiles = 64 tiles, each with 4 hardware threads = 256 threads total.

## Core Design Philosophy

### 1. Tags Are Free — So Use Them Everywhere

Every 64-bit word carries a 3-bit tag in its low bits:

| Tag Pattern | Meaning | How It Works |
|-------------|---------|--------------|
| `bit[0] = 0` | **Fixnum** | 63-bit signed integer in bits [63:1]. The tag is the LSB itself. |
| `bits[1:0] = 01` | **Reference** | Heap pointer. Address in bits [50:3], metadata in [63:51]. |
| `bits[2:0] = 011` | **Cons ref** | Special reference to a cons cell (pair). |
| `bits[2:0] = 101` | **Special** | Singleton values: nil, t, char, short-float, etc. |
| `bits[2:0] = 111` | **Header** | Memory-only. Contains object metadata (type, size, GC bits). |

This encoding is not an afterthought bolted onto a general-purpose ISA — it is the ISA. The ALU checks tags on arithmetic. The store path fires write barriers when it sees a reference. The GC scanner identifies pointers by checking tag bits. The branch unit tests for nil by comparing against the 64-bit constant `0x05`.

### 2. Multi-Cycle FSM, Not Pipelined

The LM-1 core uses a **multi-cycle finite state machine** rather than a pipeline. Each instruction takes 2–20+ cycles depending on complexity:

| Instruction Class | Typical Cycles | Why |
|-------------------|----------------|-----|
| Simple ALU (ADD, AND, TST) | 3 | Fetch + Decode + Execute |
| Memory load/store | 4–5 | + memory access + writeback |
| ALLOC | 8–20 | Header write + zero-fill loop |
| CALL (push frame) | 6–8 | Push LR, FP, adjust SP |
| PUSH.MULTI/POP.MULTI | 3 + n | Per-register iteration |
| 64-bit divide | 67 | 64 cycles in iterative divider |

This is a deliberate choice. The LM-1's performance strategy is **massive thread-level parallelism** (256+ hardware threads) rather than single-thread IPC. A multi-cycle FSM is dramatically simpler to verify, uses less area and power per core, and avoids pipeline hazards entirely. See [Design Decisions](07-design-decisions.md) for the full rationale.

### 3. Fine-Grained Multithreading (FGMT)

Each core contains **4 hardware thread contexts** sharing a single execution pipeline:

- **128-entry register file** (4 × 32 GPRs), addressed as `{thread_id[1:0], reg_index[4:0]}`
- **Per-thread program counters** stored in dedicated flops
- **Round-robin scheduling** at instruction boundaries (when entering the fetch state)
- **Independent halt**: each thread can halt independently; the core only reports `halted` when all 4 threads are inactive

Thread switching costs zero cycles — it's just a PC swap and a 2-bit update to the banked register address prefix. No pipeline flush, no context save, no TLB shootdown.

### 4. Allocation as a Primitive

Object allocation is an instruction, not a library call:

```
ALLOC    Rd, #template_id, #n_extra_words
ALLOC.CONS Rd                              ; allocate a cons cell
```

The hardware implements bump-pointer allocation directly:

1. Check nursery pointer (NP) against nursery limit (NL)
2. Write the header word from the template table
3. Zero-fill all payload words
4. Return the tagged reference in Rd
5. If nursery is full → trap to GC

This takes ~8–20 cycles depending on object size, versus hundreds of cycles for a software allocator (function call overhead, cache misses, lock contention).

### 5. Garbage Collection in Hardware

The most distinctive feature of the LM-1 is its **hardware garbage collection support**:

- **Write barriers** are checked automatically on every tagged store (`ST.WB` opcode). The hardware compares source and destination generations, and marks card table entries without software intervention.
- **Three dedicated GC engines** per cluster operate on the shared SRAM through a private dual-port:
  - **Scanner**: walks a memory region, reads object headers, identifies reference fields
  - **Copier**: relocates live objects to a new region, installs forwarding pointers
  - **Fixup**: scans a region and updates any references that point to forwarded objects
- The **card table** is maintained in hardware, with configurable card size (default 64 bytes).
- **Forwarding pointers** use the header's gc_bits field (0xFF = forwarded), allowing the fixup engine to transparently redirect stale pointers.

See [GC Engines](05-gc-engines.md) for the complete mechanical description.

## Instruction Set Families

The ISA is organized into **8 semantic families** plus scalar supplementary operations:

| Family | Opcodes | Purpose |
|--------|---------|---------|
| 1. Type & Arithmetic | TST, TST.SHAPE, ARITH.FIX, ADD.FIX.IMM, CMP.TAGGED | Tagged-aware computation |
| 2. Allocation | ALLOC, ALLOC.CONS, ALLOCV, ALLOC.CLOSURE | Bump-pointer object creation |
| 3. Field Access | LD, LD.CAR.CDR, ST, ST.WB, ST.CAR.CDR | Tagged loads/stores with barriers |
| 4. Dispatch | CALL.IC, IC.INSTALL, CALL.DIRECT, CALL.CLOSURE, RET, TAILCALL.* | Polymorphic dispatch via inline caches |
| 5. Prefetch | PREFETCH.REF/FLD/CDR, GATHER.PRE | Memory prefetch hints (currently no-ops) |
| 6. Messaging | SEND, RECV, TRY.RECV, CAS.TAGGED, FAA, FENCE.GC | Inter-tile communication & atomics |
| 7. GC Control | ENQ.SCAN/COPY/FIXUP/COMPACT | Software-initiated GC engine commands |
| 8. Scalar | ARITH.RAW, BITWISE, LDR, STR, BR, BR.COND, PUSH/POP, LI, LUI | Conventional scalar operations |

Plus sub-word memory access (LDB, STB, LDH, STH, LDW, STW) and system instructions (TRAP, ERET, SYS.INFO, HALT).

See [ISA & Tags](02-isa-tags.md) for the complete encoding reference.

## Memory Hierarchy

```
Per-Core:
  └── 128×64 Register File (FGMT-banked)
  └── 8 KiB I-Cache (direct-mapped, 128 sets × 64B lines)

Per-Tile:
  └── 256 KiB SRAM (single-port, code + data + stacks + card table)

Per-Cluster:
  └── 2 MiB Shared SRAM (dual-port: crossbar + GC engines)

Chip-Level:
  └── 32 GiB HBM (via 4 HBM stacks, future)
```

There is **no data cache** in the traditional sense. Each tile's SRAM is directly-addressed scratchpad memory. The instruction cache is the only cache structure. This eliminates cache coherence entirely — a massive simplification for a many-core design.

## What Makes This Different

The LM-1 occupies a unique position in the CPU design space. It is not a general-purpose processor, not a GPU, not a DSP, and not a classical Lisp Machine — though it draws ideas from all of these. See [Design Decisions & Comparisons](07-design-decisions.md) for a full analysis of how it relates to:

- **RISC-V / ARM / x86** — conventional scalar CPUs
- **GPUs (CUDA cores)** — massively parallel but untyped
- **MIT CADR / Symbolics 3600 / TI Explorer** — the original Lisp Machines
- **Azul Vega / C4** — Java-focused GC hardware
- **SPARC T-series** — fine-grained multithreaded server chips
- **Mill Architecture** — tagged-memory ISA research
