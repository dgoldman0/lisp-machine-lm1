# LM-1: A Many-Tile Dynamic-Object Processor for Lisp

**Project Status:** Standards Development + RTL Implementation + Emulator  
**Revision:** 0.1-draft  
**Date:** 2026-02-18

---

## What This Is

LM-1 is a hardware architecture specification for a **many-tile dynamic-object processor** (DOP) designed to make Lisp (and dynamic languages generally) feel native at the silicon level. Instead of one monolithic core fighting the impedance mismatch between static hardware and dynamic semantics, LM-1 decomposes the problem into a **fabric of small, efficient tiles** with:

- **Local SRAM** (nurseries, hot objects, stacks, queues)
- **A network-on-chip (NoC)** with QoS traffic classes
- **Dedicated movement/GC engines** that offload memory management from compute
- **HBM backing** for the cold heap and bulk data

The ISA is designed around ~8 semantic instruction families that map directly to Lisp operations: tagged arithmetic, allocation, barriered stores, inline-cache dispatch, prefetch for pointer graphs, lightweight messaging, bulk region operations, and optional capability-based safety.

## Design Principles

1. **Type is always cheap to know.** Every machine word is self-describing via compact tags.
2. **Fast path is hardware, slow path is runtime.** Keep cores small; trap to software for rare cases.
3. **GC is an architectural citizen.** Barriers, forwarding, scanning are ISA-level concepts.
4. **Many tiles beat one big core.** Area goes to tile count and SRAM, not OoO windows.
5. **Explicit movement beats implicit coherence** at scale. Local heaps + message passing + DMA.

## Documentation Map

| Document | Description |
|----------|-------------|
| [spec/00-overview.md](spec/00-overview.md) | Architecture overview and design rationale |
| [spec/01-object-model.md](spec/01-object-model.md) | Universal object word model and tagging scheme |
| [spec/02-isa.md](spec/02-isa.md) | Instruction set architecture specification |
| [spec/03-core.md](spec/03-core.md) | Tile core microarchitecture |
| [spec/04-soc.md](spec/04-soc.md) | SoC fabric: tiles, clusters, NoC, HBM |
| [spec/05-memory-gc.md](spec/05-memory-gc.md) | Memory model, GC invariants, and barrier protocol |
| [spec/06-runtime.md](spec/06-runtime.md) | Runtime strategy and programming model |
| [spec/07-encoding.md](spec/07-encoding.md) | Instruction encoding and binary formats |

### Design Documents

| Document | Description |
|----------|-------------|
| [design/emulator.md](design/emulator.md) | Emulator architecture and bringup plan |
| [design/bios.md](design/bios.md) | BIOS / firmware: boot sequence, trap tables, image loader |
| [design/os.md](design/os.md) | Lispos: actors, object system, GC, storage, REPL |
| [design/desktop.md](design/desktop.md) | Crystal: GEM-inspired desktop (current implementation) |
| [design/surface.md](design/surface.md) | Surface: next-gen Lisp-native interface concept |

### RTL Implementation

| Directory | Description |
|-----------|-------------|
| [rtl/core/](rtl/core/) | CPU core: ALU, decoder, control FSM, register file, LSU, I-cache, IC table |
| [rtl/tile/](rtl/tile/) | Tile: core + SRAM + message queues |
| [rtl/cluster/](rtl/cluster/) | Cluster: 8 tiles + crossbar + shared SRAM |
| [rtl/gc/](rtl/gc/) | GC engines: scanner, copier, fixup |
| [rtl/tech/](rtl/tech/) | Technology wrappers: SRAMs, clock gating |
| [rtl/target/xilinx7/](rtl/target/xilinx7/) | FPGA target: Genesys 2 (Kintex-7 325T) constraints |
| [rtl/doc/](rtl/doc/) | RTL design documentation |
| [fpga/](fpga/) | FPGA top-level wrapper + synthesis file lists |
| [tb/](tb/) | Verilator testbench: 42 tests, test generators |

### Emulator

| Directory | Description |
|-----------|-------------|
| [emu/lm1/](emu/lm1/) | Python emulator: core, executor, compiler, VDI, desktop, VFS, toolkit |
| [emu/tests/](emu/tests/) | Emulator test suite: 258 tests across 13 phases |

## Influences and Prior Art

- **Classic Lisp machines:** Symbolics 3600, TI Explorer (tagged architectures, microcoded dispatch)
- **Modern tile architectures:** Graphcore GC200 (1472 tiles, 624 KiB SRAM each), Tenstorrent (local SRAM + explicit movement)
- **Tagged memory:** Arm MTE (tag bits on pointers/memory)
- **Capability hardware:** CHERI (bounds, permissions, compartmentalization)
- **JIT/VM techniques:** Inline caches (Smalltalk-80 → V8/SpiderMonkey), generational GC barriers

## License

TBD

## Contributing

TBD
