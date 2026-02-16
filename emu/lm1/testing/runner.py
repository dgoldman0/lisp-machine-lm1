"""Test runner CLI.

Usage (typically called by the Makefile):

    python -m lm1.testing.runner                    # run all batches
    python -m lm1.testing.runner --batch phase1     # run one batch
    python -m lm1.testing.runner --list             # list batches and tests
    python -m lm1.testing.runner --status           # peek at last run
    python -m lm1.testing.runner --kill             # kill a hanging run
"""

from __future__ import annotations

import argparse
import importlib
import os
import signal
import sys
from pathlib import Path

from .harness import (
    get_registry, get_batches, get_batch, list_batches,
    run_tests, format_results, find_latest_run_dir,
    read_status, read_pid, is_process_alive,
)


def _discover_tests(test_dir: Path) -> None:
    """Import all test_*.py files in test_dir to trigger @test registration."""
    if not test_dir.is_dir():
        return
    # Ensure test dir's parent is on sys.path for imports
    parent = str(test_dir.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    for f in sorted(test_dir.glob("test_*.py")):
        module_name = f"tests.{f.stem}"
        try:
            importlib.import_module(module_name)
        except Exception as e:
            print(f"  Warning: failed to import {f.name}: {e}", file=sys.stderr)


def cmd_run(args: argparse.Namespace) -> int:
    """Run tests."""
    if args.batch:
        entries = get_batch(args.batch)
        if not entries:
            print(f"No tests in batch '{args.batch}'", file=sys.stderr)
            print(f"Available batches: {', '.join(list_batches())}", file=sys.stderr)
            return 1
    else:
        entries = get_registry()
        if not entries:
            print("No tests registered.", file=sys.stderr)
            return 1

    results = run_tests(
        entries,
        parallelism=args.jobs,
        verbose=True,
    )

    color = sys.stdout.isatty()
    # Convert TestResult objects to dicts for format_results
    results_dict = {
        name: {"name": r.name, "batch": r.batch, "status": r.status,
               "elapsed": r.elapsed, "message": r.message, "detail": r.detail}
        for name, r in results.items()
    }
    print(format_results(results_dict, color=color))

    # Exit code: 0 if all pass, 1 if any fail/error/timeout
    if all(r.status == "pass" for r in results.values()):
        return 0
    return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show status of the most recent test run."""
    run_dir = find_latest_run_dir()
    if run_dir is None:
        print("No test runs found in /tmp", file=sys.stderr)
        return 1

    results = read_status(run_dir)
    pid = read_pid(run_dir)

    alive = pid is not None and is_process_alive(pid)
    state = "RUNNING" if alive else "FINISHED"

    print(f"  Run dir:  {run_dir}")
    print(f"  PID:      {pid or '?'} ({state})")

    if not results:
        print("  No status file yet.")
        return 0

    color = sys.stdout.isatty()
    print(format_results(results, color=color))
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    """Kill a running test process."""
    run_dir = find_latest_run_dir()
    if run_dir is None:
        print("No test runs found.", file=sys.stderr)
        return 1

    pid = read_pid(run_dir)
    if pid is None:
        print("No PID file found.", file=sys.stderr)
        return 1

    if not is_process_alive(pid):
        print(f"Process {pid} is not running.", file=sys.stderr)
        return 0

    print(f"Sending SIGTERM to {pid}...")
    os.kill(pid, signal.SIGTERM)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List available batches and tests."""
    batches = get_batches()
    if not batches:
        print("No tests registered.", file=sys.stderr)
        return 1

    for batch_name in sorted(batches):
        entries = batches[batch_name]
        print(f"\n  [{batch_name}] ({len(entries)} tests)")
        for e in entries:
            print(f"    - {e.name}  (timeout={e.timeout}s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lm1-test", description="LM-1 Test Harness")
    parser.add_argument("--test-dir", type=Path, default=None,
                        help="Directory containing test_*.py files")

    sub = parser.add_subparsers(dest="command")

    # run
    run_p = sub.add_parser("run", help="Run tests")
    run_p.add_argument("--batch", "-b", type=str, default=None,
                       help="Run only this batch")
    run_p.add_argument("--jobs", "-j", type=int, default=4,
                       help="Parallelism (default: 4)")

    # status
    sub.add_parser("status", help="Peek at the last test run")

    # kill
    sub.add_parser("kill", help="Kill a hanging test run")

    # list
    sub.add_parser("list", help="List batches and tests")

    args = parser.parse_args(argv)

    # Discover tests
    test_dir = args.test_dir
    if test_dir is None:
        # Auto-detect: look for tests/ relative to emu/
        candidates = [
            Path(__file__).resolve().parent.parent.parent / "tests",
            Path.cwd() / "tests",
        ]
        for c in candidates:
            if c.is_dir():
                test_dir = c
                break
    if test_dir:
        _discover_tests(test_dir)

    if args.command is None or args.command == "run":
        # Default to run
        if args.command is None:
            # Need to fake the args
            args.batch = None
            args.jobs = 4
        return cmd_run(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "kill":
        return cmd_kill(args)
    elif args.command == "list":
        return cmd_list(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
