# ===========================================================================
# Xilinx Design Constraints — LM-1 FPGA build
#
# Target: Digilent Arty A7-100T (xc7a100tcsg324-1)
#         Adjust pin LOC and IOSTANDARD for your board.
#
# This is a PLACEHOLDER — update with actual pin assignments before
# attempting place-and-route.
# ===========================================================================

# --- Clock (100 MHz oscillator on Arty A7) ---
set_property -dict {PACKAGE_PIN E3  IOSTANDARD LVCMOS33} [get_ports sys_clk]
create_clock -period 10.000 -name sys_clk [get_ports sys_clk]

# --- Reset (active-low, directly from BTN0) ---
set_property -dict {PACKAGE_PIN D9  IOSTANDARD LVCMOS33} [get_ports sys_rst_n]

# --- LEDs (LD4..LD7 on Arty) ---
set_property -dict {PACKAGE_PIN H5  IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN J5  IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T9  IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

# --- Timing: false-path on async reset ---
set_false_path -from [get_ports sys_rst_n]

# --- External memory port & debug: PLACEHOLDER ---
# These signals should be routed to PMOD headers, JTAG, or an ILA core.
# In a synthesis-only test, leave them unconnected (optimized away).
# Uncomment and fill in pin assignments when connecting to physical I/O.
#
# set_property -dict {PACKAGE_PIN XX IOSTANDARD LVCMOS33} [get_ports ext_mem_en]
# ... (repeat for each ext_mem / dbg signal)
