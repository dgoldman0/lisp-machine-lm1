"""LM-1 emulator CLI.

Usage:
    lm1 run <binary>     Load a flat binary and run it
    lm1 run --trace <binary>   Run with trace output

The binary is loaded at address 0x0000_0000 (or a configurable base)
and execution starts at address 0x0000_0000.
"""

from __future__ import annotations

import argparse
import sys

from .execute import Emulator
from .traps import LM1Trap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lm1",
        description="LM-1 Lisp Machine Emulator",
    )
    sub = parser.add_subparsers(dest="command")

    # --- run ---
    run_p = sub.add_parser("run", help="Load and run a flat binary")
    run_p.add_argument("binary", help="Path to flat binary file")
    run_p.add_argument("--base", type=lambda x: int(x, 0), default=0,
                       help="Load base address (default: 0)")
    run_p.add_argument("--entry", type=lambda x: int(x, 0), default=None,
                       help="Entry point address (default: same as base)")
    run_p.add_argument("--mem", type=lambda x: int(x, 0), default=4 * 1024 * 1024,
                       help="Memory size in bytes (default: 4 MiB)")
    run_p.add_argument("--trace", action="store_true",
                       help="Print each instruction as it executes")
    run_p.add_argument("--max-insn", type=int, default=0,
                       help="Max instructions to execute (0 = unlimited)")
    run_p.add_argument("--stack", type=lambda x: int(x, 0), default=None,
                       help="Initial stack pointer (default: top of memory)")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "run":
        return cmd_run(args)

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # Load binary
    with open(args.binary, "rb") as f:
        data = f.read()

    emu = Emulator(mem_size=args.mem, trace=args.trace)

    # Load into memory
    emu.mem.load_binary(args.base, data)

    # Set entry point
    entry = args.entry if args.entry is not None else args.base
    emu.thread.pc = entry

    # Set stack pointer (default: top of memory)
    sp = args.stack if args.stack is not None else args.mem
    emu.thread.sp = sp

    try:
        emu.run(max_instructions=args.max_insn)
    except LM1Trap as e:
        print(f"\n--- TRAP at PC={emu.thread.pc:#010x}: {e} ---",
              file=sys.stderr)
        print(emu.thread.dump_regs(), file=sys.stderr)
        return 1

    if emu.trace:
        print(f"\n--- Halted after {emu.instruction_count} instructions ---",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
