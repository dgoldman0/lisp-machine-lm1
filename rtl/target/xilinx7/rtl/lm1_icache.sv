// ============================================================================
// LM-1 Instruction Cache — FPGA (Xilinx 7-Series) Target Override
//
// Same port interface as the pure RTL version (rtl/core/lm1_icache.sv).
// Changes for FPGA synthesis:
//
//   1. data_store uses (* ram_style = "block" *) with SYNCHRONOUS reads
//      so Yosys infers Xilinx Block RAM (RAMB36E1) instead of expanding
//      a 1024:1 async-read mux tree into $shiftx cells.
//      BRAM cost: 8 KiB data → 2 × RAMB36E1 (36 Kb each, 1024 × 36 mode).
//
//   2. tag_store and valid_store use (* ram_style = "distributed" *) for
//      LUTRAM — async reads needed for same-cycle hit detection.
//      LUTRAM cost: 128 × 51 tags → ~128 LUTs; 128 × 1 valid → trivial.
//
//   3. FSM gains IC_HIT (1-cycle BRAM read latency on cache hits) and
//      IC_SETTLE (1-cycle gap after last fill write to avoid write-read
//      collision on BRAM).  Hit path: 2 cycles vs 1.  Fill path: +1 cycle.
//      Acceptable for a multi-cycle FSM core.
//
// The 2D data_store[128][8] is flattened to 1D [1024] so Yosys sees a
// simple 1024×64 single-port-write / single-port-read memory.
// ============================================================================
module lm1_icache
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // CPU fetch interface
    input  logic               fetch_req,
    input  logic [XLEN-1:0]   fetch_addr,
    output logic               fetch_valid,
    output logic [ILEN-1:0]   fetch_inst,

    // SRAM / fill interface (downstream to tile SRAM)
    output logic               fill_req,
    output logic [XLEN-1:0]   fill_addr,
    input  logic               fill_valid,
    input  logic [XLEN-1:0]   fill_data,
    input  logic               fill_done
);

    // ----------------------------------------------------------------
    // Cache geometry (identical to pure RTL)
    // ----------------------------------------------------------------
    localparam int LINE_WORDS    = 8;
    localparam int NUM_SETS      = 128;
    localparam int OFFSET_BITS   = 6;
    localparam int INDEX_BITS    = 7;
    localparam int TAG_BITS      = XLEN - INDEX_BITS - OFFSET_BITS;
    localparam int WORD_OFF_BITS = 3;
    localparam int BRAM_ADDR_W   = INDEX_BITS + WORD_OFF_BITS;  // 10 bits

    // ----------------------------------------------------------------
    // Tag + valid — distributed RAM (LUTRAM, async read for hit detect)
    // ----------------------------------------------------------------
    (* ram_style = "distributed" *)
    logic [TAG_BITS-1:0]  tag_store [0:NUM_SETS-1];

    (* ram_style = "distributed" *)
    logic                 valid_store [0:NUM_SETS-1];

    // ----------------------------------------------------------------
    // Data storage — Block RAM (sync read, 1024 × 64-bit = 8 KiB)
    // Flattened from [128][8] to [1024].
    // ----------------------------------------------------------------
    (* ram_style = "block" *)
    logic [XLEN-1:0]     data_store [0:NUM_SETS*LINE_WORDS-1];

    // BRAM synchronous read port
    logic [BRAM_ADDR_W-1:0] bram_rd_addr;
    logic [XLEN-1:0]        bram_rd_data;

    // BRAM write port
    logic                    bram_wr_en;
    logic [BRAM_ADDR_W-1:0]  bram_wr_addr;
    logic [XLEN-1:0]         bram_wr_data;

    // Simple dual-port BRAM inference pattern:
    //   Port A — write only (fill path)
    //   Port B — sync read  (hit / done path)
    always_ff @(posedge clk) begin
        if (bram_wr_en)
            data_store[bram_wr_addr] <= bram_wr_data;
    end

    always_ff @(posedge clk) begin
        bram_rd_data <= data_store[bram_rd_addr];
    end

    // ----------------------------------------------------------------
    // Address decomposition
    // ----------------------------------------------------------------
    logic [TAG_BITS-1:0]   req_tag;
    logic [INDEX_BITS-1:0] req_index;
    logic [2:0]            req_word;
    logic                  req_half;

    assign req_tag   = fetch_addr[XLEN-1:INDEX_BITS+OFFSET_BITS];
    assign req_index = fetch_addr[INDEX_BITS+OFFSET_BITS-1:OFFSET_BITS];
    assign req_word  = fetch_addr[OFFSET_BITS-1:3];
    assign req_half  = fetch_addr[2];

    // BRAM read address — always driven from fetch_addr decomposition
    assign bram_rd_addr = {req_index, req_word};

    // ----------------------------------------------------------------
    // Hit detection (uses LUTRAM tag/valid — combinational)
    // ----------------------------------------------------------------
    logic cache_hit;
    assign cache_hit = valid_store[req_index] &&
                       (tag_store[req_index] == req_tag);

    // ----------------------------------------------------------------
    // FSM
    // ----------------------------------------------------------------
    typedef enum logic [2:0] {
        IC_IDLE,
        IC_HIT,       // 1-cycle BRAM read latency on cache hit
        IC_FILL,      // receiving fill burst from memory
        IC_SETTLE,    // 1-cycle settle after last fill write
        IC_DONE       // BRAM data valid — return instruction after fill
    } ic_state_t;

    ic_state_t   ic_state;
    logic [2:0]  fill_word_idx;
    logic [INDEX_BITS-1:0] fill_index;
    logic [TAG_BITS-1:0]   fill_tag;

    // Latch req_half for use in IC_HIT / IC_DONE (fetch_addr is stable,
    // but latching makes timing intent explicit).
    logic half_r;

    // ----------------------------------------------------------------
    // Output mux (combinational)
    // ----------------------------------------------------------------
    always_comb begin
        fetch_valid = 1'b0;
        fetch_inst  = '0;
        fill_req    = 1'b0;
        fill_addr   = '0;

        case (ic_state)
            IC_IDLE: begin
                if (fetch_req && !cache_hit) begin
                    // Miss — request fill
                    fill_req  = 1'b1;
                    fill_addr = {fetch_addr[XLEN-1:OFFSET_BITS],
                                 {OFFSET_BITS{1'b0}}};
                end
                // Hit path: no output yet — wait for IC_HIT
            end

            IC_HIT: begin
                // BRAM data is now valid (read was registered last posedge)
                fetch_valid = 1'b1;
                if (half_r)
                    fetch_inst = bram_rd_data[63:32];
                else
                    fetch_inst = bram_rd_data[31:0];
            end

            IC_FILL: begin
                fill_req  = 1'b1;
                fill_addr = {fill_tag, fill_index, {OFFSET_BITS{1'b0}}};
            end

            IC_SETTLE: begin
                // 1-cycle gap — BRAM read addr is being sampled this posedge
            end

            IC_DONE: begin
                // BRAM data valid after settle cycle
                fetch_valid = 1'b1;
                if (half_r)
                    fetch_inst = bram_rd_data[63:32];
                else
                    fetch_inst = bram_rd_data[31:0];
            end

            default: ;
        endcase
    end

    // ----------------------------------------------------------------
    // BRAM write control (active only during fill)
    // ----------------------------------------------------------------
    assign bram_wr_en   = (ic_state == IC_FILL) && fill_valid;
    assign bram_wr_addr = {fill_index, fill_word_idx};
    assign bram_wr_data = fill_data;

    // ----------------------------------------------------------------
    // Sequential FSM
    // ----------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ic_state      <= IC_IDLE;
            fill_word_idx <= '0;
            fill_index    <= '0;
            fill_tag      <= '0;
            half_r        <= 1'b0;
            for (int i = 0; i < NUM_SETS; i++) begin
                valid_store[i] = 1'b0;
                tag_store[i]   = '0;
            end
        end else begin
            case (ic_state)
                IC_IDLE: begin
                    if (fetch_req) begin
                        half_r <= req_half;
                        if (cache_hit) begin
                            // Hit — BRAM read addr registered this posedge,
                            // data available next cycle in IC_HIT.
                            ic_state <= IC_HIT;
                        end else begin
                            // Miss — start fill
                            fill_index    <= req_index;
                            fill_tag      <= req_tag;
                            fill_word_idx <= 3'd0;
                            valid_store[req_index] <= 1'b0;
                            ic_state      <= IC_FILL;
                        end
                    end
                end

                IC_HIT: begin
                    // Data delivered — back to idle
                    ic_state <= IC_IDLE;
                end

                IC_FILL: begin
                    if (fill_valid) begin
                        // bram_wr_en is asserted combinationally above
                        if (fill_done || fill_word_idx == 3'd7) begin
                            tag_store[fill_index]   <= fill_tag;
                            valid_store[fill_index] <= 1'b1;
                            ic_state <= IC_SETTLE;
                        end else begin
                            fill_word_idx <= fill_word_idx + 3'd1;
                        end
                    end
                end

                IC_SETTLE: begin
                    // 1-cycle settle: BRAM read addr {req_index, req_word}
                    // is sampled this posedge.  Data out next cycle.
                    ic_state <= IC_DONE;
                end

                IC_DONE: begin
                    // Data delivered — back to idle
                    ic_state <= IC_IDLE;
                end

                default: ic_state <= IC_IDLE;
            endcase
        end
    end

endmodule
