<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Support Triager</title>
  <script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
    h1   { font-size: 1.6rem; margin-bottom: 4px; }
    .sub { color: #94a3b8; font-size: 0.85rem; margin-bottom: 24px; }
    .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
    .kpi     { background: #1e293b; border-radius: 12px; padding: 20px; }
    .kpi-label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; }
    .kpi-value { font-size: 2rem; font-weight: 700; margin-top: 6px; }
    .charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
    .chart-box  { background: #1e293b; border-radius: 12px; padding: 16px; }
    .chart-box h3 { font-size: 0.9rem; margin-bottom: 12px; color: #cbd5e1; }
    table   { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; }
    th      { background: #0f172a; color: #94a3b8; font-size: 0.75rem; text-transform: uppercase;
              padding: 10px 14px; text-align: left; }
    td      { padding: 10px 14px; border-top: 1px solid #334155; font-size: 0.82rem; vertical-align: top; }
    tr:hover td { background: #263045; }
    .badge  { display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 0.72rem; font-weight: 600; }
    .Critical { background: #fef2f2; color: #dc2626; }
    .High     { background: #fffbeb; color: #d97706; }
    .Low      { background: #f0fdf4; color: #16a34a; }
    .section-title { font-size: 0.9rem; color: #cbd5e1; margin: 20px 0 10px; }
    .refresh-note { color: #475569; font-size: 0.75rem; margin-top: 4px; }
  </style>
</head>
<body>

<h1>🎫 Real-Time Support Triager</h1>
<p class="sub">Auto-refreshes every 30s · <span id="last-updated"></span></p>

<div class="kpi-row">
  <div class="kpi"><div class="kpi-label">Total Tickets</div><div class="kpi-value" id="kpi-total">—</div></div>
  <div class="kpi"><div class="kpi-label">Critical</div><div class="kpi-value" id="kpi-critical" style="color:#ef4444">—</div></div>
  <div class="kpi"><div class="kpi-label">Negative Sentiment</div><div class="kpi-value" id="kpi-negative" style="color:#f59e0b">—</div></div>
  <div class="kpi"><div class="kpi-label">Avg Sentiment Score</div><div class="kpi-value" id="kpi-score">—</div></div>
</div>

<div class="charts-row">
  <div class="chart-box"><h3>📈 Volume Over Time</h3><div id="chart-volume"></div></div>
  <div class="chart-box"><h3>😊 Sentiment Breakdown</h3><div id="chart-sentiment"></div></div>
</div>
<div class="charts-row">
  <div class="chart-box"><h3>🚦 Priority Distribution</h3><div id="chart-priority"></div></div>
  <div class="chart-box"><h3>📡 Channel Breakdown</h3><div id="chart-channel"></div></div>
</div>

<p class="section-title">🗂️ Latest Tickets</p>
<table>
  <thead>
    <tr>
      <th>ID</th><th>Time</th><th>Channel</th><th>Priority</th>
      <th>Sentiment</th><th>Issue Type</th><th>Message</th>
    </tr>
  </thead>
  <tbody id="ticket-table"></tbody>
</table>

<script>
const PLOTLY_CFG   = { responsive: true, displayModeBar: false };
const DARK_LAYOUT  = {
  paper_bgcolor: "transparent", plot_bgcolor: "transparent",
  font: { color: "#e2e8f0", size: 12 },
  margin: { t: 10, b: 30, l: 40, r: 10 },
  height: 260,
};

function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleDateString() + " " + d.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
}
function floorHour(ts) {
  const d = new Date(ts); d.setMinutes(0,0,0); return d.toISOString();
}

async function refresh() {
  const res  = await fetch("/tickets/recent?limit=200");
  const data = await res.json();

  document.getElementById("last-updated").textContent =
    "Last updated: " + new Date().toLocaleTimeString();

  if (!data.length) return;

  // KPIs
  const total    = data.length;
  const critical = data.filter(t => t.priority === "Critical").length;
  const negative = data.filter(t => ["negative","highly_negative"].includes(t.sentiment)).length;
  const avgScore = (data.reduce((s,t) => s + (parseFloat(t.sentiment_score)||0), 0) / total).toFixed(2);

  document.getElementById("kpi-total").textContent    = total;
  document.getElementById("kpi-critical").textContent = critical;
  document.getElementById("kpi-negative").textContent = negative;
  document.getElementById("kpi-score").textContent    = (avgScore >= 0 ? "+" : "") + avgScore;

  // Volume over time
  const hourMap = {};
  data.forEach(t => { const h = floorHour(t.timestamp); hourMap[h] = (hourMap[h]||0) + 1; });
  const hours = Object.keys(hourMap).sort();
  Plotly.react("chart-volume", [{
    x: hours, y: hours.map(h => hourMap[h]),
    type: "scatter", mode: "lines+markers",
    line: { color: "#6366f1", width: 2 }, marker: { color: "#6366f1" }
  }], { ...DARK_LAYOUT, xaxis: { color: "#94a3b8" }, yaxis: { color: "#94a3b8" } }, PLOTLY_CFG);

  // Sentiment donut
  const sentMap = {};
  data.forEach(t => sentMap[t.sentiment] = (sentMap[t.sentiment]||0) + 1);
  const sentColors = { positive:"#22c55e", neutral:"#94a3b8", negative:"#f59e0b", highly_negative:"#ef4444" };
  Plotly.react("chart-sentiment", [{
    labels: Object.keys(sentMap), values: Object.values(sentMap),
    type: "pie", hole: 0.45,
    marker: { colors: Object.keys(sentMap).map(k => sentColors[k] || "#6366f1") },
    textinfo: "label+percent"
  }], { ...DARK_LAYOUT, showlegend: false }, PLOTLY_CFG);

  // Priority bar
  const priOrder = ["Critical","High","Low"];
  const priMap   = { Critical:0, High:0, Low:0 };
  data.forEach(t => { if (priMap[t.priority] !== undefined) priMap[t.priority]++; });
  Plotly.react("chart-priority", [{
    x: priOrder, y: priOrder.map(p => priMap[p]),
    type: "bar", text: priOrder.map(p => priMap[p]), textposition: "outside",
    marker: { color: ["#ef4444","#f59e0b","#22c55e"] }
  }], { ...DARK_LAYOUT, xaxis: { color: "#94a3b8" }, yaxis: { color: "#94a3b8" }, showlegend: false }, PLOTLY_CFG);

  // Channel bar
  const chMap = {};
  data.forEach(t => chMap[t.channel] = (chMap[t.channel]||0) + 1);
  Plotly.react("chart-channel", [{
    x: Object.keys(chMap), y: Object.values(chMap),
    type: "bar", text: Object.values(chMap), textposition: "outside",
    marker: { color: ["#6366f1","#06b6d4","#f59e0b"] }
  }], { ...DARK_LAYOUT, xaxis: { color: "#94a3b8" }, yaxis: { color: "#94a3b8" }, showlegend: false }, PLOTLY_CFG);

  // Tickets table
  const tbody = document.getElementById("ticket-table");
  tbody.innerHTML = data.slice(0, 20).map(t => `
    <tr>
      <td style="font-family:monospace;color:#94a3b8">${t.ticket_id}</td>
      <td style="white-space:nowrap;color:#94a3b8">${fmt(t.timestamp)}</td>
      <td>${t.channel}</td>
      <td><span class="badge ${t.priority}">${t.priority}</span></td>
      <td>${t.sentiment}</td>
      <td>${t.issue_type}</td>
      <td style="max-width:300px">${(t.message||"").slice(0,80)}…</td>
    </tr>`).join("");
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>