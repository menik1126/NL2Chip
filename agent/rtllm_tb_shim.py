"""Icarus-compatible shims for official RTLLM testbenches."""
from __future__ import annotations

import re


def shim_sv_break(tb_code: str) -> str:
    if "break;" not in tb_code:
        return tb_code

    def _wrap(match: re.Match[str]) -> str:
        return (
            "initial begin\n"
            "  begin : __rtllm_break_loop\n"
            f"  repeat ({match.group(1)}) begin\n"
            f"{match.group(2)}disable __rtllm_break_loop;{match.group(3)}"
            "end\n"
            "  end\n"
            "end"
        )

    patched, n = re.subn(
        r"initial\s+begin\s+repeat\s*\(([^)]+)\)\s+begin\s+([\s\S]*?)break;([\s\S]*?)end\s+end",
        _wrap,
        tb_code,
        count=1,
    )
    return patched if n else tb_code.replace("break;", "// break not supported")


def shim_unpacked_array_init(tb_code: str) -> str:
    match = re.search(
        r"reg\s+(\[[^\]]+\])\s+(\w+)\s+(\[[^\]]+\])\s*=\s*\{([^}]*)\}\s*;",
        tb_code,
        re.S,
    )
    if not match:
        return tb_code
    width, name, rng, body = match.groups()
    values = [item.strip() for item in body.split(",") if item.strip()]
    inits = "\n".join(f"        {name}[{i}] = {value};" for i, value in enumerate(values))
    replacement = f"reg {width} {name} {rng};\n    initial begin\n{inits}\n    end"
    return tb_code[: match.start()] + replacement + tb_code[match.end() :]


def prepare_rtllm_testbench(tb_code: str) -> str:
    return shim_sv_break(shim_unpacked_array_init(tb_code))
