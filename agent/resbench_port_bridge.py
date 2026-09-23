"""ResBench hidden-eval adapter: map Sparkle `_gen_*` DUT ports onto the public TB."""
from __future__ import annotations

import re
from typing import Callable


def tb_instance_ports(tb_code: str, design_name: str) -> list[str]:
    inst = re.compile(
        rf"{re.escape(design_name)}\s+(?:#\s*\((?:[^()]*|\([^()]*\))*\)\s*)?\w+\s*\(([^;]+)\)\s*;",
        re.DOTALL,
    )
    match = inst.search(tb_code)
    if not match:
        return []
    return re.findall(r"\.(\w+)\s*\(", match.group(1))


def width_token(typ: str) -> str:
    match = re.search(r"\[([^\]]+)\]", typ or "")
    return f" [{match.group(1)}]" if match else ""


def zero_expr(typ: str) -> str:
    match = re.search(r"\[(\d+)\s*:\s*(\d+)\]", typ or "")
    if not match:
        return "1'b0"
    width = abs(int(match.group(1)) - int(match.group(2))) + 1
    return f"{width}'b0"


GENERIC_OUTPUTS = {"out", "q", "y", "data_out", "dout", "result"}


def _base_name(name: str) -> str:
    raw = name.lower()
    if raw.startswith("_gen_"):
        raw = raw[5:]
    return raw


def tb_port_is_input(tb_code: str, tb_name: str) -> bool:
    tb_decl = re.search(
        rf"(reg|wire)\s+(signed\s+)?(\[[^\]]+\])?\s*{re.escape(tb_name)}\s*;",
        tb_code,
    )
    return bool(tb_decl and tb_decl.group(1) == "reg")


def pick_leftover_output(
    sparkle_outputs: list[tuple[str, str, str]],
) -> tuple[str, str, str] | None:
    if not sparkle_outputs:
        return None
    generic = [p for p in sparkle_outputs if _base_name(p[2]) in GENERIC_OUTPUTS]
    if len(generic) == 1:
        return generic[0]
    if len(sparkle_outputs) == 1:
        return sparkle_outputs[0]
    for port in generic:
        if _base_name(port[2]) == "out":
            return port
    if generic:
        return generic[0]
    return None


def match_sparkle_port(
    tb_name: str,
    sparkle_ports: list[tuple[str, str, str]],
    ports_equivalent: Callable[[str, str], bool],
) -> tuple[str, str, str] | None:
    by_name = {name: (direction, typ, name) for direction, typ, name in sparkle_ports}
    if tb_name in by_name:
        return by_name[tb_name]
    generated = f"_gen_{tb_name}"
    if generated in by_name:
        return by_name[generated]
    lowered = {name.lower(): (direction, typ, name) for direction, typ, name in sparkle_ports}
    if tb_name.lower() in lowered:
        return lowered[tb_name.lower()]
    if generated.lower() in lowered:
        return lowered[generated.lower()]
    for direction, typ, name in sparkle_ports:
        if ports_equivalent(name, tb_name) or ports_equivalent(name, generated):
            return direction, typ, name
    return None


def build_bridge(
    design_name: str,
    sv_code: str,
    tb_code: str,
    parse_module_ports: Callable,
    ports_equivalent: Callable[[str, str], bool],
    is_reset_like: Callable[[str], bool],
) -> tuple[str, str | None]:
    """Return (inner_sv, wrapper_or_none). Wrapper is emitted only when TB ports miss on the DUT."""
    module_name, sparkle_ports = parse_module_ports(sv_code, module_name=design_name)
    if not module_name:
        module_name, sparkle_ports = parse_module_ports(sv_code)
    if not module_name or not sparkle_ports:
        return sv_code, None
    tb_ports = tb_instance_ports(tb_code, design_name)
    if not tb_ports:
        return sv_code, None
    sparkle_names = {name for _, _, name in sparkle_ports}
    if all(name in sparkle_names for name in tb_ports):
        return sv_code, None

    inner_name = f"{design_name}_sparkle"
    inner_sv = re.sub(
        rf"\bmodule\s+{re.escape(module_name)}\b",
        f"module {inner_name}",
        sv_code,
        count=1,
    )
    decls: list[str] = []
    conns: list[str] = []
    extras: list[str] = []
    connected: set[str] = set()
    seen_tb: set[str] = set()
    unmatched_tb: list[str] = []
    for tb_name in tb_ports:
        if tb_name in seen_tb:
            continue
        seen_tb.add(tb_name)
        matched = match_sparkle_port(tb_name, sparkle_ports, ports_equivalent)
        if matched is None:
            tb_decl = re.search(
                rf"(reg|wire)\s+(signed\s+)?(\[[^\]]+\])?\s*{re.escape(tb_name)}\s*;",
                tb_code,
            )
            direction = "input" if tb_decl and tb_decl.group(1) == "reg" else "output"
            signed = " signed" if tb_decl and tb_decl.group(2) else ""
            width = f" {tb_decl.group(3)}" if tb_decl and tb_decl.group(3) else ""
            decls.append(f"    {direction} logic{signed}{width} {tb_name}")
            unmatched_tb.append(tb_name)
            continue
        direction, typ, sparkle_name = matched
        connected.add(sparkle_name)
        signed = " signed" if "signed" in typ else ""
        decls.append(f"    {direction} logic{signed}{width_token(typ)} {tb_name}")
        conns.append(f"        .{sparkle_name}({tb_name})")

    leftover_sp_outs = [
        (direction, typ, name)
        for direction, typ, name in sparkle_ports
        if name not in connected and direction == "output"
    ]
    leftover_tb_outs = [
        name
        for name in unmatched_tb
        if name.lower() not in {"clk", "clock"}
        and not is_reset_like(name)
        and not tb_port_is_input(tb_code, name)
    ]
    if len(leftover_tb_outs) == 1:
        picked = pick_leftover_output(leftover_sp_outs)
        if picked is not None:
            _, _, sparkle_name = picked
            connected.add(sparkle_name)
            conns.append(f"        .{sparkle_name}({leftover_tb_outs[0]})")

    for direction, typ, sparkle_name in sparkle_ports:
        if sparkle_name in connected:
            continue
        dummy = f"_unconnected_{sparkle_name}"
        extras.append(f"    wire{width_token(typ)} {dummy};")
        if direction == "output":
            conns.append(f"        .{sparkle_name}({dummy})")
            continue
        if sparkle_name == "clk" and "clk" in tb_ports:
            conns.append("        .clk(clk)")
            continue
        reset_tb = next((name for name in tb_ports if is_reset_like(name)), None)
        if is_reset_like(sparkle_name) and reset_tb:
            conns.append(f"        .{sparkle_name}({reset_tb})")
            continue
        conns.append(f"        .{sparkle_name}({zero_expr(typ)})")

    wrapper = "\n".join(
        [
            f"module {design_name} (",
            ",\n".join(decls),
            ");",
            *extras,
            f"    {inner_name} sparkle_dut (",
            ",\n".join(conns),
            "    );",
            "endmodule",
            "",
        ]
    )
    return inner_sv, wrapper
