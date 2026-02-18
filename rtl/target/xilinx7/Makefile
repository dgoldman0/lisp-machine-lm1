# ============================================================================
# LM-1 Yosys Synthesis — Xilinx 7-Series
#
# Flow: sv2v (SV → Verilog-2005) → Yosys (synth_xilinx)
#
# Targets:
#   make synth      Run full flow, produce utilization report + netlist
#   make convert    Run sv2v conversion only
#   make report     Print utilization report
#   make clean      Remove build artifacts
# ============================================================================

YOSYS   ?= yosys
SV2V    ?= sv2v
DESIGN  := lm1
TOP     := lm1_fpga_top
ROOT    := ../../..

# All SV sources in dependency order.
# Five modules are swapped for FPGA-specific versions (in rtl/ subdir)
# that carry ram_style attributes or structural changes for Yosys/BRAM:
#   lm1_regfile      → distributed LUTRAM (async 2R+1W)
#   lm1_tmpl_table   → distributed LUTRAM (async 1R+1W)
#   lm1_icache       → block RAM data_store (sync read, +IC_HIT state)
#   lm1_ic_table     → N 64→16, no async reset (FDRE, smaller CAM)
#   lm1_msg_queue    → DEPTH 512→32, distributed LUTRAM
TARGET_RTL := rtl

SV_SRCS := \
	$(ROOT)/rtl/core/lm1_pkg.sv \
	$(ROOT)/rtl/tech/lm1_clock_gate.sv \
	$(ROOT)/rtl/tech/lm1_sram_sp.sv \
	$(ROOT)/rtl/tech/lm1_sram_dp.sv \
	$(ROOT)/rtl/core/lm1_decoder.sv \
	$(TARGET_RTL)/lm1_regfile.sv \
	$(ROOT)/rtl/core/lm1_alu.sv \
	$(ROOT)/rtl/core/lm1_branch.sv \
	$(ROOT)/rtl/core/lm1_lsu.sv \
	$(TARGET_RTL)/lm1_icache.sv \
	$(TARGET_RTL)/lm1_tmpl_table.sv \
	$(TARGET_RTL)/lm1_ic_table.sv \
	$(TARGET_RTL)/lm1_msg_queue.sv \
	$(ROOT)/rtl/core/lm1_perf_counters.sv \
	$(ROOT)/rtl/core/lm1_control.sv \
	$(ROOT)/rtl/core/lm1_cpu.sv \
	$(ROOT)/rtl/gc/lm1_gc_scanner.sv \
	$(ROOT)/rtl/gc/lm1_gc_copier.sv \
	$(ROOT)/rtl/gc/lm1_gc_fixup.sv \
	$(ROOT)/rtl/gc/lm1_gc_engine_top.sv \
	$(ROOT)/rtl/tile/lm1_tile.sv \
	$(ROOT)/rtl/cluster/lm1_crossbar.sv \
	$(ROOT)/rtl/cluster/lm1_cluster.sv \
	$(ROOT)/fpga/rtl/lm1_fpga_top.sv

# Build outputs
VOUT    := $(DESIGN)_all.v
JSON    := $(DESIGN).json
EDIF    := $(DESIGN).edif
RPT     := $(DESIGN)_utilization.rpt
LOG     := $(DESIGN)_synth.log
TLOG    := $(DESIGN)_timing.log

.PHONY: synth convert report clean

synth: $(JSON)

# Step 1: sv2v converts SystemVerilog → Verilog-2005
$(VOUT): $(SV_SRCS)
	@echo "=== $$(date '+%Y-%m-%d %H:%M:%S') sv2v start ===" | tee $(TLOG)
	$(SV2V) $(SV_SRCS) -w $(VOUT)
	@echo "=== $$(date '+%Y-%m-%d %H:%M:%S') sv2v done ===" | tee -a $(TLOG)

convert: $(VOUT)

# Step 2: Yosys synthesis (timestamps for each pass are in synth.ys)
$(JSON): $(VOUT) synth.ys
	$(YOSYS) -s synth.ys -l $(LOG)
	@echo ""
	@echo "=== Timing Summary ==="
	@grep '^\*\*\*\|^===' $(TLOG)

report: $(RPT)
	@echo "--- Utilization Report ---"
	@cat $(RPT)

clean:
	rm -f $(VOUT) $(JSON) $(EDIF) $(RPT) $(LOG) $(TLOG)
