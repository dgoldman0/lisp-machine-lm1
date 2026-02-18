## ============================================================================
## LM-1 Pin Constraints — Digilent Genesys 2 (Kintex-7 325T)
##
## Target: xc7k325tffg900-2
## Board:  Digilent Genesys 2
##
## Resources:
##   - 203,800 LUTs / 407,600 FFs
##   - 445 × RAMB36E1  (= 890 × 18Kb)
##   - 840 × DSP48E1
##
## This file constrains the cluster-level FPGA top (lm1_fpga_top)
## which wraps one full 8-tile cluster.
## ============================================================================

## ============================================================================
## Clock — 200 MHz LVDS oscillator → internal MMCM → core clock
## ============================================================================
set_property -dict {PACKAGE_PIN AD12 IOSTANDARD LVDS} [get_ports sys_clk_p]
set_property -dict {PACKAGE_PIN AD11 IOSTANDARD LVDS} [get_ports sys_clk_n]
create_clock -period 5.000 -name sys_clk_200 [get_ports sys_clk_p]

## ============================================================================
## Reset — active-low CPU_RESETN push button
## ============================================================================
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports sys_rst_n]

## ============================================================================
## UART — USB-UART bridge (FT232R)
## ============================================================================
set_property -dict {PACKAGE_PIN Y23 IOSTANDARD LVCMOS33} [get_ports uart_txd]
set_property -dict {PACKAGE_PIN Y20 IOSTANDARD LVCMOS33} [get_ports uart_rxd]

## ============================================================================
## Status LEDs — LD0..LD7 (accent LEDs)
## ============================================================================
set_property -dict {PACKAGE_PIN T28 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN V19 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN U30 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN U29 IOSTANDARD LVCMOS33} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN V20 IOSTANDARD LVCMOS33} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN V26 IOSTANDARD LVCMOS33} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN W24 IOSTANDARD LVCMOS33} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN W23 IOSTANDARD LVCMOS33} [get_ports {led[7]}]

## ============================================================================
## Push Buttons — active-high (active after debounce)
## ============================================================================
## BTN_C, BTN_U, BTN_D, BTN_L, BTN_R
## Reserved for future debug / breakpoint use.

## ============================================================================
## DIP Switches — reserved for tile select / debug mode select
## ============================================================================
## SW0..SW7 — directly usable as LVCMOS33 inputs.
## (Pinout not constrained here — add when board-level debug is wired.)

## ============================================================================
## Timing constraints
## ============================================================================
## The core runs from sys_clk_200 through an MMCM.  When the MMCM is
## added, create a generated clock constraint here.  For the initial
## raw-clock synthesis, the 200 MHz input clock applies everywhere.

## False-path for async reset synchroniser
set_false_path -from [get_ports sys_rst_n]
