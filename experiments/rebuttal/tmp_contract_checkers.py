#!/usr/bin/env python3
"""Three-layer NL↔formal contract checkers.

1. Soundness: RefModule traces must satisfy every decidable Prop.
2. Completeness: NL-directed mutants of the ref must falsify the contract.
3. Judge: LLM critique of NL vs contract (no ref body). Gold bullets are
   given to the judge as a coverage checklist.

Trace evaluation uses `lake env lean` + `decide` after marking defs reducible.
Ghost Cycle fields (non-ports) skip soundness for props that mention them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tmp_contract_validate import parse_ref_ports, validate_contract

PROJECT = Path(os.environ.get("LAKE_DIR", "/home/sgli/work/NL2Chip_openlux_repair_state_20260914"))
DS_DIR = PROJECT / "verilog-eval" / "dataset_spec-to-rtl"
IVERILOG = os.environ.get("IVERILOG", "/home/sgli/.local/bin/iverilog")
VVP = os.environ.get("VVP", "/home/sgli/.local/bin/vvp")

RESERVED_SV = {"in", "out", "int", "logic", "wire", "reg", "type"}

GOLD: dict[str, list[dict[str, str]]] = {
    "Prob035_count1to10": [
        {"id": "reset1", "text": "Synchronous reset sets q to 1, not 0."},
        {"id": "range", "text": "q counts 1 through 10 inclusive."},
        {"id": "wrap", "text": "After 10 the next value is 1."},
        {"id": "inc", "text": "When not reset and not at 10, q increments by 1."},
    ],
    "Prob037_review2015_count1k": [
        {"id": "reset0", "text": "Synchronous reset sets q to 0."},
        {"id": "range", "text": "q counts 0 through 999 inclusive."},
        {"id": "wrap", "text": "After 999 the next value is 0."},
        {"id": "inc", "text": "Otherwise q increments by 1."},
    ],
    "Prob063_review2015_shiftcount": [
        {"id": "shift_msb", "text": "When shift_ena=1, data is shifted in MSB first."},
        {"id": "count", "text": "When count_ena=1 and not shifting, q decrements."},
        {"id": "hold", "text": "When both enables are 0, q holds."},
        {"id": "both", "text": "When both enables are 1, behavior is don't-care — do not write a tautology."},
    ],
    "Prob067_countslow": [
        {"id": "reset0", "text": "Synchronous reset sets q to 0."},
        {"id": "pause", "text": "When slowena=0 and not reset, q holds."},
        {"id": "range", "text": "When enabled, q counts 0 through 9."},
        {"id": "wrap", "text": "After 9 with slowena, next is 0."},
    ],
    "Prob068_countbcd": [
        {"id": "digits", "text": "q is four BCD digits: ones q[3:0], tens q[7:4], hundreds q[11:8], thousands q[15:12]."},
        {"id": "ones", "text": "Ones digit counts 0-9 then wraps and carries."},
        {"id": "ena", "text": "ena[1], ena[2], ena[3] pulse when tens/hundreds/thousands should increment."},
        {"id": "reset", "text": "Synchronous reset clears the counter to 0."},
        {"id": "no_magic", "text": "Do not encode BCD carry as +7/+103/+1639 on the whole q."},
    ],
    "Prob080_timer": [
        {"id": "load", "text": "If load=1, internal count becomes data (even mid-count)."},
        {"id": "dec", "text": "If load=0 and count≠0, count decrements by 1 each cycle."},
        {"id": "tc", "text": "tc is 1 iff the internal count is 0."},
        {"id": "stay", "text": "Once count is 0 it stays 0 until the next load."},
        {"id": "n_cycles", "text": "Loading N then holding load=0 asserts tc after N cycles (N=0 asserts immediately)."},
    ],
    "Prob115_shift18": [
        {"id": "load", "text": "load=1 copies data into q."},
        {"id": "hold", "text": "load=0 and ena=0 holds q."},
        {"id": "shl1", "text": "amount=00 shifts left by 1."},
        {"id": "shl8", "text": "amount=01 shifts left by 8."},
        {"id": "asr1", "text": "amount=10 is arithmetic right shift by 1 (sign-fill)."},
        {"id": "asr8", "text": "amount=11 is arithmetic right shift by 8 (sign-fill)."},
    ],
    "Prob124_rule110": [
        {"id": "load", "text": "load=1 copies data into q."},
        {"id": "table", "text": "Each cell follows the Rule 110 8-row neighbor table."},
        {"id": "bound", "text": "Virtual neighbors q[-1] and q[512] are 0."},
        {"id": "advance", "text": "Without load, q advances one CA step per clock."},
    ],
    "Prob141_count_clock": [
        {"id": "reset", "text": "Reset to 12:00 AM (hh=0x12, mm=ss=0, pm=0), even if ena=0."},
        {"id": "ena", "text": "When ena=0 and not reset, hh/mm/ss/pm hold."},
        {"id": "bcd", "text": "ss and mm are BCD 00-59; hh is BCD 01-12."},
        {"id": "pm", "text": "pm toggles when wrapping 11:59:59 to 12:00."},
        {"id": "12to1", "text": "12:59:59 wraps hours to 01, pm unchanged."},
    ],
    "Prob144_conwaylife": [
        {"id": "load", "text": "load=1 copies data into q."},
        {"id": "b3s23", "text": "0-1 neighbors die; 2 stay; 3 become alive; 4+ die."},
        {"id": "torus", "text": "16x16 grid wraps as a torus."},
        {"id": "layout", "text": "Row r is q[16*r+15 : 16*r]."},
    ],
    "Prob146_fsm_serialdata": [
        {"id": "frame", "text": "A valid byte is start=0, 8 data bits LSB first, stop=1."},
        {"id": "done", "text": "done is 1 when a full valid frame is recognized."},
        {"id": "out", "text": "When done=1, out_byte is the received data byte."},
        {"id": "bad_stop", "text": "If stop≠1, wait until the line is 1 before hunting the next start."},
        {"id": "reset", "text": "Synchronous reset clears done and returns to idle/start hunt."},
    ],
    "Prob155_lemmings4": [
        {"id": "areset", "text": "Async areset to walk_left (others 0)."},
        {"id": "prio", "text": "Fall > dig > direction change."},
        {"id": "fall", "text": "ground=0 while walking/digging starts aaah; resume original direction on landing."},
        {"id": "splat", "text": "Falling more than 20 cycles then hitting ground splatters: all four outputs stay 0 until reset."},
        {"id": "dig", "text": "dig=1 while walking on ground starts digging until ground disappears."},
    ],
}

MUTANTS: dict[str, list[tuple[str, str, str]]] = {
    "Prob035_count1to10": [
        ("wrap11", "q == 10", "q == 11"),
        ("reset0", "reset || q == 10)\n      q <= 1;", "reset || q == 10)\n      q <= 0;"),
    ],
    "Prob037_review2015_count1k": [
        ("wrap1023", "q == 999", "q == 1023"),
        ("reset1", "q <= 0;", "q <= 1;"),
    ],
    "Prob063_review2015_shiftcount": [
        ("shift_msb", "q <= { q[2:0], data };", "q <= { data, q[3:1] };"),
        ("count_up", "q <= q - 1'b1;", "q <= q + 1'b1;"),
        ("no_hold", "else if (count_ena)\n      q <= q - 1'b1;", "q <= q - 1'b1;"),
    ],
    "Prob067_countslow": [
        ("ignore_slowena", "else if (slowena) begin", "else begin"),
        ("wrap10", "q == 9", "q == 10"),
    ],
    "Prob068_countbcd": [
        ("binary_inc", "q[i*4 +:4] == 9 && enable[i]", "1'b0"),
        ("ena_stuck0", "assign ena = enable[3:1];", "assign ena = 3'b000;"),
    ],
    "Prob080_timer": [
        ("no_decrement", "else if(count_value != 0) count_value <= count_value - 1;", ""),
        ("wrap", "else if(count_value != 0) count_value <= count_value - 1;", "else count_value <= count_value - 1;"),
        ("tc_tied_load", "assign tc = count_value == 0;", "assign tc = load;"),
    ],
    "Prob115_shift18": [
        ("logical_shr1", "q <= {q[63], q[63:1]};", "q <= {1'b0, q[63:1]};"),
        ("logical_shr8", "q <= {{8{q[63]}}, q[63:8]};", "q <= {8'b0, q[63:8]};"),
        ("swap_amt", "2'b00: q <= {q[62:0], 1'b0};", "2'b00: q <= {q[63], q[63:1]};"),
    ],
    "Prob124_rule110": [
        ("hold", "q <=\n      ~((q[$bits(q)-1:1] & q[$bits(q)-1:0] & {q[$bits(q)-2:0], 1'b0}) |\n      (~q[$bits(q)-1:1] & ~q[$bits(q)-1:0] & ~{q[$bits(q)-2:0], 1'b0}) |\n      (q[$bits(q)-1:1] & ~q[$bits(q)-1:0] & ~{q[$bits(q)-2:0], 1'b0}) )\n      ;", "q <= q;"),
        ("invert", "~((q[$bits(q)-1:1]", "(q[$bits(q)-1:1]"),
    ],
    "Prob141_count_clock": [
        ("binary_ss", "ss[3:0] == 9", "1'b0"),
        ("no_pm_toggle", "if (enable[6]) pm <= ~pm;", ""),
        ("reset_00", "{pm,hh,mm,ss} <= 25'h0120000;", "{pm,hh,mm,ss} <= 25'h0000000;"),
    ],
    "Prob144_conwaylife": [
        ("b3s4", ") == 3'h3;", ") == 3'h4;"),
        ("no_load", "if (load)\n      q <= data;", ""),
    ],
    "Prob146_fsm_serialdata": [
        ("ignore_stop", "STOP: next = in ? DONE : ERR;", "STOP: next = DONE;"),
        ("done_idle", "assign done = (state==DONE);", "assign done = (state==START);"),
        ("no_reset", "if (reset) state <= START;", "if (1'b0) state <= START;"),
    ],
    "Prob155_lemmings4": [
        ("splat5", "fall_counter >= 20", "fall_counter >= 5"),
        ("never_splat", "fall_counter >= 20 ? DEAD", "1'b0 ? DEAD"),
        ("prio_bump", "if (!ground) next = FALLL;\n        else if (dig) next = DIGL;\n        else if (bump_left) next = WR;",
         "if (bump_left) next = WR;\n        else if (!ground) next = FALLL;\n        else if (dig) next = DIGL;"),
    ],
}


def sv_name(name: str) -> str:
    n = name.strip("«»")
    return n + "_sig" if n in RESERVED_SV else n


def field_width(ty: str) -> int:
    ty = ty.strip()
    if ty == "Bool":
        return 1
    m = re.search(r"BitVec\s+(\d+)", ty)
    return int(m.group(1)) if m else 1


def make_reducible(src: str) -> str:
    src = re.sub(r"(?m)^@\[reducible\]\s*def ", "def ", src)
    return re.sub(r"(?m)^def ", "@[reducible] def ", src)


def lean_lit(ty: str, raw: str) -> str | None:
    if raw is None or re.search(r"[xzXZ]", raw):
        return None
    w = field_width(ty)
    if ty.strip() == "Bool":
        v = int(raw, 0) if raw.lower().startswith("0") or raw.isdigit() else int(raw, 16)
        return "true" if v else "false"
    hexpart = raw.lower().replace("0x", "")
    try:
        val = int(hexpart, 16)
    except ValueError:
        val = int(raw, 0)
    return f"0x{val:x}#{w}"


def parse_trace_line(line: str) -> tuple[str, dict[str, str]] | None:
    kind = None
    if line.startswith("PRE "):
        kind = "pre"
        payload = line[4:]
    elif line.startswith("POST "):
        kind = "post"
        payload = line[5:]
    else:
        return None
    rec = {}
    for tok in payload.strip().split():
        if "=" not in tok:
            continue
        k, _, v = tok.partition("=")
        rec[k.strip("«»")] = v
    return (kind, rec) if rec else None


def directed_sv(prob_id: str, names: dict[str, str]) -> str:
    """Return extra initial-block statements using SV signal names."""
    g = names
    def n(p):
        return g[p]

    if prob_id == "Prob035_count1to10":
        return f"""
    {n('reset')} = 1; repeat (2) @(negedge clk);
    {n('reset')} = 0; repeat (16) @(negedge clk);
    {n('reset')} = 1; @(negedge clk);
    {n('reset')} = 0; repeat (4) @(negedge clk);
"""
    if prob_id == "Prob037_review2015_count1k":
        return f"""
    {n('reset')} = 1; @(negedge clk);
    {n('reset')} = 0; repeat (40) @(negedge clk);
    {n('reset')} = 1; @(negedge clk); {n('reset')} = 0;
"""
    if prob_id == "Prob063_review2015_shiftcount":
        d, s, c = n("data"), n("shift_ena"), n("count_ena")
        return f"""
    {s}=1; {c}=0; {d}=1; @(negedge clk);
    {d}=0; @(negedge clk); {d}=1; @(negedge clk); {d}=1; @(negedge clk);
    {s}=0; {c}=1; repeat (6) @(negedge clk);
    {s}=0; {c}=0; repeat (2) @(negedge clk);
    {s}=1; {c}=1; @(negedge clk);
"""
    if prob_id == "Prob067_countslow":
        return f"""
    {n('reset')}=1; @(negedge clk); {n('reset')}=0;
    {n('slowena')}=1; repeat (12) @(negedge clk);
    {n('slowena')}=0; repeat (3) @(negedge clk);
    {n('slowena')}=1; repeat (4) @(negedge clk);
"""
    if prob_id == "Prob068_countbcd":
        return f"""
    {n('reset')}=1; @(negedge clk); {n('reset')}=0;
    repeat (40) @(negedge clk);
"""
    if prob_id == "Prob080_timer":
        return f"""
    {n('load')}=1; {n('data')}=10'd3; @(negedge clk);
    {n('load')}=0; repeat (8) @(negedge clk);
    {n('load')}=1; {n('data')}=10'd0; @(negedge clk);
    {n('load')}=0; repeat (2) @(negedge clk);
    {n('load')}=1; {n('data')}=10'd7; @(negedge clk);
    {n('load')}=0; repeat (4) @(negedge clk);
    {n('load')}=1; {n('data')}=10'd2; @(negedge clk);
    {n('load')}=0; repeat (6) @(negedge clk);
"""
    if prob_id == "Prob115_shift18":
        return f"""
    {n('load')}=1; {n('ena')}=0; {n('amount')}=0; {n('data')}=64'h8000_0000_0000_00ff; @(negedge clk);
    {n('load')}=0; {n('ena')}=1; {n('amount')}=2'b10; repeat (3) @(negedge clk);
    {n('amount')}=2'b11; @(negedge clk);
    {n('amount')}=2'b00; @(negedge clk);
    {n('amount')}=2'b01; @(negedge clk);
    {n('ena')}=0; repeat (2) @(negedge clk);
    {n('load')}=1; {n('data')}=64'h7fff_ffff_ffff_ffff; @(negedge clk);
    {n('load')}=0; {n('ena')}=1; {n('amount')}=2'b10; @(negedge clk);
"""
    if prob_id == "Prob124_rule110":
        return f"""
    {n('load')}=1; {n('data')}=512'h00000000000000000000000000000000000000000000000000000000000000fe; @(negedge clk);
    {n('load')}=0; repeat (6) @(negedge clk);
    {n('load')}=1; {n('data')}=512'h1; @(negedge clk);
    {n('load')}=0; repeat (4) @(negedge clk);
"""
    if prob_id == "Prob141_count_clock":
        return f"""
    {n('reset')}=1; {n('ena')}=0; @(negedge clk);
    {n('reset')}=0; {n('ena')}=0; repeat (2) @(negedge clk);
    {n('ena')}=1; repeat (30) @(negedge clk);
    {n('ena')}=0; @(negedge clk);
    {n('reset')}=1; @(negedge clk); {n('reset')}=0;
"""
    if prob_id == "Prob144_conwaylife":
        return f"""
    {n('load')}=1; {n('data')}=256'h00000000000000000000000000000700000e0000000000000000000000000000; @(negedge clk);
    {n('load')}=0; repeat (5) @(negedge clk);
"""
    if prob_id == "Prob146_fsm_serialdata":
        ins = n("in")
        rst = n("reset")
        bits = "0_10110011_1".replace("_", "")  # start, lsb-first 0xCD, stop
        seq = "\n".join(f"    {ins}={b}; @(negedge clk);" for b in bits)
        bad = "\n".join(f"    {ins}={b}; @(negedge clk);" for b in "0101010100")
        return f"""
    {rst}=1; {ins}=1; @(negedge clk); {rst}=0;
    repeat (3) @(negedge clk);
{seq}
    {ins}=1; repeat (2) @(negedge clk);
{bad}
    {ins}=1; repeat (3) @(negedge clk);
{seq}
"""
    if prob_id == "Prob155_lemmings4":
        return f"""
    {n('areset')}=1; {n('ground')}=1; {n('bump_left')}=0; {n('bump_right')}=0; {n('dig')}=0;
    @(negedge clk); {n('areset')}=0;
    repeat (2) @(negedge clk);
    {n('bump_left')}=1; @(negedge clk); {n('bump_left')}=0;
    {n('ground')}=0; repeat (5) @(negedge clk);
    {n('ground')}=1; repeat (2) @(negedge clk);
    {n('dig')}=1; @(negedge clk); {n('dig')}=0; repeat (2) @(negedge clk);
    {n('ground')}=0; repeat (22) @(negedge clk);
    {n('ground')}=1; repeat (3) @(negedge clk);
    {n('areset')}=1; @(negedge clk); {n('areset')}=0;
"""
    return ""


def write_tb(path: Path, ref_sv: str, ports: list[tuple[str, str, str]], prob_id: str, extra_cycles: int) -> list[str]:
    names = {}
    decls = []
    conn = []
    for direction, ty, name in ports:
        w = field_width(ty)
        sv = sv_name(name)
        names[name] = sv
        if name.lower() in {"clk", "clock"}:
            decls.append("  reg clk;")
            conn.append(f"    .{name}(clk)")
            continue
        kind = "reg" if direction == "input" else "wire"
        width = "" if w == 1 else f" [{w-1}:0]"
        decls.append(f"  {kind}{width} {sv};")
        conn.append(f"    .{name}({sv})")
    dump_args = [names[name] for _, ty, name in ports if name.lower() not in {"clk", "clock"}]
    fmt = " ".join(
        f'{name.strip("«»")}={"%b" if field_width(ty)==1 else "%h"}'
        for _, ty, name in ports if name.lower() not in {"clk", "clock"}
    )
    inits = []
    for direction, ty, name in ports:
        if direction != "input" or name.lower() in {"clk", "clock"}:
            continue
        sv = names[name]
        inits.append(f"    {sv} = 0;")
    directed = directed_sv(prob_id, names)
    tb = f"""{ref_sv}

module tb;
{chr(10).join(decls)}
  RefModule dut (
{chr(10).join(c + ',' for c in conn[:-1])}
{conn[-1]}
  );
  initial clk = 1;
  always #5 clk = ~clk;
  integer cyc;
  initial begin
{chr(10).join(inits)}
    cyc = 0;
{directed}
    repeat ({extra_cycles}) begin
      @(negedge clk);
    end
    $finish;
  end
  always @(negedge clk) begin
    $strobe("PRE {fmt}", {", ".join(dump_args)});
  end
  always @(posedge clk) begin
    #1;
    $display("POST {fmt}", {", ".join(dump_args)});
    cyc = cyc + 1;
  end
endmodule
"""
    path.write_text(tb)
    return list(names.keys())


def simulate(ref_sv: str, ports, prob_id: str, work: Path, tag: str) -> tuple[list[dict[str, str]], str]:
    work.mkdir(parents=True, exist_ok=True)
    tb = work / f"{tag}_tb.sv"
    vvp = work / f"{tag}.vvp"
    extra = 8 if any(field_width(ty) >= 64 for _, ty, _ in ports) else 16
    write_tb(tb, ref_sv, ports, prob_id, extra)
    comp = subprocess.run(
        [IVERILOG, "-g2012", "-o", str(vvp), str(tb)],
        capture_output=True, text=True, timeout=30,
    )
    if comp.returncode != 0:
        return [], (comp.stdout or "") + (comp.stderr or "")
    run = subprocess.run(
        [VVP, str(vvp)],
        capture_output=True, text=True, timeout=30,
    )
    traces = []
    pres: list[dict[str, str]] = []
    posts: list[dict[str, str]] = []
    for line in (run.stdout or "").splitlines():
        parsed = parse_trace_line(line)
        if not parsed:
            continue
        kind, rec = parsed
        if kind == "pre":
            pres.append(rec)
        else:
            posts.append(rec)
    n = min(len(pres), len(posts))
    traces = []
    for i in range(n):
        traces.append(pres[i])
        traces.append(posts[i])
    err = "" if traces else ((run.stdout or "") + (run.stderr or "") + (comp.stderr or ""))
    return traces, err


def cycle_ctor(fields: dict[str, str], rec: dict[str, str], ghost: list[str]) -> str | None:
    parts = []
    ghost_keys = {g.strip("«»") for g in ghost}
    for raw_name, ty in fields.items():
        key = raw_name.strip("«»")
        if key in rec:
            lit = lean_lit(ty, rec[key])
            if lit is None:
                return None
        elif key in ghost_keys:
            lit = lean_lit(ty, "0")
            if lit is None:
                lit = "false" if ty.strip() == "Bool" else f"0#{field_width(ty)}"
        else:
            return None
        parts.append(f"    {raw_name} := {lit}")
    return "{\n" + ",\n".join(parts) + "\n  }"


def prop_mentions_ghost(body: str, ghost: list[str]) -> bool:
    for g in ghost:
        if re.search(rf"\b(?:pre|post)\.{re.escape(g)}\b", body):
            return True
        if g.startswith("«") and g in body:
            return True
    return False


def eval_props_on_traces(
    contract: str,
    fields: dict[str, str],
    props: list[tuple[str, str]],
    traces: list[dict[str, str]],
    ghost: list[str],
    work: Path,
    timeout: int = 90,
) -> dict:
    pairs = []
    skipped_x = 0
    for i in range(0, len(traces) - 1, 2):
        a, b = traces[i], traces[i + 1]
        ca, cb = cycle_ctor(fields, a, ghost), cycle_ctor(fields, b, ghost)
        if ca is None or cb is None:
            skipped_x += 1
            continue
        pairs.append((ca, cb))
    max_pairs = 6 if any(field_width(ty) >= 64 for ty in fields.values()) else 24
    if len(pairs) > max_pairs:
        half = max_pairs // 2
        pairs = pairs[:half] + pairs[-half:]
    result = {
        "n_traces": len(traces),
        "n_pairs": len(pairs),
        "skipped_x": skipped_x,
        "props": {},
        "ok": True,
        "detail": "",
    }
    if not pairs:
        result["ok"] = False
        result["detail"] = "no concrete pre/post pairs"
        return result

    src = make_reducible(contract.rstrip() + "\n")
    ns = re.search(r"(?m)^namespace\s+(Contract\.\S+)", src)
    open_ns = f"open {ns.group(1)}\n" if ns else ""
    checks = []
    skipped_ghost = []
    eval_names = []
    for name, body in props:
        if prop_mentions_ghost(body, ghost):
            skipped_ghost.append(name)
            result["props"][name] = {"status": "skipped_ghost", "fails": 0}
            continue
        eval_names.append(name)
        insts = []
        for i in range(len(pairs)):
            expr = re.sub(r"\bpre\.", f"pre{i}.", body)
            expr = re.sub(r"\bpost\.", f"post{i}.", expr)
            insts.append(
                f'#eval (if decide ({expr}) then "HOLD {name} {i}" else "FAIL {name} {i}")'
            )
        checks.append("\n".join(insts))
    if not eval_names:
        result["detail"] = "all props mention ghost state"
        return result

    decls = []
    for i, (pre, post) in enumerate(pairs):
        decls.append(f"def pre{i} : Cycle := {pre}")
        decls.append(f"def post{i} : Cycle := {post}")
    driver = (
        src
        + "\nset_option maxHeartbeats 400000\n"
        + open_ns
        + "\n".join(decls)
        + "\n"
        + "\n".join(checks)
        + "\n"
    )
    lean_path = work / "trace_check.lean"
    work.mkdir(parents=True, exist_ok=True)
    lean_path.write_text(driver)
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(lean_path)],
            cwd=str(PROJECT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        result["ok"] = False
        result["detail"] = f"lean timeout {timeout}s"
        return result
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        result["ok"] = False
        result["checker_error"] = True
        result["detail"] = out[-3000:]
        result["lean_rc"] = proc.returncode
        return result
    fails: dict[str, list[int]] = {n: [] for n in eval_names}
    for m in re.finditer(r"FAIL (\S+) (\d+)", out):
        fails[m.group(1)].append(int(m.group(2)))
    any_fail = False
    for n in eval_names:
        fs = fails.get(n, [])
        result["props"][n] = {"status": "fail" if fs else "hold", "fails": len(fs)}
        if fs:
            any_fail = True
    result["ok"] = not any_fail
    result["detail"] = ("violations on ref traces" if any_fail else "all decidable props hold on ref")
    result["skipped_ghost"] = skipped_ghost
    return result


def apply_mutant(src: str, old: str, new: str) -> str | None:
    if old not in src:
        return None
    return src.replace(old, new, 1)


def check_completeness(
    contract: str,
    fields: dict[str, str],
    props: list[tuple[str, str]],
    ghost: list[str],
    ref_sv: str,
    ports,
    prob_id: str,
    work: Path,
) -> dict:
    mutants = MUTANTS.get(prob_id, [])
    rows = []
    n_applied = 0
    n_killed = 0
    for mid, old, new in mutants:
        mut = apply_mutant(ref_sv, old, new)
        if mut is None:
            rows.append({"id": mid, "applied": False, "killed": False, "detail": "pattern not found"})
            continue
        n_applied += 1
        traces, err = simulate(mut, ports, prob_id, work / "mut" / mid, "mut")
        if not traces:
            rows.append({"id": mid, "applied": True, "killed": False, "detail": f"sim fail {err[-400:]}"})
            continue
        ev = eval_props_on_traces(contract, fields, props, traces, ghost, work / "mut" / mid / "lean", timeout=90)
        if ev.get("checker_error"):
            rows.append({
                "id": mid, "applied": True, "killed": False,
                "detail": "lean checker error: " + (ev.get("detail") or "")[:300],
                "props": ev.get("props"),
            })
            continue
        killed = (not ev.get("ok")) and "violations" in (ev.get("detail") or "")
        if killed:
            n_killed += 1
        rows.append({
            "id": mid,
            "applied": True,
            "killed": killed,
            "detail": ev.get("detail", "")[:400],
            "props": ev.get("props"),
        })
    ok = n_applied > 0 and n_killed == n_applied
    weak = n_applied > 0 and n_killed < n_applied
    return {
        "ok": ok,
        "weak": weak,
        "n_applied": n_applied,
        "n_killed": n_killed,
        "mutants": rows,
    }


JUDGE_SYS = """You are checking whether a Lean observational contract matches a hardware English spec.
You do NOT see any Verilog. Do not invent ports.

Return ONLY JSON:
{
  "verdict": "aligned" | "partial" | "misaligned",
  "errors": ["wrong constraints"],
  "missing": ["NL requirements not constrained"],
  "tautologies": ["named props that are vacuous"],
  "datapath_arithmetic": ["props that implement BCD/magic instead of stating the rule"],
  "covered_bullet_ids": ["..."],
  "uncovered_bullet_ids": ["..."]
}
"""


def run_judge(prob_id: str, nl: str, ports_txt: str, contract: str, wrapper: Path, last_path: Path, log_path: Path) -> dict:
    bullets = GOLD.get(prob_id, [])
    prompt = (
        JUDGE_SYS
        + f"\nProblem: {prob_id}\n\nNatural language:\n{nl}\n\nPorts:\n{ports_txt}\n\n"
        + "Gold requirement bullets (coverage checklist):\n"
        + json.dumps(bullets, ensure_ascii=False)
        + "\n\nLean contract:\n```lean\n"
        + contract
        + "\n```\n"
    )
    env = os.environ.copy()
    env["NL2CHIP_ISOLATION_ROOT"] = str(wrapper.parent / "agent_state")
    env["NL2CHIP_TASK_ID"] = "contract_judge_" + re.sub(r"[^A-Za-z0-9_]", "_", prob_id)[:40]
    for name in ("OPENLUX_API_KEY", "OPENLUX_BASE_URL", "CODEX_GATEWAY_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        env.pop(name, None)
    cmd = [
        str(wrapper), "exec", "--json", "--skip-git-repo-check", "--ignore-user-config",
        "-m", "gpt-5.6-sol", "-c", 'model_reasoning_effort="high"',
        "--sandbox", "read-only", "-o", str(last_path), "-",
    ]
    prompt_path = last_path.with_suffix(".prompt.txt")
    prompt_path.write_text(prompt)
    with log_path.open("ab") as fh, prompt_path.open("rb") as pin:
        rc = subprocess.call(cmd, env=env, cwd="/tmp", stdin=pin, stdout=fh, stderr=subprocess.STDOUT)
    text = last_path.read_text(errors="replace") if last_path.exists() else ""
    m = re.search(r"\{[\s\S]*\}", text)
    parsed = {}
    if m:
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            parsed = {"parse_error": True, "raw": text[-1500:]}
    else:
        parsed = {"parse_error": True, "raw": text[-1500:]}
    parsed["judge_rc"] = rc
    verdict = parsed.get("verdict")
    parsed["ok"] = verdict == "aligned" and not parsed.get("uncovered_bullet_ids")
    parsed["aux_ok"] = verdict in {"aligned", "partial"}
    return parsed


def summarize_row(syntax, sound, complete, judge) -> dict:
    syntax_ok = bool(syntax.get("ok"))
    sound_ok = bool(sound.get("ok"))
    complete_ok = bool(complete.get("ok"))
    judge_ok = bool(judge.get("aux_ok")) if judge else True
    accept = syntax_ok and sound_ok and complete_ok
    return {
        "syntax_ok": syntax_ok,
        "sound_ok": sound_ok,
        "complete_ok": complete_ok,
        "judge_ok": judge_ok,
        "accept": accept,
        "hard_gate_ok": accept,
    }


def check_contract(
    *,
    prob_id: str,
    contract: str,
    ref_sv: str,
    nl: str,
    work: Path,
    wrapper: Path | None = None,
    run_llm_judge: bool = True,
) -> dict:
    ports = parse_ref_ports(ref_sv)
    syntax = validate_contract(contract, prob_id=prob_id, ports=ports, min_props=3)
    fields = syntax.get("fields") or {}
    ghost = syntax.get("ghost_fields") or []
    props = [(p["name"], p["body"]) for p in syntax.get("props") or []]
    work.mkdir(parents=True, exist_ok=True)
    traces, sim_err = simulate(ref_sv, ports, prob_id, work / "refsim", "ref")
    sound = eval_props_on_traces(contract, fields, props, traces, ghost, work / "reflean") if traces else {
        "ok": False, "detail": sim_err[-1500:] or "no traces", "props": {}, "n_traces": 0, "n_pairs": 0,
    }
    complete = check_completeness(contract, fields, props, ghost, ref_sv, ports, prob_id, work)
    judge = {}
    if run_llm_judge and wrapper and wrapper.exists():
        iface = "\n".join(f"  {d} {n} : {t}" for d, t, n in ports)
        judge = run_judge(
            prob_id, nl, iface, contract, wrapper,
            work / "judge_last.txt", work / "judge.log",
        )
    row = {
        "prob_id": prob_id,
        "syntax": {"ok": syntax.get("ok"), "reasons": syntax.get("reasons"), "n_props": syntax.get("n_props"),
                   "ghost_fields": ghost},
        "soundness": sound,
        "completeness": complete,
        "judge": judge,
    }
    row.update(summarize_row(syntax, sound, complete, judge))
    return row


def feedback_from_row(row: dict) -> str:
    parts = []
    syn = row.get("syntax") or {}
    if not syn.get("ok"):
        parts.append("Syntax/style gate: " + "; ".join(syn.get("reasons") or [])[:1500])
    sound = row.get("soundness") or {}
    if not sound.get("ok"):
        fails = [k for k, v in (sound.get("props") or {}).items() if v.get("status") == "fail"]
        parts.append(
            "Soundness: the contract is FALSE on the reference circuit traces. "
            "Wrong props: " + ", ".join(fails or [sound.get("detail", "")[:400]])
        )
    comp = row.get("completeness") or {}
    if not comp.get("ok"):
        live = [m["id"] for m in comp.get("mutants") or [] if m.get("applied") and not m.get("killed")]
        parts.append(
            "Completeness: these NL-violating mutants still satisfy the contract: "
            + ", ".join(live)
            + ". Add observational constraints that those mutants break. "
            "For hidden counters, declare extra Cycle fields (ghost state)."
        )
    judge = row.get("judge") or {}
    if judge:
        if judge.get("missing"):
            parts.append("Judge missing: " + "; ".join(map(str, judge["missing"])[:8]))
        if judge.get("errors"):
            parts.append("Judge errors: " + "; ".join(map(str, judge["errors"])[:8]))
        if judge.get("uncovered_bullet_ids"):
            parts.append("Uncovered gold bullets: " + ", ".join(map(str, judge["uncovered_bullet_ids"])))
        if judge.get("tautologies"):
            parts.append("Judge tautologies: " + "; ".join(map(str, judge["tautologies"])))
        if judge.get("datapath_arithmetic"):
            parts.append("Judge datapath: " + "; ".join(map(str, judge["datapath_arithmetic"])))
    return "\n".join(parts)[:4000]
