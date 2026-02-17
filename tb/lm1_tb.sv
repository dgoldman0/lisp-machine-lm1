// ============================================================================
// LM-1 CPU Testbench (SystemVerilog wrapper for Verilator)
//
// Loads a .hex file into SRAM via the external memory port,
// then releases reset and runs until halted or timeout.
// After halt, reads register values for checking.
// ============================================================================
module lm1_tb;
    import lm1_pkg::*;

    parameter int MEM_DEPTH_LOG2 = 16;
    parameter int MAX_CYCLES     = 100000;

    logic               clk;
    logic               rst_n;

    // Config
    logic [XLEN-1:0]   cfg_tile_id;
    logic [XLEN-1:0]   cfg_thread_id;

    // Status
    logic               halted;
    logic [XLEN-1:0]   pc_out;
    logic [XLEN-1:0]   cycle_count;

    // External memory port
    logic               ext_mem_en;
    logic               ext_mem_we;
    logic [MEM_DEPTH_LOG2-1:0] ext_mem_addr;
    logic [XLEN-1:0]   ext_mem_wdata;
    logic [XLEN-1:0]   ext_mem_rdata;

    // Debug register read
    logic [REG_IDX_W-1:0] dbg_reg_addr;
    logic [XLEN-1:0]     dbg_reg_data;

    // GC engine command (stub — tied off for standalone CPU test)
    logic               gc_cmd_valid;
    logic [3:0]         gc_cmd_op;
    logic [XLEN-1:0]   gc_cmd_arg0;
    logic [XLEN-1:0]   gc_cmd_arg1;
    logic [XLEN-1:0]   gc_cmd_arg2;

    // Message queue external port (stub)
    logic [XLEN-1:0]   ext_mq_rd_data;
    logic               ext_mq_rd_valid;
    logic               ext_mq_wr_ready;
    logic [3:0]         mq_empty;
    logic [3:0]         mq_full;

    // Crossbar port (stub — no cluster SRAM in standalone test)
    logic               xbar_req_valid;
    logic               xbar_req_we;
    logic [XLEN-1:0]   xbar_req_addr;
    logic [XLEN-1:0]   xbar_req_wdata;

    // DUT
    lm1_cpu #(
        .MEM_DEPTH_LOG2(MEM_DEPTH_LOG2)
    ) u_dut (
        .clk           (clk),
        .rst_n         (rst_n),
        .cfg_tile_id   (cfg_tile_id),
        .cfg_thread_id (cfg_thread_id),
        .halted        (halted),
        .pc_out        (pc_out),
        .cycle_count   (cycle_count),
        .ext_mem_en    (ext_mem_en),
        .ext_mem_we    (ext_mem_we),
        .ext_mem_addr  (ext_mem_addr),
        .ext_mem_wdata (ext_mem_wdata),
        .ext_mem_rdata (ext_mem_rdata),
        .dbg_reg_addr  (dbg_reg_addr),
        .dbg_reg_data  (dbg_reg_data),
        // GC engine — not connected (engine always ready/idle)
        .gc_cmd_valid  (gc_cmd_valid),
        .gc_cmd_op     (gc_cmd_op),
        .gc_cmd_arg0   (gc_cmd_arg0),
        .gc_cmd_arg1   (gc_cmd_arg1),
        .gc_cmd_arg2   (gc_cmd_arg2),
        .gc_cmd_ready  (1'b1),
        .gc_engine_busy(1'b0),
        // External message queue — not connected
        .ext_mq_wr_en  (1'b0),
        .ext_mq_wr_id  (2'b0),
        .ext_mq_wr_data(64'b0),
        .ext_mq_wr_ready(ext_mq_wr_ready),
        .ext_mq_rd_en  (1'b0),
        .ext_mq_rd_id  (2'b0),
        .ext_mq_rd_data(ext_mq_rd_data),
        .ext_mq_rd_valid(ext_mq_rd_valid),
        // Queue status
        .mq_empty      (mq_empty),
        .mq_full       (mq_full),
        // Crossbar — tied off (no cluster SRAM in standalone test)
        .xbar_req_valid (xbar_req_valid),
        .xbar_req_we    (xbar_req_we),
        .xbar_req_addr  (xbar_req_addr),
        .xbar_req_wdata (xbar_req_wdata),
        .xbar_req_ready (1'b1),
        .xbar_resp_data ({XLEN{1'b0}}),
        .xbar_resp_valid(1'b0)
    );

    // Clock generation
    initial clk = 0;
    always #5 clk = ~clk;  // 100 MHz

    // Memory image
    logic [XLEN-1:0] mem_image [0:(1<<MEM_DEPTH_LOG2)-1];

    // Main test sequence
    initial begin
        int unsigned ncycles;

        // Init
        rst_n         = 0;
        ext_mem_en    = 0;
        ext_mem_we    = 0;
        ext_mem_addr  = 0;
        ext_mem_wdata = 0;
        cfg_tile_id   = 0;
        cfg_thread_id = 0;
        dbg_reg_addr  = 0;

        // Load hex file
        begin
            string hex_file;
            if (!$value$plusargs("HEX=%s", hex_file))
                hex_file = "test.hex";
            $readmemh(hex_file, mem_image);
        end

        // Hold reset and load memory
        repeat (2) @(posedge clk);

        ext_mem_en = 1;
        ext_mem_we = 1;
        for (int i = 0; i < (1 << MEM_DEPTH_LOG2); i++) begin
            ext_mem_addr  = i[MEM_DEPTH_LOG2-1:0];
            ext_mem_wdata = mem_image[i];
            @(posedge clk);
        end
        ext_mem_en = 0;
        ext_mem_we = 0;

        // Release reset
        repeat (2) @(posedge clk);
        rst_n = 1;

        // Run until halted or timeout
        ncycles = 0;
        while (!halted && ncycles < MAX_CYCLES) begin
            @(posedge clk);
            ncycles++;
        end

        if (halted) begin
            $display("CPU halted after %0d cycles (PC=%0h)", ncycles, pc_out);
        end else begin
            $display("TIMEOUT after %0d cycles (PC=%0h)", ncycles, pc_out);
        end

        // Dump all 32 registers
        for (int i = 0; i < 32; i++) begin
            // To read register i, we would need the debug port.
            // With our current design, dbg_reg_data is latched from port B
            // when halted. Let's read via port B by driving dbg_reg_addr.
            // Actually, looking at the CPU, dbg_reg_data reads rf_rd2_data
            // which is driven by rf_rd2_addr. But rf_rd2_addr is driven by
            // the control FSM. When halted, the FSM holds its state and
            // rf_rd2_addr stays at its last value.
            //
            // We need a different approach. Let's just read the register
            // file directly through the hierarchy.
            $display("REG r%0d = %016h", i,
                     u_dut.u_regfile.regs[i]);
        end

        $finish;
    end

    // Waveform dump
    initial begin
        $dumpfile("lm1_tb.vcd");
        $dumpvars(0, lm1_tb);
    end

endmodule
