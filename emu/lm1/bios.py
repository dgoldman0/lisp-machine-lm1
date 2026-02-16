"""LM-1 BIOS — firmware that boots the machine and loads an OS image.

The BIOS:
  1. Initializes SP, FP
  2. Installs a trap table with default handlers
  3. Installs header templates via TRAP 0x91
  4. Prints the BIOS banner
  5. Attempts to load an OS image from block device (TRAP 0x82)
  6. Verifies the image magic ("LM1I")
  7. Jumps to the OS entry point, or halts if no image found

Memory layout (BIOS loaded at base 0x0000):
  0x0000 .. code     BIOS instructions
  code  .. data      trap table, strings, constants
"""

from __future__ import annotations

from .asm import Assembler
from .word import make_header, HDR_CONS, HDR_CLOSURE, HDR_VECTOR

# OS image header format (all 64-bit words):
#   offset 0:  magic   = 0x49_31_4D_4C ("LM1I" little-endian)
#   offset 8:  version = 1
#   offset 16: entry   = byte offset from image base to entry point
#   offset 24: size    = total image size in bytes (including header)
IMAGE_MAGIC = 0x49_31_4D_4C   # "LM1I" in little-endian

# Address where the OS image is loaded (blocks read here)
IMAGE_LOAD_ADDR = 0x0001_0000  # 64 KiB

# Stack top for BIOS (16 KiB stack: 0x8000 .. 0xBFFF)
BIOS_STACK_TOP = 0xBFF8

BIOS_SOURCE = r"""
; ===================================================================
; LM-1 BIOS v1.0
; ===================================================================

.equ EMU_PUTCHAR,   0x80
.equ EMU_GETCHAR,   0x81
.equ EMU_BLOCK_IO,  0x82
.equ EMU_SET_TRAP,  0x90
.equ EMU_SET_TMPL,  0x91
.equ EMU_DEBUG_PRT, 0x9F

.equ BLOCK_READ,    0
.equ IMAGE_MAGIC_LO, 0x4D4C
.equ IMAGE_MAGIC_HI, 0x4931

; ===================================================================
; Entry point
; ===================================================================
bios_entry:
    ; Set up stack
    LI  sp, 0x3FF8
    LI  fp, 0

    ; Install trap table
    LI  r1, trap_table
    TRAP EMU_SET_TRAP

    ; Install header templates
    CALL.DIRECT install_templates

    ; Print banner
    CALL.DIRECT print_banner

    ; Load OS image
    CALL.DIRECT load_os_image
    ; r1 = tag_fixnum(1) on success, 0 on failure
    BR.FIX.EQ r1, no_os

    ; Verify image magic
    LUI r10, 1              ; r10 = 0x10000 (image base)
    LDR r2, r10, 0          ; magic word from image
    LI  r3, IMAGE_MAGIC_LO
    LUI r4, IMAGE_MAGIC_HI
    ADD r3, r3, r4           ; r3 = expected magic 0x49314D4C
    EQ  r5, r2, r3
    BR.NIL r5, bad_magic

    ; Load entry offset and jump
    LDR r6, r10, 16         ; entry_offset
    ADD r6, r6, r10          ; absolute entry address
    MOV r1, r10              ; r1 = boot info (image base)
    JR  r6                   ; jump to OS entry

no_os:
    LI  r1, str_no_os
    LI  r2, str_no_os_len
    TRAP EMU_DEBUG_PRT
    HALT

bad_magic:
    LI  r1, str_bad_magic
    LI  r2, str_bad_magic_len
    TRAP EMU_DEBUG_PRT
    HALT

; ===================================================================
; Subroutines
; ===================================================================

print_banner:
    LI  r1, str_banner
    LI  r2, str_banner_len
    TRAP EMU_DEBUG_PRT
    RET

install_templates:
    ; Template 0: cons = make_header(1,2,0) = 0x20F
    ; Layout: tag(3)=111 | sub(5)=00001 | size(16)=2<<8 | shape(32)=0
    LI  r1, 0
    LI  r2, 0x020F
    TRAP EMU_SET_TMPL
    ; Template 1: closure = make_header(4,0,0) = 0x27
    LI  r1, 1
    LI  r2, 0x0027
    TRAP EMU_SET_TMPL
    ; Template 2: vector = make_header(2,0,0) = 0x17
    LI  r1, 2
    LI  r2, 0x0017
    TRAP EMU_SET_TMPL
    RET

load_os_image:
    ; Read block 0 from block device into 0x10000
    ; Returns r1 = tag_fixnum(1) on success, 0 on failure
    LI  r1, BLOCK_READ
    LI  r2, 0
    LUI r3, 1               ; 0x10000
    TRAP EMU_BLOCK_IO
    ; r0 = tag_fixnum(0) on success, tag_fixnum(-1) on error
    ; tag_fixnum(-1) = -2.  s64(-2) < 0 → use BR.FIX.LT
    MOV r1, r0
    BR.FIX.LT r1, load_fail
    ; Success
    LI  r1, 2               ; tag_fixnum(1)
    RET
load_fail:
    LI  r1, 0               ; tag_fixnum(0)
    RET

default_trap_handler:
    LI  r1, str_trap
    LI  r2, str_trap_len
    TRAP EMU_DEBUG_PRT
    HALT

; ===================================================================
; Data section
; ===================================================================
.align 4

str_banner:
.byte 0x4C  ; L
.byte 0x4D  ; M
.byte 0x2D  ; -
.byte 0x31  ; 1
.byte 0x20  ;
.byte 0x42  ; B
.byte 0x49  ; I
.byte 0x4F  ; O
.byte 0x53  ; S
.byte 0x20  ;
.byte 0x76  ; v
.byte 0x31  ; 1
.byte 0x2E  ; .
.byte 0x30  ; 0
.byte 0x0A  ; \n
.equ str_banner_len, 15

str_no_os:
.byte 0x4E  ; N
.byte 0x6F  ; o
.byte 0x20  ;
.byte 0x4F  ; O
.byte 0x53  ; S
.byte 0x20  ;
.byte 0x69  ; i
.byte 0x6D  ; m
.byte 0x61  ; a
.byte 0x67  ; g
.byte 0x65  ; e
.byte 0x2E  ; .
.byte 0x0A  ; \n
.equ str_no_os_len, 13

str_bad_magic:
.byte 0x42  ; B
.byte 0x61  ; a
.byte 0x64  ; d
.byte 0x20  ;
.byte 0x6D  ; m
.byte 0x61  ; a
.byte 0x67  ; g
.byte 0x69  ; i
.byte 0x63  ; c
.byte 0x2E  ; .
.byte 0x0A  ; \n
.equ str_bad_magic_len, 11

str_trap:
.byte 0x54  ; T
.byte 0x52  ; R
.byte 0x41  ; A
.byte 0x50  ; P
.byte 0x21  ; !
.byte 0x0A  ; \n
.equ str_trap_len, 6

.align 8
trap_table:
"""

# Separator: the trap table entries are generated programmatically
# because .word directives with forward references to `default_trap_handler`
# work since it's already defined above in the assembly pass.
# But we'll generate 128 .word entries.
_TRAP_TABLE_ENTRIES = 128

def _generate_trap_table() -> str:
    """Generate trap table entries as .word directives."""
    lines = []
    for i in range(_TRAP_TABLE_ENTRIES):
        lines.append(f".word default_trap_handler   ; trap {i:#04x}")
    return '\n'.join(lines)


def get_bios_source() -> str:
    """Return the complete BIOS assembly source."""
    return BIOS_SOURCE + _generate_trap_table() + '\n'


def assemble_bios() -> tuple[list[int], dict[str, int]]:
    """Assemble the BIOS and return (instruction_words, labels).

    Returns:
        words: list of 32-bit instruction words for load_instructions()
        labels: dict mapping label names to byte addresses
    """
    asm = Assembler()
    source = get_bios_source()
    binary = asm.assemble(source)

    # Pad binary to 4-byte alignment
    while len(binary) % 4:
        binary += b'\x00'

    # Convert bytes to list of 32-bit words
    words = []
    for i in range(0, len(binary), 4):
        w = int.from_bytes(binary[i:i+4], 'little')
        words.append(w)

    return words, dict(asm.labels)


def make_os_image(code_words: list[int], entry_offset: int = 32) -> bytes:
    """Create a minimal OS image with the standard header.

    Args:
        code_words: 32-bit instruction words for the OS code
        entry_offset: byte offset from image start to first instruction
                      (default 32 = after the 4-word header)

    Returns:
        Byte string containing the OS image (header + code)
    """
    import struct

    # Header: 4 × 64-bit words
    header = struct.pack('<QQQQ',
        IMAGE_MAGIC,       # magic
        1,                 # version
        entry_offset,      # entry offset (bytes from image base)
        32 + len(code_words) * 4,  # total image size
    )

    # Code section
    code = b''.join(w.to_bytes(4, 'little') for w in code_words)

    return header + code
