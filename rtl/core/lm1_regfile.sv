// ============================================================================
// LM-1 Register File  (32 x 64-bit)
//
// Synchronous write, asynchronous read (combinational read ports).
// Two independent read ports (A, B) and one write port (W).
//
// NOTE: r0 is NOT hardwired to zero — all 32 registers are general-purpose.
//
// For ASIC:  This infers a register file from flip-flops.  For area
//            optimization, replace with a compiled RF macro.
// For FPGA:  Distributed RAM or LUT-based — appropriate for 32x64.
// ============================================================================
module lm1_regfile
    import lm1_pkg::*;
(
    input  logic                    clk,
    input  logic                    rst_n,

    // Read port A (combinational)
    input  logic [REG_IDX_W-1:0]   ra_addr,
    output logic [XLEN-1:0]        ra_data,

    // Read port B (combinational)
    input  logic [REG_IDX_W-1:0]   rb_addr,
    output logic [XLEN-1:0]        rb_data,

    // Write port
    input  logic                    w_en,
    input  logic [REG_IDX_W-1:0]   w_addr,
    input  logic [XLEN-1:0]        w_data
);

    // 32 registers, each 64 bits
    logic [XLEN-1:0] regs [0:NREGS-1];

    // Asynchronous read with write-through (bypass)
    // If reading the same register being written, return the new value.
    always_comb begin
        if (w_en && (ra_addr == w_addr))
            ra_data = w_data;
        else
            ra_data = regs[ra_addr];
    end

    always_comb begin
        if (w_en && (rb_addr == w_addr))
            rb_data = w_data;
        else
            rb_data = regs[rb_addr];
    end

    // Synchronous write
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < NREGS; i++)
                regs[i] <= '0;
        end else if (w_en) begin
            regs[w_addr] <= w_data;
        end
    end

endmodule
