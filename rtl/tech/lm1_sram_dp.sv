// ============================================================================
// LM-1 True Dual-Port Synchronous SRAM
//
// Technology-agnostic behavioral model.  Infers true dual-port block RAM
// on FPGA (Xilinx/Intel).  For ASIC, replace with compiled dual-port macro.
//
// Both ports support independent read and write with byte enables.
// Simultaneous write to the same address from both ports is undefined.
// ============================================================================
module lm1_sram_dp #(
    parameter int DEPTH      = 1024,
    parameter int DATA_WIDTH = 64
) (
    input  logic                          clk,

    // Port A
    input  logic                          a_en,
    input  logic                          a_we,
    input  logic [DATA_WIDTH/8-1:0]       a_be,
    input  logic [$clog2(DEPTH)-1:0]      a_addr,
    input  logic [DATA_WIDTH-1:0]         a_wdata,
    output logic [DATA_WIDTH-1:0]         a_rdata,

    // Port B
    input  logic                          b_en,
    input  logic                          b_we,
    input  logic [DATA_WIDTH/8-1:0]       b_be,
    input  logic [$clog2(DEPTH)-1:0]      b_addr,
    input  logic [DATA_WIDTH-1:0]         b_wdata,
    output logic [DATA_WIDTH-1:0]         b_rdata
);

    localparam int ADDR_WIDTH = $clog2(DEPTH);
    localparam int BE_WIDTH   = DATA_WIDTH / 8;

    (* ram_style = "block" *)
    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    // Port A — read-first
    always_ff @(posedge clk) begin
        if (a_en) begin
            a_rdata <= mem[a_addr];
            if (a_we) begin
                for (int i = 0; i < BE_WIDTH; i++) begin
                    if (a_be[i])
                        mem[a_addr][i*8 +: 8] <= a_wdata[i*8 +: 8];
                end
            end
        end
    end

    // Port B — read-first
    always_ff @(posedge clk) begin
        if (b_en) begin
            b_rdata <= mem[b_addr];
            if (b_we) begin
                for (int i = 0; i < BE_WIDTH; i++) begin
                    if (b_be[i])
                        mem[b_addr][i*8 +: 8] <= b_wdata[i*8 +: 8];
                end
            end
        end
    end

endmodule
