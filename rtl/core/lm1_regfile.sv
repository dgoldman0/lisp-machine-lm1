// ============================================================================
// LM-1 Register File  (128 x 64-bit — 4 threads × 32 GPRs)
//
// Synchronous write, asynchronous read (combinational read ports).
// Two independent read ports (A, B) and one write port (W).
//
// Address format: {thread_id[1:0], reg_idx[4:0]} — 7 bits total.
// Each thread has its own bank of 32 registers.
//
// NOTE: r0 is NOT hardwired to zero — all 32 registers are general-purpose.
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

    // 128 registers (4 threads × 32), each 64 bits
    localparam int TOTAL_REGS = NUM_THREADS * NREGS;
    logic [XLEN-1:0] regs [0:TOTAL_REGS-1];

    // Asynchronous read — no write-through bypass.
    // The multi-cycle FSM never needs same-cycle read-after-write;
    // reads and writes always occur in separate FSM states, so the
    // synchronous write (available next posedge) is sufficient.
    // Removing the bypass eliminates combinational loops through
    // read-modify-write patterns (PUSH SP, ALLOC NP, PUSH_FRAME SP).
    assign ra_data = regs[ra_addr];
    assign rb_data = regs[rb_addr];

    // Synchronous write
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < TOTAL_REGS; i++)
                regs[i] = '0;  // blocking OK in reset (Verilator compat)
        end else if (w_en) begin
            regs[w_addr] <= w_data;
        end
    end

endmodule
