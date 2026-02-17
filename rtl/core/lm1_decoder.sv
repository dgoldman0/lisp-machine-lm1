// ============================================================================
// LM-1 Instruction Decoder
//
// Pure combinational decode of a 32-bit instruction word into all fields.
// All fields are extracted in parallel regardless of instruction format;
// the execute stage selects relevant fields based on opcode.
//
// Also produces control signals consumed by the datapath:
//   - Register file read/write port selects
//   - ALU operation select
//   - Memory access type
//   - Branch type
//   - Immediate type and value
// ============================================================================
module lm1_decoder
    import lm1_pkg::*;
(
    input  logic [ILEN-1:0]  inst_word,    // raw 32-bit instruction

    // Decoded fields
    output decoded_t         dec,          // all instruction fields
    output logic [XLEN-1:0]  imm_sext,     // sign-extended immediate (context-dependent)

    // Register file addressing
    output logic [REG_IDX_W-1:0] rf_rd_idx,   // destination register index
    output logic [REG_IDX_W-1:0] rf_rs1_idx,  // source 1 register index
    output logic [REG_IDX_W-1:0] rf_rs2_idx,  // source 2 register index
    output logic                 rf_we,        // register file write enable
    output logic                 rf_rd_rs2,    // need to read rs2 (for 3-operand insns)

    // Control classification
    output logic is_alu_op,       // uses ALU result
    output logic is_mem_load,     // loads from memory -> rd
    output logic is_mem_store,    // stores to memory
    output logic is_branch,       // branch instruction (affects PC)
    output logic is_jump,         // unconditional jump / call / ret
    output logic is_system,       // system / trap / halt / nop
    output logic is_alloc,        // allocation instruction
    output logic is_multi_cycle,  // requires FSM sequencing (not single-cycle)
    output logic is_nop           // no-op (prefetch, NOP, fence)
);

    // ---------------------------------------------------------------
    // Field extraction (purely combinational)
    // ---------------------------------------------------------------
    always_comb begin
        dec = decode_inst(inst_word);
    end

    // ---------------------------------------------------------------
    // Immediate sign extension
    //
    // Most instructions that use an immediate treat imm16 as signed.
    // We always provide the sign-extended value; consumers that need
    // unsigned (e.g., TST tag constant) mask appropriately.
    // ---------------------------------------------------------------
    assign imm_sext = sext16(dec.imm16);

    // ---------------------------------------------------------------
    // Register file addressing
    //
    // Default mapping (may be overridden by control FSM for multi-cycle):
    //   rf_rs1_idx = dec.rs1  (bits 20:16)
    //   rf_rs2_idx = dec.rs2  (bits 15:11)
    //   rf_rd_idx  = dec.rd   (bits 25:21)
    // ---------------------------------------------------------------
    assign rf_rd_idx  = dec.rd;
    assign rf_rs1_idx = dec.rs1;
    assign rf_rs2_idx = dec.rs2;

    // ---------------------------------------------------------------
    // Control signal generation
    // ---------------------------------------------------------------
    always_comb begin
        // Defaults
        rf_we          = 1'b0;
        rf_rd_rs2      = 1'b0;
        is_alu_op      = 1'b0;
        is_mem_load    = 1'b0;
        is_mem_store   = 1'b0;
        is_branch      = 1'b0;
        is_jump        = 1'b0;
        is_system      = 1'b0;
        is_alloc       = 1'b0;
        is_multi_cycle = 1'b0;
        is_nop         = 1'b0;

        case (dec.opcode)
            // --- ALU operations (Format R, write rd) ---
            OP_ARITH_RAW: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
                rf_rd_rs2 = 1'b1;
            end
            OP_BITWISE: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
                rf_rd_rs2 = (dec.func != FUNC_NOT);  // NOT only uses rs1
            end

            // --- Tagged arithmetic (Format R, write rd) ---
            OP_ARITH_FIX: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
                rf_rd_rs2 = 1'b1;
            end
            OP_ADD_FIX_IMM: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
            end
            OP_CMP_TAGGED: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
                rf_rd_rs2 = 1'b1;
            end

            // --- Type tests (Format I, write rd) ---
            OP_TST: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
            end
            OP_TST_SHAPE: begin
                is_mem_load    = 1'b1;  // needs to read header from memory
                is_multi_cycle = 1'b1;
                rf_we          = 1'b1;
            end

            // --- Load immediate ---
            OP_LI: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
            end
            OP_LI32: begin
                is_mem_load    = 1'b1;  // fetches trailing 32-bit word
                is_multi_cycle = 1'b1;
                rf_we          = 1'b1;
            end
            OP_LUI: begin
                is_alu_op = 1'b1;
                rf_we     = 1'b1;
            end

            // --- Raw memory access ---
            OP_LDR: begin
                is_mem_load = 1'b1;
                rf_we       = 1'b1;
            end
            OP_STR: begin
                is_mem_store = 1'b1;
            end

            // --- Tagged field access ---
            OP_LD, OP_LD_CAR_CDR: begin
                is_mem_load    = 1'b1;
                is_multi_cycle = 1'b1;  // ref check + address calc + load
                rf_we          = 1'b1;
            end
            OP_ST, OP_ST_WB, OP_ST_CAR_CDR: begin
                is_mem_store   = 1'b1;
                is_multi_cycle = 1'b1;  // ref check + address calc + store
                rf_rd_rs2      = 1'b1;  // field index in rs2
            end

            // --- Branches ---
            OP_BR: begin
                is_branch = 1'b1;
            end
            OP_BR_COND: begin
                is_branch = 1'b1;
            end

            // --- Stack push/pop ---
            OP_PUSH_POP: begin
                if (dec.func == FUNC_PUSH) begin
                    is_mem_store = 1'b1;
                end else begin
                    is_mem_load = 1'b1;
                    rf_we       = 1'b1;
                end
            end
            OP_PUSH_MULTI, OP_POP_MULTI: begin
                is_multi_cycle = 1'b1;
                // PUSH_MULTI does stores, POP_MULTI does loads + writes
            end

            // --- Calls and returns ---
            OP_CALL_DIRECT, OP_CALL_CLOSURE, OP_CALL_IC: begin
                is_jump        = 1'b1;
                is_multi_cycle = 1'b1;  // push_frame + jump
            end
            OP_TAILCALL_IC, OP_TAILCALL_DIR: begin
                is_jump = 1'b1;
            end
            OP_RET: begin
                is_jump        = 1'b1;
                is_multi_cycle = 1'b1;  // pop_frame
            end
            OP_JR: begin
                is_jump = 1'b1;
            end

            // --- IC install ---
            OP_IC_INSTALL: begin
                is_multi_cycle = 1'b1;
                rf_rd_rs2      = 1'b1;
            end

            // --- Allocation ---
            OP_ALLOC, OP_ALLOC_CONS, OP_ALLOCV, OP_ALLOC_CLOSURE: begin
                is_alloc       = 1'b1;
                is_multi_cycle = 1'b1;
                rf_we          = 1'b1;
            end

            // --- Concurrency ---
            OP_SEND: begin
                is_multi_cycle = 1'b1;
            end
            OP_RECV: begin
                is_multi_cycle = 1'b1;
                rf_we          = 1'b1;
            end
            OP_CAS_TAGGED: begin
                is_multi_cycle = 1'b1;
                rf_we          = 1'b1;
                rf_rd_rs2      = 1'b1;
            end
            OP_FAA_FENCE: begin
                if (dec.func == FUNC_FENCE_GC) begin
                    is_nop = 1'b1;
                end else begin
                    is_multi_cycle = 1'b1;
                    rf_we          = 1'b1;
                    rf_rd_rs2      = 1'b1;
                end
            end

            // --- System ---
            OP_TRAP: begin
                is_system = 1'b1;
            end
            OP_ERET: begin
                is_system = 1'b1;
                is_jump   = 1'b1;
            end
            OP_SYS_INFO: begin
                is_system = 1'b1;
                rf_we     = 1'b1;
            end
            OP_HALT_NOP: begin
                is_system = 1'b1;
                if (dec.rd != 5'd0)
                    is_nop = 1'b1;  // NOP when rd != 0
            end

            // --- Prefetch (all no-ops) ---
            OP_PREFETCH_REF, OP_PREFETCH_FLD,
            OP_PREFETCH_CDR, OP_GATHER_PRE: begin
                is_nop = 1'b1;
            end

            // --- Region/bulk (reserved, treat as NOP) ---
            OP_ENQ_SCAN, OP_ENQ_COPY,
            OP_ENQ_FIXUP, OP_ENQ_COMPACT: begin
                is_nop = 1'b1;
            end

            default: begin
                is_system = 1'b1;  // unknown → will trap UNIMPLEMENTED
            end
        endcase
    end

endmodule
