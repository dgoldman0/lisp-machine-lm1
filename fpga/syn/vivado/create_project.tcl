# ===========================================================================
# Vivado TCL script — create LM-1 FPGA project
#
# Usage:
#   cd <repo>/fpga/syn/vivado
#   vivado -mode batch -source create_project.tcl
#
# This creates a project in fpga/syn/vivado/lm1_proj/, adds all RTL
# sources from the file list, sets the FPGA top module, and applies
# constraints.  It does NOT run synthesis — use the Vivado GUI or:
#   vivado -mode batch -source run_synth.tcl
# ===========================================================================

set repo_root [file normalize [file join [file dirname [info script]] ../../..]]
set proj_dir  [file join [file dirname [info script]] lm1_proj]
set part      "xc7a100tcsg324-1"    ;# Arty A7-100T

# Create project
create_project lm1 $proj_dir -part $part -force
set_property target_language Verilog [current_project]

# Read file list and add sources
set fl [open [file join $repo_root fpga/syn/filelist.f] r]
while {[gets $fl line] >= 0} {
    set line [string trim $line]
    # skip comments and blank lines
    if {$line eq "" || [string index $line 0] eq "#"} continue
    set fpath [file join $repo_root $line]
    if {[file exists $fpath]} {
        add_files -norecurse $fpath
        set_property file_type SystemVerilog [get_files $fpath]
    } else {
        puts "WARNING: file not found: $fpath"
    }
}
close $fl

# Set top module
set_property top lm1_fpga_top [current_fileset]

# Add constraints
add_files -fileset constrs_1 -norecurse [file join $repo_root fpga/syn/vivado/lm1.xdc]

# Print summary
puts "=== LM-1 FPGA project created ==="
puts "Part:    $part"
puts "Top:     lm1_fpga_top"
puts "Project: $proj_dir"
puts ""
puts "Next steps:"
puts "  1. Open the project in Vivado GUI, or"
puts "  2. launch_runs synth_1 -jobs 4"
