// ============================================================================
// LM-1 Tile Top Module
//
// Instantiates a single DOP core, tile SRAM, and exposes:
//   - Cluster crossbar port (read/write to cluster shared SRAM)
//   - GC engine command forwarding
//   - NoC router port stub
//   - DMA endpoint stub
//
// Memory map (256 KiB tile SRAM, 32K × 64-bit words):
//   0x0000..0xFFFF  — tile SRAM (via CPU internal SRAM)
//   Cluster accesses go through the crossbar port.
//
// Parameters:
//   TILE_MEM_LOG2 — log2(words), default 15 → 32K words → 256 KiB
//   TILE_ID       — unique tile ID within the SoC
// ============================================================================
module lm1_tile
    import lm1_pkg::*;
#(
    parameter int TILE_MEM_LOG2 = 15,     // 32K words = 256 KiB
    parameter int TILE_ID       = 0
)
(
    input  logic               clk,
    input  logic               rst_n,

    // --- Status ---
    output logic               halted,
    output logic [XLEN-1:0]   pc_out,
    output logic [XLEN-1:0]   cycle_count,

    // --- External memory port (for initial program load) ---
    input  logic               ext_mem_en,
    input  logic               ext_mem_we,
    input  logic [TILE_MEM_LOG2-1:0] ext_mem_addr,
    input  logic [XLEN-1:0]   ext_mem_wdata,
    output logic [XLEN-1:0]   ext_mem_rdata,

    // --- Debug ---
    input  logic [REG_IDX_W-1:0] dbg_reg_addr,
    output logic [XLEN-1:0]     dbg_reg_data,

    // --- Cluster crossbar port (tile ↔ cluster shared SRAM) ---
    output logic               xbar_req_valid,
    output logic               xbar_req_we,
    output logic [XLEN-1:0]   xbar_req_addr,
    output logic [XLEN-1:0]   xbar_req_wdata,
    input  logic               xbar_req_ready,
    input  logic [XLEN-1:0]   xbar_resp_data,
    input  logic               xbar_resp_valid,

    // --- GC engine command (forwarded to cluster engine) ---
    output logic               gc_cmd_valid,
    output logic [3:0]         gc_cmd_op,
    output logic [XLEN-1:0]   gc_cmd_arg0,
    output logic [XLEN-1:0]   gc_cmd_arg1,
    input  logic               gc_cmd_ready,
    input  logic               gc_engine_busy,

    // --- NoC message port (external side of queue) ---
    input  logic               noc_mq_wr_en,
    input  logic [1:0]         noc_mq_wr_id,
    input  logic [XLEN-1:0]   noc_mq_wr_data,
    output logic               noc_mq_wr_ready,
    input  logic               noc_mq_rd_en,
    input  logic [1:0]         noc_mq_rd_id,
    output logic [XLEN-1:0]   noc_mq_rd_data,
    output logic               noc_mq_rd_valid,

    // --- Queue status ---
    output logic [3:0]         mq_empty,
    output logic [3:0]         mq_full
);

    // Tile ID as a 64-bit constant
    logic [XLEN-1:0] tile_id_w;
    assign tile_id_w = XLEN'(TILE_ID);

    // Thread ID — single-thread implementation for now
    logic [XLEN-1:0] thread_id_w;
    assign thread_id_w = '0;

    // ---------------------------------------------------------------
    // DOP Core
    // ---------------------------------------------------------------
    lm1_cpu #(
        .MEM_DEPTH_LOG2 (TILE_MEM_LOG2)
    ) u_core (
        .clk            (clk),
        .rst_n          (rst_n),
        .cfg_tile_id    (tile_id_w),
        .cfg_thread_id  (thread_id_w),
        .halted         (halted),
        .pc_out         (pc_out),
        .cycle_count    (cycle_count),
        // External memory (program load)
        .ext_mem_en     (ext_mem_en),
        .ext_mem_we     (ext_mem_we),
        .ext_mem_addr   (ext_mem_addr),
        .ext_mem_wdata  (ext_mem_wdata),
        .ext_mem_rdata  (ext_mem_rdata),
        // Debug
        .dbg_reg_addr   (dbg_reg_addr),
        .dbg_reg_data   (dbg_reg_data),
        // GC engine command (pass through to cluster)
        .gc_cmd_valid   (gc_cmd_valid),
        .gc_cmd_op      (gc_cmd_op),
        .gc_cmd_arg0    (gc_cmd_arg0),
        .gc_cmd_arg1    (gc_cmd_arg1),
        .gc_cmd_ready   (gc_cmd_ready),
        .gc_engine_busy (gc_engine_busy),
        // External message queue port (NoC ↔ queue)
        .ext_mq_wr_en   (noc_mq_wr_en),
        .ext_mq_wr_id   (noc_mq_wr_id),
        .ext_mq_wr_data (noc_mq_wr_data),
        .ext_mq_wr_ready(noc_mq_wr_ready),
        .ext_mq_rd_en   (noc_mq_rd_en),
        .ext_mq_rd_id   (noc_mq_rd_id),
        .ext_mq_rd_data (noc_mq_rd_data),
        .ext_mq_rd_valid(noc_mq_rd_valid),
        // Queue status
        .mq_empty       (mq_empty),
        .mq_full        (mq_full)
    );

    // ---------------------------------------------------------------
    // Cluster crossbar port — stub for now
    //
    // In a full implementation the tile SRAM would have a second
    // port and the crossbar interface would arbitrate tile-local
    // vs cluster-SRAM accesses based on address ranges.
    // For now, the crossbar port is inactive (no cluster accesses).
    // ---------------------------------------------------------------
    assign xbar_req_valid = 1'b0;
    assign xbar_req_we    = 1'b0;
    assign xbar_req_addr  = '0;
    assign xbar_req_wdata = '0;

endmodule
