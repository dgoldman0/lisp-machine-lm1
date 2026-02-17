# LM-1 ISA & Tag System

## The Tagged Word Model

Every value in the LM-1 — in registers, on the stack, in memory — is a **64-bit tagged word**. The low 3 bits encode the type, and the hardware interprets these bits on every operation.

### Tag Encoding

```
63                                                           3  2  1  0
┌──────────────────────────────────────────────────────────────┬──┬──┬──┐
│                     Payload (61-63 bits)                      │  Tag  │
└──────────────────────────────────────────────────────────────┴──┴──┴──┘
```

| Tag Bits | Type | Encoding Details |
|----------|------|------------------|
| `bit[0] = 0` | **Fixnum** | 63-bit signed integer. Value = `word >>> 1` (arithmetic shift). Tag is just the LSB being 0. This gives fixnums a 62-bit signed range [-2^62, 2^62-1]. |
| `bits[1:0] = 01` | **Reference** | Heap pointer. `bits[50:3]` = 48-bit byte address (word-aligned). `bits[63:51]` = metadata (shape hint, GC generation, capability bits). |
| `bits[2:0] = 011` | **Cons Reference** | Specialized reference to a cons cell (Lisp pair). Same address layout as ref, but tag distinguishes cons pointers for fast `consp` checks. |
| `bits[2:0] = 101` | **Special** | Immediate singleton values. `bits[7:3]` = 5-bit subtype. No heap allocation needed. |
| `bits[2:0] = 111` | **Header** | Memory-only word (never in registers during normal execution). Marks the beginning of a heap object. |

### Why This Tag Scheme?

The fixnum encoding (`bit[0] = 0`) is the most important design choice. Because the LSB is the tag, fixnums are simply even numbers. `tag_fixnum(v) = v << 1`. `untag_fixnum(w) = w >>> 1`. Fixnum addition is just regular addition (the tags cancel out: `(a<<1) + (b<<1) = (a+b)<<1`). Only overflow detection requires tag awareness.

The 3-bit scheme allows single-cycle type dispatch:
```systemverilog
// Hardware type test — purely combinational
function automatic logic is_fixnum(logic [63:0] w);
    return ~w[0];           // Just check bit 0
endfunction

function automatic logic is_any_ref(logic [63:0] w);
    return (w[1:0] == 2'b01);   // Bits [1:0] = 01
endfunction

function automatic logic is_nil(logic [63:0] w);
    return (w == 64'h0000_0000_0000_0005);  // The specific nil constant
endfunction
```

### Special Values

| Name | Hex Value | Tag Bits | Subtype |
|------|-----------|----------|---------|
| `nil` | `0x0000_0000_0000_0005` | `101` | 0 |
| `t` | `0x0000_0000_0000_000D` | `101` | 1 |
| `unbound` | `0x0000_0000_0000_0015` | `101` | 2 |
| `eof` | `0x0000_0000_0000_001D` | `101` | 3 |
| `void` | `0x0000_0000_0000_0025` | `101` | 4 |
| `undefined` | `0x0000_0000_0000_002D` | `101` | 5 |
| Character base | `0x35` | `101` | 6 (+ char code in upper bits) |
| Short-float base | `0x3D` | `101` | 7 (+ float bits in upper bits) |

### Header Word Layout

Header words appear at the base address of every heap object:

```
63       56  55            24  23         8  7     3  2  0
┌──────────┬────────────────┬──────────────┬────────┬─────┐
│ gc_bits  │   shape_id     │  size_words  │hdr_sub │ 111 │
│  (8b)    │   (32b)        │   (16b)      │ (5b)   │ tag │
└──────────┴────────────────┴──────────────┴────────┴─────┘
```

| Field | Bits | Purpose |
|-------|------|---------|
| `gc_bits` | [63:56] | GC metadata. `0xFF` = forwarding pointer (object has been relocated). |
| `shape_id` | [55:24] | 32-bit type identifier. Maps to shape table (method dispatch). |
| `size` | [23:8] | Object size in 64-bit words (including header). Max 65535 words = 512 KiB per object. |
| `hdr_sub` | [7:3] | Header subtype (16 defined types). |
| `tag` | [2:0] | Always `111` (header). |

**Header subtypes** (5-bit `hdr_sub`):

| Value | Type | Description |
|-------|------|-------------|
| 0 | INSTANCE | General object with named fields |
| 1 | CONS | Cons cell (pair) — car + cdr |
| 2 | VECTOR | General vector (array of tagged words) |
| 3 | BYTEVEC | Byte vector (raw bytes, no tag interpretation) |
| 4 | CLOSURE | Function closure (code pointer + captured environment) |
| 5 | SYMBOL | Interned symbol |
| 6 | BIGNUM | Arbitrary-precision integer |
| 7 | RATIO | Exact rational (numerator/denominator pair) |
| 8 | DOUBLE | 64-bit IEEE 754 float (boxed) |
| 9 | COMPLEX | Complex number |
| 10 | WEAKREF | Weak reference (not traced by GC) |
| 11 | CONT | Continuation (captured stack) |
| 12 | PORT | I/O port |
| 13 | HASHTABLE | Hash table |
| 14 | FOREIGN | Foreign function interface pointer |
| 15 | SHAPE | Shape descriptor (class metadata) |
| 31 | EXTENDED | Extended header (for objects > 16 subtypes) |

---

## Instruction Encoding

All instructions are 32 bits wide. Four encoding formats:

### Format R (Register-Register)
```
31    26  25  21  20  16  15  11  10    6  5     0
┌────────┬───────┬───────┬───────┬────────┬───────┐
│ opcode │  rd   │  rs1  │  rs2  │  func  │unused │
│  (6b)  │ (5b)  │ (5b)  │ (5b)  │  (5b)  │ (6b)  │
└────────┴───────┴───────┴───────┴────────┴───────┘
```

### Format I (Immediate)
```
31    26  25  21  20  16  15                    0
┌────────┬───────┬───────┬──────────────────────┐
│ opcode │  rd   │  rs1  │       imm16          │
│  (6b)  │ (5b)  │ (5b)  │      (16b)           │
└────────┴───────┴───────┴──────────────────────┘
```

### Format S (Store)
```
31    26  25  21  20  16  15  11  10           0
┌────────┬───────┬───────┬───────┬─────────────┐
│ opcode │  rd   │  rs1  │  rs2  │   imm11     │
│  (6b)  │ (5b)  │ (5b)  │ (5b)  │   (11b)     │
└────────┴───────┴───────┴───────┴─────────────┘
```

### Format X (Extended)
```
31    26  25                                   0
┌────────┬─────────────────────────────────────┐
│ opcode │              raw26                  │
│  (6b)  │              (26b)                  │
└────────┴─────────────────────────────────────┘
```

---

## Opcode Families

### Family 1: Type Tests & Tagged Arithmetic

| Opcode | Mnemonic | Format | Operation |
|--------|----------|--------|-----------|
| 0 | TST | R | `Rd = (regs[Rs1] matches tag constant in imm16) ? t : nil` |
| 1 | TST.SHAPE | R | `Rd = (object's shape_id == imm) ? t : nil` |
| 2 | ARITH.FIX | R | Tagged fixnum arithmetic: ADD.FIX, SUB.FIX, MUL.FIX, DIV.FIX (traps on non-fixnum or overflow) |
| 3 | ADD.FIX.IMM | I | `Rd = regs[Rs1] + tag_fixnum(imm16)` with overflow trap |
| 4 | CMP.TAGGED | R | Tagged comparison → tagged -1/0/+1 (fixnum), or identity compare for non-fixnums |

**Key implementation detail:** Fixnum ADD/SUB are single-cycle. The ALU performs the raw 64-bit add, then checks for overflow by comparing input/output sign bits. MUL uses the hardware multiplier and verifies the result fits in 63 bits. DIV uses a 64-cycle iterative divider — it untags both operands, divides, retags the quotient.

### Family 2: Allocation

| Opcode | Mnemonic | Format | Operation |
|--------|----------|--------|-----------|
| 8 | ALLOC | I | Bump-allocate an object. Template from table, zero-fill payload. |
| 9 | ALLOC.CONS | R | Allocate a 3-word cons cell (header + car + cdr). |
| 10 | ALLOCV | I | Allocate a variable-length vector. |
| 11 | ALLOC.CLOSURE | I | Allocate a closure (code pointer + environment). |

**Hardware allocation sequence** (implemented as a multi-state FSM):
1. Read template header from the 256-entry template table
2. Compute object size from template + `n_extra_words`
3. Check NP (nursery pointer) + size ≤ NL (nursery limit). If not → TRAP_NURSERY_OVERFLOW.
4. Write header to `[NP]`
5. Zero-fill all payload words (one per cycle)
6. Set `Rd = make_ref(NP)` (tagged reference to new object)
7. Update `NP = NP + size * 8`

### Family 3: Field Access

| Opcode | Mnemonic | Format | Operation |
|--------|----------|--------|-----------|
| 16 | LD | I | `Rd = mem[ref_address(Rs1) + imm16*8]` — load tagged field |
| 17 | LD.CAR.CDR | R | `Rd = mem[cons_addr + 8]` (car) or `Rd = mem[cons_addr + 16]` (cdr) |
| 18 | ST | S | `mem[ref_address(Rs1) + imm11*8] = Rd` — store tagged field |
| 19 | ST.WB | S | Store with write barrier — checks if stored ref crosses GC generations |
| 20 | ST.CAR.CDR | R | Store to car/cdr of a cons cell |

**Write barrier** (`ST.WB`): After the store, the hardware checks if `Rd` is a reference (`is_any_ref`) and if the destination address is in a different GC generation than the source. If so, it marks the corresponding card table entry. The card table base, card shift (log₂ of card size), and generation boundary are configured via system traps.

### Family 4: Dispatch & Control Flow

| Opcode | Mnemonic | Format | Operation |
|--------|----------|--------|-----------|
| 24 | CALL.IC | I | Inline-cached method dispatch. Lookup `(PC, object_shape)` in IC table. |
| 25 | IC.INSTALL | R | Install an IC table entry: `(PC, shape) → target`. |
| 26 | CALL.DIRECT | I | Direct function call. Push frame, jump to target. |
| 27 | CALL.CLOSURE | R | Call a closure. Read code pointer from closure object, push frame, jump. |
| 28 | RET | R | Return. Pop frame, restore LR/FP/SP, jump to LR. |
| 29 | TAILCALL.IC | I | Tail-call via inline cache (no frame push). |
| 30 | TAILCALL.DIR | I | Direct tail-call. |
| 31 | JR | R | Jump register (indirect jump). |

**CALL.IC sequence:**
1. Read receiver object's header → extract `shape_id`
2. Lookup `(callsite_PC, shape_id)` in the 64-entry IC table
3. If **hit** → jump to cached target address (fast path, ~4 cycles)
4. If **miss** → `TRAP_IC_MISS` (slow path, runtime installs entry via `IC.INSTALL`)

This implements **monomorphic inline caching** — the key optimization for dynamic dispatch in Smalltalk/JavaScript/Ruby-style languages.

### Family 6: Messaging & Atomics

| Opcode | Mnemonic | Format | Operation |
|--------|----------|--------|-----------|
| 36 | SEND | I | Send tagged word to hardware queue. Traps if full. |
| 37 | RECV | I | Blocking receive from hardware queue. Traps if empty. |
| 37 | TRY.RECV | R | Non-blocking receive. `Rd = value or nil`, `Rd2 = t or nil`. |
| 38 | CAS.TAGGED | X | Compare-and-swap on a tagged word in memory. |
| 39 | FAA / FENCE.GC | R | Fetch-and-add / GC fence (wait for all engines idle). |

**TRY.RECV encoding:** Same opcode as RECV, but `func ≠ 0`. The `func` field encodes the Rd2 register index. This allows non-blocking queue polling without trapping.

### Family 7: GC Engine Commands

| Opcode | Mnemonic | Format | Operation |
|--------|----------|--------|-----------|
| 40 | ENQ.SCAN | R | Start scanner engine on memory region. |
| 41 | ENQ.COPY | R | Start copier engine. `Rs1 = src_base`, `Rs2 = dst_base`, `Rd = size`. |
| 42 | ENQ.FIXUP | R | Start fixup engine on memory region. |
| 43 | ENQ.COMPACT | R | Start compaction (reuses copier). |

These instructions issue commands through the GC engine interface. The command includes `arg0` (base), `arg1` (destination or size), and `arg2` (size for copy). The CPU traps if the engines are busy (`TRAP_ENGINE_BUSY`).

### Scalar Supplementary

| Opcode | Mnemonic | Purpose |
|--------|----------|---------|
| 48 | ARITH.RAW | Untagged 64-bit arithmetic (ADD, SUB, MUL, DIV, MOD) |
| 49 | BITWISE | AND, OR, XOR, SHL, SHR, ASR, NOT |
| 50 | LDR | Load raw 64-bit word from memory |
| 51 | STR | Store raw 64-bit word to memory |
| 52 | BR | Unconditional branch (PC-relative) |
| 53 | BR.COND | Conditional branch (6 conditions: truthy, nil, fix<, fix=, fix>, eq_zero) |
| 54 | PUSH/POP | Stack operations (push/pop via SP) |
| 55 | LI | Load 16-bit sign-extended immediate |
| 56 | LUI | Load upper 16 bits |
| 57 | PUSH.MULTI | Push multiple registers (bitmask) |
| 58 | POP.MULTI | Pop multiple registers (bitmask) |
| 59 | LI32 | Load 32-bit immediate (LI + LUI fused) |
| 5/6 | LDW/STW | Sub-word: 32-bit load/store |
| 44/45 | LDB/STB | Sub-word: byte load/store |
| 46/47 | LDH/STH | Sub-word: 16-bit halfword load/store |

### System Instructions

| Opcode | Mnemonic | Purpose |
|--------|----------|---------|
| 60 | TRAP | Software trap (enter trap handler) + system configuration traps |
| 61 | ERET | Exception return (return from trap handler) |
| 62 | SYS.INFO | Read system info (tile ID, thread ID, perf counters) |
| 63 | HALT/NOP | If `rd=0` → halt processor; else → no-op |

**System traps** (via `TRAP` with high bit set in trap code):

| Code | Name | Configures |
|------|------|------------|
| 0x90 | SET_TRAP_TABLE | Base address of trap vector table |
| 0x91 | SET_TEMPLATE | Write entry in header template table |
| 0x92 | SET_CARD_BASE | Card table base address |
| 0x93 | SET_CARD_SHIFT | Card size = 2^shift bytes |
| 0x94 | SET_GEN_BOUNDARY | Nursery/old-gen boundary address |
| 0x95 | SET_QUEUE_BASE | Message queue base address |

---

## Register Conventions

| Register | Alias | Purpose |
|----------|-------|---------|
| r0–r24 | — | General-purpose |
| r25 | NL | Nursery limit (GC allocation boundary) |
| r26 | NP | Nursery pointer (current allocation point) |
| r27 | TP | Thread pointer (thread-local storage) |
| r28 | LR | Link register (return address) |
| r29 | FP | Frame pointer |
| r30 | SP | Stack pointer |
| r31 | — | General-purpose (no hardwired zero) |

Note: r0 is **not** hardwired to zero (unlike RISC-V). All 32 registers are fully general-purpose; the aliases above are conventions enforced by the compiler and calling convention, not by hardware.

---

## Trap System

The LM-1 implements a vectored trap system:

1. On trap, the hardware reads the trap handler address from the trap table at `trap_tbl + trap_code * 8`
2. The trap table base is set via `TRAP 0x90` (SET_TRAP_TABLE)
3. The handler address is loaded via the LSU (1 SRAM read cycle)
4. PC is saved, execution jumps to the handler
5. `ERET` returns from the handler

**Trap codes** are 8-bit values categorized by high nybble:

| Range | Category | Examples |
|-------|----------|----------|
| 0x01–0x07 | Type errors | NOT_FIXNUM, DIVIDE_BY_ZERO, NOT_REF, NOT_CONS |
| 0x10 | GC | NURSERY_OVERFLOW |
| 0x20 | IC | IC_MISS |
| 0x30–0x31 | Queue | QUEUE_FULL, QUEUE_EMPTY |
| 0x40 | Engine | ENGINE_BUSY |
| 0x50 | Barrier | BARRIER_OVERFLOW |
| 0x60 | Security | CAPABILITY_VIOLATION |
| 0x70 | Stack | STACK_UNDERFLOW |
| 0xFE | Fallback | UNIMPLEMENTED (unknown opcode) |
