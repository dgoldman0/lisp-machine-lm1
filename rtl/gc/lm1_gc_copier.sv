// ============================================================================
// LM-1 GC Copier Engine
//
// Copies live objects from a source region to a destination region.
// For each object: copies header + payload sequentially,
// then installs a forwarding pointer at the old header location.
//
// A forwarding pointer is a header word with gc_bits == 0xFF
// and the new address in place of the shape_id/size fields.
//
// Interface:
//   - cmd_valid/cmd_ready: accept a copy command (src_base, dst_base, size)
//   - mem port: issues reads (src) and writes (dst + forwarding)
//   - busy: asserted while copying
// ============================================================================
module lm1_gc_copier
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // Command interface
    input  logic               cmd_valid,
    output logic               cmd_ready,
    input  logic [XLEN-1:0]   cmd_src_base,    // source region start
    input  logic [XLEN-1:0]   cmd_dst_base,    // destination region start
    input  logic [XLEN-1:0]   cmd_size,        // source region size in bytes

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
    output logic               busy,
    output logic [XLEN-1:0]   dst_ptr          // current dst allocation pointer
);

    typedef enum logic [3:0] {
        IDLE,
        READ_HDR,
        WAIT_HDR,
        COPY_HDR,
        WAIT_COPY_HDR,
        COPY_FIELD,
        WAIT_RD_FIELD,
        WRITE_FIELD,
        WAIT_WR_FIELD,
        INSTALL_FWD,
        WAIT_FWD,
        ADVANCE,
        DONE
    } copy_state_t;

    copy_state_t             st;
    logic [XLEN-1:0]        src_end;
    logic [XLEN-1:0]        src_addr;          // current source read pointer
    logic [XLEN-1:0]        dst_addr;          // current dest write pointer
    logic [XLEN-1:0]        obj_src;           // source object header address
    logic [XLEN-1:0]        obj_dst;           // dest object header address
    logic [XLEN-1:0]        hdr_val;           // latched header
    logic [15:0]             obj_size;          // payload words from header
    logic [15:0]             word_idx;          // current word being copied
    logic [XLEN-1:0]        field_data;        // latched field value

    assign busy      = (st != IDLE);
    assign cmd_ready = (st == IDLE);
    assign dst_ptr   = dst_addr;

    // Forwarding pointer: gc_bits=0xFF, rest encodes new address
    function automatic logic [XLEN-1:0] make_fwd_ptr(logic [XLEN-1:0] new_addr);
        return {8'hFF, new_addr[55:3], TAG_HEADER};
    endfunction

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st         <= IDLE;
            src_end    <= '0;
            src_addr   <= '0;
            dst_addr   <= '0;
            obj_src    <= '0;
            obj_dst    <= '0;
            hdr_val    <= '0;
            obj_size   <= '0;
            word_idx   <= '0;
            field_data <= '0;
        end else begin
            case (st)
            IDLE: begin
                if (cmd_valid) begin
                    src_addr <= cmd_src_base;
                    dst_addr <= cmd_dst_base;
                    src_end  <= cmd_src_base + cmd_size;
                    st       <= READ_HDR;
                end
            end

            READ_HDR: begin
                if (src_addr >= src_end)
                    st <= DONE;
                else
                    st <= WAIT_HDR;
            end

            WAIT_HDR: begin
                if (mem_rd_valid) begin
                    hdr_val <= mem_rd_data;
                    if (is_header(mem_rd_data) &&
                        mem_rd_data[HDR_GC_HI:HDR_GC_LO] != 8'hFF) begin
                        // Live object — copy it
                        obj_src  <= src_addr;
                        obj_dst  <= dst_addr;
                        obj_size <= header_size(mem_rd_data);
                        word_idx <= 16'd0;
                        st       <= COPY_HDR;
                    end else begin
                        // Already forwarded or not a header — skip one word.
                        // NOTE: for forwarded headers, header_size() would
                        // extract address bits (not size) from the fwd ptr,
                        // so we always advance word-by-word.  Subsequent
                        // payload words are not headers and will be skipped
                        // one at a time until the next real header.
                        src_addr <= src_addr + 64'd8;
                        st <= READ_HDR;
                    end
                end
            end

            // Write header to destination
            COPY_HDR: begin
                st <= WAIT_COPY_HDR;
            end

            WAIT_COPY_HDR: begin
                if (mem_wr_ready) begin
                    src_addr <= src_addr + 64'd8;
                    dst_addr <= dst_addr + 64'd8;
                    if (obj_size == 16'd0)
                        st <= INSTALL_FWD;
                    else
                        st <= COPY_FIELD;
                end
            end

            // Read a field from source
            COPY_FIELD: begin
                if (word_idx >= obj_size)
                    st <= INSTALL_FWD;
                else
                    st <= WAIT_RD_FIELD;
            end

            WAIT_RD_FIELD: begin
                if (mem_rd_valid) begin
                    field_data <= mem_rd_data;
                    st         <= WRITE_FIELD;
                end
            end

            // Write field to destination
            WRITE_FIELD: begin
                st <= WAIT_WR_FIELD;
            end

            WAIT_WR_FIELD: begin
                if (mem_wr_ready) begin
                    word_idx <= word_idx + 16'd1;
                    src_addr <= src_addr + 64'd8;
                    dst_addr <= dst_addr + 64'd8;
                    st       <= COPY_FIELD;
                end
            end

            // Install forwarding pointer at old header location
            INSTALL_FWD: begin
                st <= WAIT_FWD;
            end

            WAIT_FWD: begin
                if (mem_wr_ready) begin
                    st <= READ_HDR;
                end
            end

            DONE: begin
                st <= IDLE;
            end

            default: st <= IDLE;
            endcase
        end
    end

    // Memory read control
    assign mem_rd_en   = (st == READ_HDR && src_addr < src_end) ||
                         (st == COPY_FIELD && word_idx < obj_size);
    assign mem_rd_addr = src_addr;

    // Memory write control
    assign mem_wr_en   = (st == COPY_HDR) ||
                         (st == WRITE_FIELD) ||
                         (st == INSTALL_FWD);
    assign mem_wr_addr = (st == INSTALL_FWD) ? obj_src : dst_addr;
    assign mem_wr_data = (st == COPY_HDR)     ? hdr_val :
                         (st == INSTALL_FWD)   ? make_fwd_ptr(obj_dst) :
                                                 field_data;

endmodule
