/*
 * LM-1 Emulator — C++ Acceleration Extension
 *
 * Provides a fast fetch-decode-execute inner loop as a CPython C extension.
 * Built with setuptools; falls back to pure Python (or PyPy JIT) if
 * this module isn't compiled.
 *
 * Phase 1 stub: implements scalar ops, branches, LI/LUI, HALT/NOP,
 * and emulator I/O traps.
 *
 * Build: pip install -e ./emu   (or: python emu/setup.py build_ext --inplace)
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>

/* ---- Constants ---- */
#define WORD_MASK  0xFFFFFFFFFFFFFFFFULL
#define SIGN_BIT   (1ULL << 63)
#define NIL_VAL    0x05ULL
#define T_VAL      0x0DULL

/* Opcodes (must match lm1/decode.py) */
#define OP_ARITH_RAW  48
#define OP_BITWISE    49
#define OP_LDR        50
#define OP_STR        51
#define OP_BR         52
#define OP_BR_COND    53
#define OP_PUSH_POP   54
#define OP_LI         55
#define OP_LUI        56
#define OP_TRAP       60
#define OP_ERET       61
#define OP_SYS_INFO   62
#define OP_HALT_NOP   63

/* Emulator I/O traps */
#define EMU_TRAP_PUTCHAR 0x80
#define EMU_TRAP_GETCHAR 0x81

/* ---- Helpers ---- */

static inline int16_t sign_extend_16(uint16_t val) {
    return (int16_t)val;
}

static inline int64_t s64(uint64_t v) {
    return (int64_t)v;
}

static inline bool is_fixnum(uint64_t w) {
    return (w & 1) == 0;
}

static inline bool is_truthy(uint64_t w) {
    return w != NIL_VAL && w != 0;
}

/*
 * step_n(regs, memory_buffer, pc, n_instructions)
 *
 * Runs n instructions on the given register array and memory buffer.
 * Returns (new_pc, instructions_executed, halt_flag, trap_code_or_minus1).
 *
 * regs: array.array('Q', ...) of 32 uint64
 * memory_buffer: bytearray or array.array('Q', ...) buffer
 * pc: int — current program counter
 * n: int — max instructions to execute
 */
static PyObject *
accel_step_n(PyObject *self, PyObject *args)
{
    Py_buffer regs_buf, mem_buf;
    uint64_t pc;
    int n;

    if (!PyArg_ParseTuple(args, "y*y*Ki",
                          &regs_buf, &mem_buf, &pc, &n))
        return NULL;

    uint64_t *regs = (uint64_t *)regs_buf.buf;
    uint64_t *mem  = (uint64_t *)mem_buf.buf;
    size_t mem_words = mem_buf.len / 8;

    int executed = 0;
    int halted = 0;
    int trap_code = -1;

    for (int i = 0; i < n; i++) {
        /* Fetch 32-bit instruction */
        size_t word_idx = pc / 8;
        uint32_t raw;
        if (pc & 4)
            raw = (uint32_t)(mem[word_idx] >> 32);
        else
            raw = (uint32_t)(mem[word_idx] & 0xFFFFFFFF);

        /* Decode */
        int opcode  = (raw >> 26) & 0x3F;
        int rd      = (raw >> 21) & 0x1F;
        int rs1     = (raw >> 16) & 0x1F;
        int rs2     = (raw >> 11) & 0x1F;
        int func    = (raw >> 6)  & 0x1F;
        int16_t imm16 = sign_extend_16(raw & 0xFFFF);
        uint32_t raw26 = raw & 0x03FFFFFF;

        uint64_t next_pc = pc + 4;

        switch (opcode) {
        case OP_ARITH_RAW: {
            uint64_t a = regs[rs1], b = regs[rs2];
            switch (func) {
            case 0: regs[rd] = a + b; break;
            case 1: regs[rd] = a - b; break;
            case 2: regs[rd] = a * b; break;
            case 3:
                if (b == 0) { trap_code = 0x03; goto done; }
                regs[rd] = a / b;
                break;
            case 4:
                if (b == 0) { trap_code = 0x03; goto done; }
                regs[rd] = a % b;
                break;
            }
            break;
        }

        case OP_BITWISE: {
            uint64_t a = regs[rs1], b = regs[rs2];
            switch (func) {
            case 0: regs[rd] = a & b; break;
            case 1: regs[rd] = a | b; break;
            case 2: regs[rd] = a ^ b; break;
            case 3: regs[rd] = a << (b & 63); break;
            case 4: regs[rd] = a >> (b & 63); break;
            case 5: regs[rd] = (uint64_t)(s64(a) >> (b & 63)); break;
            case 6: regs[rd] = ~a; break;
            }
            break;
        }

        case OP_LDR: {
            uint64_t addr = (regs[rs1] + imm16) & ~(uint64_t)7;
            size_t wi = addr / 8;
            if (wi < mem_words)
                regs[rd] = mem[wi];
            break;
        }

        case OP_STR: {
            uint64_t addr = (regs[rd] + imm16) & ~(uint64_t)7;
            size_t wi = addr / 8;
            if (wi < mem_words)
                mem[wi] = regs[rs1];
            break;
        }

        case OP_LI:
            regs[rd] = (uint64_t)(int64_t)imm16;
            break;

        case OP_LUI:
            regs[rd] = ((uint64_t)(uint16_t)imm16) << 16;
            break;

        case OP_BR:
            next_pc = pc + ((int64_t)imm16 * 4);
            break;

        case OP_BR_COND: {
            uint64_t val1 = regs[rs1];
            bool taken = false;
            switch (rs2) {
            case 0: taken = is_truthy(val1); break;
            case 1: taken = (val1 == NIL_VAL); break;
            case 2: taken = is_fixnum(val1) && s64(val1) < 0; break;
            case 3: taken = (val1 == 0); break;
            case 4: taken = is_fixnum(val1) && s64(val1) > 0 && val1 != 0; break;
            case 5: taken = (val1 == 0); break;
            }
            if (taken)
                next_pc = pc + ((int64_t)imm16 * 4);
            break;
        }

        case OP_PUSH_POP:
            if (func == 0) { /* PUSH */
                regs[30] -= 8;
                size_t wi = regs[30] / 8;
                if (wi < mem_words)
                    mem[wi] = regs[rd];
            } else if (func == 1) { /* POP */
                size_t wi = regs[30] / 8;
                if (wi < mem_words)
                    regs[rd] = mem[wi];
                regs[30] += 8;
            }
            break;

        case OP_TRAP:
            trap_code = raw26 & 0xFF;
            goto done;

        case OP_SYS_INFO:
            /* For now, just return 0 for TILE.ID/THREAD.ID/CYCLE */
            switch (rs1) {
            case 0: regs[rd] = 0; break;  /* TILE.ID */
            case 1: regs[rd] = 0; break;  /* THREAD.ID */
            case 2: regs[rd] = (uint64_t)executed; break;  /* CYCLE */
            }
            break;

        case OP_HALT_NOP:
            if (((raw26 >> 21) & 0x1F) == 0) {
                halted = 1;
                executed++;
                goto done;
            }
            /* NOP: do nothing */
            break;

        default:
            /* Unhandled opcode — return to Python for dispatch */
            trap_code = 0xFE;
            goto done;
        }

        pc = next_pc;
        executed++;
    }

done:
    PyBuffer_Release(&regs_buf);
    PyBuffer_Release(&mem_buf);

    return Py_BuildValue("(Kiii)", pc, executed, halted, trap_code);
}

static PyMethodDef AccelMethods[] = {
    {"step_n", accel_step_n, METH_VARARGS,
     "Execute N instructions on the LM-1 emulator (C++ fast path)."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef accelmodule = {
    PyModuleDef_HEAD_INIT,
    "_accel_ext",
    "LM-1 C++ acceleration extension",
    -1,
    AccelMethods
};

PyMODINIT_FUNC
PyInit__accel_ext(void)
{
    return PyModule_Create(&accelmodule);
}
