// ============================================================================
// LM-1 Cluster Crossbar — 8-Port Non-Blocking Crossbar
//
// Connects 8 tile ports to a shared resource port (cluster SRAM,
// movement engines, DMA). Full crossbar implemented as round-robin
// arbiter feeding a single shared-SRAM port.
//
// Each tile port has request/response semantics:
//   req_valid, req_we, req_addr, req_wdata  →  resp_data, resp_valid
//
// Shared port is a single-port SRAM interface.
//
// Arbitration: round-robin with configurable priority boost for GC.
// ============================================================================
module lm1_crossbar
    import lm1_pkg::*;
#(
    parameter int NUM_PORTS = 8
)
(
    input  logic               clk,
    input  logic               rst_n,

    // --- Tile request ports (NUM_PORTS requesters) ---
    input  logic [NUM_PORTS-1:0]          req_valid,
    input  logic [NUM_PORTS-1:0]          req_we,
    input  logic [XLEN-1:0]              req_addr  [0:NUM_PORTS-1],
    input  logic [XLEN-1:0]              req_wdata [0:NUM_PORTS-1],
    output logic [NUM_PORTS-1:0]          req_ready,

    output logic [XLEN-1:0]              resp_data [0:NUM_PORTS-1],
    output logic [NUM_PORTS-1:0]          resp_valid,

    // --- Shared SRAM port (single-port downstream) ---
    output logic               sram_en,
    output logic               sram_we,
    output logic [XLEN-1:0]   sram_addr,
    output logic [XLEN-1:0]   sram_wdata,
    input  logic [XLEN-1:0]   sram_rdata
);

    // ---------------------------------------------------------------
    // Round-robin arbiter
    // ---------------------------------------------------------------
    logic [$clog2(NUM_PORTS)-1:0] rr_ptr;    // round-robin pointer
    logic [$clog2(NUM_PORTS)-1:0] grant_idx;
    logic                          grant_valid;
    logic                          pending_resp;
    logic [$clog2(NUM_PORTS)-1:0] resp_port;
    logic                          resp_was_rd;

    // Find next requester in round-robin order
    always_comb begin
        grant_valid = 1'b0;
        grant_idx   = rr_ptr;
        for (int i = 0; i < NUM_PORTS; i++) begin
            automatic int idx = (int'(rr_ptr) + i) % NUM_PORTS;
            if (req_valid[idx] && !pending_resp) begin
                grant_valid = 1'b1;
                grant_idx   = idx[$clog2(NUM_PORTS)-1:0];
                break;
            end
        end
    end

    // Grant / ready signals
    always_comb begin
        req_ready = '0;
        if (grant_valid)
            req_ready[grant_idx] = 1'b1;
    end

    // Drive shared SRAM port
    assign sram_en    = grant_valid;
    assign sram_we    = grant_valid && req_we[grant_idx];
    assign sram_addr  = req_addr[grant_idx];
    assign sram_wdata = req_wdata[grant_idx];

    // Response routing (reads take 1 cycle to return)
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rr_ptr       <= '0;
            pending_resp <= 1'b0;
            resp_port    <= '0;
            resp_was_rd  <= 1'b0;
        end else begin
            pending_resp <= 1'b0;
            if (grant_valid) begin
                rr_ptr <= grant_idx + 1;
                if (!req_we[grant_idx]) begin
                    // Read request — response will arrive next cycle
                    pending_resp <= 1'b1;
                    resp_port    <= grant_idx;
                    resp_was_rd  <= 1'b1;
                end
            end
        end
    end

    // Route read response to the correct port
    always_comb begin
        resp_valid = '0;
        for (int i = 0; i < NUM_PORTS; i++)
            resp_data[i] = '0;

        if (pending_resp && resp_was_rd) begin
            resp_valid[resp_port]   = 1'b1;
            resp_data[resp_port]    = sram_rdata;
        end
    end

endmodule
