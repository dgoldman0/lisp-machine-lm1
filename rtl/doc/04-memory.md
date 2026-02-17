# LM-1 Memory Subsystem

## Memory Hierarchy Overview

```
┌────────────────────────────────────────────────────────┐
│                       Cluster                          │
│                                                        │
│  ┌──────────┐  ┌──────────┐       ┌──────────┐        │
│  │  Tile 0  │  │  Tile 1  │  ...  │  Tile 7  │        │
│  │          │  │          │       │          │        │
│  │ ┌──────┐ │  │ ┌──────┐ │       │ ┌──────┐ │        │
│  │ │ICache│ │  │ │ICache│ │       │ │ICache│ │        │
│  │ │ 8KiB │ │  │ │ 8KiB │ │       │ │ 8KiB │ │        │
│  │ └──────┘ │  │ └──────┘ │       │ └──────┘ │        │
│  │ ┌──────┐ │  │ ┌──────┐ │       │ ┌──────┐ │        │
│  │ │ SRAM │ │  │ │ SRAM │ │       │ │ SRAM │ │        │
│  │ │256KiB│ │  │ │256KiB│ │       │ │256KiB│ │        │
│  │ └──┬───┘ │  │ └──┬───┘ │       │ └──┬───┘ │        │
│  └────┼─────┘  └────┼─────┘       └────┼─────┘        │
│       │              │                  │              │
│  ─────┴──────────────┴──────────────────┴───────       │
│                    Crossbar                            │
│  ──────────────────────┬───────────────────────        │
│                        │                               │
│                   ┌────┴─────┐                         │
│                   │  Shared  │                         │
│                   │  SRAM    │   ← dual-port           │
│                   │  2 MiB   │   (port A: crossbar     │
│                   │          │    port B: GC engines)   │
│                   └──────────┘                         │
│                                                        │
│  ┌──────────────────────────────────────────────┐      │
│  │            GC Engines                        │      │
│  │  Scanner  │  Copier  │  Fixup                │      │
│  └──────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────┘
```

---

## Tile-Local SRAM

Each tile contains a single-port SRAM (`lm1_sram_sp`) that serves as the
primary memory for the CPU core.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DEPTH` | 2^16 = 65,536 words | 65K × 64-bit = 512 KiB |
| `XLEN` | 64 | Word width |
| `MEM_DEPTH_LOG2` | 16 | Address bits for local SRAM |

Note: The tile parameter `MEM_DEPTH_LOG2` defaults to 15 (32K words = 256 KiB)
in the tile wrapper, but the CPU module defaults to 16 (64K words = 512 KiB).
The actual deployed size depends on synthesis parameters.

### SRAM Interface

```systemverilog
module lm1_sram_sp #(
    parameter DEPTH = 65536,
    parameter WIDTH = 64
) (
    input  logic                     clk,
    input  logic                     en,        // chip enable
    input  logic                     we,        // write enable
    input  logic [$clog2(DEPTH)-1:0] addr,      // word address
    input  logic [WIDTH-1:0]         wdata,     // write data
    input  logic [WIDTH/8-1:0]       be,        // byte enables
    output logic [WIDTH-1:0]         rdata      // read data (1-cycle latency)
);
```

- **Read latency**: 1 clock cycle (synchronous read)
- **Write latency**: 0 cycles (combinational write, data available next cycle)
- **Byte enables**: 8-bit `be` vector, one per byte of the 64-bit word
- **Technology**: Synthesizes to block RAM on FPGA, SRAM macros on ASIC

### Byte-Enable Semantics

The `be` vector gates individual byte writes within a 64-bit word. For each bit:
- `be[i] = 1`: byte `i` of `wdata` is written to memory
- `be[i] = 0`: byte `i` of memory is preserved

This enables sub-word stores (byte, halfword, word) without read-modify-write
cycles.

---

## Address Decode

### Local vs Cluster Address

The CPU uses a simple address decode to determine whether a memory access
targets local tile SRAM or the cluster-shared SRAM:

```systemverilog
assign lsu_is_cluster_addr = |mem_addr_lsu[XLEN-1:MEM_DEPTH_LOG2];
```

If **any** address bit above the local SRAM depth is set, the access is
routed to the cluster crossbar. Otherwise, it accesses local tile SRAM.

For `MEM_DEPTH_LOG2 = 15` (256 KiB):
- Addresses `0x00000` – `0x07FFF` → local SRAM (32K words)
- Addresses `0x08000` and above → cluster crossbar

### Read Data Mux

```systemverilog
assign mem_rdata = lsu_is_cluster_addr ? xbar_resp_data : sram_rdata;
```

The LSU doesn't distinguish between local and cluster accesses — it issues
the same request either way. The CPU top-level routes the request to the
correct port and muxes the response back.

### Memory Map (Tile-Centric View)

```
0x0000_0000_0000_0000 ┬───────────────┐
                      │  Local SRAM   │ 256 KiB (32K × 64-bit words)
0x0000_0000_0000_7FFF ┤               │
                      ├───────────────┤
0x0000_0000_0000_8000 │  Cluster SRAM │ Via crossbar
                      │  region       │ 2 MiB (256K × 64-bit words)
                      │               │
                      ├───────────────┤
                      │  Other tiles  │ Via crossbar + NoC (future)
                      │               │
                      └───────────────┘
```

---

## Cluster-Shared SRAM

The cluster contains a **dual-port** SRAM (`lm1_sram_dp`) shared by all
tiles and the GC engines.

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DEPTH` | 2^18 = 262,144 words | 262K × 64-bit = 2 MiB |
| `WIDTH` | 64 | Word width |

### Dual-Port Interface

```systemverilog
module lm1_sram_dp #(
    parameter DEPTH = 262144,
    parameter WIDTH = 64
) (
    input  logic clk,
    // Port A (Crossbar / CPU access)
    input  logic                     a_en,
    input  logic                     a_we,
    input  logic [$clog2(DEPTH)-1:0] a_addr,
    input  logic [WIDTH-1:0]         a_wdata,
    input  logic [WIDTH/8-1:0]       a_be,
    output logic [WIDTH-1:0]         a_rdata,
    // Port B (GC engine access)
    input  logic                     b_en,
    input  logic                     b_we,
    input  logic [$clog2(DEPTH)-1:0] b_addr,
    input  logic [WIDTH-1:0]         b_wdata,
    input  logic [WIDTH/8-1:0]       b_be,
    output logic [WIDTH-1:0]         b_rdata
);
```

**Port A** is connected to the cluster crossbar, serving CPU memory requests.
**Port B** is connected to the GC engine top, allowing concurrent GC operations
without stalling CPU accesses.

### Why Dual-Port?

The GC engines need to read and write memory independently of CPU activity.
With a single-port SRAM, GC operations would either stall CPU cores or require
time-multiplexing (halving effective bandwidth). The dual-port design allows
GC engines to scan, copy, and fixup objects in the background while all 8
tile CPUs continue executing at full speed.

This is one of the most important hardware design decisions in the LM-1 —
it directly enables concurrent, low-pause garbage collection.

---

## Crossbar

`lm1_crossbar.sv` — Round-robin arbiter connecting tiles to shared SRAM.

### Architecture

```
        Tile 0    Tile 1    Tile 2   ...   Tile 7
          │         │         │               │
          ▼         ▼         ▼               ▼
    ┌─────────────────────────────────────────────┐
    │           Round-Robin Arbiter               │
    │                                             │
    │  grant = round_robin_select(valid_requests) │
    │  rr_ptr increments on each grant            │
    └──────────────────┬──────────────────────────┘
                       │
                       ▼
                ┌──────────────┐
                │ Shared SRAM  │
                │  Port A      │
                └──────────────┘
```

### Parameters

| Parameter | Value |
|-----------|-------|
| `N_TILES` | 8 |
| `XLEN` | 64 |
| `MEM_DEPTH_LOG2` | 18 (256K words) |

### Protocol

1. Tile asserts `req_valid[i]`, `req_we[i]`, `req_addr[i]`, `req_wdata[i]`
2. Arbiter grants one tile per cycle (round-robin, starting from `rr_ptr`)
3. Granted tile's signals drive shared SRAM port A
4. One cycle later, `resp_valid[granted]` asserts with `resp_data[granted]`
5. `rr_ptr` advances to `(granted + 1) % N_TILES`

### Arbitration Logic

```systemverilog
// Round-robin scan from rr_ptr
for (int k = 0; k < N_TILES; k++) begin
    int idx = (rr_ptr + k) % N_TILES;
    if (req_valid[idx] && !granted) begin
        grant = idx;
        granted = 1;
    end
end
```

### Limitations

- **Single outstanding request**: Each tile can have at most 1 pending
  crossbar request. The LSU stalls until the response arrives.
- **No pipelining**: Grant → SRAM access → response takes 2 cycles minimum.
- **No write buffering**: Write requests are acknowledged immediately but
  still consume a grant cycle.
- **8-tile contention**: In worst case, a tile waits 7 cycles for its turn.
  With FGMT (4 threads), this is masked by thread switching.

---

## Memory Port Priority in the CPU

The tile SRAM has a single port shared by three consumers. The CPU's memory
port mux resolves contention with a fixed priority scheme:

```
Priority 1 (highest): External memory port
    Used for: program loading, debug reads/writes
    Signals:  ext_mem_en, ext_mem_we, ext_mem_addr, ext_mem_wdata
    BE:       0xFF (always full-word)

Priority 2: I-Cache fill sequencer
    Used for: filling cache lines on miss
    Signals:  icfill_sram_en, icfill_sram_addr
    Access:   Read-only (loads 8 words × 64-bit per fill)

Priority 3 (lowest): LSU (local access only)
    Used for: CPU load/store instructions
    Signals:  lsu_mem_en, lsu_mem_we, lsu_mem_addr, lsu_mem_wdata
    Access:   Read/write with byte enables

Not routed to local SRAM: cluster addresses
    Cluster-bound accesses bypass local SRAM entirely
    and route through the crossbar interface.
```

### Contention Behavior

When the I-Cache fill sequencer is active (filling a cache line), LSU requests
to local SRAM are blocked. This means a cache-miss fill (16 cycles for 8 words)
can stall pending local loads/stores. However, cluster-bound accesses are
unaffected since they use a separate path.

External memory access (program loading) has highest priority and can preempt
both I-Cache fills and LSU operations. In practice, external access only
happens before program execution begins.

---

## Sub-Word Memory Access

The LM-1 supports byte (8-bit), halfword (16-bit), and word (32-bit) loads
and stores in addition to the native 64-bit word operations.

### Store Path

Sub-word stores use byte enables to write a subset of the 64-bit word:

```
STORE_BYTE (at addr):
  be    = 8'h01 << addr[2:0]          (1 byte at offset 0-7)
  wdata = {8{value[7:0]}}             (byte replicated 8x)

STORE_HALF (at addr):
  be    = 8'h03 << {addr[2:1], 1'b0}  (2 bytes, aligned to halfword)
  wdata = {4{value[15:0]}}            (halfword replicated 4x)

STORE_WORD (at addr):
  be    = addr[2] ? 8'hF0 : 8'h0F     (upper or lower 4 bytes)
  wdata = {2{value[31:0]}}            (word replicated 2x)
```

The replication strategy means the correct data is always present in the
lane that the byte enable selects. This avoids a barrel shifter on the
write path.

### Load Path

Sub-word loads extract the requested bytes from the full 64-bit read:

```
LOAD_BYTE:
  result = {56'b0, rdata[addr[2:0]*8 +: 8]}     (select 1 byte, zero-extend)

LOAD_HALF:
  offset = {addr[2:1], 1'b0}
  result = {48'b0, rdata[offset*8 +: 16]}        (select 2 bytes, zero-extend)

LOAD_WORD:
  result = addr[2] ? {32'b0, rdata[63:32]}       (upper word)
                   : {32'b0, rdata[31:0]}         (lower word)
```

All sub-word loads are **zero-extended** (not sign-extended). The compiler
must add explicit sign extension if needed.

### Instruction Fetch

Instruction fetch (`LSU_OP_IFETCH`) uses the same word-select logic as
`LOAD_WORD` — it extracts a 32-bit instruction from either the upper or
lower half of the 64-bit SRAM word, based on `addr[2]`:

```
inst = addr[2] ? rdata[63:32] : rdata[31:0]
```

This is because instructions are 32 bits but SRAM words are 64 bits, so
each SRAM word holds 2 instructions.

---

## I-Cache Organization

### Structure

```
┌─────────────────────────────────────────────────────────────┐
│                     I-Cache (8 KiB)                         │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Line 0:  Tag  V  │ Word0 │ Word1 │...│ Word7 │       │  │
│  │ Line 1:  Tag  V  │ Word0 │ Word1 │...│ Word7 │       │  │
│  │ Line 2:  Tag  V  │ Word0 │ Word1 │...│ Word7 │       │  │
│  │  ...                                                  │  │
│  │ Line 15: Tag  V  │ Word0 │ Word1 │...│ Word7 │       │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  16 lines × 8 words × 64 bits = 8192 bytes                 │
│  Line select: addr[9:6]   (4 bits for 16 lines)            │
│  Word select: addr[5:3]   (3 bits for 8 words)             │
│  Byte select: addr[2:0]   (3 bits, instruction alignment)  │
│  Tag:         addr[XLEN-1:10]                               │
└─────────────────────────────────────────────────────────────┘
```

### Hit Logic

```systemverilog
line_idx   = fetch_addr[9:6];
word_idx   = fetch_addr[5:3];
tag_match  = (tag_store[line_idx] == fetch_addr[XLEN-1:10]);
cache_hit  = tag_match && valid[line_idx];
fetch_data = data_store[line_idx][word_idx];  // 64-bit word
```

The fetched 64-bit word contains two 32-bit instructions. The CPU selects
the correct instruction using `addr[2]` (see instruction fetch above).

### Fill Protocol

On a miss:
1. I-Cache asserts `fill_req` with `fill_addr` = missed address
2. Fill sequencer reads 8 consecutive words from tile SRAM
3. Each word is written to the cache via `fill_valid` + `fill_data`
4. On the 8th word, `fill_done` asserts
5. I-Cache marks the line valid and retries the fetch

### Coherence

There is no cache coherence protocol. The I-Cache must be explicitly
invalidated (flushed) when code is modified in memory. This is acceptable
because the LM-1 targets GC'd language runtimes where code is typically
immutable after loading.

---

## Memory Consistency Model

The LM-1 has a **sequentially consistent** memory model within a single
tile (single SRAM port, no store buffer, no write-back cache). Cross-tile
consistency through the cluster crossbar is naturally serialized by the
round-robin arbiter.

For cross-cluster consistency (future NoC), the `CAS_TAGGED` and `FAA`
instructions provide atomic read-modify-write operations, and explicit
fence instructions would be needed.

### Atomics

**CAS_TAGGED (Compare-And-Swap):**
```
FSM sequence:
  S_MEM:      Load old value from mem[addr]
  S_MEM_WAIT: Compare old value with expected
              If match: store new value, Rd = old value (success)
              If no match: Rd = old value (failure)
```

Since the tile SRAM is single-ported and the FSM is non-interruptible
across the load-compare-store sequence, CAS is naturally atomic within
a tile. Cross-tile atomicity would require crossbar-level support (not
yet implemented).

**FAA (Fetch-And-Add):**
```
FSM sequence:
  S_MEM:      Load old value from mem[addr]
  S_MEM_WAIT: Write (old + delta) to mem[addr]
              Rd = old value
```

Same atomicity semantics as CAS — atomic within tile, requires
protocol extension for cross-tile atomicity.
