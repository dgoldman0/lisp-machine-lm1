# LM-1 Test Gap Analysis & Ideas

_Generated 2026-02-17 — based on RTL review of all modules, FSM states, opcodes, and GC engine logic._

---

## Priority Legend

| Tag | Meaning |
|-----|---------|
| **P0** | Potential correctness bug found during review — fix or prove safe |
| **P1** | Entire instruction or major feature has zero test coverage |
| **P2** | Feature is partially tested but important edge cases are missing |
| **P3** | Requires infrastructure beyond the current single-core testbench |

---

## P0 — Potential Bugs to Investigate

### P0-1  CAS.TAGGED store fires without checking `lsu_ready`

In `S_MEM_WAIT` for `OP_CAS_TAGGED`, when the compare succeeds the FSM
issues `lsu_req = 1` / `lsu_op = LSU_STORE64` and immediately transitions
to `S_FETCH`.  It never checks `lsu_ready`.  If the LSU isn't ready to
accept a new request the cycle after completing the read response, the
store is silently dropped — the CAS reports success (returns `T`) but memory
is unchanged.

**Where:** `lm1_control.sv`, S_MEM_WAIT → OP_CAS_TAGGED branch.
**Risk:** Silent data loss on successful CAS — correctness of all
lock-free data structures.
**Action:** Verify the LSU is always ready after a load response (it
transitions from `S_RESP` → `S_IDLE` in one cycle, so `ready` should
be high).  If that invariant can break under back-pressure, add a
`S_CAS_WRITE` wait-state.

### P0-2  FAA store fires without checking `lsu_ready`

Identical issue to P0-1, but in the `OP_FAA_FENCE` branch of `S_MEM_WAIT`.

**Where:** `lm1_control.sv`, S_MEM_WAIT → OP_FAA_FENCE branch.
**Action:** Same analysis as P0-1 — prove or add a wait-state.

### P0-3  I-Cache has no invalidation mechanism

`lm1_icache.sv` stores lines in a direct-mapped cache with tag+valid
bits, but there is no `FENCE.I`, `IC.INVAL`, or any other way to flush
a cache line.  If code is patched via `STR` to an address that's already
cached, the CPU executes the stale cached copy.

**Where:** `lm1_icache.sv` — no state/port for invalidation.
**Risk:** JIT compilation, dynamic code loading, and self-modifying code
are all silently broken.
**Action:** Either (a) add an I-cache flush instruction, or (b) document
that code regions are immutable and enforce it architecturally.

### P0-4  Tagged fixnum DIV uses unsigned divider

`lm1_alu.sv` untags fixnums with `$signed(op) >>> 1`, but then feeds
the result to the same unsigned iterative divider.  For negative
fixnums the `$unsigned()` cast means the divider sees a huge positive
number, producing a wrong quotient and remainder.

**Where:** `lm1_alu.sv`, `FUNC_DIV_FIX` / `FUNC_MOD_FIX` paths.
**Risk:** All negative fixnum division gives wrong results.
**Action:** Either add sign correction logic around the divider or
switch to a signed divider.

### P0-5  Template table uses `initial` block, not `rst_n`

`lm1_tmpl_table.sv` zeroes the template RAM in an `initial` block,
which is ignored by synthesis.  On real silicon the template entries
are undefined after reset.

**Where:** `lm1_tmpl_table.sv:37`.
**Risk:** Post-synthesis mismatch; real hardware reads garbage from
uninitialized template slots.
**Action:** Add a reset-sequencer that clears all 64 entries, or add a
"template valid" bit and trap on access to invalid entries.

---

## P1 — Entirely Untested Instructions

### P1-1  DIV / MOD (raw and tagged fixnum)

No test for `FUNC_DIV`, `FUNC_MOD`, `FUNC_DIV_FIX`, or `FUNC_MOD_FIX`.
The iterative divider runs for up to 64 cycles — never exercised.

**Test ideas:**
- Basic: `100 / 10 = 10`, `100 % 10 = 0`
- Boundary: `x / 1 = x`, `0 / x = 0`
- Division by zero → `TRAP_DIVIDE_BY_ZERO`
- Large 64-bit dividend, small divisor
- Tagged: fixnum divide positive/positive, negative/positive, negative/negative
- Tagged: divide by fixnum-zero → trap
- P0-4 regression: `-6 / 3` must equal `-2`, not a huge positive number

### P1-2  SEND / RECV / TRY.RECV (message queues)

Same-core SEND→RECV is fully wired and testable without external stubs.
4 independent hardware queues (IDs 0-3), depth 512 each.

**Test ideas:**
- SEND value, RECV it back — round-trip
- SEND to all 4 queues, RECV each — correct routing
- RECV on empty queue → `TRAP_QUEUE_EMPTY`
- SEND to full queue → `TRAP_QUEUE_FULL` (needs 512 SENDs)
- TRY.RECV on empty → returns NIL + NIL (status)
- TRY.RECV on non-empty → returns value + T
- FIFO ordering: SEND A, SEND B, RECV → should get A first

### P1-3  TST.SHAPE (header-based type testing)

Allocate objects with known template shape IDs, then use `TST.SHAPE`
to check.  Also test fast-path: non-ref operands (fixnum, NIL,
cons-ref) should return NIL without a memory access.

**Test ideas:**
- Allocate object with shape ID 42, `TST.SHAPE 42` → T
- Same object, `TST.SHAPE 99` → NIL
- Fixnum input → NIL (fast path, no memory access)
- NIL input → NIL
- Cons-ref input → NIL (cons has no header to read)
- Shape ID 0 and 0xFFFF boundary values

### P1-4  Sub-word memory ops (LDB, LDH, LDW, STB, STH, STW)

Card-table barrier uses internal `STB` but no user-level sub-word
instruction is tested.

**Test ideas:**
- STB + LDB round-trip at each of the 8 byte offsets within a 64-bit word
- STH + LDH at each of the 4 halfword positions
- STW + LDW at top/bottom word positions
- Verify surrounding bytes/words are not clobbered
- Alignment masking: LDH with odd address → bit 0 forced to 0

### P1-5  TST with all tag constants (1–7)

`test_09` only checks `TST.FIX` (tag constant 0).  Tag constants 1–7
(ref, cons, special, nil, char, sfloat, header) are never tested.

**Test ideas:**
- Construct values of each tag type and TST each
- Cross-check: `TST.REF` on a fixnum → NIL, on a ref → T
- Char-tagged value → `TST.CHAR` → T
- Sfloat-tagged value → `TST.SFLOAT` → T
- Header-tagged value → `TST.HDR` → T
- NIL → `TST.NIL` → T, `TST.FIX` → NIL

### P1-6  LI32 — 32-bit immediate loading

The assembler auto-expands `LI` for large values, so LI32 is used
implicitly but never verified in isolation.

**Test ideas:**
- LI of a value > 32767 (triggers LI32 expansion)
- LI of a negative 32-bit value — **check zero-extend vs sign-extend**
  (`lsu_rdata` from `LSU_LOAD32` may zero-extend; does the emulator agree?)
- LI of 0x7FFFFFFF (max positive 32-bit)
- LI of 0xFFFFFFFF (-1 as 32-bit) — should become 0x00000000FFFFFFFF, NOT
  0xFFFFFFFFFFFFFFFF, if zero-extended
- LI at different PC alignments within a cache line

### P1-7  CAS.TAGGED — compare-and-swap

**Test ideas:**
- Successful CAS: `mem[addr] == expected` → swap + return T
- Failed CAS: `mem[addr] != expected` → no swap + return NIL
- CAS on a ref-tagged address (tag stripping for address computation)
- P0-1 regression: verify the store actually lands in memory, not just
  that T is returned

### P1-8  FAA — fetch-and-add

**Test ideas:**
- Basic: `mem[addr] = 10`, FAA with delta 5 → returns 10, mem = 15
- Delta = 0 → returns old value, no change
- Negative delta (if raw DIV is unsigned, what about raw ADD overflow?)
- P0-2 regression: verify the store actually lands

---

## P2 — Partially Tested Features, Missing Edge Cases

### P2-1  CALL.CLOSURE end-to-end

`CALL.DIR` is tested (test_16), but `CALL.CLOSURE` takes a completely
different path through `S_HDR_WAIT` → header verification → field 0
read for code entry.

**Test ideas:**
- Allocate a closure, install code entry, call it → verify execution
  at the code entry address and env slot accessibility
- Call a non-closure object → `TRAP_NOT_CLOSURE`
- Closure with 0 environment slots (minimal closure)

### P2-2  CALL.IC / TAILCALL.IC / IC.INSTALL (inline cache dispatch)

The entire inline-cache dispatch system is untested.

**Test ideas:**
- IC.INSTALL shape + target, then CALL.IC on matching shape → hits
- CALL.IC on non-matching shape → `TRAP_IC_MISS`
- IC miss handler installs, retry hits
- IC table fill + FIFO eviction (install 65 entries, verify oldest evicted)
- TAILCALL.IC (no frame push variant)

### P2-3  PUSH.MULTI / POP.MULTI

Multi-register push/pop with 16-bit mask never tested.

**Test ideas:**
- Empty mask (mm=0) → no-op, no stack change
- Single-bit mask → push/pop one register
- All 16 bits set → push/pop 16 registers, verify ordering
- Alternating bits (0xAAAA) → only even/odd registers
- Both banks (rd[0]=0 and rd[0]=1) → different register ranges

### P2-4  Nested traps

A trap inside a trap handler overwrites `trap_pc` and `trap_cause` —
no trap context stack.

**Test ideas:**
- Allocate inside a trap handler → trigger nursery overflow → nested trap
- Verify ERET returns to the correct location (it won't — this is a
  known architectural limitation, but should be documented as such)
- If nested traps are "undefined behavior," add a trap-inside-trap
  detection that halts or panics

### P2-5  ERET when not in trap

Executing ERET with `in_trap == 0` should trigger `TRAP_UNIMPLEMENTED`.

**Test ideas:**
- Execute ERET without a preceding trap → verify trap fires
- This is a one-liner test

### P2-6  Trap table entry = 0 (halt-on-unhandled-trap)

When the loaded trap handler address is 0, the CPU halts.

**Test ideas:**
- Trigger a trap for a vector that was never configured → verify HALT
- Set a vector to 0 explicitly, trigger it → verify HALT

### P2-7  JR (jump register) and TAILCALL.DIR

Both are simple but never tested as standalone instructions.

**Test ideas:**
- JR: load address into register, JR to it, verify execution continues there
- TAILCALL.DIR: verify no frame push (SP unchanged), correct target

### P2-8  SYS_PERF_CTR readback

Counter wiring is present but no test reads any counter.

**Test ideas:**
- Read CTR_CYCLES (id=0) — should be non-zero after some instructions
- Read CTR_INSTRS (id=1) — should match instruction count
- Read CTR_BRANCHES (id=2), CTR_ALLOCS (id=3), etc.
- Read invalid counter ID (8-31) → should return 0
- Verify counter IDs are encoded in the correct instruction field
  (`imm16[4:0]` per RTL)

### P2-9  ALLOC.CONS with NP/NL as car or cdr argument

When `rs2 == REG_NP`, the CDR value read in `S_ALLOC_RD_CDR` gets the
*updated* NP (post-bump), not the original.  Is this intended?

**Test idea:** `ALLOC.CONS r3, r1, np` → what CDR value is stored?

### P2-10  Backward branch boundary

No test verifies a branch with the maximum negative offset, or a
branch to self (offset=0, infinite loop).

**Test ideas:**
- Branch backward across a known distance, verify target
- BR with offset 0 — this creates an infinite loop; verify with a
  cycle limit that the PC doesn't advance

### P2-11 BR.T (truthy) edge cases

**Test ideas:**
- val = 1 → truthy (non-zero, non-NIL)
- val = NIL → not truthy
- val = 0 → not truthy
- val = ref pointer → truthy

### P2-12 CMP.TAGGED cross-type comparison

Comparing a cons-ref (tag 011) and a general-ref (tag 001) — should
this be a type-mismatch trap, or a valid identity comparison?  The RTL
checks `a[2:0] == b[2:0]`, so different primary tags → trap.

**Test ideas:**
- cons-ref vs general-ref → verify trap fires
- NIL vs NIL → same tag, identity match → returns fixnum 0
- Same ref vs same ref → returns fixnum 0
- Different refs, same tag → what ordering?

---

## P3 — Needs Infrastructure Work

### P3-1  Multi-thread (FGMT)

Every test runs single-threaded (only thread 0 active).  Thread
activation, context switching, per-thread NP/NL, interleaved execution,
and thread halting with remaining active siblings are entirely untested.

**Known concerns:**
- CSRs (trap table, card base, etc.) are shared, not per-thread
- `in_trap` flag is shared — nested trap from a different thread corrupts it
- Thread activation mechanism needs to be identified (is there a
  `THREAD.START` instruction or a CSR write?)

**Infrastructure needed:** Testbench support for activating threads
(probably via a system trap or init sequence).

### P3-2  GC engine integration (scanner, copier, fixup)

The scanner, copier, and fixup engines in `rtl/gc/` are never
instantiated in any test.  They need shared SRAM and the memory arbiter.

**Known concerns in scanner:**
- If a region starts mid-object, the scanner interprets payload words as headers
- If the arbiter delays a grant, `mem_rd_valid` timing may be off

**Known concerns in copier:**
- Already-forwarded objects (gc_bits == 0xFF) are skipped with 8-byte
  advance, but a multi-word object's payload words are then individually
  scanned — a payload word that looks like a header is misinterpreted

**Known concerns in fixup:**
- `update_ref()` zeroes the top 8 bits (gc_bits) — fine for < 48-bit addrs
- Self-fixup (fixing up the same region being copied) is an edge case

**Infrastructure needed:** Cluster-level testbench with `lm1_gc_engine_top`,
SRAM, and the memory arbiter.  Alternatively, a standalone GC-engine-only
testbench.

### P3-3  FENCE.GC stall behavior

`gc_engine_busy` is hardwired to 0 in the testbench, so `FENCE.GC`
completes in one cycle always.

**Infrastructure needed:** A toggle-able `gc_engine_busy` signal, or
integration with the real GC engine top.

### P3-4  Scanner result FIFO (SYS_SCAN_COUNT, SYS_SCAN_POP_REF, etc.)

All scanner FIFO inputs are hardwired to 0 in the testbench.

**Infrastructure needed:** Either scanner engine integration or a separate
FIFO stimulus mechanism in the testbench.

### P3-5  Cluster / crossbar / tile modules

`lm1_cluster.sv`, `lm1_crossbar.sv`, `lm1_tile.sv` have zero coverage.
The crossbar arbiter, multi-tile memory routing, and clock gating are
all untested.

**Infrastructure needed:** A cluster-level testbench.

### P3-6  External message queue injection

The external side of the message queues (`ext_mq_*`) is stubbed.
Testing NoC message arrival requires testbench stimulus on these ports.

---

## Emulator Considerations

### E-1  SYS_INFO dead code in emulator

`emu/lm1/cpu.py` lines 478-492 still have the old `func`-based
SYS_INFO dispatch path.  The live path (lines 499+) uses `rs1`.
The dead code should be removed or clearly marked, to avoid confusion
if someone edits the emulator.

### E-2  LI32 sign-extension divergence

RTL zero-extends the 32-bit immediate via `lsu_rdata` (which is the
full 64-bit memory word, zero-padded on the upper 32 bits for
`LSU_LOAD32`).  If the emulator sign-extends, there's a divergence
for negative 32-bit immediates.  Needs a cross-check.

### E-3  Tagged DIV/MOD — emulator vs RTL

The emulator probably uses Python's `//` and `%` which handle signs
correctly.  The RTL uses an unsigned divider (P0-4).  These will
diverge for negative operands.

---

## Suggested Test Ordering

If implementing these tests, a natural order based on dependency and
value:

1. **P0-4** (tagged DIV sign bug) — investigate and fix first
2. **P0-1/P0-2** (CAS/FAA lsu_ready) — audit and prove or fix
3. **P1-1** (DIV/MOD) — exercises the divider, catches P0-4
4. **P1-5** (TST all tags) — trivial, high value
5. **P1-6** (LI32) — catches E-2 divergence
6. **P1-3** (TST.SHAPE) — needed before CALL.IC tests
7. **P1-4** (sub-word loads/stores) — fundamental memory correctness
8. **P1-2** (SEND/RECV) — self-contained, no stubs needed
9. **P1-7/P1-8** (CAS/FAA) — catches P0-1/P0-2
10. **P2-3** (PUSH.MULTI/POP.MULTI) — important for real programs
11. **P2-1** (CALL.CLOSURE) — needed before P2-2
12. **P2-2** (CALL.IC) — the dispatch system
13. **P2-4 through P2-12** — remaining edge cases
14. **P3-**** — infrastructure-dependent, tackle when cluster TB exists
