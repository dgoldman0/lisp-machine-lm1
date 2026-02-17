// ============================================================================
// LM-1 ISA Package
//
// Defines all ISA-level constants, types, opcodes, tag system, trap codes,
// and helper functions for the LM-1 tagged-word processor.
//
// Machine overview:
//   - 64-bit tagged words, 32-bit fixed-width instructions
//   - 32 general-purpose 64-bit registers (r0 is NOT hardwired to zero)
//   - No condition flags — all comparisons produce register values
//   - Tag-based type system: fixnum, ref, cons, special, header
//   - Bump-allocator nursery with generational GC support
//   - Trap-vector exception model
// ============================================================================
package lm1_pkg;

    // ---------------------------------------------------------------
    // Machine parameters
    // ---------------------------------------------------------------
    parameter int XLEN       = 64;   // Data word width
    parameter int ILEN       = 32;   // Instruction width
    parameter int NREGS      = 32;   // Number of GPRs per thread
    parameter int REG_IDX_W  = 5;    // log2(NREGS)
    parameter int NUM_THREADS = 4;   // Hardware threads per core
    parameter int THREAD_IDX_W = 2;  // log2(NUM_THREADS)
    parameter int FULL_REG_W = THREAD_IDX_W + REG_IDX_W;  // 7-bit banked address
    parameter int OPCODE_W   = 6;    // Opcode field width
    parameter int FUNC_W     = 5;    // Function/sub-op field width
    parameter int IMM16_W    = 16;   // I-format immediate width
    parameter int IMM11_W    = 11;   // S-format immediate width
    parameter int RAW26_W    = 26;   // X-format payload width

    // ---------------------------------------------------------------
    // Register aliases
    // ---------------------------------------------------------------
    parameter logic [REG_IDX_W-1:0] REG_NL = 5'd25;  // Nursery limit
    parameter logic [REG_IDX_W-1:0] REG_NP = 5'd26;  // Nursery pointer
    parameter logic [REG_IDX_W-1:0] REG_TP = 5'd27;  // Thread pointer
    parameter logic [REG_IDX_W-1:0] REG_LR = 5'd28;  // Link register
    parameter logic [REG_IDX_W-1:0] REG_FP = 5'd29;  // Frame pointer
    parameter logic [REG_IDX_W-1:0] REG_SP = 5'd30;  // Stack pointer

    // ---------------------------------------------------------------
    // Opcodes  (instruction bits [31:26])
    //
    // Encoding matches emu/lm1/decode.py Op enum exactly.
    // ---------------------------------------------------------------
    typedef enum logic [OPCODE_W-1:0] {
        // Family 1 — Tagged arithmetic & type tests
        OP_TST           = 6'd0,
        OP_TST_SHAPE     = 6'd1,
        OP_ARITH_FIX     = 6'd2,
        OP_ADD_FIX_IMM   = 6'd3,
        OP_CMP_TAGGED    = 6'd4,

        // Family 2 — Allocation
        OP_ALLOC         = 6'd8,
        OP_ALLOC_CONS    = 6'd9,
        OP_ALLOCV        = 6'd10,
        OP_ALLOC_CLOSURE = 6'd11,

        // Family 3 — Field access
        OP_LD            = 6'd16,
        OP_LD_CAR_CDR    = 6'd17,
        OP_ST            = 6'd18,
        OP_ST_WB         = 6'd19,
        OP_ST_CAR_CDR    = 6'd20,

        // Family 4 — Dispatch
        OP_CALL_IC       = 6'd24,
        OP_IC_INSTALL    = 6'd25,
        OP_CALL_DIRECT   = 6'd26,
        OP_CALL_CLOSURE  = 6'd27,
        OP_RET           = 6'd28,
        OP_TAILCALL_IC   = 6'd29,
        OP_TAILCALL_DIR  = 6'd30,
        OP_JR            = 6'd31,

        // Family 5 — Prefetch (no-ops)
        OP_PREFETCH_REF  = 6'd32,
        OP_PREFETCH_FLD  = 6'd33,
        OP_PREFETCH_CDR  = 6'd34,
        OP_GATHER_PRE    = 6'd35,

        // Family 6 — Concurrency
        OP_SEND          = 6'd36,
        OP_RECV          = 6'd37,
        OP_CAS_TAGGED    = 6'd38,
        OP_FAA_FENCE     = 6'd39,

        // Family 7 — Region / bulk (reserved)
        OP_ENQ_SCAN      = 6'd40,
        OP_ENQ_COPY      = 6'd41,
        OP_ENQ_FIXUP     = 6'd42,
        OP_ENQ_COMPACT   = 6'd43,

        // Scalar supplementary
        OP_ARITH_RAW     = 6'd48,
        OP_BITWISE       = 6'd49,
        OP_LDR           = 6'd50,
        OP_STR           = 6'd51,
        OP_BR            = 6'd52,
        OP_BR_COND       = 6'd53,
        OP_PUSH_POP      = 6'd54,
        OP_LI            = 6'd55,
        OP_LUI           = 6'd56,
        OP_PUSH_MULTI    = 6'd57,
        OP_POP_MULTI     = 6'd58,
        OP_LI32          = 6'd59,

        // Sub-word raw loads/stores  (supplementary scalar)
        OP_LDB           = 6'd44,   // byte load   (zero-extended)
        OP_STB           = 6'd45,   // byte store
        OP_LDH           = 6'd46,   // halfword load (zero-extended)
        OP_STH           = 6'd47,   // halfword store
        OP_LDW           = 6'd5,    // 32-bit word load (zero-extended)
        OP_STW           = 6'd6,    // 32-bit word store

        // System
        OP_TRAP          = 6'd60,
        OP_ERET          = 6'd61,
        OP_SYS_INFO      = 6'd62,
        OP_HALT_NOP      = 6'd63
    } opcode_t;

    // ---------------------------------------------------------------
    // Sub-function codes  (instruction bits [10:6] or as noted)
    // ---------------------------------------------------------------

    // ARITH_RAW sub-functions
    parameter logic [FUNC_W-1:0] FUNC_ADD = 5'd0;
    parameter logic [FUNC_W-1:0] FUNC_SUB = 5'd1;
    parameter logic [FUNC_W-1:0] FUNC_MUL = 5'd2;
    parameter logic [FUNC_W-1:0] FUNC_DIV = 5'd3;
    parameter logic [FUNC_W-1:0] FUNC_MOD = 5'd4;

    // BITWISE sub-functions
    parameter logic [FUNC_W-1:0] FUNC_AND = 5'd0;
    parameter logic [FUNC_W-1:0] FUNC_OR  = 5'd1;
    parameter logic [FUNC_W-1:0] FUNC_XOR = 5'd2;
    parameter logic [FUNC_W-1:0] FUNC_SHL = 5'd3;
    parameter logic [FUNC_W-1:0] FUNC_SHR = 5'd4;
    parameter logic [FUNC_W-1:0] FUNC_ASR = 5'd5;
    parameter logic [FUNC_W-1:0] FUNC_NOT = 5'd6;

    // ARITH_FIX sub-functions
    parameter logic [FUNC_W-1:0] FUNC_ADD_FIX = 5'd0;
    parameter logic [FUNC_W-1:0] FUNC_SUB_FIX = 5'd1;
    parameter logic [FUNC_W-1:0] FUNC_MUL_FIX = 5'd2;
    parameter logic [FUNC_W-1:0] FUNC_DIV_FIX = 5'd3;

    // CMP_TAGGED sub-functions
    parameter logic [FUNC_W-1:0] FUNC_CMP = 5'd0;
    parameter logic [FUNC_W-1:0] FUNC_EQ  = 5'd1;

    // PUSH_POP sub-functions
    parameter logic [FUNC_W-1:0] FUNC_PUSH = 5'd0;
    parameter logic [FUNC_W-1:0] FUNC_POP  = 5'd1;

    // FAA_FENCE: func == 0x1F means FENCE.GC, else FAA
    parameter logic [FUNC_W-1:0] FUNC_FENCE_GC = 5'h1F;

    // ---------------------------------------------------------------
    // SYS_INFO sub-functions  (in rs1 field, bits [20:16])
    // ---------------------------------------------------------------
    parameter logic [REG_IDX_W-1:0] SYS_TILE_ID    = 5'd0;
    parameter logic [REG_IDX_W-1:0] SYS_THREAD_ID  = 5'd1;
    parameter logic [REG_IDX_W-1:0] SYS_CYCLE      = 5'd2;
    parameter logic [REG_IDX_W-1:0] SYS_TRAP_CAUSE = 5'd3;
    parameter logic [REG_IDX_W-1:0] SYS_TRAP_PC    = 5'd4;

    // ---------------------------------------------------------------
    // Branch condition codes  (in rs1 field of BR_COND, bits [20:16])
    // ---------------------------------------------------------------
    parameter logic [REG_IDX_W-1:0] BR_T      = 5'd0;  // truthy
    parameter logic [REG_IDX_W-1:0] BR_NIL    = 5'd1;  // == NIL
    parameter logic [REG_IDX_W-1:0] BR_FIX_LT = 5'd2;  // fixnum < 0
    parameter logic [REG_IDX_W-1:0] BR_FIX_EQ = 5'd3;  // == 0
    parameter logic [REG_IDX_W-1:0] BR_FIX_GT = 5'd4;  // fixnum > 0
    parameter logic [REG_IDX_W-1:0] BR_EQ_Z   = 5'd5;  // word == 0

    // ---------------------------------------------------------------
    // Tag system  (low 3 bits of a 64-bit word)
    //
    // Fixnum:  bit[0] == 0  (value is w >>> 1)
    // Ref:     bits[1:0] == 01
    // Cons:    bits[2:0] == 011
    // Special: bits[2:0] == 101
    // Header:  bits[2:0] == 111  (memory-only, not a register value)
    // ---------------------------------------------------------------
    parameter logic [2:0] TAG_REF     = 3'b001;
    parameter logic [2:0] TAG_CONS    = 3'b011;
    parameter logic [2:0] TAG_SPECIAL = 3'b101;
    parameter logic [2:0] TAG_HEADER  = 3'b111;

    // Tag-test constants (for TST instruction, imm16[2:0])
    parameter logic [2:0] TAG_CONST_FIXNUM  = 3'd0;
    parameter logic [2:0] TAG_CONST_REF     = 3'd1;
    parameter logic [2:0] TAG_CONST_CONS    = 3'd2;
    parameter logic [2:0] TAG_CONST_SPECIAL = 3'd3;
    parameter logic [2:0] TAG_CONST_NIL     = 3'd4;
    parameter logic [2:0] TAG_CONST_CHAR    = 3'd5;
    parameter logic [2:0] TAG_CONST_SFLOAT  = 3'd6;
    parameter logic [2:0] TAG_CONST_HEADER  = 3'd7;

    // ---------------------------------------------------------------
    // Special immediate values
    // ---------------------------------------------------------------
    parameter logic [XLEN-1:0] VAL_NIL       = 64'h0000_0000_0000_0005;
    parameter logic [XLEN-1:0] VAL_T         = 64'h0000_0000_0000_000D;
    parameter logic [XLEN-1:0] VAL_UNBOUND   = 64'h0000_0000_0000_0015;
    parameter logic [XLEN-1:0] VAL_EOF       = 64'h0000_0000_0000_001D;
    parameter logic [XLEN-1:0] VAL_VOID      = 64'h0000_0000_0000_0025;
    parameter logic [XLEN-1:0] VAL_UNDEFINED = 64'h0000_0000_0000_002D;
    parameter logic [7:0]      CHAR_TAG_BYTE   = 8'h35;  // low byte of char
    parameter logic [7:0]      SFLOAT_TAG_BYTE = 8'h3D;  // low byte of short-float

    // Reference address mask: bits [50:3]
    parameter logic [XLEN-1:0] REF_ADDR_MASK = 64'h0007_FFFF_FFFF_FFF8;

    // Sign bit
    parameter logic [XLEN-1:0] SIGN_BIT = {1'b1, {(XLEN-1){1'b0}}};

    // ---------------------------------------------------------------
    // Header word layout  (64-bit word stored at object base address)
    //
    //   [63:56]  gc_bits    8 bits
    //   [55:24]  shape_id  32 bits
    //   [23:8]   size      16 bits  (number of payload words)
    //   [7:3]    hdr_sub    5 bits  (header subtype)
    //   [2:0]    111              (TAG_HEADER)
    // ---------------------------------------------------------------
    parameter int HDR_GC_HI    = 63;
    parameter int HDR_GC_LO    = 56;
    parameter int HDR_SHAPE_HI = 55;
    parameter int HDR_SHAPE_LO = 24;
    parameter int HDR_SIZE_HI  = 23;
    parameter int HDR_SIZE_LO  = 8;
    parameter int HDR_SUB_HI   = 7;
    parameter int HDR_SUB_LO   = 3;

    // Header subtypes
    parameter logic [4:0] HDR_INSTANCE  = 5'd0;
    parameter logic [4:0] HDR_CONS      = 5'd1;
    parameter logic [4:0] HDR_VECTOR    = 5'd2;
    parameter logic [4:0] HDR_BYTEVEC   = 5'd3;
    parameter logic [4:0] HDR_CLOSURE   = 5'd4;
    parameter logic [4:0] HDR_SYMBOL    = 5'd5;
    parameter logic [4:0] HDR_BIGNUM    = 5'd6;
    parameter logic [4:0] HDR_RATIO     = 5'd7;
    parameter logic [4:0] HDR_DOUBLE    = 5'd8;
    parameter logic [4:0] HDR_COMPLEX   = 5'd9;
    parameter logic [4:0] HDR_WEAKREF   = 5'd10;
    parameter logic [4:0] HDR_CONT      = 5'd11;
    parameter logic [4:0] HDR_PORT      = 5'd12;
    parameter logic [4:0] HDR_HASHTABLE = 5'd13;
    parameter logic [4:0] HDR_FOREIGN   = 5'd14;
    parameter logic [4:0] HDR_SHAPE     = 5'd15;
    parameter logic [4:0] HDR_EXTENDED  = 5'd31;

    // ---------------------------------------------------------------
    // Trap codes
    // ---------------------------------------------------------------
    parameter logic [7:0] TRAP_NOT_FIXNUM       = 8'h01;
    parameter logic [7:0] TRAP_FIXNUM_OVERFLOW  = 8'h02;
    parameter logic [7:0] TRAP_DIVIDE_BY_ZERO   = 8'h03;
    parameter logic [7:0] TRAP_TYPE_MISMATCH    = 8'h04;
    parameter logic [7:0] TRAP_NOT_REF          = 8'h05;
    parameter logic [7:0] TRAP_NOT_CONS         = 8'h06;
    parameter logic [7:0] TRAP_NOT_CLOSURE      = 8'h07;
    parameter logic [7:0] TRAP_NURSERY_OVERFLOW = 8'h10;
    parameter logic [7:0] TRAP_IC_MISS          = 8'h20;
    parameter logic [7:0] TRAP_QUEUE_FULL       = 8'h30;
    parameter logic [7:0] TRAP_QUEUE_EMPTY      = 8'h31;
    parameter logic [7:0] TRAP_ENGINE_BUSY      = 8'h40;
    parameter logic [7:0] TRAP_BARRIER_OVERFLOW = 8'h50;
    parameter logic [7:0] TRAP_CAPABILITY_VIOL  = 8'h60;
    parameter logic [7:0] TRAP_STACK_UNDERFLOW  = 8'h70;
    parameter logic [7:0] TRAP_UNIMPLEMENTED    = 8'hFE;

    // ---------------------------------------------------------------
    // System-trap sub-codes  (OP_TRAP with imm[7] = 1)
    //
    //   0x90  SET_TRAP_TABLE   — r1 → trap_tbl
    //   0x91  SET_TEMPLATE     — tmpl[r1[7:0]] ← r2
    //   0x92  SET_CARD_BASE    — r1 → card_table_base cfg register
    //   0x93  SET_CARD_SHIFT   — r1[5:0] → card_shift (default 6 = 64-byte cards)
    //   0x94  SET_GEN_BOUNDARY — r1 → gen_boundary (addrs < gen_boundary are nursery)
    //   0x95  SET_QUEUE_BASE   — r1 → message-queue base address
    //   0x96  GC_ENGINE_CMD    — enqueue a GC engine command (used by ENQ.*)
    // ---------------------------------------------------------------
    parameter logic [7:0] SYS_SET_TRAP_TABLE   = 8'h90;
    parameter logic [7:0] SYS_SET_TEMPLATE     = 8'h91;
    parameter logic [7:0] SYS_SET_CARD_BASE    = 8'h92;
    parameter logic [7:0] SYS_SET_CARD_SHIFT   = 8'h93;
    parameter logic [7:0] SYS_SET_GEN_BOUNDARY = 8'h94;
    parameter logic [7:0] SYS_SET_QUEUE_BASE   = 8'h95;
    parameter logic [7:0] SYS_GC_ENGINE_CMD    = 8'h96;

    // SYS_INFO sub-codes for GC-related queries
    parameter logic [REG_IDX_W-1:0] SYS_CARD_BASE     = 5'd5;
    parameter logic [REG_IDX_W-1:0] SYS_CARD_SHIFT    = 5'd6;
    parameter logic [REG_IDX_W-1:0] SYS_GEN_BOUNDARY  = 5'd7;
    parameter logic [REG_IDX_W-1:0] SYS_QUEUE_BASE    = 5'd8;
    parameter logic [REG_IDX_W-1:0] SYS_GC_STATUS     = 5'd9;
    parameter logic [REG_IDX_W-1:0] SYS_PERF_CTR      = 5'd10;

    // SYS_INFO sub-codes for scanner result FIFO
    parameter logic [REG_IDX_W-1:0] SYS_SCAN_COUNT      = 5'd11;  // FIFO occupancy
    parameter logic [REG_IDX_W-1:0] SYS_SCAN_HEAD_OBJ   = 5'd12;  // head entry: obj addr
    parameter logic [REG_IDX_W-1:0] SYS_SCAN_HEAD_FIELD  = 5'd13;  // head entry: field index
    parameter logic [REG_IDX_W-1:0] SYS_SCAN_POP_REF     = 5'd14;  // head entry: ref (+ pop)

    // ---------------------------------------------------------------
    // GC engine command codes  (for ENQ.* opcodes → engine command port)
    // ---------------------------------------------------------------
    parameter logic [3:0] GC_CMD_SCAN    = 4'd1;
    parameter logic [3:0] GC_CMD_COPY    = 4'd2;
    parameter logic [3:0] GC_CMD_FIXUP   = 4'd3;
    parameter logic [3:0] GC_CMD_COMPACT = 4'd4;
    parameter logic [3:0] GC_CMD_FENCE   = 4'd5;

    // ---------------------------------------------------------------
    // Performance counter IDs
    // ---------------------------------------------------------------
    parameter logic [4:0] CTR_ALLOC_COUNT     = 5'd0;
    parameter logic [4:0] CTR_ALLOC_BYTES     = 5'd1;
    parameter logic [4:0] CTR_BARRIER_FIRES   = 5'd2;
    parameter logic [4:0] CTR_BARRIER_FILTERED = 5'd3;
    parameter logic [4:0] CTR_IC_HITS         = 5'd4;
    parameter logic [4:0] CTR_IC_MISSES       = 5'd5;
    parameter logic [4:0] CTR_GC_CYCLES       = 5'd6;
    parameter logic [4:0] CTR_NURSERY_OVERFLOWS = 5'd7;

    // ---------------------------------------------------------------
    // Message queue constants
    // ---------------------------------------------------------------
    parameter int QUEUE_COUNT    = 4;     // 4 queues per tile
    parameter int QUEUE_DEPTH    = 512;   // entries per queue

    // Queue IDs (in imm16 field of SEND/RECV)
    parameter logic [1:0] QUEUE_WORK_IN  = 2'd0;
    parameter logic [1:0] QUEUE_WORK_OUT = 2'd1;
    parameter logic [1:0] QUEUE_GC       = 2'd2;
    parameter logic [1:0] QUEUE_USER     = 2'd3;

    // ---------------------------------------------------------------
    // Decoded instruction — all fields extracted in parallel
    //
    // Fields overlap in the raw instruction word; the execute stage
    // selects the relevant fields based on the opcode.
    //
    //   Format R: opcode, rd, rs1, rs2, func
    //   Format I: opcode, rd, rs1, imm16
    //   Format S: opcode, rd(=rs), rs1(=rt), rs2(=field), imm11
    //   Format B: opcode, rd(=rs1), rs1(=cond), imm16(=offset)
    //   Format X: opcode, raw26
    // ---------------------------------------------------------------
    typedef struct packed {
        opcode_t                opcode;  // [31:26]  6 bits
        logic [REG_IDX_W-1:0]  rd;      // [25:21]  5 bits
        logic [REG_IDX_W-1:0]  rs1;     // [20:16]  5 bits
        logic [REG_IDX_W-1:0]  rs2;     // [15:11]  5 bits
        logic [FUNC_W-1:0]     func;    // [10:6]   5 bits
        logic [IMM16_W-1:0]    imm16;   // [15:0]  16 bits (raw, not sign-extended)
        logic [IMM11_W-1:0]    imm11;   // [10:0]  11 bits
        logic [RAW26_W-1:0]    raw26;   // [25:0]  26 bits
    } decoded_t;

    // ---------------------------------------------------------------
    // Helper functions
    // ---------------------------------------------------------------

    // --- Instruction decode ---

    function automatic decoded_t decode_inst(logic [ILEN-1:0] raw);
        decoded_t d;
        d.opcode = opcode_t'(raw[31:26]);
        d.rd     = raw[25:21];
        d.rs1    = raw[20:16];
        d.rs2    = raw[15:11];
        d.func   = raw[10:6];
        d.imm16  = raw[15:0];
        d.imm11  = raw[10:0];
        d.raw26  = raw[25:0];
        return d;
    endfunction

    // --- Sign extension ---

    function automatic logic [XLEN-1:0] sext16(logic [15:0] imm);
        return {{(XLEN-16){imm[15]}}, imm};
    endfunction

    function automatic logic [XLEN-1:0] sext11(logic [10:0] imm);
        return {{(XLEN-11){imm[10]}}, imm};
    endfunction

    // --- Tag checks ---

    // Fixnum: bit[0] == 0
    function automatic logic is_fixnum(logic [XLEN-1:0] w);
        return ~w[0];
    endfunction

    // Ref (non-cons): bits[1:0] == 01
    function automatic logic is_ref(logic [XLEN-1:0] w);
        return (w[1:0] == 2'b01);
    endfunction

    // Cons ref: bits[2:0] == 011
    function automatic logic is_cons_ref(logic [XLEN-1:0] w);
        return (w[2:0] == 3'b011);
    endfunction

    // Any heap ref (ref or cons): bit[0]==1 && bit[2]==0
    function automatic logic is_any_ref(logic [XLEN-1:0] w);
        return w[0] & ~w[2];
    endfunction

    // Special immediate: bits[2:0] == 101
    function automatic logic is_special(logic [XLEN-1:0] w);
        return (w[2:0] == TAG_SPECIAL);
    endfunction

    // Nil test
    function automatic logic is_nil(logic [XLEN-1:0] w);
        return (w == VAL_NIL);
    endfunction

    // Header: bits[2:0] == 111
    function automatic logic is_header(logic [XLEN-1:0] w);
        return (w[2:0] == TAG_HEADER);
    endfunction

    // Truthy: not NIL and not zero
    function automatic logic is_truthy(logic [XLEN-1:0] w);
        return (w != VAL_NIL) && (w != {XLEN{1'b0}});
    endfunction

    // Character: low byte == 0x35
    function automatic logic is_char(logic [XLEN-1:0] w);
        return (w[7:0] == CHAR_TAG_BYTE);
    endfunction

    // Short-float: low byte == 0x3D
    function automatic logic is_sfloat(logic [XLEN-1:0] w);
        return (w[7:0] == SFLOAT_TAG_BYTE);
    endfunction

    // --- Fixnum tag / untag ---

    // Tag: v << 1
    function automatic logic [XLEN-1:0] tag_fixnum(logic [XLEN-1:0] v);
        return {v[XLEN-2:0], 1'b0};
    endfunction

    // Untag: arithmetic right shift by 1
    function automatic logic signed [XLEN-1:0] untag_fixnum(logic [XLEN-1:0] w);
        return $signed(w) >>> 1;
    endfunction

    // --- Reference helpers ---

    // Extract address from ref (bits [50:3])
    function automatic logic [XLEN-1:0] ref_address(logic [XLEN-1:0] w);
        return w & REF_ADDR_MASK;
    endfunction

    // Build a ref word from an address
    function automatic logic [XLEN-1:0] make_ref(logic [XLEN-1:0] addr, logic cons);
        if (cons)
            return (addr & REF_ADDR_MASK) | {61'b0, TAG_CONS};
        else
            return (addr & REF_ADDR_MASK) | {61'b0, TAG_REF};
    endfunction

    // --- Header helpers ---

    function automatic logic [4:0] header_subtype(logic [XLEN-1:0] hdr);
        return hdr[HDR_SUB_HI:HDR_SUB_LO];
    endfunction

    function automatic logic [15:0] header_size(logic [XLEN-1:0] hdr);
        return hdr[HDR_SIZE_HI:HDR_SIZE_LO];
    endfunction

    function automatic logic [31:0] header_shape_id(logic [XLEN-1:0] hdr);
        return hdr[HDR_SHAPE_HI:HDR_SHAPE_LO];
    endfunction

    function automatic logic [XLEN-1:0] make_header(
        logic [4:0]  hdr_sub,
        logic [15:0] size,
        logic [31:0] shape_id
    );
        return {8'b0, shape_id, size, hdr_sub, TAG_HEADER};
    endfunction

    // Patch size into an existing header word
    function automatic logic [XLEN-1:0] header_patch_size(
        logic [XLEN-1:0] hdr,
        logic [15:0]     new_size
    );
        logic [XLEN-1:0] mask;
        mask = ~(64'hFFFF << HDR_SIZE_LO);
        return (hdr & mask) | ({48'b0, new_size} << HDR_SIZE_LO);
    endfunction

endpackage
