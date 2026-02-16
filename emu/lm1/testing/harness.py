"""Test harness core — registration, threaded execution, status tracking.

Usage in test files:

    from lm1.testing.harness import test, batch

    @test("scalar_arith", batch="phase1")
    def test_add():
        ...
        assert result == 42

The harness:
 - Runs each test function in its own thread (with a timeout).
 - Writes per-test status to a JSON file in /tmp/lm1-test-<run_id>/.
 - Writes a PID file so `make test-status` / `make test-kill` can find it.
 - Supports running a single batch or the full suite.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class Status(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    PASS     = "pass"
    FAIL     = "fail"
    ERROR    = "error"
    TIMEOUT  = "timeout"
    SKIPPED  = "skipped"


@dataclass
class TestEntry:
    name: str
    batch: str
    func: Callable
    timeout: float  # seconds


@dataclass
class TestResult:
    name: str
    batch: str
    status: str         # Status value
    elapsed: float = 0.0
    message: str = ""
    detail: str = ""    # traceback if error


_registry: list[TestEntry] = []
_batches: dict[str, list[TestEntry]] = {}


def test(name: str, *, batch: str = "default", timeout: float = 30.0):
    """Decorator to register a test function."""
    def decorator(func: Callable) -> Callable:
        entry = TestEntry(name=name, batch=batch, func=func, timeout=timeout)
        _registry.append(entry)
        _batches.setdefault(batch, []).append(entry)
        func._test_entry = entry
        return func
    return decorator


def get_registry() -> list[TestEntry]:
    return list(_registry)


def get_batches() -> dict[str, list[TestEntry]]:
    return dict(_batches)


def get_batch(name: str) -> list[TestEntry]:
    return list(_batches.get(name, []))


def list_batches() -> list[str]:
    return sorted(_batches.keys())


# ---------------------------------------------------------------------------
# Run directory (in /tmp)
# ---------------------------------------------------------------------------

RUN_DIR_PREFIX = "lm1-test-"
PID_FILENAME   = "pid"
STATUS_FILENAME = "status.json"
META_FILENAME   = "meta.json"


def _make_run_dir() -> Path:
    """Create a unique run directory in /tmp."""
    d = Path(tempfile.mkdtemp(prefix=RUN_DIR_PREFIX))
    return d


def _write_pid(run_dir: Path) -> None:
    (run_dir / PID_FILENAME).write_text(str(os.getpid()))


def _write_status(run_dir: Path, results: dict[str, TestResult]) -> None:
    data = {name: asdict(r) for name, r in results.items()}
    tmp = run_dir / (STATUS_FILENAME + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(run_dir / STATUS_FILENAME)


def _write_meta(run_dir: Path, meta: dict) -> None:
    (run_dir / META_FILENAME).write_text(json.dumps(meta, indent=2))


# ---------------------------------------------------------------------------
# Find latest run dir
# ---------------------------------------------------------------------------

def find_latest_run_dir() -> Optional[Path]:
    """Find the most recent lm1-test-* directory in /tmp."""
    tmp = Path(tempfile.gettempdir())
    candidates = sorted(
        (d for d in tmp.iterdir()
         if d.is_dir() and d.name.startswith(RUN_DIR_PREFIX)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def read_status(run_dir: Path) -> dict[str, dict]:
    """Read the status file from a run directory."""
    sf = run_dir / STATUS_FILENAME
    if not sf.exists():
        return {}
    return json.loads(sf.read_text())


def read_pid(run_dir: Path) -> Optional[int]:
    pf = run_dir / PID_FILENAME
    if not pf.exists():
        return None
    try:
        return int(pf.read_text().strip())
    except ValueError:
        return None


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests(
    entries: list[TestEntry],
    *,
    run_dir: Optional[Path] = None,
    parallelism: int = 4,
    verbose: bool = False,
) -> dict[str, TestResult]:
    """Run a list of test entries with threading and timeout support.

    Each test gets its own thread.  We run up to `parallelism` at a time,
    flushing status to disk after each completes.
    """
    if run_dir is None:
        run_dir = _make_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_pid(run_dir)

    # Build initial results (all pending)
    results: dict[str, TestResult] = {}
    for e in entries:
        results[e.name] = TestResult(
            name=e.name, batch=e.batch, status=Status.PENDING.value,
        )
    _write_status(run_dir, results)

    _write_meta(run_dir, {
        "pid": os.getpid(),
        "started": time.time(),
        "batches": sorted({e.batch for e in entries}),
        "total": len(entries),
        "run_dir": str(run_dir),
    })

    if verbose:
        print(f"Run dir: {run_dir}", file=sys.stderr)
        print(f"Tests:   {len(entries)}", file=sys.stderr)

    semaphore = threading.Semaphore(parallelism)
    lock = threading.Lock()
    threads: list[threading.Thread] = []

    def _run_one(entry: TestEntry):
        semaphore.acquire()
        try:
            with lock:
                results[entry.name].status = Status.RUNNING.value
                _write_status(run_dir, results)

            t0 = time.monotonic()
            try:
                entry.func()
                elapsed = time.monotonic() - t0
                with lock:
                    results[entry.name].status = Status.PASS.value
                    results[entry.name].elapsed = round(elapsed, 4)
            except AssertionError as e:
                elapsed = time.monotonic() - t0
                with lock:
                    results[entry.name].status = Status.FAIL.value
                    results[entry.name].elapsed = round(elapsed, 4)
                    results[entry.name].message = str(e)
                    results[entry.name].detail = traceback.format_exc()
            except Exception as e:
                elapsed = time.monotonic() - t0
                with lock:
                    results[entry.name].status = Status.ERROR.value
                    results[entry.name].elapsed = round(elapsed, 4)
                    results[entry.name].message = f"{type(e).__name__}: {e}"
                    results[entry.name].detail = traceback.format_exc()
            finally:
                with lock:
                    _write_status(run_dir, results)
        finally:
            semaphore.release()

    # Start all threads
    for entry in entries:
        t = threading.Thread(target=_run_one, args=(entry,), name=entry.name,
                             daemon=True)
        threads.append(t)
        t.start()

    # Join with per-test timeouts
    for t, entry in zip(threads, entries):
        t.join(timeout=entry.timeout)
        if t.is_alive():
            with lock:
                results[entry.name].status = Status.TIMEOUT.value
                results[entry.name].message = f"Timed out after {entry.timeout}s"
                _write_status(run_dir, results)

    # Final write
    _write_status(run_dir, results)

    # Write completion meta
    meta = json.loads((run_dir / META_FILENAME).read_text())
    meta["finished"] = time.time()
    meta["duration"] = round(meta["finished"] - meta["started"], 3)
    counts = {}
    for r in results.values():
        counts[r.status] = counts.get(r.status, 0) + 1
    meta["counts"] = counts
    _write_meta(run_dir, meta)

    return results


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------

_STATUS_SYMBOLS = {
    "pending": ".",
    "running": "~",
    "pass":    "\033[32m✓\033[0m",
    "fail":    "\033[31m✗\033[0m",
    "error":   "\033[31m!\033[0m",
    "timeout": "\033[33m⏱\033[0m",
    "skipped": "\033[36m-\033[0m",
}

_STATUS_LABELS = {
    "pending": ".",
    "running": "~",
    "pass":    "OK",
    "fail":    "FAIL",
    "error":   "ERR",
    "timeout": "TIME",
    "skipped": "SKIP",
}


def format_results(results: dict[str, dict], *, color: bool = True) -> str:
    """Format results dict (as read from status.json) into a human-readable report."""
    lines = []
    syms = _STATUS_SYMBOLS if color else _STATUS_LABELS

    # Group by batch
    by_batch: dict[str, list[dict]] = {}
    for name, r in results.items():
        b = r.get("batch", "default")
        by_batch.setdefault(b, []).append(r)

    for batch in sorted(by_batch):
        lines.append(f"\n  [{batch}]")
        for r in by_batch[batch]:
            sym = syms.get(r["status"], "?")
            elapsed_str = f" ({r['elapsed']:.3f}s)" if r.get("elapsed") else ""
            msg = f"  {r['message']}" if r.get("message") else ""
            lines.append(f"    {sym} {r['name']}{elapsed_str}{msg}")

    # Summary
    counts: dict[str, int] = {}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = len(results)
    passed = counts.get("pass", 0)
    failed = counts.get("fail", 0)
    errors = counts.get("error", 0)
    timeouts = counts.get("timeout", 0)
    running = counts.get("running", 0)
    pending = counts.get("pending", 0)

    summary_parts = [f"{total} tests"]
    if passed:   summary_parts.append(f"\033[32m{passed} passed\033[0m" if color else f"{passed} passed")
    if failed:   summary_parts.append(f"\033[31m{failed} failed\033[0m" if color else f"{failed} failed")
    if errors:   summary_parts.append(f"\033[31m{errors} errors\033[0m" if color else f"{errors} errors")
    if timeouts: summary_parts.append(f"\033[33m{timeouts} timed out\033[0m" if color else f"{timeouts} timed out")
    if running:  summary_parts.append(f"{running} running")
    if pending:  summary_parts.append(f"{pending} pending")

    lines.append(f"\n  {', '.join(summary_parts)}")

    # Show failures/errors detail
    for r in results.values():
        if r["status"] in ("fail", "error") and r.get("detail"):
            lines.append(f"\n  --- {r['name']} ---")
            lines.append(f"  {r['detail'].rstrip()}")

    return "\n".join(lines)
