// ============================================================================
// LM-1 Inline Cache (IC) Table — FPGA (Xilinx 7-Series) Target Override
//
// Same port interface as the pure RTL version (rtl/core/lm1_ic_table.sv).
// Changes for FPGA synthesis:
//
//   1. Entry count reduced from 64 to 16 — the CAM is inherently register-
//      based (every entry compared simultaneously), so 64 entries explode
//      to ~10K FFs + ~6K LUTs per tile.  16 entries is sufficient for
//      FPGA validation and reduces the CAM to ~2.6K FFs.
//
//   2. Async reset removed from storage arrays (valid, e_pc, e_shp, e_tgt).
//      This eliminates ~10K INV cells per tile (FDCE → FDRE).  Storage is
//      initialised via `initial` blocks (FPGA bitstream init).
//
//   3. evict_ptr keeps async reset (6 bits — negligible).
//
// Pure RTL under rtl/core/ is untouched.
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

    // Reduced from 64 (pure RTL) to 16 for FPGA area
    localparam int N = 16;

    // Entry storage — no async reset for FDRE inference
    logic               valid [0:N-1];
    logic [XLEN-1:0]    e_pc  [0:N-1];
    logic [31:0]         e_shp [0:N-1];
    logic [XLEN-1:0]    e_tgt [0:N-1];

    // FIFO evict pointer
    logic [3:0] evict_ptr;

    // Simulation initialisation (maps to FPGA bitstream init)
    initial begin
        evict_ptr = '0;
        for (int i = 0; i < N; i++) begin
            valid[i] = 1'b0;
            e_pc[i]  = '0;
            e_shp[i] = '0;
            e_tgt[i] = '0;
        end
    end

    // Combinational lookup — 16-way CAM
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

    // Synchronous install — no async reset on storage, only evict_ptr
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            evict_ptr <= '0;
        end else if (inst_valid) begin
            valid[evict_ptr] <= 1'b1;
            e_pc[evict_ptr]  <= inst_pc;
            e_shp[evict_ptr] <= inst_shape;
            e_tgt[evict_ptr] <= inst_target;
            evict_ptr        <= evict_ptr + 4'd1;
        end
    end

endmodule
