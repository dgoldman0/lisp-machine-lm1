// ============================================================================
// LM-1 Inline Cache (IC) Table
//
// Fully-associative 16-entry inline cache for CALL.IC / TAILCALL.IC.
// Each entry maps (callsite_pc, shape_id) → code_target.
//
// Lookup is combinational: present (pc, shape) on the same cycle,
// get hit/miss + target immediately.
//
// Install writes a new entry, evicting the oldest (FIFO pointer).
// ============================================================================
module lm1_ic_table
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // Lookup (combinational)
    input  logic [XLEN-1:0]   lu_pc,
    input  logic [31:0]        lu_shape,
    input  logic               lu_valid,
    output logic [XLEN-1:0]   hit_target,
    output logic               hit,

    // Install (synchronous)
    input  logic               inst_valid,
    input  logic [XLEN-1:0]   inst_pc,
    input  logic [31:0]        inst_shape,
    input  logic [XLEN-1:0]   inst_target
);

    localparam int N = 64;

    // Entry storage
    logic               valid [0:N-1];
    logic [XLEN-1:0]    e_pc  [0:N-1];
    logic [31:0]         e_shp [0:N-1];
    logic [XLEN-1:0]    e_tgt [0:N-1];

    // FIFO evict pointer
    logic [5:0] evict_ptr;

    // Combinational lookup
    always_comb begin
        hit        = 1'b0;
        hit_target = '0;
        if (lu_valid) begin
            for (int i = 0; i < N; i++) begin
                if (valid[i] && e_pc[i] == lu_pc && e_shp[i] == lu_shape) begin
                    hit        = 1'b1;
                    hit_target = e_tgt[i];
                end
            end
        end
    end

    // Synchronous install
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            evict_ptr <= '0;
            for (int i = 0; i < N; i++) begin
                valid[i] <= 1'b0;
                e_pc[i]  <= '0;
                e_shp[i] <= '0;
                e_tgt[i] <= '0;
            end
        end else if (inst_valid) begin
            valid[evict_ptr] <= 1'b1;
            e_pc[evict_ptr]  <= inst_pc;
            e_shp[evict_ptr] <= inst_shape;
            e_tgt[evict_ptr] <= inst_target;
            evict_ptr        <= evict_ptr + 6'd1;
        end
    end

endmodule
