# ============================================================================
# LM-1 Synthesis File List
#
# Lists all RTL source files in dependency order (packages first).
# Paths are relative to the repository root.
#
# Usage:
#   - Yosys:   see rtl/target/xilinx7/synth.ys
#   - Generic: read_verilog -sv -f filelist.f
# ============================================================================

# --- Package (must come first) ---
rtl/core/lm1_pkg.sv

# --- Technology primitives ---
rtl/tech/lm1_clock_gate.sv
rtl/tech/lm1_sram_sp.sv
rtl/tech/lm1_sram_dp.sv

# --- Core modules ---
rtl/core/lm1_decoder.sv
rtl/core/lm1_regfile.sv
rtl/core/lm1_alu.sv
rtl/core/lm1_branch.sv
rtl/core/lm1_lsu.sv
rtl/core/lm1_icache.sv
rtl/core/lm1_tmpl_table.sv
rtl/core/lm1_ic_table.sv
rtl/core/lm1_msg_queue.sv
rtl/core/lm1_perf_counters.sv
rtl/core/lm1_control.sv
rtl/core/lm1_cpu.sv

# --- GC engine ---
rtl/gc/lm1_gc_scanner.sv
rtl/gc/lm1_gc_copier.sv
rtl/gc/lm1_gc_fixup.sv
rtl/gc/lm1_gc_engine_top.sv

# --- Tile ---
rtl/tile/lm1_tile.sv

# --- Cluster (not used in single-tile FPGA build, but included
#     for completeness; synthesizer will trim unused logic) ---
rtl/cluster/lm1_crossbar.sv
rtl/cluster/lm1_cluster.sv

# --- FPGA top ---
fpga/rtl/lm1_fpga_top.sv
