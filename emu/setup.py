"""Build script for the LM-1 C++ accelerator extension.

    pip install -e ./emu          # editable install (builds extension)
    python emu/setup.py build_ext --inplace   # build only
"""

from setuptools import setup, Extension

accel_ext = Extension(
    "lm1._accel_ext",
    sources=["lm1/_accel_ext.c"],
    extra_compile_args=["-O2", "-Wall"],
)

setup(
    ext_modules=[accel_ext],
)
