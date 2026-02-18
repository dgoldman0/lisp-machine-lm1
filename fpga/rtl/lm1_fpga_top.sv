// ============================================================================
// LM-1 FPGA Top-Level Wrapper
//
// Wraps a full lm1_cluster (8 tiles + crossbar + shared SRAM + GC engines)
// for synthesis and board-level testing.
//
// Features:
//   - MMCM placeholder (raw board clock for initial synthesis)
//   - Shift-register reset debouncer
//   - External memory port exposed for tile 0 (program load via
//     UART/JTAG loader); remaining tiles share the same image
//     via broadcast or SW copy.
//   - UART TX/RX stubs for future program loader
//   - 8 status LEDs (1 per tile halted status)
//
// Target:  Xilinx 7-series (Genesys 2, xc7k325tffg900-2).
//          See rtl/target/xilinx7/ for synthesis scripts.
//
// Parameters:
//   TILE_MEM_LOG2    — per-tile SRAM depth (log2 words).
//                      13 = 8K words = 64 KiB at 61% BRAM on K7-325T.
//   CLUSTER_MEM_LOG2 — cluster shared SRAM depth (log2 words).
//                      16 = 64K words = 512 KiB.
// ============================================================================
module lm1_fpga_top
    import lm1_pkg::*;
#(
    parameter int TILE_MEM_LOG2    = 13,  // 8K words  = 64 KiB per tile
    parameter int CLUSTER_MEM_LOG2 = 16,  // 64K words = 512 KiB shared
    parameter int TILES            = 8
)
(
    input  logic        sys_clk,       // board oscillator (200 MHz on Genesys 2)
    input  logic        sys_rst_n,     // active-low reset button

    // --- Status LEDs ---
    output logic [7:0]  led,

    // --- UART (stub — future program loader) ---
    output logic        uart_txd,
    input  logic        uart_rxd,

    // --- External memory port for tile 0 (program load) ---
    input  logic                         ext_mem_en,
    input  logic                         ext_mem_we,
    input  logic [TILE_MEM_LOG2-1:0]     ext_mem_addr,
    input  logic [63:0]                  ext_mem_wdata,
    output logic [63:0]                  ext_mem_rdata,

    // --- Debug register read (tile 0 only) ---
    input  logic [4:0]                   dbg_reg_addr,
    output logic [63:0]                  dbg_reg_data
);

    // ---------------------------------------------------------------
    // Clock & Reset
    // ---------------------------------------------------------------
    // For the initial synthesis test the core runs on the raw board
    // clock.  Replace with MMCM instantiation when target Fmax is known.
    logic core_clk;
    assign core_clk = sys_clk;

    // Synchronous reset with debounce shift register (4 cycles)
    logic [3:0] rst_sr;
    logic        rst_n;

    always_ff @(posedge core_clk or negedge sys_rst_n) begin
        if (!sys_rst_n)
            rst_sr <= '0;
        else
            rst_sr <= {rst_sr[2:0], 1'b1};
    end
    assign rst_n = rst_sr[3];

    // ---------------------------------------------------------------
    // Per-tile external memory port wiring
    //
    // Only tile 0 is connected to the external load port.
    // Tiles 1–7 have ext_mem disabled (en=0) — they load via shared
    // SRAM or SW copy from tile 0.
    // ---------------------------------------------------------------
    logic [TILES-1:0]            cl_ext_mem_en;
    logic [TILES-1:0]            cl_ext_mem_we;
    logic [TILE_MEM_LOG2-1:0]    cl_ext_mem_addr  [0:TILES-1];
    logic [XLEN-1:0]             cl_ext_mem_wdata [0:TILES-1];
    logic [XLEN-1:0]             cl_ext_mem_rdata [0:TILES-1];

    // Debug: only tile 0
    logic [REG_IDX_W-1:0]        cl_dbg_reg_addr  [0:TILES-1];
    logic [XLEN-1:0]             cl_dbg_reg_data  [0:TILES-1];

    // Tile 0 — connected to external port
    assign cl_ext_mem_en[0]      = ext_mem_en;
    assign cl_ext_mem_we[0]      = ext_mem_we;
    assign cl_ext_mem_addr[0]    = ext_mem_addr;
    assign cl_ext_mem_wdata[0]   = ext_mem_wdata;
    assign ext_mem_rdata         = cl_ext_mem_rdata[0];
    assign cl_dbg_reg_addr[0]    = dbg_reg_addr;
    assign dbg_reg_data          = cl_dbg_reg_data[0];

    // Tiles 1–7 — disabled external ports, zero debug
    genvar i;
    generate
        for (i = 1; i < TILES; i++) begin : gen_tie
            assign cl_ext_mem_en[i]    = 1'b0;
            assign cl_ext_mem_we[i]    = 1'b0;
            assign cl_ext_mem_addr[i]  = '0;
            assign cl_ext_mem_wdata[i] = '0;
            assign cl_dbg_reg_addr[i]  = '0;
        end
    endgenerate

    // ---------------------------------------------------------------
    // Cluster instantiation — 8 tiles, crossbar, shared SRAM, GC
    // ---------------------------------------------------------------
    logic [TILES-1:0] tile_halted;

    lm1_cluster #(
        .CLUSTER_ID        (0),
        .TILES_PER_CLUSTER (TILES),
        .TILE_MEM_LOG2     (TILE_MEM_LOG2),
        .CLUSTER_MEM_LOG2  (CLUSTER_MEM_LOG2)
    ) u_cluster (
        .clk            (core_clk),
        .rst_n          (rst_n),

        // Tile status
        .tile_halted    (tile_halted),

        // External memory (per-tile)
        .ext_mem_en     (cl_ext_mem_en),
        .ext_mem_we     (cl_ext_mem_we),
        .ext_mem_addr   (cl_ext_mem_addr),
        .ext_mem_wdata  (cl_ext_mem_wdata),
        .ext_mem_rdata  (cl_ext_mem_rdata),

        // Debug (per-tile)
        .dbg_reg_addr   (cl_dbg_reg_addr),
        .dbg_reg_data   (cl_dbg_reg_data),

        // NoC — stub (not used in FPGA prototype)
        .noc_tx_valid   (),
        .noc_tx_data    (),
        .noc_tx_ready   (1'b1),
        .noc_rx_valid   (1'b0),
        .noc_rx_data    ({XLEN{1'b0}}),
        .noc_rx_ready   (),

        // DMA — stub
        .dma_req_valid  (),
        .dma_req_we     (),
        .dma_req_addr   (),
        .dma_req_wdata  (),
        .dma_req_ready  (1'b1),
        .dma_resp_data  ({XLEN{1'b0}}),
        .dma_resp_valid (1'b0)
    );

    // ---------------------------------------------------------------
    // LED mapping — one LED per tile halted status
    // ---------------------------------------------------------------
    assign led = tile_halted;

    // ---------------------------------------------------------------
    // UART stub — tie off until loader is implemented
    // ---------------------------------------------------------------
    assign uart_txd = 1'b1;  // idle high (no transmission)

    // Suppress unused-input warning for uart_rxd
    /* verilator lint_off UNUSEDSIGNAL */
    logic uart_rxd_unused;
    assign uart_rxd_unused = uart_rxd;
    /* verilator lint_on UNUSEDSIGNAL */

endmodule
