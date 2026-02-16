# LM-1 SoC Architecture: Tiles, Clusters, and Fabric

**Spec ID:** LM1-SPEC-04  
**Revision:** 0.1-draft  
**Date:** 2026-02-16

---

## 1. Scope

This document specifies the system-on-chip (SoC) organization of an LM-1 processor: how tiles are composed into clusters, how clusters are interconnected via a network-on-chip (NoC), and how the system interfaces with HBM and external I/O.

## 2. Hierarchical Structure

```
┌──────────────────────────────────────────────────────────────────┐
│                          LM-1 SoC                                │
│                                                                  │
│  ┌──────────────────────┐    ┌──────────────────────┐            │
│  │      Cluster 0       │    │      Cluster 1       │            │
│  │  ┌──────┐ ┌──────┐   │    │  ┌──────┐ ┌──────┐   │   ...     │
│  │  │Tile 0│ │Tile 1│   │    │  │Tile 8│ │Tile 9│   │            │
│  │  └──────┘ └──────┘   │    │  └──────┘ └──────┘   │            │
│  │  ┌──────┐ ┌──────┐   │    │  ┌──────┐ ┌──────┐   │            │
│  │  │Tile 2│ │Tile 3│   │    │  │Tile10│ │Tile11│   │            │
│  │  └──────┘ └──────┘   │    │  └──────┘ └──────┘   │            │
│  │  ┌──────┐ ┌──────┐   │    │  ┌──────┐ ┌──────┐   │            │
│  │  │Tile 4│ │Tile 5│   │    │  │Tile12│ │Tile13│   │            │
│  │  └──────┘ └──────┘   │    │  └──────┘ └──────┘   │            │
│  │  ┌──────┐ ┌──────┐   │    │  ┌──────┐ ┌──────┐   │            │
│  │  │Tile 6│ │Tile 7│   │    │  │Tile14│ │Tile15│   │            │
│  │  └──────┘ └──────┘   │    │  └──────┘ └──────┘   │            │
│  │                       │    │                       │            │
│  │  ┌─────────────────┐  │    │  ┌─────────────────┐  │            │
│  │  │ Cluster Shared  │  │    │  │ Cluster Shared  │  │            │
│  │  │ SRAM (2 MiB)    │  │    │  │ SRAM (2 MiB)    │  │            │
│  │  └─────────────────┘  │    │  └─────────────────┘  │            │
│  │  ┌─────────────────┐  │    │  ┌─────────────────┐  │            │
│  │  │Movement Engines │  │    │  │Movement Engines │  │            │
│  │  │(scan/copy/fixup)│  │    │  │(scan/copy/fixup)│  │            │
│  │  └─────────────────┘  │    │  └─────────────────┘  │            │
│  │  ┌───────────┐        │    │  ┌───────────┐        │            │
│  │  │Cluster DMA│        │    │  │Cluster DMA│        │            │
│  │  └───────────┘        │    │  └───────────┘        │            │
│  └──────────────────────┘    └──────────────────────┘            │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                    Network-on-Chip (NoC)                    │  │
│  │              2D mesh, QoS lanes, wormhole routing           │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ HBM PHY 0│ │ HBM PHY 1│ │ HBM PHY 2│ │ HBM PHY 3│           │
│  │ (8 GiB)  │ │ (8 GiB)  │ │ (8 GiB)  │ │ (8 GiB)  │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐            │
│  │  PCIe    │ │ Ethernet │ │ SIMD/Tensor Sidekick │            │
│  │ Gen5 x16 │ │  100GbE  │ │   Tiles (optional)   │            │
│  └──────────┘ └──────────┘ └──────────────────────┘            │
└──────────────────────────────────────────────────────────────────┘
```

## 3. System Parameters

### 3.1 Reference Configuration: LM-1 Standard

| Parameter | Value | Notes |
|-----------|-------|-------|
| Tiles per cluster | 8 | 2×4 mesh within cluster |
| Clusters | 32 | 4×8 cluster mesh |
| Total tiles | 256 | |
| HW threads per tile | 4 | |
| Total HW threads | 1024 | |
| Tile local SRAM | 256 KiB | Nursery + stacks + hot data |
| Cluster shared SRAM | 2 MiB | Old-gen region + caches + queues |
| Total on-chip SRAM | 256×256K + 32×2M = **65.5 MiB + 64 MiB = ~130 MiB** | |
| HBM capacity | 32 GiB (4 stacks × 8 GiB) | |
| HBM bandwidth | 1.6 TB/s (aggregate) | |
| NoC topology | 2D mesh, hierarchical | |
| Die area (est.) | ~400–600 mm² at 5nm | |
| TDP (est.) | 150–300 W | |

### 3.2 Scaling

| Variant | Tiles | Clusters | SRAM | HBM | Target |
|---------|:-----:|:--------:|:----:|:---:|--------|
| LM-1 Minimal | 16 | 2 | ~5 MiB | 8 GiB | Development, embedded |
| LM-1 Standard | 256 | 32 | ~130 MiB | 32 GiB | Workstation, server |
| LM-1 Full | 1024+ | 128+ | ~500+ MiB | 128 GiB | HPC, large-scale symbolic AI |

---

## 4. Tile Architecture

### 4.1 Tile Block Diagram

```
┌─────────────────────────────────────────────────┐
│                     Tile                         │
│                                                  │
│  ┌───────────────────────────────────┐           │
│  │          DOP Core                 │           │
│  │  (4 HW threads, 6-stage pipeline,│           │
│  │   tag unit, IC unit, allocator)   │           │
│  └──────────┬───────────┬────────────┘           │
│             │           │                        │
│     ┌───────▼───┐  ┌────▼────────┐               │
│     │  I-Cache  │  │  SRAM Port  │               │
│     │  (8 KiB)  │  │             │               │
│     └───────────┘  └────┬────────┘               │
│                         │                        │
│  ┌──────────────────────▼────────────────────┐   │
│  │              Tile SRAM (256 KiB)          │   │
│  │                                            │   │
│  │  ┌──────────┐ ┌─────────┐ ┌────────────┐  │   │
│  │  │ Nursery  │ │ Stacks  │ │ Hot Objects │  │   │
│  │  │ (64 KiB) │ │(64 KiB) │ │ (96 KiB)   │  │   │
│  │  └──────────┘ └─────────┘ └────────────┘  │   │
│  │  ┌──────────────┐ ┌───────────────────┐   │   │
│  │  │ Card Table   │ │ Message Queues    │   │   │
│  │  │ (8 KiB)      │ │ (16 KiB)         │   │   │
│  │  └──────────────┘ └───────────────────┘   │   │
│  │  ┌──────────────────────────────────────┐ │   │
│  │  │ Scratch / Runtime Data (8 KiB)       │ │   │
│  │  └──────────────────────────────────────┘ │   │
│  └───────────────────────────────────────────┘   │
│                                                  │
│  ┌───────────────┐  ┌─────────────────────────┐  │
│  │  Tile DMA     │  │  NoC Router Port        │  │
│  │  Endpoint     │  │  (2 VCs: mutator + GC)  │  │
│  └───────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### 4.2 SRAM Regions

The 256 KiB tile SRAM is **software-partitioned** into regions. The runtime configures region boundaries at tile initialization. Default layout:

| Region | Size | Purpose |
|--------|:----:|---------|
| **Nursery** | 64 KiB | Bump-pointer young-generation heap. `np` starts at base, `nl` is at base + size. |
| **Stacks** | 64 KiB | Thread stacks (16 KiB per HW thread × 4 threads) |
| **Hot data** | 96 KiB | Frequently accessed objects, closures, environments. Managed by runtime placement policy. |
| **Card table** | 8 KiB | Write-barrier card table for nursery + hot-data regions. 1 byte per 64-byte card. |
| **Message queues** | 16 KiB | Hardware-managed FIFO queues for SEND/RECV. 4 queues × 4 KiB per queue, or configurable. |
| **Scratch** | 8 KiB | Runtime metadata, trap tables, tile-local variables. |

### 4.3 Nursery Lifecycle

1. **Allocation phase:** `ALLOC` bumps `np`. Objects accumulate in the nursery.
2. **Overflow:** When `np` reaches `nl`, `TRAP_NURSERY_OVERFLOW` fires.
3. **Minor GC:** The runtime (or a movement engine) scans the nursery, copies survivors to the cluster's shared SRAM (old-gen region), resets `np` to nursey base.
4. **Card table reset:** After minor GC, the card table is cleared.

### 4.4 Message Queue Hardware

Each tile has **4 hardware message queues**, configurable as input or output:

| Queue | Default Role |
|:-----:|-------------|
| Q0 | **Work queue in** — receives tasks from the scheduler |
| Q1 | **Work queue out** — sends results / completed tasks |
| Q2 | **GC coordination** — receives GC phase transition signals |
| Q3 | **General / user** — application-level inter-tile messaging |

Queue entries are single tagged words (64 bits). Queue depth: configurable, default 512 entries (4 KiB).

**Flow control:** `SEND` to a full queue traps (`TRAP_QUEUE_FULL`). The runtime can either back-pressure (stall the sender) or spill to SRAM.

### 4.5 Tile DMA Endpoint

The DMA endpoint handles explicit data movement:

- **Tile → Cluster SRAM:** Object promotion (nursery → old-gen), spilling cold data
- **Cluster SRAM → Tile:** Fetching objects, loading code, filling caches
- **Tile → Tile:** Direct message passing (bypasses cluster for adjacent tiles)
- **Cluster → HBM:** Spilling old-gen to cold heap
- **HBM → Tile:** Paging in cold objects

DMA commands are enqueued by the core or by movement engines. Each command specifies source address, destination address, length, and a completion signal (interrupt or queue message).

---

## 5. Cluster Architecture

### 5.1 Cluster Block Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         Cluster                              │
│                                                              │
│  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐  │
│  │ T0 │ │ T1 │ │ T2 │ │ T3 │ │ T4 │ │ T5 │ │ T6 │ │ T7 │  │
│  └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘  │
│     │      │      │      │      │      │      │      │      │
│  ┌──▼──────▼──────▼──────▼──────▼──────▼──────▼──────▼──┐   │
│  │              Cluster Crossbar (8-port)                 │   │
│  └──┬────────────────┬────────────────┬──────────────┬───┘   │
│     │                │                │              │        │
│  ┌──▼──────────┐  ┌──▼──────────┐  ┌─▼────────┐  ┌─▼─────┐ │
│  │Cluster SRAM │  │  Movement   │  │ Cluster  │  │ NoC   │ │
│  │  (2 MiB)    │  │  Engines    │  │ DMA to   │  │Uplink │ │
│  │             │  │             │  │ HBM      │  │       │ │
│  │ ┌─────────┐ │  │ ┌─────────┐ │  └──────────┘  └───────┘ │
│  │ │Old-Gen  │ │  │ │ Scanner │ │                           │
│  │ │Region   │ │  │ ├─────────┤ │                           │
│  │ │(1.5 MiB)│ │  │ │ Copier  │ │                           │
│  │ ├─────────┤ │  │ ├─────────┤ │                           │
│  │ │Method   │ │  │ │ Fixup   │ │                           │
│  │ │Cache    │ │  │ │ Engine  │ │                           │
│  │ │(256 KiB)│ │  │ └─────────┘ │                           │
│  │ ├─────────┤ │  └─────────────┘                           │
│  │ │Symbol   │ │                                            │
│  │ │Table    │ │                                            │
│  │ │(128 KiB)│ │                                            │
│  │ ├─────────┤ │                                            │
│  │ │Work Q   │ │                                            │
│  │ │(128 KiB)│ │                                            │
│  │ └─────────┘ │                                            │
│  └─────────────┘                                            │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Cluster Shared SRAM (2 MiB)

| Region | Size | Purpose |
|--------|:----:|---------|
| **Old-gen region** | 1.5 MiB | Objects promoted from tile nurseries. Managed by the cluster's movement engines. |
| **Method cache** | 256 KiB | Software-managed cache of method-lookup results. Shared by all tiles in the cluster. Reduces `TRAP_IC_MISS` cost for cold dispatch sites. |
| **Symbol table** | 128 KiB | Interned symbols, package tables. Read-heavy, rarely written. |
| **Work queues** | 128 KiB | Scheduler's pending-task queues for the cluster's tiles. |

Access latency from a tile: **4–8 cycles** (intra-cluster crossbar).

### 5.3 Movement Engines

Each cluster has **3 movement engines**, each a dedicated finite-state machine (not a programmable core):

#### 5.3.1 Scanner Engine

- **Input:** A region descriptor (base address, size).
- **Operation:** Walks every object in the region. For each object:
  1. Reads the header to determine size and layout.
  2. Scans all fields, identifying refs (tag check on each word).
  3. Outputs a list of `(object, field, target_ref)` tuples to a scan-results buffer.
- **Output:** A pointer list (stored in SRAM or streamed to another engine).
- **Throughput:** 1 word per cycle (limited by SRAM read bandwidth).

#### 5.3.2 Copier Engine

- **Input:** A region to evacuate, a destination region.
- **Operation:** For each live object (marked by scanner or mark bit):
  1. Copies the object to the destination region (bump allocation).
  2. Installs a forwarding pointer in the source location.
- **Output:** A forwarding table (old_address → new_address).
- **Throughput:** 1 word per cycle, burst-optimized.

#### 5.3.3 Fixup Engine

- **Input:** A pointer list and a forwarding table.
- **Operation:** For each pointer in the list:
  1. Loads the pointer value.
  2. Looks up the old address in the forwarding table.
  3. If found, writes the new address.
- **Output:** All pointers updated.
- **Throughput:** 1 fixup per 2–3 cycles (load + lookup + store).

**Coordination:** Engines are sequenced by the runtime via `ENQ.SCAN` → `ENQ.COPY` → `ENQ.FIXUP` commands. They signal completion via queue messages or interrupts.

### 5.4 Cluster Crossbar

An **8-port non-blocking crossbar** connecting tiles to cluster resources:

| Port | Connected To |
|:----:|-------------|
| 0–7 | Tile 0–7 SRAM ports |
| S | Cluster shared SRAM |
| M | Movement engines |
| D | Cluster DMA (to NoC / HBM) |

**Bandwidth:** 64 bits per port per cycle at core clock frequency.  
**Arbitration:** Round-robin with priority boost for GC-related traffic.

---

## 6. Network-on-Chip (NoC)

### 6.1 Topology

**2D mesh** at the cluster level. Each cluster has a NoC router connecting it to its 4 neighbors (north, south, east, west) and optionally diagonal.

For a 32-cluster configuration (4×8 grid):
- Maximum hop count: 10 (corner to corner)
- Average hop count: ~5

### 6.2 Virtual Channels (QoS Lanes)

The NoC supports **3 virtual channels** to prevent deadlock and provide QoS:

| VC | Traffic Class | Priority | Description |
|:--:|:----------:|:--------:|-------------|
| VC0 | **Mutator** | Normal | Application data movement, object fetches, message passing |
| VC1 | **GC** | High | GC scanning, copying, fixup traffic. Must not be blocked by mutator traffic. |
| VC2 | **Control** | Highest | Tile management, configuration, debug, interrupt delivery |

### 6.3 Packet Format

```
┌──────────┬──────────┬──────────┬──────────────────────────────┐
│ VC (2b) │ Src (10b)│ Dst (10b)│         Payload (64–512b)    │
├──────────┴──────────┴──────────┴──────────────────────────────┤
│                      Flit structure                           │
│  Header flit: VC, routing info, length                        │
│  Body flits: 64-bit data words                                │
│  Tail flit: EOM marker                                        │
└───────────────────────────────────────────────────────────────┘
```

- **Flit width:** 64 bits (matches word size).
- **Routing:** Dimension-ordered (XY) for deadlock freedom, with adaptive routing as an OPTIONAL extension.
- **Flow control:** Credit-based, per-VC.

### 6.4 Latency

| Path | Hops | Latency (est.) |
|------|:----:|:--------------:|
| Intra-cluster (tile-to-tile) | 0 | 1–2 cycles (crossbar) |
| Adjacent clusters | 1 | 5–8 cycles |
| Cross-chip (worst case) | 10 | 25–40 cycles |
| Tile to HBM controller | 3–6 | 15–25 cycles (NoC) + 50–100 cycles (HBM) |

---

## 7. HBM Interface

### 7.1 HBM Controllers

The SoC has **4 HBM controllers**, one per die edge, each managing one HBM stack.

| Parameter | Value |
|-----------|-------|
| HBM generation | HBM3E |
| Stacks | 4 |
| Capacity per stack | 8 GiB |
| Total capacity | 32 GiB |
| Bandwidth per stack | 400 GB/s |
| Total bandwidth | 1.6 TB/s |

### 7.2 HBM Usage

| Usage | Description |
|-------|-------------|
| **Cold heap** | Objects that have survived multiple GC generations and are not accessed frequently. |
| **Bulk arrays** | Large vectors, strings, bytevectors that don't fit in cluster SRAM. |
| **Code storage** | Compiled code segments, loaded into tile I-caches on demand. |
| **Persistent data** | Symbol tables, class hierarchies, system images. |
| **GC metadata** | Forwarding tables, mark bitmaps for full-heap collection. |

### 7.3 Address Mapping

The runtime maps the HBM address space into regions:

```
HBM Address Space (32 GiB)
├── 0x0000_0000_0000 ─ 0x0004_0000_0000  Code (16 GiB max)
├── 0x0004_0000_0000 ─ 0x0007_0000_0000  Cold Heap (12 GiB)
├── 0x0007_0000_0000 ─ 0x0007_8000_0000  GC Metadata (2 GiB)
└── 0x0007_8000_0000 ─ 0x0008_0000_0000  System / I/O (2 GiB)
```

---

## 8. Optional SIMD/Tensor Sidekick Tiles

For numeric workloads (array processing, ML inference), the LM-1 Full configuration includes **sidekick tiles** with wide SIMD/tensor units.

### 8.1 Sidekick Tile Architecture

| Parameter | Value |
|-----------|-------|
| ALU width | 256-bit SIMD (4×64, 8×32, 16×16, 32×8) |
| Local SRAM | 512 KiB |
| Threads | 1 (no FGMT — these are throughput units, not latency-hiding) |
| ISA | Subset of LM-1 scalar + vector extension |

### 8.2 Invocation

DOP tiles invoke sidekick tiles by sending a **work descriptor** via the NoC:

```
work_descriptor = {
    op: VECTOR_ADD,
    src1: ref_to_vector_a,
    src2: ref_to_vector_b,
    dst: ref_to_vector_c,
    length: n
}
SEND q_sidekick, work_descriptor_ref
```

The sidekick tile DMAs the vector data, performs the operation, DMAs results back, and sends a completion message.

---

## 9. External I/O

### 9.1 PCIe

One PCIe Gen5 x16 controller for:
- Host CPU communication (if LM-1 is an accelerator)
- NVMe storage access
- Network (if no dedicated Ethernet)

### 9.2 Ethernet

One or more 100 GbE ports for:
- Distributed Lisp systems (multi-chip)
- Network services

### 9.3 UART / Debug

- JTAG debug port
- UART for boot console
- Trace port for performance monitoring

### 9.4 Display Controller

A hardware display controller (the **VDI engine**, named after GEM's Virtual Device Interface) provides framebuffer-based output without consuming DOP tile cycles for pixel pushing.

#### 9.4.1 Architecture

```
┌────────────────────────────────────────────────────────────┐
│                   VDI Display Engine                        │
│                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │   Scanout     │  │   Blit/Fill  │  │ Cursor / Sprite  │ │
│  │   Controller  │  │   Accelerator│  │   Overlay        │ │
│  │  (reads FB,   │  │  (rect fill, │  │  (HW cursor,     │ │
│  │   generates   │  │   bitblt,    │  │   1 sprite plane) │ │
│  │   pixel clock) │  │   ROP ops)  │  │                  │ │
│  └───────┬───────┘  └──────┬──────┘  └────────┬─────────┘ │
│          │                 │                   │            │
│  ┌───────▼─────────────────▼───────────────────▼────────┐  │
│  │           Display SRAM (2 MiB)                        │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │  │
│  │  │ Framebuffer 0│ │ Framebuffer 1│ │ Blit Scratch │   │  │
│  │  │ (up to 1 MiB)│ │ (up to 1 MiB)│ │  (256 KiB)  │   │  │
│  │  └──────────────┘ └──────────────┘ └──────────────┘   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌───────────┐  ┌───────────┐  ┌──────────────────────┐   │
│  │  NoC Port  │  │  HDMI/DP  │  │  MMIO Register Bank │   │
│  │ (DMA in)   │  │  PHY      │  │  (tile-accessible)  │   │
│  └───────────┘  └───────────┘  └──────────────────────┘   │
└────────────────────────────────────────────────────────────┘
```

#### 9.4.2 Display Modes

| Mode | Resolution | Depth | FB Size | Refresh |
|------|:----------:|:-----:|:-------:|:-------:|
| Text/debug | 640×480 | 8-bit indexed | 300 KiB | 60 Hz |
| Desktop | 1024×768 | 8-bit indexed | 768 KiB | 60 Hz |
| Desktop hi-color | 1024×768 | 16-bit (5-6-5) | 1.5 MiB (double-buffered partial) | 60 Hz |
| Retina/4K | Delegated to external GPU via PCIe | — | — | — |

The on-die VDI engine targets **classic Atari ST / early Mac resolution** at high quality. For 4K output, an external GPU receives blitted frame data via PCIe DMA or a dedicated display link.

#### 9.4.3 MMIO Registers

Tiles interact with the VDI engine via memory-mapped I/O registers in the system region of the HBM address space:

| Register | Offset | Description |
|----------|:------:|-------------|
| `VDI_MODE` | 0x00 | Resolution/depth selector |
| `VDI_FB_BASE` | 0x08 | Active framebuffer base address |
| `VDI_FB_STRIDE` | 0x10 | Bytes per scanline |
| `VDI_PALETTE` | 0x18–0x218 | 256-entry CLUT (32-bit RGBA per entry) |
| `VDI_CURSOR_X/Y` | 0x220/0x228 | Hardware cursor position |
| `VDI_CURSOR_DATA` | 0x230 | Cursor bitmap base (32×32×2bpp) |
| `VDI_BLIT_SRC` | 0x300 | Blit source address |
| `VDI_BLIT_DST` | 0x308 | Blit destination address |
| `VDI_BLIT_SIZE` | 0x310 | Blit width/height |
| `VDI_BLIT_ROP` | 0x318 | Raster operation (GXcopy, GXxor, etc.) |
| `VDI_BLIT_GO` | 0x320 | Write to trigger blit; read for busy flag |
| `VDI_VSYNC` | 0x328 | VSync counter / interrupt control |

#### 9.4.4 Blit Accelerator

The blit engine supports GEM-style raster operations:

- **Rect fill:** solid color or pattern fill to any FB rectangle
- **BitBLT:** source-to-destination block transfer with 16 raster ops (GXcopy, GXor, GXxor, GXand, GXinvert, etc.)
- **Color expansion:** 1-bit source to N-bit dest (for font rendering)
- **Clipping:** hardware clip rectangle register

Throughput: **1 pixel per clock** for simple ops, **2 clocks per pixel** for ROP-blended operations.

#### 9.4.5 Emulation

In the emulator, the VDI engine is modeled as a memory-mapped device. The emulator renders the framebuffer to a host window (via SDL/Pygame) or a VNC/web server. Blit operations execute immediately (no pipelining).

---

## 10. Boot Sequence

1. **Power-on reset:** All tiles held in reset. HBM controllers initialize.
2. **Boot ROM:** A small ROM on the SoC loads the initial runtime image from flash/NVMe into HBM.
3. **Bootstrap tile:** Tile 0 is released from reset. It runs the bootloader, which:
   a. Initializes HBM memory map.
   b. Loads the runtime image from HBM into cluster shared SRAMs.
   c. Configures tile SRAM regions (nursery, stacks, etc.).
   d. Starts the scheduler on a set of bootstrap tiles.
4. **Tile bringup:** The scheduler releases remaining tiles, each of which:
   a. Loads its trap table and runtime stubs from cluster SRAM.
   b. Configures `np`/`nl` for its nursery.
   c. Enters the work-stealing loop (receives tasks from Q0).
5. **System ready:** The runtime signals readiness. The REPL / application entry point is invoked.

---

## 11. Power Management

### 11.1 Power Domains

| Domain | Granularity | Description |
|--------|:-----------:|-------------|
| Tile | Per-tile | Clock gating when no threads are runnable |
| Cluster | Per-cluster | Power gating when all tiles in cluster are idle |
| HBM | Per-stack | Reduced refresh rate when stack is mostly unused |
| NoC | Per-link | Clock gating on idle links |

### 11.2 DVFS

The SoC supports **2–3 voltage/frequency operating points**:

| Point | Frequency | Voltage | Use Case |
|:-----:|:---------:|:-------:|----------|
| High | 1.5 GHz | 0.85 V | Full mutator throughput |
| Normal | 1.0 GHz | 0.70 V | Steady-state, balanced power |
| Low | 0.5 GHz | 0.55 V | GC scanning, idle, background |

Tiles performing GC work MAY run at the Low point, saving power while the scanner/copier engines (which are bandwidth-limited, not frequency-limited) are active.

---

## 12. Physical Design Considerations

### 12.1 Floorplan

```
┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
│ C0   │ C1   │ C2   │ C3   │ HBM  │ HBM  │      │      │
│      │      │      │      │ PHY0 │ PHY1 │ PCIe │ Eth  │
├──────┼──────┼──────┼──────┼──────┴──────┤      │      │
│ C4   │ C5   │ C6   │ C7   │            │      │      │
│      │      │      │      │   SIMD     │      │      │
├──────┼──────┼──────┼──────┤  Sidekick  ├──────┴──────┤
│ C8   │ C9   │ C10  │ C11  │  Region    │            │
│      │      │      │      │            │  Boot ROM  │
├──────┼──────┼──────┼──────┼──────┬─────┤  + Debug   │
│ C12  │ C13  │ C14  │ C15  │ HBM  │ HBM │            │
│      │      │      │      │ PHY2 │ PHY3│            │
└──────┴──────┴──────┴──────┴──────┴─────┴────────────┘
```

HBM PHYs on the die edges (top-left, top-right, bottom-left, bottom-right). Clusters fill the interior. I/O on one edge. Sidekick region is flexible.

### 12.2 Die Area Estimate (5nm)

| Component | Area (mm²) | % of Die |
|-----------|:----------:|:--------:|
| 256 DOP tiles (core + 256K SRAM) | 200 | 40% |
| 32 cluster shared SRAMs (2M each) | 80 | 16% |
| 32 × 3 movement engines | 15 | 3% |
| NoC routers + links | 25 | 5% |
| 4 HBM PHYs | 40 | 8% |
| SIMD sidekick tiles (opt) | 40 | 8% |
| PCIe + Ethernet + misc I/O | 20 | 4% |
| Power delivery + clocking | 30 | 6% |
| Boot ROM + debug + reserve | 50 | 10% |
| **Total** | **~500** | **100%** |
