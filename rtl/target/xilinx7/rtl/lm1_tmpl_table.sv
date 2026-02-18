// ============================================================================
// LM-1 Header Template Table — FPGA (Xilinx 7-Series) Target Override v2
//
// Same port interface as the pure RTL version (rtl/core/lm1_tmpl_table.sv).
//
// Optimisation vs v1:
//   - Async reset removed from the entries[] array.  The v1 version had
//     `if (!rst_n) for (i) entries[i] = '0;` which forces Yosys to use
//     individual FFs (16 384 FDCE + 5 440 LUT6 = 5 601 LCs) because
//     LUTRAM primitives have no async reset.  Without the reset, Yosys
//     can attempt to infer RAM256X1S distributed RAM.
//   - For simulation, an `initial` block zeroes the array.
//   - (* ram_style = "distributed" *) retained as a hint to Yosys.
// ============================================================================
module lm1_tmpl_table
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // Read port (combinational)
    input  logic [7:0]         rd_idx,
    output logic [XLEN-1:0]   rd_data,

    // Write port (synchronous)
    input  logic               wr_en,
    input  logic [7:0]         wr_idx,
    input  logic [XLEN-1:0]   wr_data
);

    // Force Xilinx distributed RAM (LUTRAM).
    // No async reset — LUTRAM is bitstream-initialised.
    (* ram_style = "distributed" *)
    logic [XLEN-1:0] entries [0:255];

    // Simulation-only initialisation
    // synthesis translate_off
    initial begin
        for (int i = 0; i < 256; i++)
            entries[i] = '0;
    end
    // synthesis translate_on

    // Combinational read (same as pure RTL)
    assign rd_data = entries[rd_idx];

    // Synchronous write — no reset, clean pattern for RAM inference
    always_ff @(posedge clk) begin
        if (wr_en)
            entries[wr_idx] <= wr_data;
    end

endmodule
