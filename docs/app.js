// Fib-Leg Scanner dashboard — vanilla JS, no build step.
const $ = (s) => document.querySelector(s);

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
  el.onclick = () => showChart(w.symbol);
  return el;
}

let tvLoaded = false;
function showChart(symbol) {
  $("#chart-section").hidden = false;
  $("#chart-symbol").textContent = symbol;
  const mount = $("#tv-chart");
  mount.innerHTML = "";
  const widget = document.createElement("div");
  widget.className = "tradingview-widget-container";
  widget.style.height = "100%";
  mount.appendChild(widget);
  const inner = document.createElement("div");
  inner.style.height = "100%";
  widget.appendChild(inner);
  const s = document.createElement("script");
  s.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
  s.async = true;
  s.innerHTML = JSON.stringify({
    symbol: tvSymbol(symbol), interval: "60", theme: "dark", style: "1",
    locale: "en", autosize: true, hide_side_toolbar: false, allow_symbol_change: true,
  });
  widget.appendChild(s);
  $("#chart-section").scrollIntoView({ behavior: "smooth" });
}

function recentRow(r) {
  const el = document.createElement("div");
  el.className = "row";
  el.innerHTML = `
    <span class="sym">${r.symbol} <span class="badge ${r.side}">${r.side}</span></span>
    <span class="num">@${r.entry}</span>
    <span class="num">sl ${r.sl}</span>
    <span class="when">${fmtAge(r.ts)}</span>`;
  el.onclick = () => showChart(r.symbol);
  el.style.cursor = "pointer";
  return el;
}

async function load() {
  try {
    const res = await fetch("signals.json?t=" + Date.now());
    const d = await res.json();
    $("#meta").textContent =
      `source: ${d.source} · updated ${fmtAge(d.generated_at)} · ${d.symbols.length} symbols`;

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
