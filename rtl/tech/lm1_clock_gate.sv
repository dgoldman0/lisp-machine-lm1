// ============================================================================
// LM-1 Clock Gating Cell
//
// Technology-agnostic integrated clock gate (ICG).
//
// For ASIC: replace with a standard-cell ICG from the target library.
//           The latch-based design below is glitch-free.
//
// For FPGA: clock gating is typically unnecessary (clock distribution
//           uses dedicated routing).  Synthesis tools will optimize this
//           to a simple pass-through or use clock-enable logic.
//
// Behavior:
//   gclk = clk AND (latched_en OR test_enable)
//
// The enable is latched on the falling edge of clk to prevent glitches.
// ============================================================================
module lm1_clock_gate (
    input  logic clk,
    input  logic en,       // clock enable (sampled on clk falling edge)
    input  logic te,       // test/scan enable (bypass)
    output logic gclk      // gated clock output
);

    logic en_latched;

    // Latch enable on falling edge of clk — standard ICG pattern
    always_latch begin
        if (~clk)
            en_latched = en;
    end

    assign gclk = clk & (en_latched | te);

endmodule
