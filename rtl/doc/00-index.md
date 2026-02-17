# LM-1 RTL Documentation

Comprehensive hardware documentation for the LM-1 Dynamic Object Processor.

## Document Index

| # | Document | Description |
|---|----------|-------------|
| 01 | [Architecture Overview](01-architecture.md) | High-level architecture, design philosophy, how LM-1 compares to conventional CPUs, GPUs, and Lisp machines |
| 02 | [ISA & Tag System](02-isa-tags.md) | The tagged-word object model, instruction encoding, opcode families, and the type system in hardware |
| 03 | [Core Pipeline](03-core-pipeline.md) | Multi-cycle FSM, FGMT threading, instruction cache, register file, ALU, branch, LSU — every datapath component |
| 04 | [Memory Subsystem](04-memory.md) | SRAM hierarchy, address decoding, crossbar, sub-word access, memory port arbitration |
| 05 | [Garbage Collection Hardware](05-gc-engines.md) | Scanner, copier, fixup engines, write barriers, card tables, forwarding pointers — how GC runs in hardware |
| 06 | [SoC Integration](06-soc-integration.md) | Tile, cluster, crossbar, clock gating, message queues, NoC stubs, and the full chip hierarchy |
| 07 | [Design Decisions & Comparisons](07-design-decisions.md) | Why multi-cycle over pipelined, why tagged words, how this differs from RISC-V/x86/GPU/Lisp Machine, tradeoffs made |

## RTL File Map

```
rtl/
├── core/                          # Processor core (DOP)
│   ├── lm1_pkg.sv                 # ISA package — all constants, types, opcodes
│   ├── lm1_decoder.sv             # Combinational instruction decoder
│   ├── lm1_regfile.sv             # 128×64 banked register file (4 threads × 32 GPRs)
│   ├── lm1_alu.sv                 # ALU — arithmetic, logic, compare, type-test, divider
│   ├── lm1_branch.sv              # Branch condition evaluator
│   ├── lm1_lsu.sv                 # Load/Store Unit — all memory access ops
│   ├── lm1_control.sv             # Control FSM — 46-state multi-cycle orchestrator
│   ├── lm1_cpu.sv                 # CPU top-level — integrates all core components
│   ├── lm1_icache.sv              # 8 KiB direct-mapped instruction cache
│   ├── lm1_tmpl_table.sv          # 256-entry header template table
│   ├── lm1_ic_table.sv            # 64-entry inline cache (polymorphic dispatch)
│   ├── lm1_msg_queue.sv           # 4 × 512-entry hardware message FIFOs
│   └── lm1_perf_counters.sv       # 8 × 64-bit performance counters
├── tile/
│   └── lm1_tile.sv                # Tile wrapper — clock gating, ID, core instantiation
├── cluster/
│   ├── lm1_cluster.sv             # 8-tile cluster — crossbar, shared SRAM, GC engines
│   └── lm1_crossbar.sv            # Round-robin arbiter for shared SRAM access
├── gc/
│   ├── lm1_gc_engine_top.sv       # GC engine wrapper — command dispatch, memory arbiter
│   ├── lm1_gc_scanner.sv          # Object graph scanner — finds live references
│   ├── lm1_gc_copier.sv           # Object copier — relocates objects, installs forwarding ptrs
│   └── lm1_gc_fixup.sv            # Reference fixup — updates stale pointers post-copy
└── tech/
    ├── lm1_sram_sp.sv             # Single-port SRAM (behavioral, infers block RAM)
    ├── lm1_sram_dp.sv             # Dual-port SRAM (behavioral, true dual-port)
    └── lm1_clock_gate.sv          # ICG cell (latch-based, ASIC-portable)
```

## Key Numbers

| Metric | Value |
|--------|-------|
| Total RTL | ~6,500 lines across 23 files |
| Word width | 64-bit tagged words |
| Instruction width | 32-bit fixed |
| GPRs per thread | 32 |
| Hardware threads | 4 per core (FGMT) |
| I-Cache | 8 KiB direct-mapped |
| Tile SRAM | 256 KiB (default) |
| Cluster SRAM | 2 MiB shared (default) |
| Tiles per cluster | 8 |
| GC engines | 3 per cluster (scanner, copier, fixup) |
| Performance counters | 8 × 64-bit |
| Message queues | 4 × 512 entries per tile |
| IC table entries | 64 per core |
| Template table entries | 256 per core |
| Target process node | 5 nm |
| Target die area | ~135 mm² (64 tiles, 8 clusters) |
