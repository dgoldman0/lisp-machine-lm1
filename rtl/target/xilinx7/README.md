# LM-1 — Xilinx 7-Series Target (Genesys 2 / Kintex-7 325T)

Open-source synthesis flow using **Yosys** (`synth_xilinx`) + **sv2v**.

## Target Board

**Digilent Genesys 2** — `xc7k325tffg900-2`

| Resource | Available | Budget (8 tiles) |
|---|---|---|
| LUTs | 203,800 | ~130K (64%) |
| FFs | 407,600 | ~155K (38%) |
| BRAM (36Kb) | 445 | ~272 (61%) |
| DSP48E1 | 840 | ~160 (19%) |

## Prerequisites

- [sv2v](https://github.com/zachjs/sv2v) (>= 0.0.13)
- [Yosys](https://github.com/YosysHQ/yosys) (>= 0.30 recommended)
- Optional: [nextpnr-xilinx](https://github.com/gatecat/nextpnr-xilinx)
  for place-and-route / bitstream generation

## Quick Start

```bash
cd rtl/target/xilinx7
make synth          # runs sv2v + Yosys, produces lm1.json + reports
make report         # prints the utilization report
```

## Outputs

| File | Description |
|------|-------------|
| `lm1.json` | JSON netlist (for nextpnr-xilinx) |
| `lm1.edif` | EDIF netlist (alternate interchange format) |
| `lm1_utilization.rpt` | Resource utilization summary |
| `lm1_synth.log` | Full Yosys log |

## What Gets Synthesized

A full `lm1_cluster` (8 tiles) wrapped by `lm1_fpga_top`:

- **8 × lm1_tile** (each: CPU + 64 KiB tile SRAM + icache + msg_queue)
- **lm1_crossbar** — 8-port round-robin arbiter to shared SRAM
- **512 KiB cluster shared SRAM** (dual-port, crossbar + GC engines)
- **lm1_gc_engine_top** — scanner, copier, fixup engines
- External memory port (tile 0) for program loading
- Debug register read (tile 0)
- 8 status LEDs (one per tile halted)

## FPGA-Specific RTL Overrides

Four modules under `rtl/` are FPGA-specific replacements that carry
synthesis attributes or structural changes for Yosys/BRAM inference.
The pure RTL under `rtl/core/` is never modified.

| Module | Change | Reason |
|---|---|---|
| `lm1_regfile` | No async reset, `(* ram_style = "distributed" *)` | LUTRAM inference |
| `lm1_tmpl_table` | No async reset, `(* ram_style = "distributed" *)` | LUTRAM inference |
| `lm1_icache` | Sync BRAM data_store, IC_HIT/IC_SETTLE states | Block RAM inference |
| `lm1_msg_queue` | 4×1D arrays, no mem reset, DEPTH=32 | LUTRAM inference |

## Pin Constraints

`genesys2.xdc` has constraints for the Digilent Genesys 2. Adjust
for alternate boards.

## Notes

- `(* ram_style = "block" *)` on SRAM models → Yosys BRAM inference.
- `(* ram_style = "distributed" *)` on small arrays → Yosys LUTRAM.
- Async resets on storage arrays **prevent** Yosys LUTRAM inference —
  FPGA overrides remove them (array content is bitstream-initialised).
- The latch-based clock gate (`lm1_clock_gate`) maps to LUT-based gating;
  on FPGA this is typically optimized away by the synthesizer.

