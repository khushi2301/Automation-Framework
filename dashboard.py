#!/usr/bin/env python3
"""Usage dashboard in the Assignment 2 style, built from logs/llm_usage.csv and logs/run_history.csv.

    python dashboard.py            # writes reports/dashboard.html and opens it
    python dashboard.py --no-open  # just write the file

The page is self-contained (no network). Its "Other agent log" tab loads any CSV that has the llm_usage columns.
"""
import argparse
import csv
import json
import webbrowser
from pathlib import Path


def read_csv(path: Path):
    if not path.exists():
        return []
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def main() -> int:
    p = argparse.ArgumentParser(description="Build the usage dashboard")
    p.add_argument('--logs', default='./logs', help='Folder with llm_usage.csv and run_history.csv')
    p.add_argument('--out', default='./reports/dashboard.html')
    p.add_argument('--no-open', action='store_true', help='Do not open the browser')
    args = p.parse_args()

    logs = Path(args.logs)
    data = {'calls': read_csv(logs / 'llm_usage.csv'), 'runs': read_csv(logs / 'run_history.csv')}
    if not data['calls'] and not data['runs']:
        print(f"No usage data found in {logs}")
        return 1

    payload = json.dumps(data).replace('</', '<\\/')
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HTML.replace('__DATA__', payload), encoding='utf-8')
    print(f"Dashboard written to {out.resolve()} ({len(data['calls'])} LLM calls, {len(data['runs'])} runs)")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test Automation Usage Dashboard</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--ink:#1c2430;--mute:#5d6878;--line:#e1e5ea;--in:#2f6fed;--out:#17a398;--ok:#2e9d57;--bad:#d64545;--warn:#d89b1d;--hi:#366092}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1a2029;--ink:#e6eaf0;--mute:#9aa6b6;--line:#2a3340;--in:#6c9bff;--out:#3fc7b9;--ok:#52c27d;--bad:#ef6f6f;--warn:#e8b54a;--hi:#6f9ad0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:20px 24px 0}
h1{margin:0 0 4px;font-size:20px}
.sub{color:var(--mute);margin:0 0 14px}
nav{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--line);overflow-x:auto}
nav button{background:none;border:0;color:var(--mute);padding:10px 14px;font:inherit;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}
nav button.on{color:var(--ink);border-bottom-color:var(--hi);font-weight:600}
main{padding:20px 24px 40px;max-width:1200px;margin:0 auto}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:18px}
.tile,.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile .k{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tile .v{font-size:24px;font-weight:650;margin-top:2px}
.tile .s{color:var(--mute);font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:14px;margin-bottom:14px}
.card h2{margin:0 0 10px;font-size:14px}
.card .note{color:var(--mute);font-size:12px;margin:8px 0 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{color:var(--mute);font-weight:600;font-size:12px}
.scroll{overflow-x:auto}
.bar{display:flex;align-items:center;gap:8px;margin:7px 0}
.bar .lab{width:130px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar .track{flex:1;display:flex;height:16px;background:var(--bg);border-radius:4px;overflow:hidden}
.bar .track i{display:block;height:100%}
.bar .num{width:92px;flex:none;text-align:right;color:var(--mute);font-variant-numeric:tabular-nums}
.i-in{background:var(--in)}.i-out{background:var(--out)}
.legend{display:flex;gap:14px;color:var(--mute);font-size:12px;margin-bottom:6px}
.legend b{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.cols{display:flex;align-items:flex-end;gap:2px;height:150px;border-bottom:1px solid var(--line);padding-top:6px}
.cols i{flex:1;min-width:3px;background:var(--in);border-radius:2px 2px 0 0}
.cols i.z{background:var(--ok)}
.cols.rate i{background:var(--out)}
.axis{display:flex;justify-content:space-between;color:var(--mute);font-size:11px;margin-top:4px}
.pill{padding:1px 8px;border-radius:99px;font-size:12px}
.p-ok{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.p-bad{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad)}
.p-warn{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn)}
.drop{border:2px dashed var(--line);border-radius:10px;padding:26px;text-align:center;color:var(--mute);margin-bottom:16px}
.drop input{margin-top:10px}
.empty{color:var(--mute);padding:16px 0}
label.a{display:inline-flex;gap:8px;align-items:center;color:var(--mute);font-size:12px}
label.a input{width:64px;padding:3px 6px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:6px;font:inherit}
@media (max-width:520px){main{padding:16px 14px 32px}header{padding:16px 14px 0}nav{padding:0 14px}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>Test Automation Usage Dashboard</h1>
  <p class="sub" id="sub"></p>
</header>
<nav id="tabs">
  <button data-t="usage" class="on">LLM usage</button>
  <button data-t="runs">Test runs</button>
  <button data-t="other">Other agent log</button>
</nav>
<main>
  <section id="usage"></section>
  <section id="runs" hidden></section>
  <section id="other" hidden>
    <div class="drop">
      <div><strong>Load another agent's usage log</strong></div>
      <div>CSV with columns: timestamp, user_id, agent_name, model, input_tokens, output_tokens, latency_ms, status, cost_usd</div>
      <input type="file" id="file" accept=".csv,text/csv">
    </div>
    <div id="otherOut"><p class="empty">No file loaded yet. The file stays in your browser; nothing is uploaded.</p></div>
  </section>
</main>
<script>
const DATA = __DATA__;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = v => { const n = parseFloat(v); return isNaN(n) ? 0 : n; };
const fmt = n => Math.round(n).toLocaleString();
const usd = (n, d = 2) => '$' + n.toFixed(d);
const sum = (a, f) => a.reduce((s, x) => s + f(x), 0);
const pct = n => (n * 100).toFixed(1) + '%';
function group(a, f) { const m = new Map(); a.forEach(x => { const k = f(x); if (!m.has(k)) m.set(k, []); m.get(k).push(x); }); return m; }

function normCalls(rows) {
  return rows.map(r => ({
    t: new Date(r.timestamp), user: r.user_id || 'unknown', agent: r.agent_name || 'unknown', model: r.model || '',
    tin: num(r.input_tokens), tout: num(r.output_tokens), lat: num(r.latency_ms), cost: num(r.cost_usd),
    ok: String(r.status || '').toLowerCase() === 'success', run: r.run_id || ''
  })).filter(c => !isNaN(c.t));
}

function tile(k, v, s) { return `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s || ''}</div></div>`; }

function stackedBars(groups, keyLabel) {
  const rows = [...groups].map(([k, a]) => ({ k, i: sum(a, c => c.tin), o: sum(a, c => c.tout), cost: sum(a, c => c.cost) }))
    .sort((x, y) => (y.i + y.o) - (x.i + x.o));
  const max = Math.max(1, ...rows.map(r => r.i + r.o));
  if (!rows.length) return '<p class="empty">No data</p>';
  return `<div class="legend"><span><b class="i-in"></b>Input</span><span><b class="i-out"></b>Output</span></div>` +
    rows.map(r => `<div class="bar"><span class="lab" title="${esc(r.k)}">${esc(r.k)}</span>
      <span class="track" title="${esc(keyLabel)} ${esc(r.k)}: ${fmt(r.i)} in, ${fmt(r.o)} out, ${usd(r.cost, 4)}">
      <i class="i-in" style="width:${r.i / max * 100}%"></i><i class="i-out" style="width:${r.o / max * 100}%"></i></span>
      <span class="num">${fmt(r.i + r.o)}</span></div>`).join('');
}

function usageHTML(calls, label) {
  if (!calls.length) return '<p class="empty">No LLM calls in this log.</p>';
  const now = Date.now(), H = 3600e3;
  const tin = sum(calls, c => c.tin), tout = sum(calls, c => c.tout), cost = sum(calls, c => c.cost);
  const errs = calls.filter(c => !c.ok).length;
  const lat = sum(calls, c => c.lat) / calls.length;
  const byAgent = group(calls, c => c.agent), byUser = group(calls, c => c.user);

  const win = (a, hrs) => a.filter(c => now - c.t.getTime() <= hrs * H).length;
  const usageRows = [...byAgent].map(([k, a]) => `<tr><td>${esc(k)}</td><td>${win(a, 24)}</td><td>${win(a, 24 * 7)}</td>
    <td>${win(a, 24 * 30)}</td><td>${a.length}</td></tr>`).join('');

  const healthRows = [...byAgent].map(([k, a]) => {
    const e = a.filter(c => !c.ok).length, r = e / a.length;
    return `<tr><td>${esc(k)}</td><td>${a.length}</td><td><span class="pill ${r === 0 ? 'p-ok' : r < .05 ? 'p-warn' : 'p-bad'}">${pct(r)}</span></td>
      <td>${(sum(a, c => c.lat) / a.length / 1000).toFixed(1)} s</td><td>${usd(sum(a, c => c.cost), 3)}</td></tr>`;
  }).join('');

  const days = [...group(calls, c => c.t.toISOString().slice(0, 10))].sort((a, b) => a[0] < b[0] ? -1 : 1);
  const dmax = Math.max(1, ...days.map(([, a]) => sum(a, c => c.tin + c.tout)));
  const dayCols = days.map(([d, a]) => { const t = sum(a, c => c.tin + c.tout);
    return `<i style="height:${Math.max(2, t / dmax * 100)}%" title="${d}: ${fmt(t)} tokens, ${a.length} calls, ${usd(sum(a, c => c.cost), 3)}"></i>`; }).join('');

  return `
  <div class="tiles">
    ${tile('Total tokens', fmt(tin + tout), `${fmt(tin)} in &middot; ${fmt(tout)} out`)}
    ${tile('Estimated cost', usd(cost), `${usd(cost / calls.length, 4)} per call`)}
    ${tile('LLM calls', fmt(calls.length), `${byAgent.size} agent(s), ${byUser.size} user(s)`)}
    ${tile('Error rate', pct(errs / calls.length), `${errs} failed call(s)`)}
    ${tile('Avg latency', (lat / 1000).toFixed(1) + ' s', 'per call')}
  </div>
  <div class="grid">
    <div class="card"><h2>Tokens by agent</h2>${stackedBars(byAgent, 'Agent')}</div>
    <div class="card"><h2>Tokens by user</h2>${stackedBars(byUser, 'User')}</div>
  </div>
  <div class="grid">
    <div class="card"><h2>Agent usage (calls)</h2><div class="scroll"><table>
      <tr><th>Agent</th><th>Last 24 h</th><th>Last 7 d</th><th>Last 30 d</th><th>All time</th></tr>${usageRows}</table></div>
      <p class="note">Windows are measured from the moment this page is opened.</p></div>
    <div class="card"><h2>Error rate and latency per agent</h2><div class="scroll"><table>
      <tr><th>Agent</th><th>Calls</th><th>Error rate</th><th>Avg latency</th><th>Cost</th></tr>${healthRows}</table></div></div>
  </div>
  <div class="card"><h2>Tokens per day</h2><div class="cols">${dayCols}</div>
    <div class="axis"><span>${days[0][0]}</span><span>${days[days.length - 1][0]}</span></div></div>`;
}

// ---- tab: this framework's LLM usage ----
document.getElementById('usage').innerHTML = usageHTML(normCalls(DATA.calls));

// ---- tab: test runs ----
function runsHTML() {
  const runs = DATA.runs.map(r => ({
    id: r.run_id, start: new Date(r.start_time), dur: num(r.total_duration_ms) / 1000,
    tests: num(r.total_test_cases), pass: num(r.passed_test_cases), fail: num(r.failed_test_cases),
    block: num(r.blocked_test_cases), skip: num(r.skipped_test_cases), steps: num(r.total_steps), spass: num(r.passed_steps),
    tok: num(r.total_tokens_used), calls: num(r.llm_calls), hit: num(r.cache_hit_rate), heals: num(r.heals),
    cost: num(r.total_cost_usd)
  })).filter(r => !isNaN(r.start)).sort((a, b) => a.start - b.start);
  if (!runs.length) return '<p class="empty">No run history found.</p>';

  const executed = r => r.pass + r.fail + r.block;
  const real = runs.filter(r => r.steps > 0);
  const replays = runs.filter(r => r.tok === 0 && r.steps > 0);
  const passes = sum(runs, r => r.pass), exec = sum(runs, executed);
  const maxTok = Math.max(1, ...runs.map(r => r.tok));

  const tokCols = runs.map((r, i) => `<i class="${r.tok === 0 ? 'z' : ''}" style="height:${r.tok === 0 ? 2 : Math.max(3, r.tok / maxTok * 100)}%"
    title="#${i + 1} ${r.id}: ${fmt(r.tok)} tokens, ${usd(r.cost, 3)}"></i>`).join('');
  const hitCols = real.map((r, i) => `<i style="height:${Math.max(2, r.hit * 100)}%" title="${r.id}: ${pct(r.hit)} cache hits"></i>`).join('');

  const last = runs.slice(-15).reverse().map(r => {
    const st = r.fail ? ['Failures', 'p-bad'] : r.block ? ['Blocked', 'p-warn'] : r.pass ? ['All passed', 'p-ok'] : ['No tests', 'p-warn'];
    const m = Math.floor(r.dur / 60), s = Math.round(r.dur % 60);
    return `<tr><td>${esc(r.id)}</td><td>${r.start.toLocaleString([], {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}</td>
      <td>${m}:${String(s).padStart(2, '0')}</td><td>${r.pass} / ${r.fail} / ${r.block}</td><td>${fmt(r.tok)}</td>
      <td>${pct(r.hit)}</td><td>${r.heals}</td><td>${usd(r.cost, 3)}</td><td><span class="pill ${st[1]}">${st[0]}</span></td></tr>`;
  }).join('');

  return `
  <div class="tiles" id="runTiles"></div>
  <div class="grid">
    <div class="card"><h2>Tokens per run</h2><div class="cols">${tokCols}</div>
      <div class="axis"><span>oldest</span><span>latest</span></div>
      <p class="note">Green marks a 0-token replay: ${replays.length} of ${runs.length} runs. The first learning run is the tall bar.</p></div>
    <div class="card"><h2>Cache hit rate per run (runs that executed steps)</h2><div class="cols rate">${hitCols}</div>
      <div class="axis"><span>oldest</span><span>latest</span></div>
      <p class="note">Share of element lookups served from the store without the LLM.</p></div>
  </div>
  <div class="card"><h2>Latest 15 runs</h2><div class="scroll"><table>
    <tr><th>Run</th><th>Started</th><th>Time</th><th>Pass / Fail / Block</th><th>Tokens</th><th>Cache</th><th>Heals</th><th>Cost</th><th>Result</th></tr>${last}
  </table></div></div>
  <div class="card" style="margin-top:14px"><h2>Time saved (estimate)</h2>
    <div class="tiles" id="saved"></div>
    <label class="a">Assumed manual minutes per step <input id="mps" type="number" min="0.1" step="0.1" value="0.5"></label>
    <p class="note">Estimate = (steps executed &times; assumed manual time) &minus; actual automated run time, summed over every run that executed steps.
    It counts each run as if a person had done it by hand, so repeated runs of the same suite add up. Change the assumption to see its effect.</p>
  </div>`;
}
document.getElementById('runs').innerHTML = runsHTML();

(function wireRuns() {
  const runs = DATA.runs.map(r => ({ steps: num(r.total_steps), dur: num(r.total_duration_ms) / 1000, tok: num(r.total_tokens_used),
    pass: num(r.passed_test_cases), fail: num(r.failed_test_cases), block: num(r.blocked_test_cases), cost: num(r.total_cost_usd), hit: num(r.cache_hit_rate) }));
  const t = document.getElementById('runTiles'); if (!t) return;
  const real = runs.filter(r => r.steps > 0);
  const exec = sum(runs, r => r.pass + r.fail + r.block);
  t.innerHTML = tile('Runs', runs.length, `${real.length} executed steps`) +
    tile('Test pass rate', exec ? pct(sum(runs, r => r.pass) / exec) : 'n/a', `${sum(runs, r => r.pass)} of ${exec} executed`) +
    tile('0-token replays', runs.filter(r => r.tok === 0 && r.steps > 0).length, 'steps run, no LLM call') +
    tile('Avg cache hit rate', real.length ? pct(sum(real, r => r.hit) / real.length) : 'n/a', 'runs that executed steps') +
    tile('Total cost', usd(sum(runs, r => r.cost)), `${fmt(sum(runs, r => r.tok))} tokens`);
  const inp = document.getElementById('mps');
  const upd = () => {
    const per = num(inp.value) || 0.5;
    const manualH = sum(real, r => r.steps * per) / 60, autoH = sum(real, r => r.dur) / 3600;
    document.getElementById('saved').innerHTML = tile('Time saved', (manualH - autoH).toFixed(1) + ' h', 'person-hours, estimated') +
      tile('Manual equivalent', manualH.toFixed(1) + ' h', `${fmt(sum(real, r => r.steps))} steps`) +
      tile('Automated time', autoH.toFixed(1) + ' h', 'actual run time');
  };
  inp.addEventListener('input', upd); upd();
})();

// ---- tab: another agent's log ----
function parseCSV(text) {
  const rows = []; let row = [], cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += ch; }
    else if (ch === '"') q = true;
    else if (ch === ',') { row.push(cell); cell = ''; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cell); cell = ''; if (row.some(x => x !== '')) rows.push(row); row = []; }
    else cell += ch;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  const head = rows.shift() || [];
  return rows.map(r => Object.fromEntries(head.map((h, i) => [h.trim(), r[i]])));
}
document.getElementById('file').addEventListener('change', e => {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    const rows = parseCSV(String(rd.result));
    const need = ['timestamp', 'agent_name', 'input_tokens', 'output_tokens'];
    const have = rows.length ? Object.keys(rows[0]) : [];
    const miss = need.filter(n => !have.includes(n));
    const out = document.getElementById('otherOut');
    out.innerHTML = miss.length
      ? `<p class="empty">This file is missing required column(s): ${miss.map(esc).join(', ')}.</p>`
      : `<p class="sub">${esc(f.name)}: ${rows.length} rows</p>` + usageHTML(normCalls(rows));
  };
  rd.readAsText(f);
});

// ---- tabs and header ----
document.getElementById('sub').textContent =
  `${DATA.calls.length} LLM calls and ${DATA.runs.length} runs from logs/llm_usage.csv and logs/run_history.csv. Token counts are the API usage fields; cost is an estimate.`;
document.querySelectorAll('#tabs button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
  ['usage', 'runs', 'other'].forEach(id => document.getElementById(id).hidden = id !== b.dataset.t);
}));
</script>
</body>
</html>
"""


if __name__ == '__main__':
    raise SystemExit(main())
