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
from tmp_toklens_llm import chat as toklens_chat

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

# Each mutant: SV search/replace plus NL-facing diagnostics for the generator.
MUTANTS: dict[str, list[dict]] = {
    "Prob035_count1to10": [
        {
            "id": "wrap11",
            "old": "q == 10",
            "new": "q == 11",
            "bullet": "wrap",
            "violation": "After q=10 the circuit goes to 11 instead of wrapping to 1.",
            "suggest": "¬pre.reset ∧ pre.q = 10#4 → post.q = 1#4",
        },
        {
            "id": "reset0",
            "old": "reset || q == 10)\n      q <= 1;",
            "new": "reset || q == 10)\n      q <= 0;",
            "bullet": "reset1",
            "violation": "Synchronous reset (and the wrap path) now writes q=0 instead of q=1.",
            "suggest": "pre.reset = true → post.q = 1#4",
        },
    ],
    "Prob037_review2015_count1k": [
        {
            "id": "wrap1023",
            "old": "q == 999",
            "new": "q == 1023",
            "bullet": "wrap",
            "violation": "q wraps at 1023 instead of after 999; 1000 is now a reachable next value.",
            "suggest": "¬pre.reset ∧ pre.q = 999#10 → post.q = 0#10",
        },
        {
            "id": "reset1",
            "old": "q <= 0;",
            "new": "q <= 1;",
            "bullet": "reset0",
            "violation": "Synchronous reset writes q=1 instead of 0.",
            "suggest": "pre.reset = true → post.q = 0#10",
        },
    ],
    "Prob063_review2015_shiftcount": [
        {
            "id": "shift_msb",
            "old": "q <= { q[2:0], data };",
            "new": "q <= { data, q[3:1] };",
            "bullet": "shift_msb",
            "violation": "shift_ena=1 now shifts LSB-first instead of inserting data as the new MSB.",
            "suggest": "pre.shift_ena = true → post.q = pre.q <<< 1 | pre.data",
        },
        {
            "id": "count_up",
            "old": "q <= q - 1'b1;",
            "new": "q <= q + 1'b1;",
            "bullet": "count",
            "violation": "When counting (shift_ena=0, count_ena=1) q increments instead of decrementing.",
            "suggest": "¬pre.shift_ena ∧ pre.count_ena = true → post.q = pre.q - 1#4",
        },
        {
            "id": "no_hold",
            "old": "else if (count_ena)\n      q <= q - 1'b1;",
            "new": "q <= q - 1'b1;",
            "bullet": "hold",
            "violation": "Both enables 0 no longer hold: the circuit still decrements.",
            "suggest": "¬pre.shift_ena ∧ ¬pre.count_ena → post.q = pre.q",
        },
    ],
    "Prob067_countslow": [
        {
            "id": "ignore_slowena",
            "old": "else if (slowena) begin",
            "new": "else begin",
            "bullet": "pause",
            "violation": "slowena=0 is ignored: q still increments instead of holding.",
            "suggest": "¬pre.reset ∧ pre.slowena = false → post.q = pre.q",
        },
        {
            "id": "wrap10",
            "old": "q == 9",
            "new": "q == 10",
            "bullet": "wrap",
            "violation": "With slowena, q goes 0..10 and wraps from 10, not after 9.",
            "suggest": "¬pre.reset ∧ pre.slowena = true ∧ pre.q = 9#4 → post.q = 0#4",
        },
    ],
    "Prob068_countbcd": [
        {
            "id": "binary_inc",
            "old": "q[i*4 +:4] == 9 && enable[i]",
            "new": "1'b0",
            "bullet": "ones",
            "violation": "Digits never wrap at 9: the whole q increments as binary, not BCD.",
            "suggest": "ones digit: ¬pre.reset ∧ pre.q[3:0]=9 → post.q[3:0]=0 (carry into tens), else +1 on ones only",
        },
        {
            "id": "ena_stuck0",
            "old": "assign ena = enable[3:1];",
            "new": "assign ena = 3'b000;",
            "bullet": "ena",
            "violation": "ena[1]/ena[2]/ena[3] are tied to 0 even when a higher digit should increment.",
            "suggest": "ena[1]=1 iff ones wrap (pre ones=9 and that digit is enabled); similarly for hundreds/thousands",
        },
    ],
    "Prob080_timer": [
        {
            "id": "no_decrement",
            "old": "else if(count_value != 0) count_value <= count_value - 1;",
            "new": "",
            "bullet": "dec",
            "violation": "When load=0 the internal count no longer decrements.",
            "suggest": "¬pre.load ∧ hidden_count≠0 → next count is count-1, and tc stays 0 until it hits 0",
        },
        {
            "id": "wrap",
            "old": "else if(count_value != 0) count_value <= count_value - 1;",
            "new": "else count_value <= count_value - 1;",
            "bullet": "stay",
            "violation": "At 0 the counter wraps instead of staying 0 until the next load.",
            "suggest": "¬pre.load ∧ tc already 1 → post.tc = true (stay at 0)",
        },
        {
            "id": "tc_tied_load",
            "old": "assign tc = count_value == 0;",
            "new": "assign tc = load;",
            "bullet": "tc",
            "violation": "tc is tied to load instead of (internal count == 0).",
            "suggest": "post.tc = true ↔ the count after this cycle is 0, independent of pre.load",
        },
    ],
    "Prob115_shift18": [
        {
            "id": "logical_shr1",
            "old": "q <= {q[63], q[63:1]};",
            "new": "q <= {1'b0, q[63:1]};",
            "bullet": "asr1",
            "violation": "amount=10 does a logical right shift by 1 (zero-fill) instead of arithmetic (sign-fill).",
            "suggest": "¬pre.load ∧ pre.ena ∧ pre.amount=2#2 → post.q = pre.q >>> 1 (sign bit copied)",
        },
        {
            "id": "logical_shr8",
            "old": "q <= {{8{q[63]}}, q[63:8]};",
            "new": "q <= {8'b0, q[63:8]};",
            "bullet": "asr8",
            "violation": "amount=11 zero-fills the top 8 bits instead of repeating q[63].",
            "suggest": "¬pre.load ∧ pre.ena ∧ pre.amount=3#2 → post.q = pre.q >>> 8 (sign-fill)",
        },
        {
            "id": "swap_amt",
            "old": "2'b00: q <= {q[62:0], 1'b0};",
            "new": "2'b00: q <= {q[63], q[63:1]};",
            "bullet": "shl1",
            "violation": "amount=00 now arithmetic-shifts right instead of shifting left by 1.",
            "suggest": "¬pre.load ∧ pre.ena ∧ pre.amount=0#2 → post.q = pre.q <<< 1",
        },
    ],
    "Prob124_rule110": [
        {
            "id": "hold",
            "old": "q <=\n      ~((q[$bits(q)-1:1] & q[$bits(q)-1:0] & {q[$bits(q)-2:0], 1'b0}) |\n      (~q[$bits(q)-1:1] & ~q[$bits(q)-1:0] & ~{q[$bits(q)-2:0], 1'b0}) |\n      (q[$bits(q)-1:1] & ~q[$bits(q)-1:0] & ~{q[$bits(q)-2:0], 1'b0}) )\n      ;",
            "new": "q <= q;",
            "bullet": "advance",
            "violation": "Without load, q holds instead of taking one Rule-110 step.",
            "suggest": "¬pre.load → post.q is Rule 110 of pre.q (do NOT write ∀i if decide cannot check it; constrain a few concrete neighbor triples)",
        },
        {
            "id": "invert",
            "old": "~((q[$bits(q)-1:1]",
            "new": "(q[$bits(q)-1:1]",
            "bullet": "table",
            "violation": "The Rule-110 table is inverted: neighbor patterns map to the wrong next bit.",
            "suggest": "Encode the 8-row Rule 110 table on three neighboring bits (111→0, 110→1, 101→1, 100→0, 011→1, 010→1, 001→1, 000→0)",
        },
    ],
    "Prob141_count_clock": [
        {
            "id": "binary_ss",
            "old": "ss[3:0] == 9",
            "new": "1'b0",
            "bullet": "bcd",
            "violation": "ss ones digit no longer wraps at 9; seconds increment as binary, not BCD 00-59.",
            "suggest": "¬pre.reset ∧ pre.ena ∧ pre.ss[3:0]=9 → post.ss[3:0]=0 and ss tens +1; never ss=0x0A",
        },
        {
            "id": "no_pm_toggle",
            "old": "if (enable[6]) pm <= ~pm;",
            "new": "",
            "bullet": "pm",
            "violation": "pm never toggles on the 11:59:59 → 12:00 wrap.",
            "suggest": "rolling 11:59:59 AM/PM with ena → post.hh=0x12, mm=ss=0, post.pm = ¬pre.pm",
        },
        {
            "id": "reset_00",
            "old": "{pm,hh,mm,ss} <= 25'h0120000;",
            "new": "{pm,hh,mm,ss} <= 25'h0000000;",
            "bullet": "reset",
            "violation": "Reset goes to 00:00 instead of 12:00 AM (hh=0x12, mm=ss=0, pm=0).",
            "suggest": "pre.reset = true → post.hh = 0x12#8 ∧ post.mm = 0 ∧ post.ss = 0 ∧ post.pm = false",
        },
    ],
    "Prob144_conwaylife": [
        {
            "id": "b3s4",
            "old": ") == 3'h3;",
            "new": ") == 3'h4;",
            "bullet": "b3s23",
            "violation": "Birth/survival uses 4 neighbors instead of 3 (not B3/S23).",
            "suggest": "¬pre.load: 0-1 neighbors die; 2 stay; 3 become alive; 4+ die. Constrain a few concrete cells, not a huge ∀ if decide times out.",
        },
        {
            "id": "no_load",
            "old": "if (load)\n      q <= data;",
            "new": "",
            "bullet": "load",
            "violation": "load=1 no longer copies data into q.",
            "suggest": "pre.load = true → post.q = pre.data",
        },
    ],
    "Prob146_fsm_serialdata": [
        {
            "id": "ignore_stop",
            "old": "STOP: next = in ? DONE : ERR;",
            "new": "STOP: next = DONE;",
            "bullet": "bad_stop",
            "violation": "A 0 stop bit is accepted as a valid frame; done still pulses.",
            "suggest": "start=0, 8 data bits, stop=0 → post.done = false (wait until line=1 before next start hunt)",
        },
        {
            "id": "done_idle",
            "old": "assign done = (state==DONE);",
            "new": "assign done = (state==START);",
            "bullet": "done",
            "violation": "done is high in the idle/start-hunt state instead of only after a valid frame.",
            "suggest": "post.done = true only on the cycle a full start+8data+stop=1 frame just completed",
        },
        {
            "id": "no_reset",
            "old": "if (reset) state <= START;",
            "new": "if (1'b0) state <= START;",
            "bullet": "reset",
            "violation": "Synchronous reset is ignored; done/out_byte can stick through reset.",
            "suggest": "pre.reset = true → post.done = false",
        },
    ],
    "Prob155_lemmings4": [
        {
            "id": "splat5",
            "old": "fall_counter >= 20",
            "new": "fall_counter >= 5",
            "bullet": "splat",
            "violation": "Splat after falling >5 cycles instead of >20.",
            "suggest": "aaah then land with fall length 6..20 → NOT splat (resume walk). Only fall>20 then ground ⇒ all outputs 0",
        },
        {
            "id": "never_splat",
            "old": "fall_counter >= 20 ? DEAD",
            "new": "1'b0 ? DEAD",
            "bullet": "splat",
            "violation": "Splat never happens no matter how long the fall.",
            "suggest": "ground=0 for 21 consecutive walk/fall cycles then ground=1 → post.walk_left=post.walk_right=post.aaah=post.digging=false",
        },
        {
            "id": "prio_bump",
            "old": "if (!ground) next = FALLL;\n        else if (dig) next = DIGL;\n        else if (bump_left) next = WR;",
            "new": "if (bump_left) next = WR;\n        else if (!ground) next = FALLL;\n        else if (dig) next = DIGL;",
            "bullet": "prio",
            "violation": "Bump/direction change is checked before fall, so ground=0 no longer has highest priority.",
            "suggest": "walking and ground=0 → post.aaah = true even if bump_left/bump_right=1",
        },
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
    {n('reset')} = 0; repeat (1005) @(negedge clk);
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
    {n('load')}=0; repeat (5) @(negedge clk);
    {n('load')}=1; {n('data')}=10'd0; @(negedge clk);
    {n('load')}=0; repeat (2) @(negedge clk);
    {n('load')}=1; {n('data')}=10'd4; @(negedge clk);
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
        result["ok"] = False
        result["checker_error"] = True
        result["detail"] = "all props mention ghost state"
        result["skipped_ghost"] = skipped_ghost
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


def gold_text(prob_id: str, bullet_id: str | None) -> str:
    if not bullet_id:
        return ""
    for b in GOLD.get(prob_id, []):
        if b.get("id") == bullet_id:
            return b.get("text") or ""
    return ""


def mutant_lookup(prob_id: str, mid: str) -> dict:
    for m in MUTANTS.get(prob_id, []):
        if m.get("id") == mid:
            return m
    return {}


def _prop_names_by_status(mrow: dict, status: str) -> list[str]:
    return [k for k, v in (mrow.get("props") or {}).items() if (v or {}).get("status") == status]


def observable_trace(traces: list[dict[str, str]], ghost: list[str]) -> tuple:
    skip = {g.strip("«»") for g in ghost}
    snaps = []
    for rec in traces:
        items = tuple(sorted(
            (k.strip("«»"), (v or "").lower())
            for k, v in rec.items()
            if k.strip("«»") not in skip
        ))
        snaps.append(items)
    return tuple(snaps)


def traces_distinguished(
    ref_traces: list[dict[str, str]],
    mut_traces: list[dict[str, str]],
    ghost: list[str],
) -> bool:
    """True iff mutant I/O dumps differ from ref on this stimulus (NL-visible fork)."""
    if not ref_traces or not mut_traces:
        return False
    return observable_trace(ref_traces, ghost) != observable_trace(mut_traces, ghost)


def mutant_issue_kind(mrow: dict) -> str:
    """Classify a completeness row: scored unkilled vs unknown vs killed."""
    if mrow.get("kind"):
        return mrow["kind"]
    detail = mrow.get("detail") or ""
    if mrow.get("killed"):
        return "killed"
    if not mrow.get("applied"):
        return "not_applied"
    if mrow.get("distinguished") is False:
        return "not_distinguished"
    markers = (
        ("sim fail", "sim_fail"),
        ("lean checker error", "lean_error"),
        ("no concrete pre/post", "no_pairs"),
        ("all props mention ghost", "ghost_skip"),
        ("lean timeout", "lean_error"),
        ("pattern not found", "not_applied"),
        ("traces match ref", "not_distinguished"),
    )
    for needle, kind in markers:
        if needle in detail:
            return kind
    return "unkilled"


def check_completeness(
    contract: str,
    fields: dict[str, str],
    props: list[tuple[str, str]],
    ghost: list[str],
    ref_sv: str,
    ports,
    prob_id: str,
    work: Path,
    ref_traces: list[dict[str, str]] | None = None,
) -> dict:
    mutants = MUTANTS.get(prob_id, [])
    rows = []
    n_applied = 0
    n_killed = 0
    n_scored = 0
    n_weak = 0
    n_unknown = 0
    if ref_traces is None:
        ref_traces, _ = simulate(ref_sv, ports, prob_id, work / "refsim_c", "ref")

    def finish_row(base: dict) -> dict:
        kind = mutant_issue_kind(base)
        base["kind"] = kind
        return base

    for spec in mutants:
        mid, old, new = spec["id"], spec["old"], spec["new"]
        meta = {
            "bullet": spec.get("bullet"),
            "violation": spec.get("violation"),
            "suggest": spec.get("suggest"),
        }
        mut = apply_mutant(ref_sv, old, new)
        if mut is None:
            n_unknown += 1
            rows.append(finish_row({
                "id": mid, "applied": False, "killed": False, "scored": False,
                "detail": "pattern not found", "kind": "not_applied", **meta,
            }))
            continue
        n_applied += 1
        traces, err = simulate(mut, ports, prob_id, work / "mut" / mid, "mut")
        if not traces:
            n_unknown += 1
            rows.append(finish_row({
                "id": mid, "applied": True, "killed": False, "scored": False,
                "detail": f"sim fail {err[-400:]}", "kind": "sim_fail", **meta,
            }))
            continue
        distinguished = traces_distinguished(ref_traces, traces, ghost)
        if not distinguished:
            n_unknown += 1
            rows.append(finish_row({
                "id": mid, "applied": True, "killed": False, "scored": False,
                "distinguished": False,
                "detail": "traces match ref on this stimulus (mutant not exercised)",
                "kind": "not_distinguished", **meta,
            }))
            continue
        ev = eval_props_on_traces(contract, fields, props, traces, ghost, work / "mut" / mid / "lean", timeout=90)
        if ev.get("checker_error") or "no concrete pre/post" in (ev.get("detail") or "") or "lean timeout" in (ev.get("detail") or "") or "all props mention ghost" in (ev.get("detail") or ""):
            kind = "lean_error"
            d = ev.get("detail") or ""
            if "no concrete" in d:
                kind = "no_pairs"
            elif "ghost" in d:
                kind = "ghost_skip"
            n_unknown += 1
            rows.append(finish_row({
                "id": mid, "applied": True, "killed": False, "scored": False,
                "distinguished": True,
                "detail": (("lean checker error: " if kind == "lean_error" else "") + d)[:400],
                "props": ev.get("props"), "kind": kind, **meta,
            }))
            continue
        killed = (not ev.get("ok")) and "violations" in (ev.get("detail") or "")
        n_scored += 1
        if killed:
            n_killed += 1
            kind = "killed"
        else:
            n_weak += 1
            kind = "unkilled"
        rows.append(finish_row({
            "id": mid,
            "applied": True,
            "killed": killed,
            "scored": True,
            "distinguished": True,
            "detail": ev.get("detail", "")[:400],
            "props": ev.get("props"),
            "kind": kind,
            **meta,
        }))
    # Hard fail only on scored unkilled mutants (case 1). Unknown (2/3) does not fail.
    ok = n_weak == 0
    return {
        "ok": ok,
        "weak": n_weak > 0,
        "vacuous": n_scored == 0,
        "n_applied": n_applied,
        "n_killed": n_killed,
        "n_scored": n_scored,
        "n_weak": n_weak,
        "n_unknown": n_unknown,
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


def run_judge(prob_id: str, nl: str, ports_txt: str, contract: str, wrapper: Path | None, last_path: Path, log_path: Path) -> dict:
    bullets = GOLD.get(prob_id, [])
    prompt = (
        f"Problem: {prob_id}\n\nNatural language:\n{nl}\n\nPorts:\n{ports_txt}\n\n"
        + "Gold requirement bullets (coverage checklist):\n"
        + json.dumps(bullets, ensure_ascii=False)
        + "\n\nLean contract:\n```lean\n"
        + contract
        + "\n```\n"
    )
    last_path.with_suffix(".prompt.txt").write_text(JUDGE_SYS + "\n" + prompt)
    try:
        result = toklens_chat(
            [
                {"role": "system", "content": JUDGE_SYS},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
            timeout=120,
        )
        text = result["text"]
        last_path.write_text(text)
        log_path.write_text(json.dumps({"ok": True, "model": result.get("model")}, ensure_ascii=False) + "\n")
        rc = 0
    except Exception as exc:
        text = ""
        log_path.write_text(f"toklens_error: {exc}\n")
        rc = 1
    parsed = {}
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "verdict" in obj:
            parsed = obj
            break
    if not parsed:
        parsed = {"parse_error": True, "raw": text[-1500:]}
    parsed["judge_rc"] = rc
    parsed["backend"] = "toklens"
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
        "complete_scored": int(complete.get("n_scored") or 0),
        "complete_weak": int(complete.get("n_weak") or 0),
        "complete_unknown": int(complete.get("n_unknown") or 0),
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
    complete = check_completeness(
        contract, fields, props, ghost, ref_sv, ports, prob_id, work, ref_traces=traces,
    )
    judge = {}
    if run_llm_judge:
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


FEEDBACK_LIMIT = 6000


def _format_completeness_mutant(prob_id: str, mrow: dict) -> str:
    mid = mrow.get("id") or "?"
    meta = mutant_lookup(prob_id, mid)
    bullet = mrow.get("bullet") or meta.get("bullet")
    nl_req = gold_text(prob_id, bullet)
    violation = mrow.get("violation") or meta.get("violation") or ""
    suggest = mrow.get("suggest") or meta.get("suggest") or ""
    kind = mutant_issue_kind(mrow)
    holds = _prop_names_by_status(mrow, "hold")
    fails = _prop_names_by_status(mrow, "fail")
    ghosts = _prop_names_by_status(mrow, "skipped_ghost")
    kind_help = {
        "unkilled": (
            "UNKILLED: this broken circuit still satisfies every evaluated Prop. "
            "The contract is too weak on the NL requirement below."
        ),
        "sim_fail": (
            "CHECKER: mutant Verilog did not simulate. Do not add ghost Cycle fields for this. "
            "Keep port-only observational constraints; the next run will retry."
        ),
        "lean_error": (
            "CHECKER: Lean could not `decide` this mutant (∀/timeout/undecidable). "
            "Rewrite the relevant Prop as a finite Boolean/BitVec implication, not ∀ over 512 bits."
        ),
        "no_pairs": (
            "CHECKER: no concrete PRE/POST pairs (X in the dump). "
            "Constrain only 0/1 ports such as done/reset; do not mention X-valued buses unless they are defined."
        ),
        "ghost_skip": (
            "CHECKER: every Prop mentions ghost Cycle fields, so completeness was skipped. "
            "Add port-only Props that `decide` can run; do not add more ghost fields."
        ),
        "not_applied": (
            "CHECKER: mutant search/replace did not match RefModule. Not a contract bug."
        ),
        "not_distinguished": (
            "UNKNOWN: mutant I/O matches the reference on this testbench, so we never saw the NL violation. "
            "Not a contract failure."
        ),
        "killed": "KILLED (ok).",
    }.get(kind, kind)
    lines = [
        f"- [{kind}] {mid}",
        f"  What the mutant did: {violation}" if violation else f"  mutant id: {mid}",
        f"  NL requirement ({bullet}): {nl_req}" if nl_req else "",
        f"  {kind_help}",
        f"  Props still HOLD on this mutant: {', '.join(holds)}" if holds else "",
        f"  Props that FAIL (good): {', '.join(fails)}" if fails else "",
        f"  Props skipped (ghost): {', '.join(ghosts)}" if ghosts else "",
        f"  Add a Prop that this mutant must violate, e.g.: {suggest}" if suggest and kind == "unkilled" else "",
    ]
    return "\n".join(x for x in lines if x)


def feedback_from_row(row: dict) -> str:
    parts = []
    pid = row.get("prob_id") or ""
    syn = row.get("syntax") or {}
    if not syn.get("ok"):
        parts.append("Syntax/style gate: " + "; ".join(syn.get("reasons") or [])[:1500])
    sound = row.get("soundness") or {}
    if not sound.get("ok"):
        fails = [k for k, v in (sound.get("props") or {}).items() if v.get("status") == "fail"]
        detail = sound.get("detail") or ""
        checkerish = bool(sound.get("checker_error")) or any(
            s in detail for s in (
                "no concrete pre/post", "lean timeout", "all props mention ghost", "failed to",
            )
        )
        if checkerish and not fails:
            parts.append(
                "Soundness checker could not evaluate the contract on ref traces: "
                + detail[:800]
                + ". Keep port-only decidable Props (no huge ∀, no ghost-only contract)."
            )
        else:
            parts.append(
                "Soundness: the contract is FALSE on the reference circuit traces. "
                "Those Props over-constrain the real DUT — relax or guard them with the correct reset/enable. "
                "Wrong props: " + ", ".join(fails or [detail[:400]])
            )
    comp = row.get("completeness") or {}
    if comp.get("mutants") is not None and not comp.get("ok"):
        mutants = comp.get("mutants") or []
        unkilled = [m for m in mutants if mutant_issue_kind(m) == "unkilled"]
        blocked = [m for m in mutants if mutant_issue_kind(m) not in {"unkilled", "killed"}]
        killed = [m for m in mutants if m.get("killed")]
        header = [
            "Completeness failed: "
            f"{comp.get('n_weak', 0)} distinguished mutant(s) still satisfy the contract "
            f"(killed {comp.get('n_killed', 0)}/{comp.get('n_scored', 0)} scored; "
            f"{comp.get('n_unknown', 0)} unknown not counted against you).",
            "Do NOT declare extra Cycle ghost fields unless a Prop must mention hidden state; "
            "prefer observational constraints on existing ports.",
        ]
        if unkilled:
            header.append("The following mutants still satisfy the contract — add the suggested Props:")
            header.extend(_format_completeness_mutant(pid, m) for m in unkilled)
        if blocked:
            header.append("These mutants were not a clean kill because of the checker, not because you should dump ghost state:")
            header.extend(_format_completeness_mutant(pid, m) for m in blocked)
        if killed:
            header.append("Already killed (keep these constraints): " + ", ".join(m.get("id", "?") for m in killed))
        parts.append("\n".join(header))
    judge = row.get("judge") or {}
    if judge:
        if judge.get("missing"):
            parts.append("Judge missing: " + "; ".join(str(x) for x in list(judge["missing"])[:8]))
        if judge.get("errors"):
            parts.append("Judge errors: " + "; ".join(str(x) for x in list(judge["errors"])[:8]))
        if judge.get("uncovered_bullet_ids"):
            parts.append("Uncovered gold bullets: " + ", ".join(str(x) for x in list(judge["uncovered_bullet_ids"])[:8]))
        if judge.get("tautologies"):
            parts.append("Judge tautologies: " + "; ".join(str(x) for x in list(judge["tautologies"])[:8]))
        if judge.get("datapath_arithmetic"):
            parts.append("Judge datapath: " + "; ".join(str(x) for x in list(judge["datapath_arithmetic"])[:8]))
    return "\n".join(parts)[:FEEDBACK_LIMIT]
