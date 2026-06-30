// Fib-Leg Scanner dashboard — vanilla JS, no build step.
const $ = (s) => document.querySelector(s);
let CHARTS = {};        // symbol -> [{time,open,high,low,close}]
let PIVOTS = {};        // symbol -> [{time,value}] zigzag pivots
let chartObj = null;

function tvSymbol(sym) {
  const idx = { "^NSEI": "NSE:NIFTY", "^NSEBANK": "NSE:BANKNIFTY" };
  if (idx[sym]) return idx[sym];
  if (sym.endsWith(".NS")) return "NSE:" + sym.slice(0, -3);
  return sym;
}

function fmtAge(iso) {
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return Math.floor(d / 60) + "m ago";
  if (d < 86400) return Math.floor(d / 3600) + "h ago";
  return Math.floor(d / 86400) + "d ago";
}

// NSE hours: Mon–Fri 09:15–15:30 IST
function marketStatus() {
  const ist = new Date(Date.now() + (5.5 * 60 - new Date().getTimezoneOffset()) * 60000);
  const day = ist.getUTCDay();          // ist shifted into UTC fields
  const mins = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  const open = day >= 1 && day <= 5 && mins >= 555 && mins <= 930;
  return open
    ? { open: true, text: "● Market open" }
    : { open: false, text: "○ Market closed · showing last scan" };
}

function setupCard(w) {
  const el = document.createElement("div");
  el.className = "card";
  el.innerHTML = `
    <div class="top">
      <span class="sym">${w.symbol}</span>
      <span class="badge ${w.side}">${w.side}</span>
    </div>
    <div class="state">${w.state.replace(/_/g, " ")} · leg ${w.leg.start} → ${w.leg.end}</div>
    <div class="levels">
      <span class="k">Entry</span><span class="v entry">${w.entry}</span>
      <span class="k">Stop</span><span class="v sl">${w.sl}</span>
      <span class="k">Targets</span><span class="v">${w.targets.join(" / ")}</span>
    </div>`;
  el.onclick = () => showChart(w.symbol, w);
  return el;
}

function priceLine(series, price, color, style, title) {
  series.createPriceLine({ price, color, lineWidth: 2, lineStyle: style, title });
}

// resample 1H bars into N-hour candles (factor = hours per candle)
function resample(bars, factor) {
  if (factor <= 1) return bars;
  const out = [];
  for (let i = 0; i < bars.length; i += factor) {
    const g = bars.slice(i, i + factor);
    if (!g.length) break;
    out.push({
      time: g[0].time, open: g[0].open,
      high: Math.max(...g.map((b) => b.high)),
      low: Math.min(...g.map((b) => b.low)),
      close: g[g.length - 1].close,
    });
  }
  return out;
}

let curSymbol = null, curSetup = null, curTF = 60;

function showChart(symbol, setup) {
  curSymbol = symbol; curSetup = setup; curTF = 60;
  $("#chart-section").hidden = false;
  $("#chart-symbol").textContent = symbol;
  $("#tv-link").href = "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent(tvSymbol(symbol));
  document.querySelectorAll("#tf-select .tf").forEach((b) =>
    b.classList.toggle("active", +b.dataset.tf === curTF));
  renderChart();
  $("#chart-section").scrollIntoView({ behavior: "smooth" });
}

function renderChart() {
  const base = CHARTS[curSymbol] || [];
  const mount = $("#chart");
  mount.innerHTML = "";
  if (chartObj) { chartObj.remove(); chartObj = null; }
  if (!base.length || typeof LightweightCharts === "undefined") {
    mount.innerHTML = '<p class="empty">No chart data for ' + curSymbol + ".</p>";
    $("#legend").innerHTML = "";
    return;
  }
  const bars = resample(base, curTF / 60);

  chartObj = LightweightCharts.createChart(mount, {
    autoSize: true,
    layout: { background: { color: "#131c2e" }, textColor: "#e6edf6" },
    grid: { vertLines: { color: "#1b2740" }, horzLines: { color: "#1b2740" } },
    timeScale: { timeVisible: true, borderColor: "#243150" },
    rightPriceScale: { borderColor: "#243150" },
    crosshair: { mode: 0 },
  });
  const series = chartObj.addCandlestickSeries({
    upColor: "#2ec27e", downColor: "#f0556d",
    wickUpColor: "#2ec27e", wickDownColor: "#f0556d", borderVisible: false,
  });
  series.setData(bars);

  // zigzag swing line — drawn on the 1H base (pivot times align there)
  const zz = PIVOTS[curSymbol];
  if (curTF === 60 && zz && zz.length > 1) {
    const zline = chartObj.addLineSeries({
      color: "#ffb454", lineWidth: 2, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false,
    });
    zline.setData(zz);
  }

  const LS = LightweightCharts.LineStyle;
  const setup = curSetup;
  if (setup) {
    if (setup.leg) {
      priceLine(series, setup.leg.start, "#8aa0c0", LS.Dotted, "leg 0.0");
      priceLine(series, setup.leg.end, "#8aa0c0", LS.Dotted, "leg 1.0");
    }
    priceLine(series, setup.entry, "#4c8dff", LS.Solid, "0.5 entry " + setup.entry);
    priceLine(series, setup.sl, "#f0556d", LS.Dashed, "0.618 SL " + setup.sl);
    (setup.targets || []).forEach((t, i) =>
      priceLine(series, t, "#2ec27e", LS.Dashed, "T" + (i + 1) + " " + t));
    $("#legend").innerHTML =
      `<span class="lg entry">0.5 entry ${setup.entry}</span>` +
      `<span class="lg sl">0.618 SL ${setup.sl}</span>` +
      `<span class="lg tgt">targets ${(setup.targets || []).join(" / ")}</span>`;
  }
  chartObj.timeScale().fitContent();
}

document.querySelectorAll("#tf-select .tf").forEach((btn) => {
  btn.onclick = () => {
    curTF = +btn.dataset.tf;
    document.querySelectorAll("#tf-select .tf").forEach((b) =>
      b.classList.toggle("active", b === btn));
    if (curSymbol) renderChart();
  };
});

function historyRow(h) {
  const el = document.createElement("div");
  el.className = "row hist";
  el.style.cursor = "pointer";
  const sign = h.points > 0 ? "+" : "";
  const ptsClass = h.points > 0 ? "win" : h.points < 0 ? "loss" : "flat";
  const resultLabel = { target: "TARGET", stop: "STOP", flat: "FLAT" }[h.result] || "—";
  el.innerHTML = `
    <span class="sym">${h.symbol} <span class="badge ${h.side}">${h.side}</span></span>
    <span class="result ${h.result}">${resultLabel}</span>
    <span class="pts ${ptsClass}">${sign}${h.points} pts</span>
    <span class="num">${h.r >= 0 ? "+" : ""}${h.r}R</span>
    <span class="when">${h.ts ? fmtAge(h.ts) : ""}</span>`;
  el.onclick = () => showChart(h.symbol, { entry: h.entry, sl: h.sl });
  return el;
}

async function load() {
  try {
    const res = await fetch("signals.json?t=" + Date.now());
    const d = await res.json();
    CHARTS = d.charts || {};
    PIVOTS = d.pivots || {};
    $("#meta").textContent =
      `source: ${d.source} · updated ${fmtAge(d.generated_at)} · ${d.symbols.length} symbols`;
    const ms = marketStatus();
    const mk = $("#market");
    mk.textContent = ms.text;
    mk.className = "market " + (ms.open ? "open" : "closed");

    const wl = $("#watchlist");
    wl.innerHTML = "";
    $("#watch-count").textContent = d.watchlist.length;
    $("#watch-empty").hidden = d.watchlist.length > 0;
    d.watchlist.forEach((w) => wl.appendChild(setupCard(w)));

    const st = d.stats || {};
    const se = $("#stats");
    if (st.trades) {
      const cls = st.net_points >= 0 ? "win" : "loss";
      se.innerHTML = `<span class="${cls}">${st.net_points >= 0 ? "+" : ""}${st.net_points} pts</span>` +
        ` · ${Math.round((st.win_rate || 0) * 100)}% win · ${st.trades} trades`;
    } else { se.textContent = ""; }

    const hc = $("#history");
    hc.innerHTML = "";
    const hist = d.history || [];
    if (!hist.length) hc.innerHTML = '<p class="empty">No completed trades yet.</p>';
    hist.forEach((h) => hc.appendChild(historyRow(h)));
  } catch (e) {
    $("#meta").textContent = "could not load signals.json — run scan.py";
    console.error(e);
  }
}

$("#refresh").onclick = load;
load();
setInterval(load, 60000);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
