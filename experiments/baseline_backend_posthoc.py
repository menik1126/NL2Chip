#!/usr/bin/env python3
"""
Post-hoc backend evaluation for iterative SystemVerilog baseline outputs.

This reuses the final .sv files already produced by
experiments/baseline_verilog_iterative.py and runs:
  synthesis, post-synth GLS, P&R, DRC, LVS, and post-P&R GLS.

No LLM calls are made by this script.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import Dataset  # noqa: E402
from evaluator import parse_module_ports  # noqa: E402


DOCKER_IMAGE = "openroad/orfs:latest"
MIN_DIE_SIDE_UM = 50
SYNTH_TIMEOUT = 600
PNR_TIMEOUT = 900
DRC_TIMEOUT = 300
LVS_TIMEOUT = 300
GLS_TIMEOUT = 180


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backend eval for iterative Verilog baseline results")
    p.add_argument("--dataset", choices=["verilogeval", "rtllm", "resbench", "cvdp"], required=True)
    p.add_argument("--source-run", action="append", required=True, help="baseline_verilog_iterative_* run dir; repeatable")
    p.add_argument("--results-dir", default=None)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--filter", default=None)
    p.add_argument("--all-compiled", action="store_true", help="Run backend for all compile-passing rows instead of only sim-passing rows")
    p.add_argument("--skip-drc", action="store_true")
    p.add_argument("--skip-lvs", action="store_true")
    p.add_argument("--skip-gls", action="store_true")
    p.add_argument("--resume", action="store_true")
    return p.parse_args()


def resolve_path(p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def docker_run(command: str, synth_dir: Path, timeout: int, volumes: list[str] | None = None) -> dict:
    vol_args: list[str] = []
    for v in volumes or []:
        vol_args += ["-v", v]
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{synth_dir}:/workspace",
        *vol_args,
        "-w", "/OpenROAD-flow-scripts/flow",
        DOCKER_IMAGE,
        "bash", "-lc", command,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"success": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"Timeout ({timeout}s)"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


def make_volumes(synth_dir: Path) -> list[str]:
    return [
        f"{synth_dir / 'orfs_results'}:/OpenROAD-flow-scripts/flow/results",
        f"{synth_dir / 'orfs_logs'}:/OpenROAD-flow-scripts/flow/logs",
        f"{synth_dir / 'orfs_reports'}:/OpenROAD-flow-scripts/flow/reports",
    ]


def find_sky130_verilog_paths(synth_dir: Path) -> tuple[str, str]:
    host_dir = (
        Path.home() / ".volare" / "volare" / "sky130" / "versions"
        / "c6d73a35f524070e85faff4a6a9eef49553ebc2b"
        / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "verilog"
    )
    host_prim = host_dir / "primitives.v"
    host_cells = host_dir / "sky130_fd_sc_hd.v"
    if host_prim.exists() and host_cells.exists():
        model_dir = synth_dir / "gls_models"
        model_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(host_prim, model_dir / "primitives.v")
        shutil.copy2(host_cells, model_dir / "sky130_fd_sc_hd.v")
        return str(model_dir / "primitives.v"), str(model_dir / "sky130_fd_sc_hd.v")

    marker = synth_dir / "sky130_paths.json"
    if marker.exists():
        obj = json.loads(marker.read_text())
        return obj["primitives"], obj["cells"]
    cmd = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "roots=[Path('/OpenROAD-flow-scripts'), Path('/usr/local'), Path('/opt')]\n"
        "prims=[]; cells=[]\n"
        "for root in roots:\n"
        "    if root.exists():\n"
        "        prims += list(root.rglob('primitives.v'))\n"
        "        cells += list(root.rglob('sky130_fd_sc_hd.v'))\n"
        "print(prims[0] if prims else '')\n"
        "print(cells[0] if cells else '')\n"
        "PY"
    )
    res = docker_run(cmd, synth_dir, 120)
    lines = [l.strip() for l in res.get("stdout", "").splitlines()]
    prim = lines[0] if len(lines) >= 1 else ""
    cells = lines[1] if len(lines) >= 2 else ""
    if not prim or not cells:
        raise RuntimeError(f"Could not locate sky130 Verilog cell models in {DOCKER_IMAGE}: {res}")
    marker.write_text(json.dumps({"primitives": prim, "cells": cells}, indent=2))
    return prim, cells


def has_clk(sv_code: str) -> bool:
    return bool(re.search(r"\binput\b[^;,\n]*\bclk\b", sv_code))


def write_synth_workspace(prob_id: str, sv_code: str, top_module: str, run_dir: Path) -> Path:
    synth_dir = run_dir / "synth" / prob_id
    synth_dir.mkdir(parents=True, exist_ok=True)
    (synth_dir / f"{prob_id}.sv").write_text(sv_code)
    if has_clk(sv_code):
        (synth_dir / "constraints.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    else:
        (synth_dir / "constraints.sdc").write_text("# Combinational design - no clock constraint\n")
    for d in ["orfs_results", "orfs_logs", "orfs_reports"]:
        path = synth_dir / d
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    (synth_dir / "config.mk").write_text(
        f"export DESIGN_NAME = {top_module}\n"
        "export PLATFORM = sky130hd\n"
        f"export VERILOG_FILES = /workspace/{prob_id}.sv\n"
        "export SDC_FILE = /workspace/constraints.sdc\n"
        "export CORE_UTILIZATION = 5\n"
        "export CORE_ASPECT_RATIO = 1\n"
        "export CORE_MARGIN = 2\n"
    )
    return synth_dir


def parse_synth_stats(synth_dir: Path, result: dict) -> None:
    for sf in (synth_dir / "orfs_reports").rglob("synth_stat.txt"):
        text = sf.read_text(errors="replace")
        m = re.search(r"Chip area.*?:\s*([0-9.]+)", text)
        if m:
            result["area_um2"] = float(m.group(1))
        m = re.search(r"(\d+)\s+[\d.]+\s+cells", text)
        if m:
            result["cell_count"] = int(m.group(1))
        break


def run_synthesis(prob_id: str, sv_code: str, top_module: str, run_dir: Path) -> tuple[Path, dict]:
    result = {"synth_pass": False, "area_um2": None, "cell_count": None}
    synth_dir = write_synth_workspace(prob_id, sv_code, top_module, run_dir)
    res = docker_run(
        "make -B DESIGN_CONFIG=/workspace/config.mk synth",
        synth_dir,
        SYNTH_TIMEOUT,
        make_volumes(synth_dir),
    )
    (synth_dir / "synth_stdout.txt").write_text(res.get("stdout", ""))
    (synth_dir / "synth_stderr.txt").write_text(res.get("stderr", ""))
    if not res.get("success"):
        result["synth_error"] = res.get("stderr", "")[-1000:]
        return synth_dir, result
    result["synth_pass"] = True
    parse_synth_stats(synth_dir, result)
    return synth_dir, result


def run_pnr(synth_dir: Path, sv_code: str, top_module: str) -> dict:
    result = {"pnr_pass": False, "gds_generated": False, "wns_ns": None, "power_uw": None}
    cell_area = None
    for sf in (synth_dir / "orfs_reports").rglob("synth_stat.txt"):
        m = re.search(r"Chip area.*?:\s*([0-9.]+)", sf.read_text(errors="replace"))
        if m:
            cell_area = float(m.group(1))
            break
    if cell_area and cell_area > 0:
        core_side = math.sqrt(cell_area / 0.3)
        die_side = max(core_side + 4, MIN_DIE_SIDE_UM)
    else:
        die_side = MIN_DIE_SIDE_UM
    die_side = math.ceil(die_side)
    margin = 2
    core_side_val = die_side - 2 * margin
    sv_name = next(synth_dir.glob("*.sv")).name
    (synth_dir / "config.mk").write_text(
        f"export DESIGN_NAME = {top_module}\n"
        "export PLATFORM = sky130hd\n"
        f"export VERILOG_FILES = /workspace/{sv_name}\n"
        "export SDC_FILE = /workspace/constraints.sdc\n"
        f"export DIE_AREA = 0 0 {die_side} {die_side}\n"
        f"export CORE_AREA = {margin} {margin} {core_side_val} {core_side_val}\n"
        "export PLACE_DENSITY = 0.15\n"
    )
    if not (synth_dir / "constraints.sdc").exists():
        (synth_dir / "constraints.sdc").write_text(
            "create_clock -period 10 [get_ports clk]\n" if has_clk(sv_code) else "# Combinational design\n"
        )
    res = docker_run(
        "make DESIGN_CONFIG=/workspace/config.mk finish",
        synth_dir,
        PNR_TIMEOUT,
        make_volumes(synth_dir),
    )
    (synth_dir / "pnr_stdout.txt").write_text(res.get("stdout", ""))
    (synth_dir / "pnr_stderr.txt").write_text(res.get("stderr", ""))
    if not res.get("success"):
        result["pnr_error"] = res.get("stderr", "")[-1000:]
        return result
    result["pnr_pass"] = True
    result["gds_generated"] = bool(list((synth_dir / "orfs_results").rglob("6_final.gds")))
    for rpt in (synth_dir / "orfs_reports").rglob("6_finish.rpt"):
        text = rpt.read_text(errors="replace")
        m = re.search(r"wns\s+max\s+([0-9.eE+-]+)", text)
        if m:
            result["wns_ns"] = float(m.group(1))
        for line in text.splitlines():
            if line.strip().startswith("Total") and "100" in line:
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        result["power_uw"] = float(parts[-2]) * 1e6
                    except ValueError:
                        pass
        break
    return result


def run_drc(synth_dir: Path) -> dict:
    result = {"drc_pass": None, "drc_violations": None}
    res = docker_run(
        "make DESIGN_CONFIG=/workspace/config.mk drc",
        synth_dir,
        DRC_TIMEOUT,
        make_volumes(synth_dir),
    )
    (synth_dir / "drc_stdout.txt").write_text(res.get("stdout", ""))
    (synth_dir / "drc_stderr.txt").write_text(res.get("stderr", ""))
    if not res.get("success"):
        result["drc_error"] = res.get("stderr", "")[-500:]
        return result
    for f in (synth_dir / "orfs_reports").rglob("6_drc_count.rpt"):
        text = f.read_text(errors="replace").strip()
        result["drc_violations"] = int(text) if text.isdigit() else -1
        result["drc_pass"] = result["drc_violations"] == 0
        break
    return result


def run_lvs(synth_dir: Path) -> dict:
    result = {"lvs_pass": None, "lvs_error": None}
    cmd = (
        "sed -i -e '/ short$/d' -e 's| / | |g' "
        "/OpenROAD-flow-scripts/flow/platforms/sky130hd/cdl/sky130hd.cdl && "
        "make DESIGN_CONFIG=/workspace/config.mk lvs"
    )
    res = docker_run(cmd, synth_dir, LVS_TIMEOUT, make_volumes(synth_dir))
    (synth_dir / "lvs_stdout.txt").write_text(res.get("stdout", ""))
    (synth_dir / "lvs_stderr.txt").write_text(res.get("stderr", ""))
    if res.get("success"):
        result["lvs_pass"] = True
    else:
        result["lvs_pass"] = False
        result["lvs_error"] = res.get("stderr", "")[-500:]
    return result


def netlist_for_stage(synth_dir: Path, stage: str) -> Path | None:
    if stage == "post_synth":
        globs = ["1_*_yosys.v"]
    else:
        globs = ["6_1_merged.v", "6_final.v"]
    for glob in globs:
        found = list((synth_dir / "orfs_results").rglob(glob))
        if found:
            return found[0]
    return None


def build_gls_files(dataset: str, ds: Dataset, prob_id: str, synth_dir: Path, stage: str) -> tuple[list[str], Path]:
    info = ds.load_problem(prob_id)
    gls_dir = synth_dir / f"gls_{stage}"
    if gls_dir.exists():
        shutil.rmtree(gls_dir)
    gls_dir.mkdir(parents=True, exist_ok=True)
    netlist = netlist_for_stage(synth_dir, stage)
    if netlist is None:
        raise FileNotFoundError(f"No {stage} netlist found")
    shutil.copy2(netlist, gls_dir / "netlist.v")
    compile_files = [str(gls_dir / "netlist.v")]
    cwd = gls_dir
    if dataset == "verilogeval":
        if info.ref_path is None or not info.testbench_path.exists():
            raise FileNotFoundError("Missing VerilogEval ref/testbench")
        shutil.copy2(info.ref_path, gls_dir / "ref.sv")
        tb_text = info.testbench_path.read_text(errors="replace")
        tb_text = re.sub(r"\btb_mismatch\s*,\s*", "", tb_text)
        (gls_dir / "test.sv").write_text(tb_text)
        compile_files += [str(gls_dir / "ref.sv"), str(gls_dir / "test.sv")]
    elif dataset == "rtllm":
        shutil.copy2(info.testbench_path, gls_dir / "testbench.v")
        tb_dir = info.testbench_path.parent
        for data_file in tb_dir.iterdir():
            if data_file.is_file() and data_file.suffix in (".txt", ".dat", ".hex", ".mem"):
                shutil.copy2(data_file, gls_dir / data_file.name)
        compile_files += [str(gls_dir / "testbench.v")]
    elif dataset == "resbench":
        (gls_dir / "testbench.sv").write_text(info.metadata.get("testbench", ""))
        compile_files += [str(gls_dir / "testbench.sv")]
    else:
        raise ValueError(dataset)
    return compile_files, cwd


def parse_gls_output(dataset: str, output: str) -> tuple[str, int]:
    if dataset == "verilogeval":
        m = re.search(r"Mismatches:\s*(\d+)", output)
        if m:
            mismatches = int(m.group(1))
            return ("sim_pass" if mismatches == 0 else "sim_fail"), mismatches
    elif dataset == "rtllm":
        if re.search(r"Your Design Passed", output):
            return "sim_pass", 0
        fail_m = re.search(r"(\d+)\s*/\s*\d+\s*failures", output)
        if fail_m:
            return "sim_fail", int(fail_m.group(1))
    elif dataset == "resbench":
        if "All tests passed" in output:
            return "sim_pass", 0
        if "Some tests failed" in output or "FAIL" in output:
            return "sim_fail", -1
    return "sim_error", -1


def run_gls(dataset: str, ds: Dataset, prob_id: str, synth_dir: Path, stage: str) -> dict:
    key = "synth" if stage == "post_synth" else "pnr"
    result = {f"gls_{key}_status": "not_run", f"gls_{key}_mismatches": -1}
    try:
        prim, cells = find_sky130_verilog_paths(synth_dir)
        compile_files, cwd = build_gls_files(dataset, ds, prob_id, synth_dir, stage)
    except Exception as e:
        result[f"gls_{key}_status"] = "sim_error"
        result[f"gls_{key}_error"] = str(e)
        return result
    gls_dir = synth_dir / f"gls_{stage}"
    vvp_path = gls_dir / "gls.vvp"
    try:
        comp = subprocess.run(
            [
                "iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#0",
                "-o", str(vvp_path), prim, cells, *compile_files,
            ],
            capture_output=True, text=True, timeout=GLS_TIMEOUT,
        )
        if comp.returncode != 0:
            (gls_dir / "gls_stdout.txt").write_text(comp.stdout)
            (gls_dir / "gls_stderr.txt").write_text(comp.stderr)
            result[f"gls_{key}_status"] = "sim_error"
            result[f"gls_{key}_error"] = comp.stderr[-800:]
            return result

        sim = subprocess.run(
            ["vvp", str(vvp_path)],
            capture_output=True, text=True, timeout=GLS_TIMEOUT, cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        result[f"gls_{key}_status"] = "sim_error"
        result[f"gls_{key}_error"] = f"GLS {stage} timeout"
        return result
    except Exception as e:
        result[f"gls_{key}_status"] = "sim_error"
        result[f"gls_{key}_error"] = str(e)
        return result

    (gls_dir / "gls_stdout.txt").write_text(sim.stdout)
    (gls_dir / "gls_stderr.txt").write_text(sim.stderr)
    output = sim.stdout + sim.stderr
    if sim.returncode != 0 and not output:
        result[f"gls_{key}_status"] = "sim_error"
        result[f"gls_{key}_error"] = f"vvp exited with {sim.returncode}"
        return result
    status, mismatches = parse_gls_output(dataset, output)
    result[f"gls_{key}_status"] = status
    result[f"gls_{key}_mismatches"] = mismatches
    return result


def load_source_rows(source_runs: list[Path], dataset: str, all_compiled: bool) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for source in source_runs:
        rf = source / "results.jsonl"
        if not rf.exists():
            raise FileNotFoundError(rf)
        for line in rf.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("dataset") and row.get("dataset") != dataset:
                continue
            if all_compiled:
                eligible = bool(row.get("compile_pass"))
            else:
                eligible = row.get("sim_status") == "sim_pass"
            if not eligible:
                continue
            prob_id = row["prob_id"]
            sv_path = source / "sv" / f"{prob_id}.sv"
            if sv_path.exists():
                rows[prob_id] = {**row, "source_run": str(source), "sv_path": str(sv_path)}
    return rows


def process_one(dataset: str, ds: Dataset, row: dict, run_dir: Path, skip_drc: bool, skip_lvs: bool, skip_gls: bool) -> dict:
    prob_id = row["prob_id"]
    t0 = time.monotonic()
    sv_code = Path(row["sv_path"]).read_text(errors="replace")
    parsed_top, _ = parse_module_ports(sv_code)
    info = ds.load_problem(prob_id)
    top_module = parsed_top or info.design_name
    if dataset == "verilogeval":
        top_module = "TopModule"
    result = {
        "prob_id": prob_id,
        "dataset": dataset,
        "source_run": row["source_run"],
        "frontend_compile_pass": row.get("compile_pass"),
        "rtl_sim_status": row.get("sim_status"),
        "top_module": top_module,
        "synth_pass": False,
        "pnr_pass": False,
        "drc_pass": None,
        "lvs_pass": None,
        "gls_synth_status": "not_run",
        "gls_pnr_status": "not_run",
    }
    try:
        synth_dir, sr = run_synthesis(prob_id, sv_code, top_module, run_dir)
        result.update(sr)
        if result.get("synth_pass") and not skip_gls:
            result.update(run_gls(dataset, ds, prob_id, synth_dir, "post_synth"))
        if result.get("synth_pass"):
            pr = run_pnr(synth_dir, sv_code, top_module)
            result.update(pr)
            if result.get("pnr_pass"):
                if not skip_drc:
                    result.update(run_drc(synth_dir))
                if not skip_lvs:
                    result.update(run_lvs(synth_dir))
                if not skip_gls:
                    result.update(run_gls(dataset, ds, prob_id, synth_dir, "post_pnr"))
    except Exception as e:
        result["backend_error"] = str(e)
    result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
    return result


def main() -> None:
    args = parse_args()
    source_runs = [resolve_path(p) for p in args.source_run]
    ds = Dataset(args.dataset, PROJECT_ROOT)
    all_probs = ds.discover_problems(filter_re=args.filter)
    total = len(all_probs)
    rows = load_source_rows(source_runs, args.dataset, args.all_compiled)
    ordered = [rows[p] for p in all_probs if p in rows]
    if args.limit:
        ordered = ordered[: args.limit]
    out_base = Path(args.results_dir).resolve() if args.results_dir else PROJECT_ROOT / "results"
    run_dir = out_base / f"baseline_backend_posthoc_{args.dataset}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_file = run_dir / "results.jsonl"

    done: set[str] = set()
    if args.resume:
        for prev in out_base.glob(f"baseline_backend_posthoc_{args.dataset}_*/results.jsonl"):
            for line in prev.read_text(errors="replace").splitlines():
                try:
                    done.add(json.loads(line)["prob_id"])
                except Exception:
                    pass
    ordered = [r for r in ordered if r["prob_id"] not in done]

    print(f"dataset={args.dataset} total={total} eligible={len(rows)} attempted={len(ordered)} out={run_dir}", flush=True)
    lock_path = run_dir / ".write_lock"
    del lock_path

    def write_result(r: dict) -> None:
        with open(results_file, "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(process_one, args.dataset, ds, row, run_dir, args.skip_drc, args.skip_lvs, args.skip_gls) for row in ordered]
        for fut in as_completed(futs):
            r = fut.result()
            write_result(r)
            completed += 1
            print(
                f"[{completed}/{len(ordered)}] {r['prob_id']} "
                f"S={r.get('synth_pass')} P={r.get('pnr_pass')} "
                f"D={r.get('drc_pass')} L={r.get('lvs_pass')} "
                f"GS={r.get('gls_synth_status')} GP={r.get('gls_pnr_status')}",
                flush=True,
            )

    final_rows = [json.loads(l) for l in results_file.read_text().splitlines() if l.strip()] if results_file.exists() else []
    attempted = len(final_rows)
    summary = {
        "experiment": "baseline_backend_posthoc",
        "dataset": args.dataset,
        "total": total,
        "eligible_frontend": len(rows),
        "attempted": attempted,
        "source_runs": [str(p) for p in source_runs],
        "mode": "all_compiled" if args.all_compiled else "sim_pass_only",
        "synth_pass": sum(1 for r in final_rows if r.get("synth_pass")),
        "pnr_pass": sum(1 for r in final_rows if r.get("pnr_pass")),
        "drc_pass": sum(1 for r in final_rows if r.get("drc_pass")),
        "lvs_pass": sum(1 for r in final_rows if r.get("lvs_pass")),
        "gls_synth_pass": sum(1 for r in final_rows if r.get("gls_synth_status") == "sim_pass"),
        "gls_pnr_pass": sum(1 for r in final_rows if r.get("gls_pnr_status") == "sim_pass"),
        "avg_pd": None,
    }
    pd_vals = [summary["synth_pass"], summary["pnr_pass"], summary["drc_pass"], summary["lvs_pass"]]
    if total:
        summary.update({
            "synth_rate": f"{summary['synth_pass']/total*100:.1f}%",
            "pnr_rate": f"{summary['pnr_pass']/total*100:.1f}%",
            "drc_rate": f"{summary['drc_pass']/total*100:.1f}%",
            "lvs_rate": f"{summary['lvs_pass']/total*100:.1f}%",
            "gls_synth_rate": f"{summary['gls_synth_pass']/total*100:.1f}%",
            "gls_pnr_rate": f"{summary['gls_pnr_pass']/total*100:.1f}%",
            "avg_pd": f"{sum(pd_vals)/4/total*100:.1f}%",
        })
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
