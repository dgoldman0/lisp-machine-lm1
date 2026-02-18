// ============================================================================
// LM-1 Register File — FPGA (Xilinx 7-Series) Target Override
//
// Functionally identical to the pure RTL version in rtl/core/lm1_regfile.sv.
// The ONLY change is the (* ram_style = "distributed" *) attribute on the
// register array, which directs Yosys / Vivado to infer LUTRAM (RAM128X1D)
// instead of expanding async reads into massive $shiftx mux trees that
// stall TECHMAP for 30+ minutes.
//
// LUTRAM cost: 128 × 64 bits = 8 192 bits → ~256 LUTs (RAM128X1D, 2R+1W).
// ============================================================================
module lm1_regfile
    import lm1_pkg::*;
(
    input  logic                      clk,
    input  logic                      rst_n,

    // Read port A (combinational)
    input  logic [FULL_REG_W-1:0]     ra_addr,
    output logic [XLEN-1:0]           ra_data,

    // Read port B (combinational)
    input  logic [FULL_REG_W-1:0]     rb_addr,
    output logic [XLEN-1:0]           rb_data,

    // Write port
    input  logic                      w_en,
    input  logic [FULL_REG_W-1:0]     w_addr,
    input  logic [XLEN-1:0]           w_data
);

    localparam int TOTAL_REGS = NUM_THREADS * NREGS;

    // Force Xilinx distributed RAM (LUTRAM) — supports async reads natively.
    (* ram_style = "distributed" *)
    logic [XLEN-1:0] regs [0:TOTAL_REGS-1];

    // Asynchronous reads (same as pure RTL)
    assign ra_data = regs[ra_addr];
    assign rb_data = regs[rb_addr];

    // Synchronous write
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < TOTAL_REGS; i++)
                regs[i] = '0;
        end else if (w_en) begin
            regs[w_addr] <= w_data;
        end
    end

endmodule
