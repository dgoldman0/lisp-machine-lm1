// ============================================================================
// LM-1 GC Fixup (Pointer-Update) Engine
//
// Scans a region of memory and updates every ref that points into the
// forwarded range.  A ref whose target has a forwarding pointer (gc_bits
// == 0xFF in the header) is rewritten to the new address extracted from
// the forwarding pointer.
//
// The engine walks the region word-by-word.  For each tagged ref it:
//   1. reads the header at the target address
//   2. if forwarded, rewrites the ref in-place
//
// Interface mirrors scanner & copier for uniform cluster wiring.
// ============================================================================
module lm1_gc_fixup
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // Command interface
    input  logic               cmd_valid,
    output logic               cmd_ready,
    input  logic [XLEN-1:0]   cmd_region_base,  // region to fixup
    input  logic [XLEN-1:0]   cmd_region_size,  // size in bytes

    // Forwarding source region (latched from last copier command)
    // Only follow refs pointing into this range.
    input  logic [XLEN-1:0]   fwd_region_base,
    input  logic [XLEN-1:0]   fwd_region_end,

    // Memory read port
    output logic               mem_rd_en,
    output logic [XLEN-1:0]   mem_rd_addr,
    input  logic [XLEN-1:0]   mem_rd_data,
    input  logic               mem_rd_valid,

    // Memory write port
    output logic               mem_wr_en,
    output logic [XLEN-1:0]   mem_wr_addr,
    output logic [XLEN-1:0]   mem_wr_data,
    input  logic               mem_wr_ready,

    // Status
    output logic               busy
);

    typedef enum logic [3:0] {
        IDLE,
        READ_WORD,
        WAIT_WORD,
        CHECK_REF,
        READ_TARGET_HDR,
        WAIT_TARGET_HDR,
        WRITE_UPDATED,
        WAIT_WRITE,
        ADVANCE,
        DONE
    } state_t;

    state_t                    st;
    logic [XLEN-1:0]         scan_addr;        // current word address in region
    logic [XLEN-1:0]         scan_end;         // region end address
    logic [XLEN-1:0]         cur_word;         // latched word from region
    logic [XLEN-1:0]         target_hdr;       // latched header of target object

    assign busy      = (st != IDLE);
    assign cmd_ready = (st == IDLE);

    // Extract new address from forwarding pointer
    function automatic logic [XLEN-1:0] fwd_new_addr(logic [XLEN-1:0] fwd);
        // The forwarding pointer was built as {0xFF, new_addr[55:3], TAG_HEADER}
        // Address occupies bits [55:3] (53 bits).  Bits [63:56] are gc_bits=0xFF.
        return {8'b0, fwd[55:3], 3'b0};
    endfunction

    // Build an updated ref: same tag, new address from fwd
    function automatic logic [XLEN-1:0] update_ref(
        logic [XLEN-1:0] old_ref,
        logic [XLEN-1:0] fwd
    );
        logic [XLEN-1:0] new_addr;
        new_addr = fwd_new_addr(fwd);
        // Ref format: {addr[63:3], tag[2:0]}
        return {new_addr[63:3], old_ref[2:0]};
    endfunction

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st         <= IDLE;
            scan_addr  <= '0;
            scan_end   <= '0;
            cur_word   <= '0;
            target_hdr <= '0;
        end else begin
            case (st)
            IDLE: begin
                if (cmd_valid) begin
                    scan_addr <= cmd_region_base;
                    scan_end  <= cmd_region_base + cmd_region_size;
                    st        <= READ_WORD;
                end
            end

            READ_WORD: begin
                if (scan_addr >= scan_end)
                    st <= DONE;
                else
                    st <= WAIT_WORD;
            end

            WAIT_WORD: begin
                if (mem_rd_valid) begin
                    cur_word <= mem_rd_data;
                    st       <= CHECK_REF;
                end
            end

            CHECK_REF: begin
                if (is_any_ref(cur_word)) begin
                    // Only follow refs that point into the copier's source
                    // region where forwarding pointers were installed.
                    // Refs to other regions are live and should not be touched.
                    logic [XLEN-1:0] ref_addr;
                    ref_addr = ref_address(cur_word);
                    if (ref_addr >= fwd_region_base && ref_addr < fwd_region_end)
                        st <= READ_TARGET_HDR;
                    else
                        st <= ADVANCE;
                end else begin
                    // Not a ref — skip
                    st <= ADVANCE;
                end
            end

            // Read header of the referent object
            READ_TARGET_HDR: begin
                st <= WAIT_TARGET_HDR;
            end

            WAIT_TARGET_HDR: begin
                if (mem_rd_valid) begin
                    target_hdr <= mem_rd_data;
                    if (is_header(mem_rd_data) &&
                        mem_rd_data[HDR_GC_HI:HDR_GC_LO] == 8'hFF) begin
                        // Forwarded — update the ref
                        st <= WRITE_UPDATED;
                    end else begin
                        // Not forwarded — leave as-is
                        st <= ADVANCE;
                    end
                end
            end

            // Write updated ref back to the scanned location
            WRITE_UPDATED: begin
                st <= WAIT_WRITE;
            end

            WAIT_WRITE: begin
                if (mem_wr_ready)
                    st <= ADVANCE;
            end

            ADVANCE: begin
                scan_addr <= scan_addr + 64'd8;
                st        <= READ_WORD;
            end

            DONE: begin
                st <= IDLE;
            end

            default: st <= IDLE;
            endcase
        end
    end

    // Memory read port
    assign mem_rd_en   = (st == READ_WORD && scan_addr < scan_end) ||
                         (st == READ_TARGET_HDR);
    assign mem_rd_addr = (st == READ_TARGET_HDR) ? ref_address(cur_word)
                                                 : scan_addr;

    // Memory write port
    assign mem_wr_en   = (st == WRITE_UPDATED);
    assign mem_wr_addr = scan_addr;
    assign mem_wr_data = update_ref(cur_word, target_hdr);

endmodule
