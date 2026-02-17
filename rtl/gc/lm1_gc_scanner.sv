// ============================================================================
// LM-1 GC Scanner Engine
//
// Walks a memory region (base..base+size) object-by-object.
// For each object, reads the header to determine size,
// then scans all fields. Fields that are refs are written
// to a scan-results buffer (pointer list).
//
// Interface:
//   - cmd_valid/cmd_ready: accept a scan command (base + size)
//   - mem port: issues reads to cluster or tile SRAM
//   - result port: streams discovered ref pointers
//   - busy: asserted while scanning
// ============================================================================
module lm1_gc_scanner
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // Command interface
    input  logic               cmd_valid,
    output logic               cmd_ready,
    input  logic [XLEN-1:0]   cmd_base,        // region start address
    input  logic [XLEN-1:0]   cmd_size,        // region size in bytes

    // Memory read port
    output logic               mem_rd_en,
    output logic [XLEN-1:0]   mem_rd_addr,
    input  logic [XLEN-1:0]   mem_rd_data,
    input  logic               mem_rd_valid,

    // Result output: discovered refs
    output logic               res_valid,
    output logic [XLEN-1:0]   res_obj_addr,    // container object address
    output logic [15:0]        res_field,       // field index
    output logic [XLEN-1:0]   res_ref,         // the ref value
    input  logic               res_ready,       // consumer can accept

    // Status
    output logic               busy
);

    typedef enum logic [2:0] {
        IDLE,
        READ_HDR,
        WAIT_HDR,
        READ_FIELD,
        WAIT_FIELD,
        EMIT_REF,
        DONE
    } scan_state_t;

    scan_state_t             st;
    logic [XLEN-1:0]        region_end;
    logic [XLEN-1:0]        cur_addr;          // current object address
    logic [XLEN-1:0]        obj_addr;          // base of current object
    logic [15:0]             obj_size;          // payload words
    logic [15:0]             field_idx;         // current field being scanned
    logic [XLEN-1:0]        scan_data;         // latched field value

    assign busy      = (st != IDLE);
    assign cmd_ready = (st == IDLE);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st         <= IDLE;
            region_end <= '0;
            cur_addr   <= '0;
            obj_addr   <= '0;
            obj_size   <= '0;
            field_idx  <= '0;
            scan_data  <= '0;
        end else begin
            case (st)
            IDLE: begin
                if (cmd_valid) begin
                    cur_addr   <= cmd_base;
                    region_end <= cmd_base + cmd_size;
                    st         <= READ_HDR;
                end
            end

            READ_HDR: begin
                if (cur_addr >= region_end) begin
                    st <= DONE;
                end else begin
                    // Issue header read
                    st <= WAIT_HDR;
                end
            end

            WAIT_HDR: begin
                if (mem_rd_valid) begin
                    if (is_header(mem_rd_data)) begin
                        obj_addr  <= cur_addr;
                        obj_size  <= header_size(mem_rd_data);
                        field_idx <= 16'd0;
                        if (header_size(mem_rd_data) == 16'd0) begin
                            // Empty object, advance past header
                            cur_addr <= cur_addr + 64'd8;
                            st       <= READ_HDR;
                        end else begin
                            // Start scanning fields
                            cur_addr <= cur_addr + 64'd8;
                            st       <= READ_FIELD;
                        end
                    end else begin
                        // Not a header — skip (possibly forwarding pointer
                        // or corrupted region). Advance by one word.
                        cur_addr <= cur_addr + 64'd8;
                        st       <= READ_HDR;
                    end
                end
            end

            READ_FIELD: begin
                if (field_idx >= obj_size || cur_addr >= region_end) begin
                    // All fields scanned or hit region boundary
                    st <= READ_HDR;
                end else begin
                    st <= WAIT_FIELD;
                end
            end

            WAIT_FIELD: begin
                if (mem_rd_valid) begin
                    scan_data <= mem_rd_data;
                    if (is_any_ref(mem_rd_data)) begin
                        st <= EMIT_REF;
                    end else begin
                        // Not a ref, skip
                        field_idx <= field_idx + 16'd1;
                        cur_addr  <= cur_addr + 64'd8;
                        st        <= READ_FIELD;
                    end
                end
            end

            EMIT_REF: begin
                if (res_ready) begin
                    field_idx <= field_idx + 16'd1;
                    cur_addr  <= cur_addr + 64'd8;
                    st        <= READ_FIELD;
                end
                // else: stall until consumer accepts
            end

            DONE: begin
                st <= IDLE;
            end

            default: st <= IDLE;
            endcase
        end
    end

    // Memory read control
    assign mem_rd_en   = (st == READ_HDR && cur_addr < region_end) ||
                         (st == READ_FIELD && field_idx < obj_size &&
                          cur_addr < region_end);
    assign mem_rd_addr = cur_addr;

    // Result output
    assign res_valid    = (st == EMIT_REF);
    assign res_obj_addr = obj_addr;
    assign res_field    = field_idx;
    assign res_ref      = scan_data;

endmodule
