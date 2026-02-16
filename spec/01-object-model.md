# LM-1 Universal Object Word Model

**Spec ID:** LM1-SPEC-01  
**Revision:** 0.1-draft  
**Date:** 2026-02-16

---

## 1. Scope

This document defines the **tagged word format** used by all LM-1 instructions and memory. Every 64-bit machine word is self-describing: the hardware can determine its dynamic type without consulting external metadata.

## 2. Design Goals

1. **Single-cycle type testing.** `TST` instructions must resolve in 1 cycle.
2. **Zero-cost fixnum arithmetic.** Tagged fixnums must be usable by the ALU with at most a 1-cycle tag-strip/re-tag overhead.
3. **Full 48-bit address space for refs.** Heap pointers must not sacrifice addressability.
4. **GC-friendly.** The hardware must be able to distinguish pointers from non-pointers in any word, in any context, without ambiguity.
5. **Extensible.** Room for future type tags without breaking existing code.

## 3. Word Layout

All values in registers and memory are 64-bit words. The low 3 bits form the **primary tag**.

```
 63                              3  2  1  0
┌──────────────────────────────────┬────────┐
│            payload               │  tag   │
└──────────────────────────────────┴────────┘
```

### 3.1 Primary Tag Encoding

| Tag (bits 2:0) | Class | Description |
|:-:|:-:|:--|
| `000` | **Fixnum (even)** | Immediate signed integer |
| `001` | **Ref** | Pointer to heap object |
| `010` | **Fixnum (odd)** | Immediate signed integer (tag bit 0 = 0 for all fixnums) |
| `011` | **Cons Ref** | Pointer to cons cell (specialized ref) |
| `100` | **Fixnum (even)** | Immediate signed integer |
| `101` | **Special** | Immediates: nil, t, unbound, characters, etc. |
| `110` | **Fixnum (odd)** | Immediate signed integer |
| `111` | **Header** | Object header word (only appears in memory, not in registers under normal operation) |

**Fixnum rule:** A word is a fixnum if and only if bit 0 is `0`. This gives fixnums a single-bit test and a full 63-bit signed integer range (−2⁶² to 2⁶²−1). Fixnum arithmetic uses the word directly; adds and subtracts work without untagging (the tag bits cancel). Multiplication and division require a shift.

**Ref rule:** A word is a heap pointer if bits 1:0 are `01`. Bit 2 distinguishes general refs (`001`) from cons refs (`011`). The pointer payload occupies bits 63:3, giving an 8-byte-aligned effective address. With 48-bit virtual addresses, bits 50:3 hold the address and bits 63:51 are available for **metadata** (see § 3.4).

### 3.2 Fixnum Detail

```
 63                                        1  0
┌────────────────────────────────────────────┬──┐
│          signed integer value              │0 │
└────────────────────────────────────────────┴──┘
```

- The value is stored as a **shifted integer**: the numeric value is `word >> 1` (arithmetic shift).
- Addition: `a + b` just works (both have tag 0 in bit 0, sum preserves it).
- Subtraction: `a - b` just works.
- Multiplication: `(a >> 1) * b` or `a * (b >> 1)` to compensate for the double-shift.
- Overflow on any fixnum operation MUST trap to the runtime slow path, which promotes to a heap-allocated bignum.

**Rationale:** Using bit 0 alone for the fixnum test (rather than 3 bits) maximizes fixnum range and simplifies the common `fixnum?` predicate. The 3-bit primary tag is only consulted when bit 0 is `1` (i.e., the value is not a fixnum).

### 3.3 Ref Detail

```
 63      51 50                            3  2  1  0
┌──────────┬──────────────────────────────┬──┬─────┐
│ metadata │     address bits 50:3        │  │ 0 1 │
└──────────┴──────────────────────────────┴──┴─────┘
                                           │
                                      cons bit (bit 2)
```

- **Address extraction:** To dereference a ref, mask out the low 3 bits and (if metadata bits are used) the high 13 bits: `effective_addr = word & 0x0007_FFFF_FFFF_FFF8`.
- **Cons bit (bit 2):** `0` = general ref, `1` = cons ref. Cons refs allow a fast `consp` predicate without loading the object header.
- **Metadata bits 63:51** (13 bits): Available for implementation-defined use:
  - **Shape cache hint** (bits 58:51, 8 bits): A hash of the object's shape/class. Used by `CALL.IC` to speed up the first-level shape check without loading the header. Updated lazily; a mismatch is not an error, just a miss.
  - **GC generation bits** (bits 60:59, 2 bits): Encodes which generation/region the object belongs to. Used by `ST.WB` to quickly decide if a write barrier card-mark is needed.
  - **Capability/safety bits** (bits 63:61, 3 bits): Reserved for capability mode (see [02-isa.md § 2.8](02-isa.md)).

**Alignment:** All heap objects MUST be aligned to 8 bytes (3 low bits zero in the actual address). This is naturally enforced by `ALLOC`.

### 3.4 Special Immediates

```
 63                              8  7     3  2  1  0
┌──────────────────────────────────┬────────┬────────┐
│            payload               │ subtype│ 1  0  1│
└──────────────────────────────────┴────────┴────────┘
```

Tag `101` encodes non-pointer immediates that are not fixnums. The **subtype** field (bits 7:3, 5 bits) distinguishes:

| Subtype | Value | Description |
|:-:|:-:|:--|
| `00000` | `#<nil>` | The empty list / false (`nil`) |
| `00001` | `#<t>` | Boolean true |
| `00010` | `#<unbound>` | Unbound variable marker |
| `00011` | `#<eof>` | End-of-file marker |
| `00100` | `#<void>` | No-value / void return |
| `00101` | `#<undefined>` | Undefined (for optional safety) |
| `00110` | Character | bits 63:8 hold a Unicode code point (up to 21 bits needed) |
| `00111` | Single-float | bits 63:8 hold an IEEE 754 binary32 (with 24 unused bits) |
| `01000`–`11111` | *Reserved* | For future use |

**Canonical nil:** `nil` is the all-zero word with tag `101` in bits 2:0 — i.e., the integer value `0x0000_0000_0000_0005`. This is chosen so that `nil` is neither a fixnum nor a pointer, but is still a small constant.

**Canonical t:** `t` is `0x0000_0000_0000_000D` (subtype `00001`, tag `101`).

### 3.5 Object Headers (Memory-Only)

```
 63      56 55                   24 23    8  7     3  2  1  0
┌──────────┬───────────────────────┬────────┬────────┬────────┐
│  GC bits │      shape ID        │  size  │ hdr-sub│ 1  1  1│
└──────────┴───────────────────────┴────────┴────────┴────────┘
```

Tag `111` marks an **object header word**. Headers appear only as the first word of a heap-allocated object.

| Field | Bits | Description |
|-------|------|-------------|
| **tag** | 2:0 | Always `111` |
| **hdr-sub** | 7:3 | Header subtype (see below) |
| **size** | 23:8 | Object size in words (up to 65535 words = 512 KiB). Objects larger than this use an extended-size encoding (hdr-sub `11111`). |
| **shape ID** | 55:24 | A 32-bit identifier for the object's shape/class. Used by `CALL.IC` and `TST`. |
| **GC bits** | 63:56 | 8 bits for GC metadata: mark bits, forwarding flag, pinned flag, generation, etc. |

#### Header Subtypes

| hdr-sub | Object Kind |
|:-:|:--|
| `00000` | **Standard instance** (fixed slots followed by optional variable-length payload) |
| `00001` | **Cons cell** (exactly 2 words: car, cdr) |
| `00010` | **Vector** (array of tagged words) |
| `00011` | **Bytevector / String** (array of raw bytes, not tagged) |
| `00100` | **Closure** (code pointer + captured environment slots) |
| `00101` | **Symbol** (name, value, plist, package) |
| `00110` | **Bignum** (arbitrary-precision integer) |
| `00111` | **Ratio** (numerator, denominator) |
| `01000` | **Double-float** (IEEE 754 binary64, boxed) |
| `01001` | **Complex** (real, imaginary) |
| `01010` | **Weak reference** |
| `01011` | **Continuation / stack frame** |
| `01100` | **Port / I/O object** |
| `01101` | **Hash table** |
| `01110` | **Foreign pointer** (opaque, not traced by GC) |
| `01111` | **Shape descriptor** (class/layout metadata) |
| `10000`–`11110` | *Reserved* |
| `11111` | **Extended** (next word holds extended size and subtype) |

### 3.6 Forwarding Pointers

During GC, a moved object's header is replaced by a **forwarding pointer**:

```
 63                              3  2  1  0
┌──────────────────────────────────┬────────┐
│      new-address bits 63:3       │ 1  1  1│
└──────────────────────────────────┴────────┘
   (with GC-bits[63:56] = 0xFF to indicate forwarding)
```

A header with GC bits = `0xFF` is a forwarding pointer. Bits 55:3 hold the new location. The mutator MUST NOT encounter forwarding pointers in normal operation; the GC's fixup pass resolves them before resuming mutators (or the read barrier does so lazily, if the implementation supports concurrent collection).

## 4. Object Layout in Memory

All heap objects start with a header word, followed by payload words.

### 4.1 Cons Cell

```
Offset 0:  header  (hdr-sub = 00001, size = 2, shape = <cons-shape-id>)
Offset 8:  car     (tagged word)
Offset 16: cdr     (tagged word)
```

Total: 3 words = 24 bytes. Cons-ref (tag `011`) points to offset 0 of this object.

**Note:** Implementations MAY use a **headerless cons** optimization where the cons-ref tag alone identifies the layout, saving the header word. In this mode, cons cells are 2 words = 16 bytes, and the GC uses the cons-ref tag to identify them during scanning. This optimization is RECOMMENDED for LM-1 Standard and above.

### 4.2 Standard Instance

```
Offset 0:   header    (hdr-sub = 00000, size = N, shape = <shape-id>)
Offset 8:   slot[0]   (tagged word)
Offset 16:  slot[1]   (tagged word)
...
Offset 8*N: slot[N-1] (tagged word)
```

### 4.3 Vector

```
Offset 0:  header       (hdr-sub = 00010, size = ceil((len+1)/1), shape = <vector-shape-id>)
Offset 8:  length       (fixnum — the logical length of the vector)
Offset 16: element[0]   (tagged word)
Offset 24: element[1]   (tagged word)
...
```

### 4.4 Closure

```
Offset 0:  header       (hdr-sub = 00100, size = N+1, shape = <closure-shape-id>)
Offset 8:  code-entry   (raw code pointer or ref to code object)
Offset 16: env[0]       (tagged word — captured variable)
Offset 24: env[1]       (tagged word)
...
```

### 4.5 Symbol

```
Offset 0:  header       (hdr-sub = 00101, size = 4, shape = <symbol-shape-id>)
Offset 8:  name         (ref to string)
Offset 16: value        (tagged word — global value cell)
Offset 24: plist        (ref to list or nil)
Offset 32: package      (ref to package or nil)
```

## 5. Tag Manipulation Rules

### 5.1 Extracting Address from Ref

```
effective_address = word & 0x0007_FFFF_FFFF_FFF8
```

This masks out the 3 tag bits and the 13 metadata bits.

### 5.2 Constructing a Ref

```
ref = (address & 0x0007_FFFF_FFFF_FFF8) | tag | (metadata << 51)
```

Where `tag` is `001` (general ref) or `011` (cons ref).

### 5.3 Fixnum Value Extraction

```
integer_value = (int64_t)word >> 1     // arithmetic right shift
```

### 5.4 Fixnum Construction

```
fixnum_word = (value << 1)             // bit 0 is always 0
```

### 5.5 Type Predicates (Hardware-Accelerated)

| Predicate | Test |
|-----------|------|
| `fixnump` | `(word & 1) == 0` |
| `refp` | `(word & 3) == 1` |
| `cons-refp` | `(word & 7) == 3` |
| `general-refp` | `(word & 7) == 1` |
| `specialp` | `(word & 7) == 5` |
| `nilp` | `word == 0x05` |
| `headerp` | `(word & 7) == 7` |
| `characterp` | `(word & 0xFF) == 0x35` (tag=101, subtype=00110) |
| `single-floatp` | `(word & 0xFF) == 0x3D` (tag=101, subtype=00111) |

All of these are single-instruction via the `TST` family.

## 6. Invariants

The following invariants MUST hold at all times for any conforming implementation:

1. **Tag integrity.** Every word in a register or memory location has a valid primary tag.
2. **Pointer alignment.** Every ref points to an 8-byte-aligned address.
3. **Header validity.** The first word of every live heap object is a valid header (tag `111`) or a forwarding pointer during GC.
4. **No pointer forgery.** Non-ref words MUST NOT be used as pointers. The hardware MAY enforce this in capability mode.
5. **GC visibility.** The GC can determine, for any word, whether it contains a pointer, by checking bit 0 (non-fixnum) and then bits 2:1 (ref class). This is the *precise pointer identification* guarantee.

## 7. Rationale Notes

### Why bit-0 for fixnum?

Using a single bit gives fixnums 63-bit range and makes the `fixnump` test a single AND. This is more valuable than reserving 3 full bits for more immediate types, because fixnum operations dominate the hot paths of most Lisp programs.

### Why separate cons-ref?

Cons cells are the most common heap object in Lisp. A dedicated tag avoids loading the header just to answer `consp`. This also enables the headerless cons optimization (§ 4.1), saving 8 bytes per cons cell—a significant memory reduction for list-heavy programs.

### Why metadata bits in refs?

The shape cache hint avoids a dependent load on the critical path of `CALL.IC`: the hardware checks the hint first, and only loads the header on a mismatch. The GC generation bits let `ST.WB` decide whether to card-mark without loading region metadata. Both are performance optimizations; correctness never depends on them.

### Why not NaN-boxing?

NaN-boxing (as used in some JS engines) encodes non-double values in the NaN space of IEEE 754 doubles. This makes doubles unboxed but penalizes integers and pointers. For Lisp, where fixnums and cons cells dominate, the tag-in-low-bits approach is strictly better: fixnum arithmetic is cheaper, pointer dereferencing is cheaper, and doubles are boxed only when necessary (they can be immediate in the special-immediate subtype for single-float, and boxed for double-float).
