# LM-1 — Xilinx 7-Series Target

Open-source synthesis flow using **Yosys** (`synth_xilinx`).

## Prerequisites

- [Yosys](https://github.com/YosysHQ/yosys) (>= 0.30 recommended)
- Optional: [nextpnr-xilinx](https://github.com/gatecat/nextpnr-xilinx)
  for place-and-route / bitstream generation

## Quick Start

```bash
cd rtl/target/xilinx7
make synth          # runs Yosys, produces lm1.json + utilization report
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

A single `lm1_tile` wrapped by `lm1_fpga_top`:

- 128 KiB tile SRAM (16K × 64-bit, inferred as BRAM)
- Crossbar, GC, NoC, DMA ports tied off (single-tile mode)
- External memory port for program loading
- Debug register read port
- 4 status LEDs

## Pin Constraints

`arty_a7.xdc` has placeholder constraints for the Digilent Arty A7-100T.
Edit for your board.

## Notes

- `(* ram_style = "block" *)` attributes on SRAM models are recognized by
  Yosys `synth_xilinx` for BRAM inference.
- The latch-based clock gate (`lm1_clock_gate`) maps to LUT-based gating;
  on FPGA this is typically optimized away by the synthesizer.
- To add a new board target (e.g. Nexys, Basys), copy this directory and
  adjust the XDC and `synth.ys` parameters.
