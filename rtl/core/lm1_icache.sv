// ============================================================================
// LM-1 Instruction Cache — 8 KiB Direct-Mapped
//
// Cache parameters:
//   - Total size: 8 KiB = 8192 bytes = 128 cache lines
//   - Line size: 64 bytes = 8 × 64-bit words (256-bit fill)
//   - Sets: 128 (direct-mapped)
//   - Tag: addr[XLEN-1:13]  (bits above index+offset)
//   - Index: addr[12:6]     (7 bits → 128 sets)
//   - Offset: addr[5:0]     (6 bits → 64 bytes per line)
//
// On a hit (1 cycle):
//   - Return the requested 32-bit instruction from the cached line.
//
// On a miss:
//   - Assert fill_req to the memory subsystem.
//   - Stall until fill_valid returns with fill_data (one word at a time,
//     burst of 8 words, or however the memory delivers it).
//
// For simplicity (multi-cycle FSM core), this I-cache sits between
// the CPU's fetch logic and the tile SRAM, intercepting instruction
// fetch addresses.
//
// Interface is kept simple: the CPU requests a fetch at a given PC;
// the I-cache returns the 32-bit instruction or stalls.
// ============================================================================
module lm1_icache
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // CPU fetch interface
    input  logic               fetch_req,       // fetch request
    input  logic [XLEN-1:0]   fetch_addr,      // byte address of instruction
    output logic               fetch_valid,     // instruction is valid this cycle
    output logic [ILEN-1:0]   fetch_inst,      // 32-bit instruction word

    // SRAM / fill interface (downstream to tile SRAM)
    output logic               fill_req,        // request a cache line fill
    output logic [XLEN-1:0]   fill_addr,       // base address of line to fill
    input  logic               fill_valid,      // fill data word is valid
    input  logic [XLEN-1:0]   fill_data,       // 64-bit fill data word
    input  logic               fill_done        // last word of fill burst
);

    // Cache geometry
    localparam int LINE_WORDS    = 8;          // 8 × 8 = 64 bytes per line
    localparam int NUM_SETS      = 128;        // 128 lines = 8 KiB
    localparam int OFFSET_BITS   = 6;          // log2(64)
    localparam int INDEX_BITS    = 7;          // log2(128)
    localparam int TAG_BITS      = XLEN - INDEX_BITS - OFFSET_BITS;
    localparam int WORD_OFF_BITS = 3;          // byte offset within 64-bit word

    // Tag + valid storage
    logic [TAG_BITS-1:0]  tag_store [0:NUM_SETS-1];
    logic                 valid_store [0:NUM_SETS-1];

    // Data storage — 128 sets × 8 words × 64 bits = 8 KiB
    logic [XLEN-1:0]     data_store [0:NUM_SETS-1][0:LINE_WORDS-1];

    // Address decomposition
    logic [TAG_BITS-1:0]  req_tag;
    logic [INDEX_BITS-1:0] req_index;
    logic [2:0]            req_word;      // which 64-bit word in the line
    logic                  req_half;      // which 32-bit half of that word

    assign req_tag   = fetch_addr[XLEN-1:INDEX_BITS+OFFSET_BITS];
    assign req_index = fetch_addr[INDEX_BITS+OFFSET_BITS-1:OFFSET_BITS];
    assign req_word  = fetch_addr[OFFSET_BITS-1:3];
    assign req_half  = fetch_addr[2];

    // Hit detection
    logic cache_hit;
    assign cache_hit = valid_store[req_index] &&
                       (tag_store[req_index] == req_tag);

    // Fill state machine
    typedef enum logic [1:0] {
        IC_IDLE,
        IC_FILL,
        IC_DONE
    } ic_state_t;

    ic_state_t   ic_state;
    logic [2:0]  fill_word_idx;   // which word of the line we're filling
    logic [INDEX_BITS-1:0] fill_index;
    logic [TAG_BITS-1:0]   fill_tag;

    // Output mux
    always_comb begin
        fetch_valid = 1'b0;
        fetch_inst  = '0;
        fill_req    = 1'b0;
        fill_addr   = '0;

        case (ic_state)
            IC_IDLE: begin
                if (fetch_req) begin
                    if (cache_hit) begin
                        fetch_valid = 1'b1;
                        // Extract 32-bit instruction from cached line
                        if (req_half)
                            fetch_inst = data_store[req_index][req_word][63:32];
                        else
                            fetch_inst = data_store[req_index][req_word][31:0];
                    end else begin
                        // Miss — request fill
                        fill_req  = 1'b1;
                        fill_addr = {fetch_addr[XLEN-1:OFFSET_BITS], {OFFSET_BITS{1'b0}}};
                    end
                end
            end

            IC_FILL: begin
                // Keep fill request asserted for ongoing fill
                fill_req  = 1'b1;
                fill_addr = {fill_tag, fill_index, {OFFSET_BITS{1'b0}}};
            end

            IC_DONE: begin
                // Fill just completed — re-read and return
                fetch_valid = 1'b1;
                if (req_half)
                    fetch_inst = data_store[req_index][req_word][63:32];
                else
                    fetch_inst = data_store[req_index][req_word][31:0];
            end

            default: ;
        endcase
    end

    // Fill state machine — sequential
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ic_state      <= IC_IDLE;
            fill_word_idx <= '0;
            fill_index    <= '0;
            fill_tag      <= '0;
            for (int i = 0; i < NUM_SETS; i++) begin
                valid_store[i] = 1'b0;   // blocking OK in reset (Verilator compat)
                tag_store[i]   = '0;
            end
        end else begin
            case (ic_state)
                IC_IDLE: begin
                    if (fetch_req && !cache_hit) begin
                        // Start fill
                        fill_index    <= req_index;
                        fill_tag      <= req_tag;
                        fill_word_idx <= 3'd0;
                        valid_store[req_index] <= 1'b0;  // invalidate during fill
                        ic_state      <= IC_FILL;
                    end
                end

                IC_FILL: begin
                    if (fill_valid) begin
                        data_store[fill_index][fill_word_idx] <= fill_data;
                        if (fill_done || fill_word_idx == 3'd7) begin
                            // Fill complete
                            tag_store[fill_index]   <= fill_tag;
                            valid_store[fill_index] <= 1'b1;
                            ic_state <= IC_DONE;
                        end else begin
                            fill_word_idx <= fill_word_idx + 3'd1;
                        end
                    end
                end

                IC_DONE: begin
                    ic_state <= IC_IDLE;
                end

                default: ic_state <= IC_IDLE;
            endcase
        end
    end

endmodule
