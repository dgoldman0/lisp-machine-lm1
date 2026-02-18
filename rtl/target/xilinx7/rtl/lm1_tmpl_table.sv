// ============================================================================
// LM-1 Header Template Table — FPGA (Xilinx 7-Series) Target Override
//
// Functionally identical to the pure RTL version in rtl/core/lm1_tmpl_table.sv.
// The ONLY change is the (* ram_style = "distributed" *) attribute on the
// entries array, directing Yosys to infer Xilinx LUTRAM (RAM256X1S) instead
// of expanding a 256:1 async read mux into $shiftx cells.
//
// LUTRAM cost: 256 × 64 bits = 16 384 bits → ~1024 LUTs (RAM256X1S, 1R+1W).
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

    // Force Xilinx distributed RAM (LUTRAM) — supports async reads natively.
    (* ram_style = "distributed" *)
    logic [XLEN-1:0] entries [0:255];

    // Combinational read (same as pure RTL)
    assign rd_data = entries[rd_idx];

    // synthesis translate_off
    initial begin
        for (int i = 0; i < 256; i++)
            entries[i] = '0;
    end
    // synthesis translate_on

    // Synchronous write with reset
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < 256; i++)
                entries[i] = '0;
        end else if (wr_en) begin
            entries[wr_idx] <= wr_data;
        end
    end

endmodule
