// ============================================================================
// LM-1 Branch Evaluator
//
// Combinational unit that evaluates branch conditions for BR_COND.
// Also computes branch target address for both BR and BR_COND.
//
// Branch semantics (from ISA):
//   - BR:     unconditional, target = PC + (offset * 4)
//   - BR_COND: test register value against condition code
//     Conditions test a single register (no two-register branch conditions):
//       BR.T       (0): is_truthy(val)  — val != NIL && val != 0
//       BR.NIL     (1): val == NIL
//       BR.FIX.LT  (2): is_fixnum(val) && signed(val) < 0
//       BR.FIX.EQ  (3): val == 0
//       BR.FIX.GT  (4): is_fixnum(val) && signed(val) > 0 && val != 0
//       BR.EQ      (5): val == 0  (word-equal to zero)
//
// Offset is in WORDS (multiply by 4 for byte offset), relative to
// the CURRENT PC (not next_pc).
// ============================================================================
module lm1_branch
    import lm1_pkg::*;
(
    // Inputs
    input  logic [XLEN-1:0]        pc,           // current PC
    input  logic [XLEN-1:0]        reg_val,      // register value to test (rd field)
    input  logic [REG_IDX_W-1:0]   cond,         // condition code (rs1 field of BR_COND)
    input  logic [IMM16_W-1:0]     offset,       // 16-bit signed word offset
    input  logic                    is_br,        // unconditional branch
    input  logic                    is_br_cond,   // conditional branch

    // Outputs
    output logic [XLEN-1:0]        target,       // branch target address
    output logic                    taken         // branch is taken
);

    // ---------------------------------------------------------------
    // Target address calculation
    // target = PC + sign_extend(offset) * 4
    // ---------------------------------------------------------------
    logic signed [XLEN-1:0] offset_bytes;
    assign offset_bytes = {{(XLEN-IMM16_W){offset[IMM16_W-1]}}, offset} << 2;
    assign target       = pc + $unsigned(offset_bytes);

    // ---------------------------------------------------------------
    // Condition evaluation
    // ---------------------------------------------------------------
    logic cond_result;

    always_comb begin
        cond_result = 1'b0;

        case (cond)
            BR_T: begin
                // truthy: val != NIL && val != 0
                cond_result = (reg_val != VAL_NIL) && (reg_val != {XLEN{1'b0}});
            end
            BR_NIL: begin
                cond_result = (reg_val == VAL_NIL);
            end
            BR_FIX_LT: begin
                // fixnum (bit[0]==0) AND signed < 0
                cond_result = ~reg_val[0] && reg_val[XLEN-1];
            end
            BR_FIX_EQ: begin
                // val == 0 (tagged fixnum zero is 0x0)
                cond_result = (reg_val == {XLEN{1'b0}});
            end
            BR_FIX_GT: begin
                // fixnum AND signed > 0 AND not zero
                cond_result = ~reg_val[0] && ~reg_val[XLEN-1] &&
                              (reg_val != {XLEN{1'b0}});
            end
            BR_EQ_Z: begin
                // word-equal to zero
                cond_result = (reg_val == {XLEN{1'b0}});
            end
            default: begin
                cond_result = 1'b0;
            end
        endcase
    end

    // ---------------------------------------------------------------
    // Taken output
    // ---------------------------------------------------------------
    assign taken = is_br | (is_br_cond & cond_result);

endmodule
