# LM-1 Emulator Design

**Status:** Design sketch  
**Date:** 2026-02-16

---

## 1. Purpose

The emulator is the first thing that runs LM-1 code. It's not a cycle-accurate simulator — it's a **functional emulator** that executes the ISA correctly so we can:

1. Boot the BIOS and OS
2. Develop and test the compiler
3. Run Lisp programs and validate semantics
4. Debug the ISA design (find missing instructions, broken invariants)

Performance target: ~10–100 MIPS on a modern host. Fast enough for interactive REPL use, slow enough that we don't waste time optimizing the emulator itself.

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Host Process                          │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │                  Emulator Core                      │  │
│  │                                                    │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ Decoder   │  │ Executor │  │ Tag Logic        │  │  │
│  │  │          │  │          │  │ (pure functions)  │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  │                                                    │  │
│  │  ┌──────────────────────────────────────────────┐  │  │
│  │  │             Thread Contexts (N)              │  │  │
│  │  │  [regs, pc, sp, fp, np, nl, state, ...]      │  │  │
│  │  └──────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │                  Memory Subsystem                   │  │
│  │                                                    │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ Tile SRAM│  │ Cluster  │  │ HBM (mmap'd      │  │  │
│  │  │ (arrays) │  │ SRAM     │  │  host memory)    │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  │                                                    │  │
│  │  ┌──────────────────────────────────────────────┐  │  │
│  │  │  Card Tables  │  Queues  │  DMA Emulation    │  │  │
│  │  └──────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │                  Support Services                   │  │
│  │                                                    │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ Trap     │  │ IC Table │  │ Movement Engine  │  │  │
│  │  │ Dispatch │  │ Emulation│  │ Emulation        │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  │                                                    │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ Debugger │  │ Profiler │  │ I/O (terminal,   │  │  │
│  │  │ Interface│  │ Counters │  │  files, network) │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

## 3. What We Emulate

### 3.1 Must Emulate (Functional Correctness)

| Component | Emulation Strategy |
|-----------|-------------------|
| **Tagged words** | Native 64-bit host integers. Tag operations are just bitmasks. |
| **Registers** | Array of 32 uint64 per thread context. |
| **Instruction decode + execute** | Giant switch on opcode. Each case implements the pseudocode from the ISA spec. |
| **Nursery allocator** | Per-tile: a base pointer, bump pointer (np), and limit (nl). `ALLOC` bumps; overflow calls the GC slow path. |
| **Write barriers** | `ST.WB` checks tags and generations, updates a card-table byte array. Exact same logic as the hardware, just in software. |
| **IC table** | A hash map per tile: key = (callsite, shape), value = code_entry. `CALL.IC` probes it; miss triggers the trap handler. |
| **Traps** | On trap: save PC, switch to handler address from trap table, set trap-code register. `ERET` restores. |
| **Message queues** | Per-tile array of ring buffers. `SEND` enqueues, `RECV` dequeues. Cross-tile: route through a simulated NoC (just a function call in the emulator). |
| **Movement engines** | `ENQ.SCAN/COPY/FIXUP` run their logic synchronously (in the emulator, they just execute immediately, not concurrently). |
| **Memory map** | One big host-allocated array. Tile SRAMs, cluster SRAMs, and HBM are just offset ranges within it. |

### 3.2 Simplified (Don't Need Full Fidelity)

| Component | Simplification |
|-----------|---------------|
| **Pipeline stages** | No pipeline simulation. Instructions execute atomically. |
| **FGMT** | Emulate as cooperative multitasking: run one thread for N instructions, then switch. |
| **NoC** | No latency modeling. Cross-tile messages are instant. |
| **DMA** | Instant memcpy. No latency, no bandwidth limits. |
| **Power/clock gating** | Ignored. |
| **Caches** | No I-cache or D-cache simulation. All memory accesses are instant. |
| **Capability mode** | Deferred. Implement later if/when we design the safe-mode runtime. |

### 3.3 Not Emulated

| Component | Why |
|-----------|-----|
| **SIMD sidekick tiles** | Removed from design per discussion. |
| **HBM bandwidth limits** | Not useful for functional correctness. |
| **Physical layout** | Not relevant. |

## 4. Configuration

The emulator is parameterized:

```
EmulatorConfig = {
    tile_count:         4       -- start small; 1 is fine for initial bringup
    threads_per_tile:   4
    tile_sram_bytes:    262144  -- 256 KiB
    cluster_size:       4       -- tiles per cluster (so 1 cluster for 4 tiles)
    cluster_sram_bytes: 2097152 -- 2 MiB
    hbm_bytes:          67108864 -- 64 MiB (plenty for emulation)
    nursery_bytes:      65536   -- 64 KiB per tile
    ic_entries:         64      -- per tile
    queue_depth:        512     -- entries per queue
    queues_per_tile:    4
}
```

Start with **1 tile, 1 thread** for initial bringup. Scale up once the BIOS and OS boot.

## 5. Emulator Loop

```
fn run(emu: &mut Emulator) {
    loop {
        let tile = emu.schedule_tile();          // round-robin across tiles
        let thread = tile.schedule_thread();      // round-robin across threads
        
        let pc = thread.pc;
        let instruction = tile.fetch(pc);         // read 32 bits from memory at pc
        let decoded = decode(instruction);        // extract opcode, fields
        
        match decoded.family {
            F1_TAGGED_ARITH => exec_tagged_arith(tile, thread, decoded),
            F2_ALLOC        => exec_alloc(tile, thread, decoded),
            F3_FIELD_ACCESS => exec_field_access(tile, thread, decoded),
            F4_DISPATCH     => exec_dispatch(tile, thread, decoded),
            F5_PREFETCH     => { /* no-op in emulator */ },
            F6_CONCURRENCY  => exec_concurrency(tile, thread, decoded),
            F7_REGION_OPS   => exec_region_ops(tile, thread, decoded),
            F8_CAPABILITY   => trap(thread, TRAP_UNIMPLEMENTED),
            SCALAR          => exec_scalar(tile, thread, decoded),
            SYSTEM          => exec_system(tile, thread, decoded),
        }
        
        thread.cycle_count += 1;
        
        // Check for pending interrupts, GC requests, etc.
        if thread.cycle_count % 1024 == 0 {
            check_interrupts(tile, thread);
        }
    }
}
```

## 6. Debug Interface

The emulator exposes a debug interface (over a socket or stdio) that lets you:

| Command | Description |
|---------|-------------|
| `step [N]` | Execute N instructions on current thread |
| `continue` | Run until breakpoint or trap |
| `break <addr>` | Set breakpoint |
| `regs` | Dump all registers (pretty-printed as tagged values) |
| `inspect <ref>` | Dereference a ref and print the object (header, fields) |
| `mem <addr> <len>` | Dump raw memory |
| `trace on/off` | Log every instruction executed |
| `tiles` | Show tile status (running/idle, thread states) |
| `queues` | Show queue contents |
| `gc-status` | Show nursery fill levels, card table stats |
| `ic-dump` | Show IC table contents |
| `disas <addr> <len>` | Disassemble instructions |

This is critical for bringing up the BIOS — there's no display or REPL yet, so the debugger is your only window into the machine.

## 7. Host I/O Bridge

The emulator provides I/O devices as memory-mapped regions or via special TRAP codes:

| Device | Mechanism | Purpose |
|--------|-----------|---------|
| **Console** | TRAP #0x80 with char in r1 | Print a character to the host terminal |
| **Console input** | TRAP #0x81, char returned in r0 | Read a character from the host terminal |
| **Block storage** | TRAP #0x82 with args | Read/write a 4K block from a host file |
| **Timer** | CYCLE instruction | Read host cycle counter (or wall clock) |
| **Shutdown** | HALT instruction | Exit emulator |

These traps are **not** part of the ISA spec — they're emulator-specific. The BIOS abstracts over them so the OS never calls these directly.

## 8. Implementation Language

Rust or C. Leaning Rust:
- Tagged word manipulation is all safe integer ops
- Memory subsystem is a big `Vec<u64>` with bounds checking in debug mode
- Pattern matching for decode is clean
- Easy to add a socket-based debugger later

But C is fine too — the emulator is ~3000–5000 lines of straightforward code.

## 9. Bringup Sequence

1. **Phase 1:** Emulate 1 tile, 1 thread. No GC, no IC, no queues. Just enough to execute scalar ops, branches, loads/stores. Verify with hand-assembled test programs.
2. **Phase 2:** Add allocation (nursery bump pointer). Write `ALLOC` + `LD` + `ST` test programs. No GC yet — just let the nursery fill up and halt.
3. **Phase 3:** Add trap handling. Implement `TRAP_NURSERY_OVERFLOW` → simple stop-and-copy GC in the trap handler (written in LM-1 assembly or cheating with emulator-native code initially).
4. **Phase 4:** Add IC table. Test `CALL.IC` with hand-crafted dispatch. Verify hit/miss/install cycle.
5. **Phase 5:** Add message queues. Test `SEND`/`RECV` between threads, then between tiles.
6. **Phase 6:** Load and run the BIOS image. First sign of life: the BIOS prints "LM-1" to the console.
7. **Phase 7:** Load and boot the OS image. REPL comes up.

## 10. Testing Strategy

- **ISA conformance tests:** One test per instruction, verifying the pseudocode from the spec. These are tiny hand-assembled programs (or assembled from a trivial assembler).
- **GC stress tests:** Tight allocation loops that force nursery overflows. Verify no tagged pointers are lost.
- **IC tests:** Monomorphic, polymorphic, and megamorphic dispatch patterns.
- **Queue tests:** Producer-consumer across tiles.
- **Integration:** Boot the BIOS, then boot the OS, then run `(+ 1 2)` at the REPL.
