// ============================================================================
// LM-1 Single-Port Synchronous SRAM
//
// Technology-agnostic behavioral model.  Infers block RAM on FPGA.
// For ASIC, replace this file with a compiled SRAM macro wrapper.
//
// - Synchronous read with 1-cycle latency (read-first mode)
// - Byte-enable granularity for writes
// - Parameterized depth and data width
// ============================================================================
module lm1_sram_sp #(
    parameter int DEPTH      = 1024,
    parameter int DATA_WIDTH = 64
) (
    input  logic                          clk,
    input  logic                          en,       // chip enable
    input  logic                          we,       // write enable
    input  logic [DATA_WIDTH/8-1:0]       be,       // byte enables
    input  logic [$clog2(DEPTH)-1:0]      addr,
    input  logic [DATA_WIDTH-1:0]         wdata,
    output logic [DATA_WIDTH-1:0]         rdata
);

    localparam int ADDR_WIDTH = $clog2(DEPTH);
    localparam int BE_WIDTH   = DATA_WIDTH / 8;

    // Storage array — synthesis attribute for FPGA block RAM inference
    (* ram_style = "block" *)
    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    // Read-first: on simultaneous read+write to the same address,
    // the OLD data is returned.
    always_ff @(posedge clk) begin
        if (en) begin
            rdata <= mem[addr];
            if (we) begin
                for (int i = 0; i < BE_WIDTH; i++) begin
                    if (be[i])
                        mem[addr][i*8 +: 8] <= wdata[i*8 +: 8];
                end
            end
        end
    end

endmodule
