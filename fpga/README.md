# LM-1 FPGA Build

FPGA-specific build infrastructure that wraps the pure (technology-agnostic)
RTL under `rtl/`.  Nothing in this directory modifies the core RTL — it adds
an FPGA top-level wrapper and synthesis project files.

## Directory Layout

```
fpga/
├── rtl/
│   └── lm1_fpga_top.sv        # FPGA top-level (clock, reset, LED, tie-offs)
├── syn/
│   ├── filelist.f              # Ordered list of all RTL sources
│   └── vivado/
│       ├── create_project.tcl  # Create Vivado project (batch)
│       ├── run_synth.tcl       # Run synthesis + reports (batch)
│       └── lm1.xdc            # Pin constraints (placeholder — edit for your board)
└── README.md                   # This file
```

## Quick Start (Vivado)

```bash
cd fpga/syn/vivado
vivado -mode batch -source create_project.tcl
vivado -mode batch -source run_synth.tcl
```

Or open the generated project in the Vivado GUI:

```bash
vivado fpga/syn/vivado/lm1_proj/lm1.xpr
```

## Target Board

The default constraints target **Digilent Arty A7-100T**
(`xc7a100tcsg324-1`).  To retarget:

1. Edit `create_project.tcl` — change the `part` variable.
2. Edit `lm1.xdc` — update pin LOC and IOSTANDARD.

## What Gets Synthesized

A single `lm1_tile` with:

- 128 KiB tile SRAM (16K × 64-bit words, BRAM-inferred)
- All crossbar, GC, NoC, and DMA ports tied off (single-tile mode)
- External memory port exposed for program loading via ILA / JTAG
- Debug register read port exposed
- 4 status LEDs (halted, reset, PC LSB, heartbeat)

The cluster / crossbar / GC modules are included in the file list but
will be optimized away since their ports are unconnected.

## Notes

- The SRAM primitives (`lm1_sram_sp`, `lm1_sram_dp`) already carry
  `(* ram_style = "block" *)` attributes for Xilinx BRAM inference.
- The latch-based clock gate (`lm1_clock_gate`) synthesizes correctly
  on Xilinx 7-series — the tools infer BUFGCE or CE-based gating.
- For Intel/Quartus, create an equivalent project script and adjust
  the SRAM attribute to `(* ramstyle = "M20K" *)`.
