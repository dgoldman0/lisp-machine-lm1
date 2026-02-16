# LM-1 Core Microarchitecture

**Spec ID:** LM1-SPEC-03  
**Revision:** 0.1-draft  
**Date:** 2026-02-16

---

## 1. Scope

This document specifies the microarchitecture of a single LM-1 DOP core — one implementation of the ISA contract defined in [02-isa.md](02-isa.md). The DOP core is designed for **area efficiency** and **throughput**, not peak single-thread performance: the goal is to fit as many cores as possible on a die while keeping dynamic-language fast paths at 1–2 cycles.

## 2. Design Summary

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Pipeline order | In-order | Saves area; latency hidden by FGMT |
| Pipeline depth | 6 stages | Short enough for fast branches |
| Hardware threads | 4 per core | Hide memory latency; 4 is a sweet spot between area and utilization |
| Register file | 32 × 64-bit per thread (128 total per core) | Full ISA register set per thread context |
| Issue width | Single-issue | Keeps decode/issue simple; area goes to tile count |
| Instruction width | 32 bits fixed | Simplifies fetch/decode |
| Local SRAM | External to core (tile-level, see [04-soc.md](04-soc.md)) | |
| I-Cache | 8 KiB, direct-mapped | Small; code locality is decent in Lisp |
| D-Cache | None (SRAM-first) or 4–8 KiB, 2-way | Tile SRAM serves as primary data store |

## 3. Pipeline Stages

```
┌───────┬───────┬───────┬───────┬───────┬───────┐
│ FETCH │DECODE │  TAG  │  EX   │  MEM  │  WB   │
│  (IF) │  (ID) │  (TG) │  (EX) │  (MA) │  (WB) │
└───────┴───────┴───────┴───────┴───────┴───────┘
```

### 3.1 Stage IF: Instruction Fetch

- Fetches one 32-bit instruction per cycle from the I-cache or tile SRAM.
- **Thread interleaving:** Each cycle fetches for a different hardware thread (round-robin or priority-based). With 4 threads, each thread sees an instruction enter the pipeline every 4 cycles in the base case, or every 1 cycle if other threads are idle/stalled.
- Branch prediction: **static** (backward-taken, forward-not-taken) or a very small (32-entry) BTB. Dynamic branch prediction is a luxury traded away for area.

### 3.2 Stage ID: Instruction Decode

- Decodes the 32-bit instruction, identifies the family and operation.
- Reads source registers from the register file.
- For `CALL.IC`: extracts the callsite ID and begins the IC lookup (pipelined into TG).

### 3.3 Stage TG: Tag Check (Lisp-Specific)

This is the **novel stage** that makes the DOP core different from a generic RISC.

- **Tag extraction and checking.** For tagged instructions (`ADD.FIX`, `LD`, `ST.WB`, `TST`, etc.), extracts and validates operand tags.
- **Fast-path / slow-path decision.** If tags don't match expectations (e.g., non-fixnum in `ADD.FIX`), signals a trap instead of proceeding.
- **IC probe completion.** For `CALL.IC`, the IC unit returns hit/miss by the end of this stage. On hit, the target address is ready for EX.
- **Ref metadata extraction.** For field-access and barrier instructions, extracts the address, shape hint, and generation bits from ref operands.

**Area cost:** The tag unit is a small combinational block (a few hundred gates) plus the IC lookup table (see § 5).

### 3.4 Stage EX: Execute

- **ALU operations:** fixnum add/sub (tagged), untagged arithmetic, bitwise ops, comparisons.
- **Address calculation:** for loads/stores, computes effective address from ref + field offset.
- **Allocation pointer update:** for `ALLOC` variants, computes `np + size`, checks vs `nl`, prepares header write.
- **Branch resolution:** computes branch targets, determines taken/not-taken.
- **Trap injection:** if TG signaled a trap, EX computes the trap vector and redirects control.

### 3.5 Stage MA: Memory Access

- **Loads:** issues read to D-cache or SRAM.
- **Stores:** issues write. For `ST.WB`, the **barrier logic** executes here:
  1. Check if stored value is a ref (tag test on the value being stored — this was pre-computed in TG).
  2. Check cross-generation (compare generation bits from store address context vs stored ref metadata).
  3. If barrier fires: compute card-table address, issue card-mark write (can be overlapped with the data store).
- **Allocation (header write):** writes the header word to the nursery.
- **Queue operations:** `SEND`/`RECV` interact with the tile's queue hardware.

### 3.6 Stage WB: Write-Back

- Writes results to the destination register.
- Commits the instruction.
- Updates the PC for the thread.

## 4. Fine-Grained Multithreading (FGMT)

### 4.1 Thread Scheduling

The core supports **4 hardware thread contexts**. Each context has its own:

- 32 GPRs (r0–r31) + special registers (sp, fp, lr, tp, np, nl)
- PC
- Thread state (running, stalled, halted)
- IC scratch registers (ic0–ic3)

The scheduler selects which thread enters the pipeline each cycle. Policies:

| Policy | Description | When |
|--------|-------------|------|
| **Round-robin** | Strict rotation among non-stalled threads | Default |
| **Priority** | Higher-priority threads (e.g., GC coordination) get first pick | When GC is active |
| **Stall-skip** | Stalled threads (waiting on memory) are skipped | Always |

### 4.2 Latency Hiding

With 4 threads and a 6-stage pipeline, a memory access with 4-cycle SRAM latency is fully hidden: by the time thread A's load returns, threads B, C, D have each had a cycle, and thread A's turn comes around exactly when the data is ready.

For longer-latency accesses (HBM, 50+ cycles), the thread stalls and the remaining 3 threads share the pipeline. If all 4 threads are stalled, the core is idle (a signal to the scheduler that this tile's working set is too cold).

### 4.3 Context Size

Per-thread context: 32 × 8 = 256 bytes (GPRs) + ~64 bytes (specials, PC, state) ≈ 320 bytes.  
Total for 4 threads: ~1.3 KiB. This fits easily in a small register file SRAM.

## 5. Special Functional Units

### 5.1 Tag Unit

Located in the TG stage. Combinational logic that performs:

- Primary tag extraction (bits 2:0)
- Tag class determination (fixnum/ref/cons/special/header)
- Ref metadata extraction (shape hint, generation bits, address)
- Fixnum validity check for arithmetic ops
- Trap signal generation for tag mismatches

**Area:** ~500 gates. Negligible.

### 5.2 Inline-Cache (IC) Unit

A small **content-addressable memory** (CAM) that stores IC entries. Probed during the TG stage for `CALL.IC` instructions.

| Parameter | Value |
|-----------|-------|
| Entries | 64 per core (shared across threads) |
| Entry size | 16 bytes (callsite: 3B, shape: 4B, code_entry: 6B, flags: 1B, padding: 2B) |
| Total IC SRAM | 1 KiB |
| Lookup | Fully associative, 1-cycle |
| Replacement | LRU (approximate, 2-bit) |

The IC unit:
1. Receives `(callsite_id, shape_hint)` from the TG stage.
2. Probes all entries in parallel (CAM match on callsite + shape).
3. On hit: outputs `code_entry` to EX stage.
4. On miss: signals `TRAP_IC_MISS`.

**Polymorphic IC extension (OPTIONAL):** An implementation MAY support **megamorphic entries** — entries keyed only by callsite that point to a runtime-generated dispatch stub. This avoids repeated traps for highly polymorphic sites.

### 5.3 Nursery Allocator

Integrated into the EX and MA stages. The allocator is simply the `np` and `nl` registers plus a comparator.

- `ALLOC`: compute `np + size`, compare with `nl`. If fits, output `np` as the object address, update `np` in WB. If not, trap.
- The header write goes to the MA stage as a normal store.
- The entire fast-path allocation is **1 cycle in EX + 1 cycle in MA** (but pipelined, so throughput is 1 allocation per cycle).

### 5.4 Write-Barrier Logic

Integrated into the MA stage's store path. For `ST.WB`:

1. **Ref check:** Tag unit (TG) pre-identifies whether the stored value is a ref.
2. **Generation check:** Compare GC-generation bits from the target object's ref metadata with the stored value's ref metadata. If they differ (old → young pointer), the barrier fires.
3. **Card mark:** Compute card-table entry address from the store's effective address. Issue a byte store to set the card-table byte. This is a separate store-buffer entry and does not stall the pipeline.

**Area:** A comparator, a shift-and-mask unit for card address computation, and store-buffer bandwidth for the card-mark write. ~1000 gates.

### 5.5 Stack Cache (OPTIONAL)

A small (8–16 entry) hardware stack cache that accelerates `PUSH`/`POP` and `CALL`/`RET` frame operations. Stack entries spill to SRAM on overflow and fill on underflow.

| Parameter | Value |
|-----------|-------|
| Depth | 8–16 entries per thread |
| Entry | 64 bits (one tagged word) |
| Total | 256–512 bytes per thread, 1–2 KiB per core |

**Conformance:** OPTIONAL. RECOMMENDED for LM-1 Standard and above.

## 6. Exceptions and Traps

### 6.1 Trap Pipeline

When a trap condition is detected (in TG, EX, or MA):

1. The trap-signaling instruction and all younger instructions in the pipeline are squashed.
2. The thread's PC is saved to a trap-save register.
3. The thread's PC is set to the trap vector address (from a per-thread trap-table base register).
4. The trap handler runs at runtime privilege level.

Because the pipeline is in-order and short, trap handling is simple: there are at most ~3 instructions in flight per thread, and all are squashed.

### 6.2 Trap Latency

From trap detection to first handler instruction: **3 cycles** (pipeline drain + vector fetch).

### 6.3 Return from Trap

`ERET` restores the PC from the trap-save register and resumes the thread.

## 7. Core Area Budget (Illustrative)

Rough die-area breakdown for one DOP core at a 5nm process:

| Component | Area (mm²) | Notes |
|-----------|:----------:|-------|
| Pipeline + ALU | 0.010 | Simple in-order, single-issue |
| Register file (4 threads) | 0.005 | 1.3 KiB SRAM |
| Tag unit | 0.001 | ~500 gates |
| IC unit | 0.003 | 1 KiB CAM |
| I-Cache (8 KiB) | 0.008 | |
| Stack cache (opt) | 0.004 | 2 KiB SRAM |
| Control/misc | 0.004 | |
| **Core total** | **~0.035** | |

At 0.035 mm² per core, a 600 mm² die (reticle limit) could fit ~8000 cores with 50% of the die for SRAM, NoC, movement engines, HBM PHY, and I/O. In practice, a 256–2048 tile design is realistic with generous per-tile SRAM.

## 8. Power Considerations

- **Clock gating:** Unused thread contexts are clock-gated. If a tile has no scheduled work, the entire core is gated.
- **Voltage scaling:** Tiles in the "cold" GC-scanning role MAY run at reduced voltage/frequency.
- **No speculative execution:** The in-order, non-speculative design avoids the power overhead of speculation, reorder buffers, and recovery logic.

## 9. Core-Tile Interface

The core communicates with the rest of its tile via:

| Interface | Width | Description |
|-----------|:-----:|-------------|
| SRAM port | 64-bit | Read/write to tile-local SRAM (nursery, stacks, queues, hot data) |
| I-Cache fill port | 256-bit | Burst fill from SRAM or cluster |
| Queue port | 64-bit | Send/receive to tile message queues |
| DMA request port | command | Enqueue commands to the tile's DMA endpoint |
| Interrupt/trap lines | 4 | From movement engine, NoC, timers, external |

These are detailed further in [04-soc.md](04-soc.md).
