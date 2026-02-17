# LM-1 GC Engines

## Why Hardware GC?

In a garbage-collected language runtime, GC pauses are the dominant source of
latency. Conventional GC runs on the same CPU cores that execute the mutator
(application code), competing for cache and memory bandwidth. The LM-1 takes a
different approach: **dedicated hardware state machines** perform the three core
GC operations (scanning, copying, fixup) on a separate SRAM port, running
concurrently with mutator threads.

This is not hypothetical — Azul Systems' Vega processor (2005) proved that
hardware-assisted GC could eliminate stop-the-world pauses for Java. The LM-1
applies a similar philosophy to a Lisp/Smalltalk-style runtime, but with a
simpler, more targeted design: three fixed-function engines instead of Vega's
general-purpose read-barrier hardware.

```
┌──────────────────────────────────────────────────────────┐
│                    GC Engine Top                         │
│                                                          │
│  ┌───────────┐   ┌───────────┐   ┌───────────┐          │
│  │  Scanner  │   │  Copier   │   │  Fixup    │          │
│  │  7 states │   │ 12 states │   │ 10 states │          │
│  │ (read-only│   │ (read +   │   │ (read +   │          │
│  │  walk)    │   │  write)   │   │  write)   │          │
│  └─────┬─────┘   └─────┬─────┘   └─────┬─────┘          │
│        │               │               │                │
│   ┌────┴───────────────┴───────────────┴────┐           │
│   │      Read Arbiter (Scanner > Copier > Fixup)        │
│   │      Write Arbiter (Copier > Fixup)                 │
│   └─────────────────────┬───────────────────┘           │
│                         │                               │
└─────────────────────────┼───────────────────────────────┘
                          │
                    SRAM Port B
                    (dual-port)
```

---

## Command Interface

GC engines are controlled by CPU instructions that issue commands through
the cluster GC arbiter.

### CPU Instructions → Engine Commands

| Instruction | GC Command | Engine | Arguments |
|-------------|-----------|--------|-----------|
| `ENQ.SCAN` | `GC_CMD_SCAN` (1) | Scanner | `arg0 = region_base`, `arg1 = region_size` |
| `ENQ.COPY` | `GC_CMD_COPY` (2) | Copier | `arg0 = src_base`, `arg1 = dst_base`, `arg2 = size` |
| `ENQ.FIXUP` | `GC_CMD_FIXUP` (3) | Fixup | `arg0 = region_base`, `arg1 = region_size` |
| `ENQ.COMPACT` | `GC_CMD_COMPACT` (4) | Copier (reused) | Same as COPY |
| `FENCE.GC` | — | — | Stall CPU until `busy == 0` |

### Command Handshake

```
cmd_valid ──▶ ┌──────────────┐ ──▶ engine starts
cmd_ready ◀── │ GC Engine Top│
              └──────────────┘
```

- `cmd_ready = !busy` — commands accepted only when **all three engines are idle**
- If the CPU issues a command while busy → `TRAP_ENGINE_BUSY`
- Only one engine runs at a time (mutual exclusion enforced by single `busy` signal)

### Cluster Command Arbiter

When multiple tiles issue GC commands simultaneously, the cluster uses a
**priority-encoded arbiter** (lowest tile index wins):

```
for i in 0..7:
    if tile_gc_cmd_valid[i] and no prior grant:
        forward tile i's command to GC engine top
        tile_gc_cmd_ready[i] = gc_cmd_ready
        break
```

All other tiles' `ready` signals remain low, causing those CPUs to stall in
`S_ENQ_WAIT` until the engine becomes available.

---

## Scanner Engine

**File:** `lm1_gc_scanner.sv` — 7 states, read-only.

The scanner walks a memory region object-by-object, reading each object's header
to determine its size, then examining each field. When it finds a reference
(pointer to another heap object), it emits the reference through a result
interface.

### Purpose

The scanner implements the **marking/tracing phase** of garbage collection.
It discovers all reachable references from a given memory region, populating
either a mark stack or a worklist for the copier.

### State Machine

```
         ┌──────┐
    ────▶│ IDLE │
         └──┬───┘
            │ cmd_valid
            ▼
      ┌───────────┐──── cur_addr >= region_end ────▶ DONE ──▶ IDLE
      │ READ_HDR  │
      └─────┬─────┘
            │ mem_rd_en
            ▼
      ┌───────────┐
      │ WAIT_HDR  │──── not a header ──▶ READ_HDR (skip)
      └─────┬─────┘
            │ valid header, extract obj_size
            ▼
      ┌───────────┐──── field_idx >= obj_size ──▶ READ_HDR (next object)
      │ READ_FIELD│
      └─────┬─────┘
            │ mem_rd_en
            ▼
      ┌───────────┐
      │ WAIT_FIELD│──── not a ref ──▶ READ_FIELD (skip)
      └─────┬─────┘
            │ is_any_ref(data)
            ▼
      ┌───────────┐
      │ EMIT_REF  │──── res_ready ──▶ READ_FIELD (next field)
      └───────────┘     (stall if FIFO full)
```

### Reference Detection

The scanner uses `is_any_ref()` to identify references:

```systemverilog
function automatic logic is_any_ref(logic [XLEN-1:0] w);
    return w[0] & ~w[2];   // bit[0]=1 AND bit[2]=0
endfunction
```

This matches:
- `TAG_REF` (3'b001) — general heap reference
- `TAG_CONS` (3'b011) — cons cell reference

It excludes:
- `TAG_SPECIAL` (3'b101) — immediate values like nil, t
- `TAG_HEADER` (3'b111) — header words
- Fixnums (bit[0]=0)

### Output: Scanner Results

Each discovered reference produces a 3-tuple:

| Signal | Width | Content |
|--------|-------|---------|
| `res_obj_addr` | 64 | Base address of the containing object |
| `res_field` | 16 | Field index within the object |
| `res_ref` | 64 | The tagged reference value |

These are buffered in a **128-entry FIFO** in the cluster module. The runtime
drains the FIFO via `SYS_INFO` sub-codes:

| SYS_INFO sub-code | Value | Description |
|-------------------|-------|-------------|
| `SYS_SCAN_COUNT` | 5'd11 | FIFO occupancy (0..128) |
| `SYS_SCAN_HEAD_OBJ` | 5'd12 | Head entry: object address |
| `SYS_SCAN_HEAD_FIELD` | 5'd13 | Head entry: field index |
| `SYS_SCAN_POP_REF` | 5'd14 | Head entry: ref value **+ pop** |

Reading `SYS_SCAN_POP_REF` returns the ref and atomically advances the
FIFO read pointer. The drain loop is:

```
while SYS_INFO(SYS_SCAN_COUNT) > 0:
    obj   = SYS_INFO(SYS_SCAN_HEAD_OBJ)
    field = SYS_INFO(SYS_SCAN_HEAD_FIELD)
    ref   = SYS_INFO(SYS_SCAN_POP_REF)   // also pops
    process(obj, field, ref)
```

### FIFO Backpressure

If the FIFO is full (`scan_fifo_count == 128`), `res_ready` deasserts and the
scanner stalls in `EMIT_REF` until a slot opens. This prevents reference loss
without requiring the scanner to have its own buffering.

### Cycle Costs

| Object | Cycles (approximate) |
|--------|---------------------|
| 1-word object (header only) | 4 (read header + overhead) |
| N-word object, K refs | 2N + 2K + 4 (read each field + emit each ref) |
| Non-ref field | 2 cycles (read + check) |
| Ref field | 2 + stall (read + check + emit, may stall on FIFO) |

---

## Copier Engine

**File:** `lm1_gc_copier.sv` — 12 states, read + write.

The copier relocates live objects from a source region to a destination region.
After copying each object, it overwrites the source header with a **forwarding
pointer** so that the fixup engine (and mutator barriers) can find the new
location.

### Purpose

The copier implements the **evacuation phase** of a copying or compacting GC.
It produces a contiguous, compacted copy of live objects in the destination
space, leaving forwarding pointers in the source space.

### State Machine

```
         ┌──────┐
    ────▶│ IDLE │
         └──┬───┘
            │ cmd_valid
            ▼
      ┌───────────┐──── src_addr >= src_end ────▶ DONE ──▶ IDLE
      │ READ_HDR  │
      └─────┬─────┘
            │ mem_rd_en
            ▼
      ┌───────────┐
      │ WAIT_HDR  │──── already forwarded (gc_bits=0xFF) ──▶ skip ──▶ READ_HDR
      └─────┬─────┘     or not a header ──▶ skip word ──▶ READ_HDR
            │ live object found
            ▼
      ┌───────────┐     Write header to dst_addr
      │ COPY_HDR  │────▶ WAIT_COPY_HDR
      └───────────┘
            │
            ▼ (if obj_size > 0)
      ┌───────────┐──── word_idx >= obj_size ──▶ INSTALL_FWD
      │ COPY_FIELD│
      └─────┬─────┘
            │ mem_rd_en
            ▼
      ┌────────────────┐
      │ WAIT_RD_FIELD  │──▶ WRITE_FIELD ──▶ WAIT_WR_FIELD ──▶ COPY_FIELD
      └────────────────┘    (word-by-word copy loop)
            
      ┌─────────────┐     Write fwd ptr to obj_src (old header)
      │ INSTALL_FWD │────▶ WAIT_FWD ──▶ READ_HDR (next object)
      └─────────────┘
```

### Forwarding Pointer Format

After copying an object, the copier overwrites the **original header** with a
forwarding pointer:

```
63       56  55                                       3   2  0
┌──────────┬──────────────────────────────────────────┬──────┐
│ 0xFF     │         new_address[55:3]                │  111 │
│ gc_bits  │         (destination address)            │ tag  │
└──────────┴──────────────────────────────────────────┴──────┘
```

- `gc_bits = 0xFF` distinguishes forwarding pointers from live headers
- Tag = `111` (header) so the word still type-checks as a header
- Bits [58:3] encode the new address (56 bits, 8-byte aligned)

The `make_fwd_ptr()` function constructs this:

```systemverilog
function automatic logic [XLEN-1:0] make_fwd_ptr(logic [XLEN-1:0] new_addr);
    return {8'hFF, new_addr[55:3], TAG_HEADER};
endfunction
```

### Copy Sequence (Per Object)

1. **Read header** from source (2 cycles)
2. **Check**: if already forwarded (gc_bits=0xFF), skip one word and
   continue scanning word-by-word (the forwarding pointer overwrites the
   size field, so object-sized skips are not possible)
3. **Write header** to destination (2 cycles)
4. **Copy payload**: for each word — read from source, write to destination (4 cycles per word)
5. **Install forwarding pointer** at source header location (2 cycles)

Total for an N-word object: ~4N + 6 cycles

### The `cmd_arg2` Size Parameter

The copier uniquely needs **three arguments**: source base, destination base,
and region size. The other engines only need two (base + size). The `cmd_arg2`
port was added through the entire command hierarchy (control FSM → CPU → tile →
cluster → GC engine top → copier) to support this.

In the CPU control FSM, `cmd_arg2` is sourced from the `rd` register field of
the `ENQ.COPY` instruction (the same register that normally receives results).

---

## Fixup Engine

**File:** `lm1_gc_fixup.sv` — 10 states, read + write.

The fixup engine walks a memory region and updates every reference that points
to a forwarded object. It reads the referent's header; if it's a forwarding
pointer, it rewrites the reference with the new address.

### Purpose

After the copier has relocated objects and installed forwarding pointers, the
fixup engine **patches all stale references** in the live heap. This is the
final phase that makes the copied objects reachable and the old locations
unreachable.

### State Machine

```
         ┌──────┐
    ────▶│ IDLE │
         └──┬───┘
            │ cmd_valid
            ▼
      ┌───────────┐──── scan_addr >= scan_end ────▶ DONE ──▶ IDLE
      │ READ_WORD │
      └─────┬─────┘
            │ mem_rd_en at scan_addr
            ▼
      ┌───────────┐
      │ WAIT_WORD │
      └─────┬─────┘
            │ latch cur_word
            ▼
      ┌───────────┐──── not a ref ──▶ ADVANCE ──▶ READ_WORD
      │ CHECK_REF │
      └─────┬─────┘
            │ is_any_ref(cur_word)
            ▼
      ┌──────────────────┐
      │ READ_TARGET_HDR  │  Read header at ref_address(cur_word)
      └────────┬─────────┘
               │ mem_rd_en
               ▼
      ┌──────────────────┐
      │ WAIT_TARGET_HDR  │──── not forwarded ──▶ ADVANCE
      └────────┬─────────┘
               │ gc_bits == 0xFF (forwarded!)
               ▼
      ┌───────────────┐     Write updated ref to scan_addr
      │ WRITE_UPDATED │────▶ WAIT_WRITE ──▶ ADVANCE
      └───────────────┘
```

### Reference Update Logic

When a forwarding pointer is found, the fixup engine constructs an updated
reference:

```systemverilog
function automatic logic [XLEN-1:0] update_ref(
    logic [XLEN-1:0] old_ref,        // the stale reference
    logic [XLEN-1:0] fwd              // the forwarding pointer
);
    logic [XLEN-1:0] new_addr;
    new_addr = fwd_new_addr(fwd);     // extract bits [55:3], shift left 3
    return {new_addr[63:3], old_ref[2:0]};  // new address + original tag
endfunction
```

**Key insight:** The original tag bits (ref vs cons) are preserved. The fixup
engine only changes the address portion, maintaining type correctness.

### Forwarding Pointer Detection

```systemverilog
is_forwarded = is_header(target_hdr) && (target_hdr[63:56] == 8'hFF);
```

This checks:
1. The referent's first word is a header (tag = `111`)
2. The gc_bits field is all-ones (`0xFF`)

If both conditions hold, the object has been relocated and the header contains
the new address.

### Forwarding Region Bounds Check

The fixup engine only follows refs that point into the **forwarding source
region** — the region where the copier installed forwarding pointers. This
prevents reading garbage from out-of-range SRAM addresses (which would wrap
modulo SRAM depth and could false-match the forwarding pointer pattern).

The `engine_top` module latches the copier's source base and end address
each time a `GC_CMD_COPY` or `GC_CMD_COMPACT` command is dispatched. These
are passed to the fixup engine as `fwd_region_base` / `fwd_region_end`.

```systemverilog
// In CHECK_REF:
if (is_any_ref(cur_word)) begin
    ref_addr = ref_address(cur_word);
    if (ref_addr >= fwd_region_base && ref_addr < fwd_region_end)
        → READ_TARGET_HDR   // might be forwarded
    else
        → ADVANCE           // different region, leave as-is
end
```

### Cycle Costs

| Word Type | Cycles |
|-----------|--------|
| Non-reference (fixnum, special, header) | 4 (read + check + advance) |
| Reference, not forwarded | 8 (read word + check + read target header + check + advance) |
| Reference, forwarded | 10 (read word + check + read target + check + write update + advance) |

For a region of N words with K forwarded references:
~4N + 4K_refs + 2K_forwarded cycles

---

## Memory Arbitration

### Read Priority

The three engines share a single read port on the cluster SRAM (port B).
Fixed priority with no starvation prevention:

```
1. Scanner  (highest)
2. Copier
3. Fixup    (lowest)
```

In practice this doesn't cause starvation because only one engine runs at
a time (mutual exclusion via `busy`). The priority only matters if future
revisions allow concurrent engine operation.

### Write Priority

Only the copier and fixup write memory. The scanner is read-only.

```
1. Copier   (highest)
2. Fixup    (lowest)
```

### Data Broadcast

All three engines receive `mem_rd_data` unconditionally (wired to all).
Only the selected engine's `mem_rd_valid` signal is asserted — the others
see `valid = 0` and ignore the data.

Because SRAM reads have 1-cycle latency, the arbiter **registers which
engine won the grant**. On the next cycle, `mem_rd_valid` is routed to
the registered winner (not to whichever engine currently asserts
`mem_rd_en`, which would be none — engines deassert `mem_rd_en` after
transitioning to their WAIT states).

### SRAM Port B Mux

In the cluster, port B of the dual-port SRAM handles both reads and writes:

```
if gc_mem_wr_en:
    port_B_addr  = gc_mem_wr_addr   (write address)
    port_B_we    = 1
    port_B_wdata = gc_mem_wr_data
else if gc_mem_rd_en:
    port_B_addr  = gc_mem_rd_addr   (read address)
    port_B_we    = 0
```

Writes take priority. If both a read and write happen on the same cycle,
the read is suppressed (`gc_mem_rd_valid_r` is gated by `!gc_mem_wr_en`).

---

## Write Barrier (Software + Hardware)

The write barrier is the mutator-side complement to the GC engines. It
ensures that cross-generation pointer stores are tracked so the GC knows
which old-generation objects may point to young-generation objects.

### ST.WB Instruction

`ST.WB` (Store With Barrier) is a tagged field store that additionally
performs a barrier check:

```
S_FIELD_MEM:   Store the value to memory (same as ST)
S_FIELD_WAIT:  Wait for store completion
S_BARRIER_CHECK:
    if is_any_ref(stored_value) AND target_addr < gen_boundary:
        // Old object references young object → mark card dirty
        card_addr = card_table_base + (target_addr >> card_shift)
        → S_BARRIER_MARK
    else:
        → S_FETCH (filtered out — no barrier needed)

S_BARRIER_MARK:  Store 0xFF to card_addr (byte store via LSU_STORE_BYTE)
S_BARRIER_MARK_W: Wait for card mark store
→ S_FETCH
```

### Card Table

The card table divides the heap into fixed-size **cards** (default
`1 << card_shift` bytes, default shift = 6 → 64-byte cards). Each card
has a 1-byte entry in the card table:

- `0x00` = clean (no cross-generation stores in this card)
- `0xFF` = dirty (at least one cross-generation store)

The GC runtime scans dirty cards during minor collections to find
old→young references without scanning the entire old generation.

### Configuration

| System Trap | Register | Description |
|-------------|----------|-------------|
| `TRAP 0x92` | `card_table_base = r1` | Base address of card table in memory |
| `TRAP 0x93` | `card_shift = r1[5:0]` | log₂(card_size) — default 6 |
| `TRAP 0x94` | `gen_boundary = r1` | Address boundary between generations |

### Performance Counters

| Counter | Incremented When |
|---------|-----------------|
| `ctr_barrier_fire` | Barrier detects cross-gen store → card marked |
| `ctr_barrier_filt` | Barrier check passed → no card mark needed |

These allow the runtime to measure barrier overhead and tune GC parameters
(card size, nursery size, generation boundary).

---

## End-to-End GC Flow

Here's how a minor (nursery) collection works with the hardware:

```
1. Mutator runs, allocating via ALLOC instructions
   NP (nursery pointer) advances toward NL (nursery limit)

2. NP reaches NL → TRAP_NURSERY_OVERFLOW
   Trap handler initiates GC

3. ENQ.SCAN with nursery region → Scanner walks nursery
   Scanner emits references into 128-entry FIFO
   (runs on SRAM port B, mutators can continue on port A)

4. Runtime drains scan FIFO, builds worklist of live objects

5. ENQ.COPY from nursery to survivor space
   Copier relocates live objects, installs forwarding pointers
   Source nursery headers now contain fwd ptrs (gc_bits=0xFF)

6. ENQ.FIXUP on old generation + stack regions
   Fixup engine patches references pointing to old nursery locations
   Each stale ref is updated with the new survivor-space address

7. FENCE.GC → stall until all engines complete

8. Reset NP to nursery base, NL stays the same
   Nursery is now empty and ready for reuse

9. ERET → return from trap handler, mutator resumes
```

The key advantage: steps 3, 5, and 6 run on dedicated hardware using
SRAM port B, allowing mutator threads on other tiles to continue executing
via SRAM port A and the crossbar. The GC pause is limited to the time
needed the runtime to drain the scan FIFO and issue commands — the bulk
of the work is done by the engines in the background.

---

## Limitations and Future Work

### Current Limitations

- **Sequential engine execution**: Only one engine runs at a time. A
  pipelined approach (scan → copy as refs are discovered) would reduce
  total GC time.

- **No concurrent marking**: The scanner must complete before the copier
  starts. A concurrent mark-copy pipeline would overlap phases.

- **Single shared SRAM**: All GC engines share one SRAM port. If engines
  ran concurrently, they'd contend on port B.

- **No hardware mark bits**: The scanner only reports references; it
  doesn't maintain a mark bitmap. The runtime must track liveness in
  software (or via the card table).

- **Copier skip cost for forwarded objects**: When encountering an
  already-forwarded header, the copier advances one word at a time
  (since the forwarding pointer overwrites the size field). This is
  correct but O(N) for an N-word forwarded object.

### Possible Extensions

- **Concurrent scan + copy pipeline** with a hardware worklist
- **Hardware mark bitmap** with atomic set/test operations
- **Generational hardware** (track object age in headers, auto-promote)
- **Incremental fixup** (fixup selected regions rather than full sweep)
- **Read barrier support** for concurrent/incremental collectors (like Azul's)
