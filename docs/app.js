// Fib-Leg Scanner dashboard — vanilla JS, no build step.
const $ = (s) => document.querySelector(s);
let CHARTS = {};        // symbol -> [{time,open,high,low,close}]
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

function showChart(symbol, setup) {
  const bars = CHARTS[symbol];
  $("#chart-section").hidden = false;
  $("#chart-symbol").textContent = symbol;
  $("#tv-link").href = "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent(tvSymbol(symbol));
  const mount = $("#chart");
  mount.innerHTML = "";
  if (chartObj) { chartObj.remove(); chartObj = null; }

  if (!bars || !bars.length || typeof LightweightCharts === "undefined") {
    mount.innerHTML = '<p class="empty">No chart data for ' + symbol + ".</p>";
    $("#legend").innerHTML = "";
    return;
  }

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

  const LS = LightweightCharts.LineStyle;
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
  $("#chart-section").scrollIntoView({ behavior: "smooth" });
}

function recentRow(r) {
  const el = document.createElement("div");
  el.className = "row";
  el.style.cursor = "pointer";
  el.innerHTML = `
    <span class="sym">${r.symbol} <span class="badge ${r.side}">${r.side}</span></span>
    <span class="num">@${r.entry}</span>
    <span class="num">sl ${r.sl}</span>
    <span class="when">${fmtAge(r.ts)}</span>`;
  el.onclick = () => showChart(r.symbol, r);
  return el;
}

async function load() {
  try {
    const res = await fetch("signals.json?t=" + Date.now());
    const d = await res.json();
    CHARTS = d.charts || {};
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

    const rc = $("#recent");
    rc.innerHTML = "";
    if (!d.recent.length) rc.innerHTML = '<p class="empty">No recent signals.</p>';
    d.recent.forEach((r) => rc.appendChild(recentRow(r)));
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
