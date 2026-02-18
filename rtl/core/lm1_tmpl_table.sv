// ============================================================================
// LM-1 Header Template Table
//
// Small addressable table that stores header-word templates for ALLOC
// instructions.  Software writes templates via TRAP 0x91 (SET_TEMPLATE);
// the allocator reads them to stamp headers on new objects.
//
// 256 entries × 64 bits, single-port read + single-port write.
// Reads are combinational (same-cycle), writes are synchronous.
// ============================================================================
module lm1_tmpl_table
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // Read port (combinational)
    input  logic [7:0]         rd_idx,
    output logic [XLEN-1:0]   rd_data,

    // Write port (synchronous)
    input  logic               wr_en,
    input  logic [7:0]         wr_idx,
    input  logic [XLEN-1:0]   wr_data
);

    logic [XLEN-1:0] entries [0:255];

    // Combinational read
    assign rd_data = entries[rd_idx];

    // synthesis translate_off
    initial begin
        for (int i = 0; i < 256; i++)
            entries[i] = '0;
    end
    // synthesis translate_on

    // Synchronous write with reset
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < 256; i++)
                entries[i] = '0;  // blocking in reset loop (Verilator req)
        end else if (wr_en) begin
            entries[wr_idx] <= wr_data;
        end
    end

endmodule
