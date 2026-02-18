// ============================================================================
// LM-1 FPGA Top-Level Wrapper
//
// Wraps a single lm1_tile for synthesis / board-level testing.
// All technology-specific adaptations live here; the pure RTL under
// rtl/ is untouched.
//
// Features:
//   - PLL/MMCM wrapper placeholder to generate the core clock from
//     the board oscillator (replace with target-specific primitive
//     when the clock frequency is known).
//   - Directly exposes the external memory port so an external
//     controller (UART loader, JTAG, etc.) can write the program
//     image before de-asserting reset.
//   - Simple synchronous reset from an active-low push-button with
//     a shift-register debouncer.
//   - LED outputs for basic status.
//
// Target:  Generic FPGA.  See rtl/target/<family>/ for synthesis
//          scripts and pin constraints.
//
// Parameters:
//   TILE_MEM_LOG2 — tile SRAM depth.  Max practical value depends on
//                   available BRAM; 14 (16K × 8 B = 128 KiB) is a
//                   safe starting point for Artix-7 100T.
// ============================================================================
module lm1_fpga_top
    import lm1_pkg::*;
#(
    parameter int TILE_MEM_LOG2 = 14   // 16K words = 128 KiB
)
(
    input  logic        sys_clk,       // board oscillator (e.g. 100 MHz)
    input  logic        sys_rst_n,     // active-low reset button

    // --- Status LEDs ---
    output logic [3:0]  led,

    // --- External memory port (directly from FPGA fabric / ILA) ---
    input  logic                         ext_mem_en,
    input  logic                         ext_mem_we,
    input  logic [TILE_MEM_LOG2-1:0]     ext_mem_addr,
    input  logic [63:0]                  ext_mem_wdata,
    output logic [63:0]                  ext_mem_rdata,

    // --- Debug register read ---
    input  logic [4:0]                   dbg_reg_addr,
    output logic [63:0]                  dbg_reg_data
);

    // ---------------------------------------------------------------
    // Clock & Reset
    // ---------------------------------------------------------------
    // For the initial synthesis test the core runs on the raw board
    // clock.  Replace this with a Clocking Wizard / MMCM instantiation
    // when the target frequency is known.
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
    // Tile instantiation
    // ---------------------------------------------------------------
    logic              halted;
    logic [63:0]       pc_out;
    logic [63:0]       cycle_count;

    lm1_tile #(
        .TILE_MEM_LOG2 (TILE_MEM_LOG2),
        .TILE_ID       (0)
    ) u_tile (
        .clk            (core_clk),
        .rst_n          (rst_n),

        // Status
        .halted         (halted),
        .pc_out         (pc_out),
        .cycle_count    (cycle_count),

        // External memory port
        .ext_mem_en     (ext_mem_en),
        .ext_mem_we     (ext_mem_we),
        .ext_mem_addr   (ext_mem_addr),
        .ext_mem_wdata  (ext_mem_wdata),
        .ext_mem_rdata  (ext_mem_rdata),

        // Debug
        .dbg_reg_addr   (dbg_reg_addr),
        .dbg_reg_data   (dbg_reg_data),

        // Cluster crossbar — not used in single-tile FPGA build
        .xbar_req_valid  (),
        .xbar_req_we     (),
        .xbar_req_addr   (),
        .xbar_req_wdata  (),
        .xbar_req_ready  (1'b1),     // always ready (never stalls)
        .xbar_resp_data  (64'd0),
        .xbar_resp_valid (1'b0),

        // GC engine — not used in single-tile FPGA build
        .gc_cmd_valid   (),
        .gc_cmd_op      (),
        .gc_cmd_arg0    (),
        .gc_cmd_arg1    (),
        .gc_cmd_arg2    (),
        .gc_cmd_ready   (1'b1),
        .gc_engine_busy (1'b0),

        // Scanner result FIFO — empty
        .scan_fifo_count      (8'd0),
        .scan_fifo_head_obj   (64'd0),
        .scan_fifo_head_field (16'd0),
        .scan_fifo_head_ref   (64'd0),
        .scan_fifo_pop        (),

        // NoC message queue — not used
        .noc_mq_wr_en    (1'b0),
        .noc_mq_wr_id    (2'b00),
        .noc_mq_wr_data  (64'd0),
        .noc_mq_wr_ready (),
        .noc_mq_rd_en    (1'b0),
        .noc_mq_rd_id    (2'b00),
        .noc_mq_rd_data  (),
        .noc_mq_rd_valid (),

        // Queue status
        .mq_empty        (),
        .mq_full         ()
    );

    // ---------------------------------------------------------------
    // LED mapping
    // ---------------------------------------------------------------
    assign led[0] = halted;            // solid when CPU has halted
    assign led[1] = rst_n;             // on after reset released
    assign led[2] = pc_out[0];         // flickers while executing
    assign led[3] = cycle_count[20];   // heartbeat (~1 Hz at 100 MHz)

endmodule
