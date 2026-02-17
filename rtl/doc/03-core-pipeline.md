# LM-1 Core Pipeline

## Overview

The LM-1 core is a **multi-cycle FSM processor**, not a pipelined one. Each instruction
passes through a variable-length sequence of states depending on its complexity.
There is no pipeline hazard logic, no bypass network, no branch predictor, and no
speculation. Instead, the core uses **fine-grained multithreading (FGMT)** to hide
latency — 4 hardware threads share the datapath, switching every instruction boundary.

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ Thread 0 │──▶│ Thread 1 │──▶│ Thread 2 │──▶│ Thread 3 │──▶ (wrap)
  └──────────┘   └──────────┘   └──────────┘   └──────────┘
  Each completes its full instruction before the next runs.
```

## Datapath Block Diagram

```
                    ┌─────────────────────────────────────────┐
                    │              lm1_cpu                     │
                    │                                         │
  inst from         │   ┌──────────┐      ┌───────────┐      │
  I-Cache ─────────▶│──▶│ Decoder  │─────▶│ Control   │      │
                    │   └──────────┘      │  FSM      │      │
                    │        │            │ (46 states)│      │
                    │        ▼            └─────┬─────┘      │
                    │   ┌──────────┐            │            │
                    │   │ Regfile  │◀───────────┘            │
                    │   │ 128×64   │ rd/wr                   │
                    │   │ (4×32)   │                         │
                    │   └──┬──┬────┘                         │
                    │      │  │                              │
                    │      ▼  ▼                              │
                    │   ┌──────────┐      ┌───────────┐      │
                    │   │   ALU    │      │  Branch   │      │
                    │   │  +div    │      │ Evaluator │      │
                    │   └──────────┘      └───────────┘      │
                    │        │                               │
                    │        ▼                               │
                    │   ┌──────────┐      ┌───────────┐      │
                    │   │   LSU    │─────▶│ Tile SRAM │      │
                    │   └──────────┘      │ / Xbar    │      │
                    │                     └───────────┘      │
                    │                                         │
                    │   ┌──────────┐  ┌──────────┐  ┌──────┐ │
                    │   │ IC Table │  │Tmpl Table│  │ MsgQ │ │
                    │   │  64-ent  │  │ 256-ent  │  │ 4×512│ │
                    │   └──────────┘  └──────────┘  └──────┘ │
                    └─────────────────────────────────────────┘
```

---

## Instruction Flow: State by State

### 1. Fetch — `S_FETCH`

The FSM begins each instruction by requesting the I-Cache:

```
S_FETCH:
  assert icache_fetch_req with addr = pc
  if icache_fetch_valid:
      inst_latched = icache_fetch_data
      increment IC hit counter
      → S_DECODE
  else:
      stay in S_FETCH (I-Cache fill runs in parallel)
```

On a cache miss, the I-Cache raises `fill_req`. The **fill sequencer** in
`lm1_cpu` reads 8 consecutive 64-bit words from tile SRAM into the cache line.
While the fill runs, the FSM stays in `S_FETCH` polling `icache_fetch_valid`
each cycle.

**Legacy path (`S_FETCH_WAIT`):** The original pre-cache path issued a direct
LSU instruction fetch and waited for `lsu_valid`. This path remains as
fallback but is not exercised in normal operation.

### 2. Decode — `S_DECODE`

The decoder is purely **combinational** — it continuously decodes `inst_latched`.
The `S_DECODE` state latches the decoded fields and reads register operands:

```
S_DECODE:
  dr = decoded instruction (opcode, rd, rs1, rs2, func, imm16)
  imm_r = sign_extend(imm16)
  Read rd  via regfile port A → opa (for 3-operand ops and stores)
  Read rs1 via regfile port B → opb
  → S_EXECUTE
```

The register file uses **asynchronous reads** (combinational), so operands
are available the same cycle they're requested.

### 3. Execute — `S_EXECUTE`

This is the big dispatch state. The opcode determines the next state:

| Category | Instructions | Next State(s) |
|----------|-------------|----------------|
| ALU ops | ARITH_RAW, BITWISE, ARITH_FIX, ADD_FIX_IMM, CMP_TAGGED, TST | `S_ALU_WAIT` |
| Simple imm | LI, LUI | Direct writeback → `S_FETCH` |
| Raw memory | LDR, STR, LDB, STB, LDH, STH, LDW, STW | `S_MEM` → `S_MEM_WAIT` |
| Tagged loads | LD, LD_CAR_CDR | ref check → `S_FIELD_MEM` → `S_FIELD_WAIT` |
| Tagged stores | ST, ST_CAR_CDR | ref check → `S_FIELD_MEM` → `S_FIELD_WAIT` |
| Write-barrier | ST_WB | `S_FIELD_MEM` → `S_FIELD_WAIT` → `S_BARRIER_CHECK` |
| Branches | BR, BR_COND | Evaluate → update `pc_n` → `S_FETCH` |
| Calls | CALL_DIRECT | `S_PUSH_FRAME_0` (3 store states) → target |
| Calls | CALL_IC, TAILCALL_IC | `S_HDR_READ` → `S_IC_DISPATCH` |
| Calls | CALL_CLOSURE | `S_HDR_READ` → `S_CLOS_CODE_RD` → frame push |
| Return | RET | `S_POP_FRAME_0` chain (3 loads) |
| Allocation | ALLOC, ALLOC_CONS, ALLOCV, ALLOC_CLOSURE | Nursery check → `S_ALLOC_HDR` → zero-fill loop |
| Stack multi | PUSH_MULTI, POP_MULTI | `S_MULTI_ITER` loop |
| Queue | SEND, RECV, TRY.RECV | Direct or trap |
| GC commands | ENQ_SCAN/COPY/FIXUP/COMPACT | Issue cmd → `S_FETCH` or `S_ENQ_WAIT` |
| System | TRAP, ERET, SYS_INFO, HALT_NOP | Various |

During `S_EXECUTE`, the `rs2` register is read via port A (it wasn't read in
`S_DECODE` since only `rd` and `rs1` were needed for the common case).

### 4. ALU Wait — `S_ALU_WAIT`

Single-cycle operations: ALU sets `alu_valid` and `alu_result` immediately.
The FSM writes the result to the register file and transitions to `S_FETCH`.

Multi-cycle operations (DIV/MOD): The ALU's internal divider FSM runs for
**64 clock cycles** (1 bit per cycle, shift-and-subtract). The control FSM
polls `alu_valid` each cycle. On completion, the result is written back.

If the ALU raises `alu_trap`, the FSM captures the trap code and transitions
to `S_TRAP_LOOKUP` instead.

### 5. Memory Access — `S_MEM` / `S_MEM_WAIT`

For raw loads and stores:

```
S_MEM:
  Issue LSU request (op, addr, wdata)
  → S_MEM_WAIT

S_MEM_WAIT:
  Wait for lsu_valid
  If load: writeback lsu_rdata to rd
  → S_FETCH
```

For sub-word operations (LDB/LDH/LDW/STB/STH/STW), the LSU handles byte
enable generation and sub-word extraction internally. The control FSM simply
issues the appropriate LSU opcode.

### 6. Tagged Field Access — `S_FIELD_MEM` / `S_FIELD_WAIT`

Before entering field access, the FSM **type-checks** the base register:

```
S_EXECUTE (for LD/ST):
  if !is_any_ref(opa):
      trap TRAP_NOT_REF
  else:
      ta = ref_address(opa) + imm * 8
      → S_FIELD_MEM
```

For cons operations, the check is `is_cons()` and the trap is `TRAP_NOT_CONS`.

### 7. Write Barrier — `S_BARRIER_CHECK` / `S_BARRIER_MARK`

After a `ST.WB` store completes:

```
S_BARRIER_CHECK:
  If stored value is a reference AND target address < gen_boundary:
      card_addr = card_table_base + (ta >> card_shift)
      → S_BARRIER_MARK (store 0xFF to card_addr via LSU_STORE_BYTE)
  Else:
      → S_FETCH (filtered out)

S_BARRIER_MARK:
  → S_BARRIER_MARK_W (wait for store)
  → S_FETCH
```

The barrier increments either `ctr_barrier_fire` (dirty path) or
`ctr_barrier_filt` (filtered path) for profiling.

### 8. Call/Return Frames

**CALL_DIRECT** pushes a 2-word frame:

```
S_PUSH_FRAME_0:  mem[SP-8] = LR     (save return address)
S_PUSH_FRAME_W0: wait
S_PUSH_FRAME_1:  mem[SP-16] = FP    (save frame pointer)
S_PUSH_FRAME_W1: wait
S_PUSH_FRAME_2:  LR = return_addr; FP = SP-16; SP = SP-16
                  pc = target
                  → S_FETCH
```

**RET** restores the frame:

```
S_POP_FRAME_0:   Load FP from mem[FP]     (saved frame pointer)
S_POP_FRAME_W0:  Wait; FP ← rdata
                 Load LR from mem[FP+8]    (saved return address)
S_POP_FRAME_1:   Wait; LR ← rdata; SP = FP+16
S_POP_FRAME_W1:  Wait
S_POP_FRAME_2:   PC = LR; → S_FETCH
```

### 9. Allocation Sequence

```
S_EXECUTE (ALLOC):
  Read template header from tmpl_table[imm]
  total_words = template_size + n_extra
  if NP + total_words*8 > NL:
      trap TRAP_NURSERY_OVERFLOW
  aa = NP            (base address)
  anw = total_words
  → S_ALLOC_HDR

S_ALLOC_HDR:   mem[aa] = header_word (with patched size)
S_ALLOC_HDR_W: wait
S_ALLOC_ZERO:  mem[aa + acnt*8] = 0  (zero one word)
S_ALLOC_ZERO_W: wait; acnt--; if acnt > 0 → S_ALLOC_ZERO
S_ALLOC_INIT:  Type-specific init (cons car, closure code ptr)
S_ALLOC_DONE:  Rd = make_ref(aa); NP += total_words * 8
               increment alloc counter; → S_FETCH
```

The zero-fill loop runs one store per word. For a 4-word object, this is
4 stores × 2 cycles = 8 cycles for the zero fill alone.

### 10. Inline Cache Dispatch

```
S_HDR_READ:    Load header from mem[ref_address(receiver)]
S_HDR_WAIT:    Extract shape_id from header[55:24]
               Lookup (callsite_pc, shape_id) in IC table
S_IC_DISPATCH: If hit → push frame (CALL_IC) or jump (TAILCALL_IC)
               If miss → trap TRAP_IC_MISS
```

Miss handling is in software: the trap handler resolves the method, then
executes `IC.INSTALL` to insert the entry for future fast-path hits.

---

## The Control FSM — All 49 States

The heart of the processor is a 49-state FSM in `lm1_control.sv` (~2,200 lines).
The state register is 6 bits wide. Here is every state with its role:

| # | State | Cycles | Role |
|---|-------|--------|------|
| 0 | `S_RESET` | 1 | Initialize; activate thread 0 only |
| 1 | `S_FETCH` | 1+ | I-Cache fetch request; wait on miss |
| 2 | `S_FETCH_WAIT` | 1 | Legacy LSU fetch path |
| 3 | `S_DECODE` | 1 | Latch decoded fields, read rd/rs1 |
| 4 | `S_EXECUTE` | 1 | Big opcode dispatch, read rs2 |
| 5 | `S_ALU_WAIT` | 1-65 | Wait for ALU result (div = 64 cycles) |
| 6 | `S_MEM` | 1 | Issue LSU load/store |
| 7 | `S_MEM_WAIT` | 1 | Wait for LSU response |
| 8 | `S_FIELD_MEM` | 1 | Tagged field load/store issue |
| 9 | `S_FIELD_WAIT` | 1 | Tagged field LSU response |
| 10–14 | `S_PUSH_FRAME_*` | 5 | Save LR/FP, set new frame |
| 15–19 | `S_POP_FRAME_*` | 5 | Restore LR/FP/SP |
| 20–23 | `S_MULTI_*` | N | Push/pop multiple registers |
| 24–32 | `S_ALLOC_*` | 6-40+ | Object allocation + zero fill |
| 33–34 | `S_HDR_READ/WAIT` | 2 | Read object header (IC, closure, shape) |
| 35–36 | `S_CLOS_CODE_*` | 2 | Read closure code pointer |
| 37 | `S_IC_DISPATCH` | 1 | IC hit/miss resolution |
| 38–40 | `S_BARRIER_*` | 1-3 | Write barrier check + card mark |
| 41–42 | `S_SEND/RECV_WAIT` | 1 | Queue operations (reserved) |
| 43 | `S_TRY_RECV_WB` | 1 | Non-blocking receive writeback |
| 44 | `S_ENQ_WAIT` | 1+ | Wait for GC engine acceptance |
| 45 | `S_FENCE_GC` | 1+ | Wait for GC engines idle |
| 46–47 | `S_TRAP_*` | 2 | Trap handler lookup + dispatch |
| 48 | `S_HALTED` | ∞ | Terminal (per-thread or full halt) |

### Typical Cycle Counts

| Instruction | Cycles | Notes |
|-------------|--------|-------|
| LI, LUI, NOP | 4 | FETCH + DECODE + EXECUTE + FETCH |
| ADD.FIX, SUB.FIX | 5 | + ALU_WAIT |
| LDR (hit) | 6 | + MEM + MEM_WAIT |
| STR | 6 | + MEM + MEM_WAIT |
| LD (tagged) | 6 | Ref check + FIELD_MEM + FIELD_WAIT |
| ST.WB (no barrier) | 7 | + BARRIER_CHECK (filtered) |
| ST.WB (barrier fires) | 9 | + BARRIER_MARK + BARRIER_MARK_W |
| CALL_DIRECT | 9 | + 5 frame-push states |
| RET | 9 | + 5 frame-pop states |
| CALL_IC (hit) | 11 | + HDR_READ + HDR_WAIT + IC_DISPATCH + frame |
| CALL_IC (miss) | ~8 + trap | HDR + trap lookup |
| ALLOC (4-word) | ~16 | HDR + 4×zero + init + done |
| DIV.FIX | ~68 | 64-cycle divider |
| BR (taken) | 4 | Same as LI |
| PUSH_MULTI (8 regs) | ~20 | 8 iterations × ~2.5 cycles |

With 4 threads in FGMT, the effective throughput is 4× these numbers
(modulo memory port contention).

---

## Decoder Detail

The decoder (`lm1_decoder.sv`) is purely combinational. It extracts fields
from the 32-bit instruction and generates control signals.

### Field Extraction

```
instruction[31:26] → opcode (6 bits)
instruction[25:21] → rd     (5 bits)
instruction[20:16] → rs1    (5 bits)
instruction[15:11] → rs2    (5 bits)
instruction[10:6]  → func   (5 bits)
instruction[15:0]  → imm16  (16 bits, sign-extended)
instruction[10:0]  → imm11  (11 bits)
instruction[25:0]  → raw26  (26 bits)
```

### Control Signals

| Signal | When Set |
|--------|----------|
| `rf_we` | Instructions that write a result to `rd` |
| `rf_rd_rs2` | 3-operand instructions needing `rs2` |
| `is_alu_op` | Result comes from ALU |
| `is_mem_load` | Load from memory → `rd` |
| `is_mem_store` | Store to memory |
| `is_branch` | Affects PC (branch/jump) |
| `is_alloc` | Allocation instruction |
| `is_multi_cycle` | Requires multi-state sequencing |
| `is_nop` | No-op (prefetch, NOP, FENCE.GC) |

### Instruction Latch

The decoder doesn't have its own register. Instead, the control FSM manages
`inst_latched` (32-bit register in `lm1_cpu`). It's updated via `inst_latch_en` /
`inst_latch_data` when a valid instruction arrives from either the I-Cache or
the LSU fallback path. The decoder continuously decodes whatever is in the latch.

---

## Register File

`lm1_regfile.sv` — 128 × 64-bit registers organized as 4 banks of 32.

### Architecture

```
Thread 0: regs[0:31]    → addresses 0x00–0x1F
Thread 1: regs[0:31]    → addresses 0x20–0x3F
Thread 2: regs[0:31]    → addresses 0x40–0x5F
Thread 3: regs[0:31]    → addresses 0x60–0x7F
```

Bank select: `banked_addr(tid, ridx) = {tid[1:0], ridx[4:0]}`

### Ports

| Port | Direction | Timing |
|------|-----------|--------|
| Read A (rd_addr1) | Combinational | Used for: rd operand, rs2 in execute, debug |
| Read B (rd_addr2) | Combinational | Used for: rs1 operand, debug readout |
| Write (wr_addr, wr_data, wr_en) | Synchronous (posedge) | Single write per cycle |

### Write-Through Bypass

Both read ports implement combinational forwarding:

```systemverilog
assign rd_data1 = (w_en && rd_addr1 == w_addr) ? w_data : mem[rd_addr1];
assign rd_data2 = (w_en && rd_addr2 == w_addr) ? w_data : mem[rd_addr2];
```

This eliminates read-after-write hazards within the same cycle.

### No Hardwired Zero

Unlike RISC-V (which hardwires `x0=0`), the LM-1 has no zero register.
All 32 registers per thread are fully general-purpose. This is a deliberate
choice: the tagged-word ISA needs `nil` (0x05), not zero, as its
"default" value, so a hardwired zero register would be less useful.

---

## ALU Detail

`lm1_alu.sv` — Arithmetic, logic, comparison, and type-test operations.

### Operation Categories

**Raw 64-bit (untagged):**
- ADD, SUB, MUL: single-cycle
- DIV, MOD: 64-cycle iterative divider

**Tagged fixnum:**
- ADD.FIX, SUB.FIX: single-cycle with overflow detection
- MUL.FIX: single-cycle (untag × tagged, retag)
- DIV.FIX: 64-cycle (untag both, divide, retag quotient)

**Bitwise:**
- AND, OR, XOR, NOT, SHL, SHR, ASR: single-cycle

**Type tests (TST):**
- Tests tag bits against 8 type constants (fixnum, ref, cons, special, nil, char, sfloat, header)
- Result: `VAL_T` or `VAL_NIL`

**Shape test (TST_SHAPE):**
- Reads header from memory (handled by FSM, not ALU)
- Compares `shape_id` field

**Comparison (CMP_TAGGED):**
- CMP: signed fixnum → tagged -1/0/+1; identity compare for refs
- EQ: bitwise equality → `VAL_T` / `VAL_NIL`

### The Divider

The divider is a sequential shift-and-subtract unit:

```
DIV_IDLE → (start) → DIV_RUNNING → (64 cycles) → DIV_DONE → DIV_IDLE
```

Internal state: 64-bit quotient `div_q`, 64-bit remainder `div_r`,
6-bit counter `div_count`. Each cycle shifts the dividend left by 1 bit
into the remainder, conditionally subtracts the divisor, and shifts
the result bit into the quotient.

Division by zero is detected at start and immediately produces
`DIV_DONE` with `div_by_zero = 1`.

### Overflow Detection

For tagged fixnum arithmetic:

```
ADD overflow: (a_sign == b_sign) && (a_sign != result_sign)
SUB overflow: (a_sign != b_sign) && (b_sign == result_sign)
MUL overflow: result[0] != 0  (tag bit should be 0 for valid fixnum)
```

---

## Branch Evaluator

`lm1_branch.sv` — Condition evaluation and target computation.

### Target Calculation

```
target = pc + sign_extend(offset[15:0]) << 2
```

The offset is in **words** (4 bytes each). The shift converts to bytes.
Maximum branch range: ±128 KiB from current PC.

### Conditions

| Code | Name | Test |
|------|------|------|
| 0 | `BR_T` (Truthy) | `val ≠ VAL_NIL && val ≠ 0` |
| 1 | `BR_NIL` | `val == VAL_NIL` (exact 64-bit match) |
| 2 | `BR_FIX_LT` | Fixnum < 0: `!val[0] && val[63]` |
| 3 | `BR_FIX_EQ` | Equal to zero: `val == 0` |
| 4 | `BR_FIX_GT` | Fixnum > 0: `!val[0] && !val[63] && val ≠ 0` |
| 5 | `BR_EQ_Z` | Word-zero: `val == 64'h0` |

The truthy test is the most important: it follows Lisp semantics where
everything except `nil` and fixnum-zero is considered true.

---

## Load/Store Unit

`lm1_lsu.sv` — Single-port memory access controller with sub-word support.

### Operations

| Code | Operation | Read/Write | Width |
|------|-----------|------------|-------|
| 0 | `NONE` | — | — |
| 1 | `IFETCH` | Read | 32-bit instruction |
| 2 | `LOAD64` | Read | 64-bit word |
| 3 | `STORE64` | Write | 64-bit word |
| 4 | `LOAD32` | Read | 32-bit (for LI32) |
| 5 | `LOAD_BYTE` | Read | 8-bit, zero-extended |
| 6 | `STORE_BYTE` | Write | 8-bit |
| 7 | `LOAD_HALF` | Read | 16-bit, zero-extended |
| 8 | `STORE_HALF` | Write | 16-bit |
| 9 | `LOAD_WORD` | Read | 32-bit, zero-extended |
| 10 | `STORE_WORD` | Write | 32-bit |

### FSM

```
LSU_IDLE → (read)  → LSU_WAIT_RD → (extract result) → done
         → (write) → LSU_DONE → done
```

Reads require one wait cycle for SRAM latency. Writes complete immediately
(SRAM has synchronous write).

### Byte Enable Generation

The SRAM is 64-bit wide. For sub-word stores, the LSU generates byte enables:

| Store Type | Byte Enable Pattern |
|------------|-------------------|
| STORE64 | `0xFF` (all bytes) |
| STORE_BYTE | `0x01 << addr[2:0]` |
| STORE_HALF | `0x03 << {addr[2:1], 0}` |
| STORE_WORD | `addr[2] ? 0xF0 : 0x0F` |

Store data is replicated across the 64-bit word so the correct byte lane
always has the right value regardless of alignment.

### Sub-Word Extraction (Loads)

| Load Type | Extraction |
|-----------|-----------|
| LOAD64 | Full 64-bit `rdata` |
| LOAD_BYTE | `{56'b0, rdata[addr_low*8 +: 8]}` |
| LOAD_HALF | `{48'b0, rdata[half_offset*8 +: 16]}` |
| LOAD_WORD | `addr[2] ? {32'b0, rdata[63:32]} : {32'b0, rdata[31:0]}` |
| IFETCH / LOAD32 | Same as LOAD_WORD |

---

## FGMT Threading

### Mechanism

The LM-1 supports 4 hardware threads (`NUM_THREADS = 4`). Thread switching
happens at every instruction boundary (round-robin):

```
On transition to S_FETCH (from a completed instruction):
  1. Save pc_n into thread_pc[cur_thread]
  2. Scan cur_thread+1, +2, +3, wrapping, for first active thread
  3. Load thread_pc[new_thread] into pc
  4. Set cur_thread = new_thread
```

### Thread State

| Signal | Width | Description |
|--------|-------|-------------|
| `cur_thread` | 2 bits | Currently executing thread index |
| `thread_pc[0:3]` | 4 × 64 bits | Per-thread program counter |
| `thread_active[3:0]` | 4 bits | Bitmask of active threads |

### Startup

On reset, only thread 0 is active (`thread_active = 4'b0001`). Threads 1-3
are started by software via system traps or thread-spawn instructions.

### Halt Behavior

When a thread executes `HALT` (rd=0):
1. `thread_active[cur_thread]` is cleared
2. If any other thread is still active, the FSM switches to that thread
3. The CPU-level `halted` signal only asserts when `thread_active == 0` and `state == S_HALTED`

### Register Banking

Each thread has its own 32-register bank in the 128-entry register file.
The banking function `banked_addr(tid, ridx) = {tid[1:0], ridx[4:0]}`
provides thread isolation without any save/restore overhead on switch.

---

## I-Cache

### Organization

`lm1_icache.sv` — 8 KiB direct-mapped cache.

| Parameter | Value |
|-----------|-------|
| Line size | 8 × 64-bit words (64 bytes) |
| Lines | 16 |
| Total | 16 × 64 = 1024 bytes... 8192 bits per line → 8 KiB total |
| Tag width | address bits above line+offset |
| Associativity | Direct-mapped (1-way) |

### Fill Sequencer (in `lm1_cpu`)

```
ICFILL_IDLE:
  Wait for icache_fill_req (cache miss)
  Compute icfill_base from fill_addr
  → ICFILL_READ

ICFILL_READ:
  Assert sram_en with addr = icfill_base + cnt
  → ICFILL_RESP

ICFILL_RESP:
  Forward sram_rdata to icache via fill_valid + fill_data
  cnt++
  if cnt == 7: assert fill_done → ICFILL_IDLE
  else: → ICFILL_READ (next word)
```

Fill takes **16 cycles** (8 reads × 2 cycles each — one for address setup,
one for data). During fill, the SRAM port is occupied, blocking CPU memory
accesses.

### Memory Port Priority

The SRAM port mux in `lm1_cpu` has this priority:

```
1. External (program load / debug)  ← highest
2. I-Cache fill
3. LSU (local address)              ← lowest
4. (Cluster access goes via crossbar, not local SRAM)
```

---

## Trap System

### Entry Path

```
Trap triggered (type error, nursery overflow, IC miss, etc.):
  1. tc = trap_code
  → S_TRAP_LOOKUP:
     trap_pc = pc
     trap_cause = tc
     in_trap = 1
     Issue LSU load: addr = trap_tbl + tc * 8
     → S_TRAP_WAIT
  → S_TRAP_WAIT:
     If handler_addr == 0: → S_HALTED (unhandled trap)
     Else: pc = handler_addr; → S_FETCH
```

### System Traps (Non-Faulting)

System traps (`TRAP` with imm bit 7 set) are handled directly in `S_EXECUTE`
without entering the trap handler path:

| Code | Name | Action |
|------|------|--------|
| 0x90 | SET_TRAP_TABLE | `trap_tbl = r1` |
| 0x91 | SET_TEMPLATE | `tmpl[r1[7:0]] = r2` |
| 0x92 | SET_CARD_BASE | `card_table_base = r1` |
| 0x93 | SET_CARD_SHIFT | `card_shift = r1[5:0]` |
| 0x94 | SET_GEN_BOUNDARY | `gen_boundary = r1` |
| 0x95 | SET_QUEUE_BASE | `queue_base = r1` |

### Exception Return

`ERET` restores `pc = trap_pc`, clears `in_trap`, and returns to `S_FETCH`.
If `ERET` is executed outside a trap handler, it traps `TRAP_UNIMPLEMENTED`.

---

## Performance Counters

`lm1_perf_counters.sv` — 8 × 64-bit saturating counters.

| Index | Counter | Strobe Signal |
|-------|---------|---------------|
| 0 | Allocations | `ctr_alloc_inc` |
| 1 | Bytes allocated | `ctr_alloc_bytes_inc[15:0]` |
| 2 | Barrier fires | `ctr_barrier_fire_inc` |
| 3 | Barrier filtered | `ctr_barrier_filt_inc` |
| 4 | IC hits | `ctr_ic_hit_inc` |
| 5 | IC misses | `ctr_ic_miss_inc` |
| 6 | Nursery overflows | `ctr_nursery_ovf_inc` |
| 7 | GC engine busy cycles | `gc_engine_busy` (level) |

Counters are read via `SYS_INFO` with `rs1 = SYS_PERF_CTR` and `imm16[4:0]`
selecting the counter index.

---

## Supporting Tables

### Template Table (`lm1_tmpl_table.sv`)

256-entry × 64-bit table of pre-formed header words. Used by allocation
instructions to avoid runtime header construction. Written via `TRAP 0x91`
(SET_TEMPLATE). Read port provides the template during `S_EXECUTE` for
allocation opcodes.

### IC Table (`lm1_ic_table.sv`)

64-entry fully-associative inline cache. Each entry stores
`(callsite_pc, shape_id) → target_address`. Lookup is combinational
(parallel match across all entries). Replacement is FIFO (circular pointer).

Operations:
- **Lookup**: `(ic_lookup_pc, ic_lookup_shape)` → `ic_hit`, `ic_target`
- **Install**: `ic_install_en` + `(pc, shape, target)` → writes next entry

---

## Debug Interface

When the CPU is halted, the debug interface allows register reads:

```
Input:  dbg_reg_addr (5-bit register index)
Output: dbg_reg_data (64-bit register value)
```

The address is routed through the regfile's port B with thread 0's bank
(`banked_addr(0, dbg_reg_addr)`). The result is latched every cycle while
halted and exposed as `dbg_reg_data`. This is used by the testbench to
verify register contents after program execution.
