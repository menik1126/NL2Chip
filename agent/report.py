#!/usr/bin/env python3
"""
Sparkle HTML Report Generator — self-contained report from agent run results.

Usage:
    python agent/report.py results/agent_run_20250407_120000
    python agent/report.py  # auto-detect latest run
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from html import escape
from pathlib import Path


def load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    """Load summary.json and results.jsonl from a run directory."""
    summary = {}
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())

    results = []
    jsonl_path = run_dir / "results.jsonl"
    if jsonl_path.exists():
        for line in jsonl_path.read_text().splitlines():
            if line.strip():
                results.append(json.loads(line))
    return summary, results


def _pct(n: int, total: int) -> str:
    return f"{n/total*100:.1f}%" if total > 0 else "N/A"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def generate_html(summary: dict, results: list[dict], run_dir: Path) -> str:
    """Generate a self-contained HTML report."""
    attempted = summary.get("attempted", len(results))
    compile_pass = summary.get("compile_pass", sum(1 for r in results if r.get("compile_pass")))
    sim_pass = summary.get("sim_pass", sum(1 for r in results if r.get("sim_status") == "sim_pass"))
    sim_fail = summary.get("sim_fail", sum(1 for r in results if r.get("sim_status") == "sim_fail"))
    sim_error = summary.get("sim_error", sum(1 for r in results if r.get("sim_status") not in ("sim_pass", "sim_fail", "not_run")))
    synth_pass = summary.get("synth_pass", sum(1 for r in results if r.get("synth_pass")))
    synth_enabled = summary.get("synth_enabled", any(r.get("synth_pass") for r in results))
    pnr_pass = summary.get("pnr_pass", sum(1 for r in results if r.get("pnr_pass")))
    drc_pass = summary.get("drc_pass", sum(1 for r in results if r.get("drc_pass")))
    lvs_pass = summary.get("lvs_pass", sum(1 for r in results if r.get("lvs_pass")))
    pnr_enabled = summary.get("pnr_enabled", any(r.get("pnr_pass") for r in results))
    model = summary.get("model", "unknown")
    elapsed = summary.get("elapsed_seconds", 0)
    tokens = summary.get("agent_tokens", {})
    in_tok = tokens.get("input", 0)
    out_tok = tokens.get("output", 0)

    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    elapsed_str = f"{h}:{m:02d}:{s:02d}"

    # ── Build problem rows ──
    rows_html = []
    for r in results:
        pid = r.get("prob_id", "?")
        c_pass = r.get("compile_pass", False)
        lint = r.get("lint_pass", False)
        sim_st = r.get("sim_status", "not_run")
        mismatch = r.get("sim_mismatches", -1)
        s_pass = r.get("synth_pass", False)
        p_pass = r.get("pnr_pass", False)
        d_pass = r.get("drc_pass", False)
        d_violations = r.get("drc_violations", -1)
        l_pass = r.get("lvs_pass", False)
        l_error = r.get("lvs_error")
        area = r.get("area_um2")
        cells = r.get("cell_count")
        wns = r.get("wns_ns")
        power = r.get("power_uw")
        turns = r.get("agent_turns", 0)
        detail = escape(str(r.get("detail", ""))[:80])

        # Status badges
        def badge(ok, label_ok="Pass", label_fail="Fail"):
            if ok:
                return f'<span class="badge ok">{label_ok}</span>'
            return f'<span class="badge fail">{label_fail}</span>'

        sim_badge = {
            "sim_pass": '<span class="badge ok">Pass</span>',
            "sim_fail": f'<span class="badge fail">Fail ({mismatch})</span>',
            "sim_error": '<span class="badge warn">Error</span>',
            "not_run": '<span class="badge na">N/A</span>',
        }.get(sim_st, '<span class="badge na">?</span>')

        synth_cell = ""
        if synth_enabled:
            synth_cell = f"""
            <td>{badge(s_pass)}</td>
            <td class="num">{f'{area:.1f}' if area else '-'}</td>
            <td class="num">{cells if cells else '-'}</td>
            <td class="num">{f'{wns:.3f}' if wns is not None else '-'}</td>
            <td class="num">{f'{power:.2f}' if power else '-'}</td>"""

        pnr_cell = ""
        if pnr_enabled:
            drc_label = f'Pass ({d_violations})' if d_pass else 'Fail'
            drc_cls = 'ok' if d_pass else ('na' if not p_pass else 'fail')
            lvs_label = 'Pass' if l_pass else (escape(l_error[:20]) if l_error else 'Fail')
            lvs_cls = 'ok' if l_pass else ('warn' if l_error else ('na' if not p_pass else 'fail'))
            pnr_cell = f"""
            <td>{badge(p_pass)}</td>
            <td><span class="badge {drc_cls}">{drc_label if p_pass else 'N/A'}</span></td>
            <td><span class="badge {lvs_cls}">{lvs_label if p_pass else 'N/A'}</span></td>"""

        rows_html.append(f"""<tr>
            <td class="pid">{escape(pid)}</td>
            <td>{badge(c_pass)}</td>
            <td>{badge(lint)}</td>
            <td>{sim_badge}</td>{synth_cell}{pnr_cell}
            <td class="num">{turns}</td>
            <td class="detail" title="{detail}">{detail}</td>
        </tr>""")

    synth_header = ""
    if synth_enabled:
        synth_header = """
            <th>Synth</th>
            <th>Area (um2)</th>
            <th>Cells</th>
            <th>WNS (ns)</th>
            <th>Power (uW)</th>"""

    pnr_header = ""
    if pnr_enabled:
        pnr_header = """
            <th>P&R</th>
            <th>DRC</th>
            <th>LVS</th>"""

    # Compute turns column index for sort
    turns_sort_col = 4
    if synth_enabled:
        turns_sort_col += 5
    if pnr_enabled:
        turns_sort_col += 3

    # ── Synth summary card ──
    synth_card = ""
    if synth_enabled:
        synth_card = f"""
        <div class="card">
            <div class="card-value">{synth_pass}<small>/{attempted}</small></div>
            <div class="card-label">Synth Pass</div>
            <div class="card-bar"><div class="bar synth" style="width:{synth_pass/max(attempted,1)*100:.0f}%"></div></div>
        </div>"""

    # ── PNR/DRC/LVS summary cards ──
    pnr_cards = ""
    if pnr_enabled:
        pnr_cards = f"""
        <div class="card">
            <div class="card-value">{pnr_pass}<small>/{attempted}</small></div>
            <div class="card-label">P&R Pass</div>
            <div class="card-bar"><div class="bar" style="width:{pnr_pass/max(attempted,1)*100:.0f}%;background:var(--blue)"></div></div>
        </div>
        <div class="card">
            <div class="card-value">{drc_pass}<small>/{attempted}</small></div>
            <div class="card-label">DRC Pass</div>
            <div class="card-bar"><div class="bar" style="width:{drc_pass/max(attempted,1)*100:.0f}%;background:var(--green)"></div></div>
        </div>
        <div class="card">
            <div class="card-value">{lvs_pass}<small>/{attempted}</small></div>
            <div class="card-label">LVS Pass</div>
            <div class="card-bar"><div class="bar" style="width:{lvs_pass/max(attempted,1)*100:.0f}%;background:var(--yellow)"></div></div>
        </div>"""

    # ── PPA distribution (only if synth enabled) ──
    ppa_section = ""
    if synth_enabled:
        areas = [r["area_um2"] for r in results if r.get("area_um2")]
        cells_list = [r["cell_count"] for r in results if r.get("cell_count")]
        if areas:
            max_area = max(areas)
            area_bars = "".join(
                f'<div class="ppa-bar" style="width:{a/max_area*100:.0f}%" title="{r.get("prob_id","")}: {a:.1f} um2"></div>'
                for r, a in zip([r for r in results if r.get("area_um2")], areas)
            )
            ppa_section += f"""
            <div class="section">
                <h2>Area Distribution (um2)</h2>
                <div class="ppa-chart">{area_bars}</div>
                <div class="ppa-stats">min={min(areas):.1f} / avg={sum(areas)/len(areas):.1f} / max={max_area:.1f}</div>
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sparkle Agent Report — {run_dir.name}</title>
<style>
:root {{ --bg: #0d1117; --surface: #161b22; --surface2: #21262d; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e; --green: #3fb950; --red: #f85149;
  --yellow: #d29922; --blue: #58a6ff; --purple: #bc8cff; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5; padding: 24px; }}
.container {{ max-width: 1400px; margin: 0 auto; }}
h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
.subtitle {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
.card-value {{ font-size: 1.8rem; font-weight: 700; }}
.card-value small {{ font-size: 1rem; color: var(--muted); font-weight: 400; }}
.card-label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
.card-bar {{ height: 4px; background: var(--surface2); border-radius: 2px; margin-top: 8px; overflow: hidden; }}
.bar {{ height: 100%; border-radius: 2px; }}
.bar.compile {{ background: var(--blue); }}
.bar.sim {{ background: var(--green); }}
.bar.synth {{ background: var(--purple); }}
.section {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
.section h2 {{ font-size: 1rem; margin-bottom: 12px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
th {{ text-align: left; padding: 8px 10px; background: var(--surface2); color: var(--muted);
  font-weight: 600; text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.05em;
  position: sticky; top: 0; cursor: pointer; user-select: none; border-bottom: 1px solid var(--border); }}
th:hover {{ color: var(--text); }}
td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); }}
tr:hover {{ background: var(--surface2); }}
.pid {{ font-family: monospace; font-weight: 600; color: var(--blue); }}
.num {{ text-align: right; font-family: monospace; }}
.detail {{ color: var(--muted); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.72rem; font-weight: 600; }}
.badge.ok {{ background: rgba(63,185,80,0.15); color: var(--green); }}
.badge.fail {{ background: rgba(248,81,73,0.15); color: var(--red); }}
.badge.warn {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
.badge.na {{ background: var(--surface2); color: var(--muted); }}
.ppa-chart {{ display: flex; flex-direction: column; gap: 3px; }}
.ppa-bar {{ height: 14px; background: var(--purple); border-radius: 3px; min-width: 2px; opacity: 0.8; transition: opacity 0.2s; }}
.ppa-bar:hover {{ opacity: 1; }}
.ppa-stats {{ color: var(--muted); font-size: 0.78rem; margin-top: 6px; font-family: monospace; }}
.filter {{ margin-bottom: 12px; }}
.filter input {{ background: var(--surface2); border: 1px solid var(--border); color: var(--text);
  padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; width: 260px; }}
.filter input::placeholder {{ color: var(--muted); }}
footer {{ color: var(--muted); font-size: 0.75rem; margin-top: 24px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
<h1>Sparkle Agent Report</h1>
<div class="subtitle">{run_dir.name} &middot; {model} &middot; {elapsed_str} &middot; {_fmt_tokens(in_tok)}+{_fmt_tokens(out_tok)} tokens</div>

<div class="cards">
    <div class="card">
        <div class="card-value">{attempted}</div>
        <div class="card-label">Problems</div>
    </div>
    <div class="card">
        <div class="card-value">{compile_pass}<small>/{attempted}</small></div>
        <div class="card-label">Compile Pass</div>
        <div class="card-bar"><div class="bar compile" style="width:{compile_pass/max(attempted,1)*100:.0f}%"></div></div>
    </div>
    <div class="card">
        <div class="card-value">{sim_pass}<small>/{attempted}</small></div>
        <div class="card-label">Sim Pass</div>
        <div class="card-bar"><div class="bar sim" style="width:{sim_pass/max(attempted,1)*100:.0f}%"></div></div>
    </div>
    {synth_card}
    {pnr_cards}
    <div class="card">
        <div class="card-value">{_fmt_tokens(in_tok + out_tok)}</div>
        <div class="card-label">Total Tokens</div>
    </div>
</div>

{ppa_section}

<div class="section">
    <h2>Results</h2>
    <div class="filter"><input type="text" id="filter" placeholder="Filter by problem ID..." oninput="filterTable()"></div>
    <div style="overflow-x:auto;">
    <table id="results">
    <thead><tr>
        <th onclick="sortTable(0)">Problem</th>
        <th onclick="sortTable(1)">Compile</th>
        <th onclick="sortTable(2)">Lint</th>
        <th onclick="sortTable(3)">Sim</th>{synth_header}{pnr_header}
        <th onclick="sortTable({turns_sort_col})">Turns</th>
        <th>Detail</th>
    </tr></thead>
    <tbody>
    {"".join(rows_html)}
    </tbody>
    </table>
    </div>
</div>

<footer>Generated by Sparkle Agent &middot; {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</footer>
</div>

<script>
function filterTable() {{
    const q = document.getElementById('filter').value.toLowerCase();
    document.querySelectorAll('#results tbody tr').forEach(tr => {{
        tr.style.display = tr.cells[0].textContent.toLowerCase().includes(q) ? '' : 'none';
    }});
}}
function sortTable(col) {{
    const tb = document.querySelector('#results tbody');
    const rows = Array.from(tb.rows);
    const asc = tb.dataset.sortCol == col && tb.dataset.sortDir == 'asc' ? 'desc' : 'asc';
    tb.dataset.sortCol = col; tb.dataset.sortDir = asc;
    rows.sort((a, b) => {{
        let va = a.cells[col].textContent.trim(), vb = b.cells[col].textContent.trim();
        const na = parseFloat(va), nb = parseFloat(vb);
        if (!isNaN(na) && !isNaN(nb)) return asc == 'asc' ? na - nb : nb - na;
        return asc == 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
    }});
    rows.forEach(r => tb.appendChild(r));
}}
</script>
</body>
</html>"""


def generate_report(run_dir: Path) -> Path:
    """Generate HTML report and return path to the file."""
    summary, results = load_run(run_dir)
    html = generate_html(summary, results, run_dir)
    out_path = run_dir / "report.html"
    out_path.write_text(html)
    return out_path


def find_latest_run(results_dir: Path) -> Path | None:
    """Find the most recent agent_run_* directory."""
    runs = sorted(results_dir.glob("agent_run_*"), reverse=True)
    return runs[0] if runs else None


def main():
    project_root = Path(__file__).parent.parent.resolve()
    results_dir = project_root / "results"

    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1]).resolve()
    else:
        run_dir = find_latest_run(results_dir)
        if not run_dir:
            print("No agent runs found in results/")
            sys.exit(1)

    if not run_dir.exists():
        print(f"Run directory not found: {run_dir}")
        sys.exit(1)

    out = generate_report(run_dir)
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
