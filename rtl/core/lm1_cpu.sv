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
//   - Hardware message queues (4 FIFOs)
//   - Performance counters (8 × 64-bit)
//   - Main memory (SRAM)
//
// GC engine command interface is exposed for tile/cluster wiring.
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
    output logic [XLEN-1:0]     dbg_reg_data,

    // --- GC engine command interface (exposed for tile/cluster) ---
    output logic               gc_cmd_valid,
    output logic [3:0]         gc_cmd_op,
    output logic [XLEN-1:0]   gc_cmd_arg0,
    output logic [XLEN-1:0]   gc_cmd_arg1,
    output logic [XLEN-1:0]   gc_cmd_arg2,
    input  logic               gc_cmd_ready,
    input  logic               gc_engine_busy,

    // --- External message queue port (NoC ↔ queue) ---
    input  logic               ext_mq_wr_en,
    input  logic [1:0]         ext_mq_wr_id,
    input  logic [XLEN-1:0]   ext_mq_wr_data,
    output logic               ext_mq_wr_ready,
    input  logic               ext_mq_rd_en,
    input  logic [1:0]         ext_mq_rd_id,
    output logic [XLEN-1:0]   ext_mq_rd_data,
    output logic               ext_mq_rd_valid,

    // --- Queue status ---
    output logic [3:0]         mq_empty,
    output logic [3:0]         mq_full,

    // --- Cluster crossbar port (for addresses beyond local SRAM) ---
    output logic               xbar_req_valid,
    output logic               xbar_req_we,
    output logic [XLEN-1:0]   xbar_req_addr,
    output logic [XLEN-1:0]   xbar_req_wdata,
    input  logic               xbar_req_ready,
    input  logic [XLEN-1:0]   xbar_resp_data,
    input  logic               xbar_resp_valid
);

    // ---------------------------------------------------------------
    // Internal wires
    // ---------------------------------------------------------------

    // Decoder ↔ Control
    decoded_t           dec_fields;
    logic [XLEN-1:0]   dec_imm_sext;

    // Register file ports
    logic               rf_we;
    logic [FULL_REG_W-1:0] rf_w_addr;
    logic [XLEN-1:0]   rf_w_data;
    logic [FULL_REG_W-1:0] rf_rd1_addr, rf_rd2_addr;
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

    // Message queue — core side
    logic               mq_wr_en;
    logic [1:0]         mq_wr_id;
    logic [XLEN-1:0]   mq_wr_data;
    logic               mq_wr_ready;
    logic               mq_rd_en;
    logic [1:0]         mq_rd_id;
    logic [XLEN-1:0]   mq_rd_data;
    logic               mq_rd_valid;

    // Performance counter
    logic [4:0]         ctr_id;
    logic [XLEN-1:0]   ctr_value;
    logic               ctr_alloc_inc;
    logic [15:0]        ctr_alloc_bytes_inc;
    logic               ctr_barrier_fire_inc;
    logic               ctr_barrier_filt_inc;
    logic               ctr_ic_hit_inc;
    logic               ctr_ic_miss_inc;
    logic               ctr_nursery_ovf_inc;

    // I-Cache wires
    logic               icache_fetch_req, icache_fetch_valid;
    logic [XLEN-1:0]   icache_fetch_addr;
    logic [ILEN-1:0]   icache_fetch_inst;
    logic               icache_fill_req, icache_fill_valid, icache_fill_done;
    logic [XLEN-1:0]   icache_fill_addr, icache_fill_data;

    // Instruction latch control (driven by control FSM)
    logic               inst_latch_en;
    logic [ILEN-1:0]   inst_latch_data;

    // ---------------------------------------------------------------
    // Decoder: not used as control classification signals
    // ---------------------------------------------------------------
    // Instruction latch: capture the 32-bit instruction word.
    // Updated by the control FSM via inst_latch_en/data (from I-Cache
    // hit or LSU fallback).
    // ---------------------------------------------------------------
    logic [ILEN-1:0] inst_latched;
    always_ff @(posedge clk) begin
        if (!rst_n)
            inst_latched <= '0;
        else if (inst_latch_en)
            inst_latched <= inst_latch_data;
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
    // Instruction Cache — 8 KiB direct-mapped
    // ---------------------------------------------------------------
    lm1_icache u_icache (
        .clk         (clk),
        .rst_n       (rst_n),
        .fetch_req   (icache_fetch_req),
        .fetch_addr  (icache_fetch_addr),
        .fetch_valid (icache_fetch_valid),
        .fetch_inst  (icache_fetch_inst),
        .fill_req    (icache_fill_req),
        .fill_addr   (icache_fill_addr),
        .fill_valid  (icache_fill_valid),
        .fill_data   (icache_fill_data),
        .fill_done   (icache_fill_done)
    );

    // I-Cache fill sequencer
    // Reads 8 sequential 64-bit words from SRAM to fill a cache line.
    // Active only when the I-Cache asserts fill_req (cache miss).
    typedef enum logic [1:0] {
        ICFILL_IDLE,
        ICFILL_READ,
        ICFILL_RESP
    } icfill_state_t;

    icfill_state_t icfill_state;
    logic [2:0]    icfill_cnt;
    logic [MEM_DEPTH_LOG2-1:0] icfill_base;

    logic               icfill_sram_en;
    logic [MEM_DEPTH_LOG2-1:0] icfill_sram_addr;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            icfill_state <= ICFILL_IDLE;
            icfill_cnt   <= '0;
            icfill_base  <= '0;
        end else begin
            case (icfill_state)
                ICFILL_IDLE: begin
                    if (icache_fill_req) begin
                        // fill_addr is byte address; convert to word address
                        icfill_base  <= icache_fill_addr[MEM_DEPTH_LOG2+2:3];
                        icfill_cnt   <= 3'd0;
                        icfill_state <= ICFILL_READ;
                    end
                end
                ICFILL_READ: begin
                    // SRAM read issued this cycle; wait 1 cycle for data
                    icfill_state <= ICFILL_RESP;
                end
                ICFILL_RESP: begin
                    // sram_rdata now valid; send to I-Cache
                    if (icfill_cnt == 3'd7) begin
                        icfill_state <= ICFILL_IDLE;
                    end else begin
                        icfill_cnt   <= icfill_cnt + 3'd1;
                        icfill_state <= ICFILL_READ;
                    end
                end
                default: icfill_state <= ICFILL_IDLE;
            endcase
        end
    end

    assign icfill_sram_en   = (icfill_state == ICFILL_READ);
    assign icfill_sram_addr = icfill_base + {{(MEM_DEPTH_LOG2-3){1'b0}}, icfill_cnt};
    assign icache_fill_valid = (icfill_state == ICFILL_RESP);
    assign icache_fill_data  = sram_rdata;
    assign icache_fill_done  = (icfill_state == ICFILL_RESP) && (icfill_cnt == 3'd7);

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
        // Message queue
        .mq_wr_en     (mq_wr_en),
        .mq_wr_id     (mq_wr_id),
        .mq_wr_data   (mq_wr_data),
        .mq_wr_ready  (mq_wr_ready),
        .mq_rd_en     (mq_rd_en),
        .mq_rd_id     (mq_rd_id),
        .mq_rd_data   (mq_rd_data),
        .mq_rd_valid  (mq_rd_valid),
        // GC engine command
        .gc_cmd_valid (gc_cmd_valid),
        .gc_cmd_op    (gc_cmd_op),
        .gc_cmd_arg0  (gc_cmd_arg0),
        .gc_cmd_arg1  (gc_cmd_arg1),
        .gc_cmd_arg2  (gc_cmd_arg2),
        .gc_cmd_ready (gc_cmd_ready),
        .gc_engine_busy(gc_engine_busy),
        // Perf counter read
        .ctr_id       (ctr_id),
        .ctr_value    (ctr_value),
        // Config
        .cfg_tile_id  (cfg_tile_id),
        .cfg_thread_id(cfg_thread_id),
        .halted       (halted),
        .pc_out       (pc_out),
        .cycle_count  (cycle_count),
        // Perf counter strobes
        .ctr_alloc_inc       (ctr_alloc_inc),
        .ctr_alloc_bytes_inc (ctr_alloc_bytes_inc),
        .ctr_barrier_fire_inc(ctr_barrier_fire_inc),
        .ctr_barrier_filt_inc(ctr_barrier_filt_inc),
        .ctr_ic_hit_inc      (ctr_ic_hit_inc),
        .ctr_ic_miss_inc     (ctr_ic_miss_inc),
        .ctr_nursery_ovf_inc (ctr_nursery_ovf_inc),
        // I-Cache interface
        .icache_fetch_req   (icache_fetch_req),
        .icache_fetch_addr  (icache_fetch_addr),
        .icache_fetch_valid (icache_fetch_valid),
        .icache_fetch_inst  (icache_fetch_inst),
        // Instruction latch
        .inst_latch_en      (inst_latch_en),
        .inst_latch_data    (inst_latch_data)
    );

    // ---------------------------------------------------------------
    // Hardware Message Queues (4 FIFOs)
    // ---------------------------------------------------------------
    lm1_msg_queue u_msg_queue (
        .clk          (clk),
        .rst_n        (rst_n),
        // Core ports
        .wr_en        (mq_wr_en),
        .wr_id        (mq_wr_id),
        .wr_data      (mq_wr_data),
        .wr_ready     (mq_wr_ready),
        .rd_en        (mq_rd_en),
        .rd_id        (mq_rd_id),
        .rd_data      (mq_rd_data),
        .rd_valid     (mq_rd_valid),
        // External ports (for NoC / tile interconnect)
        .ext_wr_en    (ext_mq_wr_en),
        .ext_wr_id    (ext_mq_wr_id),
        .ext_wr_data  (ext_mq_wr_data),
        .ext_wr_ready (ext_mq_wr_ready),
        .ext_rd_en    (ext_mq_rd_en),
        .ext_rd_id    (ext_mq_rd_id),
        .ext_rd_data  (ext_mq_rd_data),
        .ext_rd_valid (ext_mq_rd_valid),
        // Status
        .q_empty      (mq_empty),
        .q_full       (mq_full)
    );

    // ---------------------------------------------------------------
    // Performance Counters
    // ---------------------------------------------------------------
    lm1_perf_counters u_perf_ctrs (
        .clk              (clk),
        .rst_n            (rst_n),
        .alloc_inc        (ctr_alloc_inc),
        .alloc_bytes_inc  (ctr_alloc_bytes_inc),
        .barrier_fire_inc (ctr_barrier_fire_inc),
        .barrier_filt_inc (ctr_barrier_filt_inc),
        .ic_hit_inc       (ctr_ic_hit_inc),
        .ic_miss_inc      (ctr_ic_miss_inc),
        .gc_cycle_inc     (gc_engine_busy),  // count every cycle an engine is active
        .nursery_ovf_inc  (ctr_nursery_ovf_inc),
        .rd_id            (ctr_id),
        .rd_value         (ctr_value)
    );

    // ---------------------------------------------------------------
    // Main Memory (SRAM)
    //
    // Multiplexed between LSU (normal operation) and external port
    // (loading programs / debug access).
    // When the CPU is not halted or external access is not enabled,
    // the LSU has full control.
    //
    // Address decode: if LSU address word-index >= MEM_DEPTH (i.e.
    // exceeds local tile SRAM), route access through the cluster
    // crossbar instead of local SRAM.
    // ---------------------------------------------------------------
    localparam int MEM_DEPTH = 1 << MEM_DEPTH_LOG2;

    logic               sram_en;
    logic               sram_we;
    logic [XLEN/8-1:0]  sram_be;
    logic [MEM_DEPTH_LOG2-1:0] sram_addr;
    logic [XLEN-1:0]   sram_wdata;
    logic [XLEN-1:0]   sram_rdata;

    // Address decode: is the LSU requesting a cluster (remote) address?
    logic               lsu_is_cluster_addr;
    assign lsu_is_cluster_addr = |mem_addr_lsu[XLEN-1:MEM_DEPTH_LOG2];

    // Crossbar request: issue when LSU drives a cluster address
    assign xbar_req_valid = mem_en && !ext_mem_en && lsu_is_cluster_addr;
    assign xbar_req_we    = mem_we;
    assign xbar_req_addr  = mem_addr_lsu;  // full word address to crossbar
    assign xbar_req_wdata = mem_wdata_lsu;

    // Memory port mux: external > I-Cache fill > LSU (local) > idle
    always_comb begin
        if (ext_mem_en) begin
            sram_en    = 1'b1;
            sram_we    = ext_mem_we;
            sram_be    = {(XLEN/8){1'b1}};  // full-word write for external
            sram_addr  = ext_mem_addr;
            sram_wdata = ext_mem_wdata;
        end else if (icfill_sram_en) begin
            // I-Cache line fill: read-only SRAM access
            sram_en    = 1'b1;
            sram_we    = 1'b0;
            sram_be    = {(XLEN/8){1'b1}};
            sram_addr  = icfill_sram_addr;
            sram_wdata = '0;
        end else if (!lsu_is_cluster_addr) begin
            sram_en    = mem_en;
            sram_we    = mem_we;
            sram_be    = mem_be;
            sram_addr  = mem_addr_lsu[MEM_DEPTH_LOG2-1:0];
            sram_wdata = mem_wdata_lsu;
        end else begin
            // Cluster address — SRAM is idle, crossbar handles it
            sram_en    = 1'b0;
            sram_we    = 1'b0;
            sram_be    = '0;
            sram_addr  = '0;
            sram_wdata = '0;
        end
    end

    // Read data mux: use crossbar response for cluster addresses,
    // local SRAM for tile-local addresses
    assign mem_rdata     = lsu_is_cluster_addr ? xbar_resp_data : sram_rdata;
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
