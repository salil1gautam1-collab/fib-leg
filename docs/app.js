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
  // epoch + 5.5h read via UTC fields = IST wall clock, correct on ANY device timezone
  const ist = new Date(Date.now() + 5.5 * 3600000);
  const day = ist.getUTCDay();
  const mins = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  if (day >= 1 && day <= 5 && mins >= 540 && mins < 555)
    return { open: false, text: "◐ Pre-open (09:00–09:15 IST)" };
  const open = day >= 1 && day <= 5 && mins >= 555 && mins <= 930;
  if (!open) return { open: false, text: "○ Market closed · showing last scan" };
  // Clock says open — but is NSE actually trading? Holidays and surprise halts
  // leave the data STALE (the scanner pulls fresh bars every 15 min). Give the
  // first hour some slack for the feed's delay + the first scans of the day.
  if (DATA && DATA.last_bar_epoch) {
    const ageMin = (Date.now() / 1000 - DATA.last_bar_epoch) / 60;
    if (ageMin > 75 && mins > 555 + 60)
      return { open: false, text: "⛱ Session hours, but no fresh data — NSE holiday or halt" };
    if (ageMin > 75)
      return { open: false, text: "◔ Session hours · waiting for the first scan…" };
  }
  return { open: true, text: "● Market open" };
}

function setupCard(w) {
  const el = document.createElement("div");
  el.className = "card";
  el.innerHTML = `
    <div class="top">
      <span class="sym">${w.symbol}</span>
      <span class="badges">
        <span class="badge ${w.side}">${w.side}</span>
        ${w.mw ? `<span class="mw on" title="${w.side === "long" ? "W (double-bottom) at the leg start — a downtrend ended and this up-impulse began" : "M (double-top) at the leg start — an uptrend ended and this down-impulse began"}">${w.side === "long" ? "W" : "M"}</span>` : ""}
        ${w.ew ? `<span class="ew on" title="Elliott Wave: the impulse subdivides into a clean 5-wave structure">EW</span>` : ""}
        ${w.conf ? `<span class="conf on" title="A+ confluence: a broken prior mountain/valley sits in the 0.5–0.618 entry zone (old resistance→support)">A+</span>` : ""}
        ${w.ctx && w.ctx.pass ? `<span class="conf on" title="⭐ Best (context-pass): VIX calm · sector aligned · no market whipsaw · projected reward-to-risk ${w.ctx.rr ?? "n/a"}">⭐${w.ctx.rr ? " R:R " + w.ctx.rr : ""}</span>` : ""}
        <span class="htf ${w.htf ? "ok" : "no"}" title="${w.htf ? `impulse also a same-direction swing on a higher timeframe (${htfList()})` : `not confirmed on a higher timeframe (${htfList()}) — lower confidence`}">${w.htf ? "HTF ✓" : `${tfLabel(detectTF)} only`}</span>
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

// shade the S/R zone (lo..hi) across the chart via a baseline series — the fill
// between the baseline (lo) and the flat line (hi) is the band.
function zoneBand(lo, hi) {
  if (!chartObj || !curBars || !curBars.length) return;
  const bs = chartObj.addBaselineSeries({
    baseValue: { type: "price", price: lo },
    topFillColor1: "rgba(185,138,255,0.16)", topFillColor2: "rgba(185,138,255,0.16)",
    topLineColor: "rgba(0,0,0,0)", bottomLineColor: "rgba(0,0,0,0)",
    bottomFillColor1: "rgba(0,0,0,0)", bottomFillColor2: "rgba(0,0,0,0)",
    lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
  });
  bs.setData(curBars.map((b) => ({ time: b.time, value: hi })));
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
let ALL_LEGS_RAW = {};   // symbol -> current leg (UNFILTERED), for chart viewing on any TF
const overrides = JSON.parse(localStorage.getItem("legOverrides") || "{}");

// recompute the fib levels from a leg (same ratios as the backend): entry at the
// chosen level, STOP at 0.786 (a 15m close beyond it triggers it), targets per the
// chosen exit style (full = leg top only; partial = 1.0 / 1.272 / 1.618).
function fibFromLeg(side, start, end) {
  const rng = Math.abs(end - start);
  const up = side === "long";
  const r = (x) => +(up ? end - x * rng : end + x * rng).toFixed(2);
  const ext = (t) => +(up ? start + t * rng : start - t * rng).toFixed(2);
  const er = parseFloat(entryRatio) || 0.5;
  const sr = parseFloat(slRatio) || 0.786;
  const tgts = exitStyle === "full" ? [1.0] : [1.0, 1.272, 1.618];
  return { side, leg: { start: +start.toFixed(2), end: +end.toFixed(2) },
    entry: r(er), sl: r(sr), targets: tgts.map(ext) };
}

function applyOverride(symbol, setup) {
  if (!setup || setup.result) return setup;   // a HISTORY trade keeps its own leg
  const o = overrides[symbol];
  if (!o) return setup;
  return fibFromLeg(o.end >= o.start ? "long" : "short", o.start, o.end);
}

function showChart(symbol, setup) {
  curSymbol = symbol; curBaseSetup = setup;
  curSetup = applyOverride(symbol, setup);
  adjustMode = 0;
  $("#chart-section").hidden = false;
  if (typeof chartCollapsed !== "undefined" && chartCollapsed) setChartCollapsed(false);
  $("#adjust-panel").hidden = true;
  $("#chart-symbol").textContent = symbol + (overrides[symbol] && !setup.result ? " ✏️" : "");
  $("#tv-link").href = "https://www.tradingview.com/chart/?symbol=" + encodeURIComponent(tvSymbol(symbol));
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
  const bars = base;                  // already at the chosen TF (no client resample)

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

  // zigzag swing line — charts are at the detection TF so pivots always align
  const zz = PIVOTS[curSymbol];
  if (zz && zz.length > 1) {
    const zline = chartObj.addLineSeries({
      color: "#ffb454", lineWidth: 2, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false,
    });
    zline.setData(zz);
  }

  const LS = LightweightCharts.LineStyle;
  const setup = curSetup;
  // leg START time — fib levels anchor here (never extend left of the leg start)
  const startTs = setup && setup.leg && setup.leg.start_ts
    ? Math.floor(new Date(setup.leg.start_ts).getTime() / 1000) : null;
  const tEnd = bars.length ? bars[bars.length - 1].time : null;
  const barSec = bars.length > 1 ? (bars[1].time - bars[0].time) : 3600;
  if (setup) {
    // fib levels anchored at the leg's START candle (don't extend left of it).
    // leg 1.0 (impulse end) == T1, so one line labelled as both.
    const snapT = (t) => {
      let bt = bars[0].time, best = Infinity;
      for (const x of bars) { const d = Math.abs(x.time - t); if (d < best) { best = d; bt = x.time; } }
      return bt;
    };
    const lvl = (price, color, style, label) => {
      if (startTs == null || tEnd == null) { priceLine(series, price, color, style, label); return; }
      const ls = chartObj.addLineSeries({ color, lineWidth: style === LS.Solid ? 2 : 1,
        lineStyle: style, priceLineVisible: false, lastValueVisible: true,
        crosshairMarkerVisible: false, title: label });
      ls.setData([{ time: snapT(Math.max(startTs, bars[0].time)), value: price },
                  { time: tEnd, value: price }]);
    };
    // fib convention (TradeWisely): the leg START (origin) = 1.0, the leg END (the
    // impulse extreme, the "level 0" you drag) = 0.0 — for BOTH directions. So a
    // LONG reads 1.0 at the bottom (start) → 0.0 at the top (end); a SHORT reads
    // 1.0 at the top (start) → 0.0 at the bottom (end). This matches the level
    // maths: entry/SL are measured from the END, so END is the 0.0 reference.
    if (setup.leg) lvl(setup.leg.start, "#8aa0c0", LS.Dotted, "leg 1.0");
    // The entry zone — shaded band so you can eyeball price reacting inside it.
    // A+: the broken mountain/valley ± zone width. No mountain: the plain 0.5–0.618
    // fib band (labeled so you know the confluence edge is absent).
    let noMtn = false;
    if (setup.conf_mtn != null && setup.conf_zone_lo != null && setup.conf_zone_hi != null) {
      zoneBand(setup.conf_zone_lo, setup.conf_zone_hi);
      lvl(setup.conf_zone_hi, "#b98aff", LS.Dashed, "zone ↑");
      lvl(setup.conf_mtn, "#b98aff", LS.Solid, "S/R mountain");
      lvl(setup.conf_zone_lo, "#b98aff", LS.Dashed, "zone ↓");
    } else if (DATA && DATA.zone_entry && setup.leg && setup.entry != null) {
      // plain fib 0.5–0.618 band (no mountain on the left)
      noMtn = true;
      const r618 = setup.leg.end + 0.618 * (setup.leg.start - setup.leg.end);
      const zlo = Math.min(setup.entry, r618), zhi = Math.max(setup.entry, r618);
      zoneBand(zlo, zhi);
      lvl(zhi, "#8a94a8", LS.Dashed, "0.5–0.618 zone");
      lvl(zlo, "#8a94a8", LS.Dashed, "no mountain");
    }
    lvl(setup.entry, "#4c8dff", LS.Solid, "zone entry");
    lvl(setup.sl, "#f0556d", LS.Dashed, "0.786 SL");
    (setup.targets || []).forEach((t, i) =>
      lvl(t, "#2ec27e", LS.Dashed, i === 0 ? "T1 · leg 0.0" : "T" + (i + 1)));
    $("#legend").innerHTML =
      (setup.conf_mtn != null ? `<span class="lg zone">S/R zone ${setup.conf_zone_lo}–${setup.conf_zone_hi}</span>` :
       noMtn ? `<span class="lg nomtn">⚠ no mountain/valley — plain fib zone</span>` : "") +
      `<span class="lg entry">zone entry ${setup.entry}</span>` +
      `<span class="lg sl">0.786 SL ${setup.sl}</span>` +
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
  } else if (startTs != null && tEnd != null) {
    // live / validate leg: frame the impulse + the retracement after it, so the
    // fib lines fill the view (like the history chart) instead of the leg sitting
    // mid-chart with a long run of bars — and lines — trailing off to the left.
    try {
      chartObj.timeScale().setVisibleRange({ from: startTs - barSec * 8, to: tEnd + barSec * 4 });
    } catch (e) { chartObj.timeScale().fitContent(); }
  } else {
    chartObj.timeScale().fitContent();
  }
}


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
  const mw = w.mw ? `<span class="mw on" title="${w.side === "long" ? "W reversal at the leg start" : "M reversal at the leg start"}">${w.side === "long" ? "W" : "M"}</span>` : "";
  const ew = w.ew ? `<span class="ew on" title="Elliott 5-wave structure">EW</span>` : "";
  const conf = w.conf ? `<span class="conf on" title="A+ confluence: broken mountain in the 0.5–0.618 zone">A+</span>` : "";
  const star = w.ctx && w.ctx.pass ? `<span class="conf on" title="⭐ Best: VIX calm · sector aligned · no whipsaw · R:R ${w.ctx.rr ?? "n/a"}">⭐${w.ctx.rr ? " " + w.ctx.rr : ""}</span>` : "";
  el.innerHTML = `
    <span class="sym">${w.symbol} <span class="badge ${w.side}">${w.side}</span>${edited}</span>
    <span class="num">${w.leg.start} → ${w.leg.end}</span>
    ${mw}${ew}${conf}${star}
    <span class="htf ${w.htf ? "ok" : "no"}" title="${w.htf ? `confirmed on a higher TF (${htfList()})` : `not confirmed on ${htfList()}`}">${w.htf ? "HTF ✓" : `${tfLabel(detectTF)} only`}</span>`;
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

// Settings that follow the validated defaults. Bump SETTINGS_VER when the defaults
// change so returning users get reset to them ONCE (their leg corrections are kept).
const SETTINGS_KEYS = ["detectTF", "legMethod", "entryRatio", "exitStyle", "trigTf",
                       "slRatio", "reversalMode", "showIndices", "chartTF", "confOnly", "mwOnly"];
const SETTINGS_VER = "2026-07-03";   // 2H · ⭐ Best (context gates) · lock-B · 0.618 legs
if (localStorage.getItem("settingsVer") !== SETTINGS_VER) {
  SETTINGS_KEYS.forEach((k) => localStorage.removeItem(k));   // keep legOverrides
  localStorage.setItem("settingsVer", SETTINGS_VER);
}

let DATA = null;
let detectTF = localStorage.getItem("detectTF") || "";
let method = localStorage.getItem("legMethod") || "";
let entryRatio = localStorage.getItem("entryRatio") || "";   // "0.5" | "0.618"
let exitStyle = localStorage.getItem("exitStyle") || "";     // "full" | "partial"
let trigTf = localStorage.getItem("trigTf") || "";           // "5" | "15" (trigger-TF minutes)
let slRatio = localStorage.getItem("slRatio") || "";         // "0.618" | "0.786" (stop level)
// ONE setup filter (mutually exclusive) — "all" | "aplus" | "mw" | "pin".
// migrate the old confOnly/mwOnly checkboxes to the new single mode.
let reversalMode = localStorage.getItem("reversalMode") ||
  (localStorage.getItem("confOnly") === "1" ? "aplus"
    : localStorage.getItem("mwOnly") === "1" ? "mw" : "best");
let confOnly = reversalMode === "aplus";   // A+ full-edge (confluence + nested + zone + M/W|pin)
let mwOnly = reversalMode === "mw";        // only M/W reversal
let mwTrend = reversalMode === "mwtrend";  // M/W + higher-TF trend
let bestOnly = reversalMode === "best";    // ⭐ context-pass: calm VIX + sector-aligned + no whipsaw + R:R>=1 (DEFAULT)
let pinOnly = reversalMode === "pin";      // only pin-bar reversal
const REVERSAL_LABELS = { best: "⭐ Best", mwtrend: "Reversal + trend", all: "All", aplus: "A+", mw: "Only M/W", pin: "Only Pin" };
let showIndices = localStorage.getItem("showIndices") === "1";   // default off = stocks only

const isIndex = (sym) => typeof sym === "string" && sym.startsWith("^");
const execKey = () => [entryRatio, exitStyle, trigTf, slRatio].join("|");
const exitLabel = (x) => x === "full" ? "Square all at T1"
  : x === "lockb" ? "Let it run + lock B" : "Let it run + BE";
const trigLabel = (t) => t + "m close";
const slLabel = (s) => s + " SL";

const METHOD_LABELS = { adaptive: "Adaptive", book: "Book 0.236", book382: "Book 0.382" };
function methodLabel(k) { return METHOD_LABELS[k] || k; }

// apply a saved manual override to a leg item (so the list reflects your edits)
function withOverride(w) {
  const o = overrides[w.symbol];
  if (!o) return w;
  return { ...w, ...fibFromLeg(o.end >= o.start ? "long" : "short", o.start, o.end), edited: true };
}

// in A+ mode the entry/SL come from the confluence (0.5-0.618 zone, 0.786 stop),
// not the toggles — swap them in for display.
function applyConf(w) {
  if (!confOnly || w.conf_entry == null) return w;
  return { ...w, entry: w.conf_entry, sl: w.conf_sl };
}

function tfLabel(m) { m = +m; return m < 60 ? m + "m" : (m / 60) + "H"; }

// the higher timeframes the HTF check uses = 2x/3x/4x the SELECTED detection TF
function htfList() { const b = +detectTF || 240; return [2, 3, 4].map((f) => tfLabel(b * f)).join(" / "); }

// any settings change re-renders the lists/chart from the loaded scan. The scan
// holds every TF/method/exec/trigger combo, so switching is instant and always
// reflects the current settings (no stale filter). The DATA itself is refreshed on
// page load and the ⟳ button (payload is a few MB — too big to re-pull per toggle).
function applySettings() {
  renderTFButtons();
  renderMethodButtons();
  renderExecButtons();
  render();
  // always refresh the open chart on any settings/TF change — use the UNFILTERED current
  // leg for the symbol (so switching TF redraws the leg even if the filter would hide it).
  if (curSymbol) showChart(curSymbol, ALL_LEGS_RAW[curSymbol] || LEG_BY_SYM[curSymbol] || curBaseSetup);
}

function setTF(tf) {
  detectTF = String(tf);
  localStorage.setItem("detectTF", detectTF);
  applySettings();
}

// the SAME timeframe buttons in Settings and on the chart both drive detectTF
function renderTFButtons() {
  const tfs = (DATA && DATA.detect_tfs) || ["45", "60", "120", "180", "240"];
  ["#detect-tf", "#tf-select"].forEach((id) => {
    const box = $(id);
    if (!box) return;
    box.innerHTML = "";
    tfs.forEach((tf) => {
      const b = document.createElement("button");
      b.className = "tf" + (String(tf) === detectTF ? " active" : "");
      b.textContent = tfLabel(tf);
      b.onclick = () => setTF(tf);
      box.appendChild(b);
    });
  });
}

function setMethod(mth) {
  method = String(mth);
  localStorage.setItem("legMethod", method);
  applySettings();
}

function setEntry(r) {
  entryRatio = String(r);
  localStorage.setItem("entryRatio", entryRatio);
  applySettings();
}

function setExit(x) {
  exitStyle = String(x);
  localStorage.setItem("exitStyle", exitStyle);
  applySettings();
}

function setTrig(t) {
  trigTf = String(t);
  localStorage.setItem("trigTf", trigTf);
  applySettings();
}

function setSl(s) {
  slRatio = String(s);
  localStorage.setItem("slRatio", slRatio);
  applySettings();
}

// execution chooser (Settings): entry x exit x trigger x stop level, from DATA.execs
function renderExecButtons() {
  const execs = (DATA && DATA.execs) || ["0.5|full|5|0.786"];
  // .filter(Boolean) so a stale feed (missing a dimension) never yields an
  // "undefined" button while the fresh scan is still propagating.
  const col = (i) => [...new Set(execs.map((e) => e.split("|")[i]).filter(Boolean))];
  const group = (box, opts, cur, label, set) => {
    if (!box) return;
    box.innerHTML = "";
    opts.forEach((o) => {
      const b = document.createElement("button");
      b.className = "tf" + (o === cur ? " active" : "");
      b.textContent = label(o);
      b.onclick = () => set(o);
      box.appendChild(b);
    });
  };
  // Entry/stop are ALWAYS the zone now — never manual. In zone mode the exit options
  // come from the CONF backtest (full / partial-BE / lock-at-B), not the byExec list.
  const zoneAlways = !!(DATA && DATA.zone_entry);
  const confExits = [...new Set(((DATA && DATA.conf_execs) || []).map((e) => e.split("|")[0]).filter(Boolean))];
  group($("#entry-ratio"), col(0), entryRatio, (r) => r, setEntry);
  group($("#exit-style"), (zoneAlways && confExits.length ? confExits : col(1)), exitStyle, exitLabel, setExit);
  group($("#trigger-tf"), col(2), trigTf, trigLabel, setTrig);
  group($("#sl-ratio"), col(3), slRatio, slLabel, setSl);
  $("#entry-ratio") && $("#entry-ratio").classList.toggle("disabled", zoneAlways);
  $("#sl-ratio") && $("#sl-ratio").classList.toggle("disabled", zoneAlways);
  // zone mode fixes entry (0.5–0.618 zone) and stop (0.786) — hide the dead selectors
  // entirely instead of showing permanently-grayed rows.
  ["#entry-ratio", "#sl-ratio"].forEach((id) => {
    const row = $(id) && $(id).closest(".set-row");
    if (row) row.hidden = zoneAlways;
  });
}

// leg-detection method chooser (Settings) — A/B the two ways of drawing the leg
function renderMethodButtons() {
  const box = $("#detect-method");
  if (!box) return;
  const methods = (DATA && DATA.methods) || ["adaptive", "book"];
  box.innerHTML = "";
  methods.forEach((mth) => {
    const b = document.createElement("button");
    b.className = "tf" + (mth === method ? " active" : "");
    b.textContent = methodLabel(mth);
    b.onclick = () => setMethod(mth);
    box.appendChild(b);
  });
}

function render() {
  if (!DATA) return;
  const tf = (DATA.byTF && (DATA.byTF[detectTF] || DATA.byTF[DATA.default_tf])) || {};
  CHARTS = tf.charts || {};          // charts + zigzag are per-TF, method-independent
  PIVOTS = tf.pivots || {};
  // Entry/stop are ALWAYS the zone now, so the zone-entry backtest (byConf, keyed by
  // exit|trigger) drives every mode; the setup filter (All/A+/M/W/Pin) is a client-side
  // flag filter over it. byExec is only a fallback for an old feed without byConf.
  const meth = (tf.byMethod && (tf.byMethod[method] || tf.byMethod[DATA.default_method])) || {};
  const usingConf = !!meth.byConf;
  const m = usingConf
    ? (meth.byConf[exitStyle + "|" + trigTf] || meth.byConf[DATA.default_conf] || {})
    : ((meth.byExec && (meth.byExec[execKey()] || meth.byExec[DATA.default_exec])) || {});
  const lvlLabel = usingConf ? "zone 0.5–0.618 · SL 0.786" : `entry ${entryRatio} · SL ${slRatio}`;
  $("#meta").textContent =
    `source: ${DATA.source} · ${tfLabel(detectTF)} · ${methodLabel(method)} · ${lvlLabel} · ${exitStyle} · ${trigTf}m · updated ${fmtAge(DATA.generated_at)}`;
  const ms = marketStatus();
  const mk = $("#market");
  const mcx = DATA && DATA.market_ctx;
  const regTxt = mcx ? { SDW: "sideways ✓", UPT: "uptrend ✓", DNT: "downtrend ✓", WHP: "whipsaw ⚠" }[mcx.regime] : null;
  mk.textContent = ms.text + (mcx ? ` · ${regTxt || "?"} · VIX ${mcx.vix_hi ? "high ⚠" : "calm ✓"}` : "");
  mk.className = "market " + (ms.open ? "open" : "closed");

  const wl = $("#watchlist");
  wl.innerHTML = "";
  let watch = (m.watchlist || []).map(withOverride);
  if (!showIndices) watch = watch.filter((w) => !isIndex(w.symbol));
  if (mwOnly) watch = watch.filter((w) => w.mw);
  if (mwTrend) watch = watch.filter((w) => (w.mw || w.pin) && w.htf);
  if (bestOnly) watch = watch.filter((w) => w.ctx && w.ctx.pass);
  if (pinOnly) watch = watch.filter((w) => w.pin);
  if (confOnly) watch = watch.filter((w) => w.conf);
  if (confOnly && !usingConf) watch = watch.map(applyConf);
  $("#watch-count").textContent = watch.length;
  $("#watch-empty").hidden = watch.length > 0;
  watch.forEach((w) => wl.appendChild(setupCard(w)));

  let hist = m.history || [];
  if (!showIndices) hist = hist.filter((h) => !isIndex(h.symbol));
  if (mwOnly) hist = hist.filter((h) => h.mw);   // history follows the same filter
  if (mwTrend) hist = hist.filter((h) => (h.mw || h.pin) && h.htf);
  if (bestOnly) hist = hist.filter((h) => h.ctx && h.ctx.pass);
  if (pinOnly) hist = hist.filter((h) => h.pin);
  if (confOnly) hist = hist.filter((h) => h.conf);

  const se = $("#stats");
  if (hist.length) {                             // stats recomputed from the shown trades.
    // aggregate in R (scale-free) — summing raw points across an index and a
    // stock is meaningless when they trade at wildly different price levels.
    const netR = Math.round(hist.reduce((s, h) => s + (h.r || 0), 0) * 100) / 100;
    const wins = hist.filter((h) => h.r > 0).length;
    const cls = netR >= 0 ? "win" : "loss";
    se.innerHTML = `<span class="${cls}">${netR >= 0 ? "+" : ""}${netR}R</span>` +
      ` · ${Math.round((wins / hist.length) * 100)}% win · ${hist.length} trades`;
  } else { se.textContent = ""; }

  const hc = $("#history");
  hc.innerHTML = "";
  if (!hist.length) {
    hc.innerHTML = reversalMode !== "all"
      ? `<p class="empty">No ${REVERSAL_LABELS[reversalMode]} trades at this timeframe.</p>`
      : '<p class="empty">No completed trades yet.</p>';
  }
  hist.forEach((h) => hc.appendChild(historyRow(h)));

  let all = (m.all_legs || []).map(withOverride);
  // UNFILTERED leg per symbol — so the chart can draw ANY symbol's current leg on ANY
  // TF regardless of the list filter (the setup filter must not cripple chart viewing).
  ALL_LEGS_RAW = {};
  all.forEach((w) => (ALL_LEGS_RAW[w.symbol] = w));
  if (!showIndices) all = all.filter((w) => !isIndex(w.symbol));
  if (mwOnly) all = all.filter((w) => w.mw);
  if (mwTrend) all = all.filter((w) => (w.mw || w.pin) && w.htf);
  if (bestOnly) all = all.filter((w) => w.ctx && w.ctx.pass);
  if (pinOnly) all = all.filter((w) => w.pin);
  if (confOnly) all = all.filter((w) => w.conf);
  if (confOnly && !usingConf) all = all.map(applyConf);
  LEG_BY_SYM = {};
  all.forEach((w) => (LEG_BY_SYM[w.symbol] = w));
  watch.forEach((w) => { if (!LEG_BY_SYM[w.symbol]) LEG_BY_SYM[w.symbol] = withOverride(w); });
  navSyms = all.map((w) => w.symbol);
  $("#all-count").textContent = all.length;
  const ac = $("#all-legs");
  ac.innerHTML = "";
  if (!all.length) ac.innerHTML = '<p class="empty">No legs yet.</p>';
  all.forEach((w) => ac.appendChild(legRow(w)));
  renderAgent(m);
  renderHistTabs();
  renderBacktest();
}

async function load() {
  try {
    const res = await fetch("signals.json?t=" + Date.now());
    DATA = await res.json();
    if (!BT)   // the 11-yr backtest is static — fetch once
      fetch("backtest.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
        .then((j) => { if (j) { BT = j; render(); } }).catch(() => {});
    // the persistent paper log grows with each scan — refresh it alongside signals
    fetch("paper_log.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) PL = j; }).catch(() => {});
    if (!detectTF || !(DATA.detect_tfs || []).includes(detectTF))
      detectTF = DATA.default_tf || "240";
    if (!method || !(DATA.methods || []).includes(method))
      method = DATA.default_method || "adaptive";
    // Validate execution. In ZONE mode the exit+trigger come from the CONF backtest
    // (full/partial/lockb × 5/15) and entry/SL are fixed at the zone — so validate the
    // pair against conf_execs, NOT the byExec list (which has no lock-at-B and was
    // clobbering a saved 'lockb' back to 'full' on every load). Non-zone: byExec.
    if (DATA.zone_entry) {
      const ce = DATA.conf_execs || ["lockb|5"];
      if (!localStorage.getItem("exitStyle") || !localStorage.getItem("trigTf")
          || !ce.includes(exitStyle + "|" + trigTf)) {
        const d = (DATA.default_conf || "lockb|5").split("|");
        exitStyle = d[0]; trigTf = d[1];
      }
      entryRatio = "0.5"; slRatio = "0.786";   // fixed in zone mode (selectors grayed)
    } else if (!(DATA.execs || []).includes(execKey())) {
      const def = (DATA.default_exec || "0.5|full|5|0.786").split("|");
      entryRatio = def[0] || "0.5"; exitStyle = def[1] || "full";
      trigTf = def[2] || "5"; slRatio = def[3] || "0.786";
    }
    renderTFButtons();
    renderMethodButtons();
    renderExecButtons();
    render();
  } catch (e) {
    $("#meta").textContent = "could not load signals.json — run scan.py";
    console.error(e);
  }
}

$("#refresh").onclick = () => {
  $("#meta").textContent = "refreshing…";        // visible feedback; load() rewrites it with the fresh timestamp
  load();
};
$("#settings-btn").onclick = () => { const s = $("#settings"); s.hidden = !s.hidden; };

// ONE mutually-exclusive setup filter: All / A+ / Only M/W / Only Pin
function setMode(m) {
  reversalMode = m;
  confOnly = m === "aplus"; mwOnly = m === "mw"; mwTrend = m === "mwtrend"; bestOnly = m === "best"; pinOnly = m === "pin";
  localStorage.setItem("reversalMode", m);
  localStorage.removeItem("confOnly"); localStorage.removeItem("mwOnly");  // retire old keys
  renderReversalButtons();
  applySettings();
}
function renderReversalButtons() {
  const box = $("#reversal-mode");
  if (!box) return;
  box.innerHTML = "";
  ["best", "mwtrend", "all", "aplus", "mw", "pin"].forEach((m) => {
    const b = document.createElement("button");
    b.className = "tf" + (m === reversalMode ? " active" : "");
    b.textContent = REVERSAL_LABELS[m];
    b.onclick = () => setMode(m);
    box.appendChild(b);
  });
}
renderReversalButtons();

$("#show-indices").checked = showIndices;
$("#show-indices").onchange = (e) => {
  showIndices = e.target.checked;
  localStorage.setItem("showIndices", showIndices ? "1" : "0");
  applySettings();
};
// show the running app version (read from this script's ?v=) + a hard cache-clear button
$("#app-ver").textContent =
  ((document.querySelector('script[src*="app.js"]') || {}).src || "").match(/v=(\d+)/)?.[1] || "?";
$("#clear-cache").onclick = async () => {
  const b = $("#clear-cache");
  b.textContent = "Updating…"; b.disabled = true;
  try {
    if ("serviceWorker" in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    }
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
    // also reset settings to the current validated defaults (keep leg corrections)
    SETTINGS_KEYS.forEach((k) => localStorage.removeItem(k));
    localStorage.removeItem("settingsVer");
  } catch (e) { console.error(e); }
  location.reload();
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
// ---------- 🤖 Paper agent — alerts + paper only; the OWNER holds start/pause/stop ----------
// Backtest map (2H · ⭐ Best · lock-B · Future+DOTM, 11yr walk-forward, net 0.15%):
const RISK_PLANS = {
  "0.5": { cagr: "~8%/yr", dd: "~-13%" },
  "1":   { cagr: "~15%/yr", dd: "~-24%" },
  "1.5": { cagr: "~22%/yr", dd: "~-35%" },
  "2":   { cagr: "~30%/yr", dd: "~-44%" },
};
let AG = JSON.parse(localStorage.getItem("agentState") || "null") ||
  { status: "stopped", capital: 0, risk: "1", startedAt: null, pausedAt: null, funds: [] };
// import an agent shared from another device via a 🔗 sync link (#agent=…). The state
// rides in the URL FRAGMENT, which browsers never send to the server — device-to-device only.
if (location.hash.startsWith("#agent=")) {
  try {
    const inc = JSON.parse(atob(decodeURIComponent(location.hash.slice(7))));
    if (inc && typeof inc === "object" && "status" in inc &&
        confirm(`Import agent from sync link?\n\ncapital ₹${(+inc.capital || 0).toLocaleString("en-IN")} · risk ${inc.risk}%/trade · started ${(inc.startedAt || "—").slice(0, 10)}\n\nThis replaces this device's agent.`)) {
      AG = inc;
      localStorage.setItem("agentState", JSON.stringify(AG));
    }
  } catch (e) { /* malformed link — ignore */ }
  history.replaceState(null, "", location.pathname + location.search);
}
// History sub-tabs + the precomputed full-history backtest (docs/backtest.json)
let histTab = localStorage.getItem("histTab") || "paper";
let BT = null;
let PL = null;   // persistent cloud paper log (docs/paper_log.json) — never rolls off
let btRange = "10", btBest = "best";   // "10" | "15" | "custom" years · ⭐/rev/All
let btTf = "120", btExit = "lockb";    // backtest combo — TF × exit (validated defaults)
function agSave() {
  AG._savedAt = new Date().toISOString();          // lets Drive sync pick the newer copy
  localStorage.setItem("agentState", JSON.stringify(AG));
  if (typeof gQueuePush === "function") gQueuePush();   // mirrors to Google Drive when connected
}
// PIN lock removed — clear any leftover encrypted state so a device that had set a
// PIN starts clean (settings only; the cloud trade log is untouched).
localStorage.removeItem("agentStateEnc");

function renderAgent(m) {
  const st = $("#agent-status");
  if (!st) return;
  st.textContent = AG.status === "running" ? "▶ running" : AG.status === "paused" ? "⏸ paused" : "⏹ stopped";
  st.className = "pill " + (AG.status === "running" ? "win" : "");
  const cap = $("#agent-capital");
  if (document.activeElement !== cap) cap.value = AG.capital || "";
  const rb = $("#agent-risk");
  rb.innerHTML = "";
  Object.keys(RISK_PLANS).forEach((r) => {
    const b = document.createElement("button");
    b.className = "tf" + (r === AG.risk ? " active" : "");
    b.textContent = r + "%";
    b.onclick = () => { AG.risk = r; agSave(); render(); };
    rb.appendChild(b);
  });
  const p = RISK_PLANS[AG.risk];
  const capV = AG.capital || 0;
  let plan = `<b>Plan:</b> risk ${AG.risk}% per trade → backtest ${p.cagr}, worst dip ${p.dd}. `;
  plan += "Changing risk mid-run applies to FUTURE trades only (consequence: higher risk = faster growth AND deeper dips). ";
  if (capV && capV < 800000) plan += "<b>⚠ Below ~₹8L:</b> too small to trade even one hedged lot safely — treat results as practice only. ";
  else if (capV && capV < 2500000) plan += "<b>⚠ Below ~₹25L:</b> a real account couldn't take every signal (one lot = one open trade) — live results would trail this paper curve. ";
  $("#agent-plan").innerHTML = plan;

  const rep = $("#agent-report");
  const mc = DATA && DATA.market_ctx;
  const mline = mc ? `Market now: ${mc.regime === "SDW" ? "sideways (good)" : mc.regime === "WHP" ? "whipsaw (agent stands aside)" : mc.regime || "?"} · VIX ${mc.vix_hi ? "elevated ⚠" : "calm ✓"}. ` : "";
  if (AG.status === "stopped" || !capV) {
    rep.innerHTML = mline + "Set capital, pick a risk plan, press ▶ Start. The agent paper-trades every ⭐ Best signal and reports here daily.";
    const pn0 = $("#paper-note");
    if (pn0) { pn0.textContent = "Agent stopped — press ▶ Start (with capital set) to begin the paper ledger."; $("#paper-rows").innerHTML = ""; }
    return;
  }
  // paper ledger: the persistent cloud log (never rolls off; kept by the cron whether
  // you're online or not) — fall back to the rolling history until the first log lands.
  const src = (PL && PL.trades && PL.trades.length)
    ? PL.trades.map((t) => ({ ...t, ctx: { pass: t.ctx_pass } }))
    : (m.history || []);
  let tr = src.filter((h) => h.ctx && h.ctx.pass && !isIndex(h.symbol) && h.entry_ts);
  tr = tr.slice().sort((a, b) => a.entry_ts.localeCompare(b.entry_ts));
  const endTs = AG.status === "paused" && AG.pausedAt ? AG.pausedAt : "9999";
  const evs = tr.filter((h) => h.entry_ts >= AG.startedAt && h.entry_ts <= endTs)
    .map((h) => ({ ts: h.entry_ts, kind: "trade", h }))
    .concat((AG.funds || []).map((f) => ({ ts: f.ts, kind: "fund", amt: f.amt })))
    .sort((a, b) => a.ts.localeCompare(b.ts));
  let eq = capV, added = 0, peak = capV, dd = 0, wins = 0, n = 0;
  const prows = [];
  const rs = (v) => `${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;
  for (const e of evs) {
    if (e.kind === "fund") {
      eq += e.amt; added += e.amt; peak = Math.max(peak, eq);
      prows.push(`<div class="row hist"><span>${(e.ts || "").slice(0, 10)}</span><span>＋ funds added</span><span></span><span class="win">${rs(e.amt)}</span><span>eq ₹${Math.round(eq).toLocaleString("en-IN")}</span></div>`);
      continue;
    }
    const h = e.h, rf = Math.abs(h.entry - h.sl) / (h.entry || 1);
    if (!rf || !isFinite(rf)) continue;
    const riskAmt = eq * (+AG.risk / 100);
    const pnl = riskAmt * (Math.max(h.r || 0, -1.5) - 0.0015 / rf);   // DOTM caps the tail; 0.15% cost
    eq += pnl;
    n++; if ((h.r || 0) > 0) wins++;
    peak = Math.max(peak, eq); dd = Math.min(dd, eq / peak - 1);
    prows.push(`<div class="row hist"><span>${(h.entry_ts || "").slice(0, 10)}</span><span>${(h.symbol || "").replace(".NS", "")} ${h.side === "long" ? "▲ long" : "▼ short"}</span><span class="${(h.r || 0) > 0 ? "win" : "loss"}">${(h.r || 0) >= 0 ? "+" : ""}${(h.r || 0).toFixed(2)}R</span><span class="${pnl >= 0 ? "win" : "loss"}">${rs(pnl)}</span><span>eq ₹${Math.round(eq).toLocaleString("en-IN")}</span></div>`);
  }
  const pn = $("#paper-note");
  if (pn) {
    pn.innerHTML = `Paper ledger since <b>${(AG.startedAt || "").slice(0, 10)}</b> · ${n} trades · ` +
      `${n ? Math.round((100 * wins) / n) : 0}% win · equity <b>₹${Math.round(eq).toLocaleString("en-IN")}</b>` +
      ` · rehearsal mode until the live feed (Fyers) is wired`;
    $("#paper-rows").innerHTML = prows.reverse().join("") ||
      '<p class="empty">No ⭐ Best trades have closed since the agent started — they\'ll appear here.</p>';
  }
  const pnl = eq - capV - added, cls = pnl >= 0 ? "win" : "loss";
  rep.innerHTML = mline +
    `<b>Paper equity: ₹${Math.round(eq).toLocaleString("en-IN")}</b> ` +
    `(<span class="${cls}">${pnl >= 0 ? "+" : ""}₹${Math.round(pnl).toLocaleString("en-IN")}</span>` +
    (added ? ` · ₹${added.toLocaleString("en-IN")} added` : "") + `) · ` +
    `${n} trades · ${n ? Math.round((100 * wins) / n) : 0}% win · worst dip ${(dd * 100).toFixed(1)}%` +
    `<br>Note: until the live feed (Fyers) is active, this ledger replays the scanner's rolling ⭐ Best history — treat it as a rehearsal, not a track record.`;
}

$("#agent-play").onclick = () => {
  const v = +($("#agent-capital").value || 0);
  if (!v) { $("#agent-report").textContent = "Enter your capital first."; return; }
  AG.capital = v;
  if (AG.status !== "paused" || !AG.startedAt) AG.startedAt = new Date().toISOString();
  AG.status = "running"; AG.pausedAt = null; agSave(); render();
};
$("#agent-pause").onclick = () => {
  if (AG.status !== "running") return;
  AG.status = "paused"; AG.pausedAt = new Date().toISOString(); agSave(); render();
};
$("#agent-stop").onclick = () => {
  AG = { status: "stopped", capital: AG.capital, risk: AG.risk, startedAt: null, pausedAt: null, funds: [] };
  agSave(); render();
};
$("#agent-fund").onclick = () => {
  const v = +($("#agent-add").value || 0);
  if (v > 0) { (AG.funds = AG.funds || []).push({ ts: new Date().toISOString(), amt: v }); $("#agent-add").value = ""; agSave(); render(); }
};
// ---------- ☁️ Google Drive sync — same OAuth client + pattern as the other DedicatusIT
// PWAs on this origin. Agent state lives in a hidden app-folder of the OWNER'S Drive
// (drive.appdata): private to their Google account, synced across devices, no server.
const G_CLIENT = "829111234642-lh1pqlen2lnoe6mv1hu998r3bg3gedrj.apps.googleusercontent.com";
const G_SCOPE = "https://www.googleapis.com/auth/drive.appdata";
const G_FILE = "fibleg_agent.json";   // distinct name — shares the app-folder with the other apps
const GD = { token: null, expiry: 0, fileId: null, on: localStorage.getItem("gdriveOn") === "1", status: "off" };
let _gTokenClient = null, _gPushTimer = null;

function gLoadScript() {
  return new Promise((res, rej) => {
    if (window.google && google.accounts) return res();
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.onload = res; s.onerror = rej;
    document.head.appendChild(s);
  });
}
async function gToken() {
  if (GD.token && Date.now() < GD.expiry - 60000) return GD.token;
  await gLoadScript();
  return new Promise((resolve, reject) => {
    _gTokenClient = _gTokenClient || google.accounts.oauth2.initTokenClient({
      client_id: G_CLIENT, scope: G_SCOPE, callback: () => {},
    });
    _gTokenClient.callback = (resp) => {
      if (resp && resp.access_token) {
        GD.token = resp.access_token;
        GD.expiry = Date.now() + (resp.expires_in || 3600) * 1000;
        resolve(GD.token);
      } else reject(resp);
    };
    try { _gTokenClient.requestAccessToken({ prompt: "" }); } catch (e) { reject(e); }
  });
}
async function gPull() {
  const t = await gToken();
  const q = encodeURIComponent(`name='${G_FILE}'`);
  const r = await fetch(`https://www.googleapis.com/drive/v3/files?spaces=appDataFolder&q=${q}&fields=files(id)`,
    { headers: { Authorization: "Bearer " + t } });
  const j = await r.json();
  GD.fileId = (j.files && j.files[0] && j.files[0].id) || null;
  if (!GD.fileId) return null;
  const f = await fetch(`https://www.googleapis.com/drive/v3/files/${GD.fileId}?alt=media`,
    { headers: { Authorization: "Bearer " + t } });
  return f.status === 200 ? f.json() : null;
}
async function gPush() {
  const t = await gToken();
  // NOTE: never fabricate _savedAt here — only agSave() (a real user action: ▶⏸⏹ /
  // funds / risk) stamps it. Otherwise a device merely CONNECTING with a stale stopped
  // copy would look "newest" and clobber a running agent on every other device.
  const body = JSON.stringify(AG);
  if (GD.fileId) {
    await fetch(`https://www.googleapis.com/upload/drive/v3/files/${GD.fileId}?uploadType=media`,
      { method: "PATCH", headers: { Authorization: "Bearer " + t, "Content-Type": "application/json" }, body });
  } else {
    const b = "fibleg" + Date.now();
    const mp = `--${b}\r\nContent-Type: application/json\r\n\r\n` +
      JSON.stringify({ name: G_FILE, parents: ["appDataFolder"] }) +
      `\r\n--${b}\r\nContent-Type: application/json\r\n\r\n${body}\r\n--${b}--`;
    const r = await fetch("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
      { method: "POST", headers: { Authorization: "Bearer " + t, "Content-Type": "multipart/related; boundary=" + b }, body: mp });
    const j = await r.json();
    GD.fileId = j.id || null;
  }
}
function gQueuePush() {
  if (!GD.on) return;
  clearTimeout(_gPushTimer);
  _gPushTimer = setTimeout(() => gPush()
    .then(() => { GD.status = "synced ✓"; renderGStatus(); })
    .catch(() => { GD.status = "sync failed — tap ☁️"; renderGStatus(); }), 1500);
}
function renderGStatus() {
  const el = $("#gdrive-status");
  if (el) { el.textContent = GD.status; el.className = "pill " + (GD.on && GD.status.includes("✓") ? "win" : ""); }
}
async function gConnect(auto) {
  try {
    GD.status = "connecting…"; renderGStatus();
    await gToken();
    const remote = await gPull();
    // newer copy wins (each save stamps _savedAt); a remote agent adopts onto this device
    if (remote && remote._savedAt && (!AG._savedAt || remote._savedAt > AG._savedAt)) {
      AG = remote;
      localStorage.setItem("agentState", JSON.stringify(AG));
    }
    GD.on = true; localStorage.setItem("gdriveOn", "1");
    await gPush();
    GD.status = "synced ✓"; renderGStatus(); render();
  } catch (e) {
    GD.on = false;
    if (auto) {
      // silent resume was blocked (browsers only allow the Google window on a tap):
      // stay linked and ask for one tap instead of quietly turning sync off
      GD.status = "tap ☁️ to reconnect"; renderGStatus();
    } else {
      localStorage.setItem("gdriveOn", "0");
      GD.status = "off"; renderGStatus();
      const rep = $("#agent-report");
      if (rep) rep.innerHTML = "☁️ Google sign-in didn't complete — allow the popup and try again (use the same Google account as your other DedicatusIT apps).";
    }
  }
}
$("#gdrive-btn").onclick = () => {
  if (GD.on) {
    GD.on = false; GD.token = null; localStorage.setItem("gdriveOn", "0");
    GD.status = "off"; renderGStatus();
    $("#agent-report").innerHTML = "☁️ Google sync turned off on this device (the copy in your Drive stays).";
  } else gConnect();
};
if (GD.on) { GD.status = "reconnecting…"; gConnect(true); }   // silent resume on devices already linked

// while the app is open, quietly re-check Drive each minute so a change made on
// another device (e.g. ▶ started on the PC) appears here without a reopen
async function gAutoPull() {
  // strictly non-interactive: only while the current token is still valid — a timer
  // must never trigger the Google popup (browsers block it and spam errors)
  if (!GD.on || !GD.token || Date.now() > GD.expiry - 60000) return;
  try {
    const remote = await gPull();
    if (remote && remote._savedAt && (!AG._savedAt || remote._savedAt > AG._savedAt)) {
      AG = remote;
      localStorage.setItem("agentState", JSON.stringify(AG));
      GD.status = "synced ✓"; renderGStatus(); render();
    }
  } catch (e) { /* transient — next tick retries */ }
}
setInterval(gAutoPull, 60000);

// ---------- History sub-tabs: 📜 Paper · 💼 Real · 🧪 Backtest ----------
function renderHistTabs() {
  const box = $("#hist-tabs");
  if (!box) return;
  box.innerHTML = "";
  [["paper", "📜 Paper trades"], ["real", "💼 Real trades"], ["backtest", "🧪 Backtest"]].forEach(([v, l]) => {
    const b = document.createElement("button");
    b.className = "tf" + (histTab === v ? " active" : "");
    b.textContent = l;
    b.onclick = () => { histTab = v; localStorage.setItem("histTab", v); renderHistTabs(); };
    box.appendChild(b);
  });
  $("#hist-paper").hidden = histTab !== "paper";
  $("#hist-real").hidden = histTab !== "real";
  $("#hist-backtest").hidden = histTab !== "backtest";
}

const BTS = {};        // per-TF backtest files, fetched on demand (backtest_{tf}.json)
let btYearsFor = "";   // which dataset the year pickers were populated from
const BT_EXIT_LABELS = { full: "Square all at T1", partial: "Let run + BE", lockb: "Let run + lock B" };
function btButtons(box, opts, cur, onPick) {
  box.innerHTML = "";
  opts.forEach(([v, l]) => {
    const b = document.createElement("button");
    b.className = "tf" + (cur === v ? " active" : "");
    b.textContent = l;
    b.onclick = () => onPick(v);
    box.appendChild(b);
  });
}
function renderBacktest() {
  const el = $("#bt-stats");
  if (!el) return;
  btButtons($("#bt-tf"), ["45", "60", "120", "180", "240"].map((t) => [t, tfLabel(t)]),
    btTf, (v) => { btTf = v; renderBacktest(); });
  btButtons($("#bt-exit"), Object.entries(BT_EXIT_LABELS), btExit, (v) => { btExit = v; renderBacktest(); });
  btButtons($("#bt-range"), [["10", "Last 10 yrs"], ["15", "Last 15 yrs"], ["custom", "Custom"]],
    btRange, (v) => { btRange = v; renderBacktest(); });
  btButtons($("#bt-filter"), [["best", "⭐ Best"], ["rev", "Reversal+trend"], ["all", "All"]],
    btBest, (v) => { btBest = v; renderBacktest(); });
  // ↺ back to the validated default combo (2H · lock-B · ⭐ Best · last 10 yrs)
  const rst = document.createElement("button");
  rst.className = "tf";
  rst.textContent = "↺ Default";
  rst.title = "Reset to the validated configuration: 2H · Let run + lock B · ⭐ Best · Last 10 yrs";
  rst.onclick = () => { btTf = "120"; btExit = "lockb"; btBest = "best"; btRange = "10"; renderBacktest(); };
  $("#bt-filter").appendChild(rst);
  const from = $("#bt-from"), to = $("#bt-to");
  from.style.display = to.style.display = btRange === "custom" ? "" : "none";

  // resolve the dataset for the chosen TF: per-TF file, else (120m) the legacy file
  let bt = BTS[btTf];
  if (bt === undefined) {
    BTS[btTf] = null;   // loading
    fetch(`backtest_${btTf}.json?t=` + Date.now()).then((r) => (r.ok ? r.json() : { missing: true }))
      .catch(() => ({ missing: true }))
      .then((j) => { BTS[btTf] = j; renderBacktest(); });
    bt = null;
  }
  if (bt && bt.missing && btTf === "120" && BT)
    bt = { config: BT.config + " · lock-at-B only", cost_pct: BT.cost_pct, dotm_cap_r: BT.dotm_cap_r,
           year_min: BT.year_min, year_max: BT.year_max, legacy: true,
           exits: { lockb: BT.trades.map((t) => ({ y: t.y, m: t.m, d: t.d, r: t.r, rf: t.rf, f: t.c ? 1 : 0 })) } };
  if (bt === null) { el.textContent = "loading backtest…"; $("#bt-years").innerHTML = ""; return; }
  if (!bt || bt.missing) {
    el.innerHTML = `<b>${tfLabel(btTf)} results aren't generated yet.</b> On the PC that holds the ` +
      `11-yr dataset, double-click <b>generate_backtests.bat</b> (repo root) — it computes every ` +
      `timeframe × exit combo (~2–3 hrs) and publishes them here automatically.`;
    $("#bt-years").innerHTML = "";
    return;
  }
  let tr = bt.exits && bt.exits[btExit];
  if (!tr) {
    el.innerHTML = `<b>${BT_EXIT_LABELS[btExit]}</b> isn't in this dataset (legacy file has lock-B only). ` +
      `Run <b>generate_backtests.bat</b> to add every exit.`;
    $("#bt-years").innerHTML = "";
    return;
  }
  const y0a = bt.year_min, y1a = bt.year_max;
  if (btYearsFor !== btTf + ":" + y0a) {           // (re)populate the year pickers per dataset
    from.innerHTML = ""; to.innerHTML = "";
    for (let y = y0a; y <= y1a; y++) { from.add(new Option(y, y)); to.add(new Option(y, y)); }
    from.value = y0a; to.value = y1a;
    from.onchange = to.onchange = renderBacktest;
    btYearsFor = btTf + ":" + y0a;
  }
  let y0, y1;
  if (btRange === "custom") { y0 = +from.value; y1 = +to.value; if (y0 > y1) [y0, y1] = [y1, y0]; }
  else { y1 = y1a; y0 = Math.max(y0a, y1a - (+btRange) + 1); }
  // filter flags: 1=⭐ ctx-pass · 2=M/W · 4=pin · 16=HTF
  const pass = btBest === "best" ? (t) => t.f & 1
    : btBest === "rev" ? (t) => (t.f & 2 || t.f & 4) && (t.f & 16)
    : () => true;
  if (btBest === "rev" && bt.legacy) {
    el.innerHTML = "Reversal+trend slicing needs the full files — run <b>generate_backtests.bat</b>. (⭐ Best and All work on the legacy file.)";
    $("#bt-years").innerHTML = "";
    return;
  }
  const cost = (bt.cost_pct || 0.15) / 100, cap = bt.dotm_cap_r || -1.5;
  tr = tr.filter((t) => t.y >= y0 && t.y <= y1 && pass(t));
  let net = 0, wins = 0, eq = 1, peak = 1, dd = 0;
  const perY = {};
  for (const t of tr) {
    const nr = t.r - cost / t.rf;
    net += nr; if (t.r > 0) wins++;
    const p = (perY[t.y] = perY[t.y] || { n: 0, r: 0, w: 0 });
    p.n++; p.r += nr; if (t.r > 0) p.w++;
    eq *= 1 + 0.01 * (Math.max(t.r, cap) - cost / t.rf);   // 1% risk, DOTM-capped
    peak = Math.max(peak, eq); dd = Math.min(dd, eq / peak - 1);
  }
  const yrs = Math.max(1, y1 - y0 + 1);
  const cagr = (Math.pow(eq, 1 / yrs) - 1) * 100;
  const fLabel = btBest === "best" ? "⭐ Best (context-gated)" : btBest === "rev" ? "Reversal + trend" : "All setups";
  el.innerHTML = `<b>${tfLabel(btTf)} · ${BT_EXIT_LABELS[btExit]} · ${fLabel} · ${y0}–${y1}</b><br>` +
    `net <b class="${net >= 0 ? "win" : "loss"}">${net >= 0 ? "+" : ""}${net.toFixed(0)}R</b> (after ${bt.cost_pct}% costs) · ` +
    `${tr.length} trades · ${tr.length ? Math.round((100 * wins) / tr.length) : 0}% win · ` +
    `at 1% risk/trade: <b>${eq.toFixed(1)}×</b> ≈ ${cagr.toFixed(0)}%/yr · worst dip ${(dd * 100).toFixed(0)}%` +
    (btRange === "15" && y1a - y0a + 1 < 15
      ? `<br>⚠ data begins ${y0a} — showing the full ${y0a}–${y1a} span (${y1a - y0a + 1} yrs)` : "");
  const box = $("#bt-years");
  box.innerHTML = "";
  Object.keys(perY).sort().forEach((y) => {
    const p = perY[y], cls = p.r >= 0 ? "win" : "loss";
    const row = document.createElement("div");
    row.className = "row hist";
    row.innerHTML = `<span>${y}</span><span>${p.n} trades</span>` +
      `<span>${Math.round((100 * p.w) / p.n)}% win</span>` +
      `<span class="${cls}">${p.r >= 0 ? "+" : ""}${p.r.toFixed(1)}R</span>`;
    box.appendChild(row);
  });
}

// ---------- top-level tabs: 📡 Live · 🤖 Agent · 📜 History · ✅ Legs ----------
let mainTab = localStorage.getItem("mainTab") || "live";
function renderMainTabs() {
  const box = $("#main-tabs");
  if (!box) return;
  box.innerHTML = "";
  [["live", "📡 Live"], ["agent", "🤖 Agent"], ["history", "📜 History"], ["legs", "✅ Legs"]].forEach(([v, l]) => {
    const b = document.createElement("button");
    b.className = "tf" + (mainTab === v ? " active" : "");
    b.textContent = l;
    b.onclick = () => {
      mainTab = v; localStorage.setItem("mainTab", v);
      $("#chart-section").hidden = true;   // the chart doesn't follow you across tabs — tap a symbol to reopen
      renderMainTabs();
    };
    box.appendChild(b);
  });
  ["live", "agent", "history", "legs"].forEach((v) => {
    const el = $("#tab-" + v);
    if (el) el.hidden = mainTab !== v;
  });
}
renderMainTabs();

// chart collapse (keep the header, hide the canvas) and close
let chartCollapsed = false;
function setChartCollapsed(c) {
  chartCollapsed = c;
  ["#chart", "#legend"].forEach((s) => { const e = $(s); if (e) e.style.display = c ? "none" : ""; });
  const bar = document.querySelector("#chart-section .chart-bar");
  if (bar) bar.style.display = c ? "none" : "";
  $("#adjust-panel").hidden = true;
  $("#chart-collapse").textContent = c ? "▸" : "▾";
}
$("#chart-collapse").onclick = () => setChartCollapsed(!chartCollapsed);
$("#chart-close").onclick = () => { $("#chart-section").hidden = true; };

load();
setInterval(load, 60000);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
