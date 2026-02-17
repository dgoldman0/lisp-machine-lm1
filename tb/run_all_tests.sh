#!/bin/bash
# Run all LM-1 RTL tests and check results against expected values.
#
# Usage: ./run_all_tests.sh
#
# Exit code 0 if all tests pass, 1 otherwise.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$ROOT/tb/build"
SIM="$BUILD_DIR/obj_dir/Vlm1_tb"

if [ ! -x "$SIM" ]; then
    echo "ERROR: simulation binary not found at $SIM"
    echo "       Build it first (see tb/run_test.sh or use verilator --binary)"
    exit 1
fi

TOTAL=0
PASSED=0
FAILED=0
FAIL_LIST=""

for hex in "$ROOT/tb/tests"/*.hex; do
    name=$(basename "$hex" .hex)
    expected="$ROOT/tb/tests/${name}.expected"
    TOTAL=$((TOTAL + 1))

    if [ ! -f "$expected" ]; then
        echo "SKIP $name (no .expected file)"
        continue
    fi

    # Run simulation
    output=$("$SIM" "+HEX=$hex" 2>&1)

    # Check if halted
    if echo "$output" | grep -q "^TIMEOUT"; then
        echo "FAIL $name — TIMEOUT"
        FAILED=$((FAILED + 1))
        FAIL_LIST="$FAIL_LIST $name"
        continue
    fi

    # Check each expected register
    test_ok=1
    while IFS='=' read -r reg val; do
        regnum="${reg#r}"
        expected_hex=$(printf '%016x' "$val")
        actual_line=$(echo "$output" | grep "^REG r${regnum} = " || true)
        if [ -z "$actual_line" ]; then
            echo "FAIL $name — r${regnum} not found in output"
            test_ok=0
            break
        fi
        actual_hex=$(echo "$actual_line" | awk '{print $4}')
        if [ "$actual_hex" != "$expected_hex" ]; then
            echo "FAIL $name — r${regnum}: expected=$expected_hex actual=$actual_hex"
            test_ok=0
        fi
    done < "$expected"

    if [ "$test_ok" = "1" ]; then
        cycles=$(echo "$output" | grep "^CPU halted" | sed 's/.*after \([0-9]*\).*/\1/')
        echo "PASS $name (${cycles} cycles)"
        PASSED=$((PASSED + 1))
    else
        FAILED=$((FAILED + 1))
        FAIL_LIST="$FAIL_LIST $name"
    fi
done

echo ""
echo "Results: $PASSED/$TOTAL passed, $FAILED failed"
if [ "$FAILED" -gt 0 ]; then
    echo "Failed tests:$FAIL_LIST"
    exit 1
fi
exit 0
