"""C++ accelerator for the LM-1 inner loop.

If the compiled extension is available, import the fast path.
Otherwise, fall back to the pure-Python implementation (PyPy-friendly).
"""

try:
    from lm1._accel_ext import step_n as _step_n_native  # type: ignore[import-not-found]
    ACCEL_AVAILABLE = True
except ImportError:
    _step_n_native = None
    ACCEL_AVAILABLE = False


def accel_available() -> bool:
    """Return True if the C++ accelerator is loaded."""
    return ACCEL_AVAILABLE
