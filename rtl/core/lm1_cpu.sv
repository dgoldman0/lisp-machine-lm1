// ============================================================================
// LM-1 CPU Top-Level
//
// Integrates all sub-modules:
//   - Decoder (combinational)
//   - Register file (32×64, 2 read + 1 write)
//   - ALU (arithmetic, logic, compare, type-test)
//   - Branch evaluator (condition + target)
//   - Load/Store Unit (single memory port)
//   - Control FSM (multi-cycle orchestration)
//   - Header template table (256×64)
//   - Inline cache table (16-entry fully-associative)
//   - Main memory (SRAM)
//
// Memory:
//   Single 64-bit SRAM port, addressed in 8-byte words.
//   The control FSM ensures instruction fetch and data access
//   never overlap (multi-cycle, not pipelined).
//
// Parameters:
//   MEM_DEPTH_LOG2 — log2 of number of 64-bit words in SRAM.
//                    Default 16 → 64K words → 512 KB.
// ============================================================================
module lm1_cpu
    import lm1_pkg::*;
#(
    parameter int MEM_DEPTH_LOG2 = 16
)
(
    input  logic               clk,
    input  logic               rst_n,

    // Configuration
    input  logic [XLEN-1:0]   cfg_tile_id,
    input  logic [XLEN-1:0]   cfg_thread_id,

    // Status
    output logic               halted,
    output logic [XLEN-1:0]   pc_out,
    output logic [XLEN-1:0]   cycle_count,

    // External memory port (optional — directly expose SRAM for loading)
    input  logic               ext_mem_en,
    input  logic               ext_mem_we,
    input  logic [MEM_DEPTH_LOG2-1:0] ext_mem_addr,
    input  logic [XLEN-1:0]   ext_mem_wdata,
    output logic [XLEN-1:0]   ext_mem_rdata,

    // Debug: allow reading registers
    input  logic [REG_IDX_W-1:0] dbg_reg_addr,
    output logic [XLEN-1:0]     dbg_reg_data
);

    // ---------------------------------------------------------------
    // Internal wires
    // ---------------------------------------------------------------

    // Decoder ↔ Control
    decoded_t           dec_fields;
    logic [XLEN-1:0]   dec_imm_sext;

    // Register file ports
    logic               rf_we;
    logic [REG_IDX_W-1:0] rf_w_addr;
    logic [XLEN-1:0]   rf_w_data;
    logic [REG_IDX_W-1:0] rf_rd1_addr, rf_rd2_addr;
    logic [XLEN-1:0]   rf_rd1_data, rf_rd2_data;

    // ALU
    opcode_t            alu_op;
    logic [FUNC_W-1:0]  alu_func;
    logic [XLEN-1:0]   alu_a, alu_b, alu_result;
    logic               alu_start, alu_valid, alu_trap;
    logic [7:0]         alu_trap_code;

    // Branch
    logic [XLEN-1:0]   br_pc, br_val, br_target;
    logic [REG_IDX_W-1:0] br_cond;
    logic [IMM16_W-1:0] br_offset;
    logic               br_is_br, br_is_cond, br_taken;

    // LSU ↔ Control
    logic               lsu_req, lsu_ready, lsu_valid;
    logic [3:0]         lsu_op;
    logic [XLEN-1:0]   lsu_addr, lsu_wdata, lsu_rdata;
    logic [ILEN-1:0]   lsu_inst;

    // LSU ↔ Memory
    logic               mem_en, mem_we;
    logic [XLEN/8-1:0]  mem_be;
    logic [XLEN-1:0]   mem_addr_lsu;
    logic [XLEN-1:0]   mem_wdata_lsu;
    logic [XLEN-1:0]   mem_rdata;

    // Template table
    logic [7:0]         tmpl_rd_idx;
    logic [XLEN-1:0]   tmpl_rd_data;
    logic               tmpl_wr_en;
    logic [7:0]         tmpl_wr_idx;
    logic [XLEN-1:0]   tmpl_wr_data;

    // IC table
    logic [XLEN-1:0]   ic_lu_pc;
    logic [31:0]        ic_lu_shape;
    logic               ic_lu_valid;
    logic [XLEN-1:0]   ic_hit_target;
    logic               ic_hit;
    logic               ic_inst_valid;
    logic [XLEN-1:0]   ic_inst_pc;
    logic [31:0]        ic_inst_shape;
    logic [XLEN-1:0]   ic_inst_target;

    // ---------------------------------------------------------------
    // Decoder: not used as control classification signals
    // ---------------------------------------------------------------
    // Instruction latch: capture the 32-bit instruction word from the
    // LSU when the fetch response is valid.  The decoder is driven from
    // this register (stable in S_DECODE), not the transient lsu_inst
    // which is only combinationally valid during LSU_WAIT_RD.
    // ---------------------------------------------------------------
    logic [ILEN-1:0] inst_latched;
    always_ff @(posedge clk) begin
        if (!rst_n)
            inst_latched <= '0;
        else if (lsu_valid)
            inst_latched <= lsu_inst;
    end

    // ---------------------------------------------------------------
    // The decoder produces dec + imm_sext from the latched instruction.
    // The control FSM latches these at decode time.
    logic [REG_IDX_W-1:0] dec_rf_rd, dec_rf_rs1, dec_rf_rs2;
    logic                 dec_rf_we, dec_rf_rd_rs2;
    logic                 dec_is_alu, dec_is_load, dec_is_store;
    logic                 dec_is_branch, dec_is_jump, dec_is_system;
    logic                 dec_is_alloc, dec_is_multi, dec_is_nop;

    lm1_decoder u_decoder (
        .inst_word   (inst_latched),
        .dec         (dec_fields),
        .imm_sext    (dec_imm_sext),
        .rf_rd_idx   (dec_rf_rd),
        .rf_rs1_idx  (dec_rf_rs1),
        .rf_rs2_idx  (dec_rf_rs2),
        .rf_we       (dec_rf_we),
        .rf_rd_rs2   (dec_rf_rd_rs2),
        .is_alu_op   (dec_is_alu),
        .is_mem_load (dec_is_load),
        .is_mem_store(dec_is_store),
        .is_branch   (dec_is_branch),
        .is_jump     (dec_is_jump),
        .is_system   (dec_is_system),
        .is_alloc    (dec_is_alloc),
        .is_multi_cycle(dec_is_multi),
        .is_nop      (dec_is_nop)
    );

    // ---------------------------------------------------------------
    // Register File
    // ---------------------------------------------------------------
    lm1_regfile u_regfile (
        .clk     (clk),
        .rst_n   (rst_n),
        .ra_addr (rf_rd1_addr),
        .ra_data (rf_rd1_data),
        .rb_addr (rf_rd2_addr),
        .rb_data (rf_rd2_data),
        .w_en    (rf_we),
        .w_addr  (rf_w_addr),
        .w_data  (rf_w_data)
    );

    // Debug register read: directly from the register array via port B
    // when halted.  Uses a registered output to avoid comb loops.
    logic [XLEN-1:0] dbg_reg_latched;
    always_ff @(posedge clk) begin
        if (halted)
            dbg_reg_latched <= rf_rd2_data;
    end
    assign dbg_reg_data = dbg_reg_latched;

    // ---------------------------------------------------------------
    // ALU
    // ---------------------------------------------------------------
    lm1_alu u_alu (
        .clk          (clk),
        .rst_n        (rst_n),
        .alu_op       (alu_op),
        .alu_func     (alu_func),
        .operand_a    (alu_a),
        .operand_b    (alu_b),
        .start        (alu_start),
        .result       (alu_result),
        .result_valid (alu_valid),
        .trap_raise   (alu_trap),
        .trap_code    (alu_trap_code)
    );

    // ---------------------------------------------------------------
    // Branch Evaluator
    // ---------------------------------------------------------------
    lm1_branch u_branch (
        .pc          (br_pc),
        .reg_val     (br_val),
        .cond        (br_cond),
        .offset      (br_offset),
        .is_br       (br_is_br),
        .is_br_cond  (br_is_cond),
        .target      (br_target),
        .taken       (br_taken)
    );

    // ---------------------------------------------------------------
    // Load/Store Unit
    // ---------------------------------------------------------------
    lm1_lsu u_lsu (
        .clk        (clk),
        .rst_n      (rst_n),
        .req_valid  (lsu_req),
        .req_op     (lsu_op),
        .req_addr   (lsu_addr),
        .req_wdata  (lsu_wdata),
        .req_ready  (lsu_ready),
        .resp_valid (lsu_valid),
        .resp_rdata (lsu_rdata),
        .resp_inst  (lsu_inst),
        .mem_en     (mem_en),
        .mem_we     (mem_we),
        .mem_be     (mem_be),
        .mem_addr   (mem_addr_lsu),
        .mem_wdata  (mem_wdata_lsu),
        .mem_rdata  (mem_rdata)
    );

    // ---------------------------------------------------------------
    // Header Template Table
    // ---------------------------------------------------------------
    lm1_tmpl_table u_tmpl (
        .clk     (clk),
        .rst_n   (rst_n),
        .rd_idx  (tmpl_rd_idx),
        .rd_data (tmpl_rd_data),
        .wr_en   (tmpl_wr_en),
        .wr_idx  (tmpl_wr_idx),
        .wr_data (tmpl_wr_data)
    );

    // ---------------------------------------------------------------
    // Inline Cache Table
    // ---------------------------------------------------------------
    lm1_ic_table u_ic (
        .clk         (clk),
        .rst_n       (rst_n),
        .lu_pc       (ic_lu_pc),
        .lu_shape    (ic_lu_shape),
        .lu_valid    (ic_lu_valid),
        .hit_target  (ic_hit_target),
        .hit         (ic_hit),
        .inst_valid  (ic_inst_valid),
        .inst_pc     (ic_inst_pc),
        .inst_shape  (ic_inst_shape),
        .inst_target (ic_inst_target)
    );

    // ---------------------------------------------------------------
    // Control FSM
    // ---------------------------------------------------------------
    lm1_control u_ctrl (
        .clk          (clk),
        .rst_n        (rst_n),
        .dec_in       (dec_fields),
        .imm_sext_in  (dec_imm_sext),
        .rf_rd1_data  (rf_rd1_data),
        .rf_rd2_data  (rf_rd2_data),
        .rf_we        (rf_we),
        .rf_w_addr    (rf_w_addr),
        .rf_w_data    (rf_w_data),
        .rf_rd1_addr  (rf_rd1_addr),
        .rf_rd2_addr  (rf_rd2_addr),
        .alu_op       (alu_op),
        .alu_func     (alu_func),
        .alu_a        (alu_a),
        .alu_b        (alu_b),
        .alu_start    (alu_start),
        .alu_result   (alu_result),
        .alu_valid    (alu_valid),
        .alu_trap     (alu_trap),
        .alu_trap_code(alu_trap_code),
        .br_pc        (br_pc),
        .br_val       (br_val),
        .br_cond      (br_cond),
        .br_offset    (br_offset),
        .br_is_br     (br_is_br),
        .br_is_cond   (br_is_cond),
        .br_target    (br_target),
        .br_taken     (br_taken),
        .lsu_req      (lsu_req),
        .lsu_op       (lsu_op),
        .lsu_addr     (lsu_addr),
        .lsu_wdata    (lsu_wdata),
        .lsu_ready    (lsu_ready),
        .lsu_valid    (lsu_valid),
        .lsu_rdata    (lsu_rdata),
        .lsu_inst     (lsu_inst),
        .tmpl_rd_idx  (tmpl_rd_idx),
        .tmpl_rd_data (tmpl_rd_data),
        .tmpl_wr_en   (tmpl_wr_en),
        .tmpl_wr_idx  (tmpl_wr_idx),
        .tmpl_wr_data (tmpl_wr_data),
        .ic_lu_pc     (ic_lu_pc),
        .ic_lu_shape  (ic_lu_shape),
        .ic_lu_valid  (ic_lu_valid),
        .ic_hit_target(ic_hit_target),
        .ic_hit       (ic_hit),
        .ic_inst_valid(ic_inst_valid),
        .ic_inst_pc   (ic_inst_pc),
        .ic_inst_shape(ic_inst_shape),
        .ic_inst_target(ic_inst_target),
        .cfg_tile_id  (cfg_tile_id),
        .cfg_thread_id(cfg_thread_id),
        .halted       (halted),
        .pc_out       (pc_out),
        .cycle_count  (cycle_count)
    );

    // ---------------------------------------------------------------
    // Main Memory (SRAM)
    //
    // Multiplexed between LSU (normal operation) and external port
    // (loading programs / debug access).
    // When the CPU is not halted or external access is not enabled,
    // the LSU has full control.
    // ---------------------------------------------------------------
    localparam int MEM_DEPTH = 1 << MEM_DEPTH_LOG2;

    logic               sram_en;
    logic               sram_we;
    logic [XLEN/8-1:0]  sram_be;
    logic [MEM_DEPTH_LOG2-1:0] sram_addr;
    logic [XLEN-1:0]   sram_wdata;
    logic [XLEN-1:0]   sram_rdata;

    // Memory port mux: external has priority when ext_mem_en is high
    always_comb begin
        if (ext_mem_en) begin
            sram_en    = 1'b1;
            sram_we    = ext_mem_we;
            sram_be    = {(XLEN/8){1'b1}};  // full-word write for external
            sram_addr  = ext_mem_addr;
            sram_wdata = ext_mem_wdata;
        end else begin
            sram_en    = mem_en;
            sram_we    = mem_we;
            sram_be    = mem_be;
            sram_addr  = mem_addr_lsu[MEM_DEPTH_LOG2-1:0];
            sram_wdata = mem_wdata_lsu;
        end
    end

    assign mem_rdata     = sram_rdata;
    assign ext_mem_rdata = sram_rdata;

    lm1_sram_sp #(
        .DATA_WIDTH (XLEN),
        .DEPTH      (MEM_DEPTH)
    ) u_sram (
        .clk   (clk),
        .en    (sram_en),
        .we    (sram_we),
        .be    (sram_be),
        .addr  (sram_addr),
        .wdata (sram_wdata),
        .rdata (sram_rdata)
    );

endmodule
