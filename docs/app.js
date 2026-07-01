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
        ${w.mw ? `<span class="mw on" title="${w.side === "long" ? "W (double-bottom) at the leg start — a downtrend ended and this up-impulse began" : "M (double-top) at the leg start — an uptrend ended and this down-impulse began"}">${w.side === "long" ? "W" : "M"}</span>` : ""}
        ${w.ew ? `<span class="ew on" title="Elliott Wave: the impulse subdivides into a clean 5-wave structure">EW</span>` : ""}
        ${w.conf ? `<span class="conf on" title="A+ confluence: a broken prior mountain/valley sits in the 0.5–0.618 entry zone (old resistance→support)">A+</span>` : ""}
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
  el.innerHTML = `
    <span class="sym">${w.symbol} <span class="badge ${w.side}">${w.side}</span>${edited}</span>
    <span class="num">${w.leg.start} → ${w.leg.end}</span>
    ${mw}${ew}${conf}
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
    : localStorage.getItem("mwOnly") === "1" ? "mw" : "all");
let confOnly = reversalMode === "aplus";   // A+ full-edge (confluence + nested + zone + M/W|pin)
let mwOnly = reversalMode === "mw";        // only M/W reversal
let pinOnly = reversalMode === "pin";      // only pin-bar reversal
const REVERSAL_LABELS = { all: "All", aplus: "A+", mw: "Only M/W", pin: "Only Pin" };
let showIndices = localStorage.getItem("showIndices") === "1";   // default off = stocks only

const isIndex = (sym) => typeof sym === "string" && sym.startsWith("^");
const execKey = () => [entryRatio, exitStyle, trigTf, slRatio].join("|");
const exitLabel = (x) => x === "full" ? "Square all at T1" : "Let it run + protect";
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
  if (curSymbol && LEG_BY_SYM[curSymbol]) showChart(curSymbol, LEG_BY_SYM[curSymbol]);
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
  group($("#entry-ratio"), col(0), entryRatio, (r) => r, setEntry);
  group($("#exit-style"), col(1), exitStyle, exitLabel, setExit);
  group($("#trigger-tf"), col(2), trigTf, trigLabel, setTrig);
  group($("#sl-ratio"), col(3), slRatio, slLabel, setSl);
  // Entry/stop are ALWAYS the zone (0.5-0.618 entry, 0.786 stop) now — never manual —
  // so both selectors are permanently grayed out in every mode.
  const zoneAlways = !!(DATA && DATA.zone_entry);
  $("#entry-ratio") && $("#entry-ratio").classList.toggle("disabled", zoneAlways);
  $("#sl-ratio") && $("#sl-ratio").classList.toggle("disabled", zoneAlways);
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
  mk.textContent = ms.text;
  mk.className = "market " + (ms.open ? "open" : "closed");

  const wl = $("#watchlist");
  wl.innerHTML = "";
  let watch = (m.watchlist || []).map(withOverride);
  if (!showIndices) watch = watch.filter((w) => !isIndex(w.symbol));
  if (mwOnly) watch = watch.filter((w) => w.mw);
  if (pinOnly) watch = watch.filter((w) => w.pin);
  if (confOnly) watch = watch.filter((w) => w.conf);
  if (confOnly && !usingConf) watch = watch.map(applyConf);
  $("#watch-count").textContent = watch.length;
  $("#watch-empty").hidden = watch.length > 0;
  watch.forEach((w) => wl.appendChild(setupCard(w)));

  let hist = m.history || [];
  if (!showIndices) hist = hist.filter((h) => !isIndex(h.symbol));
  if (mwOnly) hist = hist.filter((h) => h.mw);   // history follows the same filter
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
  if (!showIndices) all = all.filter((w) => !isIndex(w.symbol));
  if (mwOnly) all = all.filter((w) => w.mw);
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
}

async function load() {
  try {
    const res = await fetch("signals.json?t=" + Date.now());
    DATA = await res.json();
    if (!detectTF || !(DATA.detect_tfs || []).includes(detectTF))
      detectTF = DATA.default_tf || "240";
    if (!method || !(DATA.methods || []).includes(method))
      method = DATA.default_method || "adaptive";
    // validate execution (entry|exit|trigger|sl); fall back to the feed's default
    if (!(DATA.execs || []).includes(execKey())) {
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

$("#refresh").onclick = load;
$("#settings-btn").onclick = () => { const s = $("#settings"); s.hidden = !s.hidden; };

// ONE mutually-exclusive setup filter: All / A+ / Only M/W / Only Pin
function setMode(m) {
  reversalMode = m;
  confOnly = m === "aplus"; mwOnly = m === "mw"; pinOnly = m === "pin";
  localStorage.setItem("reversalMode", m);
  localStorage.removeItem("confOnly"); localStorage.removeItem("mwOnly");  // retire old keys
  renderReversalButtons();
  applySettings();
}
function renderReversalButtons() {
  const box = $("#reversal-mode");
  if (!box) return;
  box.innerHTML = "";
  ["all", "aplus", "mw", "pin"].forEach((m) => {
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
load();
setInterval(load, 60000);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
