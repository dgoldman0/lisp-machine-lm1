# LM-1 Instruction Encoding and Binary Format

**Spec ID:** LM1-SPEC-07  
**Revision:** 0.1-draft  
**Date:** 2026-02-16

---

## 1. Scope

This document specifies the binary encoding of LM-1 instructions. All instructions are fixed-width **32 bits**, simplifying fetch, decode, and alignment.

## 2. Encoding Principles

1. **Fixed 32-bit width.** No variable-length encoding. Simplifies the in-order pipeline.
2. **6-bit family/opcode field.** Bits 31:26 identify the instruction family and operation.
3. **Orthogonal register fields.** Register specifiers are always 5 bits, always in the same positions when present.
4. **Immediates are sign-extended** unless otherwise noted.
5. **No condition codes.** Conditional branches test a register, not flags.

## 3. Top-Level Format

```
 31    26 25                                  0
┌────────┬────────────────────────────────────┐
│ opcode │        format-dependent fields     │
│ (6 bit)│                                    │
└────────┴────────────────────────────────────┘
```

The 6-bit opcode gives 64 top-level instruction slots. These are grouped by family:

| Opcode Range | Family | Description |
|:------------:|:------:|-------------|
| `000000`–`000111` | F1 | Tagged arithmetic & type tests |
| `001000`–`001111` | F2 | Allocation |
| `010000`–`010111` | F3 | Field access with barriers |
| `011000`–`011111` | F4 | Dynamic dispatch |
| `100000`–`100011` | F5 | Pointer prefetch |
| `100100`–`100111` | F6 | Concurrency & messaging |
| `101000`–`101011` | F7 | Region / bulk ops |
| `101100`–`101111` | F8 | Capability / safety (optional) |
| `110000`–`111011` | Scalar | Supplementary scalar & control |
| `111100`–`111111` | System | System, traps, NOP, HALT |

## 4. Instruction Formats

Five canonical formats, named R, I, S, B, and X:

### 4.1 Format R (Register-Register)

Used by most ALU, type-test, and register-to-register instructions.

```
 31    26 25  21 20  16 15  11 10   6  5      0
┌────────┬──────┬──────┬──────┬──────┬─────────┐
│ opcode │  Rd  │  Rs1 │  Rs2 │ func │ (rsvd)  │
│  (6)   │  (5) │  (5) │  (5) │  (5) │  (6)    │
└────────┴──────┴──────┴──────┴──────┴─────────┘
```

| Field | Bits | Description |
|-------|:----:|-------------|
| opcode | 31:26 | Instruction identifier |
| Rd | 25:21 | Destination register |
| Rs1 | 20:16 | Source register 1 |
| Rs2 | 15:11 | Source register 2 |
| func | 10:6 | Function selector (sub-operation within family) |
| reserved | 5:0 | Must be zero |

### 4.2 Format I (Register-Immediate)

Used by arithmetic-with-immediate, load-with-offset, type tests with tag constants.

```
 31    26 25  21 20  16 15                    0
┌────────┬──────┬──────┬────────────────────────┐
│ opcode │  Rd  │  Rs1 │      imm16 (signed)    │
│  (6)   │  (5) │  (5) │         (16)           │
└────────┴──────┴──────┴────────────────────────┘
```

| Field | Bits | Description |
|-------|:----:|-------------|
| opcode | 31:26 | Instruction identifier |
| Rd | 25:21 | Destination register |
| Rs1 | 20:16 | Source register |
| imm16 | 15:0 | 16-bit signed immediate (sign-extended to 64 bits) |

### 4.3 Format S (Store)

Used by store instructions where the destination is memory, not a register.

```
 31    26 25  21 20  16 15  11 10            0
┌────────┬──────┬──────┬──────┬───────────────┐
│ opcode │  Rs  │  Rt  │field │   imm11       │
│  (6)   │  (5) │  (5) │  (5) │    (11)       │
└────────┴──────┴──────┴──────┴───────────────┘
```

| Field | Bits | Description |
|-------|:----:|-------------|
| opcode | 31:26 | Instruction identifier |
| Rs | 25:21 | Base object ref register |
| Rt | 20:16 | Value to store |
| field | 15:11 | Field index |
| imm11 | 10:0 | Additional offset or flags (often unused, zero) |

### 4.4 Format B (Branch)

Used by all branch instructions.

```
 31    26 25  21 20  16 15                    0
┌────────┬──────┬──────┬────────────────────────┐
│ opcode │  Rs1 │  Rs2 │   offset16 (signed)    │
│  (6)   │  (5) │  (5) │      (16)              │
└────────┴──────┴──────┴────────────────────────┘
```

| Field | Bits | Description |
|-------|:----:|-------------|
| opcode | 31:26 | Branch type |
| Rs1 | 25:21 | First test register |
| Rs2 | 20:16 | Second test register (or 0 for single-operand branches) |
| offset16 | 15:0 | Signed branch offset in words (multiply by 4 for byte offset). Range: ±128 KiB. |

### 4.5 Format X (Extended / Special)

Used by dispatch, allocation, movement engine, and system instructions that need non-standard field layouts.

```
 31    26 25                                  0
┌────────┬────────────────────────────────────┐
│ opcode │       instruction-specific         │
│  (6)   │            (26)                    │
└────────┴────────────────────────────────────┘
```

The 26-bit payload is defined per-instruction.

---

## 5. Family-Specific Encodings

### 5.1 Family 1: Tagged Arithmetic & Type Tests

#### `TST Rd, Rs, #tag` — Format I

```
opcode = 000000
Rd = destination
Rs1 = source
imm16[2:0] = tag constant (TAG_FIXNUM=0, TAG_REF=1, TAG_CONS=2, ...)
imm16[15:3] = reserved (0)
```

#### `TST.SHAPE Rd, Rs, #shape_id` — Format I (overloaded imm)

```
opcode = 000001
Rd = destination
Rs1 = source
imm16 = low 16 bits of shape_id (high bits from a preceding LI instruction if needed)
```

#### `ADD.FIX Rd, Rs1, Rs2` — Format R

```
opcode = 000010
func = 00000
```

#### `SUB.FIX Rd, Rs1, Rs2` — Format R

```
opcode = 000010
func = 00001
```

#### `MUL.FIX Rd, Rs1, Rs2` — Format R

```
opcode = 000010
func = 00010
```

#### `DIV.FIX Rd, Rs1, Rs2` — Format R

```
opcode = 000010
func = 00011
```

#### `ADD.FIX.IMM Rd, Rs, #imm` — Format I

```
opcode = 000011
imm16 = pre-tagged fixnum constant (must have bit 0 = 0)
```

#### `CMP.TAGGED Rd, Rs1, Rs2` — Format R

```
opcode = 000100
func = 00000
```

#### `EQ Rd, Rs1, Rs2` — Format R

```
opcode = 000100
func = 00001
```

### 5.2 Family 2: Allocation

#### `ALLOC Rd, #words, #header_template` — Format X

```
opcode = 001000

 25  21 20      16 15                        0
┌──────┬──────────┬────────────────────────────┐
│  Rd  │  words   │   header_template_index    │
│  (5) │  (5)     │        (16)                │
└──────┴──────────┴────────────────────────────┘
```

`words`: object size in words (0–31). For larger objects, use `ALLOCV`.  
`header_template_index`: index into a per-tile/per-thread header-template table (16-bit), which stores the full 64-bit header word. This keeps the instruction compact.

#### `ALLOC.CONS Rd` — Format X (minimalist)

```
opcode = 001001

 25  21 20                                   0
┌──────┬────────────────────────────────────────┐
│  Rd  │              (reserved, 0)              │
│  (5) │                 (21)                    │
└──────┴────────────────────────────────────────┘
```

#### `ALLOCV Rd, Rs_length, #header_template` — Format X

```
opcode = 001010

 25  21 20  16 15                            0
┌──────┬──────┬────────────────────────────────┐
│  Rd  │Rs_len│   header_template_index        │
│  (5) │  (5) │         (16)                   │
└──────┴──────┴────────────────────────────────┘
```

#### `ALLOC.CLOSURE Rd, Rs_code, #env_size` — Format X

```
opcode = 001011

 25  21 20  16 15     11 10                  0
┌──────┬──────┬────────┬──────────────────────┐
│  Rd  │Rs_cod│env_size│     (reserved, 0)    │
│  (5) │  (5) │  (5)   │       (11)           │
└──────┴──────┴────────┴──────────────────────┘
```

### 5.3 Family 3: Field Access with Barriers

#### `LD Rd, Rs, #field` — Format I

```
opcode = 010000
Rd = destination
Rs1 = object ref
imm16[4:0] = field index (0–31 for fast path; larger via extended addressing)
imm16[15:5] = reserved
```

#### `LD.CAR Rd, Rs` — Format I

```
opcode = 010001
imm16 = 0 (field 0, car)
```

#### `LD.CDR Rd, Rs` — Format I

```
opcode = 010001
imm16 = 1 (field 1, cdr)
```

#### `ST Rs, #field, Rt` — Format S

```
opcode = 010010
```

#### `ST.WB Rs, #field, Rt` — Format S

```
opcode = 010011
```

#### `ST.CAR Rs, Rt` — Format S

```
opcode = 010100
field = 0
```

#### `ST.CDR Rs, Rt` — Format S

```
opcode = 010100
field = 1
```

### 5.4 Family 4: Dynamic Dispatch

#### `CALL.IC #callsite, Rs_receiver, #argc` — Format X

```
opcode = 011000

 25  21 20  16 15       8  7               0
┌──────┬──────┬──────────┬───────────────────┐
│ argc │Rs_rcv│ callsite │   (reserved, 0)   │
│  (5) │  (5) │   (8)    │      (8)          │
└──────┴──────┴──────────┴───────────────────┘
```

`callsite`: 8-bit callsite index. For programs with >256 callsites, a callsite-base register extends this. The runtime sets the base at function entry.

`argc`: number of arguments (0–31). Enough for most calls; variadic functions use a different convention.

#### `IC.INSTALL #callsite, #shape, Rs_entry` — Format X

```
opcode = 011001

 25  21 20  16 15       8  7               0
┌──────┬──────┬──────────┬───────────────────┐
│(rsvd)│Rs_ent│ callsite │   shape (low 8)   │
│  (5) │  (5) │   (8)    │      (8)          │
└──────┴──────┴──────────┴───────────────────┘
```

The full 32-bit shape ID is passed via a register (the IC unit reads it from Rs1 or a preceding `LI`).

#### `CALL.DIRECT Rs_entry` — Format X

```
opcode = 011010

 25  21 20                                   0
┌──────┬────────────────────────────────────────┐
│(rsvd)│              Rs_entry (5) + reserved    │
└──────┴────────────────────────────────────────┘
```

Actually:
```
 25  21 20  16 15                            0
┌──────┬──────┬────────────────────────────────┐
│(rsvd)│Rs_ent│           (reserved, 0)        │
│  (5) │  (5) │              (16)              │
└──────┴──────┴────────────────────────────────┘
```

#### `CALL.CLOSURE Rs_closure, #argc` — Format X

```
opcode = 011011

 25  21 20  16 15     11 10                  0
┌──────┬──────┬────────┬──────────────────────┐
│ argc │Rs_cls│ (rsvd) │     (reserved, 0)    │
│  (5) │  (5) │  (5)   │       (11)           │
└──────┴──────┴────────┴──────────────────────┘
```

#### `RET` — Format X

```
opcode = 011100
All payload bits = 0
```

#### `TAILCALL.IC #callsite, Rs_receiver, #argc` — Format X

```
opcode = 011101
(same layout as CALL.IC)
```

#### `TAILCALL.DIRECT Rs_entry` — Format X

```
opcode = 011110
(same layout as CALL.DIRECT)
```

### 5.5 Family 5: Pointer Prefetch

#### `PREFETCH.REF Rs` — Format I

```
opcode = 100000
Rd = 0 (no destination)
Rs1 = ref to prefetch
imm16 = 0
```

#### `PREFETCH.FIELD Rs, #field` — Format I

```
opcode = 100001
Rd = 0
Rs1 = ref
imm16 = field index
```

#### `PREFETCH.CDR Rs` — Format I

```
opcode = 100010
Rd = 0
Rs1 = cons ref
imm16 = 0
```

#### `GATHER.PREFETCH Rs_base, Rs_index_vec, #count` — Format R

```
opcode = 100011
Rd = 0
Rs1 = base
Rs2 = index vector ref
func[4:0] = count (0–31)
```

### 5.6 Family 6: Concurrency & Messaging

#### `SEND Rs_queue, Rt_value` — Format S

```
opcode = 100100
Rs = queue descriptor
Rt = value to send
field = 0
```

#### `RECV Rd, Rs_queue` — Format I

```
opcode = 100101
Rd = destination
Rs1 = queue descriptor
imm16 = 0 (blocking)
```

#### `TRY.RECV Rd, Rd2, Rs_queue` — Format R

```
opcode = 100101
Rd = value destination
Rs1 = queue descriptor
Rs2 = status destination (overloaded: Rd2 is encoded in func field)
func[4:0] = Rd2 register index
```

#### `CAS.TAGGED Rd, Rs_addr, Rs_expected, Rt_new` — Format X

```
opcode = 100110

 25  21 20  16 15  11 10   6  5            0
┌──────┬──────┬──────┬──────┬───────────────┐
│  Rd  │Rs_adr│Rs_exp│Rt_new│  (reserved)   │
│  (5) │  (5) │  (5) │  (5) │     (6)       │
└──────┴──────┴──────┴──────┴───────────────┘
```

#### `FAA Rd, Rs_addr, Rs_delta` — Format R

```
opcode = 100111
```

#### `FENCE.GC` — Format X

```
opcode = 100111
All other bits indicate FENCE subtype (func = 11111)
```

### 5.7 Family 7: Region / Bulk Ops

#### `ENQ.SCAN Rs_region, Rd_result` — Format I

```
opcode = 101000
```

#### `ENQ.COPY Rs_src_region, Rd_dst_region` — Format I

```
opcode = 101001
```

#### `ENQ.FIXUP Rs_pointer_list, Rs_forwarding_table` — Format R

```
opcode = 101010
```

#### `ENQ.COMPACT Rs_region` — Format I

```
opcode = 101011
```

#### `POLL.ENGINE Rd, Rs_descriptor` — Format I

```
opcode = 101011, func = 00001 (encoded in imm)
```

#### `AWAIT.ENGINE Rs_descriptor` — Format I

```
opcode = 101011, func = 00010
```

### 5.8 Supplementary Scalar & Control

#### Arithmetic: `ADD`, `SUB`, `MUL`, `DIV`, `MOD` — Format R

```
opcode = 110000
func = 00000 (ADD), 00001 (SUB), 00010 (MUL), 00011 (DIV), 00100 (MOD)
```

#### Bitwise: `AND`, `OR`, `XOR`, `SHL`, `SHR`, `ASR`, `NOT` — Format R

```
opcode = 110001
func = 00000 (AND), 00001 (OR), 00010 (XOR), 00011 (SHL), 00100 (SHR), 
       00101 (ASR), 00110 (NOT — Rs2 ignored)
```

#### Raw Load/Store: `LDR`, `STR`, `LDB`, `STB`, `LDH`, `STH`, `LDW`, `STW` — Format I / S

```
opcode = 110010 (loads), 110011 (stores)
func/imm bits select width: 000 = byte, 001 = half, 010 = word, 011 = dword
```

#### Branches — Format B

```
opcode = 110100 (BR)
opcode = 110101 (BR.T, BR.NIL, BR.FIX.LT, BR.FIX.EQ, BR.FIX.GT, BR.EQ)
Rs2 encoding or func bits select the condition type.
```

#### Stack: `PUSH`, `POP` — Format I

```
opcode = 110110
imm16 = 0 for single, or bitmask for PUSH.MULTI / POP.MULTI
```

#### Immediate Load: `LI Rd, #imm16` — Format I

```
opcode = 110111
Rd = destination
Rs1 = 0 (ignored)
imm16 = 16-bit value (sign-extended to 64 bits and tagged as fixnum? or raw? — depends on context)
```

#### Upper Immediate: `LUI Rd, #imm16` — Format I

```
opcode = 111000
Loads imm16 into bits 31:16 of Rd, zeroing other bits.
Used with LI to construct 32-bit constants.
```

#### System — Format X

```
opcode = 111100 (TRAP #code)
opcode = 111101 (ERET)
opcode = 111110 (TILE.ID, THREAD.ID, CYCLE — func selects which)
opcode = 111111 (HALT, NOP — func selects which)
```

---

## 6. Header-Template Table

Because full 64-bit header words cannot fit in a 32-bit instruction, allocation instructions reference a **header-template table** by index. Each tile maintains a table of up to 65536 (16-bit index) header templates:

```
header_template_table[index] = 64-bit header word
```

The table is stored in the tile's scratch SRAM region and initialized by the runtime at boot or when new classes are defined. Common entries:

| Index | Header Template | Object Type |
|:-----:|----------------|-------------|
| 0 | Cons header (hdr-sub=00001, shape=cons_shape_id, size=2) | Cons cell |
| 1 | Closure header template | Closure |
| 2 | Vector header template | General vector |
| 3 | String/bytevector header template | String |
| 4–N | Application-defined shapes | User classes |

---

## 7. Callsite-Base Register

With an 8-bit callsite field in `CALL.IC`, a function can have at most 256 distinct dispatch sites. For functions with more, the compiler partitions callsites into groups of 256 and sets a **callsite-base register** (stored in `tp` or a dedicated field) at the beginning of each group. The hardware IC lookup uses `callsite_base + callsite_field` as the effective callsite ID.

---

## 8. Instruction Alignment

All instructions are 4-byte aligned. The PC is always a multiple of 4. Branch offsets are in units of 4 bytes (1 instruction word).

---

## 9. Encoding Summary Table

| Instruction | Opcode | Format | Key Fields |
|-------------|:------:|:------:|------------|
| TST | 000000 | I | Rd, Rs, tag |
| TST.SHAPE | 000001 | I | Rd, Rs, shape_id_lo |
| ADD.FIX | 000010 | R | Rd, Rs1, Rs2, func=0 |
| SUB.FIX | 000010 | R | func=1 |
| MUL.FIX | 000010 | R | func=2 |
| DIV.FIX | 000010 | R | func=3 |
| ADD.FIX.IMM | 000011 | I | Rd, Rs, imm16 |
| CMP.TAGGED | 000100 | R | func=0 |
| EQ | 000100 | R | func=1 |
| ALLOC | 001000 | X | Rd, words, template |
| ALLOC.CONS | 001001 | X | Rd |
| ALLOCV | 001010 | X | Rd, Rs_len, template |
| ALLOC.CLOSURE | 001011 | X | Rd, Rs_code, env_size |
| LD | 010000 | I | Rd, Rs, field |
| LD.CAR/CDR | 010001 | I | Rd, Rs, 0/1 |
| ST | 010010 | S | Rs, field, Rt |
| ST.WB | 010011 | S | Rs, field, Rt |
| ST.CAR/CDR | 010100 | S | Rs, Rt, 0/1 |
| CALL.IC | 011000 | X | argc, Rs_rcv, callsite |
| IC.INSTALL | 011001 | X | Rs_entry, callsite, shape |
| CALL.DIRECT | 011010 | X | Rs_entry |
| CALL.CLOSURE | 011011 | X | argc, Rs_closure |
| RET | 011100 | X | — |
| TAILCALL.IC | 011101 | X | (as CALL.IC) |
| TAILCALL.DIRECT | 011110 | X | (as CALL.DIRECT) |
| PREFETCH.REF | 100000 | I | Rs |
| PREFETCH.FIELD | 100001 | I | Rs, field |
| PREFETCH.CDR | 100010 | I | Rs |
| GATHER.PREFETCH | 100011 | R | Rs_base, Rs_idx, count |
| SEND | 100100 | S | Rs_q, Rt_val |
| RECV | 100101 | I | Rd, Rs_q |
| CAS.TAGGED | 100110 | X | Rd, Rs_addr, Rs_exp, Rt_new |
| FAA | 100111 | R | Rd, Rs_addr, Rs_delta |
| FENCE.GC | 100111 | X | (func=11111) |
| ENQ.SCAN | 101000 | I | Rs_region, Rd |
| ENQ.COPY | 101001 | I | Rs_src, Rd_dst |
| ENQ.FIXUP | 101010 | R | Rs_ptrs, Rs_fwd |
| ENQ.COMPACT | 101011 | I | Rs_region |
| ADD (raw) | 110000 | R | func=0 |
| AND | 110001 | R | func=0 |
| LDR | 110010 | I | width in imm |
| STR | 110011 | S | width in imm |
| BR | 110100 | B | offset |
| BR.T / BR.NIL | 110101 | B | Rs, offset |
| PUSH / POP | 110110 | I | Rs or bitmask |
| LI | 110111 | I | Rd, imm16 |
| LUI | 111000 | I | Rd, imm16 |
| TRAP | 111100 | X | trap code |
| ERET | 111101 | X | — |
| TILE.ID etc | 111110 | X | func selects |
| HALT / NOP | 111111 | X | func selects |

---

## 10. Object File Format

Compiled Lisp functions are stored in **LM-1 object files** (`.lmo`):

```
┌────────────────────────────────┐
│         File Header            │
│  magic: "LMO1"                 │
│  version: 1                    │
│  section_count: N              │
│  entry_point: offset           │
└────────────────────────────────┘
│       Section Table            │
│  [{name, type, offset, size}]  │
└────────────────────────────────┘
│       .code section            │
│  (32-bit instructions)         │
└────────────────────────────────┘
│       .data section            │
│  (tagged constants, templates) │
└────────────────────────────────┘
│       .relo section            │
│  (relocation entries)          │
└────────────────────────────────┘
│       .symtab section          │
│  (symbol definitions/refs)     │
└────────────────────────────────┘
│       .debug section (opt)     │
│  (source locations, names)     │
└────────────────────────────────┘
```

### Section Types

| Type | ID | Description |
|------|:--:|-------------|
| CODE | 1 | Executable instructions |
| DATA | 2 | Tagged constant data |
| RELO | 3 | Relocation table |
| SYMTAB | 4 | Symbol table |
| DEBUG | 5 | Debug information |
| TEMPLATES | 6 | Header-template definitions |
| META | 7 | Function metadata (arglists, docstrings) |

### Relocation Entry

```
relo_entry = {
    offset: uint32      ; byte offset within the section
    type: uint8          ; relocation type
    symbol: uint24       ; symbol table index
}
```

Relocation types:
- `R_LM1_ABS64`: 64-bit absolute address
- `R_LM1_REL16`: 16-bit relative offset (for branch/imm fields)
- `R_LM1_TEMPLATE`: header-template table index
- `R_LM1_CALLSITE`: callsite ID assignment
