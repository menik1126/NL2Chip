"""
Sparkle Dashboard — FastAPI backend serving agent run results.

Reads from results/agent_run_*/ directories and serves data for the frontend.

Usage:
    uvicorn dashboard.api:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Config ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
RESULTS_DIR = PROJECT_ROOT / "results"
STATIC_DIR = Path(__file__).parent / "static"


# ── Pydantic Models ─────────────────────────────────────────────

class RunSummary(BaseModel):
    run_id: str
    timestamp: str
    model: str | None = None
    total: int = 0
    attempted: int = 0
    skipped: int = 0
    compile_pass: int = 0
    sim_pass: int = 0
    sim_fail: int = 0
    sim_error: int = 0
    synth_pass: int = 0
    pnr_pass: int = 0
    drc_pass: int = 0
    lvs_pass: int = 0
    synth_enabled: bool = False
    pnr_enabled: bool = False
    compile_rate: str | None = None
    sim_rate: str | None = None
    elapsed_seconds: int = 0
    agent_tokens: dict[str, int] = {}
    report_available: bool = False


class ProblemResult(BaseModel):
    prob_id: str
    compile_pass: bool = False
    sv_extracted: bool = False
    lint_pass: bool = False
    sim_status: str = "not_run"
    sim_mismatches: int = -1
    synth_pass: bool = False
    pnr_pass: bool = False
    gds_generated: bool = False
    drc_pass: bool = False
    drc_violations: int = -1
    lvs_pass: bool = False
    lvs_error: str | None = None
    area_um2: float | None = None
    cell_count: int | None = None
    wns_ns: float | None = None
    power_uw: float | None = None
    agent_turns: int = 0
    agent_input_tokens: int = 0
    agent_output_tokens: int = 0
    detail: str = ""
    timestamp: str | None = None


class CodeFile(BaseModel):
    filename: str
    content: str
    language: str = "systemverilog"


# ── Helpers ─────────────────────────────────────────────────────

def _list_runs() -> list[Path]:
    """List agent run directories sorted newest first."""
    if not RESULTS_DIR.exists():
        return []
    return sorted(RESULTS_DIR.glob("agent_run_*"), reverse=True)


def _get_run_dir(run_id: str) -> Path:
    run_dir = RESULTS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return run_dir


def _load_summary(run_dir: Path) -> dict:
    path = run_dir / "summary.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _load_results(run_dir: Path) -> list[dict]:
    path = run_dir / "results.jsonl"
    if not path.exists():
        return []
    results = []
    for line in path.read_text().splitlines():
        if line.strip():
            results.append(json.loads(line))
    return results


def _run_timestamp(run_dir: Path) -> str:
    """Extract timestamp from run directory name."""
    name = run_dir.name  # agent_run_20250407_120000
    parts = name.replace("agent_run_", "")
    try:
        return datetime.strptime(parts, "%Y%m%d_%H%M%S").isoformat()
    except ValueError:
        return ""


# ── App ─────────────────────────────────────────────────────────

app = FastAPI(
    title="Sparkle Dashboard API",
    description="API for viewing Sparkle agent run results and PPA metrics",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ───────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    runs = _list_runs()
    return {"status": "healthy", "runs_count": len(runs), "results_dir": str(RESULTS_DIR)}


@app.get("/api/runs", response_model=list[RunSummary])
async def list_runs():
    """List all agent runs, newest first."""
    runs = []
    for run_dir in _list_runs():
        summary = _load_summary(run_dir)
        report_exists = (run_dir / "report.html").exists()
        runs.append(RunSummary(
            run_id=run_dir.name,
            timestamp=_run_timestamp(run_dir),
            model=summary.get("model"),
            total=summary.get("total", 0),
            attempted=summary.get("attempted", 0),
            skipped=summary.get("skipped", 0),
            compile_pass=summary.get("compile_pass", 0),
            sim_pass=summary.get("sim_pass", 0),
            sim_fail=summary.get("sim_fail", 0),
            sim_error=summary.get("sim_error", 0),
            synth_pass=summary.get("synth_pass", 0),
            pnr_pass=summary.get("pnr_pass", 0),
            drc_pass=summary.get("drc_pass", 0),
            lvs_pass=summary.get("lvs_pass", 0),
            synth_enabled=summary.get("synth_enabled", False),
            pnr_enabled=summary.get("pnr_enabled", False),
            compile_rate=summary.get("compile_rate"),
            sim_rate=summary.get("sim_rate"),
            elapsed_seconds=summary.get("elapsed_seconds", 0),
            agent_tokens=summary.get("agent_tokens", {}),
            report_available=report_exists,
        ))
    return runs


@app.get("/api/runs/{run_id}", response_model=RunSummary)
async def get_run(run_id: str):
    """Get summary for a specific run."""
    run_dir = _get_run_dir(run_id)
    summary = _load_summary(run_dir)
    report_exists = (run_dir / "report.html").exists()
    return RunSummary(
        run_id=run_dir.name,
        timestamp=_run_timestamp(run_dir),
        model=summary.get("model"),
        total=summary.get("total", 0),
        attempted=summary.get("attempted", 0),
        skipped=summary.get("skipped", 0),
        compile_pass=summary.get("compile_pass", 0),
        sim_pass=summary.get("sim_pass", 0),
        sim_fail=summary.get("sim_fail", 0),
        sim_error=summary.get("sim_error", 0),
        synth_pass=summary.get("synth_pass", 0),
        pnr_pass=summary.get("pnr_pass", 0),
        drc_pass=summary.get("drc_pass", 0),
        lvs_pass=summary.get("lvs_pass", 0),
        synth_enabled=summary.get("synth_enabled", False),
        pnr_enabled=summary.get("pnr_enabled", False),
        compile_rate=summary.get("compile_rate"),
        sim_rate=summary.get("sim_rate"),
        elapsed_seconds=summary.get("elapsed_seconds", 0),
        agent_tokens=summary.get("agent_tokens", {}),
        report_available=report_exists,
    )


@app.get("/api/runs/{run_id}/problems", response_model=list[ProblemResult])
async def list_problems(
    run_id: str,
    status: Optional[str] = Query(None, description="Filter: sim_pass, sim_fail, sim_error"),
):
    """List all problem results for a run."""
    run_dir = _get_run_dir(run_id)
    results = _load_results(run_dir)
    items = []
    for r in results:
        if status and r.get("sim_status") != status:
            continue
        items.append(ProblemResult(
            prob_id=r.get("prob_id", ""),
            compile_pass=r.get("compile_pass", False),
            sv_extracted=r.get("sv_extracted", False),
            lint_pass=r.get("lint_pass", False),
            sim_status=r.get("sim_status", "not_run"),
            sim_mismatches=r.get("sim_mismatches", -1),
            synth_pass=r.get("synth_pass", False),
            pnr_pass=r.get("pnr_pass", False),
            gds_generated=r.get("gds_generated", False),
            drc_pass=r.get("drc_pass", False),
            drc_violations=r.get("drc_violations", -1),
            lvs_pass=r.get("lvs_pass", False),
            lvs_error=r.get("lvs_error"),
            area_um2=r.get("area_um2"),
            cell_count=r.get("cell_count"),
            wns_ns=r.get("wns_ns"),
            power_uw=r.get("power_uw"),
            agent_turns=r.get("agent_turns", 0),
            agent_input_tokens=r.get("agent_input_tokens", 0),
            agent_output_tokens=r.get("agent_output_tokens", 0),
            detail=r.get("detail", ""),
            timestamp=r.get("timestamp"),
        ))
    return items


@app.get("/api/runs/{run_id}/problems/{prob_id}", response_model=ProblemResult)
async def get_problem(run_id: str, prob_id: str):
    """Get detailed result for a specific problem."""
    run_dir = _get_run_dir(run_id)
    results = _load_results(run_dir)
    for r in results:
        if r.get("prob_id") == prob_id:
            return ProblemResult(
                prob_id=r.get("prob_id", ""),
                compile_pass=r.get("compile_pass", False),
                sv_extracted=r.get("sv_extracted", False),
                lint_pass=r.get("lint_pass", False),
                sim_status=r.get("sim_status", "not_run"),
                sim_mismatches=r.get("sim_mismatches", -1),
                synth_pass=r.get("synth_pass", False),
                pnr_pass=r.get("pnr_pass", False),
                gds_generated=r.get("gds_generated", False),
                drc_pass=r.get("drc_pass", False),
                drc_violations=r.get("drc_violations", -1),
                lvs_pass=r.get("lvs_pass", False),
                lvs_error=r.get("lvs_error"),
                area_um2=r.get("area_um2"),
                cell_count=r.get("cell_count"),
                wns_ns=r.get("wns_ns"),
                power_uw=r.get("power_uw"),
                agent_turns=r.get("agent_turns", 0),
                agent_input_tokens=r.get("agent_input_tokens", 0),
                agent_output_tokens=r.get("agent_output_tokens", 0),
                detail=r.get("detail", ""),
                timestamp=r.get("timestamp"),
            )
    raise HTTPException(status_code=404, detail=f"Problem not found: {prob_id}")


@app.get("/api/runs/{run_id}/problems/{prob_id}/sv", response_model=CodeFile)
async def get_sv_code(run_id: str, prob_id: str):
    """Get extracted SystemVerilog for a problem."""
    run_dir = _get_run_dir(run_id)
    sv_file = run_dir / "sv" / f"{prob_id}.sv"
    if not sv_file.exists():
        raise HTTPException(status_code=404, detail="SV file not found")
    return CodeFile(filename=sv_file.name, content=sv_file.read_text())


@app.get("/api/runs/{run_id}/ppa")
async def get_ppa_summary(run_id: str):
    """Get PPA metrics summary across all problems in a run."""
    run_dir = _get_run_dir(run_id)
    results = _load_results(run_dir)
    ppa = []
    for r in results:
        if r.get("synth_pass"):
            ppa.append({
                "prob_id": r.get("prob_id"),
                "area_um2": r.get("area_um2"),
                "cell_count": r.get("cell_count"),
                "wns_ns": r.get("wns_ns"),
                "power_uw": r.get("power_uw"),
            })

    areas = [p["area_um2"] for p in ppa if p["area_um2"] is not None]
    cells = [p["cell_count"] for p in ppa if p["cell_count"] is not None]
    return {
        "total_synth_pass": len(ppa),
        "problems": ppa,
        "stats": {
            "area": {"min": min(areas), "max": max(areas), "avg": sum(areas) / len(areas)} if areas else None,
            "cells": {"min": min(cells), "max": max(cells), "avg": sum(cells) / len(cells)} if cells else None,
        },
    }


@app.get("/api/runs/{run_id}/report")
async def get_report(run_id: str):
    """Get the HTML report for a run."""
    run_dir = _get_run_dir(run_id)
    report = run_dir / "report.html"
    if not report.exists():
        raise HTTPException(status_code=404, detail="Report not generated yet")
    return FileResponse(report, media_type="text/html")


@app.get("/api/runs/{run_id}/synth/{prob_id}/logs")
async def get_synth_logs(run_id: str, prob_id: str):
    """Get synthesis logs for a problem."""
    run_dir = _get_run_dir(run_id)
    synth_dir = run_dir / "synth" / prob_id

    if not synth_dir.exists():
        raise HTTPException(status_code=404, detail="Synthesis directory not found")

    logs: dict[str, str] = {}
    for name in ["synth_stdout.txt", "synth_stderr.txt", "config.mk", "constraints.sdc"]:
        f = synth_dir / name
        if f.exists():
            logs[name] = f.read_text()[:10000]

    # Check orfs_reports
    reports_dir = synth_dir / "orfs_reports"
    if reports_dir.exists():
        for f in reports_dir.rglob("*"):
            if f.is_file() and f.suffix in (".txt", ".rpt", ".log"):
                rel = str(f.relative_to(synth_dir))
                logs[rel] = f.read_text()[:10000]

    return {"prob_id": prob_id, "logs": logs}


# ── Static files & root ─────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the dashboard SPA."""
    index = STATIC_DIR / "index.html"
    return HTMLResponse(index.read_text())


# ── Main ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
