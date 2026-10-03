/* Astrolabe dashboard client: REST seed + WS live updates. */
"use strict";

const state = {
  tickers: {}, funding: {}, balance: null, positions: [], alerts: [],
  chartInst: null, chart: null,
};

const $ = (sel) => document.querySelector(sel);
const fmt = (n, d = 2) => (n == null || isNaN(n)) ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const pctClass = (v) => v >= 0 ? "up" : "down";

/* ---------- watchlist ---------- */
function renderWatchlist() {
  const tbody = $("#watchlist tbody");
  const rows = Object.values(state.tickers).filter(t => state.watchlist?.includes(t.instId));
  tbody.innerHTML = rows.map(t => {
    const chg = parseFloat(t.sodUtc0 || t.open24h) ? ((parseFloat(t.last) - parseFloat(t.open24h)) / parseFloat(t.open24h)) * 100 : null;
    const fr = state.funding[t.instId];
    return `<tr>
      <td>${t.instId}</td>
      <td>${fmt(parseFloat(t.last), t.instId.includes("SWAP") ? 1 : 2)}</td>
      <td class="${chg == null ? "" : pctClass(chg)}">${chg == null ? "—" : chg.toFixed(2) + "%"}</td>
      <td class="muted">${fmt(parseFloat(t.high24h), 0)} / ${fmt(parseFloat(t.low24h), 0)}</td>
      <td class="${fr != null ? pctClass(fr) : "muted"}">${fr != null ? fr.toFixed(4) + "%" : "—"}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="5" class="muted">waiting for data…</td></tr>`;
}

/* ---------- chart ---------- */
const DAY_BARS = new Set(["1D", "1W", "1M"]);

function axisLabelFormatter(value) {
  const d = new Date(+value);
  if (DAY_BARS.has(state.chartBar)) {
    return state.chartBar === "1M"
      ? d.toLocaleDateString("zh-CN", { year: "numeric", month: "short" })
      : d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  }
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function initChart() {
  state.chart = echarts.init($("#chart"), "dark");
  window.addEventListener("resize", () => state.chart.resize());
  const select = $("#inst-select");
  select.innerHTML = state.watchlist.map(i => `<option>${i}</option>`).join("");
  select.value = state.chartInst = state.defaultChartInst || state.watchlist[0];
  select.addEventListener("change", () => { state.chartInst = select.value; loadCandles(); });

  const barSelect = $("#bar-select");
  const bars = state.timeframes || ["1m", "5m", "15m", "1H", "4H", "1D", "1W", "1M"];
  barSelect.innerHTML = bars.map(b => `<option value="${b}">${b}</option>`).join("");
  barSelect.value = state.chartBar = "1m";
  barSelect.addEventListener("change", () => { state.chartBar = barSelect.value; loadCandles(); });

  loadCandles();
}

async function loadCandles() {
  const inst = state.chartInst, bar = state.chartBar || "1m";
  const candles = await fetch(`/api/candles?instId=${inst}&bar=${bar}`).then(r => r.json());
  if (!Array.isArray(candles)) return;
  // OKX returns newest-first: [ts, o, h, l, c, ...]
  const rows = candles.map(c => [c[0], c[1], c[2], c[3], c[4]]).reverse();
  const ts = rows.map(r => parseInt(r[0]));
  const kdata = rows.map(r => [+r[1], +r[2], +r[3], +r[4]]);
  state.chartData = { ts, kdata };
  drawChart();
}

function drawChart() {
  const { ts, kdata } = state.chartData || {};
  if (!ts) return;
  state.chart.setOption({
    backgroundColor: "transparent",
    grid: { left: 60, right: 20, top: 20, bottom: 60 },
    xAxis: { type: "category", data: ts, axisLabel: { color: "#6b7694", formatter: axisLabelFormatter } },
    yAxis: { scale: true, axisLabel: { color: "#6b7694" }, splitLine: { lineStyle: { color: "#1c2540" } } },
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    series: [{
      type: "candlestick", data: kdata,
      itemStyle: { color: "#4fd6a0", color0: "#f7768e", borderColor: "#4fd6a0", borderColor0: "#f7768e" },
    }],
  }, { notMerge: true });
}

function pushCandle(c) {
  if (!state.chartData) return;
  const i = state.chartData.ts.indexOf(c[0]);
  const bar = [+c[1], +c[2], +c[3], +c[4]];
  if (i === -1) {
    state.chartData.ts.push(c[0]); state.chartData.kdata.push(bar);
    if (state.chartData.ts.length > 300) { state.chartData.ts.shift(); state.chartData.kdata.shift(); }
  } else {
    state.chartData.kdata[i] = bar;
  }
  drawChart();
}

/* ---------- account ---------- */
function renderAccount() {
  const hasPrivate = state.private;
  $("#account-guide").classList.toggle("hidden", hasPrivate);
  $("#account-data").classList.toggle("hidden", !hasPrivate);
  if (!hasPrivate) return;
  const eq = state.balance ? parseFloat(state.balance.eq) : null;
  $("#equity").textContent = eq != null ? fmt(eq) + " USDT" : "—";
  const tbody = $("#positions tbody");
  const rows = state.positions.filter(p => parseFloat(p.pos) !== 0);
  $("#no-positions").classList.toggle("hidden", rows.length > 0);
  tbody.innerHTML = rows.map(p => {
    const upl = parseFloat(p.upl || 0), avg = parseFloat(p.avgPx || 0);
    return `<tr>
      <td>${p.instId}</td>
      <td class="${p.posSide === "long" ? "up" : "down"}">${p.posSide === "long" ? "多" : "空"}</td>
      <td>${fmt(Math.abs(parseFloat(p.pos)), 0)} 张</td>
      <td>${fmt(avg)}</td>
      <td class="${pctClass(upl)}">${fmt(upl)}</td>
    </tr>`;
  }).join("");
}

/* ---------- alerts ---------- */
function renderAlerts() {
  const ul = $("#alerts");
  ul.innerHTML = state.alerts.map(a =>
    `<li class="${a.severity}">[${new Date(a.ts * 1000).toLocaleTimeString("zh-CN")}] ${a.message}</li>`
  ).join("") || `<li class="muted">尚无异动</li>`;
}

/* ---------- realtime ---------- */
function onWsMessage(msg) {
  const { topic, data } = msg;
  if (topic === "ticker") {
    state.tickers[data.instId] = data; renderWatchlist();
  } else if (topic === "funding") {
    state.funding[data.instId] = parseFloat(data.fundingRate) * 100; renderWatchlist();
  } else if (topic === "candle") {
    if (msg.bar === state.chartBar && data.instId === state.chartInst) pushCandle(data);
  } else if (topic === "balance") {
    state.balance = data; renderAccount();
  } else if (topic === "positions") {
    state.positions = data; renderAccount();
  } else if (topic === "alert") {
    state.alerts = [data, ...state.alerts]; renderAlerts();
  }
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { $("#ws-status").textContent = "● LIVE"; $("#ws-status").className = "badge ok"; };
  ws.onclose = () => {
    $("#ws-status").textContent = "● OFFLINE"; $("#ws-status").className = "badge down";
    setTimeout(connectWs, 2000);
  };
  ws.onmessage = (e) => { try { onWsMessage(JSON.parse(e.data)); } catch { /* ignore */ } };
}

/* ---------- boot ---------- */
async function boot() {
  const s = await fetch("/api/state").then(r => r.json());
  state.tickers = s.tickers; state.funding = s.funding; state.balance = s.balance;
  state.positions = s.positions; state.alerts = s.alerts; state.private = s.private;
  state.watchlist = s.watchlist; state.defaultChartInst = s.watchlist[0];
  state.timeframes = s.timeframes;
  $("#mode-badge").textContent = s.mode.toUpperCase();
  $("#mode-badge").className = "badge " + (s.mode === "live" ? "live" : "demo");
  renderWatchlist(); renderAccount(); renderAlerts();
  initChart();
  connectWs();
}

boot();
