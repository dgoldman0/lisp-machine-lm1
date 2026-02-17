// ============================================================================
// LM-1 Performance Counters
//
// 8 64-bit counters for GC and runtime telemetry.
// Counters are incremented via strobe inputs from the control FSM.
// Readable via SYS_INFO with SYS_PERF_CTR sub-code.
// ============================================================================
module lm1_perf_counters
    import lm1_pkg::*;
(
    input  logic               clk,
    input  logic               rst_n,

    // Increment strobes from the control FSM
    input  logic               alloc_inc,        // CTR_ALLOC_COUNT
    input  logic [15:0]        alloc_bytes_inc,   // CTR_ALLOC_BYTES (add N bytes)
    input  logic               barrier_fire_inc,  // CTR_BARRIER_FIRES
    input  logic               barrier_filt_inc,  // CTR_BARRIER_FILTERED
    input  logic               ic_hit_inc,        // CTR_IC_HITS
    input  logic               ic_miss_inc,       // CTR_IC_MISSES
    input  logic               gc_cycle_inc,      // CTR_GC_CYCLES (1 cycle of engine active)
    input  logic               nursery_ovf_inc,   // CTR_NURSERY_OVERFLOWS

    // Read port
    input  logic [4:0]         rd_id,
    output logic [XLEN-1:0]   rd_value
);

    logic [XLEN-1:0] counters [0:7];

    // Combinational read
    always_comb begin
        if (rd_id < 5'd8)
            rd_value = counters[rd_id[2:0]];
        else
            rd_value = '0;
    end

    // Sequential update
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < 8; i++)
                counters[i] <= '0;
        end else begin
            if (alloc_inc)
                counters[CTR_ALLOC_COUNT[2:0]] <= counters[CTR_ALLOC_COUNT[2:0]] + 64'd1;
            if (alloc_bytes_inc != '0)
                counters[CTR_ALLOC_BYTES[2:0]] <= counters[CTR_ALLOC_BYTES[2:0]] +
                                              {48'b0, alloc_bytes_inc};
            if (barrier_fire_inc)
                counters[CTR_BARRIER_FIRES[2:0]] <= counters[CTR_BARRIER_FIRES[2:0]] + 64'd1;
            if (barrier_filt_inc)
                counters[CTR_BARRIER_FILTERED[2:0]] <= counters[CTR_BARRIER_FILTERED[2:0]] + 64'd1;
            if (ic_hit_inc)
                counters[CTR_IC_HITS[2:0]] <= counters[CTR_IC_HITS[2:0]] + 64'd1;
            if (ic_miss_inc)
                counters[CTR_IC_MISSES[2:0]] <= counters[CTR_IC_MISSES[2:0]] + 64'd1;
            if (gc_cycle_inc)
                counters[CTR_GC_CYCLES[2:0]] <= counters[CTR_GC_CYCLES[2:0]] + 64'd1;
            if (nursery_ovf_inc)
                counters[CTR_NURSERY_OVERFLOWS[2:0]] <= counters[CTR_NURSERY_OVERFLOWS[2:0]] + 64'd1;
        end
    end

endmodule
