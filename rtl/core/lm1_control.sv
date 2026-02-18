// ============================================================================
// LM-1 Control Unit — Multi-Cycle FSM
//
// Orchestrates the fetch-decode-execute cycle for the LM-1 processor.
// Multi-cycle design: each instruction takes 2+ cycles.
//
// Architecture:
//   - All state register updates in a single always_ff block
//   - All next-state / output computation in a single always_comb
//   - Bridge via *_nxt signals: comb computes, ff latches
// ============================================================================
module lm1_control
    import lm1_pkg::*;
(
    input  logic                clk,
    input  logic                rst_n,

    // -- Decoded instruction (from decoder, active one cycle after ifetch) --
    input  decoded_t            dec_in,
    input  logic [XLEN-1:0]    imm_sext_in,

    // -- Register-file ports --
    input  logic [XLEN-1:0]    rf_rd1_data,   // read port 1 data
    input  logic [XLEN-1:0]    rf_rd2_data,   // read port 2 data
    output logic                rf_we,
    output logic [FULL_REG_W-1:0] rf_w_addr,
    output logic [XLEN-1:0]    rf_w_data,
    output logic [FULL_REG_W-1:0] rf_rd1_addr,  // read port 1 address
    output logic [FULL_REG_W-1:0] rf_rd2_addr,  // read port 2 address

    // -- ALU interface --
    output opcode_t             alu_op,
    output logic [FUNC_W-1:0]  alu_func,
    output logic [XLEN-1:0]    alu_a,
    output logic [XLEN-1:0]    alu_b,
    output logic                alu_start,
    input  logic [XLEN-1:0]    alu_result,
    input  logic                alu_valid,
    input  logic                alu_trap,
    input  logic [7:0]         alu_trap_code,

    // -- Branch unit interface --
    output logic [XLEN-1:0]    br_pc,
    output logic [XLEN-1:0]    br_val,
    output logic [REG_IDX_W-1:0] br_cond,
    output logic [IMM16_W-1:0] br_offset,
    output logic                br_is_br,
    output logic                br_is_cond,
    input  logic [XLEN-1:0]    br_target,
    input  logic                br_taken,

    // -- LSU interface --
    output logic                lsu_req,
    output logic [3:0]         lsu_op,
    output logic [XLEN-1:0]    lsu_addr,
    output logic [XLEN-1:0]    lsu_wdata,
    input  logic                lsu_ready,
    input  logic                lsu_valid,
    input  logic [XLEN-1:0]    lsu_rdata,
    input  logic [ILEN-1:0]    lsu_inst,

    // -- Header template table --
    output logic [7:0]         tmpl_rd_idx,
    input  logic [XLEN-1:0]    tmpl_rd_data,
    output logic                tmpl_wr_en,
    output logic [7:0]         tmpl_wr_idx,
    output logic [XLEN-1:0]    tmpl_wr_data,

    // -- IC table --
    output logic [XLEN-1:0]    ic_lu_pc,
    output logic [31:0]        ic_lu_shape,
    output logic                ic_lu_valid,
    input  logic [XLEN-1:0]    ic_hit_target,
    input  logic                ic_hit,
    output logic                ic_inst_valid,
    output logic [XLEN-1:0]    ic_inst_pc,
    output logic [31:0]        ic_inst_shape,
    output logic [XLEN-1:0]    ic_inst_target,

    // -- Message queue interface (4 hardware queues) --
    output logic                mq_wr_en,
    output logic [1:0]         mq_wr_id,         // which queue
    output logic [XLEN-1:0]    mq_wr_data,
    input  logic                mq_wr_ready,      // queue not full
    output logic                mq_rd_en,
    output logic [1:0]         mq_rd_id,         // which queue
    input  logic [XLEN-1:0]    mq_rd_data,
    input  logic                mq_rd_valid,      // queue not empty

    // -- GC engine command interface --
    output logic                gc_cmd_valid,
    output logic [3:0]         gc_cmd_op,
    output logic [XLEN-1:0]    gc_cmd_arg0,      // region base / pointer list base
    output logic [XLEN-1:0]    gc_cmd_arg1,      // region size / dest base
    output logic [XLEN-1:0]    gc_cmd_arg2,      // copy: region size (from rd)
    input  logic                gc_cmd_ready,     // engine accepts command
    input  logic                gc_engine_busy,   // any engine currently active

    // -- Scanner result FIFO (cluster → CPU) --
    input  logic [7:0]          scan_fifo_count,
    input  logic [XLEN-1:0]    scan_fifo_head_obj,
    input  logic [15:0]         scan_fifo_head_field,
    input  logic [XLEN-1:0]    scan_fifo_head_ref,
    output logic                scan_fifo_pop,     // pulse to pop head entry

    // -- Performance counter read port --
    output logic [4:0]         ctr_id,
    input  logic [XLEN-1:0]    ctr_value,

    // -- Config --
    input  logic [XLEN-1:0]    cfg_tile_id,
    input  logic [XLEN-1:0]    cfg_thread_id,

    // -- Status --
    output logic                halted,
    output logic [XLEN-1:0]    pc_out,
    output logic [XLEN-1:0]    cycle_count,

    // -- Performance counter increment strobes --
    output logic                ctr_alloc_inc,
    output logic [15:0]        ctr_alloc_bytes_inc,
    output logic                ctr_barrier_fire_inc,
    output logic                ctr_barrier_filt_inc,
    output logic                ctr_ic_hit_inc,
    output logic                ctr_ic_miss_inc,
    output logic                ctr_nursery_ovf_inc,

    // -- I-Cache fetch interface --
    output logic                icache_fetch_req,
    output logic [XLEN-1:0]    icache_fetch_addr,
    input  logic                icache_fetch_valid,
    input  logic [ILEN-1:0]    icache_fetch_inst,

    // -- Instruction latch control (for CPU inst_latched) --
    output logic                inst_latch_en,
    output logic [ILEN-1:0]    inst_latch_data
);

    // ---------------------------------------------------------------
    // LSU operation codes (must match lm1_lsu)
    // ---------------------------------------------------------------
    localparam logic [3:0] LSU_NONE    = 4'd0,
                           LSU_IFETCH  = 4'd1,
                           LSU_LOAD64  = 4'd2,
                           LSU_STORE64 = 4'd3,
                           LSU_LOAD32  = 4'd4,
                           LSU_LOAD_BYTE  = 4'd5,
                           LSU_STORE_BYTE = 4'd6,
                           LSU_LOAD_HALF  = 4'd7,
                           LSU_STORE_HALF = 4'd8,
                           LSU_LOAD_WORD  = 4'd9,
                           LSU_STORE_WORD = 4'd10;

    // ---------------------------------------------------------------
    // FSM states
    // ---------------------------------------------------------------
    typedef enum logic [5:0] {
        S_RESET,
        S_FETCH,
        S_FETCH_WAIT,
        S_DECODE,
        S_EXECUTE,
        S_ALU_WAIT,
        S_MEM,
        S_MEM_WAIT,
        S_FIELD_MEM,
        S_FIELD_WAIT,
        S_PUSH_FRAME_0,
        S_PUSH_FRAME_W0,
        S_PUSH_FRAME_1,
        S_PUSH_FRAME_W1,
        S_PUSH_FRAME_2,
        S_POP_FRAME_0,
        S_POP_FRAME_W0,
        S_POP_FRAME_LD_LR,
        S_POP_FRAME_1,
        S_POP_FRAME_W1,
        S_POP_FRAME_2,
        S_MULTI_ITER,
        S_MULTI_PUSH,
        S_MULTI_POP_WAIT,
        S_MULTI_SP_WR,
        S_ALLOC_HDR,
        S_ALLOC_HDR_W,
        S_ALLOC_ZERO,
        S_ALLOC_ZERO_W,
        S_ALLOC_INIT,
        S_ALLOC_INIT_W,
        S_ALLOC_INIT2,
        S_ALLOC_INIT2_W,
        S_ALLOC_RD_CDR,
        S_ALLOC_DONE,
        S_HDR_READ,
        S_HDR_WAIT,
        S_CLOS_CODE_RD,
        S_CLOS_CODE_WAIT,
        S_IC_DISPATCH,
        S_BARRIER_CHECK,
        S_BARRIER_MARK,
        S_BARRIER_MARK_W,
        S_SEND_WAIT,        // reserved — not yet used (blocking SEND)
        S_RECV_WAIT,        // reserved — not yet used (blocking RECV)
        S_TRY_RECV_WB,
        S_ENQ_WAIT,
        S_FENCE_GC,
        S_ATOMIC_STORE,
        S_ATOMIC_STORE_W,
        S_TRAP_LOOKUP,
        S_TRAP_WAIT,
        S_HALTED
    } state_t;

    // ---------------------------------------------------------------
    // State registers
    // ---------------------------------------------------------------
    state_t          state;
    logic [XLEN-1:0] pc;
    logic [XLEN-1:0] cyc;

    // --- FGMT: 4 hardware threads ---
    logic [THREAD_IDX_W-1:0] cur_thread;     // currently active thread (0..3)
    logic [XLEN-1:0] thread_pc    [0:NUM_THREADS-1];  // per-thread PC
    logic [NUM_THREADS-1:0] thread_active;   // which threads are active (not halted)

    // Helper: build banked register address from thread + reg index
    function automatic logic [FULL_REG_W-1:0] banked_addr(
        logic [THREAD_IDX_W-1:0] tid,
        logic [REG_IDX_W-1:0]    ridx
    );
        return {tid, ridx};
    endfunction

    decoded_t        dr;                 // latched instruction
    logic [XLEN-1:0] imm_r;             // latched sign-extended immediate

    logic [XLEN-1:0] opa, opb, opc;     // latched regs[rd], regs[rs1], regs[rs2]

    logic [XLEN-1:0] ta;                // temp address
    logic [XLEN-1:0] td;                // temp data / return addr
    logic [XLEN-1:0] tt;                // temp target
    logic [7:0]      tc;                // temp trap code
    logic [XLEN-1:0] th;                // temp header

    logic [15:0]     mm;                // multi mask
    logic [4:0]      mi;                // multi index
    logic [4:0]      mb;                // multi base
    logic            mdir;              // 0=push, 1=pop

    logic [XLEN-1:0] aa;                // alloc base address
    logic [15:0]     anw;               // alloc n_words
    logic [4:0]      acnt;              // alloc zero-fill counter
    logic            acon;              // alloc is_cons

    logic [XLEN-1:0] trap_tbl;
    logic [XLEN-1:0] trap_pc;
    logic [7:0]      trap_cause;
    logic            in_trap;

    // Barrier configuration registers (set via system traps)
    logic [XLEN-1:0] card_table_base;
    logic [5:0]      card_shift;       // log2(card_size), default=6 → 64B cards
    logic [XLEN-1:0] gen_boundary;     // addrs < gen_boundary are nursery (Gen 0)
    logic [XLEN-1:0] queue_base;       // message-queue base address

    // ---------------------------------------------------------------
    // Next-state signals (all computed in always_comb)
    // ---------------------------------------------------------------
    state_t          ns;
    logic [XLEN-1:0] pc_n;
    logic            cyc_inc;

    decoded_t        dr_n;
    logic [XLEN-1:0] imm_n;
    logic [XLEN-1:0] opa_n, opb_n, opc_n;
    logic [XLEN-1:0] ta_n, td_n, tt_n, th_n;
    logic [7:0]      tc_n;
    logic [15:0]     mm_n;
    logic [4:0]      mi_n, mb_n;
    logic            mdir_n;
    logic [XLEN-1:0] aa_n;
    logic [15:0]     anw_n;
    logic [4:0]      acnt_n;
    logic            acon_n;
    logic [XLEN-1:0] trap_tbl_n, trap_pc_n;
    logic [7:0]      trap_cause_n;
    logic            in_trap_n;

    logic [XLEN-1:0] card_table_base_n;
    logic [5:0]      card_shift_n;
    logic [XLEN-1:0] gen_boundary_n;
    logic [XLEN-1:0] queue_base_n;

    // ---------------------------------------------------------------
    // Outputs
    // ---------------------------------------------------------------
    assign halted      = (state == S_HALTED) && (thread_active == '0);
    assign pc_out      = pc;
    assign cycle_count = cyc;

    // ---------------------------------------------------------------
    // Sequential: latch next-state values
    // ---------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_RESET;
            pc         <= '0;
            cyc        <= '0;
            cur_thread <= '0;
            for (int t = 0; t < NUM_THREADS; t++) begin
                thread_pc[t] <= '0;
            end
            thread_active <= {{(NUM_THREADS-1){1'b0}}, 1'b1};  // only thread 0 active at reset
            dr         <= '0;
            imm_r      <= '0;
            opa        <= '0;
            opb        <= '0;
            opc        <= '0;
            ta         <= '0;
            td         <= '0;
            tt         <= '0;
            tc         <= '0;
            th         <= '0;
            mm         <= '0;
            mi         <= '0;
            mb         <= '0;
            mdir       <= 1'b0;
            aa         <= '0;
            anw        <= '0;
            acnt       <= '0;
            acon       <= 1'b0;
            trap_tbl   <= '0;
            trap_pc    <= '0;
            trap_cause <= '0;
            in_trap    <= 1'b0;
            card_table_base <= '0;
            card_shift <= 6'd6;  // default 64-byte cards
            gen_boundary <= '0;
            queue_base <= '0;
        end else begin
            state      <= ns;
            pc         <= pc_n;
            cyc        <= cyc_inc ? (cyc + 64'd1) : cyc;


            // FGMT: save/restore thread context at instruction boundaries
            // When entering S_FETCH from a completed instruction, save
            // current thread PC and switch to next active thread.
            if (ns == S_FETCH && state != S_FETCH && state != S_FETCH_WAIT &&
                state != S_RESET) begin
                // Save current thread's PC
                thread_pc[cur_thread] <= pc_n;
                // Round-robin to next active thread
                begin
                    logic [THREAD_IDX_W-1:0] next_t;
                    next_t = cur_thread;
                    for (int i = 1; i <= NUM_THREADS; i++) begin
                        logic [THREAD_IDX_W-1:0] candidate;
                        candidate = THREAD_IDX_W'((THREAD_IDX_W'(cur_thread) + THREAD_IDX_W'(i)) & THREAD_IDX_W'(NUM_THREADS-1));
                        if (thread_active[candidate]) begin
                            next_t = candidate;
                            break;
                        end
                    end
                    // Only override pc when actually switching to a
                    // different thread.  When next_t == cur_thread
                    // (single active thread), the default pc <= pc_n
                    // already has the correct value; overriding it with
                    // thread_pc[next_t] would read the stale (pre-save)
                    // value due to non-blocking assignment semantics and
                    // cause every instruction to execute twice.
                    if (next_t != cur_thread) begin
                        cur_thread <= next_t;
                        pc         <= thread_pc[next_t];
                    end
                end
            end else if (state == S_RESET) begin
                // At reset exit, load thread 0's PC
                pc <= thread_pc[0];
            end

            // Track thread halted state
            if (ns == S_HALTED && state != S_HALTED) begin
                thread_active[cur_thread] <= 1'b0;
                // If other threads are still active, switch to one
                // instead of actually entering S_HALTED.
                begin
                    logic found_active;
                    logic [THREAD_IDX_W-1:0] next_t;
                    found_active = 1'b0;
                    next_t = cur_thread;
                    for (int i = 1; i < NUM_THREADS; i++) begin
                        logic [THREAD_IDX_W-1:0] candidate;
                        candidate = THREAD_IDX_W'((THREAD_IDX_W'(cur_thread) + THREAD_IDX_W'(i)) & THREAD_IDX_W'(NUM_THREADS-1));
                        if (thread_active[candidate] && !found_active) begin
                            next_t = candidate;
                            found_active = 1'b1;
                        end
                    end
                    if (found_active) begin
                        state <= S_FETCH;  // override ns = S_HALTED
                        cur_thread <= next_t;
                        pc <= thread_pc[next_t];
                    end
                end
            end
            dr         <= dr_n;
            imm_r      <= imm_n;
            opa        <= opa_n;
            opb        <= opb_n;
            opc        <= opc_n;
            ta         <= ta_n;
            td         <= td_n;
            tt         <= tt_n;
            tc         <= tc_n;
            th         <= th_n;
            mm         <= mm_n;
            mi         <= mi_n;
            mb         <= mb_n;
            mdir       <= mdir_n;
            aa         <= aa_n;
            anw        <= anw_n;
            acnt       <= acnt_n;
            acon       <= acon_n;
            trap_tbl   <= trap_tbl_n;
            trap_pc    <= trap_pc_n;
            trap_cause <= trap_cause_n;
            in_trap    <= in_trap_n;
            card_table_base <= card_table_base_n;
            card_shift <= card_shift_n;
            gen_boundary <= gen_boundary_n;
            queue_base <= queue_base_n;
        end
    end

    // ---------------------------------------------------------------
    // Combinational: compute all next values and outputs
    //
    // Every signal must be assigned on every path (no latches).
    // Defaults at top → overridden in specific states.
    // ---------------------------------------------------------------
    always_comb begin
        // === Defaults: hold all state ===
        ns          = state;
        pc_n        = pc;
        cyc_inc     = 1'b0;
        dr_n        = dr;
        imm_n       = imm_r;
        opa_n       = opa;
        opb_n       = opb;
        opc_n       = opc;
        ta_n        = ta;
        td_n        = td;
        tt_n        = tt;
        tc_n        = tc;
        th_n        = th;
        mm_n        = mm;
        mi_n        = mi;
        mb_n        = mb;
        mdir_n      = mdir;
        aa_n        = aa;
        anw_n       = anw;
        acnt_n      = acnt;
        acon_n      = acon;
        trap_tbl_n  = trap_tbl;
        trap_pc_n   = trap_pc;
        trap_cause_n = trap_cause;
        in_trap_n   = in_trap;

        card_table_base_n = card_table_base;
        card_shift_n = card_shift;
        gen_boundary_n = gen_boundary;
        queue_base_n = queue_base;

        // === Default outputs ===
        rf_we       = 1'b0;
        rf_w_addr   = banked_addr(cur_thread, dr.rd);
        rf_w_data   = '0;
        rf_rd1_addr = banked_addr(cur_thread, dec_in.rd);
        rf_rd2_addr = banked_addr(cur_thread, dec_in.rs1);

        alu_op      = dr.opcode;
        alu_func    = dr.func;
        alu_a       = opb;
        alu_b       = opc;
        alu_start   = 1'b0;

        br_pc       = pc;
        br_val      = opa;
        br_cond     = dr.rs1;
        br_offset   = dr.imm16;
        br_is_br    = 1'b0;
        br_is_cond  = 1'b0;

        lsu_req     = 1'b0;
        lsu_op      = LSU_NONE;
        lsu_addr    = '0;
        lsu_wdata   = '0;

        tmpl_rd_idx = '0;
        tmpl_wr_en  = 1'b0;
        tmpl_wr_idx = '0;
        tmpl_wr_data = '0;

        ic_lu_valid   = 1'b0;
        ic_lu_pc      = '0;
        ic_lu_shape   = '0;
        ic_inst_valid = 1'b0;
        ic_inst_pc    = '0;
        ic_inst_shape = '0;
        ic_inst_target = '0;

        // Message queue defaults
        mq_wr_en    = 1'b0;
        mq_wr_id    = '0;
        mq_wr_data  = '0;
        mq_rd_en    = 1'b0;
        mq_rd_id    = '0;

        // GC engine defaults
        gc_cmd_valid = 1'b0;
        gc_cmd_op    = '0;
        gc_cmd_arg0  = '0;
        gc_cmd_arg1  = '0;
        gc_cmd_arg2  = '0;
        scan_fifo_pop = 1'b0;

        // Perf counter defaults
        ctr_id               = '0;
        ctr_alloc_inc        = 1'b0;
        ctr_alloc_bytes_inc  = '0;
        ctr_barrier_fire_inc = 1'b0;
        ctr_barrier_filt_inc = 1'b0;
        ctr_ic_hit_inc       = 1'b0;
        ctr_ic_miss_inc      = 1'b0;
        ctr_nursery_ovf_inc  = 1'b0;

        // I-Cache fetch defaults
        icache_fetch_req  = 1'b0;
        icache_fetch_addr = '0;
        inst_latch_en     = 1'b0;
        inst_latch_data   = '0;

        // =========================================================
        case (state)

        S_RESET: ns = S_FETCH;

        // ---------------------------------------------------------
        // FETCH — check I-Cache first; LSU fallback only if needed.
        // ---------------------------------------------------------
        S_FETCH: begin
            icache_fetch_req  = 1'b1;
            icache_fetch_addr = pc;
            if (icache_fetch_valid) begin
                // I-Cache hit (or fill just completed)
                inst_latch_en   = 1'b1;
                inst_latch_data = icache_fetch_inst;
                ns = S_DECODE;
            end
            // On miss the I-Cache fill sequencer (in lm1_cpu) handles the
            // burst read from SRAM.  We simply stay in S_FETCH until the
            // fill finishes and icache_fetch_valid asserts.
        end

        S_FETCH_WAIT: begin
            // Legacy LSU-based fetch path (kept for fallback / future use)
            if (lsu_valid) begin
                inst_latch_en   = 1'b1;
                inst_latch_data = lsu_inst;
                ns = S_DECODE;
            end
        end

        // ---------------------------------------------------------
        // DECODE: latch decoded fields + operand values
        //
        // The decoder drives dec_in from the fetched instruction.
        // Port 1 reads regs[rd], port 2 reads regs[rs1].
        // We can capture regs[rs2] next cycle from port 1.
        // For simplicity, capture rs2 immediately via port 1 reuse:
        //   We set rf_rd1_addr = dec_in.rs2, and read rf_rd1_data.
        //   But port 1 is also reading rd this same cycle.
        //   → use two cycles or accept the collission.
        //
        // We do DECODE in one cycle: read rd(port1) + rs1(port2),
        // then EXECUTE reads rs2 on port1. This costs an extra cycle
        // for R-format instructions but simplifies the design.
        // ---------------------------------------------------------
        S_DECODE: begin
            dr_n     = dec_in;
            imm_n    = imm_sext_in;
            // Read rd on port1, rs1 on port2 (banked by thread)
            rf_rd1_addr = banked_addr(cur_thread, dec_in.rd);
            rf_rd2_addr = banked_addr(cur_thread, dec_in.rs1);
            opa_n    = rf_rd1_data;  // regs[rd]
            opb_n    = rf_rd2_data;  // regs[rs1]
            ns       = S_EXECUTE;
        end

        // ---------------------------------------------------------
        // EXECUTE: big dispatch
        //
        // At entry: dr/imm_r latched.
        // opa=regs[rd], opb=regs[rs1].
        // We need regs[rs2] on port 1 this cycle.
        // ---------------------------------------------------------
        S_EXECUTE: begin
            // Read rs2 on port 1 (banked by thread)
            rf_rd1_addr = banked_addr(cur_thread, dr.rs2);
            opc_n       = rf_rd1_data;   // regs[rs2] available
            // Use rf_rd1_data as rs2 value immediate this cycle
            // (wire directly — no latency issue since regfile reads are combinational)

            // Default: advance PC, count cycle
            pc_n    = pc + 64'd4;
            cyc_inc = 1'b1;

            case (dr.opcode)

            // ===== ALU R-type =====
            OP_ARITH_RAW: begin
                alu_op   = OP_ARITH_RAW;
                alu_func = dr.func;
                alu_a    = opb;            // rs1
                alu_b    = rf_rd1_data;    // rs2 (live)
                alu_start = (dr.func inside {FUNC_DIV, FUNC_MOD});
                cyc_inc  = 1'b0;
                pc_n     = pc;
                ns       = S_ALU_WAIT;
            end

            OP_BITWISE: begin
                alu_op   = OP_BITWISE;
                alu_func = dr.func;
                alu_a    = opb;
                alu_b    = rf_rd1_data;
                cyc_inc  = 1'b0;
                pc_n     = pc;
                ns       = S_ALU_WAIT;
            end

            OP_ARITH_FIX: begin
                alu_op   = OP_ARITH_FIX;
                alu_func = dr.func;
                alu_a    = opb;
                alu_b    = rf_rd1_data;
                alu_start = (dr.func == FUNC_DIV_FIX);
                cyc_inc  = 1'b0;
                pc_n     = pc;
                ns       = S_ALU_WAIT;
            end

            OP_ADD_FIX_IMM: begin
                alu_op  = OP_ADD_FIX_IMM;
                alu_a   = opb;
                alu_b   = imm_r;
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_ALU_WAIT;
            end

            OP_CMP_TAGGED: begin
                alu_op   = OP_CMP_TAGGED;
                alu_func = dr.func;
                alu_a    = opb;
                alu_b    = rf_rd1_data;
                cyc_inc  = 1'b0;
                pc_n     = pc;
                ns       = S_ALU_WAIT;
            end

            OP_TST: begin
                alu_op  = OP_TST;
                alu_a   = opb;
                alu_b   = {48'b0, dr.imm16};
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_ALU_WAIT;
            end

            OP_TST_SHAPE: begin
                if (is_any_ref(opb)) begin
                    ta_n    = ref_address(opb);
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_HDR_READ;
                end else begin
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    rf_w_data = VAL_NIL;
                    ns        = S_FETCH;
                end
            end

            // ===== Load immediate =====
            OP_LI: begin
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, dr.rd);
                rf_w_data = imm_r;
                ns        = S_FETCH;
            end

            OP_LUI: begin
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, dr.rd);
                rf_w_data = {32'b0, dr.imm16, 16'b0};
                ns        = S_FETCH;
            end

            OP_LI32: begin
                // Fetch 32-bit immediate from PC+4
                lsu_req  = 1'b1;
                lsu_op   = LSU_LOAD32;
                lsu_addr = pc + 64'd4;
                cyc_inc  = 1'b0;
                pc_n     = pc;
                if (lsu_ready) ns = S_FIELD_WAIT;
            end

            // ===== Raw memory =====
            OP_LDR: begin
                ta_n    = (opb + imm_r) & ~64'h7;
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end

            OP_STR: begin
                ta_n    = (opa + imm_r) & ~64'h7;
                td_n    = opb;   // rs1 value to store
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end

            // ===== Sub-word loads =====
            OP_LDB: begin
                ta_n    = opb + imm_r;   // byte address, no alignment
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end
            OP_LDH: begin
                ta_n    = (opb + imm_r) & ~64'h1;  // halfword aligned
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end
            OP_LDW: begin
                ta_n    = (opb + imm_r) & ~64'h3;  // word aligned
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end

            // ===== Sub-word stores =====
            OP_STB: begin
                ta_n    = opa + imm_r;   // byte address
                td_n    = opb;
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end
            OP_STH: begin
                ta_n    = (opa + imm_r) & ~64'h1;
                td_n    = opb;
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end
            OP_STW: begin
                ta_n    = (opa + imm_r) & ~64'h3;
                td_n    = opb;
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end

            // ===== Tagged field access =====
            OP_LD: begin
                if (!is_any_ref(opb)) begin
                    tc_n    = TRAP_NOT_REF;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opb) + {56'b0, dr.imm16[4:0] + 5'd1, 3'b0};
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_FIELD_MEM;
                end
            end

            OP_LD_CAR_CDR: begin
                if (!is_any_ref(opb)) begin
                    tc_n    = TRAP_NOT_REF;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opb) +
                              (dr.imm16[0] ? 64'd16 : 64'd8);
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_FIELD_MEM;
                end
            end

            OP_ST: begin
                if (!is_any_ref(opa)) begin
                    tc_n    = TRAP_NOT_REF;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opa) + {56'b0, dr.rs2 + 5'd1, 3'b0};
                    td_n    = opb;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_FIELD_MEM;
                end
            end

            // ===== Store with write barrier =====
            // Stores opb (rs1 value) into opa[field], then checks
            // whether a card-table mark is needed.
            //
            // Barrier fires when:
            //   1. Stored value (opb) is a ref (any_ref tag)
            //   2. Container (opa) address >= gen_boundary (old-gen)
            //   3. Stored ref (opb) address < gen_boundary (nursery)
            // This detects old→young cross-generation stores.
            //
            // If barrier fires: compute card-table byte address,
            // issue a byte store to mark the card dirty (0xFF).
            OP_ST_WB: begin
                if (!is_any_ref(opa)) begin
                    tc_n    = TRAP_NOT_REF;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opa) + {56'b0, dr.rs2 + 5'd1, 3'b0};
                    td_n    = opb;              // value being stored
                    tt_n    = ref_address(opa);  // container address (for card calc)
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_FIELD_MEM;      // do the store first
                    // After S_FIELD_MEM completes, opcode==ST_WB triggers
                    // barrier check in S_BARRIER_CHECK path
                end
            end

            OP_ST_CAR_CDR: begin
                if (!is_any_ref(opa)) begin
                    tc_n    = TRAP_NOT_REF;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opa) +
                              (dr.rs2[0] ? 64'd16 : 64'd8);
                    td_n    = opb;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_FIELD_MEM;
                end
            end

            // ===== Branches =====
            OP_BR: begin
                br_is_br = 1'b1;
                if (br_taken) pc_n = br_target;
                ns = S_FETCH;
            end

            OP_BR_COND: begin
                br_is_cond = 1'b1;
                br_val     = opa;
                br_cond    = dr.rs1;
                if (br_taken) pc_n = br_target;
                ns = S_FETCH;
            end

            // ===== Stack PUSH/POP =====
            OP_PUSH_POP: begin
                rf_rd1_addr = banked_addr(cur_thread, REG_SP);
                begin
                    logic [XLEN-1:0] sp_v;
                    sp_v = rf_rd1_data;
                    if (dr.func == FUNC_PUSH) begin
                        ta_n      = sp_v - 64'd8;
                        td_n      = opa;         // value to push
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, REG_SP);
                        rf_w_data = sp_v - 64'd8;
                        cyc_inc   = 1'b0;
                        pc_n      = pc;
                        ns        = S_MEM;
                    end else begin
                        ta_n    = sp_v;   // load from current SP
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns      = S_MEM;
                    end
                end
            end

            OP_PUSH_MULTI: begin
                mm_n     = dr.imm16;
                mb_n     = {dr.rd[0], 4'b0};
                mdir_n   = 1'b0;
                mi_n     = 5'd0;
                cyc_inc  = 1'b0;
                pc_n     = pc;
                ns       = S_MULTI_ITER;
            end

            OP_POP_MULTI: begin
                mm_n     = dr.imm16;
                mb_n     = {dr.rd[0], 4'b0};
                mdir_n   = 1'b1;
                mi_n     = 5'd15;
                cyc_inc  = 1'b0;
                pc_n     = pc;
                ns       = S_MULTI_ITER;
            end

            // ===== Calls =====
            OP_CALL_DIRECT: begin
                tt_n     = pc + {{(XLEN-18){dr.imm16[15]}}, dr.imm16, 2'b00};
                td_n     = pc + 64'd4;
                cyc_inc  = 1'b0;
                pc_n     = pc;
                ns       = S_PUSH_FRAME_0;
            end

            OP_CALL_CLOSURE: begin
                if (!is_any_ref(opa)) begin
                    tc_n    = TRAP_NOT_CLOSURE;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opa);
                    td_n    = pc + 64'd4;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_HDR_READ;
                end
            end

            OP_CALL_IC: begin
                if (!is_any_ref(opa)) begin
                    tc_n    = TRAP_NOT_REF;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opa);
                    td_n    = pc + 64'd4;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_HDR_READ;
                end
            end

            OP_TAILCALL_DIR: begin
                pc_n = pc + {{(XLEN-18){dr.imm16[15]}}, dr.imm16, 2'b00};
                ns   = S_FETCH;
            end

            OP_TAILCALL_IC: begin
                if (!is_any_ref(opa)) begin
                    tc_n    = TRAP_NOT_REF;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    ta_n    = ref_address(opa);
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_HDR_READ;
                end
            end

            OP_RET: begin
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_POP_FRAME_0;
            end

            OP_JR: begin
                pc_n = opa;
                ns   = S_FETCH;
            end

            // ===== IC Install =====
            OP_IC_INSTALL: begin
                if (is_any_ref(opb)) begin
                    ta_n    = ref_address(opb);
                    td_n    = rf_rd1_data;   // rs2 = code entry
                    tt_n    = opa;           // rd = callsite PC
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_HDR_READ;
                end else begin
                    ic_inst_valid  = 1'b1;
                    ic_inst_pc     = opa;
                    ic_inst_shape  = '0;
                    ic_inst_target = rf_rd1_data;
                    ns = S_FETCH;
                end
            end

            // ===== Allocation =====
            OP_ALLOC: begin
                rf_rd1_addr = banked_addr(cur_thread, REG_NP);
                rf_rd2_addr = banked_addr(cur_thread, REG_NL);
                begin
                    logic [XLEN-1:0] np_v, nl_v, new_np;
                    logic [15:0] nw;
                    np_v   = rf_rd1_data;
                    nl_v   = rf_rd2_data;
                    nw     = {11'b0, dr.rs1};
                    new_np = np_v + {45'b0, nw + 16'd1, 3'b0};
                    anw_n  = nw;
                    acon_n = 1'b0;
                    tmpl_rd_idx = dr.imm16[7:0];
                    if (new_np > nl_v) begin
                        tc_n    = TRAP_NURSERY_OVERFLOW;
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns      = S_TRAP_LOOKUP;
                    end else begin
                        aa_n      = np_v;
                        acnt_n    = nw[4:0];
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, REG_NP);
                        rf_w_data = new_np;
                        cyc_inc   = 1'b0;
                        pc_n      = pc;
                        ns        = S_ALLOC_HDR;
                    end
                end
            end

            OP_ALLOC_CONS: begin
                rf_rd1_addr = banked_addr(cur_thread, REG_NP);
                rf_rd2_addr = banked_addr(cur_thread, REG_NL);
                begin
                    logic [XLEN-1:0] np_v, nl_v, new_np;
                    np_v   = rf_rd1_data;
                    nl_v   = rf_rd2_data;
                    new_np = np_v + 64'd24;
                    anw_n  = 16'd2;
                    acon_n = 1'b1;
                    tmpl_rd_idx = 8'd0;
                    if (new_np > nl_v) begin
                        tc_n    = TRAP_NURSERY_OVERFLOW;
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns      = S_TRAP_LOOKUP;
                    end else begin
                        aa_n      = np_v;
                        acnt_n    = 5'd2;
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, REG_NP);
                        rf_w_data = new_np;
                        cyc_inc   = 1'b0;
                        pc_n      = pc;
                        // Need an extra cycle to capture rs2 (cdr) since
                        // rf_rd1_addr was overridden to read NP above.
                        ns        = S_ALLOC_RD_CDR;
                    end
                end
            end

            OP_ALLOCV: begin
                rf_rd1_addr = banked_addr(cur_thread, REG_NP);
                rf_rd2_addr = banked_addr(cur_thread, REG_NL);
                begin
                    logic [XLEN-1:0] np_v, nl_v, new_np;
                    logic signed [XLEN-1:0] len_s;
                    logic [15:0] nw;
                    np_v  = rf_rd1_data;
                    nl_v  = rf_rd2_data;
                    len_s = $signed(opb) >>> 1;
                    nw    = len_s[15:0] + 16'd1;
                    anw_n = nw;
                    acon_n = 1'b0;
                    tmpl_rd_idx = dr.imm16[7:0];
                    new_np = np_v + {45'b0, nw + 16'd1, 3'b0};
                    if (new_np > nl_v) begin
                        tc_n    = TRAP_NURSERY_OVERFLOW;
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns      = S_TRAP_LOOKUP;
                    end else begin
                        aa_n      = np_v;
                        acnt_n    = nw[4:0];
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, REG_NP);
                        rf_w_data = new_np;
                        cyc_inc   = 1'b0;
                        pc_n      = pc;
                        ns        = S_ALLOC_HDR;
                    end
                end
            end

            OP_ALLOC_CLOSURE: begin
                rf_rd1_addr = banked_addr(cur_thread, REG_NP);
                rf_rd2_addr = banked_addr(cur_thread, REG_NL);
                begin
                    logic [XLEN-1:0] np_v, nl_v, new_np;
                    logic [15:0] nw;
                    nw     = {11'b0, dr.rs2} + 16'd1;
                    np_v   = rf_rd1_data;
                    nl_v   = rf_rd2_data;
                    new_np = np_v + {45'b0, nw + 16'd1, 3'b0};
                    anw_n  = nw;
                    acon_n = 1'b0;
                    tmpl_rd_idx = 8'd1;
                    if (new_np > nl_v) begin
                        tc_n    = TRAP_NURSERY_OVERFLOW;
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns      = S_TRAP_LOOKUP;
                    end else begin
                        aa_n      = np_v;
                        acnt_n    = nw[4:0];
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, REG_NP);
                        rf_w_data = new_np;
                        cyc_inc   = 1'b0;
                        pc_n      = pc;
                        ns        = S_ALLOC_HDR;
                    end
                end
            end

            // ===== TRAP =====
            OP_TRAP: begin
                tc_n = dr.raw26[7:0];
                if (dr.raw26[7]) begin
                    // System traps
                    case (dr.raw26[7:0])
                        8'h90: begin  // SET_TRAP_TABLE
                            rf_rd1_addr = banked_addr(cur_thread, 5'd1);
                            trap_tbl_n   = rf_rd1_data;
                        end
                        8'h91: begin  // SET_TEMPLATE
                            rf_rd1_addr = banked_addr(cur_thread, 5'd1);
                            rf_rd2_addr = banked_addr(cur_thread, 5'd2);
                            tmpl_wr_en   = 1'b1;
                            tmpl_wr_idx  = rf_rd1_data[7:0];
                            tmpl_wr_data = rf_rd2_data;
                        end
                        8'h92: begin  // SET_CARD_BASE
                            rf_rd1_addr = banked_addr(cur_thread, 5'd1);
                            card_table_base_n = rf_rd1_data;
                        end
                        8'h93: begin  // SET_CARD_SHIFT
                            rf_rd1_addr = banked_addr(cur_thread, 5'd1);
                            card_shift_n = rf_rd1_data[5:0];
                        end
                        8'h94: begin  // SET_GEN_BOUNDARY
                            rf_rd1_addr = banked_addr(cur_thread, 5'd1);
                            gen_boundary_n = rf_rd1_data;
                        end
                        8'h95: begin  // SET_QUEUE_BASE
                            rf_rd1_addr = banked_addr(cur_thread, 5'd1);
                            queue_base_n = rf_rd1_data;
                        end
                        default: ;
                    endcase
                    ns = S_FETCH;
                end else begin
                    // User trap: advance PC past the TRAP instruction
                    // so that ERET returns to the instruction after TRAP.
                    // (pc_n and cyc_inc keep their S_EXECUTE defaults:
                    //  pc_n = pc + 4, cyc_inc = 1)
                    ns      = S_TRAP_LOOKUP;
                end
            end

            OP_ERET: begin
                if (!in_trap) begin
                    tc_n    = TRAP_UNIMPLEMENTED;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end else begin
                    in_trap_n = 1'b0;
                    pc_n      = trap_pc;
                    ns        = S_FETCH;
                end
            end

            // ===== SYS_INFO =====
            OP_SYS_INFO: begin
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, dr.rd);
                case (dr.rs1)
                    SYS_TILE_ID:      rf_w_data = cfg_tile_id;
                    SYS_THREAD_ID:    rf_w_data = cfg_thread_id;
                    SYS_CYCLE:        rf_w_data = cyc;
                    SYS_TRAP_CAUSE:   rf_w_data = {56'b0, trap_cause};
                    SYS_TRAP_PC:      rf_w_data = trap_pc;
                    SYS_CARD_BASE:    rf_w_data = card_table_base;
                    SYS_CARD_SHIFT:   rf_w_data = {58'b0, card_shift};
                    SYS_GEN_BOUNDARY: rf_w_data = gen_boundary;
                    SYS_QUEUE_BASE:   rf_w_data = queue_base;
                    SYS_GC_STATUS:    rf_w_data = {63'b0, gc_engine_busy};
                    SYS_SCAN_COUNT:     rf_w_data = {56'b0, scan_fifo_count};
                    SYS_SCAN_HEAD_OBJ:  rf_w_data = scan_fifo_head_obj;
                    SYS_SCAN_HEAD_FIELD: rf_w_data = {48'b0, scan_fifo_head_field};
                    SYS_SCAN_POP_REF: begin
                        rf_w_data     = scan_fifo_head_ref;
                        scan_fifo_pop = (scan_fifo_count != 8'd0);
                    end
                    SYS_PERF_CTR: begin
                        // Counter ID in imm16[4:0]
                        ctr_id    = dr.imm16[4:0];
                        rf_w_data = ctr_value;
                    end
                    default:          rf_w_data = '0;
                endcase
                ns = S_FETCH;
            end

            // ===== HALT / NOP =====
            OP_HALT_NOP: begin
                if (dr.rd == 5'd0)
                    ns = S_HALTED;
                else
                    ns = S_FETCH;
            end

            // ===== No-ops: Prefetch family =====
            OP_PREFETCH_REF, OP_PREFETCH_FLD,
            OP_PREFETCH_CDR, OP_GATHER_PRE: begin
                ns = S_FETCH;
            end

            // ===== GC engine commands =====
            // ENQ.SCAN/COPY/FIXUP/COMPACT: issue a command to the
            // cluster's movement engines.
            //   rs1 = region base address (opb)
            //   rs2 = region size / dest addr (read from port 1)
            OP_ENQ_SCAN: begin
                gc_cmd_valid = 1'b1;
                gc_cmd_op    = GC_CMD_SCAN;
                gc_cmd_arg0  = opb;             // region base
                gc_cmd_arg1  = rf_rd1_data;     // region size (rs2 on port1)
                rf_rd1_addr  = banked_addr(cur_thread, dr.rs2);
                if (gc_cmd_ready) begin
                    ns = S_FETCH;
                end else begin
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_ENQ_WAIT;
                end
            end

            OP_ENQ_COPY: begin
                gc_cmd_valid = 1'b1;
                gc_cmd_op    = GC_CMD_COPY;
                gc_cmd_arg0  = opb;
                gc_cmd_arg1  = rf_rd1_data;
                gc_cmd_arg2  = opa;             // rd = region size
                rf_rd1_addr  = banked_addr(cur_thread, dr.rs2);
                if (gc_cmd_ready) begin
                    ns = S_FETCH;
                end else begin
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_ENQ_WAIT;
                end
            end

            OP_ENQ_FIXUP: begin
                gc_cmd_valid = 1'b1;
                gc_cmd_op    = GC_CMD_FIXUP;
                gc_cmd_arg0  = opb;
                gc_cmd_arg1  = rf_rd1_data;
                rf_rd1_addr  = banked_addr(cur_thread, dr.rs2);
                if (gc_cmd_ready) begin
                    ns = S_FETCH;
                end else begin
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_ENQ_WAIT;
                end
            end

            OP_ENQ_COMPACT: begin
                gc_cmd_valid = 1'b1;
                gc_cmd_op    = GC_CMD_COMPACT;
                gc_cmd_arg0  = opb;
                gc_cmd_arg1  = rf_rd1_data;
                rf_rd1_addr  = banked_addr(cur_thread, dr.rs2);
                if (gc_cmd_ready) begin
                    ns = S_FETCH;
                end else begin
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_ENQ_WAIT;
                end
            end

            // ===== FAA / FENCE.GC =====
            OP_FAA_FENCE: begin
                if (dr.func == FUNC_FENCE_GC) begin
                    // FENCE.GC: wait until all GC engines are idle
                    // and all pending barrier stores are drained
                    if (gc_engine_busy) begin
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns      = S_FENCE_GC;
                    end else begin
                        ns = S_FETCH;
                    end
                end else begin
                    ta_n    = is_any_ref(opb) ? ref_address(opb) : opb;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_MEM;
                end
            end

            // ===== CAS.TAGGED =====
            OP_CAS_TAGGED: begin
                ta_n    = is_any_ref(opb) ?
                          (ref_address(opb) & ~64'h7) : (opb & ~64'h7);
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_MEM;
            end

            // ===== SEND/RECV =====
            //
            // SEND Rd, #queue_id   — send opa (regs[rd]) to queue
            //   queue_id is in imm16[1:0]
            //   If queue full → TRAP_QUEUE_FULL
            //
            // RECV Rd, #queue_id   — receive from queue into Rd
            //   queue_id is in imm16[1:0]
            //   If queue empty → TRAP_QUEUE_EMPTY
            //
            OP_SEND: begin
                mq_wr_id   = dr.imm16[1:0];
                mq_wr_data = opa;              // regs[rd]
                if (mq_wr_ready) begin
                    mq_wr_en = 1'b1;
                    ns = S_FETCH;
                end else begin
                    tc_n    = TRAP_QUEUE_FULL;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_TRAP_LOOKUP;
                end
            end

            OP_RECV: begin
                mq_rd_id  = dr.imm16[1:0];
                if (dr.func != '0) begin
                    // --- TRY.RECV Rd, Rd2, Rs_queue ---
                    // Non-blocking: func[4:0] = Rd2 register index
                    if (mq_rd_valid) begin
                        mq_rd_en  = 1'b1;
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, dr.rd);
                        rf_w_data = mq_rd_data;
                        opa_n     = VAL_T;     // remember: success
                        cyc_inc   = 1'b0;      // defer PC advance to WB
                        pc_n      = pc;
                        ns = S_TRY_RECV_WB;
                    end else begin
                        // Empty — Rd = nil
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, dr.rd);
                        rf_w_data = VAL_NIL;
                        opa_n     = VAL_NIL;   // remember: empty
                        cyc_inc   = 1'b0;      // defer PC advance to WB
                        pc_n      = pc;
                        ns = S_TRY_RECV_WB;
                    end
                end else begin
                    // --- RECV (blocking) ---
                    if (mq_rd_valid) begin
                        mq_rd_en  = 1'b1;
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, dr.rd);
                        rf_w_data = mq_rd_data;
                        ns = S_FETCH;
                    end else begin
                        tc_n    = TRAP_QUEUE_EMPTY;
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns      = S_TRAP_LOOKUP;
                    end
                end
            end

            default: begin
                tc_n    = TRAP_UNIMPLEMENTED;
                cyc_inc = 1'b0;
                pc_n    = pc;
                ns      = S_TRAP_LOOKUP;
            end

            endcase  // dr.opcode in S_EXECUTE
        end  // S_EXECUTE

        // ---------------------------------------------------------
        // ALU_WAIT: wait for ALU result (single or multi-cycle)
        // ---------------------------------------------------------
        S_ALU_WAIT: begin
            alu_op   = dr.opcode;
            alu_func = dr.func;
            alu_a    = opb;
            alu_b    = (dr.opcode == OP_ADD_FIX_IMM) ? imm_r :
                       (dr.opcode == OP_TST)         ? {48'b0, dr.imm16} : opc;

            if (alu_valid) begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                if (alu_trap) begin
                    tc_n = alu_trap_code;
                    ns   = S_TRAP_LOOKUP;
                end else begin
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    rf_w_data = alu_result;
                    ns        = S_FETCH;
                end
            end
        end

        // ---------------------------------------------------------
        // MEM: issue load/store
        // ---------------------------------------------------------
        S_MEM: begin
            lsu_req = 1'b1;
            case (dr.opcode)
                OP_STR: begin
                    lsu_op    = LSU_STORE64;
                    lsu_addr  = ta;
                    lsu_wdata = td;
                end
                OP_PUSH_POP: begin
                    if (dr.func == FUNC_PUSH) begin
                        lsu_op    = LSU_STORE64;
                        lsu_addr  = ta;
                        lsu_wdata = td;
                    end else begin
                        lsu_op   = LSU_LOAD64;
                        lsu_addr = ta;
                    end
                end
                OP_FAA_FENCE: begin
                    lsu_op   = LSU_LOAD64;
                    lsu_addr = ta;
                end
                OP_CAS_TAGGED: begin
                    lsu_op   = LSU_LOAD64;
                    lsu_addr = ta;
                end
                // Sub-word loads
                OP_LDB: begin
                    lsu_op   = LSU_LOAD_BYTE;
                    lsu_addr = ta;
                end
                OP_LDH: begin
                    lsu_op   = LSU_LOAD_HALF;
                    lsu_addr = ta;
                end
                OP_LDW: begin
                    lsu_op   = LSU_LOAD_WORD;
                    lsu_addr = ta;
                end
                // Sub-word stores
                OP_STB: begin
                    lsu_op    = LSU_STORE_BYTE;
                    lsu_addr  = ta;
                    lsu_wdata = td;
                end
                OP_STH: begin
                    lsu_op    = LSU_STORE_HALF;
                    lsu_addr  = ta;
                    lsu_wdata = td;
                end
                OP_STW: begin
                    lsu_op    = LSU_STORE_WORD;
                    lsu_addr  = ta;
                    lsu_wdata = td;
                end
                default: begin
                    lsu_op   = LSU_LOAD64;
                    lsu_addr = ta;
                end
            endcase
            if (lsu_ready) ns = S_MEM_WAIT;
        end

        // ---------------------------------------------------------
        // MEM_WAIT
        // ---------------------------------------------------------
        S_MEM_WAIT: begin
            if (lsu_valid) begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;

                case (dr.opcode)
                OP_LDR: begin
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    rf_w_data = lsu_rdata;
                    ns        = S_FETCH;
                end

                OP_STR: begin
                    ns = S_FETCH;
                end

                // Sub-word loads: LSU already extracted and zero-extended
                OP_LDB, OP_LDH, OP_LDW: begin
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    rf_w_data = lsu_rdata;
                    ns        = S_FETCH;
                end

                // Sub-word stores: done
                OP_STB, OP_STH, OP_STW: begin
                    ns = S_FETCH;
                end

                OP_PUSH_POP: begin
                    if (dr.func == FUNC_PUSH) begin
                        ns = S_FETCH;
                    end else begin
                        // POP: write loaded value to rd
                        rf_we     = 1'b1;
                        rf_w_addr = banked_addr(cur_thread, dr.rd);
                        rf_w_data = lsu_rdata;
                        // SP += 8
                        ta_n = ta + 64'd8;
                        // Defer PC advance and cycle count to S_MULTI_SP_WR
                        // (S_MEM_WAIT default already set cyc_inc=1, pc_n=pc+4
                        //  but S_MULTI_SP_WR will also set them → double advance)
                        cyc_inc = 1'b0;
                        pc_n    = pc;
                        ns   = S_MULTI_SP_WR;  // write SP
                    end
                end

                OP_FAA_FENCE: begin
                    // old = mem[addr], store old + delta, return old
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    rf_w_data = lsu_rdata;
                    // Save computed new value for write-back via S_ATOMIC_STORE
                    // (cannot issue store here — LSU is still in WAIT_RD,
                    //  lsu_ready is 0, so the store would be silently dropped)
                    td_n    = lsu_rdata + opc;
                    cyc_inc = 1'b0;
                    pc_n    = pc;
                    ns      = S_ATOMIC_STORE;
                end

                OP_CAS_TAGGED: begin
                    if (lsu_rdata == opc) begin
                        // Match → save new value for write-back via S_ATOMIC_STORE
                        // (cannot issue store here — LSU is still in WAIT_RD,
                        //  lsu_ready is 0, so the store would be silently dropped)
                        rf_rd1_addr = banked_addr(cur_thread, dr.func);
                        td_n        = rf_rd1_data;
                        rf_we       = 1'b1;
                        rf_w_addr   = banked_addr(cur_thread, dr.rd);
                        rf_w_data   = VAL_T;
                        cyc_inc     = 1'b0;
                        pc_n        = pc;
                        ns          = S_ATOMIC_STORE;
                    end else begin
                        rf_we       = 1'b1;
                        rf_w_addr   = banked_addr(cur_thread, dr.rd);
                        rf_w_data   = VAL_NIL;
                        ns          = S_FETCH;
                    end
                end

                default: ns = S_FETCH;
                endcase
            end
        end

        // ---------------------------------------------------------
        // FIELD_MEM: tagged field load/store
        // ---------------------------------------------------------
        S_FIELD_MEM: begin
            lsu_req = 1'b1;
            case (dr.opcode)
                OP_LD, OP_LD_CAR_CDR: begin
                    lsu_op   = LSU_LOAD64;
                    lsu_addr = ta;
                end
                default: begin
                    lsu_op    = LSU_STORE64;
                    lsu_addr  = ta;
                    lsu_wdata = td;
                end
            endcase
            if (lsu_ready) ns = S_FIELD_WAIT;
        end

        // ---------------------------------------------------------
        // FIELD_WAIT
        // ---------------------------------------------------------
        S_FIELD_WAIT: begin
            if (lsu_valid) begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;

                case (dr.opcode)
                OP_LD, OP_LD_CAR_CDR: begin
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    rf_w_data = lsu_rdata;
                    ns = S_FETCH;
                end
                OP_LI32: begin
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    rf_w_data = lsu_rdata;
                    pc_n      = pc + 64'd8;
                    ns = S_FETCH;
                end
                OP_ST_WB: begin
                    // Store completed — now check barrier
                    ns = S_BARRIER_CHECK;
                end
                default: begin
                    ns = S_FETCH;
                end
                endcase
            end
        end

        // ---------------------------------------------------------
        // BARRIER_CHECK: decide if card-table mark is needed
        //
        // td = stored value (set in S_EXECUTE)
        // tt = container ref address (set in S_EXECUTE for ST.WB)
        //
        // Filter 1: skip if stored value is not a ref
        // Filter 2: skip if same generation (both nursery or both old)
        //
        // A cross-gen store is: container in old-gen AND stored ref
        // points to nursery.  With gen_boundary configured:
        //   container_addr >= gen_boundary  AND  ref_addr < gen_boundary
        // ---------------------------------------------------------
        S_BARRIER_CHECK: begin
            if (!is_any_ref(td)) begin
                // Filter 1: not a ref — no barrier needed
                ctr_barrier_filt_inc = 1'b1;
                ns = S_FETCH;
            end else begin
                // td is a ref — check generations
                begin
                    logic [XLEN-1:0] stored_addr;
                    stored_addr = ref_address(td);
                    if (gen_boundary != '0 &&
                        tt >= gen_boundary &&
                        stored_addr < gen_boundary) begin
                        // Cross-gen old→young: FIRE barrier
                        // Compute card-table byte address:
                        //   card_index = (container_addr - 0) >> card_shift
                        //   card_addr  = card_table_base + card_index
                        ta_n = card_table_base + (tt >> card_shift);
                        td_n = {56'hFF_FFFF_FFFF_FFFF, 8'hFF};  // dirty marker
                        ctr_barrier_fire_inc = 1'b1;
                        ns   = S_BARRIER_MARK;
                    end else begin
                        // Same-gen or gen_boundary not configured — filtered
                        ctr_barrier_filt_inc = 1'b1;
                        ns = S_FETCH;
                    end
                end
            end
        end

        // ---------------------------------------------------------
        // BARRIER_MARK: issue byte store to card table
        // ---------------------------------------------------------
        S_BARRIER_MARK: begin
            lsu_req   = 1'b1;
            lsu_op    = LSU_STORE_BYTE;  // single-byte store to card table
            lsu_addr  = ta;
            lsu_wdata = td;              // low byte = 0xFF (marks card dirty)
            if (lsu_ready) ns = S_BARRIER_MARK_W;
        end

        S_BARRIER_MARK_W: begin
            if (lsu_valid) begin
                ns = S_FETCH;
            end
        end

        // ---------------------------------------------------------
        // PUSH_FRAME: save LR, save FP, set LR=ret, FP=SP
        //   State 0: store LR at SP-8, SP-=8
        //   State W0: wait for store
        //   State 1: store FP at SP-8, SP-=8
        //   State W1: wait for store
        //   State 2: LR=ret_addr(td), FP=SP
        // ---------------------------------------------------------
        S_PUSH_FRAME_0: begin
            rf_rd1_addr = banked_addr(cur_thread, REG_SP);
            rf_rd2_addr = banked_addr(cur_thread, REG_LR);
            begin
                logic [XLEN-1:0] sp_v;
                sp_v = rf_rd1_data;
                ta_n  = sp_v - 64'd8;
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, REG_SP);
                rf_w_data = sp_v - 64'd8;
                lsu_req   = 1'b1;
                lsu_op    = LSU_STORE64;
                lsu_addr  = sp_v - 64'd8;
                lsu_wdata = rf_rd2_data;  // LR value
                if (lsu_ready) ns = S_PUSH_FRAME_W0;
            end
        end

        S_PUSH_FRAME_W0: begin
            if (lsu_valid) ns = S_PUSH_FRAME_1;
        end

        S_PUSH_FRAME_1: begin
            rf_rd1_addr = banked_addr(cur_thread, REG_SP);
            rf_rd2_addr = banked_addr(cur_thread, REG_FP);
            begin
                logic [XLEN-1:0] sp_v;
                sp_v = rf_rd1_data;
                ta_n  = sp_v - 64'd8;
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, REG_SP);
                rf_w_data = sp_v - 64'd8;
                lsu_req   = 1'b1;
                lsu_op    = LSU_STORE64;
                lsu_addr  = sp_v - 64'd8;
                lsu_wdata = rf_rd2_data;  // FP value
                if (lsu_ready) ns = S_PUSH_FRAME_W1;
            end
        end

        S_PUSH_FRAME_W1: begin
            if (lsu_valid) ns = S_PUSH_FRAME_2;
        end

        S_PUSH_FRAME_2: begin
            // LR = return address (td), FP = current SP
            rf_rd1_addr = banked_addr(cur_thread, REG_SP);
            rf_we       = 1'b1;
            rf_w_addr   = banked_addr(cur_thread, REG_LR);
            rf_w_data   = td;
            ta_n        = rf_rd1_data;  // save SP for FP write next cycle
            // Need one more cycle to write FP
            ns = S_POP_FRAME_2;  // reuse: writes FP=ta, then jumps to tt
        end

        // ---------------------------------------------------------
        // POP_FRAME: restore FP, LR, SP, return
        //   State 0: save LR→tt, load FP from mem[FP]
        //   State W0: wait; restore FP from rdata, load LR from mem[FP+8]
        //   State 1: wait; restore LR from rdata, SP = FP+16
        //   State W1: wait for any pending
        //   State 2: write FP=ta (reused from push_frame too), jump to tt
        // ---------------------------------------------------------
        S_POP_FRAME_0: begin
            rf_rd1_addr = banked_addr(cur_thread, REG_LR);
            rf_rd2_addr = banked_addr(cur_thread, REG_FP);
            tt_n        = rf_rd1_data;    // save LR as return address
            ta_n        = rf_rd2_data;    // FP value = restore point
            // SP = FP
            rf_we       = 1'b1;
            rf_w_addr   = banked_addr(cur_thread, REG_SP);
            rf_w_data   = rf_rd2_data;
            // Load saved FP from mem[FP]
            lsu_req     = 1'b1;
            lsu_op      = LSU_LOAD64;
            lsu_addr    = rf_rd2_data;
            if (lsu_ready) ns = S_POP_FRAME_W0;
        end

        S_POP_FRAME_W0: begin
            if (lsu_valid) begin
                // Restore FP from loaded data
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, REG_FP);
                rf_w_data = lsu_rdata;
                // Next cycle: issue load for saved LR
                ns = S_POP_FRAME_LD_LR;
            end
        end

        S_POP_FRAME_LD_LR: begin
            // Issue load of saved LR from mem[FP+8]
            // (LSU is back in IDLE now after the previous load completed)
            lsu_req   = 1'b1;
            lsu_op    = LSU_LOAD64;
            lsu_addr  = ta + 64'd8;
            if (lsu_ready) ns = S_POP_FRAME_1;
        end

        S_POP_FRAME_1: begin
            if (lsu_valid) begin
                // Restore LR
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, REG_LR);
                rf_w_data = lsu_rdata;
                // SP = FP + 16  (two saved words)
                ta_n = ta + 64'd16;
                ns   = S_POP_FRAME_W1;
            end
        end

        S_POP_FRAME_W1: begin
            // Write SP = restored FP+16
            rf_we     = 1'b1;
            rf_w_addr = banked_addr(cur_thread, REG_SP);
            rf_w_data = ta;
            ns        = S_POP_FRAME_2;
        end

        S_POP_FRAME_2: begin
            // For PUSH_FRAME completion: write FP = SP (ta holds SP)
            // For POP_FRAME / RET: jump to tt
            case (dr.opcode)
            OP_RET: begin
                cyc_inc = 1'b1;
                pc_n    = tt;
                ns      = S_FETCH;
            end
            OP_PUSH_POP: begin
                // POP single — not used here (handled by MEM_WAIT)
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                ns      = S_FETCH;
            end
            default: begin
                // CALL variants: write FP = ta (SP), jump to tt
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, REG_FP);
                rf_w_data = ta;
                cyc_inc   = 1'b1;
                pc_n      = tt;
                ns        = S_FETCH;
            end
            endcase
        end

        // ---------------------------------------------------------
        // MULTI: push/pop multiple registers
        // ---------------------------------------------------------
        S_MULTI_ITER: begin
            if (mm == 16'd0) begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                ns      = S_FETCH;
            end else if (mm[mi[3:0]]) begin
                rf_rd1_addr = banked_addr(cur_thread, REG_SP);
                rf_rd2_addr = banked_addr(cur_thread, mb + mi);  // register to push/pop

                if (mdir) begin
                    // POP: load from [SP]
                    lsu_req  = 1'b1;
                    lsu_op   = LSU_LOAD64;
                    lsu_addr = rf_rd1_data;  // SP
                    if (lsu_ready) ns = S_MULTI_POP_WAIT;
                end else begin
                    // PUSH: SP -= 8, store reg at new SP
                    begin
                        logic [XLEN-1:0] new_sp;
                        new_sp = rf_rd1_data - 64'd8;
                        rf_we      = 1'b1;
                        rf_w_addr  = banked_addr(cur_thread, REG_SP);
                        rf_w_data  = new_sp;
                        lsu_req    = 1'b1;
                        lsu_op     = LSU_STORE64;
                        lsu_addr   = new_sp;
                        lsu_wdata  = rf_rd2_data;
                        if (lsu_ready) ns = S_MULTI_PUSH;
                    end
                end
            end else begin
                // Skip: advance index
                if (mdir) begin
                    if (mi == 5'd0) mm_n = 16'd0;
                    else            mi_n = mi - 5'd1;
                end else begin
                    if (mi == 5'd15) mm_n = 16'd0;
                    else             mi_n = mi + 5'd1;
                end
            end
        end

        S_MULTI_PUSH: begin
            // Wait for store to complete, advance index
            if (lsu_valid) begin
                if (mi == 5'd15) mm_n = 16'd0;
                else             mi_n = mi + 5'd1;
                ns = S_MULTI_ITER;
            end
        end

        S_MULTI_POP_WAIT: begin
            if (lsu_valid) begin
                // Write loaded value into register
                rf_we     = 1'b1;
                rf_w_addr = banked_addr(cur_thread, mb + mi);
                rf_w_data = lsu_rdata;
                // SP += 8 — need to do in next state
                ns = S_MULTI_SP_WR;
            end
        end

        S_MULTI_SP_WR: begin
            // Update SP after POP
            rf_rd1_addr = banked_addr(cur_thread, REG_SP);
            rf_we       = 1'b1;
            rf_w_addr   = banked_addr(cur_thread, REG_SP);
            rf_w_data   = rf_rd1_data + 64'd8;

            case (dr.opcode)
            OP_POP_MULTI: begin
                if (mi == 5'd0) mm_n = 16'd0;
                else            mi_n = mi - 5'd1;
                ns = S_MULTI_ITER;
            end
            OP_PUSH_POP: begin
                // Single POP done
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                ns      = S_FETCH;
            end
            default: begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                ns      = S_FETCH;
            end
            endcase
        end

        // ---------------------------------------------------------
        // ALLOCATION
        // ---------------------------------------------------------
        S_ALLOC_HDR: begin
            tmpl_rd_idx = (dr.opcode == OP_ALLOC_CONS)    ? 8'd0 :
                          (dr.opcode == OP_ALLOC_CLOSURE)  ? 8'd1 :
                          dr.imm16[7:0];
            lsu_req     = 1'b1;
            lsu_op      = LSU_STORE64;
            lsu_addr    = aa;
            lsu_wdata   = header_patch_size(tmpl_rd_data, anw);
            if (lsu_ready) ns = S_ALLOC_HDR_W;
        end

        S_ALLOC_HDR_W: begin
            if (lsu_valid) begin
                ta_n = aa + 64'd8;
                if (acnt == 5'd0)
                    ns = S_ALLOC_INIT;
                else
                    ns = S_ALLOC_ZERO;
            end
        end

        S_ALLOC_ZERO: begin
            lsu_req   = 1'b1;
            lsu_op    = LSU_STORE64;
            lsu_addr  = ta;
            lsu_wdata = '0;
            if (lsu_ready) ns = S_ALLOC_ZERO_W;
        end

        S_ALLOC_ZERO_W: begin
            if (lsu_valid) begin
                ta_n   = ta + 64'd8;
                acnt_n = acnt - 5'd1;
                if (acnt == 5'd1)
                    ns = S_ALLOC_INIT;
                else
                    ns = S_ALLOC_ZERO;
            end
        end

        S_ALLOC_INIT: begin
            case (dr.opcode)
            OP_ALLOC_CONS: begin
                lsu_req   = 1'b1;
                lsu_op    = LSU_STORE64;
                lsu_addr  = aa + 64'd8;
                lsu_wdata = (dr.rs1 == 5'd0) ? VAL_NIL : opb;
                if (lsu_ready) ns = S_ALLOC_INIT_W;
            end
            OP_ALLOC_CLOSURE: begin
                lsu_req   = 1'b1;
                lsu_op    = LSU_STORE64;
                lsu_addr  = aa + 64'd8;
                lsu_wdata = opb;
                if (lsu_ready) ns = S_ALLOC_DONE;
            end
            OP_ALLOCV: begin
                lsu_req   = 1'b1;
                lsu_op    = LSU_STORE64;
                lsu_addr  = aa + 64'd8;
                lsu_wdata = opb;  // tagged length
                if (lsu_ready) ns = S_ALLOC_DONE;
            end
            default: ns = S_ALLOC_DONE;
            endcase
        end

        S_ALLOC_INIT_W: begin
            // Wait for car write, then write cdr
            if (lsu_valid) ns = S_ALLOC_INIT2;
        end

        // ---------------------------------------------------------
        // ALLOC_RD_CDR: capture rs2 (cdr value) for ALLOC.CONS
        //
        // In S_EXECUTE, rf_rd1_addr was overridden to read NP/NL,
        // which prevents the default opc_n = rf_rd1_data from
        // capturing regs[rs2]. This state reads rs2 on port 1.
        // ---------------------------------------------------------
        S_ALLOC_RD_CDR: begin
            rf_rd1_addr = banked_addr(cur_thread, dr.rs2);
            opc_n       = rf_rd1_data;
            ns          = S_ALLOC_HDR;
        end

        S_ALLOC_INIT2: begin
            // CONS: write cdr at aa+16
            lsu_req   = 1'b1;
            lsu_op    = LSU_STORE64;
            lsu_addr  = aa + 64'd16;
            lsu_wdata = (dr.rs2 == 5'd0) ? VAL_NIL : opc;
            if (lsu_ready) ns = S_ALLOC_INIT2_W;
        end

        S_ALLOC_INIT2_W: begin
            if (lsu_valid) ns = S_ALLOC_DONE;
        end

        S_ALLOC_DONE: begin
            rf_we     = 1'b1;
            rf_w_addr = banked_addr(cur_thread, dr.rd);
            rf_w_data = make_ref(aa, acon);
            cyc_inc   = 1'b1;
            pc_n      = pc + 64'd4;
            ctr_alloc_inc       = 1'b1;
            ctr_alloc_bytes_inc = (anw + 16'd1) << 3;  // (nwords+1)*8
            ns        = S_FETCH;
        end

        // ---------------------------------------------------------
        // HDR_READ / HDR_WAIT: read object header
        // ---------------------------------------------------------
        S_HDR_READ: begin
            lsu_req  = 1'b1;
            lsu_op   = LSU_LOAD64;
            lsu_addr = ta;
            if (lsu_ready) ns = S_HDR_WAIT;
        end

        S_HDR_WAIT: begin
            if (lsu_valid) begin
                th_n = lsu_rdata;

                case (dr.opcode)
                OP_TST_SHAPE: begin
                    rf_we     = 1'b1;
                    rf_w_addr = banked_addr(cur_thread, dr.rd);
                    if (is_header(lsu_rdata) &&
                        (header_shape_id(lsu_rdata) & 32'hFFFF) ==
                        {16'b0, dr.imm16})
                        rf_w_data = VAL_T;
                    else
                        rf_w_data = VAL_NIL;
                    cyc_inc = 1'b1;
                    pc_n    = pc + 64'd4;
                    ns      = S_FETCH;
                end

                OP_CALL_CLOSURE: begin
                    if (!is_header(lsu_rdata) ||
                        header_subtype(lsu_rdata) != HDR_CLOSURE) begin
                        tc_n = TRAP_NOT_CLOSURE;
                        ns   = S_TRAP_LOOKUP;
                    end else begin
                        // Read code entry at addr+8
                        ns = S_CLOS_CODE_RD;
                    end
                end

                OP_CALL_IC, OP_TAILCALL_IC: begin
                    begin
                        logic [31:0] shp;
                        shp = is_header(lsu_rdata) ?
                              header_shape_id(lsu_rdata) : 32'd0;
                        ic_lu_valid = 1'b1;
                        ic_lu_pc    = pc;
                        ic_lu_shape = shp;
                        ns          = S_IC_DISPATCH;
                    end
                end

                OP_IC_INSTALL: begin
                    begin
                        logic [31:0] shp;
                        shp = is_header(lsu_rdata) ?
                              header_shape_id(lsu_rdata) : 32'd0;
                        ic_inst_valid  = 1'b1;
                        ic_inst_pc     = tt;
                        ic_inst_shape  = shp;
                        ic_inst_target = td;
                        cyc_inc = 1'b1;
                        pc_n    = pc + 64'd4;
                        ns      = S_FETCH;
                    end
                end

                default: ns = S_FETCH;
                endcase
            end
        end

        // ---------------------------------------------------------
        // CLOS_CODE_RD / WAIT: read closure code entry
        // ---------------------------------------------------------
        S_CLOS_CODE_RD: begin
            lsu_req  = 1'b1;
            lsu_op   = LSU_LOAD64;
            lsu_addr = ta + 64'd8;
            if (lsu_ready) ns = S_CLOS_CODE_WAIT;
        end

        S_CLOS_CODE_WAIT: begin
            if (lsu_valid) begin
                tt_n = lsu_rdata;
                ns   = S_PUSH_FRAME_0;
            end
        end

        // ---------------------------------------------------------
        // IC_DISPATCH
        // ---------------------------------------------------------
        S_IC_DISPATCH: begin
            case (dr.opcode)
            OP_CALL_IC: begin
                if (ic_hit) begin
                    tt_n = ic_hit_target;
                    ctr_ic_hit_inc = 1'b1;
                    ns   = S_PUSH_FRAME_0;
                end else begin
                    tc_n = TRAP_IC_MISS;
                    ctr_ic_miss_inc = 1'b1;
                    ns   = S_TRAP_LOOKUP;
                end
            end
            OP_TAILCALL_IC: begin
                if (ic_hit) begin
                    pc_n    = ic_hit_target;
                    cyc_inc = 1'b1;
                    ctr_ic_hit_inc = 1'b1;
                    ns      = S_FETCH;
                end else begin
                    tc_n = TRAP_IC_MISS;
                    ctr_ic_miss_inc = 1'b1;
                    ns   = S_TRAP_LOOKUP;
                end
            end
            default: ns = S_FETCH;
            endcase
        end

        // ---------------------------------------------------------
        // TRY_RECV_WB: write Rd2 = T (success) or NIL (empty)
        //   opa holds VAL_T or VAL_NIL from previous cycle
        //   dr.func[4:0] holds the Rd2 register index
        // ---------------------------------------------------------
        S_TRY_RECV_WB: begin
            rf_we     = 1'b1;
            rf_w_addr = banked_addr(cur_thread, dr.func);
            rf_w_data = opa;  // VAL_T or VAL_NIL
            cyc_inc   = 1'b1;
            pc_n      = pc + 64'd4;
            ns        = S_FETCH;
        end

        // ---------------------------------------------------------
        // ENQ_WAIT: wait for GC engine to accept command
        // ---------------------------------------------------------
        S_ENQ_WAIT: begin
            gc_cmd_valid = 1'b1;
            gc_cmd_op    = (dr.opcode == OP_ENQ_SCAN)    ? GC_CMD_SCAN :
                           (dr.opcode == OP_ENQ_COPY)    ? GC_CMD_COPY :
                           (dr.opcode == OP_ENQ_FIXUP)   ? GC_CMD_FIXUP :
                                                           GC_CMD_COMPACT;
            gc_cmd_arg0  = opb;
            gc_cmd_arg1  = opc;
            gc_cmd_arg2  = opa;    // region size for COPY
            if (gc_cmd_ready) begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                ns      = S_FETCH;
            end
        end

        // ---------------------------------------------------------
        // FENCE_GC: stall until all GC engines are idle
        // ---------------------------------------------------------
        S_FENCE_GC: begin
            if (!gc_engine_busy) begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                ns      = S_FETCH;
            end
        end

        // ---------------------------------------------------------
        // ATOMIC_STORE: issue the store part of FAA / CAS.TAGGED
        //
        // The load response was consumed in S_MEM_WAIT.  The LSU was
        // still in WAIT_RD that cycle (lsu_ready == 0), so we could
        // NOT issue the store there.  By the time we reach this state
        // the LSU has returned to IDLE (lsu_ready == 1) and we can
        // safely issue the store.
        // ---------------------------------------------------------
        S_ATOMIC_STORE: begin
            lsu_req   = 1'b1;
            lsu_op    = LSU_STORE64;
            lsu_addr  = ta;
            lsu_wdata = td;
            if (lsu_ready) ns = S_ATOMIC_STORE_W;
        end

        S_ATOMIC_STORE_W: begin
            if (lsu_valid) begin
                cyc_inc = 1'b1;
                pc_n    = pc + 64'd4;
                ns      = S_FETCH;
            end
        end

        // ---------------------------------------------------------
        // TRAP LOOKUP
        // ---------------------------------------------------------
        S_TRAP_LOOKUP: begin
            trap_pc_n    = pc;
            trap_cause_n = tc;
            in_trap_n    = 1'b1;

            // Perf counter for nursery overflow
            if (tc == TRAP_NURSERY_OVERFLOW)
                ctr_nursery_ovf_inc = 1'b1;

            if (trap_tbl != '0 && tc < 8'h80) begin
                lsu_req  = 1'b1;
                lsu_op   = LSU_LOAD64;
                lsu_addr = trap_tbl + {52'b0, tc, 3'b0};
                if (lsu_ready) ns = S_TRAP_WAIT;
            end else begin
                ns = S_HALTED;
            end
        end

        S_TRAP_WAIT: begin
            if (lsu_valid) begin
                if (lsu_rdata == '0) begin
                    ns = S_HALTED;
                end else begin
                    pc_n = lsu_rdata;
                    ns   = S_FETCH;
                end
            end
        end

        // ---------------------------------------------------------
        S_HALTED: begin
            ns = S_HALTED;
        end

        default: ns = S_HALTED;

        endcase  // state
    end

endmodule
