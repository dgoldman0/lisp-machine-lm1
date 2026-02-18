# ===========================================================================
# Vivado TCL script — run synthesis (batch mode)
#
# Usage:
#   cd <repo>/fpga/syn/vivado
#   vivado -mode batch -source create_project.tcl   # first time only
#   vivado -mode batch -source run_synth.tcl
# ===========================================================================

set proj_dir [file join [file dirname [info script]] lm1_proj]

open_project [file join $proj_dir lm1.xpr]

# Run synthesis
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1

# Report utilisation
open_run synth_1
report_utilization -file [file join $proj_dir synth_util.rpt]
report_timing_summary -file [file join $proj_dir synth_timing.rpt]

puts "=== Synthesis complete ==="
puts "Utilization report: [file join $proj_dir synth_util.rpt]"
puts "Timing report:      [file join $proj_dir synth_timing.rpt]"
