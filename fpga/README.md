# LM-1 FPGA Build

FPGA-specific build infrastructure that wraps the pure (technology-agnostic)
RTL under `rtl/`.  Nothing in this directory modifies the core RTL — it adds
an FPGA top-level wrapper.

## Directory Layout

```
fpga/
├── rtl/
│   └── lm1_fpga_top.sv        # FPGA top-level (clock, reset, LED, tie-offs)
├── syn/
│   └── filelist.f              # Ordered list of all RTL sources
└── README.md                   # This file
```

Target-specific synthesis scripts live under `rtl/target/<family>/`.
See [rtl/target/xilinx7/README.md](../rtl/target/xilinx7/README.md)
for the Xilinx 7-series Yosys flow.

## What Gets Synthesized

A single `lm1_tile` with:

- 128 KiB tile SRAM (16K × 64-bit words, BRAM-inferred)
- All crossbar, GC, NoC, and DMA ports tied off (single-tile mode)
- External memory port exposed for program loading
- Debug register read port exposed
- 4 status LEDs (halted, reset, PC LSB, heartbeat)

The cluster / crossbar / GC modules are included in the file list but
will be optimized away since their ports are unconnected.
