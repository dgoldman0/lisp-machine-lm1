#!/bin/bash
# Run an LM-1 RTL test through Verilator simulation
#
# Usage: ./run_test.sh <test_name>
#   e.g.: ./run_test.sh 01_li
#
# Expects:
#   tb/tests/<test_name>.hex       — memory image
#   tb/tests/<test_name>.expected  — expected register values

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$ROOT/tb/build"
TEST_NAME="${1:?Usage: $0 <test_name>}"
HEX_FILE="$ROOT/tb/tests/${TEST_NAME}.hex"
EXPECTED="$ROOT/tb/tests/${TEST_NAME}.expected"

if [ ! -f "$HEX_FILE" ]; then
    echo "ERROR: $HEX_FILE not found"
    exit 1
fi

# Build (once) if needed
if [ ! -f "$BUILD_DIR/obj_dir/Vlm1_tb" ]; then
    echo "=== Building Verilator simulation ==="
    mkdir -p "$BUILD_DIR"
    cd "$BUILD_DIR"
    verilator --binary --timing -j 0 \
        -Wno-UNUSEDSIGNAL -Wno-UNDRIVEN -Wno-UNUSEDPARAM -Wno-UNOPTFLAT \
        -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-CASEINCOMPLETE \
        -Wno-BLKANDNBLK -Wno-INITIALDLY -Wno-SYNCASYNCNET \
        -Wno-PINCONNECTEMPTY \
        -GMAX_CYCLES=100000 \
        --top-module lm1_tb \
        -I"$ROOT/rtl/core" -I"$ROOT/rtl/tech" \
        "$ROOT/rtl/core/lm1_pkg.sv" \
        "$ROOT/rtl/core/lm1_decoder.sv" \
        "$ROOT/rtl/core/lm1_regfile.sv" \
        "$ROOT/rtl/core/lm1_alu.sv" \
        "$ROOT/rtl/core/lm1_branch.sv" \
        "$ROOT/rtl/core/lm1_lsu.sv" \
        "$ROOT/rtl/core/lm1_control.sv" \
        "$ROOT/rtl/core/lm1_tmpl_table.sv" \
        "$ROOT/rtl/core/lm1_ic_table.sv" \
        "$ROOT/rtl/core/lm1_msg_queue.sv" \
        "$ROOT/rtl/core/lm1_perf_counters.sv" \
        "$ROOT/rtl/core/lm1_cpu.sv" \
        "$ROOT/rtl/tech/lm1_sram_sp.sv" \
        "$ROOT/tb/lm1_tb.sv"
    cd "$ROOT"
fi

# Run
echo "=== Running test: $TEST_NAME ==="
cd "$BUILD_DIR"
./obj_dir/Vlm1_tb +verilator+seed+1 +verilator+rand+reset+0 \
    "+HEX=$HEX_FILE" \
    2>&1 | tee "$ROOT/tb/tests/${TEST_NAME}.log"

# Check results
echo ""
echo "=== Checking results ==="
PASS=1
while IFS='=' read -r reg val; do
    regnum="${reg#r}"
    expected_val="$val"
    # Extract actual value from log
    actual_line=$(grep "^REG r${regnum} = " "$ROOT/tb/tests/${TEST_NAME}.log" || true)
    if [ -z "$actual_line" ]; then
        echo "FAIL: $reg — no output found"
        PASS=0
        continue
    fi
    actual_hex=$(echo "$actual_line" | awk '{print $4}')
    # Normalize: strip leading zeros, compare
    expected_norm=$(printf '%016x' "$expected_val")
    if [ "$actual_hex" = "$expected_norm" ]; then
        echo "  PASS: $reg = 0x$actual_hex"
    else
        echo "  FAIL: $reg expected=0x$expected_norm actual=0x$actual_hex"
        PASS=0
    fi
done < "$EXPECTED"

if [ "$PASS" = "1" ]; then
    echo "=== TEST PASSED ==="
    exit 0
else
    echo "=== TEST FAILED ==="
    exit 1
fi
