# LM-1 BIOS / Firmware Design

**Status:** Design sketch  
**Date:** 2026-02-16

---

## 1. What "BIOS" Means Here

On a conventional machine, the BIOS/firmware initializes hardware, runs POST, and loads the OS bootloader. The LM-1 BIOS does the same thing, but in a Lisp-machine context:

1. **Initialize the tile fabric** — configure SRAM regions, set up nursery pointers, install trap tables
2. **Bring up enough runtime to allocate and dispatch** — the BIOS is the *first* code that uses `ALLOC` and `CALL.IC`
3. **Load and jump to the OS image** — from block storage (or embedded in the BIOS for early bringup)

The BIOS is **not** Lisp. It's hand-written LM-1 assembly. It's the minimal scaffolding to get the system to the point where Lisp code can run.

## 2. Memory Map at Power-On

When the emulator starts (or when real hardware comes out of reset), memory looks like:

```
Tile 0 SRAM (256 KiB):
  0x0000_0000 – 0x0000_FFFF  Nursery (64 KiB)     — uninitialized
  0x0001_0000 – 0x0001_FFFF  Stacks (64 KiB)      — uninitialized
  0x0002_0000 – 0x0002_7FFF  Hot data (96 KiB)     — uninitialized
  0x0002_8000 – 0x0002_9FFF  Card table (8 KiB)    — uninitialized
  0x0002_A000 – 0x0002_DFFF  Queues (16 KiB)       — uninitialized
  0x0002_E000 – 0x0002_FFFF  Scratch (8 KiB)       — uninitialized
  0x0003_0000 – 0x0003_FFFF  BIOS code (loaded by reset vector)

Cluster 0 Shared SRAM (2 MiB):
  0x0100_0000 – 0x01FF_FFFF  Uninitialized

HBM (64 MiB in emulator):
  0x1000_0000 – 0x13FF_FFFF  Available
```

**Reset vector:** PC starts at `0x0003_0000` (BIOS entry point in tile 0 SRAM). The emulator/hardware pre-loads the BIOS binary there.

## 3. BIOS Phases

### Phase 1: Bare Metal Init

Runs on Tile 0, Thread 0. No Lisp, no allocation, no dispatch. Just raw scalar instructions.

```asm
bios_entry:
    ;; ---- Phase 1: Bare metal init ----
    
    ;; Set up stack pointer for thread 0
    LI      sp, 0x0001_3FFF         ; top of thread 0's 16K stack area
    LI      fp, 0                    ; no frame yet
    
    ;; Set up nursery
    LI      np, 0x0000_0000         ; nursery base
    LI      nl, 0x0000_FFFF         ; nursery limit (64K)
    
    ;; Clear card table (zero 8K)
    LI      r1, 0x0002_8000         ; card table base
    LI      r2, 0x0002_9FFF         ; card table end
    LI      r3, 0                   ; zero
.clear_cards:
    STR     r3, [r1, #0]
    ADD     r1, r1, #8
    BR.FIX.LT r1, r2, .clear_cards
    
    ;; Install trap table
    LI      r1, trap_table_base
    ;; (store trap_table_base to the per-thread trap table register)
    TRAP    #0x90                   ; emulator: set trap table base
    
    ;; Initialize header template table
    ;; Entry 0: cons header
    LI      r1, 0                   ; template index 0
    ;; Load the 64-bit cons header constant (need two LI+LUI or a load from BIOS data)
    LI      r2, CONS_HEADER_LO
    LUI     r2, CONS_HEADER_HI
    TRAP    #0x91                   ; emulator: install header template r1=index, r2=value
    
    ;; ... more templates for closure, vector, symbol, etc.
```

### Phase 2: Trap Table Installation

The trap table maps trap codes to handler addresses. The BIOS installs minimal handlers:

```
trap_table:
    ;; Each entry is 8 bytes: the handler address (raw, untagged)
    ;; Index = trap code >> 4 (or direct mapping, TBD)
    
    TRAP_NOT_FIXNUM:      .quad   trap_not_fixnum_handler
    TRAP_FIXNUM_OVERFLOW:  .quad   trap_fixnum_overflow_handler
    TRAP_DIVIDE_BY_ZERO:   .quad   trap_divide_by_zero_handler
    TRAP_NURSERY_OVERFLOW: .quad   trap_nursery_gc_handler
    TRAP_IC_MISS:          .quad   trap_ic_miss_handler
    TRAP_QUEUE_FULL:       .quad   trap_queue_full_handler
    TRAP_QUEUE_EMPTY:      .quad   trap_queue_empty_handler
    TRAP_STACK_OVERFLOW:   .quad   trap_stack_overflow_handler
    ;; ... etc
```

**BIOS trap handlers are minimal:**

- `TRAP_NOT_FIXNUM` → print error message, halt (no generic arithmetic yet)
- `TRAP_FIXNUM_OVERFLOW` → print error, halt (no bignums yet)
- `TRAP_NURSERY_OVERFLOW` → **the important one** — run a minimal GC (see § 4)
- `TRAP_IC_MISS` → **the other important one** — basic method lookup (see § 5)
- Everything else → print trap code, halt

### Phase 3: Minimal GC Bootstrap

The BIOS GC is the simplest possible stop-and-copy collector:

```
trap_nursery_gc_handler:
    ;; Save all registers to stack (they're roots)
    PUSH.MULTI #0xFFFFFFFF
    
    ;; Destination: cluster shared SRAM old-gen region
    LI      r10, 0x0100_0000       ; old-gen base
    LDR     r11, [tp, #old_gen_ptr] ; current old-gen bump pointer
    
    ;; Scan roots: walk the stack, find all refs, copy their targets
    ;; For each word on the stack:
    ;;   if it's a ref (tag check), and it points into the nursery:
    ;;     copy the object to old-gen
    ;;     install forwarding pointer at old location
    ;;     update the stack word to point to new location
    ;;     recursively scan the copied object's fields
    
    ;; (This is Cheney's algorithm: use the old-gen copy area as its own worklist)
    
    ;; ... implementation ...
    
    ;; Reset nursery
    LI      np, 0x0000_0000
    
    ;; Restore registers
    POP.MULTI #0xFFFFFFFF
    
    ;; Retry the allocation that triggered this
    ERET
```

This is ~100–200 instructions of careful assembly. It's the hardest part of the BIOS to get right, but it's also the first thing the OS will replace with a proper GC.

### Phase 4: Minimal IC / Dispatch Bootstrap

The BIOS IC miss handler supports only a trivial dispatch model: look up a method in a flat association list.

```
trap_ic_miss_handler:
    ;; ic0 = callsite, ic1 = receiver, ic2 = argc
    ;; 
    ;; Get receiver's shape from its object header
    LD      r5, ic1, #-1            ; load header (field -1, i.e., the header word)
    ;; Extract shape ID from header
    ;; ... bit manipulation ...
    
    ;; Look up in the method table (a flat list in cluster SRAM for now)
    ;; method_table: ((shape . ((callsite . entry) ...)) ...)
    LDR     r6, [tp, #method_table_ptr]
    
.search_shape:
    BR.NIL  r6, .method_not_found
    LD.CAR  r7, r6                  ; r7 = (shape . methods)
    LD.CAR  r8, r7                  ; r8 = shape
    ;; compare r8 with extracted shape
    ;; ... if match, search the methods alist ...
    ;; ... if found, IC.INSTALL and ERET ...
    LD.CDR  r6, r6
    BR      .search_shape
    
.method_not_found:
    ;; Print error: "No method for shape X at callsite Y"
    TRAP    #0x80                   ; print to console
    HALT
```

### Phase 5: Console I/O

The BIOS provides two primitives via dedicated functions (not `TRAP` — these are normal LM-1 functions once dispatch works):

```
bios_putchar:
    ;; r1 = character (tagged as special-immediate character)
    ;; Extract the code point from the tagged value
    SHR     r2, r1, #8             ; code point is in bits 63:8
    TRAP    #0x80                   ; emulator I/O: print char in r2
    RET

bios_getchar:
    TRAP    #0x81                   ; emulator I/O: read char into r0
    ;; Tag it as a character
    SHL     r0, r0, #8
    OR      r0, r0, #0x35          ; tag = 101, subtype = 00110
    RET
```

### Phase 6: Image Loader

Once the BIOS can allocate, dispatch, and do I/O, it loads the OS image:

```
load_os_image:
    ;; Read image header from block 0
    LI      r1, 0                   ; block number
    LI      r2, 0x1000_0000         ; destination: HBM base
    TRAP    #0x82                   ; emulator: read block

    ;; Parse image header
    ;; Verify magic "LM1I"
    LDR     r3, [r2, #0]
    LI      r4, LM1I_MAGIC
    ;; ... compare r3 == r4 ...
    
    ;; Read remaining blocks into HBM
    LDR     r5, [r2, #24]          ; heap_size from image header
    ;; ... read ceil(heap_size / 4096) blocks ...
    
    ;; Relocate: walk the relocation table, adjust addresses
    ;; ... for each relocation entry, add the load base offset ...
    
    ;; Jump to the OS entry point
    LDR     r6, [r2, #16]          ; entry_point from image header
    ADD     r6, r6, r2             ; relocate
    CALL.DIRECT r6                 ; hand off to the OS

    ;; Never returns. If it does:
    HALT
```

## 4. BIOS Data Structures

The BIOS establishes these data structures in SRAM before jumping to the OS:

| Structure | Location | Format | Purpose |
|-----------|----------|--------|---------|
| Trap table | Tile scratch | Array of 256 addresses | Maps trap codes to handlers |
| Header template table | Tile scratch | Array of 64-bit header words | Referenced by ALLOC instructions |
| Method table | Cluster SRAM | Alist of (shape . ((callsite . entry) ...)) | Minimal dispatch table for BIOS-level Lisp |
| Boot info block | Cluster SRAM | Flat struct | Passed to OS: memory map, tile count, BIOS version |
| Symbol table (minimal) | Cluster SRAM | Alist of (name-hash . symbol-ref) | NIL, T, CONS, CAR, CDR, +, -, PRINT, READ |

## 5. What the BIOS Hands to the OS

When the OS entry point is called, it receives (via registers and the boot info block):

```
r1 = ref to boot-info object
    boot_info.tile_count       -- how many tiles are available
    boot_info.memory_map       -- ref to a vector of region descriptors
    boot_info.bios_version     -- fixnum
    boot_info.trap_table       -- ref to current trap table (OS will replace it)
    boot_info.symbol_table     -- ref to BIOS symbol table (OS will extend it)
    boot_info.console_putchar  -- ref to BIOS putchar function
    boot_info.console_getchar  -- ref to BIOS getchar function
    boot_info.block_read       -- ref to BIOS block-read function
    boot_info.block_write      -- ref to BIOS block-write function
```

The OS can use BIOS I/O functions during its own initialization, then replace them with its own drivers.

## 6. BIOS Size Estimate

| Component | Lines of Assembly | Words of Code |
|-----------|:-----------------:|:-------------:|
| Phase 1: Hardware init | 50 | ~50 |
| Phase 2: Trap table install | 30 | ~30 + table |
| Phase 3: GC (Cheney's) | 150 | ~150 |
| Phase 4: IC miss handler | 80 | ~80 |
| Phase 5: Console I/O | 20 | ~20 |
| Phase 6: Image loader | 100 | ~100 |
| Data: trap table, templates, symbols | — | ~200 |
| **Total** | **~430** | **~630 words ≈ 5 KiB** |

Fits easily in the 64 KiB BIOS area of tile 0 SRAM.

## 7. Bringup Strategy

| Step | Milestone | Tests |
|------|-----------|-------|
| B1 | Phase 1 runs: stack set up, nursery configured | Emulator debugger shows correct register state |
| B2 | Trap table installed, traps fire and reach handlers | Force a `TRAP #0xFF`, verify handler runs |
| B3 | `ALLOC.CONS` works, nursery bumps | Allocate 100 cons cells, verify heap layout |
| B4 | Nursery overflows, GC runs, survivors in old-gen | Allocate with live root on stack, verify GC preserves it |
| B5 | Console output: `bios_putchar` prints "LM-1" | See output on emulator terminal |
| B6 | `CALL.IC` dispatches (with manual IC install) | Call a known function via IC, verify correct target |
| B7 | IC miss handler looks up method table | Call with cold IC, verify trap → lookup → install → hit |
| B8 | Image loader reads blocks, loads to HBM | Load a trivial test image, verify contents in HBM |
| B9 | Jump to OS entry point, OS prints "booting" | Full boot chain: emulator → BIOS → OS init |
