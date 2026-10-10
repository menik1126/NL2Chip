"""Archon-style orchestration for NL2Chip circuit generation experiments.

This package is intentionally additive: it provides a benchmark-oriented
runner that can use Archon harness concepts without replacing the existing
``agent/search.py`` path.

- ``run``: Sparkle / Lean generation harness
- ``run_verilog``: direct SystemVerilog Archon baseline
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"
__all__ = ["__version__", "run", "run_verilog"]


def __getattr__(name: str) -> Any:
    if name == "run":
        from . import run as run_mod

        return run_mod
    if name == "run_verilog":
        from . import run_verilog as run_verilog_mod

        return run_verilog_mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
