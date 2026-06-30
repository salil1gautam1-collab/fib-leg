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
      <span class="badges">
        <span class="badge ${w.side}">${w.side}</span>
        ${w.mw ? `<span class="mw on" title="${w.side === "long" ? "M (double-top)" : "W (double-bottom)"} confirmed at the impulse ${w.side === "long" ? "top" : "bottom"}">${w.side === "long" ? "M" : "W"}</span>` : ""}
        <span class="htf ${w.htf ? "ok" : "no"}" title="${w.htf ? "impulse confirmed on a higher timeframe (2H/3H/4H)" : "not confirmed on 2H/3H/4H — lower confidence"}">${w.htf ? "HTF ✓" : "1H only"}</span>
      </span>
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

let curSymbol = null, curSetup = null, curBaseSetup = null, curTF = 60;
let curSeries = null, curBars = [];
let adjustMode = 0, adjustStart = null;
let LEG_BY_SYM = {}, navSyms = [];
const overrides = JSON.parse(localStorage.getItem("legOverrides") || "{}");

// recompute the fib levels from a leg (same ratios as the backend)
function fibFromLeg(side, start, end) {
  const rng = Math.abs(end - start);
  const up = side === "long";
  const r = (x) => +(up ? end - x * rng : end + x * rng).toFixed(2);
  const ext = (t) => +(up ? start + t * rng : start - t * rng).toFixed(2);
  return { side, leg: { start: +start.toFixed(2), end: +end.toFixed(2) },
    entry: r(0.5), sl: r(0.618), targets: [1.0, 1.272, 1.618].map(ext) };
}

function applyOverride(symbol, setup) {
  const o = overrides[symbol];
  if (!o) return setup;
  return fibFromLeg(o.end >= o.start ? "long" : "short", o.start, o.end);
}

function showChart(symbol, setup) {
  curSymbol = symbol; curBaseSetup = setup;
  curSetup = applyOverride(symbol, setup);
  curTF = +(localStorage.getItem("chartTF") || 60);   // remember your timeframe
  adjustMode = 0;
  $("#chart-section").hidden = false;
  $("#adjust-panel").hidden = true;
  $("#chart-symbol").textContent = symbol + (overrides[symbol] ? " ✏️" : "");
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
  curSeries = series; curBars = bars;
  chartObj.subscribeClick(onChartClick);

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
    // leg 0.0 = impulse start. leg 1.0 (impulse end) == T1 (the 1.0 target),
    // so we draw ONE line for it labelled as both, not two stacked at the same price.
    if (setup.leg) {
      priceLine(series, setup.leg.start, "#8aa0c0", LS.Dotted, "leg 0.0  " + setup.leg.start);
    }
    priceLine(series, setup.entry, "#4c8dff", LS.Solid, "0.5 entry " + setup.entry);
    priceLine(series, setup.sl, "#f0556d", LS.Dashed, "0.618 SL " + setup.sl);
    (setup.targets || []).forEach((t, i) =>
      priceLine(series, t, "#2ec27e", LS.Dashed,
        (i === 0 ? "T1 · leg 1.0  " : "T" + (i + 1) + "  ") + t));
    $("#legend").innerHTML =
      `<span class="lg entry">0.5 entry ${setup.entry}</span>` +
      `<span class="lg sl">0.618 SL ${setup.sl}</span>` +
      `<span class="lg tgt">targets ${(setup.targets || []).join(" / ")}</span>`;
  }

  // history trade: focus the chart on WHEN it played out + mark entry & SL/target
  if (setup && setup.result && setup.entry_ts && setup.ts) {
    const eFrom = Math.floor(new Date(setup.entry_ts).getTime() / 1000);
    const eTo = Math.floor(new Date(setup.ts).getTime() / 1000);
    const pad = Math.max(86400 * 2, (eTo - eFrom) * 0.5);
    const snap = (t) => {
      let b = bars[0], best = Infinity;
      for (const x of bars) { const d = Math.abs(x.time - t); if (d < best) { best = d; b = x; } }
      return b.time;
    };
    const long = setup.side === "long";
    series.setMarkers([
      { time: snap(eFrom), position: long ? "belowBar" : "aboveBar", color: "#4c8dff",
        shape: long ? "arrowUp" : "arrowDown", text: "Entry " + setup.entry },
      { time: snap(eTo), position: setup.result === "target" ? "aboveBar" : "belowBar",
        color: setup.result === "target" ? "#2ec27e" : "#f0556d", shape: "circle",
        text: (setup.result === "target" ? "TARGET " : "STOP ") + (setup.points > 0 ? "+" : "") + setup.points + " pts" },
    ]);
    try { chartObj.timeScale().setVisibleRange({ from: eFrom - pad, to: eTo + pad }); }
    catch (e) { chartObj.timeScale().fitContent(); }
  } else {
    chartObj.timeScale().fitContent();
  }
}

document.querySelectorAll("#tf-select .tf").forEach((btn) => {
  btn.onclick = () => {
    curTF = +btn.dataset.tf;
    localStorage.setItem("chartTF", curTF);          // remember it across symbols
    document.querySelectorAll("#tf-select .tf").forEach((b) =>
      b.classList.toggle("active", b === btn));
    if (curSymbol) renderChart();
  };
});

// the EXACT price where you tapped (no snapping to candle high/low)
function tapPrice(y) {
  const p = curSeries.coordinateToPrice(y);
  return p == null ? null : +p.toFixed(2);
}

function applyLeg(start, end) {
  if (!(start > 0) || !(end > 0) || start === end) return;
  overrides[curSymbol] = { start, end };
  localStorage.setItem("legOverrides", JSON.stringify(overrides));
  curSetup = fibFromLeg(end >= start ? "long" : "short", start, end);
  $("#chart-symbol").textContent = curSymbol + " ✏️";
  renderChart();
}

// tapping the chart in edit mode fills the start, then the end, then applies
function onChartClick(param) {
  if (!adjustMode || !param.point) return;
  const price = tapPrice(param.point.y);
  if (price == null) return;
  if (adjustMode === 1) {
    $("#adj-start").value = price; adjustMode = 2;
  } else {
    $("#adj-end").value = price; adjustMode = 1;
    applyLeg(parseFloat($("#adj-start").value), price);
  }
}

$("#adjust-leg").onclick = () => {
  if (!curSymbol) return;
  const p = $("#adjust-panel");
  p.hidden = !p.hidden;
  if (!p.hidden) {
    $("#adj-start").value = curSetup.leg.start;
    $("#adj-end").value = curSetup.leg.end;
    adjustMode = 1;                       // taps now set start, then end
  } else {
    adjustMode = 0;
  }
};
$("#adj-apply").onclick = () =>
  applyLeg(parseFloat($("#adj-start").value), parseFloat($("#adj-end").value));
$("#adj-reset").onclick = () => {
  delete overrides[curSymbol];
  localStorage.setItem("legOverrides", JSON.stringify(overrides));
  curSetup = curBaseSetup;
  $("#chart-symbol").textContent = curSymbol;
  $("#adj-start").value = curSetup ? curSetup.leg.start : "";
  $("#adj-end").value = curSetup ? curSetup.leg.end : "";
  renderChart();
};
$("#prev-sym").onclick = () => navSym(-1);
$("#next-sym").onclick = () => navSym(1);
function navSym(dir) {
  if (!navSyms.length) return;
  let i = navSyms.indexOf(curSymbol);
  i = (i + dir + navSyms.length) % navSyms.length;
  showChart(navSyms[i], LEG_BY_SYM[navSyms[i]]);
}

function legRow(w) {
  const el = document.createElement("div");
  el.className = "row legrow";
  el.style.cursor = "pointer";
  const edited = overrides[w.symbol] ? '<span class="ovr">✏️</span>' : "";
  const mw = w.mw ? `<span class="mw on">${w.side === "long" ? "M" : "W"}</span>` : "";
  el.innerHTML = `
    <span class="sym">${w.symbol} <span class="badge ${w.side}">${w.side}</span>${edited}</span>
    <span class="num">${w.leg.start} → ${w.leg.end}</span>
    ${mw}
    <span class="htf ${w.htf ? "ok" : "no"}">${w.htf ? "HTF ✓" : "1H"}</span>`;
  el.onclick = () => showChart(w.symbol, w);
  return el;
}

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
  el.onclick = () => showChart(h.symbol, h);   // h carries leg/entry/sl/targets
  return el;
}

let DATA = null;
let detectTF = localStorage.getItem("detectTF") || "";
let mwOnly = localStorage.getItem("mwOnly") === "1";

// apply a saved manual override to a leg item (so the list reflects your edits)
function withOverride(w) {
  const o = overrides[w.symbol];
  if (!o) return w;
  return { ...w, ...fibFromLeg(o.end >= o.start ? "long" : "short", o.start, o.end), edited: true };
}

function renderSettings() {
  const tfs = (DATA && DATA.detect_tfs) || ["2", "3", "4"];
  const box = $("#detect-tf");
  box.innerHTML = "";
  tfs.forEach((f) => {
    const b = document.createElement("button");
    b.className = "tf" + (f === detectTF ? " active" : "");
    b.textContent = f + "H";
    b.onclick = () => {
      detectTF = f;
      localStorage.setItem("detectTF", f);
      renderSettings();
      render();
      if (curSymbol && LEG_BY_SYM[curSymbol]) showChart(curSymbol, LEG_BY_SYM[curSymbol]);
    };
    box.appendChild(b);
  });
}

function render() {
  if (!DATA) return;
  const tf = (DATA.byTF && (DATA.byTF[detectTF] || DATA.byTF[DATA.default_tf])) || {};
  CHARTS = DATA.charts || {};
  PIVOTS = tf.pivots || {};
  $("#meta").textContent =
    `source: ${DATA.source} · ${detectTF}H legs · updated ${fmtAge(DATA.generated_at)} · ${DATA.symbols.length} symbols`;
  const ms = marketStatus();
  const mk = $("#market");
  mk.textContent = ms.text;
  mk.className = "market " + (ms.open ? "open" : "closed");

  const wl = $("#watchlist");
  wl.innerHTML = "";
  let watch = (tf.watchlist || []).map(withOverride);
  if (mwOnly) watch = watch.filter((w) => w.mw);
  $("#watch-count").textContent = watch.length;
  $("#watch-empty").hidden = watch.length > 0;
  watch.forEach((w) => wl.appendChild(setupCard(w)));

  const st = tf.stats || {};
  const se = $("#stats");
  if (st.trades) {
    const cls = st.net_points >= 0 ? "win" : "loss";
    se.innerHTML = `<span class="${cls}">${st.net_points >= 0 ? "+" : ""}${st.net_points} pts</span>` +
      ` · ${Math.round((st.win_rate || 0) * 100)}% win · ${st.trades} trades`;
  } else { se.textContent = ""; }

  const hc = $("#history");
  hc.innerHTML = "";
  const hist = tf.history || [];
  if (!hist.length) hc.innerHTML = '<p class="empty">No completed trades yet.</p>';
  hist.forEach((h) => hc.appendChild(historyRow(h)));

  let all = (tf.all_legs || []).map(withOverride);
  if (mwOnly) all = all.filter((w) => w.mw);
  LEG_BY_SYM = {};
  all.forEach((w) => (LEG_BY_SYM[w.symbol] = w));
  watch.forEach((w) => { if (!LEG_BY_SYM[w.symbol]) LEG_BY_SYM[w.symbol] = withOverride(w); });
  navSyms = all.map((w) => w.symbol);
  $("#all-count").textContent = all.length;
  const ac = $("#all-legs");
  ac.innerHTML = "";
  if (!all.length) ac.innerHTML = '<p class="empty">No legs yet.</p>';
  all.forEach((w) => ac.appendChild(legRow(w)));
}

async function load() {
  try {
    const res = await fetch("signals.json?t=" + Date.now());
    DATA = await res.json();
    if (!detectTF) detectTF = DATA.default_tf || "4";
    renderSettings();
    render();
  } catch (e) {
    $("#meta").textContent = "could not load signals.json — run scan.py";
    console.error(e);
  }
}

$("#refresh").onclick = load;
$("#settings-btn").onclick = () => { const s = $("#settings"); s.hidden = !s.hidden; };
$("#mw-only").checked = mwOnly;
$("#mw-only").onchange = (e) => {
  mwOnly = e.target.checked;
  localStorage.setItem("mwOnly", mwOnly ? "1" : "0");
  render();
};
$("#export-corr").onclick = async () => {
  const n = Object.keys(overrides).length;
  if (!n) { $("#corr-status").textContent = "No corrections yet — edit a leg with ✏️ first."; return; }
  const json = JSON.stringify(overrides);
  try {
    await navigator.clipboard.writeText(json);
    $("#corr-status").textContent = `✓ Copied ${n} correction(s) to clipboard — paste them to your assistant.`;
  } catch {
    $("#corr-status").textContent = json;   // fallback: select-and-copy this text
  }
};
$("#clear-corr").onclick = () => {
  for (const k in overrides) delete overrides[k];
  localStorage.setItem("legOverrides", "{}");
  $("#corr-status").textContent = "Cleared all corrections.";
  render();
};
load();
setInterval(load, 60000);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
