// ============================================================================
// LM-1 GC Engine Top — Cluster-Level Movement Engine Controller
//
// Wraps the three movement engines (scanner, copier, fixup) and
// presents a single command interface to the core(s).
//
// Commands arrive via gc_cmd_valid/op/arg0/arg1. The controller
// dispatches to the appropriate engine and reports busy status.
//
// Memory access is arbitrated: each engine has its own request port,
// this module time-multiplexes them onto a single cluster-SRAM port.
//
// In a full cluster, one instance of this module serves all 8 tiles.
// ============================================================================
module lm1_gc_engine_top
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // --- Command interface (from core / tile) ---
    input  logic               cmd_valid,
    input  logic [3:0]         cmd_op,
    input  logic [XLEN-1:0]   cmd_arg0,       // region base  / src_base
    input  logic [XLEN-1:0]   cmd_arg1,       // region size  / dst_base
    input  logic [XLEN-1:0]   cmd_arg2,       // copy: region size (from rd)
    output logic               cmd_ready,
    output logic               busy,

    // --- Cluster SRAM port (shared read/write) ---
    output logic               mem_rd_en,
    output logic [XLEN-1:0]   mem_rd_addr,
    input  logic [XLEN-1:0]   mem_rd_data,
    input  logic               mem_rd_valid,

    output logic               mem_wr_en,
    output logic [XLEN-1:0]   mem_wr_addr,
    output logic [XLEN-1:0]   mem_wr_data,
    input  logic               mem_wr_ready,

    // --- Scanner result output (ref discovery) ---
    output logic               scan_res_valid,
    output logic [XLEN-1:0]   scan_res_obj,
    output logic [15:0]        scan_res_field,
    output logic [XLEN-1:0]   scan_res_ref,
    input  logic               scan_res_ready,

    // --- Copier destination pointer (for runtime visibility) ---
    output logic [XLEN-1:0]   copy_dst_ptr
);

    // ---------------------------------------------------------------
    // Internal engine wires
    // ---------------------------------------------------------------

    // Scanner
    logic sc_cmd_valid, sc_cmd_ready, sc_busy;
    logic sc_mem_rd_en, sc_mem_rd_valid;
    logic [XLEN-1:0] sc_mem_rd_addr, sc_mem_rd_data;

    // Copier
    logic cp_cmd_valid, cp_cmd_ready, cp_busy;
    logic cp_mem_rd_en, cp_mem_rd_valid;
    logic cp_mem_wr_en, cp_mem_wr_ready;
    logic [XLEN-1:0] cp_mem_rd_addr, cp_mem_rd_data;
    logic [XLEN-1:0] cp_mem_wr_addr, cp_mem_wr_data;

    // Fixup
    logic fx_cmd_valid, fx_cmd_ready, fx_busy;
    logic fx_mem_rd_en, fx_mem_rd_valid;
    logic fx_mem_wr_en, fx_mem_wr_ready;
    logic [XLEN-1:0] fx_mem_rd_addr, fx_mem_rd_data;
    logic [XLEN-1:0] fx_mem_wr_addr, fx_mem_wr_data;

    // ---------------------------------------------------------------
    // Busy and ready signals
    // ---------------------------------------------------------------
    assign busy = sc_busy | cp_busy | fx_busy;
    assign cmd_ready = !busy;  // Accept commands only when all engines idle

    // ---------------------------------------------------------------
    // Command dispatch
    // ---------------------------------------------------------------
    always_comb begin
        sc_cmd_valid = 1'b0;
        cp_cmd_valid = 1'b0;
        fx_cmd_valid = 1'b0;

        if (cmd_valid && cmd_ready) begin
            case (cmd_op)
                GC_CMD_SCAN:    sc_cmd_valid = 1'b1;
                GC_CMD_COPY:    cp_cmd_valid = 1'b1;
                GC_CMD_FIXUP:   fx_cmd_valid = 1'b1;
                GC_CMD_COMPACT: cp_cmd_valid = 1'b1;   // COMPACT reuses copier
                default: ;
            endcase
        end
    end

    // ---------------------------------------------------------------
    // Memory port arbiter — round-robin priority
    //
    // Priority: scanner (read-only) > copier > fixup
    // In practice only one engine runs at a time, but the arbiter
    // handles overlapping requests gracefully.
    // ---------------------------------------------------------------

    // Read arbiter
    always_comb begin
        mem_rd_en   = 1'b0;
        mem_rd_addr = '0;
        sc_mem_rd_valid = 1'b0;
        cp_mem_rd_valid = 1'b0;
        fx_mem_rd_valid = 1'b0;
        sc_mem_rd_data  = mem_rd_data;
        cp_mem_rd_data  = mem_rd_data;
        fx_mem_rd_data  = mem_rd_data;

        if (sc_mem_rd_en) begin
            mem_rd_en   = 1'b1;
            mem_rd_addr = sc_mem_rd_addr;
            sc_mem_rd_valid = mem_rd_valid;
        end else if (cp_mem_rd_en) begin
            mem_rd_en   = 1'b1;
            mem_rd_addr = cp_mem_rd_addr;
            cp_mem_rd_valid = mem_rd_valid;
        end else if (fx_mem_rd_en) begin
            mem_rd_en   = 1'b1;
            mem_rd_addr = fx_mem_rd_addr;
            fx_mem_rd_valid = mem_rd_valid;
        end
    end

    // Write arbiter
    always_comb begin
        mem_wr_en   = 1'b0;
        mem_wr_addr = '0;
        mem_wr_data = '0;
        cp_mem_wr_ready = 1'b0;
        fx_mem_wr_ready = 1'b0;

        if (cp_mem_wr_en) begin
            mem_wr_en   = 1'b1;
            mem_wr_addr = cp_mem_wr_addr;
            mem_wr_data = cp_mem_wr_data;
            cp_mem_wr_ready = mem_wr_ready;
        end else if (fx_mem_wr_en) begin
            mem_wr_en   = 1'b1;
            mem_wr_addr = fx_mem_wr_addr;
            mem_wr_data = fx_mem_wr_data;
            fx_mem_wr_ready = mem_wr_ready;
        end
    end

    // ---------------------------------------------------------------
    // Engine instances
    // ---------------------------------------------------------------

    lm1_gc_scanner u_scanner (
        .clk          (clk),
        .rst_n        (rst_n),
        .cmd_valid    (sc_cmd_valid),
        .cmd_ready    (sc_cmd_ready),
        .cmd_base     (cmd_arg0),
        .cmd_size     (cmd_arg1),
        .mem_rd_en    (sc_mem_rd_en),
        .mem_rd_addr  (sc_mem_rd_addr),
        .mem_rd_data  (sc_mem_rd_data),
        .mem_rd_valid (sc_mem_rd_valid),
        .res_valid    (scan_res_valid),
        .res_obj_addr (scan_res_obj),
        .res_field    (scan_res_field),
        .res_ref      (scan_res_ref),
        .res_ready    (scan_res_ready),
        .busy         (sc_busy)
    );

    lm1_gc_copier u_copier (
        .clk          (clk),
        .rst_n        (rst_n),
        .cmd_valid    (cp_cmd_valid),
        .cmd_ready    (cp_cmd_ready),
        .cmd_src_base (cmd_arg0),
        .cmd_dst_base (cmd_arg1),
        .cmd_size     (cmd_arg2),     // region size from rd register
        .mem_rd_en    (cp_mem_rd_en),
        .mem_rd_addr  (cp_mem_rd_addr),
        .mem_rd_data  (cp_mem_rd_data),
        .mem_rd_valid (cp_mem_rd_valid),
        .mem_wr_en    (cp_mem_wr_en),
        .mem_wr_addr  (cp_mem_wr_addr),
        .mem_wr_data  (cp_mem_wr_data),
        .mem_wr_ready (cp_mem_wr_ready),
        .busy         (cp_busy),
        .dst_ptr      (copy_dst_ptr)
    );

    lm1_gc_fixup u_fixup (
        .clk              (clk),
        .rst_n            (rst_n),
        .cmd_valid        (fx_cmd_valid),
        .cmd_ready        (fx_cmd_ready),
        .cmd_region_base  (cmd_arg0),
        .cmd_region_size  (cmd_arg1),
        .mem_rd_en        (fx_mem_rd_en),
        .mem_rd_addr      (fx_mem_rd_addr),
        .mem_rd_data      (fx_mem_rd_data),
        .mem_rd_valid     (fx_mem_rd_valid),
        .mem_wr_en        (fx_mem_wr_en),
        .mem_wr_addr      (fx_mem_wr_addr),
        .mem_wr_data      (fx_mem_wr_data),
        .mem_wr_ready     (fx_mem_wr_ready),
        .busy             (fx_busy)
    );

endmodule
