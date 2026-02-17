// ============================================================================
// LM-1 Load/Store Unit
//
// Handles all memory access operations for the CPU:
//   - Raw loads/stores (LDR/STR): 64-bit word at computed address
//   - Tagged field access (LD/ST/ST.WB): ref_address + (field+1)*8
//   - Car/cdr access (LD.CAR/CDR, ST.CAR/CDR): ref_address + 8/16
//   - Stack push/pop (PUSH/POP): update SP, access stack
//   - Instruction fetch: 32-bit from PC
//   - LI32: fetch 32-bit immediate from PC+4
//
// Memory interface:
//   Single 64-bit port to main memory (SRAM).  The CPU uses a
//   multi-cycle FSM, so instruction fetch and data access never
//   overlap.  The LSU computes addresses and drives the memory bus.
//
// Address is always 8-byte aligned for 64-bit access.
// For 32-bit instruction fetch, the full 64-bit word is read and
// the appropriate half is selected by addr[2].
// ============================================================================
module lm1_lsu
    import lm1_pkg::*;
(
    input  logic                clk,
    input  logic                rst_n,

    // Request from control unit
    input  logic                req_valid,    // request is valid
    input  logic [3:0]          req_op,       // operation type (see LSU_OP_*)
    input  logic [XLEN-1:0]     req_addr,     // byte address (pre-computed)
    input  logic [XLEN-1:0]     req_wdata,    // data to store
    output logic                req_ready,    // LSU accepted the request

    // Response to control unit
    output logic                resp_valid,   // response ready
    output logic [XLEN-1:0]     resp_rdata,   // loaded data (64-bit)
    output logic [ILEN-1:0]     resp_inst,    // fetched instruction (32-bit)

    // Memory port (directly to SRAM)
    output logic                mem_en,
    output logic                mem_we,
    output logic [XLEN/8-1:0]   mem_be,       // byte enables
    output logic [XLEN-1:0]     mem_addr,     // word address (addr >> 3)
    output logic [XLEN-1:0]     mem_wdata,
    input  logic [XLEN-1:0]     mem_rdata
);

    // ---------------------------------------------------------------
    // LSU operation codes
    // ---------------------------------------------------------------
    localparam logic [3:0] LSU_OP_NONE       = 4'd0;
    localparam logic [3:0] LSU_OP_IFETCH     = 4'd1;  // instruction fetch (32-bit)
    localparam logic [3:0] LSU_OP_LOAD64     = 4'd2;  // 64-bit word load
    localparam logic [3:0] LSU_OP_STORE64    = 4'd3;  // 64-bit word store
    localparam logic [3:0] LSU_OP_LOAD32     = 4'd4;  // 32-bit load (for LI32)
    localparam logic [3:0] LSU_OP_LOAD_BYTE  = 4'd5;  // byte load (zero-extend)
    localparam logic [3:0] LSU_OP_STORE_BYTE = 4'd6;  // byte store
    localparam logic [3:0] LSU_OP_LOAD_HALF  = 4'd7;  // halfword load (zero-extend)
    localparam logic [3:0] LSU_OP_STORE_HALF = 4'd8;  // halfword store
    localparam logic [3:0] LSU_OP_LOAD_WORD  = 4'd9;  // 32-bit word load (zero-extend)
    localparam logic [3:0] LSU_OP_STORE_WORD = 4'd10;  // 32-bit word store

    // ---------------------------------------------------------------
    // State machine
    // ---------------------------------------------------------------
    typedef enum logic [1:0] {
        LSU_IDLE,
        LSU_WAIT_RD,    // waiting for read data (1-cycle SRAM latency)
        LSU_DONE
    } lsu_state_t;

    lsu_state_t state, state_next;

    logic [3:0]      op_reg;
    logic [XLEN-1:0] addr_reg;
    logic             addr_bit2;  // addr[2] for 32-bit half selection

    // ---------------------------------------------------------------
    // State register
    // ---------------------------------------------------------------
    logic [2:0]      addr_low;  // addr[2:0] for sub-word alignment

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state    <= LSU_IDLE;
            op_reg   <= LSU_OP_NONE;
            addr_reg <= '0;
            addr_bit2<= 1'b0;
            addr_low <= 3'b0;
        end else begin
            state <= state_next;
            if (state == LSU_IDLE && req_valid) begin
                op_reg    <= req_op;
                addr_reg  <= req_addr;
                addr_bit2 <= req_addr[2];
                addr_low  <= req_addr[2:0];
            end
        end
    end

    // ---------------------------------------------------------------
    // Next-state logic and memory control
    // ---------------------------------------------------------------
    always_comb begin
        state_next = state;
        mem_en     = 1'b0;
        mem_we     = 1'b0;
        mem_be     = '0;
        mem_addr   = '0;
        mem_wdata  = '0;
        req_ready  = 1'b0;
        resp_valid = 1'b0;
        resp_rdata = '0;
        resp_inst  = '0;

        case (state)
            LSU_IDLE: begin
                req_ready = 1'b1;
                if (req_valid) begin
                    case (req_op)
                        LSU_OP_IFETCH, LSU_OP_LOAD64, LSU_OP_LOAD32,
                        LSU_OP_LOAD_BYTE, LSU_OP_LOAD_HALF, LSU_OP_LOAD_WORD: begin
                            // All loads: issue read to SRAM (8-byte aligned)
                            mem_en   = 1'b1;
                            mem_addr = {3'b0, req_addr[XLEN-1:3]};  // word address
                            state_next = LSU_WAIT_RD;
                        end

                        LSU_OP_STORE64: begin
                            // Full 64-bit store
                            mem_en    = 1'b1;
                            mem_we    = 1'b1;
                            mem_be    = 8'hFF;
                            mem_addr  = {3'b0, req_addr[XLEN-1:3]};
                            mem_wdata = req_wdata;
                            state_next = LSU_DONE;
                        end

                        LSU_OP_STORE_BYTE: begin
                            // Byte store: single byte enable
                            mem_en    = 1'b1;
                            mem_we    = 1'b1;
                            mem_be    = 8'h01 << req_addr[2:0];
                            mem_addr  = {3'b0, req_addr[XLEN-1:3]};
                            // Replicate byte across all lanes
                            mem_wdata = {8{req_wdata[7:0]}};
                            state_next = LSU_DONE;
                        end

                        LSU_OP_STORE_HALF: begin
                            // Halfword store: 2 byte enables, aligned to 2-byte boundary
                            mem_en    = 1'b1;
                            mem_we    = 1'b1;
                            mem_be    = 8'h03 << {req_addr[2:1], 1'b0};
                            mem_addr  = {3'b0, req_addr[XLEN-1:3]};
                            mem_wdata = {4{req_wdata[15:0]}};
                            state_next = LSU_DONE;
                        end

                        LSU_OP_STORE_WORD: begin
                            // 32-bit word store: 4 byte enables
                            mem_en    = 1'b1;
                            mem_we    = 1'b1;
                            mem_be    = req_addr[2] ? 8'hF0 : 8'h0F;
                            mem_addr  = {3'b0, req_addr[XLEN-1:3]};
                            mem_wdata = {2{req_wdata[31:0]}};
                            state_next = LSU_DONE;
                        end

                        default: begin
                            state_next = LSU_IDLE;
                        end
                    endcase
                end
            end

            LSU_WAIT_RD: begin
                // SRAM read data is available this cycle (1-cycle latency)
                resp_valid = 1'b1;

                case (op_reg)
                    LSU_OP_IFETCH: begin
                        // Select 32-bit half based on addr[2]
                        if (addr_bit2)
                            resp_inst = mem_rdata[63:32];
                        else
                            resp_inst = mem_rdata[31:0];
                        resp_rdata = mem_rdata;
                    end

                    LSU_OP_LOAD64: begin
                        resp_rdata = mem_rdata;
                    end

                    LSU_OP_LOAD32: begin
                        // 32-bit load, zero-extended to 64
                        if (addr_bit2)
                            resp_rdata = {32'b0, mem_rdata[63:32]};
                        else
                            resp_rdata = {32'b0, mem_rdata[31:0]};
                    end

                    LSU_OP_LOAD_BYTE: begin
                        // Extract byte at addr[2:0], zero-extend to 64
                        resp_rdata = {56'b0, mem_rdata[addr_low*8 +: 8]};
                    end

                    LSU_OP_LOAD_HALF: begin
                        // Extract halfword at addr[2:1]*16, zero-extend to 64
                        resp_rdata = {48'b0, mem_rdata[{addr_low[2:1], 1'b0}*8 +: 16]};
                    end

                    LSU_OP_LOAD_WORD: begin
                        // Extract 32-bit word at addr[2]*32, zero-extend to 64
                        if (addr_bit2)
                            resp_rdata = {32'b0, mem_rdata[63:32]};
                        else
                            resp_rdata = {32'b0, mem_rdata[31:0]};
                    end

                    default: begin
                        resp_rdata = mem_rdata;
                    end
                endcase

                state_next = LSU_IDLE;
            end

            LSU_DONE: begin
                // Store completed
                resp_valid = 1'b1;
                state_next = LSU_IDLE;
            end

            default: begin
                state_next = LSU_IDLE;
            end
        endcase
    end

endmodule
