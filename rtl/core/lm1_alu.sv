// ============================================================================
// LM-1 ALU
//
// Combinational arithmetic and logic unit for the LM-1 processor.
// Handles both raw (untagged) 64-bit operations and tagged fixnum
// arithmetic, plus type-test and compare operations.
//
// Operations are selected by (alu_op, alu_func).
//
// Multi-cycle operations (MUL, DIV, MOD) use a simple iterative approach
// with valid/ready handshaking.  Single-cycle operations complete in the
// same cycle (result_valid is asserted combinationally).
// ============================================================================
module lm1_alu
    import lm1_pkg::*;
(
    input  logic                clk,
    input  logic                rst_n,

    // Operation select
    input  opcode_t             alu_op,       // opcode for context
    input  logic [FUNC_W-1:0]   alu_func,     // sub-function
    input  logic [XLEN-1:0]     operand_a,    // rs1 value (or rd value for some)
    input  logic [XLEN-1:0]     operand_b,    // rs2 value or immediate
    input  logic                start,        // pulse high to begin multi-cycle op

    // Results
    output logic [XLEN-1:0]     result,       // ALU result
    output logic                result_valid,  // result is ready
    output logic                trap_raise,    // operation triggers a trap
    output logic [7:0]          trap_code      // which trap
);

    // ---------------------------------------------------------------
    // Internal signals
    // ---------------------------------------------------------------
    logic [XLEN-1:0] add_result, sub_result;
    logic add_overflow, sub_overflow;

    // Overflow detection for tagged fixnum add/sub
    // Overflow when: both operands same sign, result different sign
    logic a_sign, b_sign, r_sign_add, r_sign_sub;

    assign a_sign     = operand_a[XLEN-1];
    assign b_sign     = operand_b[XLEN-1];
    assign add_result = operand_a + operand_b;
    assign sub_result = operand_a - operand_b;
    assign r_sign_add = add_result[XLEN-1];
    assign r_sign_sub = sub_result[XLEN-1];

    // Signed overflow: same-sign inputs produce different-sign result
    assign add_overflow = (a_sign == b_sign) && (a_sign != r_sign_add);
    assign sub_overflow = (a_sign != b_sign) && (b_sign == r_sign_sub);

    // ---------------------------------------------------------------
    // Multi-cycle divider state machine
    // ---------------------------------------------------------------
    typedef enum logic [1:0] {
        DIV_IDLE,
        DIV_RUNNING,
        DIV_DONE
    } div_state_t;

    div_state_t          div_state, div_state_next;
    logic [XLEN-1:0]     div_quotient, div_remainder;
    logic [XLEN-1:0]     div_dividend, div_divisor;
    logic [6:0]          div_count;
    logic                div_busy;
    logic                div_by_zero;

    // Iterative unsigned divider (1 bit per cycle, 64 cycles)
    logic [XLEN-1:0]     div_q, div_r;
    logic [XLEN-1:0]     div_d;  // divisor register

    assign div_busy = (div_state != DIV_IDLE);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_state    <= DIV_IDLE;
            div_quotient <= '0;
            div_remainder<= '0;
            div_q        <= '0;
            div_r        <= '0;
            div_d        <= '0;
            div_count    <= '0;
            div_by_zero  <= 1'b0;
        end else begin
            case (div_state)
                DIV_IDLE: begin
                    if (start && (alu_op == OP_ARITH_RAW) &&
                        (alu_func == FUNC_DIV || alu_func == FUNC_MOD)) begin
                        if (operand_b == '0) begin
                            div_by_zero <= 1'b1;
                            div_state   <= DIV_DONE;
                        end else begin
                            div_by_zero <= 1'b0;
                            div_q       <= '0;
                            div_r       <= '0;
                            div_d       <= operand_b;
                            div_dividend<= operand_a;
                            div_divisor <= operand_b;
                            div_count   <= 7'd63;
                            div_state   <= DIV_RUNNING;
                        end
                    end
                end

                DIV_RUNNING: begin
                    // Shift-and-subtract: process one bit per cycle (MSB first)
                    logic [XLEN-1:0] r_shifted;
                    r_shifted = {div_r[XLEN-2:0], div_dividend[div_count[5:0]]};
                    if (r_shifted >= div_d) begin
                        div_r <= r_shifted - div_d;
                        div_q[div_count[5:0]] <= 1'b1;
                    end else begin
                        div_r <= r_shifted;
                        div_q[div_count[5:0]] <= 1'b0;
                    end

                    if (div_count == 7'd0) begin
                        div_state <= DIV_DONE;
                    end else begin
                        div_count <= div_count - 7'd1;
                    end
                end

                DIV_DONE: begin
                    div_quotient  <= div_q;
                    div_remainder <= div_r;
                    div_state     <= DIV_IDLE;
                end

                default: div_state <= DIV_IDLE;
            endcase
        end
    end

    // ---------------------------------------------------------------
    // Multi-cycle multiplier
    //
    // For simplicity, use the synthesis tool's * operator — most FPGAs
    // have DSP blocks that handle 64-bit multiply in 1-3 cycles.
    // We'll treat it as single-cycle here; the control FSM can add
    // pipeline stages if needed for timing closure.
    // ---------------------------------------------------------------
    logic [XLEN-1:0] mul_result;
    assign mul_result = operand_a * operand_b;  // lower 64 bits

    // ---------------------------------------------------------------
    // Tagged fixnum multiply
    // untag(a) * b → result  (b stays tagged, a is untagged)
    // ---------------------------------------------------------------
    logic signed [XLEN-1:0] fix_a_untagged;
    logic [XLEN-1:0]        fix_mul_result;
    logic                   fix_mul_overflow;

    assign fix_a_untagged = $signed(operand_a) >>> 1;
    assign fix_mul_result = fix_a_untagged * operand_b;

    // Overflow check: untag result, retag, compare
    logic signed [XLEN-1:0] fix_mul_check;
    assign fix_mul_check = $signed(fix_mul_result) >>> 1;

    // If tag_fixnum(untag_fixnum(result)) != result → overflow
    // tag_fixnum(x) = x << 1, untag_fixnum(w) = w >>> 1
    // So check: (fix_mul_result >>> 1) << 1 == fix_mul_result
    // i.e., bit[0] must be 0 AND no sign-extension loss
    assign fix_mul_overflow = (fix_mul_result[0] != 1'b0) ||
                              ({fix_mul_check[XLEN-2:0], 1'b0} != fix_mul_result);

    // ---------------------------------------------------------------
    // Tagged fixnum divide
    // untag(a) / untag(b), truncate toward zero, retag
    // ---------------------------------------------------------------
    // This is handled by the multi-cycle divider; the control FSM
    // extracts untagged values before feeding the divider.

    // ---------------------------------------------------------------
    // Main result mux (combinational for single-cycle ops)
    // ---------------------------------------------------------------
    always_comb begin
        result       = '0;
        result_valid = 1'b0;
        trap_raise   = 1'b0;
        trap_code    = '0;

        case (alu_op)
            // === Raw 64-bit arithmetic ===
            OP_ARITH_RAW: begin
                case (alu_func)
                    FUNC_ADD: begin
                        result       = add_result;
                        result_valid = 1'b1;
                    end
                    FUNC_SUB: begin
                        result       = sub_result;
                        result_valid = 1'b1;
                    end
                    FUNC_MUL: begin
                        result       = mul_result;
                        result_valid = 1'b1;
                    end
                    FUNC_DIV: begin
                        if (div_state == DIV_DONE) begin
                            if (div_by_zero) begin
                                trap_raise   = 1'b1;
                                trap_code    = TRAP_DIVIDE_BY_ZERO;
                                result_valid = 1'b1;
                            end else begin
                                result       = div_quotient;
                                result_valid = 1'b1;
                            end
                        end
                    end
                    FUNC_MOD: begin
                        if (div_state == DIV_DONE) begin
                            if (div_by_zero) begin
                                trap_raise   = 1'b1;
                                trap_code    = TRAP_DIVIDE_BY_ZERO;
                                result_valid = 1'b1;
                            end else begin
                                result       = div_remainder;
                                result_valid = 1'b1;
                            end
                        end
                    end
                    default: begin
                        result_valid = 1'b1;
                    end
                endcase
            end

            // === Bitwise ===
            OP_BITWISE: begin
                result_valid = 1'b1;
                case (alu_func)
                    FUNC_AND: result = operand_a & operand_b;
                    FUNC_OR:  result = operand_a | operand_b;
                    FUNC_XOR: result = operand_a ^ operand_b;
                    FUNC_SHL: result = operand_a << operand_b[5:0];
                    FUNC_SHR: result = operand_a >> operand_b[5:0];
                    FUNC_ASR: result = $unsigned($signed(operand_a) >>> operand_b[5:0]);
                    FUNC_NOT: result = ~operand_a;
                    default:  result = '0;
                endcase
            end

            // === Tagged fixnum arithmetic ===
            OP_ARITH_FIX: begin
                // Both operands must be fixnums (bit[0] == 0)
                if (operand_a[0] || operand_b[0]) begin
                    trap_raise   = 1'b1;
                    trap_code    = TRAP_NOT_FIXNUM;
                    result_valid = 1'b1;
                end else begin
                    case (alu_func)
                        FUNC_ADD_FIX: begin
                            result = add_result;
                            if (add_overflow) begin
                                trap_raise = 1'b1;
                                trap_code  = TRAP_FIXNUM_OVERFLOW;
                            end
                            result_valid = 1'b1;
                        end
                        FUNC_SUB_FIX: begin
                            result = sub_result;
                            if (sub_overflow) begin
                                trap_raise = 1'b1;
                                trap_code  = TRAP_FIXNUM_OVERFLOW;
                            end
                            result_valid = 1'b1;
                        end
                        FUNC_MUL_FIX: begin
                            result = fix_mul_result;
                            if (fix_mul_overflow) begin
                                trap_raise = 1'b1;
                                trap_code  = TRAP_FIXNUM_OVERFLOW;
                            end
                            result_valid = 1'b1;
                        end
                        FUNC_DIV_FIX: begin
                            // Handled by multi-cycle divider path
                            // Control FSM manages untag → divide → retag
                            if (operand_b == '0) begin
                                trap_raise   = 1'b1;
                                trap_code    = TRAP_DIVIDE_BY_ZERO;
                                result_valid = 1'b1;
                            end
                        end
                        default: begin
                            result_valid = 1'b1;
                        end
                    endcase
                end
            end

            // === ADD.FIX.IMM ===
            OP_ADD_FIX_IMM: begin
                if (operand_a[0]) begin
                    trap_raise   = 1'b1;
                    trap_code    = TRAP_NOT_FIXNUM;
                    result_valid = 1'b1;
                end else begin
                    result = add_result;
                    if (add_overflow) begin
                        trap_raise = 1'b1;
                        trap_code  = TRAP_FIXNUM_OVERFLOW;
                    end
                    result_valid = 1'b1;
                end
            end

            // === CMP.TAGGED ===
            OP_CMP_TAGGED: begin
                result_valid = 1'b1;
                case (alu_func)
                    FUNC_CMP: begin
                        if (is_fixnum(operand_a) && is_fixnum(operand_b)) begin
                            // Signed fixnum comparison → -1, 0, +1 as tagged
                            if ($signed(operand_a) < $signed(operand_b))
                                result = tag_fixnum(64'hFFFF_FFFF_FFFF_FFFF); // -1
                            else if (operand_a == operand_b)
                                result = tag_fixnum(64'd0);                    // 0
                            else
                                result = tag_fixnum(64'd1);                    // +1
                        end else if (operand_a[2:0] == operand_b[2:0]) begin
                            // Same primary tag → identity comparison
                            result = (operand_a == operand_b) ?
                                     tag_fixnum(64'd0) : tag_fixnum(64'd1);
                        end else begin
                            trap_raise = 1'b1;
                            trap_code  = TRAP_TYPE_MISMATCH;
                        end
                    end
                    FUNC_EQ: begin
                        result = (operand_a == operand_b) ? VAL_T : VAL_NIL;
                    end
                    default: begin
                        result = '0;
                    end
                endcase
            end

            // === TST (type test) ===
            OP_TST: begin
                result_valid = 1'b1;
                // operand_a = register value, operand_b[2:0] = tag constant
                case (operand_b[2:0])
                    TAG_CONST_FIXNUM:  result = is_fixnum(operand_a)              ? VAL_T : VAL_NIL;
                    TAG_CONST_REF:     result = ((operand_a[1:0] == 2'b01))       ? VAL_T : VAL_NIL;
                    TAG_CONST_CONS:    result = ((operand_a[2:0] == 3'b011))      ? VAL_T : VAL_NIL;
                    TAG_CONST_SPECIAL: result = ((operand_a[2:0] == 3'b101))      ? VAL_T : VAL_NIL;
                    TAG_CONST_NIL:     result = (operand_a == VAL_NIL)            ? VAL_T : VAL_NIL;
                    TAG_CONST_CHAR:    result = (operand_a[7:0] == CHAR_TAG_BYTE) ? VAL_T : VAL_NIL;
                    TAG_CONST_SFLOAT:  result = (operand_a[7:0] == SFLOAT_TAG_BYTE) ? VAL_T : VAL_NIL;
                    TAG_CONST_HEADER:  result = ((operand_a[2:0] == 3'b111))      ? VAL_T : VAL_NIL;
                    default:           result = VAL_NIL;
                endcase
            end

            // === LI (load immediate — sign-extended imm16) ===
            OP_LI: begin
                result       = operand_b;  // control feeds sext16(imm16) as operand_b
                result_valid = 1'b1;
            end

            // === LUI (load upper immediate — imm16 << 16) ===
            OP_LUI: begin
                result       = {32'b0, operand_b[15:0], 16'b0};
                result_valid = 1'b1;
            end

            default: begin
                // For non-ALU opcodes, output zero
                result       = '0;
                result_valid = 1'b1;
            end
        endcase
    end

endmodule
