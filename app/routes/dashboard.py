from flask import Blueprint, Response

dashboard_bp = Blueprint("dashboard", __name__)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>quadroPE — Operations Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e4e4e7;
    --muted: #71717a;
    --accent: #6366f1;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
    --blue: #3b82f6;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .header {
    padding: 24px 32px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 {
    font-size: 22px;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--blue));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .header .status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--muted);
  }
  .header .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    padding: 24px 32px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
  }
  .card-label {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
    margin-bottom: 8px;
  }
  .card-value {
    font-size: 32px;
    font-weight: 700;
    line-height: 1;
  }
  .card-sub {
    font-size: 12px;
    color: var(--muted);
    margin-top: 6px;
  }
  .charts-row {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    padding: 0 32px 16px;
  }
  .charts-row-equal {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    padding: 0 32px 16px;
  }
  .chart-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
  }
  .chart-card h3 {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 16px;
    color: var(--text);
  }
  .chart-wrap { position: relative; height: 220px; }
  .table-wrap {
    max-height: 300px;
    overflow-y: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-weight: 600;
    position: sticky;
    top: 0;
    background: var(--card);
  }
  td {
    padding: 6px 12px;
    border-bottom: 1px solid var(--border);
  }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge-ok { background: rgba(34,197,94,0.15); color: var(--green); }
  .badge-warn { background: rgba(234,179,8,0.15); color: var(--yellow); }
  .badge-err { background: rgba(239,68,68,0.15); color: var(--red); }
  .logs-section {
    padding: 0 32px 32px;
  }
  .log-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
  }
  .log-entry {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 12px;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
    display: flex;
    gap: 12px;
  }
  .log-entry:last-child { border-bottom: none; }
  .log-ts { color: var(--muted); min-width: 80px; }
  .log-level { min-width: 60px; font-weight: 600; }
  .log-level.INFO { color: var(--blue); }
  .log-level.WARNING { color: var(--yellow); }
  .log-level.ERROR { color: var(--red); }
  .log-msg { color: var(--text); }
  .color-green { color: var(--green); }
  .color-red { color: var(--red); }
  .color-yellow { color: var(--yellow); }
  .color-blue { color: var(--blue); }
  @media (max-width: 900px) {
    .grid { grid-template-columns: repeat(2, 1fr); }
    .charts-row, .charts-row-equal { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>quadroPE Operations Dashboard</h1>
  <div class="status">
    <div class="dot" id="statusDot"></div>
    <span id="statusText">Connecting...</span>
    <span style="margin-left: 16px;">Uptime: <strong id="uptime">--</strong></span>
  </div>
</div>

<div class="grid">
  <div class="card">
    <div class="card-label">Avg Latency</div>
    <div class="card-value color-blue" id="avgLatency">--</div>
    <div class="card-sub">p95: <span id="p95">--</span> · p99: <span id="p99">--</span></div>
  </div>
  <div class="card">
    <div class="card-label">Traffic</div>
    <div class="card-value color-green" id="totalReqs">--</div>
    <div class="card-sub"><span id="rps">--</span> req/s</div>
  </div>
  <div class="card">
    <div class="card-label">Error Rate</div>
    <div class="card-value" id="errorRate">--</div>
    <div class="card-sub"><span id="totalErrors">--</span> total errors</div>
  </div>
  <div class="card">
    <div class="card-label">Saturation</div>
    <div class="card-value color-yellow" id="activeReqs">--</div>
    <div class="card-sub">Peak: <span id="peakReqs">--</span></div>
  </div>
</div>

<div class="charts-row">
  <div class="chart-card">
    <h3>Latency Over Time (ms)</h3>
    <div class="chart-wrap"><canvas id="latencyChart"></canvas></div>
  </div>
  <div class="chart-card">
    <h3>Error Breakdown</h3>
    <div class="chart-wrap"><canvas id="errorChart"></canvas></div>
  </div>
</div>

<div class="charts-row-equal">
  <div class="chart-card">
    <h3>Top Endpoints by Traffic</h3>
    <div class="chart-wrap"><canvas id="trafficChart"></canvas></div>
  </div>
  <div class="chart-card">
    <h3>System Resources</h3>
    <div class="chart-wrap"><canvas id="systemChart"></canvas></div>
  </div>
</div>

<div class="charts-row-equal">
  <div class="chart-card">
    <h3>Recent Requests</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Method</th><th>Path</th><th>Status</th><th>Latency</th></tr></thead>
        <tbody id="reqTable"></tbody>
      </table>
    </div>
  </div>
  <div class="chart-card">
    <h3>Live Logs</h3>
    <div class="table-wrap" id="logsWrap" style="max-height:260px; overflow-y:auto;"></div>
  </div>
</div>

<script>
const POLL_INTERVAL = 3000;
const MAX_POINTS = 30;

const chartColors = {
  accent: '#6366f1',
  blue: '#3b82f6',
  green: '#22c55e',
  red: '#ef4444',
  yellow: '#eab308',
  purple: '#a855f7',
  grid: '#2a2d3a',
  muted: '#71717a',
};

const commonOpts = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: {
    x: { grid: { color: chartColors.grid }, ticks: { color: chartColors.muted, font: { size: 10 } } },
    y: { grid: { color: chartColors.grid }, ticks: { color: chartColors.muted, font: { size: 10 } }, beginAtZero: true },
  },
};

const latencyData = { labels: [], datasets: [
  { label: 'Avg', data: [], borderColor: chartColors.blue, borderWidth: 2, pointRadius: 0, tension: 0.3 },
  { label: 'p95', data: [], borderColor: chartColors.yellow, borderWidth: 2, pointRadius: 0, tension: 0.3, borderDash: [4,4] },
  { label: 'p99', data: [], borderColor: chartColors.red, borderWidth: 2, pointRadius: 0, tension: 0.3, borderDash: [2,2] },
]};
const latencyChart = new Chart(document.getElementById('latencyChart'), {
  type: 'line', data: latencyData,
  options: { ...commonOpts, plugins: { legend: { display: true, labels: { color: chartColors.muted, boxWidth: 12, font: { size: 11 } } } } }
});

const errorData = { labels: [], datasets: [{ data: [], backgroundColor: [] }] };
const errorChart = new Chart(document.getElementById('errorChart'), {
  type: 'doughnut', data: errorData,
  options: { responsive: true, maintainAspectRatio: false, plugins: {
    legend: { position: 'bottom', labels: { color: chartColors.muted, font: { size: 11 }, padding: 12 } }
  } }
});

const trafficData = { labels: [], datasets: [{ data: [], backgroundColor: chartColors.accent, borderRadius: 6 }] };
const trafficChart = new Chart(document.getElementById('trafficChart'), {
  type: 'bar', data: trafficData,
  options: { ...commonOpts, indexAxis: 'y' }
});

const cpuHistory = [], ramHistory = [], sysLabels = [];
const systemData = { labels: sysLabels, datasets: [
  { label: 'CPU %', data: cpuHistory, borderColor: chartColors.green, borderWidth: 2, pointRadius: 0, tension: 0.3 },
  { label: 'RAM %', data: ramHistory, borderColor: chartColors.purple, borderWidth: 2, pointRadius: 0, tension: 0.3 },
]};
const systemChart = new Chart(document.getElementById('systemChart'), {
  type: 'line', data: systemData,
  options: { ...commonOpts, scales: { ...commonOpts.scales, y: { ...commonOpts.scales.y, max: 100 } },
    plugins: { legend: { display: true, labels: { color: chartColors.muted, boxWidth: 12, font: { size: 11 } } } } }
});

function statusBadge(code) {
  const n = Number(code) || 0;
  if (n < 300) return `<span class="badge badge-ok">${n}</span>`;
  if (n < 500) return `<span class="badge badge-warn">${n}</span>`;
  return `<span class="badge badge-err">${n}</span>`;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"'`=\\/]/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
    "'": '&#39;', '`': '&#96;', '=': '&#61;', '/': '&#47;',
  }[c]));
}

function fmtUptime(s) {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = Math.floor(s%60);
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function timeLabel() { return new Date().toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' }); }

function push(arr, val) { arr.push(val); if (arr.length > MAX_POINTS) arr.shift(); }

async function poll() {
  try {
    const [mRes, lRes] = await Promise.all([fetch('/metrics'), fetch('/logs')]);
    const m = await mRes.json();
    const l = await lRes.json();

    document.getElementById('statusDot').style.background = '#22c55e';
    document.getElementById('statusText').textContent = 'All systems operational';

    document.getElementById('avgLatency').textContent = m.latency.avg_ms + ' ms';
    document.getElementById('p95').textContent = m.latency.p95_ms + ' ms';
    document.getElementById('p99').textContent = m.latency.p99_ms + ' ms';
    document.getElementById('totalReqs').textContent = m.traffic.total_requests.toLocaleString();
    document.getElementById('rps').textContent = m.traffic.requests_per_second;

    const errEl = document.getElementById('errorRate');
    errEl.textContent = m.errors.error_rate_percent + '%';
    errEl.className = 'card-value ' + (m.errors.error_rate_percent > 10 ? 'color-red' : m.errors.error_rate_percent > 2 ? 'color-yellow' : 'color-green');
    document.getElementById('totalErrors').textContent = m.errors.total_errors;

    document.getElementById('activeReqs').textContent = m.saturation.active_requests;
    document.getElementById('peakReqs').textContent = m.saturation.peak_active_requests;
    document.getElementById('uptime').textContent = fmtUptime(m.uptime_seconds);

    const t = timeLabel();
    push(latencyData.labels, t);
    push(latencyData.datasets[0].data, m.latency.avg_ms);
    push(latencyData.datasets[1].data, m.latency.p95_ms);
    push(latencyData.datasets[2].data, m.latency.p99_ms);
    latencyChart.update('none');

    const errStatuses = Object.entries(m.errors.by_status || {});
    if (errStatuses.length > 0) {
      const colorMap = { '400': chartColors.yellow, '404': chartColors.blue, '422': '#f97316', '500': chartColors.red };
      errorData.labels = errStatuses.map(([k]) => 'HTTP ' + k);
      errorData.datasets[0].data = errStatuses.map(([,v]) => v);
      errorData.datasets[0].backgroundColor = errStatuses.map(([k]) => colorMap[k] || chartColors.muted);
    } else {
      errorData.labels = ['No Errors'];
      errorData.datasets[0].data = [1];
      errorData.datasets[0].backgroundColor = [chartColors.green];
    }
    errorChart.update('none');

    const top = (m.traffic.top_endpoints || []).slice(0, 8);
    trafficData.labels = top.map(e => e.endpoint.length > 25 ? e.endpoint.slice(0,25) + '...' : e.endpoint);
    trafficData.datasets[0].data = top.map(e => e.count);
    trafficChart.update('none');

    const ramPct = m.system.system_ram.total_gb > 0
      ? Math.round((m.system.system_ram.used_gb / m.system.system_ram.total_gb) * 100) : 0;
    push(sysLabels, t);
    push(cpuHistory, m.system.cpu_percent);
    push(ramHistory, ramPct);
    systemChart.update('none');

    const tbody = document.getElementById('reqTable');
    tbody.innerHTML = (m.recent_requests || []).map(r =>
      `<tr><td>${escapeHtml(r.method)}</td><td>${escapeHtml(r.path)}</td><td>${statusBadge(r.status)}</td><td>${escapeHtml(r.latency_ms)} ms</td></tr>`
    ).join('');

    const logsWrap = document.getElementById('logsWrap');
    const logs = (l.logs || []).slice(-20).reverse();
    logsWrap.innerHTML = logs.map(entry => {
      const ts = entry.timestamp ? entry.timestamp.split('T')[1]?.slice(0,8) || '' : '';
      const lvl = entry.level || 'INFO';
      return `<div class="log-entry"><span class="log-ts">${escapeHtml(ts)}</span><span class="log-level ${escapeHtml(lvl)}">${escapeHtml(lvl)}</span><span class="log-msg">${escapeHtml(entry.message || '')}</span></div>`;
    }).join('');

  } catch (e) {
    document.getElementById('statusDot').style.background = '#ef4444';
    document.getElementById('statusText').textContent = 'Connection lost';
  }
}

poll();
setInterval(poll, POLL_INTERVAL);
</script>
</body>
</html>"""


@dashboard_bp.route("/dashboard")
def dashboard():
    return Response(DASHBOARD_HTML, content_type="text/html")
