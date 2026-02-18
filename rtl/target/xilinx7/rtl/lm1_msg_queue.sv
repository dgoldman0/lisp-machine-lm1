// ============================================================================
// LM-1 Hardware Message Queue — FPGA (Xilinx 7-Series) Target Override v2
//
// Same port interface as the pure RTL version (rtl/core/lm1_msg_queue.sv).
//
// Optimisation vs v1:
//   - 2D array mem[Q][D] split into 4 independent 1D arrays via generate.
//     Yosys could not infer distributed RAM from 2D arrays — it expanded
//     them into individual FFs with massive $shiftx mux trees (15 293 LCs).
//     Separate 1D arrays let Yosys see a clean single-port write +
//     single-port async-read pattern per queue.
//   - Async reset removed from storage arrays.  LUTRAM/BRAM cannot be
//     reset at runtime; content is initialised via the FPGA bitstream.
//     This prevents the "Replacing memory with list of registers" warning
//     that forces Yosys to fall back to FFs.
//   - (* ram_style = "distributed" *) retained — if Yosys infers LUTRAM
//     (RAM32X1S), cost is ~256 LUTs total.  If not, the 1D structure
//     still produces simpler 32:1 read muxes than the old 2D 128:1.
//
// DEPTH kept at 32 (small enough for distributed; bump later if BRAM).
// ============================================================================
module lm1_msg_queue
    import lm1_pkg::*;
#(
    parameter int DEPTH      = 32,
    parameter int NUM_QUEUES = 4
)
(
    input  logic               clk,
    input  logic               rst_n,

    // --- Core write port (SEND) ---
    input  logic               wr_en,
    input  logic [1:0]         wr_id,
    input  logic [XLEN-1:0]   wr_data,
    output logic               wr_ready,

    // --- Core read port (RECV) ---
    input  logic               rd_en,
    input  logic [1:0]         rd_id,
    output logic [XLEN-1:0]   rd_data,
    output logic               rd_valid,

    // --- External write port (NoC → queue) ---
    input  logic               ext_wr_en,
    input  logic [1:0]         ext_wr_id,
    input  logic [XLEN-1:0]   ext_wr_data,
    output logic               ext_wr_ready,

    // --- External read port (queue → NoC) ---
    input  logic               ext_rd_en,
    input  logic [1:0]         ext_rd_id,
    output logic [XLEN-1:0]   ext_rd_data,
    output logic               ext_rd_valid,

    // --- Status ---
    output logic [NUM_QUEUES-1:0] q_empty,
    output logic [NUM_QUEUES-1:0] q_full
);

    localparam int ADDR_W = $clog2(DEPTH);

    // Per-queue pointers (extra MSB for full/empty discrimination)
    logic [ADDR_W:0] wr_ptr [0:NUM_QUEUES-1];
    logic [ADDR_W:0] rd_ptr [0:NUM_QUEUES-1];

    // Per-queue head word (async read of current rd_ptr position)
    logic [XLEN-1:0] q_head [0:NUM_QUEUES-1];

    // Derived write/read enables per queue
    logic [NUM_QUEUES-1:0] q_wr_en, q_rd_en;
    logic [XLEN-1:0]      q_wr_data [0:NUM_QUEUES-1];

    // ----------------------------------------------------------------
    // Status (combinational, from pointers)
    // ----------------------------------------------------------------
    genvar g;
    generate
        for (g = 0; g < NUM_QUEUES; g++) begin : gen_status
            assign q_empty[g] = (wr_ptr[g] == rd_ptr[g]);
            assign q_full[g]  = (wr_ptr[g][ADDR_W] != rd_ptr[g][ADDR_W]) &&
                                (wr_ptr[g][ADDR_W-1:0] == rd_ptr[g][ADDR_W-1:0]);
        end
    endgenerate

    // ----------------------------------------------------------------
    // Per-queue FIFO storage — independent 1D arrays
    //
    // Each queue is a separate generate instance so Yosys sees a clean
    // 1-write / 1-async-read memory that can map to LUTRAM.
    // No reset on storage — LUTRAM is bitstream-initialised.
    // ----------------------------------------------------------------
    generate
        for (g = 0; g < NUM_QUEUES; g++) begin : gen_fifo
            (* ram_style = "distributed" *)
            logic [XLEN-1:0] q_mem [0:DEPTH-1];

            // Async read — head of this queue
            assign q_head[g] = q_mem[rd_ptr[g][ADDR_W-1:0]];

            // Sync write, no reset
            always_ff @(posedge clk) begin
                if (q_wr_en[g])
                    q_mem[wr_ptr[g][ADDR_W-1:0]] <= q_wr_data[g];
            end
        end
    endgenerate

    // ----------------------------------------------------------------
    // Output muxes — select head word by queue ID
    // ----------------------------------------------------------------
    assign wr_ready     = ~q_full[wr_id];
    assign rd_valid     = ~q_empty[rd_id];
    assign rd_data      = q_head[rd_id];

    assign ext_wr_ready = ~q_full[ext_wr_id];
    assign ext_rd_valid = ~q_empty[ext_rd_id];
    assign ext_rd_data  = q_head[ext_rd_id];

    // ----------------------------------------------------------------
    // Write/read arbitration — core has priority over external
    // ----------------------------------------------------------------
    always_comb begin
        for (int i = 0; i < NUM_QUEUES; i++) begin
            q_wr_en[i]   = 1'b0;
            q_wr_data[i] = '0;
            q_rd_en[i]   = 1'b0;
        end

        // Core write
        if (wr_en && ~q_full[wr_id]) begin
            q_wr_en[wr_id]   = 1'b1;
            q_wr_data[wr_id] = wr_data;
        end
        // External write (only if core not writing to same queue)
        if (ext_wr_en && ~q_full[ext_wr_id]) begin
            if (!(wr_en && wr_id == ext_wr_id)) begin
                q_wr_en[ext_wr_id]   = 1'b1;
                q_wr_data[ext_wr_id] = ext_wr_data;
            end
        end

        // Core read
        if (rd_en && ~q_empty[rd_id])
            q_rd_en[rd_id] = 1'b1;
        // External read (only if core not reading same queue)
        if (ext_rd_en && ~q_empty[ext_rd_id]) begin
            if (!(rd_en && rd_id == ext_rd_id))
                q_rd_en[ext_rd_id] = 1'b1;
        end
    end

    // ----------------------------------------------------------------
    // Pointer management (only pointers are reset, not storage)
    // ----------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < NUM_QUEUES; i++) begin
                wr_ptr[i] <= '0;
                rd_ptr[i] <= '0;
            end
        end else begin
            for (int i = 0; i < NUM_QUEUES; i++) begin
                if (q_wr_en[i])
                    wr_ptr[i] <= wr_ptr[i] + 1;
                if (q_rd_en[i])
                    rd_ptr[i] <= rd_ptr[i] + 1;
            end
        end
    end

endmodule
