// Sparkle Dashboard — Frontend Logic
const API = '';
let currentRunId = null;
let currentProblems = [];
let sortCol = 0, sortAsc = true;

// ── API helpers ────────────────────────────────────────────────
async function api(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ── Sidebar ────────────────────────────────────────────────────
async function refreshRuns() {
  const runs = await api('/api/runs');
  const list = document.getElementById('run-list');
  list.innerHTML = runs.map(r => {
    const ts = r.timestamp ? new Date(r.timestamp).toLocaleString() : '';
    const h = Math.floor(r.elapsed_seconds / 3600);
    const m = Math.floor((r.elapsed_seconds % 3600) / 60);
    const elapsed = h > 0 ? `${h}h${m}m` : `${m}m`;
    return `<div class="run-item ${r.run_id === currentRunId ? 'active' : ''}" onclick="selectRun('${r.run_id}')">
      <div class="run-name">${r.run_id.replace('agent_run_', '')}</div>
      <div class="run-meta">${r.model || '?'} · ${elapsed} · ${ts}</div>
      <div class="run-stats">
        <span class="stat"><span class="dot green"></span>${r.sim_pass}/${r.attempted} sim</span>
        <span class="stat"><span class="dot purple"></span>${r.compile_pass} compile</span>
        ${r.synth_enabled ? `<span class="stat"><span class="dot purple"></span>${r.synth_pass} synth</span>` : ''}
        ${r.pnr_enabled ? `<span class="stat"><span class="dot purple"></span>${r.pnr_pass} P&R</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ── Select Run ─────────────────────────────────────────────────
async function selectRun(runId) {
  currentRunId = runId;
  document.querySelectorAll('.run-item').forEach(el => el.classList.remove('active'));
  document.querySelector(`.run-item[onclick*="${runId}"]`)?.classList.add('active');

  const [summary, problems] = await Promise.all([
    api(`/api/runs/${runId}`),
    api(`/api/runs/${runId}/problems`),
  ]);
  currentProblems = problems;

  document.getElementById('run-title').textContent = runId.replace('agent_run_', 'Run ');
  const btnReport = document.getElementById('btn-report');
  btnReport.style.display = summary.report_available ? '' : 'none';

  renderMain(summary, problems);
}

// ── Render Main Content ────────────────────────────────────────
function renderMain(summary, problems) {
  const a = summary.attempted || problems.length;
  const pct = (n) => a > 0 ? (n / a * 100).toFixed(0) : 0;
  const fmtTok = (n) => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(1)+'K' : n;
  const inTok = summary.agent_tokens?.input || 0;
  const outTok = summary.agent_tokens?.output || 0;

  let synthCard = '';
  if (summary.synth_enabled) {
    synthCard = `<div class="card">
      <div class="card-value">${summary.synth_pass}<small>/${a}</small></div>
      <div class="card-label">Synth Pass</div>
      <div class="card-bar"><div class="bar" style="width:${pct(summary.synth_pass)}%;background:var(--purple)"></div></div>
    </div>`;
  }

  let pnrCards = '';
  if (summary.pnr_enabled) {
    pnrCards = `<div class="card">
      <div class="card-value">${summary.pnr_pass}<small>/${a}</small></div>
      <div class="card-label">P&R Pass</div>
      <div class="card-bar"><div class="bar" style="width:${pct(summary.pnr_pass)}%;background:var(--cyan)"></div></div>
    </div>
    <div class="card">
      <div class="card-value">${summary.drc_pass}<small>/${a}</small></div>
      <div class="card-label">DRC Pass</div>
      <div class="card-bar"><div class="bar" style="width:${pct(summary.drc_pass)}%;background:var(--green)"></div></div>
    </div>
    <div class="card">
      <div class="card-value">${summary.lvs_pass}<small>/${a}</small></div>
      <div class="card-label">LVS Pass</div>
      <div class="card-bar"><div class="bar" style="width:${pct(summary.lvs_pass)}%;background:var(--yellow)"></div></div>
    </div>`;
  }

  const content = document.getElementById('main-content');
  content.innerHTML = `
    <div class="cards">
      <div class="card">
        <div class="card-value">${a}</div>
        <div class="card-label">Problems</div>
      </div>
      <div class="card">
        <div class="card-value">${summary.compile_pass}<small>/${a}</small></div>
        <div class="card-label">Compile</div>
        <div class="card-bar"><div class="bar" style="width:${pct(summary.compile_pass)}%;background:var(--blue)"></div></div>
      </div>
      <div class="card">
        <div class="card-value">${summary.sim_pass}<small>/${a}</small></div>
        <div class="card-label">Sim Pass</div>
        <div class="card-bar"><div class="bar" style="width:${pct(summary.sim_pass)}%;background:var(--green)"></div></div>
      </div>
      ${synthCard}
      ${pnrCards}
      <div class="card">
        <div class="card-value">${fmtTok(inTok + outTok)}</div>
        <div class="card-label">Tokens</div>
      </div>
    </div>

    <div class="tabs">
      <button class="tab active" onclick="switchTab('problems',this)">Problems</button>
      <button class="tab" onclick="switchTab('ppa',this)">PPA</button>
    </div>

    <div class="tab-content active" id="tab-problems">
      ${renderProblemsTable(problems, summary.synth_enabled, summary.pnr_enabled)}
    </div>
    <div class="tab-content" id="tab-ppa">
      ${renderPPA(problems)}
    </div>
  `;
}

// ── Problems Table ─────────────────────────────────────────────
function badge(ok, labelOk='Pass', labelFail='Fail') {
  return ok ? `<span class="badge ok">${labelOk}</span>` : `<span class="badge fail">${labelFail}</span>`;
}

function simBadge(status, mismatches) {
  const map = {
    sim_pass: '<span class="badge ok">Pass</span>',
    sim_fail: `<span class="badge fail">Fail (${mismatches})</span>`,
    sim_error: '<span class="badge warn">Error</span>',
    not_run: '<span class="badge na">N/A</span>',
  };
  return map[status] || '<span class="badge na">?</span>';
}

function renderProblemsTable(problems, synthEnabled, pnrEnabled) {
  const synthHeaders = synthEnabled ? '<th onclick="sortBy(4)">Synth</th><th onclick="sortBy(5)">Area</th><th onclick="sortBy(6)">Cells</th><th onclick="sortBy(7)">WNS</th>' : '';
  let nextCol = synthEnabled ? 8 : 4;
  const pnrHeaders = pnrEnabled ? `<th onclick="sortBy(${nextCol})">P&R</th><th onclick="sortBy(${nextCol+1})">DRC</th><th onclick="sortBy(${nextCol+2})">LVS</th>` : '';
  const turnsCol = nextCol + (pnrEnabled ? 3 : 0);

  const rows = problems.map(p => {
    const synthCells = synthEnabled ? `
      <td>${badge(p.synth_pass)}</td>
      <td class="num">${p.area_um2 != null ? p.area_um2.toFixed(1) : '-'}</td>
      <td class="num">${p.cell_count != null ? p.cell_count : '-'}</td>
      <td class="num">${p.wns_ns != null ? p.wns_ns.toFixed(3) : '-'}</td>` : '';
    const pnrCells = pnrEnabled ? `
      <td>${badge(p.pnr_pass)}</td>
      <td>${p.drc_pass ? `<span class="badge ok">Pass (${p.drc_violations >= 0 ? p.drc_violations : '?'})</span>` : (p.pnr_pass ? '<span class="badge fail">Fail</span>' : '<span class="badge na">N/A</span>')}</td>
      <td>${p.lvs_pass ? '<span class="badge ok">Pass</span>' : (p.lvs_error ? `<span class="badge warn" title="${p.lvs_error}">Warn</span>` : (p.pnr_pass ? '<span class="badge fail">Fail</span>' : '<span class="badge na">N/A</span>'))}</td>` : '';
    const detail = (p.detail || '').substring(0, 60);
    return `<tr class="clickable" onclick="showDetail('${p.prob_id}')">
      <td class="pid">${p.prob_id}</td>
      <td>${badge(p.compile_pass)}</td>
      <td>${badge(p.lint_pass)}</td>
      <td>${simBadge(p.sim_status, p.sim_mismatches)}</td>
      ${synthCells}
      ${pnrCells}
      <td class="num">${p.agent_turns}</td>
      <td style="color:var(--muted);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${detail}">${detail}</td>
    </tr>`;
  }).join('');

  return `<div class="section">
    <div class="section-header">
      <h3>Results (${problems.length})</h3>
      <input class="filter-input" placeholder="Filter problems..." oninput="filterProblems(this.value)">
    </div>
    <div style="overflow-x:auto">
    <table id="prob-table">
      <thead><tr>
        <th onclick="sortBy(0)">Problem</th>
        <th onclick="sortBy(1)">Compile</th>
        <th onclick="sortBy(2)">Lint</th>
        <th onclick="sortBy(3)">Sim</th>
        ${synthHeaders}
        ${pnrHeaders}
        <th onclick="sortBy(${turnsCol})">Turns</th>
        <th>Detail</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    </div>
  </div>`;
}

// ── PPA Tab ────────────────────────────────────────────────────
function renderPPA(problems) {
  const synth = problems.filter(p => p.synth_pass);
  if (synth.length === 0) return '<div class="empty"><p>No synthesis results available</p></div>';

  const areas = synth.filter(p => p.area_um2 != null);
  const cells = synth.filter(p => p.cell_count != null);

  function barChart(items, key, color, unit) {
    if (items.length === 0) return '<p style="color:var(--muted)">No data</p>';
    const maxVal = Math.max(...items.map(p => p[key]));
    const vals = items.map(p => p[key]);
    const avg = vals.reduce((a,b) => a+b, 0) / vals.length;
    const rows = items.map(p => {
      const pct = maxVal > 0 ? (p[key] / maxVal * 100).toFixed(0) : 0;
      return `<div class="ppa-row">
        <span class="label">${p.prob_id.replace('Prob','').substring(0,12)}</span>
        <div class="bar-wrap"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <span class="val">${typeof p[key] === 'number' ? (p[key] > 100 ? p[key].toFixed(0) : p[key].toFixed(2)) : '-'}</span>
      </div>`;
    }).join('');
    return `${rows}<div style="margin-top:8px;font-size:0.72rem;color:var(--muted);font-family:monospace">
      min=${Math.min(...vals).toFixed(1)} / avg=${avg.toFixed(1)} / max=${maxVal.toFixed(1)} ${unit}</div>`;
  }

  return `<div class="ppa-grid">
    <div class="ppa-card"><h3>Area (um²)</h3><div class="ppa-bars">${barChart(areas, 'area_um2', 'var(--purple)', 'um²')}</div></div>
    <div class="ppa-card"><h3>Cell Count</h3><div class="ppa-bars">${barChart(cells, 'cell_count', 'var(--cyan)', 'cells')}</div></div>
  </div>`;
}

// ── Tab switching ──────────────────────────────────────────────
function switchTab(tabId, btn) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-' + tabId).classList.add('active');
}

// ── Filter & Sort ──────────────────────────────────────────────
function filterProblems(q) {
  q = q.toLowerCase();
  document.querySelectorAll('#prob-table tbody tr').forEach(tr => {
    tr.style.display = tr.cells[0].textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

function sortBy(col) {
  const tb = document.querySelector('#prob-table tbody');
  if (!tb) return;
  if (sortCol === col) sortAsc = !sortAsc; else { sortCol = col; sortAsc = true; }
  const rows = Array.from(tb.rows);
  rows.sort((a, b) => {
    let va = a.cells[col]?.textContent.trim() || '', vb = b.cells[col]?.textContent.trim() || '';
    const na = parseFloat(va), nb = parseFloat(vb);
    if (!isNaN(na) && !isNaN(nb)) return sortAsc ? na - nb : nb - na;
    return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
  });
  rows.forEach(r => tb.appendChild(r));
}

// ── Detail Panel ───────────────────────────────────────────────
async function showDetail(probId) {
  const panel = document.getElementById('detail-panel');
  const title = document.getElementById('detail-title');
  const body = document.getElementById('detail-body');

  title.textContent = probId;
  body.innerHTML = '<p style="color:var(--muted)">Loading...</p>';
  panel.classList.add('open');

  try {
    const prob = await api(`/api/runs/${currentRunId}/problems/${probId}`);
    let html = `<div style="margin-bottom:16px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:0.8rem">
        <div>Compile: ${badge(prob.compile_pass)}</div>
        <div>Lint: ${badge(prob.lint_pass)}</div>
        <div>Sim: ${simBadge(prob.sim_status, prob.sim_mismatches)}</div>
        <div>Synth: ${badge(prob.synth_pass)}</div>
        <div>P&R: ${badge(prob.pnr_pass)}</div>
        <div>DRC: ${prob.drc_pass ? `<span class="badge ok">Pass (${prob.drc_violations >= 0 ? prob.drc_violations : '?'})</span>` : (prob.pnr_pass ? '<span class="badge fail">Fail</span>' : '<span class="badge na">N/A</span>')}</div>
        <div>LVS: ${prob.lvs_pass ? '<span class="badge ok">Pass</span>' : (prob.lvs_error ? `<span class="badge warn">${prob.lvs_error.substring(0,30)}</span>` : '<span class="badge na">N/A</span>')}</div>
        <div>GDS: ${badge(prob.gds_generated, 'Yes', 'No')}</div>
        <div>Turns: ${prob.agent_turns}</div>
        <div>Tokens: ${prob.agent_input_tokens}+${prob.agent_output_tokens}</div>
      </div>
      ${prob.detail ? `<p style="margin-top:8px;font-size:0.78rem;color:var(--muted)">${prob.detail}</p>` : ''}
    </div>`;

    if (prob.area_um2 != null || prob.cell_count != null) {
      html += `<h4 style="font-size:0.85rem;margin-bottom:8px">PPA Metrics</h4>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:0.8rem;margin-bottom:16px">
          <div>Area: ${prob.area_um2 != null ? prob.area_um2.toFixed(2) + ' um²' : 'N/A'}</div>
          <div>Cells: ${prob.cell_count ?? 'N/A'}</div>
          <div>WNS: ${prob.wns_ns != null ? prob.wns_ns.toFixed(3) + ' ns' : 'N/A'}</div>
          <div>Power: ${prob.power_uw != null ? prob.power_uw.toFixed(4) + ' uW' : 'N/A'}</div>
        </div>`;
    }

    // Try to load SV code
    try {
      const sv = await api(`/api/runs/${currentRunId}/problems/${probId}/sv`);
      html += `<h4 style="font-size:0.85rem;margin-bottom:8px">SystemVerilog</h4>
        <pre class="code">${escapeHtml(sv.content)}</pre>`;
    } catch(e) {}

    // Try to load synth logs
    try {
      const logs = await api(`/api/runs/${currentRunId}/synth/${probId}/logs`);
      if (Object.keys(logs.logs).length > 0) {
        html += `<h4 style="font-size:0.85rem;margin:16px 0 8px">Synthesis Logs</h4>`;
        for (const [name, content] of Object.entries(logs.logs)) {
          html += `<details style="margin-bottom:8px"><summary style="cursor:pointer;font-size:0.78rem;color:var(--blue)">${name}</summary>
            <pre class="code" style="margin-top:4px">${escapeHtml(content.substring(0, 3000))}</pre></details>`;
        }
      }
    } catch(e) {}

    body.innerHTML = html;
  } catch(e) {
    body.innerHTML = `<p style="color:var(--red)">Error: ${e.message}</p>`;
  }
}

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('open');
}

function openReport() {
  if (currentRunId) window.open(`/api/runs/${currentRunId}/report`, '_blank');
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Init ───────────────────────────────────────────────────────
refreshRuns();
