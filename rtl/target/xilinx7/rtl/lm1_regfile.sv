// ============================================================================
// LM-1 Register File — FPGA (Xilinx 7-Series) Target Override v2
//
// Same port interface as the pure RTL version (rtl/core/lm1_regfile.sv).
//
// Optimisation vs v1:
//   - Async reset removed from the regs[] array.  The v1 version had
//     `if (!rst_n) for (i) regs[i] = '0;` which forces Yosys to use
//     individual FFs (8 192 FDCE + 5 376 LUT6 = 5 530 LCs) because
//     LUTRAM primitives have no async reset.  Without the reset, Yosys
//     can attempt to infer RAM128X1D distributed RAM.
//   - For simulation, an `initial` block zeroes the array (synthesis-
//     ignored).  On FPGA, LUTRAM/BRAM is bitstream-initialised to 0.
//   - (* ram_style = "distributed" *) retained as a hint to Yosys.
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

    // Force Xilinx distributed RAM (LUTRAM).
    // No async reset — LUTRAM is bitstream-initialised; runtime reset
    // would force Yosys to fall back to individual flip-flops.
    (* ram_style = "distributed" *)
    logic [XLEN-1:0] regs [0:TOTAL_REGS-1];

    // Simulation-only initialisation (ignored by synthesis)
    // synthesis translate_off
    initial begin
        for (int i = 0; i < TOTAL_REGS; i++)
            regs[i] = '0;
    end
    // synthesis translate_on

    // Asynchronous reads (same as pure RTL)
    assign ra_data = regs[ra_addr];
    assign rb_data = regs[rb_addr];

    // Synchronous write — no reset, clean pattern for RAM inference
    always_ff @(posedge clk) begin
        if (w_en)
            regs[w_addr] <= w_data;
    end

endmodule
