"""LM-1 memory subsystem.

Flat byte-addressable memory backed by an array of 64-bit words.
Provides word-aligned reads/writes plus sub-word accessors.
"""

from __future__ import annotations

import array
from typing import Optional

from .word import WORD_MASK


class Memory:
    """Flat, byte-addressable memory.

    Internally stored as an array of unsigned 64-bit words.
    Total size is given in bytes and must be a multiple of 8.
    """

    __slots__ = ("_data", "_size")

    def __init__(self, size_bytes: int):
        assert size_bytes % 8 == 0, "Memory size must be a multiple of 8"
        self._size = size_bytes
        word_count = size_bytes // 8
        # 'Q' = unsigned long long (uint64)
        self._data = array.array("Q", (0 for _ in range(word_count)))

    @property
    def size(self) -> int:
        return self._size

    # -- word access (8-byte aligned) ---

    def load_word(self, addr: int) -> int:
        """Load a 64-bit word.  addr must be 8-byte aligned."""
        return self._data[addr >> 3]

    def store_word(self, addr: int, value: int) -> None:
        """Store a 64-bit word.  addr must be 8-byte aligned."""
        self._data[addr >> 3] = value & WORD_MASK

    # -- 32-bit access (4-byte aligned, for instruction fetch) ---

    def load_u32(self, addr: int) -> int:
        """Load a 32-bit value from a 4-byte-aligned address.

        Instructions are 32-bit; two fit in one 64-bit word.
        Low 32 bits of a word are at the even 4-byte offset,
        high 32 bits at the odd 4-byte offset (little-endian within word).
        """
        word_idx = addr >> 3
        if addr & 4:
            return (self._data[word_idx] >> 32) & 0xFFFF_FFFF
        else:
            return self._data[word_idx] & 0xFFFF_FFFF

    def store_u32(self, addr: int, value: int) -> None:
        """Store a 32-bit value at a 4-byte-aligned address."""
        word_idx = addr >> 3
        val32 = value & 0xFFFF_FFFF
        if addr & 4:
            self._data[word_idx] = (self._data[word_idx] & 0x0000_0000_FFFF_FFFF) | (val32 << 32)
        else:
            self._data[word_idx] = (self._data[word_idx] & 0xFFFF_FFFF_0000_0000) | val32

    # -- byte access ---

    def load_byte(self, addr: int) -> int:
        word = self._data[addr >> 3]
        shift = (addr & 7) * 8
        return (word >> shift) & 0xFF

    def store_byte(self, addr: int, value: int) -> None:
        word_idx = addr >> 3
        shift = (addr & 7) * 8
        mask = ~(0xFF << shift) & WORD_MASK
        self._data[word_idx] = (self._data[word_idx] & mask) | ((value & 0xFF) << shift)

    # -- 16-bit access ---

    def load_u16(self, addr: int) -> int:
        word = self._data[addr >> 3]
        shift = (addr & 7) * 8
        return (word >> shift) & 0xFFFF

    def store_u16(self, addr: int, value: int) -> None:
        word_idx = addr >> 3
        shift = (addr & 7) * 8
        mask = ~(0xFFFF << shift) & WORD_MASK
        self._data[word_idx] = (self._data[word_idx] & mask) | ((value & 0xFFFF) << shift)

    # -- 32-bit data access (may be at non-instruction-aligned addresses) ---

    def load_u32_data(self, addr: int) -> int:
        word = self._data[addr >> 3]
        shift = (addr & 7) * 8
        return (word >> shift) & 0xFFFF_FFFF

    def store_u32_data(self, addr: int, value: int) -> None:
        word_idx = addr >> 3
        shift = (addr & 7) * 8
        mask = ~(0xFFFF_FFFF << shift) & WORD_MASK
        self._data[word_idx] = (self._data[word_idx] & mask) | ((value & 0xFFFF_FFFF) << shift)

    # -- bulk operations ---

    def load_binary(self, base_addr: int, data: bytes) -> None:
        """Load raw bytes into memory at base_addr."""
        for i, b in enumerate(data):
            self.store_byte(base_addr + i, b)

    def load_words(self, base_addr: int, words: list[int]) -> None:
        """Load a list of 64-bit words starting at base_addr (must be 8-aligned)."""
        idx = base_addr >> 3
        for w in words:
            self._data[idx] = w & WORD_MASK
            idx += 1

    def load_instructions(self, base_addr: int, instructions: list[int]) -> None:
        """Load a list of 32-bit instructions starting at base_addr (must be 4-aligned)."""
        for i, inst in enumerate(instructions):
            self.store_u32(base_addr + i * 4, inst)

    def dump(self, addr: int, count: int) -> list[int]:
        """Return `count` 64-bit words starting at addr."""
        idx = addr >> 3
        return [self._data[idx + i] for i in range(count)]
