## ============================================================================
## LM-1 Pin Constraints — Digilent Arty A7-100T
##
## Placeholder — adjust LOC and IOSTANDARD for your actual board.
## These are used by nextpnr-xilinx (if/when doing P&R) or as
## documentation of the intended pin mapping.
## ============================================================================

## Clock — 100 MHz system oscillator
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports sys_clk]
create_clock -period 10.000 -name sys_clk [get_ports sys_clk]

## Reset — active-low push button (BTN0)
set_property -dict {PACKAGE_PIN D9 IOSTANDARD LVCMOS33} [get_ports sys_rst_n]

## LEDs
set_property -dict {PACKAGE_PIN H5  IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN J5  IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T9  IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

## External memory port — directly accessible via ILA or PMOD headers.
## Pin assignments TBD based on board wiring / PMOD usage.
## For now these are left unconstrained (synthesis-only).

## Debug register port — directly accessible via ILA.
## Pin assignments TBD.
