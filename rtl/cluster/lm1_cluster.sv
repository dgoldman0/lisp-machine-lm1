// ============================================================================
// LM-1 Cluster Top Module
//
// Instantiates:
//   - 8 tiles (each with DOP core, tile SRAM, queues, perf counters)
//   - 8-port non-blocking crossbar
//   - Cluster shared SRAM (2 MiB = 256K × 64-bit words)
//   - GC movement engine top (scanner + copier + fixup)
//   - DMA engine stub
//   - NoC uplink stub
//
// Parameters:
//   CLUSTER_ID       — unique cluster ID within the SoC
//   TILES_PER_CLUSTER — tiles per cluster (default 8)
//   TILE_MEM_LOG2    — per-tile SRAM size (log2 words, default 15)
//   CLUSTER_MEM_LOG2 — cluster shared SRAM (log2 words, default 18 = 256K words = 2 MiB)
// ============================================================================
module lm1_cluster
    import lm1_pkg::*;
#(
    parameter int CLUSTER_ID        = 0,
    parameter int TILES_PER_CLUSTER = 8,
    parameter int TILE_MEM_LOG2     = 15,   // 32K words = 256 KiB per tile
    parameter int CLUSTER_MEM_LOG2  = 18    // 256K words = 2 MiB shared
)
(
    input  logic               clk,
    input  logic               rst_n,

    // --- Per-tile status ---
    output logic [TILES_PER_CLUSTER-1:0] tile_halted,

    // --- Per-tile external memory port (initial program load) ---
    input  logic [TILES_PER_CLUSTER-1:0] ext_mem_en,
    input  logic [TILES_PER_CLUSTER-1:0] ext_mem_we,
    input  logic [TILE_MEM_LOG2-1:0]     ext_mem_addr  [0:TILES_PER_CLUSTER-1],
    input  logic [XLEN-1:0]             ext_mem_wdata [0:TILES_PER_CLUSTER-1],
    output logic [XLEN-1:0]             ext_mem_rdata [0:TILES_PER_CLUSTER-1],

    // --- Per-tile debug ---
    input  logic [REG_IDX_W-1:0]        dbg_reg_addr  [0:TILES_PER_CLUSTER-1],
    output logic [XLEN-1:0]             dbg_reg_data  [0:TILES_PER_CLUSTER-1],

    // --- NoC uplink (stub — one port for the whole cluster) ---
    output logic               noc_tx_valid,
    output logic [XLEN-1:0]   noc_tx_data,
    input  logic               noc_tx_ready,
    input  logic               noc_rx_valid,
    input  logic [XLEN-1:0]   noc_rx_data,
    output logic               noc_rx_ready,

    // --- DMA port (stub — cluster ↔ HBM) ---
    output logic               dma_req_valid,
    output logic               dma_req_we,
    output logic [XLEN-1:0]   dma_req_addr,
    output logic [XLEN-1:0]   dma_req_wdata,
    input  logic               dma_req_ready,
    input  logic [XLEN-1:0]   dma_resp_data,
    input  logic               dma_resp_valid
);

    // ---------------------------------------------------------------
    // Per-tile wires to crossbar
    // ---------------------------------------------------------------
    logic [TILES_PER_CLUSTER-1:0]     xbar_req_valid;
    logic [TILES_PER_CLUSTER-1:0]     xbar_req_we;
    logic [XLEN-1:0]                 xbar_req_addr  [0:TILES_PER_CLUSTER-1];
    logic [XLEN-1:0]                 xbar_req_wdata [0:TILES_PER_CLUSTER-1];
    logic [TILES_PER_CLUSTER-1:0]     xbar_req_ready;
    logic [XLEN-1:0]                 xbar_resp_data [0:TILES_PER_CLUSTER-1];
    logic [TILES_PER_CLUSTER-1:0]     xbar_resp_valid;

    // Per-tile GC engine command — arbitrated into single gc_cmd
    logic [TILES_PER_CLUSTER-1:0]     tile_gc_cmd_valid;
    logic [3:0]                       tile_gc_cmd_op    [0:TILES_PER_CLUSTER-1];
    logic [XLEN-1:0]                 tile_gc_cmd_arg0  [0:TILES_PER_CLUSTER-1];
    logic [XLEN-1:0]                 tile_gc_cmd_arg1  [0:TILES_PER_CLUSTER-1];
    logic [TILES_PER_CLUSTER-1:0]     tile_gc_cmd_ready;
    logic                             gc_busy;

    // ---------------------------------------------------------------
    // Tile instances
    // ---------------------------------------------------------------
    genvar t;
    generate
        for (t = 0; t < TILES_PER_CLUSTER; t++) begin : gen_tiles
            lm1_tile #(
                .TILE_MEM_LOG2 (TILE_MEM_LOG2),
                .TILE_ID       (CLUSTER_ID * TILES_PER_CLUSTER + t)
            ) u_tile (
                .clk            (clk),
                .rst_n          (rst_n),
                .halted         (tile_halted[t]),
                .pc_out         (),                // unused at cluster level
                .cycle_count    (),
                // External memory
                .ext_mem_en     (ext_mem_en[t]),
                .ext_mem_we     (ext_mem_we[t]),
                .ext_mem_addr   (ext_mem_addr[t]),
                .ext_mem_wdata  (ext_mem_wdata[t]),
                .ext_mem_rdata  (ext_mem_rdata[t]),
                // Debug
                .dbg_reg_addr   (dbg_reg_addr[t]),
                .dbg_reg_data   (dbg_reg_data[t]),
                // Crossbar
                .xbar_req_valid (xbar_req_valid[t]),
                .xbar_req_we    (xbar_req_we[t]),
                .xbar_req_addr  (xbar_req_addr[t]),
                .xbar_req_wdata (xbar_req_wdata[t]),
                .xbar_req_ready (xbar_req_ready[t]),
                .xbar_resp_data (xbar_resp_data[t]),
                .xbar_resp_valid(xbar_resp_valid[t]),
                // GC engine command
                .gc_cmd_valid   (tile_gc_cmd_valid[t]),
                .gc_cmd_op      (tile_gc_cmd_op[t]),
                .gc_cmd_arg0    (tile_gc_cmd_arg0[t]),
                .gc_cmd_arg1    (tile_gc_cmd_arg1[t]),
                .gc_cmd_ready   (tile_gc_cmd_ready[t]),
                .gc_engine_busy (gc_busy),
                // NoC message port — tied off for now (NoC stub)
                .noc_mq_wr_en   (1'b0),
                .noc_mq_wr_id   (2'b0),
                .noc_mq_wr_data ({XLEN{1'b0}}),
                .noc_mq_wr_ready(),
                .noc_mq_rd_en   (1'b0),
                .noc_mq_rd_id   (2'b0),
                .noc_mq_rd_data (),
                .noc_mq_rd_valid(),
                // Queue status
                .mq_empty       (),
                .mq_full        ()
            );
        end
    endgenerate

    // ---------------------------------------------------------------
    // Cluster Crossbar
    // ---------------------------------------------------------------
    logic               csram_en;
    logic               csram_we;
    logic [XLEN-1:0]   csram_addr;
    logic [XLEN-1:0]   csram_wdata;
    logic [XLEN-1:0]   csram_rdata;

    lm1_crossbar #(
        .NUM_PORTS (TILES_PER_CLUSTER)
    ) u_crossbar (
        .clk        (clk),
        .rst_n      (rst_n),
        .req_valid  (xbar_req_valid),
        .req_we     (xbar_req_we),
        .req_addr   (xbar_req_addr),
        .req_wdata  (xbar_req_wdata),
        .req_ready  (xbar_req_ready),
        .resp_data  (xbar_resp_data),
        .resp_valid (xbar_resp_valid),
        .sram_en    (csram_en),
        .sram_we    (csram_we),
        .sram_addr  (csram_addr),
        .sram_wdata (csram_wdata),
        .sram_rdata (csram_rdata)
    );

    // ---------------------------------------------------------------
    // Cluster Shared SRAM (2 MiB)
    // ---------------------------------------------------------------
    localparam int CLUSTER_MEM_DEPTH = 1 << CLUSTER_MEM_LOG2;

    lm1_sram_sp #(
        .DATA_WIDTH (XLEN),
        .DEPTH      (CLUSTER_MEM_DEPTH)
    ) u_cluster_sram (
        .clk   (clk),
        .en    (csram_en),
        .we    (csram_we),
        .be    ({(XLEN/8){1'b1}}),
        .addr  (csram_addr[CLUSTER_MEM_LOG2-1:0]),
        .wdata (csram_wdata),
        .rdata (csram_rdata)
    );

    // ---------------------------------------------------------------
    // GC Command Arbiter — first valid tile wins
    // ---------------------------------------------------------------
    logic               gc_cmd_valid_out;
    logic [3:0]         gc_cmd_op_out;
    logic [XLEN-1:0]   gc_cmd_arg0_out;
    logic [XLEN-1:0]   gc_cmd_arg1_out;
    logic               gc_cmd_ready_in;

    always_comb begin
        gc_cmd_valid_out = 1'b0;
        gc_cmd_op_out    = '0;
        gc_cmd_arg0_out  = '0;
        gc_cmd_arg1_out  = '0;
        tile_gc_cmd_ready = '0;

        for (int i = 0; i < TILES_PER_CLUSTER; i++) begin
            if (tile_gc_cmd_valid[i] && !gc_cmd_valid_out) begin
                gc_cmd_valid_out     = 1'b1;
                gc_cmd_op_out        = tile_gc_cmd_op[i];
                gc_cmd_arg0_out      = tile_gc_cmd_arg0[i];
                gc_cmd_arg1_out      = tile_gc_cmd_arg1[i];
                tile_gc_cmd_ready[i] = gc_cmd_ready_in;
            end
        end
    end

    // ---------------------------------------------------------------
    // GC Movement Engine Top
    //
    // Engines access cluster SRAM via a separate port.
    // For simplicity, the movement engines share the crossbar SRAM path
    // during GC phases (when mutator tiles are paused).
    // In a production design, the SRAM would be dual-ported or banked.
    // ---------------------------------------------------------------
    logic               gc_mem_rd_en;
    logic [XLEN-1:0]   gc_mem_rd_addr;
    logic               gc_mem_wr_en;
    logic [XLEN-1:0]   gc_mem_wr_addr;
    logic [XLEN-1:0]   gc_mem_wr_data;

    lm1_gc_engine_top u_gc_engines (
        .clk            (clk),
        .rst_n          (rst_n),
        // Command
        .cmd_valid      (gc_cmd_valid_out),
        .cmd_op         (gc_cmd_op_out),
        .cmd_arg0       (gc_cmd_arg0_out),
        .cmd_arg1       (gc_cmd_arg1_out),
        .cmd_ready      (gc_cmd_ready_in),
        .busy           (gc_busy),
        // Memory — engines read/write cluster SRAM via crossbar
        // (simplified: read uses sram_rdata directly, write is
        //  multiplexed with crossbar writes)
        .mem_rd_en      (gc_mem_rd_en),
        .mem_rd_addr    (gc_mem_rd_addr),
        .mem_rd_data    (csram_rdata),
        .mem_rd_valid   (gc_mem_rd_en),      // single-cycle SRAM: valid = en
        .mem_wr_en      (gc_mem_wr_en),
        .mem_wr_addr    (gc_mem_wr_addr),
        .mem_wr_data    (gc_mem_wr_data),
        .mem_wr_ready   (1'b1),
        // Scanner results — connected to nothing for now (runtime reads queue)
        .scan_res_valid (),
        .scan_res_obj   (),
        .scan_res_field (),
        .scan_res_ref   (),
        .scan_res_ready (1'b1),
        .copy_dst_ptr   ()
    );

    // ---------------------------------------------------------------
    // NoC uplink — stub
    // ---------------------------------------------------------------
    assign noc_tx_valid = 1'b0;
    assign noc_tx_data  = '0;
    assign noc_rx_ready = 1'b1;   // always accept (discard)

    // ---------------------------------------------------------------
    // DMA port — stub
    // ---------------------------------------------------------------
    assign dma_req_valid = 1'b0;
    assign dma_req_we    = 1'b0;
    assign dma_req_addr  = '0;
    assign dma_req_wdata = '0;

endmodule
