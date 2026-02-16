"""Phase 7 tests — BIOS.

Tests that the BIOS boots, initializes hardware, prints banner,
loads OS images from block device, and successfully hands off control.
"""

import io
import os
import struct
import tempfile

from lm1.testing.harness import test
from lm1.execute import Emulator
from lm1.asm import Assembler
from lm1.bios import (
    assemble_bios, make_os_image, IMAGE_MAGIC, IMAGE_LOAD_ADDR,
)
from lm1.word import (
    NIL, T, tag_fixnum, untag_fixnum,
    is_fixnum, is_cons_ref, make_header,
    HDR_CONS, HDR_CLOSURE, HDR_VECTOR,
    WORD_MASK,
)
from lm1.decode import Op, encode_x, encode_i

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

# Use memory layout compatible with BIOS expectations
# BIOS code at 0x0000, stack at 0x3FF8, image load at 0x10000
NURSERY_BASE = 0x3_0000
NURSERY_SIZE = 0x1_0000
OLDGEN_BASE  = 0x5_0000
OLDGEN_SIZE  = 0x2_0000


def _bios_emu(block_device: str | None = None, **kw) -> Emulator:
    """Create an emulator and load the BIOS."""
    defaults = dict(
        mem_size=1024 * 1024,  # 1 MiB
        nursery_base=NURSERY_BASE,
        nursery_size=NURSERY_SIZE,
        oldgen_base=OLDGEN_BASE,
        oldgen_size=OLDGEN_SIZE,
        stdout=io.StringIO(),
        block_device=block_device,
    )
    defaults.update(kw)
    emu = Emulator(**defaults)
    words, labels = assemble_bios()
    emu.load_bios(words, base=0)
    return emu, labels


def _make_test_os(message: str = "OS OK") -> bytes:
    """Create a minimal test OS image that prints a message and halts."""
    asm = Assembler()
    # OS entry point: print each char via TRAP 0x80, then HALT
    lines = ["; Minimal test OS"]
    for ch in message:
        lines.append(f"    LI r1, {ord(ch) << 1}")  # tag as fixnum
        lines.append(f"    TRAP 0x80")
    lines.append("    HALT")
    source = '\n'.join(lines)
    os_words = asm.assemble_to_words(source)
    return make_os_image(os_words, entry_offset=32)


# ===================================================================
# Batch: phase7_bios_init — BIOS initialization
# ===================================================================

@test("bios_assembles", batch="phase7_bios_init")
def test_bios_assembles():
    """BIOS source assembles without errors."""
    words, labels = assemble_bios()
    assert len(words) > 100, f"BIOS too small: {len(words)} words"
    assert 'bios_entry' in labels
    assert 'trap_table' in labels
    assert 'default_trap_handler' in labels
    assert labels['bios_entry'] == 0


@test("bios_prints_banner", batch="phase7_bios_init")
def test_bios_prints_banner():
    """BIOS boots and prints its banner (no block device → halts after 'No OS')."""
    emu, labels = _bios_emu()
    emu.run(max_instructions=200)
    output = emu._stdout.getvalue()
    assert "LM-1 BIOS v1.0" in output, f"Expected banner, got: {output!r}"


@test("bios_no_os_halts", batch="phase7_bios_init")
def test_bios_no_os_halts():
    """BIOS without block device prints 'No OS image' and halts."""
    emu, labels = _bios_emu()
    emu.run(max_instructions=200)
    output = emu._stdout.getvalue()
    assert "No OS image" in output, f"Expected 'No OS image', got: {output!r}"
    assert emu.thread.halted


@test("bios_installs_trap_table", batch="phase7_bios_init")
def test_bios_installs_trap_table():
    """BIOS installs the trap table base address."""
    emu, labels = _bios_emu()
    emu.run(max_instructions=200)
    trap_base = emu.thread.trap_table_base
    assert trap_base == labels['trap_table'], \
        f"trap_table_base={trap_base:#x}, expected {labels['trap_table']:#x}"


@test("bios_installs_templates", batch="phase7_bios_init")
def test_bios_installs_templates():
    """BIOS installs header templates for cons, closure, vector."""
    emu, labels = _bios_emu()
    emu.run(max_instructions=200)
    t = emu.thread
    # Cons: make_header(1, 2, 0) = 0x2000F
    expected_cons = make_header(HDR_CONS, 2, 0)
    assert t.header_templates[0] == expected_cons, \
        f"cons template: {t.header_templates[0]:#x}, expected {expected_cons:#x}"
    # Closure: make_header(4, 0, 0) = 0x27
    assert t.header_templates[1] == make_header(HDR_CLOSURE, 0, 0)
    # Vector: make_header(2, 0, 0) = 0x17
    assert t.header_templates[2] == make_header(HDR_VECTOR, 0, 0)


# ===================================================================
# Batch: phase7_block_io — block device emulation
# ===================================================================

@test("block_io_read", batch="phase7_block_io")
def test_block_io_read():
    """TRAP 0x82 reads a block from host file into emulator memory."""
    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as f:
        # Write a 4096-byte block with known pattern
        data = bytes(range(256)) * 16  # 4096 bytes
        f.write(data)
        f.flush()
        fname = f.name

    try:
        asm = Assembler()
        source = f"""
            LI r1, 0        ; BLOCK_READ
            LI r2, 0        ; block 0
            LI r3, 0x4000   ; destination
            TRAP 0x82
            HALT
        """
        words = asm.assemble_to_words(source)
        emu = Emulator(
            mem_size=256 * 1024,
            nursery_base=0x30000, nursery_size=0x10000,
            oldgen_base=0x50000, oldgen_size=0x20000,
            block_device=fname,
        )
        for i, w in enumerate(words):
            emu.mem.store_u32(i * 4, w)
        emu.thread.pc = 0
        emu.run(max_instructions=20)

        # Verify first few bytes
        for i in range(16):
            b = emu.mem.load_byte(0x4000 + i)
            assert b == i, f"byte[{i}]={b}, expected {i}"
        # r0 should be tag_fixnum(0) = 0 (success)
        assert emu.thread.regs[0] == tag_fixnum(0)
    finally:
        os.unlink(fname)


@test("block_io_no_device", batch="phase7_block_io")
def test_block_io_no_device():
    """TRAP 0x82 without block device returns error code."""
    asm = Assembler()
    source = """
        LI r1, 0        ; BLOCK_READ
        LI r2, 0        ; block 0
        LI r3, 0x4000   ; destination
        TRAP 0x82
        HALT
    """
    words = asm.assemble_to_words(source)
    emu = Emulator(mem_size=256 * 1024,
                   nursery_base=0x30000, nursery_size=0x10000,
                   oldgen_base=0x50000, oldgen_size=0x20000)
    for i, w in enumerate(words):
        emu.mem.store_u32(i * 4, w)
    emu.thread.pc = 0
    emu.run(max_instructions=20)
    # r0 = tag_fixnum(-1) = -2 (error)
    assert emu.thread.regs[0] == tag_fixnum(-1), \
        f"Expected error code, got {emu.thread.regs[0]:#x}"


@test("block_io_write_read", batch="phase7_block_io")
def test_block_io_write_read():
    """TRAP 0x82 write then read roundtrips data correctly."""
    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as f:
        fname = f.name

    try:
        asm = Assembler()
        # Write block 0 with pattern, then read it back to a different addr
        source = """
            ; Store test pattern at 0x4000
            LI r5, 0x4000
            LI r6, 0xAB
            STR r5, r6, 0
            ; Write block 0
            LI r1, 1        ; BLOCK_WRITE
            LI r2, 0        ; block 0
            LI r3, 0x4000   ; source
            TRAP 0x82
            ; Read block 0 to 0x5000
            LI r1, 0        ; BLOCK_READ
            LI r2, 0        ; block 0
            LI r3, 0x5000   ; destination
            TRAP 0x82
            ; Load first word from destination
            LI r5, 0x5000
            LDR r7, r5, 0
            HALT
        """
        words = asm.assemble_to_words(source)
        emu = Emulator(
            mem_size=256 * 1024,
            nursery_base=0x30000, nursery_size=0x10000,
            oldgen_base=0x50000, oldgen_size=0x20000,
            block_device=fname,
        )
        for i, w in enumerate(words):
            emu.mem.store_u32(i * 4, w)
        emu.thread.pc = 0
        emu.run(max_instructions=40)
        # r7 should contain the same value as what was stored at 0x4000
        assert emu.thread.regs[7] == 0xAB, \
            f"Roundtrip failed: got {emu.thread.regs[7]:#x}"
    finally:
        os.unlink(fname)


# ===================================================================
# Batch: phase7_jr — JR (jump register) instruction
# ===================================================================

@test("jr_basic", batch="phase7_jr")
def test_jr_basic():
    """JR jumps to address in register."""
    asm = Assembler()
    source = """
        LI r5, target
        JR r5
        HALT            ; should be skipped
    target:
        LI r1, 42
        HALT
    """
    words = asm.assemble_to_words(source)
    emu = Emulator(mem_size=256 * 1024,
                   nursery_base=0x30000, nursery_size=0x10000,
                   oldgen_base=0x50000, oldgen_size=0x20000)
    for i, w in enumerate(words):
        emu.mem.store_u32(i * 4, w)
    emu.thread.pc = 0
    emu.run(max_instructions=20)
    assert emu.thread.regs[1] == 42, f"Expected 42, got {emu.thread.regs[1]}"


# ===================================================================
# Batch: phase7_os_boot — full boot from BIOS to OS
# ===================================================================

@test("bios_boots_os_image", batch="phase7_os_boot")
def test_bios_boots_os_image():
    """BIOS loads OS image from block device and jumps to it."""
    # Create a tiny OS that writes 42 to r20 and halts
    asm = Assembler()
    os_source = """
        LI r20, 42
        HALT
    """
    os_words = asm.assemble_to_words(os_source)
    os_image = make_os_image(os_words)

    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as f:
        f.write(os_image)
        f.flush()
        fname = f.name

    try:
        emu, labels = _bios_emu(block_device=fname)
        emu.run(max_instructions=500)
        output = emu._stdout.getvalue()
        assert "LM-1 BIOS v1.0" in output, f"No banner: {output!r}"
        assert emu.thread.halted
        assert emu.thread.regs[20] == 42, \
            f"OS didn't execute: r20={emu.thread.regs[20]}"
    finally:
        os.unlink(fname)


@test("bios_os_prints", batch="phase7_os_boot")
def test_bios_os_prints():
    """BIOS loads OS that prints a message via TRAP 0x80."""
    os_image = _make_test_os("Hello from OS!")

    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as f:
        f.write(os_image)
        f.flush()
        fname = f.name

    try:
        emu, labels = _bios_emu(block_device=fname)
        emu.run(max_instructions=1000)
        output = emu._stdout.getvalue()
        assert "LM-1 BIOS v1.0" in output
        assert "Hello from OS!" in output, f"OS output missing: {output!r}"
    finally:
        os.unlink(fname)


@test("bios_bad_magic_halts", batch="phase7_os_boot")
def test_bios_bad_magic_halts():
    """BIOS rejects image with wrong magic and prints error."""
    # Create a corrupt image (bad magic)
    bad_image = struct.pack('<QQQQ', 0xDEADBEEF, 1, 32, 64)
    bad_image += b'\x00' * 32  # dummy code

    with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as f:
        f.write(bad_image)
        f.flush()
        fname = f.name

    try:
        emu, labels = _bios_emu(block_device=fname)
        emu.run(max_instructions=500)
        output = emu._stdout.getvalue()
        assert "Bad magic" in output, f"Expected 'Bad magic', got: {output!r}"
        assert emu.thread.halted
    finally:
        os.unlink(fname)


@test("os_image_format", batch="phase7_os_boot")
def test_os_image_format():
    """make_os_image creates correctly formatted OS images."""
    asm = Assembler()
    code = asm.assemble_to_words("HALT")
    img = make_os_image(code)

    # Parse header
    magic, version, entry, size = struct.unpack('<QQQQ', img[:32])
    assert magic == IMAGE_MAGIC, f"magic={magic:#x}"
    assert version == 1
    assert entry == 32  # default entry offset
    assert size == 32 + len(code) * 4
