"""RTLLM Sparkle wrapper: map _gen_/packed out onto official testbench ports."""
from __future__ import annotations

import re
from typing import Callable


def _params(tb_code: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for name, value in re.findall(r"parameter\s+(?:\w+\s+)?(\w+)\s*=\s*(\d+)", tb_code):
        found[name] = int(value)
    return found


def _eval_width_expr(expr: str, params: dict[str, int]) -> int | None:
    text = expr.strip()
    for name, value in params.items():
        text = re.sub(rf"\b{re.escape(name)}\b", str(value), text)
    text = text.replace(" ", "")
    if re.fullmatch(r"\d+", text):
        return int(text)
    match = re.fullmatch(r"(\d+)-(\d+)", text)
    if match:
        return int(match.group(1)) - int(match.group(2))
    return None


def resolve_range(bracket: str, tb_code: str) -> str:
    inner = bracket.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    if re.fullmatch(r"\d+\s*:\s*\d+", inner):
        hi, lo = [part.strip() for part in inner.split(":")]
        return f"[{hi}:{lo}]"
    if ":" not in inner:
        return ""
    hi_s, lo_s = inner.split(":", 1)
    params = _params(tb_code)
    hi = _eval_width_expr(hi_s, params)
    lo = _eval_width_expr(lo_s, params)
    if hi is None or lo is None:
        return ""
    return f"[{hi}:{lo}]"


def net_decl(tb_code: str, net: str) -> tuple[str | None, str]:
    match = re.search(
        rf"\b(input|output|inout|reg|wire|logic)\s*(signed\s+)?(\[[^\]]+\])?\s*"
        rf"(?:[A-Za-z_]\w*\s*,\s*)*{re.escape(net)}\b",
        tb_code,
    )
    if not match:
        return None, ""
    kind = match.group(1)
    width = ""
    if match.group(3):
        resolved = resolve_range(match.group(3), tb_code)
        width = f" {resolved}" if resolved else ""
    return kind, width


def width_bits(width: str) -> int:
    match = re.search(r"\[(\d+)\s*:\s*(\d+)\]", width or "")
    if match:
        return abs(int(match.group(1)) - int(match.group(2))) + 1
    return 1


def _split_port_list(body: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                parts.append(item)
            buf = []
            continue
        buf.append(char)
    item = "".join(buf).strip()
    if item:
        parts.append(item)
    return parts


def ref_port_names(ref_code: str) -> list[str]:
    header = re.search(r"module\s+\w+\s*\(([^;]*)\)\s*;", ref_code)
    if header:
        body = header.group(1)
        if "input" not in body and "output" not in body and "inout" not in body:
            names = [part.strip() for part in _split_port_list(body) if re.fullmatch(r"[A-Za-z_]\w*", part.strip())]
            if names:
                return names
    names: list[str] = []
    for match in re.finditer(
        r"\b(?:input|output|inout)\b(?:\s+(?:wire|reg|logic))?(?:\s+signed)?(?:\s*\[[^\]]+\])?\s*([^;]+);",
        ref_code,
    ):
        for part in _split_port_list(match.group(1)):
            ident = re.search(r"([A-Za-z_]\w*)\s*$", part)
            if ident:
                names.append(ident.group(1))
    return names


def sparkle_width(typ: str) -> str:
    match = re.search(r"\[([^\]]+)\]", typ or "")
    if not match:
        return ""
    inner = match.group(1)
    if re.fullmatch(r"\d+\s*:\s*\d+", inner):
        return f" [{inner.replace(' ', '')}]"
    return ""


def build_rtllm_wrapper(
    design_name: str,
    sparkle_mod_name: str,
    sparkle_ports: list[tuple[str, str, str]],
    tb_code: str,
    sv_code: str = "",
    ref_code: str = "",
    *,
    ports_equivalent: Callable[[str, str], bool],
    port_width: Callable[[str], int],
    is_reset_like: Callable[[str], bool],
    is_active_low_reset: Callable[[str], bool],
) -> str | None:
    candidate_names = [design_name]
    if "substractor" in design_name:
        candidate_names.append(design_name.replace("substractor", "subtractor"))
    if "subtractor" in design_name:
        candidate_names.append(design_name.replace("subtractor", "substractor"))

    inst_body = None
    wrapper_name = design_name
    for cand in candidate_names:
        inst = re.search(
            rf"{re.escape(cand)}\s+(?:#\s*\((?:[^()]*|\([^()]*\))*\)\s*)?\w+\s*\(([^;]+)\)\s*;",
            tb_code,
            re.DOTALL,
        )
        if inst:
            inst_body = inst.group(1)
            wrapper_name = cand
            break
    if inst_body is None:
        return None

    named = re.findall(r"\.(\w+)\s*\(\s*([^)]*?)\s*\)", inst_body)
    if not named:
        exprs = _split_port_list(inst_body)
        refs = ref_port_names(ref_code)
        if refs and len(exprs) == len(refs):
            named = list(zip(refs, exprs))
        else:
            return None

    sp_map = {name: (direction, typ) for direction, typ, name in sparkle_ports}
    used: set[str] = set()

    def classify_tb(port: str, expr: str) -> tuple[str, str]:
        net = expr.strip()
        net = net if re.fullmatch(r"[A-Za-z_]\w*", net) else port
        kind, width = net_decl(tb_code, net)
        if kind is None:
            kind, width = net_decl(tb_code, port)
        key = _compact(port)
        data_outs = {"out", "dout", "dataout", "c", "sum", "result", "q", "y"}
        clk_ins = {"clk", "clock", "clkin", "clockin"}
        if kind in {"output", "inout"}:
            return "output", width
        if kind == "input":
            return "input", width
        if kind in {"wire", "logic"}:
            return "output", width
        if kind == "reg":
            if re.fullmatch(r"clk\d+", key):
                return "output", width
            if "clk" in key or key in clk_ins:
                return "input", width
            if key in data_outs:
                return "output", width
            return "input", width
        if key in clk_ins or port.lower().endswith("_in"):
            return "input", width
        return "output", width

    def _compact(name: str) -> str:
        raw = name.lower()
        if raw.startswith("_gen_"):
            raw = raw[5:]
        return re.sub(r"[^a-z0-9]", "", raw)

    def match_sparkle(tb_name: str, tb_dir: str) -> str | None:
        want_dir = "output" if tb_dir == "output" else "input"

        def usable(name: str) -> bool:
            return name in sp_map and name not in used and sp_map[name][0] == want_dir

        for cand in (tb_name, f"_gen_{tb_name}"):
            if usable(cand):
                return cand
            for name in sp_map:
                if name.lower() == cand.lower() and usable(name):
                    return name

        tb_key = _compact(tb_name)
        groups = [{"in", "inp", "din"}, {"out", "dout", "q", "c"}]
        if tb_dir == "input":
            groups.append({"clk", "clkin", "clockin", "clock"})
        for name in sp_map:
            if not usable(name):
                continue
            sp_key = _compact(name)
            if tb_dir == "output" and sp_key in {"clk", "clock"}:
                continue
            if tb_key == sp_key:
                return name
            if tb_key in {"clkin", "clockin"} and sp_key in {"clk", "clock"}:
                return name
            if any(tb_key in group and sp_key in group for group in groups):
                return name
            if ports_equivalent(name, tb_name) or ports_equivalent(name, f"_gen_{tb_name}"):
                if tb_dir == "output" and sp_key in {"clk", "clock"}:
                    continue
                return name
        return None

    classified: list[tuple[str, str, str, str | None]] = []
    for tb_name, expr in named:
        tb_dir, tb_width = classify_tb(tb_name, expr)
        matched = match_sparkle(tb_name, tb_dir)
        if matched:
            used.add(matched)
            width = sparkle_width(sp_map[matched][1]) or tb_width
            classified.append((tb_name, tb_dir, width, matched))
        else:
            classified.append((tb_name, tb_dir, tb_width, None))

    unmatched_outs = [(name, width) for name, direction, width, match in classified if direction == "output" and match is None]
    sp_outputs = [(direction, typ, name) for direction, typ, name in sparkle_ports if direction == "output" and name not in used]
    bundled = None
    if unmatched_outs and len(sp_outputs) == 1:
        _, sp_typ, sp_name = sp_outputs[0]
        leftover_key = _compact(sp_name)
        sp_bits = port_width(sp_typ)
        tb_bits = sum(width_bits(width) for _, width in unmatched_outs)
        clk_like = leftover_key in {"clk", "clock"}
        if clk_like:
            pass
        elif sp_bits == tb_bits and len(unmatched_outs) > 1 and tb_bits > 0:
            bundled = (sp_name, sp_typ, unmatched_outs)
            used.add(sp_name)
        elif len(unmatched_outs) == 1 and sp_bits == width_bits(unmatched_outs[0][1]):
            first_name, _ = unmatched_outs[0]
            used.add(sp_name)
            classified = [
                (name, direction, width, sp_name if name == first_name else match)
                for name, direction, width, match in classified
            ]

    decls = [f"    {direction}{width} {name}" for name, direction, width, _ in classified]
    lines = [f"module {wrapper_name} (", ",\n".join(decls), ");", ""]

    dummy_i = 0
    wires: list[str] = []
    conns: list[str] = []

    def add_dummy(typ: str) -> str:
        nonlocal dummy_i
        dummy_i += 1
        name = f"_unconnected_{dummy_i}"
        width = sparkle_width(typ)
        wires.append(f"    wire{width} {name};")
        return name

    matched_out_wires: dict[str, str] = {}
    for tb_name, tb_dir, width, match in classified:
        if match and tb_dir == "output":
            wire = f"{match}_wire"
            if wire not in matched_out_wires.values():
                wires.append(f"    wire{width} {wire};")
                matched_out_wires[match] = wire

    if bundled:
        sp_name, sp_typ, _ = bundled
        wires.append(f"    wire{sparkle_width(sp_typ)} {sp_name}_wire;")

    for direction, typ, sp_name in sparkle_ports:
        matched_tb = next((tb for tb, tb_dir, _, match in classified if match == sp_name and tb_dir == "input"), None)
        if matched_tb:
            expr = matched_tb
            if is_reset_like(sp_name) and is_active_low_reset(matched_tb) != is_active_low_reset(sp_name):
                expr = f"~{matched_tb}"
            conns.append(f"        .{sp_name}({expr})")
            continue
        if bundled and sp_name == bundled[0]:
            conns.append(f"        .{sp_name}({sp_name}_wire)")
            continue
        if sp_name in matched_out_wires:
            conns.append(f"        .{sp_name}({matched_out_wires[sp_name]})")
            continue
        if direction == "output":
            conns.append(f"        .{sp_name}({add_dummy(typ)})")
            continue
        # leftover input
        clk_tb = next((tb for tb, tb_dir, _, _ in classified if tb_dir == "input" and re.sub(r"[^a-z0-9]", "", tb.lower()) in {"clk", "clock", "clkin"}), None)
        if re.sub(r"[^a-z0-9]", "", sp_name.lower().removeprefix("_gen_")) in {"clk", "clock"} and clk_tb:
            conns.append(f"        .{sp_name}({clk_tb})")
            continue
        rst_tb = next((tb for tb, tb_dir, _, _ in classified if tb_dir == "input" and is_reset_like(tb)), None)
        reset_already = any(is_reset_like(name) and name in used for name in sp_map)
        if is_reset_like(sp_name) and rst_tb and not reset_already:
            expr = rst_tb
            if is_active_low_reset(rst_tb) != is_active_low_reset(sp_name):
                expr = f"~{rst_tb}"
            conns.append(f"        .{sp_name}({expr})")
            continue
        if is_reset_like(sp_name) and reset_already:
            idle = "1'b1" if is_active_low_reset(sp_name) else "1'b0"
            conns.append(f"        .{sp_name}({idle})")
            continue
        conns.append(f"        .{sp_name}({add_dummy(typ)})")

    lines.extend(wires)
    lines.append(f"    {sparkle_mod_name} sparkle_dut (")
    lines.append(",\n".join(conns))
    lines.append("    );")

    if bundled:
        sp_name, sp_typ, fields = bundled
        ordered = [name for name, _ in fields]
        concat = re.search(rf"assign\s+{re.escape(sp_name)}\s*=\s*\{{([^}}]+)\}}", sv_code)
        if not concat:
            indirect = re.search(rf"assign\s+{re.escape(sp_name)}\s*=\s*(\w+)\s*;", sv_code)
            if indirect:
                concat = re.search(rf"assign\s+{re.escape(indirect.group(1))}\s*=\s*\{{([^}}]+)\}}", sv_code)
        if concat:
            names = [part.strip().split("[")[0].replace("_gen_", "") for part in concat.group(1).split(",")]
            field_names = {name for name, _ in fields}
            if len(names) == len(fields) and all(name in field_names for name in names):
                ordered = names
        width_by_name = {name: width for name, width in fields}
        offset = port_width(sp_typ)
        for field_name in ordered:
            bits = width_bits(width_by_name[field_name])
            high, low = offset - 1, offset - bits
            if bits == 1 and port_width(sp_typ) == 1:
                lines.append(f"    assign {field_name} = {sp_name}_wire;")
            elif bits == 1:
                lines.append(f"    assign {field_name} = {sp_name}_wire[{low}];")
            else:
                lines.append(f"    assign {field_name} = {sp_name}_wire[{high}:{low}];")
            offset -= bits

    for tb_name, tb_dir, _, match in classified:
        if tb_dir == "output" and match and not bundled:
            lines.append(f"    assign {tb_name} = {matched_out_wires[match]};")

    lines.append("endmodule")
    lines.append("")
    return "\n".join(lines)
