#!/usr/bin/env python3
import json
import re
import hashlib
from pathlib import Path
from collections import defaultdict, Counter

run = Path(
    "/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/sparkle_proof_ablation_ve30/run_20260919_221631"
)
rows = [
    json.loads(l)
    for l in (run / "results.jsonl").read_text().splitlines()
    if l.strip()
]
best = {}
for r in rows:
    key = (r["proof_mode"], r["prob_id"])
    if key not in best:
        best[key] = r
    if not r.get("agent_error"):
        best[key] = r


def lean_path(mode, pid):
    p = run / "samples" / mode / pid / "sample_000" / "lean" / f"{pid}.lean"
    return p if p.exists() else None


def extract_def(text, name):
    m = re.search(
        rf"(?ms)^(?:private\s+)?(?:noncomputable\s+)?def\s+{re.escape(name)}\b.*?"
        rf"(?=^(?:private\s+)?(?:def|theorem|lemma|structure|inductive|#synthesize)|\Z)",
        text,
    )
    return m.group(0) if m else ""


def extract_thm(text, name):
    m = re.search(
        rf"(?ms)^(?:private\s+)?(?:theorem|lemma)\s+{re.escape(name)}\b.*?"
        rf"(?=^(?:private\s+)?(?:def|theorem|lemma|#synthesize)|\Z)",
        text,
    )
    return m.group(0) if m else ""


def after_assign(s):
    i = s.find(":=")
    return s[i + 2 :] if i >= 0 else s


def norm_body(s, name):
    s = re.sub(r"--[^\n]*", "", s)
    s = re.sub(rf"\b{re.escape(name)}\b", "NAME", s)
    s = re.sub(r"\s+", " ", s).strip()
    return after_assign(s)


def analyze(text):
    defs = re.findall(
        r"(?m)^(?:private\s+)?(?:noncomputable\s+)?def\s+([A-Za-z0-9_']+)", text
    )
    thms = re.findall(
        r"(?m)^(?:private\s+)?(?:theorem|lemma)\s+([A-Za-z0-9_']+)", text
    )
    sorry = len(re.findall(r"\bsorry\b", text))
    admit = len(re.findall(r"\badmit\b", text))
    spec_defs = [d for d in defs if d.endswith("_spec") or d.endswith("Spec")]
    impl = "TopModule" if "TopModule" in defs else None
    if impl is None:
        for d in defs:
            if "Domain" in d or d.endswith("domain"):
                continue
            if d.endswith("_spec") or d.endswith("Spec"):
                continue
            impl = d
            break
    alias = False
    copy = False
    for sd in spec_defs:
        body = extract_def(text, sd)
        impl_name = sd[: -5] if sd.endswith("_spec") else impl
        if impl_name and re.search(
            rf":=\s*{re.escape(impl_name)}\s*(?:$|--)", body, re.M
        ):
            alias = True
        ib = extract_def(text, impl_name) if impl_name else ""
        if ib and body:
            if (
                norm_body(body, sd) == norm_body(ib, impl_name or "")
                and len(norm_body(body, sd)) > 40
            ):
                copy = True
    tacs = []
    rfl_thms = 0
    nontrivial = 0
    for name in thms:
        blk = extract_thm(text, name)
        tm = re.search(r":=\s*by\s+(.*)", blk, re.S)
        tac = (tm.group(1) if tm else "").strip()
        first = tac.split()[0] if tac else ""
        tacs.append((name, first, re.sub(r"\s+", " ", tac)[:160]))
        if tac.strip() == "rfl" or tac.strip().startswith("rfl"):
            rfl_thms += 1
        else:
            nontrivial += 1
    return {
        "nlines": text.count("\n") + 1,
        "defs": defs,
        "thms": thms,
        "sorry": sorry,
        "admit": admit,
        "alias": alias,
        "copy": copy,
        "rfl_thms": rfl_thms,
        "nontrivial": nontrivial,
        "tacs": tacs,
        "impl": impl,
        "md5": hashlib.md5(text.encode()).hexdigest()[:8],
        "has_file": True,
    }


print("=== tool use ===")
for mode in ["one_shot", "stepwise"]:
    n_files = 0
    step = 0
    check = 0
    hits = Counter()
    for p in (run / "samples" / mode).rglob("generate.jsonl"):
        n_files += 1
        blob = p.read_text(errors="replace").lower()
        if "lean_proof_step" in blob:
            step += 1
        if "lean-check" in blob:
            check += 1
        for k in [
            "lean_proof_step",
            "--env ",
            "proof_repl",
            "by sorry",
            "step.lean",
        ]:
            if k in blob:
                hits[k] += 1
    print(
        mode,
        "genjsonl",
        n_files,
        "files_with_proof_step",
        step,
        "files_with_lean-check",
        check,
        dict(hits),
    )

pids = sorted({pid for (_, pid) in best})
print("\n=== per problem ===")
print(
    f"{'pid':28} oC oS       oP sC sS       sP  o_rfl s_rfl o_nt s_nt o_copy s_copy same o_err s_err o_lean s_lean"
)
diff_rows = []
real_proof = []
for pid in pids:
    a, b = best[("one_shot", pid)], best[("stepwise", pid)]
    pa, pb = lean_path("one_shot", pid), lean_path("stepwise", pid)
    aa = analyze(pa.read_text()) if pa else None
    ab = analyze(pb.read_text()) if pb else None

    def gi(x, k):
        return x[k] if x else -1

    same = bool(aa and ab and aa["md5"] == ab["md5"])
    print(
        f"{pid:28} {int(bool(a.get('compile_pass')))} {str(a.get('sim_status')):8} {int(bool(a.get('proof_complete')))} "
        f"{int(bool(b.get('compile_pass')))} {str(b.get('sim_status')):8} {int(bool(b.get('proof_complete')))}  "
        f"{gi(aa,'rfl_thms'):5} {gi(ab,'rfl_thms'):5} {gi(aa,'nontrivial'):4} {gi(ab,'nontrivial'):4} "
        f"{str(gi(aa,'copy')):6} {str(gi(ab,'copy')):6} {str(same):5} "
        f"{str(bool(a.get('agent_error'))):5} {str(bool(b.get('agent_error'))):5} "
        f"{str(bool(pa)):6} {str(bool(pb)):6}"
    )
    if (a.get("proof_complete") != b.get("proof_complete")) or (
        a.get("sim_status") != b.get("sim_status")
    ):
        diff_rows.append(pid)
    if (aa and aa["nontrivial"] > 0) or (ab and ab["nontrivial"] > 0):
        real_proof.append(pid)

print("\ndiffer on sim/proof:")
for pid in diff_rows:
    a, b = best[("one_shot", pid)], best[("stepwise", pid)]
    print(
        " ",
        pid,
        "one",
        a.get("sim_status"),
        int(bool(a.get("proof_complete"))),
        "err",
        bool(a.get("agent_error")),
        "| step",
        b.get("sim_status"),
        int(bool(b.get("proof_complete"))),
        "err",
        bool(b.get("agent_error")),
    )

print("\nany non-rfl theorem pids:", real_proof)

print("\n=== tactic catalog ===")
for mode in ["one_shot", "stepwise"]:
    c = Counter()
    n_rfl = 0
    n_other = 0
    n_missing = 0
    n_copy = 0
    n_alias = 0
    n_files = 0
    for pid in pids:
        p = lean_path(mode, pid)
        if not p:
            n_missing += 1
            continue
        n_files += 1
        a = analyze(p.read_text())
        n_copy += int(a["copy"])
        n_alias += int(a["alias"])
        for name, first, tac in a["tacs"]:
            if tac.strip() == "rfl" or tac.strip().startswith("rfl"):
                n_rfl += 1
            else:
                n_other += 1
                c[tac[:100]] += 1
    print(
        mode,
        "files",
        n_files,
        "missing",
        n_missing,
        "copy_spec",
        n_copy,
        "alias_spec",
        n_alias,
        "rfl_thms",
        n_rfl,
        "other_thms",
        n_other,
    )
    for t, n in c.most_common(20):
        print("  ", n, t)

print("\n=== dump non-rfl theorems ===")
for mode in ["one_shot", "stepwise"]:
    for pid in pids:
        p = lean_path(mode, pid)
        if not p:
            continue
        a = analyze(p.read_text())
        for name, first, tac in a["tacs"]:
            if not (tac.strip() == "rfl" or tac.strip().startswith("rfl")):
                print(mode, pid, name, ":= by", tac)
