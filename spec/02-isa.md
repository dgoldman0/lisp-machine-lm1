# LM-1 Instruction Set Architecture

**Spec ID:** LM1-SPEC-02  
**Revision:** 0.1-draft  
**Date:** 2026-02-16

---

## 1. Scope

This document specifies the LM-1 instruction set architecture. The ISA is organized into **8 semantic instruction families** that map to dynamic-language operations, plus a **supplementary scalar family** for systems-level code. Each family defines fast-path behavior (hardware) and slow-path behavior (trap to runtime).

## 2. General Conventions

### 2.1 Registers

| Register | Count | Description |
|----------|:-----:|-------------|
| `r0`–`r31` | 32 | General-purpose tagged-word registers |
| `sp` | 1 | Stack pointer (alias `r30`) |
| `fp` | 1 | Frame pointer (alias `r29`) |
| `lr` | 1 | Link register (alias `r28`) |
| `pc` | 1 | Program counter (not directly addressable) |
| `tp` | 1 | Thread/tile pointer (alias `r27`) — points to per-thread/tile state |
| `np` | 1 | Nursery pointer (alias `r26`) — current nursery bump pointer |
| `nl` | 1 | Nursery limit (alias `r25`) — end of nursery region |
| `ic0`–`ic3` | 4 | Inline-cache scratch registers (not general-purpose) |

All general-purpose registers hold 64-bit tagged words. The hardware thread context includes all 32 GPRs plus the special registers.

### 2.2 Operand Notation

```
Rd        — destination register
Rs, Rs1   — source register(s)
Rt        — target register (for stores: the value being stored)
imm       — immediate constant
field     — field index (unsigned immediate)
callsite  — inline-cache site identifier
tag       — tag constant for type testing
```

### 2.3 Traps and Slow Paths

When a fast-path condition is not met, the instruction raises a **synchronous trap** with a defined trap code. The runtime's trap handler receives:

- Trap code (identifying which slow path)
- Faulting instruction's PC
- All register state

Trap codes are enumerated per instruction family below.

### 2.4 Condition Codes

LM-1 does **not** use a flags register. Comparisons produce results in registers. Conditional branches test a register value directly.

---

## 3. Family 1: Tagged Arithmetic & Type Tests

### 3.1 Type Test Instructions

#### `TST Rd, Rs, #tag`

Test whether `Rs` matches the given tag class. Write `t` to `Rd` on match, `nil` on mismatch.

| Tag Constant | Matches |
|:--:|:--|
| `TAG_FIXNUM` | `(Rs & 1) == 0` |
| `TAG_REF` | `(Rs & 3) == 1` |
| `TAG_CONS` | `(Rs & 7) == 3` |
| `TAG_SPECIAL` | `(Rs & 7) == 5` |
| `TAG_NIL` | `Rs == 0x05` |
| `TAG_CHAR` | `(Rs & 0xFF) == 0x35` |
| `TAG_SFLOAT` | `(Rs & 0xFF) == 0x3D` |
| `TAG_HEADER` | `(Rs & 7) == 7` |

**Cycles:** 1  
**Traps:** None

#### `TST.SHAPE Rd, Rs, #shape_id`

Test whether the object referenced by `Rs` has the given shape ID. This first checks the shape-cache hint in the ref's metadata bits (no memory access). On hint match, write `t`; on hint mismatch, load the object header and compare the shape ID field. Write `t` on match, `nil` on mismatch.

**Precondition:** `Rs` MUST be a ref (tag `001` or `011`). Behavior is undefined if `Rs` is not a ref.

**Cycles:** 1 (hint hit), 1 + memory latency (hint miss, header load)  
**Traps:** None (non-ref operand is undefined behavior, not a trap)

### 3.2 Fixnum Arithmetic

All fixnum arithmetic instructions operate on tagged fixnums directly. They check that both operands are fixnums (bit 0 = 0), perform the operation, and check for overflow. On non-fixnum operand or overflow, they trap.

#### `ADD.FIX Rd, Rs1, Rs2`

```
if (Rs1 & 1) != 0 or (Rs2 & 1) != 0:
    trap TRAP_NOT_FIXNUM
result = Rs1 + Rs2    // tag bits cancel: (a<<1) + (b<<1) = (a+b)<<1
if overflow:
    trap TRAP_FIXNUM_OVERFLOW
Rd = result
```

**Cycles:** 1  
**Traps:** `TRAP_NOT_FIXNUM`, `TRAP_FIXNUM_OVERFLOW`

#### `SUB.FIX Rd, Rs1, Rs2`

Same structure as `ADD.FIX` with subtraction.

#### `MUL.FIX Rd, Rs1, Rs2`

```
if (Rs1 & 1) != 0 or (Rs2 & 1) != 0:
    trap TRAP_NOT_FIXNUM
a = Rs1 >> 1    // arithmetic shift to get untagged value
result = a * Rs2    // Rs2 is still shifted, so result = a*b << 1
if overflow:
    trap TRAP_FIXNUM_OVERFLOW
Rd = result
```

**Cycles:** 3–5 (depending on multiplier latency)  
**Traps:** `TRAP_NOT_FIXNUM`, `TRAP_FIXNUM_OVERFLOW`

#### `DIV.FIX Rd, Rs1, Rs2`

```
if (Rs1 & 1) != 0 or (Rs2 & 1) != 0:
    trap TRAP_NOT_FIXNUM
if Rs2 == 0:
    trap TRAP_DIVIDE_BY_ZERO
a = Rs1 >> 1
b = Rs2 >> 1
result = (a / b) << 1    // re-tag
Rd = result
```

**Cycles:** 8–32 (depending on divider)  
**Traps:** `TRAP_NOT_FIXNUM`, `TRAP_FIXNUM_OVERFLOW`, `TRAP_DIVIDE_BY_ZERO`

#### `ADD.FIX.IMM Rd, Rs, #imm`

Immediate-operand variant. `imm` is a pre-tagged fixnum constant.

#### `CMP.TAGGED Rd, Rs1, Rs2`

Tagged comparison with type-aware rules:
- fixnum–fixnum: numeric comparison
- ref–ref: pointer equality
- special–special: identity comparison
- mixed types: `TRAP_TYPE_MISMATCH`

Result in `Rd`: fixnum `-2` (less), `0` (equal), `+2` (greater) — pre-tagged.

**Cycles:** 1–2  
**Traps:** `TRAP_TYPE_MISMATCH`

#### `EQ Rd, Rs1, Rs2`

Raw word equality (identity / `eq`). No type checking. `Rd` = `t` if `Rs1 == Rs2`, else `nil`.

**Cycles:** 1  
**Traps:** None

---

## 4. Family 2: Allocation

### 4.1 `ALLOC Rd, #words, #header_template`

Allocate a fixed-size object in the tile's nursery.

```
if np + (words + 1) * 8 > nl:
    trap TRAP_NURSERY_OVERFLOW
*np = header_template    // write header word
Rd = np | TAG_REF        // tag as ref, add metadata hints
np = np + (words + 1) * 8
```

The `header_template` encodes the header subtype, shape ID, and size. GC bits in the header are initialized to the current generation's defaults.

**Cycles:** 1 (fast path)  
**Traps:** `TRAP_NURSERY_OVERFLOW` (runtime performs minor GC, then retries)

### 4.2 `ALLOC.CONS Rd`

Allocate a cons cell. Specialized for the most common allocation.

```
if np + CONS_SIZE > nl:
    trap TRAP_NURSERY_OVERFLOW
// If headerless cons is enabled:
Rd = np | TAG_CONS_REF
np = np + 16
// else: write cons header, Rd = np | TAG_CONS_REF, np += 24
```

**Cycles:** 1 (fast path)  
**Traps:** `TRAP_NURSERY_OVERFLOW`

### 4.3 `ALLOCV Rd, Rs_length, #header_template`

Allocate a variable-length object (vector, string, bytevector).

```
len = Rs_length >> 1    // untag fixnum length
total = header_words + len  // + length slot + elements
if np + total * 8 > nl:
    trap TRAP_NURSERY_OVERFLOW
*np = header_template (with size = total)
*(np + 8) = Rs_length    // store length as tagged fixnum
Rd = np | TAG_REF
np = np + total * 8
```

**Cycles:** 2 (fast path)  
**Traps:** `TRAP_NURSERY_OVERFLOW`, `TRAP_NOT_FIXNUM` (if length is not a fixnum)

### 4.4 `ALLOC.CLOSURE Rd, Rs_code, #env_size`

Allocate a closure with a code entry point and `env_size` environment slots.

```
total = 1 + 1 + env_size    // header + code + env slots
if np + total * 8 > nl:
    trap TRAP_NURSERY_OVERFLOW
*np = closure_header(size=total)
*(np + 8) = Rs_code
Rd = np | TAG_REF
np = np + total * 8
```

**Cycles:** 1–2 (fast path)  
**Traps:** `TRAP_NURSERY_OVERFLOW`

---

## 5. Family 3: Field Access with Barriers

### 5.1 `LD Rd, Rs, #field`

Load tagged word from object field.

```
addr = untag_ref(Rs) + (field + 1) * 8    // +1 to skip header
Rd = *(addr)
```

**Precondition:** `Rs` is a ref.

**Cycles:** 1 + memory latency  
**Traps:** `TRAP_NOT_REF` (if tag check is enabled)

### 5.2 `LD.CAR Rd, Rs` / `LD.CDR Rd, Rs`

Specialized loads for cons cells.

```
// LD.CAR
addr = untag_ref(Rs)
Rd = *(addr + 0)   // car is first word (headerless) or *(addr + 8) (with header)

// LD.CDR
Rd = *(addr + 8)   // cdr is second word (headerless) or *(addr + 16)
```

**Cycles:** 1 + memory latency  
**Traps:** `TRAP_NOT_CONS` (optional, if tag check is enabled)

### 5.3 `ST Rs, #field, Rt`

Store tagged word to object field. **No write barrier.**

```
addr = untag_ref(Rs) + (field + 1) * 8
*(addr) = Rt
```

Use only when the GC can prove no barrier is needed (e.g., initializing a newly allocated object before it becomes reachable).

**Cycles:** 1  
**Traps:** None

### 5.4 `ST.WB Rs, #field, Rt`

Store with **write barrier**. This is the primary store instruction for mutating live objects.

```
addr = untag_ref(Rs) + (field + 1) * 8
*(addr) = Rt

// Hardware barrier logic:
if Rt is a ref AND cross_generation(Rs, Rt):
    card_mark(addr)    // mark the card containing addr in the card table
    // and/or: add (Rs, field) to the remembered set
```

The barrier logic is performed in the store pipeline. The hardware checks:

1. Is `Rt` a ref? (bit 0 = 1, bits 2:1 indicate ref class)
2. Is this a cross-generation pointer? (compare GC generation bits in Rs and Rt metadata, or compare region boundaries)
3. If yes, update the card table and/or remembered set.

**Cycles:** 1–2 (fast path, card-mark hits store buffer)  
**Traps:** `TRAP_BARRIER_OVERFLOW` (if remembered set or card table overflows local capacity)

### 5.5 `ST.CAR Rs, Rt` / `ST.CDR Rs, Rt`

Barriered stores to cons cell fields. Equivalent to `ST.WB` with field 0/1 and cons-specific addressing.

---

## 6. Family 4: Dynamic Dispatch

### 6.1 `CALL.IC #callsite, Rs_receiver, #argc`

Inline-cached dynamic dispatch. This is the primary instruction for method calls, generic function dispatch, and message sends.

**Hardware behavior:**

```
shape = extract_shape_hint(Rs_receiver)    // from ref metadata bits
entry = ic_lookup(callsite, shape)         // probe hardware IC table

if entry.valid AND entry.shape == shape:
    // IC hit: direct call
    push_frame(pc + instruction_size, fp, argc)
    pc = entry.code_entry
    fp = sp
else:
    // IC miss: trap to runtime
    ic0 = callsite
    ic1 = Rs_receiver
    ic2 = argc
    trap TRAP_IC_MISS
```

The hardware IC table is a small associative structure (per-core or per-tile, see [03-core.md](03-core.md)). Each entry holds:

| Field | Bits | Description |
|-------|:----:|-------------|
| callsite | 16–24 | Callsite identifier |
| shape | 32 | Expected receiver shape ID |
| code_entry | 48 | Destination code address |
| valid | 1 | Entry is valid |

**IC table size:** Implementation-defined; RECOMMENDED minimum 64 entries per hardware thread.

**IC installation:** The runtime's `TRAP_IC_MISS` handler performs method lookup, resolves the target, and executes `IC.INSTALL` to populate the cache.

**Cycles:** 1–2 (IC hit), full trap latency (IC miss)  
**Traps:** `TRAP_IC_MISS`

### 6.2 `IC.INSTALL #callsite, #shape, Rs_entry`

Install or update an inline-cache entry. Privileged to the runtime.

```
ic_table[callsite] = { shape, Rs_entry, valid=1 }
```

**Cycles:** 1–2  
**Traps:** None (but may evict existing entries)

### 6.3 `CALL.DIRECT Rs_entry`

Direct (non-dispatched) function call.

```
push_frame(pc + instruction_size, fp)
pc = Rs_entry    // or untag if Rs_entry is a ref to a code object
fp = sp
```

**Cycles:** 1  
**Traps:** None

### 6.4 `CALL.CLOSURE Rs_closure, #argc`

Call a closure. Loads the code entry from the closure object and sets up the environment.

```
code = *(untag_ref(Rs_closure) + 8)    // code entry is first field
push_frame(pc + instruction_size, fp, argc)
tp.env = Rs_closure    // runtime can access captured vars via closure ref
pc = code
fp = sp
```

**Cycles:** 1 + memory latency (for code load)  
**Traps:** `TRAP_NOT_CLOSURE` (if tag/header check fails)

### 6.5 `RET`

Return from call. Pops the frame.

```
sp = fp
pc = pop_return_address()
fp = pop_saved_fp()
```

**Cycles:** 1  
**Traps:** `TRAP_STACK_UNDERFLOW`

### 6.6 `TAILCALL.IC #callsite, Rs_receiver, #argc`

Tail-call variant of `CALL.IC`. Reuses the current frame.

### 6.7 `TAILCALL.DIRECT Rs_entry`

Tail-call variant of `CALL.DIRECT`.

---

## 7. Family 5: Pointer Prefetch

### 7.1 `PREFETCH.REF Rs`

Prefetch the object header and first cache line of the object referenced by `Rs`.

```
if Rs is a ref:
    prefetch(untag_ref(Rs), hint=READ, lines=1)
// else: no-op (prefetching a non-ref is silently ignored)
```

**Cycles:** 1 (non-blocking)  
**Traps:** None

### 7.2 `PREFETCH.FIELD Rs, #field`

Prefetch a specific field of an object.

```
if Rs is a ref:
    addr = untag_ref(Rs) + (field + 1) * 8
    prefetch(addr, hint=READ)
```

**Cycles:** 1 (non-blocking)  
**Traps:** None

### 7.3 `PREFETCH.CDR Rs`

Specialized prefetch for list traversal. Loads the cdr field and prefetches the object it points to.

```
if Rs is a cons ref:
    cdr_addr = untag_ref(Rs) + CDR_OFFSET
    cdr_val = *(cdr_addr)    // this is a load, not a prefetch
    if cdr_val is a ref:
        prefetch(untag_ref(cdr_val), hint=READ, lines=1)
```

This is a **compound instruction**: it both loads the cdr and prefetches the next cons cell. It helps the pipeline stay ahead during list traversal.

**Cycles:** 1 (issues prefetch, cdr load is pipelined)  
**Traps:** None

### 7.4 `GATHER.PREFETCH Rs_base, Rs_index_vec, #count` *(Optional)*

Prefetch a batch of objects from an index vector. For each index `i` in `Rs_index_vec[0..count-1]`, prefetches the object at `Rs_base[i]`.

**Cycles:** `count` (pipelined)  
**Traps:** None  
**Conformance:** OPTIONAL (LM-1 Full)

---

## 8. Family 6: Concurrency & Messaging

### 8.1 `SEND Rs_queue, Rt_value`

Enqueue a tagged word onto a hardware message queue.

```
if queue_full(Rs_queue):
    trap TRAP_QUEUE_FULL
enqueue(Rs_queue, Rt_value)
```

Queues are identified by a queue descriptor (ref or special register). See [04-soc.md](04-soc.md) for queue topology.

**Cycles:** 1 (fast path, if queue not full)  
**Traps:** `TRAP_QUEUE_FULL`

### 8.2 `RECV Rd, Rs_queue`

Dequeue a tagged word from a hardware message queue.

```
if queue_empty(Rs_queue):
    trap TRAP_QUEUE_EMPTY   // or: block/yield hardware thread
Rd = dequeue(Rs_queue)
```

**Cycles:** 1 (fast path, if message available)  
**Traps:** `TRAP_QUEUE_EMPTY`

### 8.3 `TRY.RECV Rd, Rd2, Rs_queue`

Non-blocking receive. `Rd` gets the value (or `nil`), `Rd2` gets `t` (success) or `nil` (empty).

**Cycles:** 1  
**Traps:** None

### 8.4 `CAS.TAGGED Rd, Rs_addr, Rs_expected, Rt_new`

Compare-and-swap on a tagged word in memory.

```
old = *Rs_addr
if old == Rs_expected:
    *Rs_addr = Rt_new
    Rd = t
else:
    Rd = nil
```

If `Rt_new` is a ref and the store crosses generations, the write barrier is also triggered.

**Cycles:** Variable (depends on memory system)  
**Traps:** None (failure is reported via `Rd`)

### 8.5 `FAA Rd, Rs_addr, Rs_delta`

Fetch-and-add on a tagged fixnum in memory.

```
old = *Rs_addr
*Rs_addr = old + Rs_delta    // must both be fixnums; tag bits cancel
Rd = old
```

**Cycles:** Variable  
**Traps:** `TRAP_NOT_FIXNUM` (if old or delta is not a fixnum)

### 8.6 `FENCE.GC`

Memory fence for GC coordination. Ensures all pending stores (including barrier metadata updates) are visible before the GC phase transition.

**Cycles:** Variable (drain store buffer)  
**Traps:** None

---

## 9. Family 7: Region & Bulk Operations (Movement Engine)

These instructions do not execute on the DOP core's pipeline. They enqueue work items to the cluster's **movement engines** (see [04-soc.md](04-soc.md) § 4.2).

### 9.1 `ENQ.SCAN Rs_region, Rd_result`

Enqueue a region for pointer scanning. The movement engine scans all objects in the region, producing a list of pointer fields (for GC tracing).

```
enqueue_to_movement_engine(OP_SCAN, Rs_region)
// Rd_result receives a descriptor for the pending operation
// Completion is signaled via a queue or interrupt
```

**Cycles:** 1 (enqueue), actual work is asynchronous  
**Traps:** `TRAP_ENGINE_BUSY`

### 9.2 `ENQ.COPY Rs_src_region, Rd_dst_region`

Enqueue a region-to-region object copy. The movement engine copies all live objects from `Rs_src_region` to a new region, updating forwarding pointers.

**Cycles:** 1 (enqueue)  
**Traps:** `TRAP_ENGINE_BUSY`

### 9.3 `ENQ.FIXUP Rs_pointer_list, Rs_forwarding_table`

Enqueue a pointer fixup pass. The movement engine walks the pointer list and updates each pointer according to the forwarding table.

**Cycles:** 1 (enqueue)  
**Traps:** `TRAP_ENGINE_BUSY`

### 9.4 `ENQ.COMPACT Rs_region`

Enqueue compaction of a region (slide objects to eliminate fragmentation, update pointers).

**Cycles:** 1 (enqueue)  
**Traps:** `TRAP_ENGINE_BUSY`

### 9.5 `POLL.ENGINE Rd, Rs_descriptor`

Poll the status of an enqueued movement-engine operation.

```
Rd = status of operation Rs_descriptor
// t = complete, nil = still running, fixnum = error code
```

**Cycles:** 1  
**Traps:** None

### 9.6 `AWAIT.ENGINE Rs_descriptor`

Block the current hardware thread until the movement-engine operation completes.

**Cycles:** Variable (yields to other hardware threads)  
**Traps:** None

---

## 10. Family 8: Capability / Safety Mode *(Optional)*

These instructions are available only in **capability mode** (a per-tile or per-thread configuration). They extend the ref model with bounds, permissions, and compartmentalization, inspired by CHERI.

### 10.1 `MKCAP Rd, Rs_ref, Rs_bounds, Rs_perms`

Create a capability from a ref, bounds descriptor, and permission set.

### 10.2 `CHKCAP Rs_cap, #perm`

Check that a capability has the required permission. Trap on failure.

**Traps:** `TRAP_CAPABILITY_VIOLATION`

### 10.3 `NARROW Rd, Rs_cap, Rs_new_bounds`

Narrow a capability's bounds (can only shrink, never expand).

### 10.4 `SEAL Rd, Rs_cap, Rs_type`

Seal a capability, making it opaque and usable only via `CALL` (entry capability).

### 10.5 `UNSEAL Rd, Rs_cap, Rs_type`

Unseal a capability (requires matching type authority).

**Conformance:** All Family 8 instructions are OPTIONAL. Required for LM-1 Full conformance.

---

## 11. Supplementary: Scalar & Control

These are conventional instructions for systems code, boot, and interfacing.

### 11.1 Arithmetic (Untagged)

`ADD`, `SUB`, `MUL`, `DIV`, `MOD` — operate on raw 64-bit integers, ignoring tags. Used in the runtime, GC, and bootloader where tagged semantics are not wanted.

### 11.2 Bitwise

`AND`, `OR`, `XOR`, `SHL`, `SHR`, `ASR`, `NOT`

### 11.3 Load/Store (Raw)

`LDR Rd, [Rs, #offset]` — load raw 64-bit word  
`STR Rt, [Rs, #offset]` — store raw 64-bit word  
`LDB`, `STB` — byte load/store  
`LDH`, `STH` — halfword (16-bit) load/store  
`LDW`, `STW` — word (32-bit) load/store

### 11.4 Control Flow

`BR #offset` — unconditional branch  
`BR.T Rs, #offset` — branch if `Rs` is not `nil` and not fixnum `0`  
`BR.NIL Rs, #offset` — branch if `Rs` is `nil`  
`BR.FIX.LT Rs1, Rs2, #offset` — branch if fixnum less-than  
`BR.FIX.EQ Rs1, Rs2, #offset` — branch if fixnum equal  
`BR.FIX.GT Rs1, Rs2, #offset` — branch if fixnum greater-than  
`BR.EQ Rs1, Rs2, #offset` — branch if word-equal (`eq`)

### 11.5 Stack Operations

`PUSH Rs` — push to stack  
`POP Rd` — pop from stack  
`PUSH.MULTI #bitmask` — push multiple registers  
`POP.MULTI #bitmask` — pop multiple registers

### 11.6 System

`TRAP #code` — explicit trap (syscall-like)  
`ERET` — return from trap handler  
`HALT` — halt the current hardware thread  
`NOP` — no operation  
`TILE.ID Rd` — read current tile ID  
`THREAD.ID Rd` — read current hardware thread ID  
`CYCLE Rd` — read cycle counter

---

## 12. Trap Code Summary

| Code | Mnemonic | Source |
|:----:|----------|--------|
| 0x01 | `TRAP_NOT_FIXNUM` | Fixnum arithmetic on non-fixnum |
| 0x02 | `TRAP_FIXNUM_OVERFLOW` | Fixnum arithmetic overflow |
| 0x03 | `TRAP_DIVIDE_BY_ZERO` | Division by zero |
| 0x04 | `TRAP_TYPE_MISMATCH` | `CMP.TAGGED` on incompatible types |
| 0x05 | `TRAP_NOT_REF` | Ref-expecting op on non-ref |
| 0x06 | `TRAP_NOT_CONS` | Cons-expecting op on non-cons |
| 0x07 | `TRAP_NOT_CLOSURE` | Closure call on non-closure |
| 0x10 | `TRAP_NURSERY_OVERFLOW` | Nursery allocation failure |
| 0x20 | `TRAP_IC_MISS` | Inline cache miss |
| 0x30 | `TRAP_QUEUE_FULL` | Send to full queue |
| 0x31 | `TRAP_QUEUE_EMPTY` | Blocking receive from empty queue |
| 0x40 | `TRAP_ENGINE_BUSY` | Movement engine queue full |
| 0x50 | `TRAP_BARRIER_OVERFLOW` | Write barrier metadata overflow |
| 0x60 | `TRAP_CAPABILITY_VIOLATION` | Capability check failure |
| 0x70 | `TRAP_STACK_UNDERFLOW` | Return with empty stack |
| 0x80 | `TRAP_STACK_OVERFLOW` | Stack exceeds allocated region |
| 0xFF | `TRAP_USER` | Explicit `TRAP` instruction |

---

## 13. Instruction Encoding Overview

All instructions are fixed-width **32 bits**. The encoding format is detailed in [07-encoding.md](07-encoding.md). A summary:

```
 31    26 25                                  0
┌────────┬────────────────────────────────────┐
│ family │        family-specific fields      │
│ (6 bit)│                                    │
└────────┴────────────────────────────────────┘
```

The 6-bit family field gives 64 top-level opcodes, more than enough for the 8 semantic families + scalar/control ops. Each family uses its 26-bit payload differently (register fields, immediates, etc.).

---

## 14. Pseudocode for Key Operations

### 14.1 `(cons x y)` → `ALLOC.CONS` + `ST`

```asm
    ALLOC.CONS r3              ; r3 = new cons ref
    ST         r3, #0, r1     ; car = x (no barrier: freshly allocated, not yet reachable)
    ST         r3, #1, r2     ; cdr = y
    ; r3 is now (cons x y)
```

With headerless cons:
```asm
    ALLOC.CONS r3              ; r3 = new cons ref (16 bytes, no header)
    ST         r3, #0, r1     ; car = x (field 0 is at ref addr + 0)
    ST         r3, #1, r2     ; cdr = y (field 1 is at ref addr + 8)
```

### 14.2 `(car x)` → `LD.CAR`

```asm
    LD.CAR r1, r0              ; r1 = (car r0)
```

### 14.3 `(+ a b)` where a, b might not be fixnums

```asm
    ADD.FIX r3, r1, r2        ; fast path: both fixnums, no overflow
    ; if either is not fixnum: traps to TRAP_NOT_FIXNUM → runtime does generic +
    ; if overflow: traps to TRAP_FIXNUM_OVERFLOW → runtime promotes to bignum
```

### 14.4 Generic function call `(foo obj arg1 arg2)`

```asm
    ; r1 = obj (receiver), r2 = arg1, r3 = arg2, pushed or in regs per ABI
    CALL.IC #42, r1, #3       ; callsite 42, receiver = r1, 3 args
    ; on IC hit: direct jump to cached method
    ; on IC miss: TRAP_IC_MISS → runtime looks up method for foo on obj's class,
    ;             installs with IC.INSTALL, retries
```

### 14.5 Write-barriered mutation `(set-car! pair new-val)`

```asm
    ST.WB r1, #0, r2          ; r1 = pair, field 0 = car, r2 = new-val
    ; hardware handles barrier: if r2 is a young ref and r1 is old, card-mark
```
