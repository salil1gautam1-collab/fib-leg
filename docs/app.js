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
  let t = new Date(iso).getTime();
  // timestamps WITHOUT a timezone suffix come from the cloud scanner's clock (UTC);
  // parsing them as device-local time skews the age (+5.5h on IST). Re-parse as UTC —
  // unless that lands in the future (a locally-generated file), then keep local.
  if (typeof iso === "string" && !/Z|[+-]\d\d:?\d\d$/.test(iso)) {
    const utc = new Date(iso + "Z").getTime();
    if (Date.now() - utc >= -60000) t = utc;
  }
  const d = (Date.now() - t) / 1000;
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
// the chart area is shared by two systems: the LEG chart (Live/Legs, per-TF bars from
// the scan) and the BOOK chart (Trades/Universe, one 5m series resampled client-side).
// chartMode routes the on-chart timeframe buttons to the right one so switching TF on a
// book chart resamples that chart instead of yanking back to a leg chart.
let chartMode = "leg";                       // "leg" | "book"
let bookCtx = null;                          // {symbol, trade, tf} when a book chart is open
let bookSeries = null;                        // open book chart's candle series (live refresh)
const BOOK_TFS = ["5", "15", "60", "120"];   // 5m / 15m / 1H / 2H (resampled from stored 5m)

// keep an already-open chart live: on each 60s data reload, push the fresh candles into the
// existing series (setData, not a rebuild) so it ticks forward WITHOUT resetting zoom/scroll.
function refreshOpenChart() {
  try {
    if (chartMode === "book" && bookCtx && bookSeries) {
      const bc = getBookData(bookCtx.symbol);
      if (bc && bc.bars && bc.bars.length) bookSeries.setData(bookBarsFor(bc, bookCtx.tf));
    } else if (chartMode === "leg" && curSymbol && curSeries) {
      const fresh = (typeof CHARTS !== "undefined" && CHARTS) ? CHARTS[curSymbol] : null;
      if (fresh && fresh.length) curSeries.setData(fresh);
    }
  } catch (e) { /* a transient render race shouldn't break the reload */ }
}
let adjustMode = 0, adjustStart = null;
let LEG_BY_SYM = {}, navSyms = [];
let ALL_LEGS_RAW = {};   // symbol -> current leg (UNFILTERED), for chart viewing on any TF
// guarded: one corrupt byte in localStorage must not brick the whole app (audit C1)
const overrides = (() => { try { return JSON.parse(localStorage.getItem("legOverrides") || "{}") || {}; } catch { return {}; } })();

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
  chartMode = "leg"; bookCtx = null;   // leaving any book chart → restore leg TF buttons
  curSymbol = symbol; curBaseSetup = setup;
  curSetup = applyOverride(symbol, setup);
  adjustMode = 0;
  renderTFButtons();
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

  // the chart library renders times in UTC by default — format the axis and the
  // crosshair in IST (exchange time) so candles read 09:15, 11:15… like TradingView
  const IST = 19800, p2 = (n) => String(n).padStart(2, "0");
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const istTick = (t, type) => {
    const d = new Date((t + IST) * 1000);
    if (type === 0) return String(d.getUTCFullYear());
    if (type === 1) return MON[d.getUTCMonth()];
    if (type === 2) return String(d.getUTCDate());
    return p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes());
  };
  const istFull = (t) => {
    const d = new Date((t + IST) * 1000);
    return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} '${String(d.getUTCFullYear()).slice(2)}  ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())} IST`;
  };
  chartObj = LightweightCharts.createChart(mount, {
    autoSize: true,
    layout: { background: { color: "#131c2e" }, textColor: "#e6edf6" },
    grid: { vertLines: { color: "#1b2740" }, horzLines: { color: "#1b2740" } },
    timeScale: { timeVisible: true, borderColor: "#243150", tickMarkFormatter: istTick },
    rightPriceScale: { borderColor: "#243150" },
    crosshair: { mode: 0 },
    localization: { timeFormatter: istFull },
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
  // always refresh the open LEG chart on any settings/TF change — use the UNFILTERED current
  // leg for the symbol (so switching TF redraws the leg even if the filter would hide it).
  // (skip when a book chart is open — a Settings change shouldn't blow it away.)
  if (chartMode === "leg" && curSymbol) showChart(curSymbol, ALL_LEGS_RAW[curSymbol] || LEG_BY_SYM[curSymbol] || curBaseSetup);
}

function setTF(tf) {
  detectTF = String(tf);
  localStorage.setItem("detectTF", detectTF);
  applySettings();
}

// #detect-tf (Settings) always drives the leg detection TF. #tf-select (on the chart)
// follows the open chart: leg TFs in leg mode, book TFs (which resample the 5m series)
// when a book chart is open.
function renderTFButtons() {
  const legTfs = (DATA && DATA.detect_tfs) || ["45", "60", "120", "180", "240"];
  ["#detect-tf", "#tf-select"].forEach((id) => {
    const box = $(id);
    if (!box) return;
    const asBook = id === "#tf-select" && chartMode === "book";
    const tfs = asBook ? BOOK_TFS : legTfs;
    const active = asBook ? (bookCtx && bookCtx.tf) : detectTF;
    box.innerHTML = "";
    tfs.forEach((tf) => {
      const b = document.createElement("button");
      b.className = "tf" + (String(tf) === String(active) ? " active" : "");
      b.textContent = tfLabel(tf);
      b.onclick = asBook ? () => setBookTF(tf) : () => setTF(tf);
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
  // honest freshness (owner, 2026-07-09): show the pipeline heartbeat AND the expectation,
  // separately — "scanned 3m ago · next ~4m". A healthy feed should LOOK healthy; and when
  // "next ~" blows past its promise you know something is actually wrong.
  const CYCLE_MIN = 6;                        // scan ~4m + 2m pause (post speed-up)
  const genMs = DATA.generated_at ? new Date(DATA.generated_at).getTime() : null;
  const agoMin = genMs ? (Date.now() - genMs) / 60000 : null;
  const isOpenNow = marketStatus().open;
  let fresh = `scanned ${fmtAge(DATA.generated_at)}`;
  if (agoMin != null && isOpenNow) {
    const nextIn = Math.max(0, CYCLE_MIN - agoMin);
    fresh += nextIn > 0 ? ` · next ~${Math.ceil(nextIn)}m` : ` · next any moment`;
    if (agoMin > CYCLE_MIN * 2.5) fresh += " ⚠ late";
  }
  $("#meta").textContent =
    `source: ${DATA.source} · ${tfLabel(detectTF)} · ${methodLabel(method)} · ${lvlLabel} · ${exitStyle} · ${trigTf}m · ${fresh}`;
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
      .then((j) => { if (j) { PL = j; renderPocketLog(); renderBookCombined(); } }).catch(() => {});
    // the two cloud paper books (⚡ Scalper + 🛡 Defense) + the combined view
    fetch("paper_levels.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { LVL = j; renderBooks(); } }).catch(() => {});
    fetch("paper_defense.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { DEF = j; renderBooks(); } }).catch(() => {});
    if (!BOOKBT)   // the Book's 11-yr comparison table — static, fetch once
      fetch("backtest_book.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
        .then((j) => { if (j) { BOOKBT = j; renderBookBacktest(); } }).catch(() => {});
    // book universe + per-symbol chart data (candles + fib levels) for the books
    fetch("book_universe.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { BOOKUNIV = j; renderUniverse(); } }).catch(() => {});
    fetch("book_charts.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { BOOKCHARTS = j; renderUniverse(); refreshOpenChart(); } }).catch(() => {});
    // deep 1H/2H history — separate file, updated ~twice/hr (higher TFs change slowly)
    fetch("book_charts_htf.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { BOOKHTF = j; refreshOpenChart(); } }).catch(() => {});
    // the live armed-order list (what's resting at the levels right now)
    fetch("resting_orders.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { RESTING = j; if (mainTab === "resting") renderResting(); } }).catch(() => {});
    // 🎲 dealer-gamma map (flip level + walls per underlying)
    fetch("gamma_map.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { GAMMA = j; if (mainTab === "gamma") renderGamma(); } }).catch(() => {});
    // 🎲 gamma engine ledger (the account whose equity answers "does gamma pay?")
    fetch("paper_gamma.json?t=" + Date.now()).then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (j) { PGAMMA = j; renderBookCombined(); if (mainTab === "gamma") renderGamma(); } }).catch(() => {});
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
    refreshOpenChart();          // tick the open chart forward with the fresh data
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
let AG = (() => { try { return JSON.parse(localStorage.getItem("agentState") || "null"); } catch { return null; } })() ||
  { status: "stopped", capital: 0, risk: "1", startedAt: null, pausedAt: null, funds: [] };
if (!RISK_PLANS[AG.risk]) AG.risk = "1";   // legacy/foreign risk values must not crash renderAgent (audit C2)
// import an agent shared from another device via a 🔗 sync link (#agent=…). The state
// rides in the URL FRAGMENT, which browsers never send to the server — device-to-device only.
if (location.hash.startsWith("#agent=")) {
  try {
    const inc = JSON.parse(atob(decodeURIComponent(location.hash.slice(7))));
    if (inc && typeof inc === "object" && "status" in inc &&
        confirm(`Import agent from sync link?\n\ncapital ₹${(+inc.capital || 0).toLocaleString("en-IN")} · risk ${inc.risk}%/trade · started ${(inc.startedAt || "—").slice(0, 10)}\n\nThis replaces this device's agent.`)) {
      AG = inc;
      if (!RISK_PLANS[AG.risk]) AG.risk = "1";   // foreign device may carry a legacy risk value
      localStorage.setItem("agentState", JSON.stringify(AG));
    }
  } catch (e) { /* malformed link — ignore */ }
  history.replaceState(null, "", location.pathname + location.search);
}
// History sub-tabs + the precomputed full-history backtest (docs/backtest.json)
let histTab = localStorage.getItem("histTab") || "paper";
let BT = null;
let PL = null;   // persistent cloud paper log (docs/paper_log.json) — never rolls off
let LVL = null;  // ⚡ Scalper ledger (docs/paper_levels.json) — cloud paper book
let DEF = null;  // 🛡 Defense ledger (docs/paper_defense.json) — cloud paper book

// shared clean table — headers[], rows[][], aligns[] ('left'|'right'|'center')
function miniTable(headers, rows, aligns) {
  const a = (i) => aligns && aligns[i] ? aligns[i] : "left";
  const th = headers.map((h, i) =>
    `<td style="padding:3px 10px 3px 0;opacity:.55;text-align:${a(i)};font-weight:600">${h}</td>`).join("");
  const tr = rows.map((r) => `<tr style="border-top:1px solid #1b2740">` +
    r.map((c, i) => `<td style="padding:3px 10px 3px 0;text-align:${a(i)}">${c}</td>`).join("") +
    `</tr>`).join("");
  return `<table style="border-collapse:collapse;white-space:nowrap;font-variant-numeric:tabular-nums">` +
    `<thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
}
const _nmS = (s) => s.replace(".NS", "").replace("^NSEBANK", "BankNifty").replace("^NSEI", "Nifty");
const _rCol = (r) => `<b style="color:${r >= 0 ? "#4ade80" : "#f87171"}">${r >= 0 ? "+" : ""}${r}R</b>`;
const _inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const _netR = (a) => a.reduce((s, t) => s + (t.r || 0), 0);
// 🏛 Pocket ⭐ Best stock trades SINCE your Start date only (anything earlier is
// backtest, not paper — owner rule). Empty until the agent is started.
function pocketTrades() {
  if (!(PL && PL.trades && AG && AG.startedAt)) return [];
  // Date-parse both sides: entry_ts is IST (+05:30), startedAt is UTC (Z) — a string
  // compare treated IST wall-clock as UTC and wrongly included ~5.5h of pre-Start trades.
  const t0 = new Date(AG.startedAt).getTime();
  return PL.trades.filter((t) => t.ctx_pass && t.entry_ts && t.side !== "short" &&
    !(t.symbol || "").startsWith("^") && new Date(t.entry_ts).getTime() >= t0);
}

function renderLedger(st, elId) {
  const el = document.getElementById(elId);
  if (!el || !st) return;
  const c = st.closed || [], sc = st.shadow_closed || [], op = st.open || [];
  const wins = c.filter((t) => (t.r || 0) > 0).length;
  const pnl = (st.equity || 0) - (st.capital || 0);
  const dir = (d) => d === 1 ? "long" : "short";
  const chip = (label, val) => `<span style="opacity:.6">${label}</span> <b>${val}</b>`;
  const stats = `<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:6px">` +
    `<span><b>${_inr(st.equity || 0)}</b> <span style="color:${pnl >= 0 ? "#4ade80" : "#f87171"}">` +
    `${pnl >= 0 ? "+" : "−"}${_inr(Math.abs(pnl))}</span></span>` +
    `<span>${chip("closed", c.length)}</span><span>${chip("win", (c.length ? Math.round(100 * wins / c.length) : 0) + "%")}</span>` +
    `<span>${chip("net", _netR(c).toFixed(1) + "R")}</span>` +
    `<span style="opacity:.55">shadow ${sc.length} (${_netR(sc).toFixed(1)}R) · since ${(st.started || "").slice(0, 10)}</span></div>`;
  let trip = "";
  if (st.halted) trip = `<div style="color:#f87171;margin-bottom:4px">⛔ <b>Tripwire halt</b> since ${st.halted.slice(0, 10)} — shadow-only until you reset it</div>`;
  else if ((st.dd || 0) >= 0.20) trip = `<div style="color:#fbbf24;margin-bottom:4px">⚠ drawdown ${(st.dd * 100).toFixed(1)}% — risk halved</div>`;
  let gem = "";
  if (elId === "lvl-audition") {
    const gi = (p) => p.sym.startsWith("^");
    const gc = c.filter(gi), go = op.filter(gi);
    gem = `<div style="opacity:.85;margin:2px 0">💎 Gem (index subset): ${gc.length} closed · net ${_netR(gc).toFixed(1)}R · ${go.length} open</div>`;
  }
  const openTbl = op.length
    ? miniTable(["Stock", "Dir", "Level", "Entry", "SL", "Target"],
      op.map((p) => [_nmS(p.sym) + (p.collision ? " ⚠" : ""), dir(p.d), p.tf / 60 + "H@" + p.lvl, p.entry, p.stop, p.tgt]),
      ["left", "left", "left", "right", "right", "right"])
    : `<div style="opacity:.55">no open positions</div>`;
  const hist = c.slice(-8).reverse();
  const histTbl = hist.length
    ? miniTable(["Date", "Stock", "Dir", "Level", "Result", "Reason"],
      hist.map((p) => [(p.exit_ts || "").slice(0, 10), _nmS(p.sym), dir(p.d), p.tf / 60 + "H@" + p.lvl, _rCol(p.r), p.reason]),
      ["left", "left", "left", "left", "right", "left"])
    : `<div style="opacity:.55">no closed trades yet</div>`;
  el.innerHTML = stats + trip + gem +
    `<div style="margin-top:6px;opacity:.6;font-weight:600">Open (${op.length})</div>` +
    `<div class="tblwrap">${openTbl}</div>` +
    `<div style="margin-top:6px;opacity:.6;font-weight:600">Recent history</div>` +
    `<div class="tblwrap">${histTbl}</div>`;
}

function renderBookCombined() {
  const el = document.getElementById("book-combined");
  if (!el) return;
  const pkt = pocketTrades();   // ⭐ stock trades since your Start date only
  const pktR = pkt.reduce((s, t) => s + (t.r || 0), 0);
  const rC = (r) => `<span style="color:${r >= 0 ? "#4ade80" : "#f87171"}">${r >= 0 ? "+" : ""}${r.toFixed(1)}R</span>`;
  const pC = (v) => v == null ? "—" : `<span style="color:${v >= 0 ? "#4ade80" : "#f87171"}">${v >= 0 ? "+" : "−"}${_inr(Math.abs(v))}</span>`;
  const eng = (name, st) => {
    if (!st) return [name, "…", "", "", ""];
    const c = st.closed || [];
    return [name, c.length, rC(_netR(c)), pC((st.equity || 0) - (st.capital || 0)), (st.open || []).length];
  };
  const rows = [["🏛 Pocket", pkt.length, rC(pktR), pC(null), "—"], eng("⚡ Scalper", LVL)];
  if (LVL) {
    const gi = (p) => p.sym.startsWith("^");
    const gc = (LVL.closed || []).filter(gi);
    rows.push([`<span style="opacity:.7">↳ 💎 Gem</span>`, gc.length, rC(_netR(gc)), "—", (LVL.open || []).filter(gi).length]);
  }
  rows.push(eng("🛡 Defense", DEF));
  if (PGAMMA) rows.push(eng("🎲 Gamma", PGAMMA));         // experimental 5th engine
  let combined = 0;
  for (const st of [LVL, DEF, PGAMMA]) if (st) combined += (st.equity || 0) - (st.capital || 0);
  // total net R across all engines (Gem is the index subset of Scalper — don't double-count)
  let totalR = pktR;
  if (LVL) totalR += _netR(LVL.closed || []);
  if (DEF) totalR += _netR(DEF.closed || []);
  if (PGAMMA) totalR += _netR(PGAMMA.closed || []);
  el.innerHTML = `<div class="tblwrap">` +
    miniTable(["Engine", "Closed", "Net R", "P&L", "Open"], rows, ["left", "right", "right", "right", "right"]) +
    `</div><div style="margin-top:6px"><b>Combined P&L (cloud books + gamma): ${pC(combined)}</b>` +
    ` <span style="opacity:.6;font-size:12px">· per-engine R above (R is not summed across engines — different ₹ per R)</span></div>` +
    `<div style="opacity:.55;font-size:12px;margin-top:3px">🎲 Gamma is experimental (forward test, unproven). ` +
    `Pocket is sized in the 🤖 Agent tab, so it's in Net R but not the ₹ combined. Gem is the index subset of Scalper.</div>`;
}

// 🏛 Pocket's ⭐ Best cloud log (docs/paper_log.json) — the objective engine record,
// independent of your personal Agent start date (that drives the Agent-tab equity).
// Shown in History → Paper trades so it MATCHES the combined table's Pocket count.
function renderPocketLog() {
  const pn = document.getElementById("paper-note"), rowsEl = document.getElementById("paper-rows");
  if (!pn || !rowsEl) return;
  const clog = pocketTrades();   // stock ⭐ trades since your Start date
  const netR = clog.reduce((s, t) => s + (t.r || 0), 0);
  const wins = clog.filter((t) => (t.r || 0) > 0).length;
  // ⭐ gate scorecard (owner ask 2026-07-15): the ledger records non-⭐ trades too —
  // Pocket's de-facto shadow book — so the gate itself is scored, like gamma's gates.
  let gateLine = "";
  if (PL && PL.trades && AG && AG.startedAt) {
    const t0 = new Date(AG.startedAt).getTime();
    const sat = PL.trades.filter((t) => !t.ctx_pass && t.entry_ts && t.side !== "short" &&
      !(t.symbol || "").startsWith("^") && new Date(t.entry_ts).getTime() >= t0 && t.r != null);
    if (sat.length) {
      const sr2 = sat.reduce((s2, t) => s2 + (t.r || 0), 0);
      gateLine = `<br><span style="opacity:.65;font-size:12px">⭐ gate: sat-out signals would've made <b>${sr2 >= 0 ? "+" : ""}${sr2.toFixed(1)}R</b> (${sat.length}) — ${sr2 < 0 ? "the gate is saving money ✓" : "the gate may be costing money — worth a look"}</span>`;
    }
  }
  pn.innerHTML = `Cloud ⭐ Best log · <b>${clog.length}</b> trades · ` +
    `${clog.length ? Math.round(100 * wins / clog.length) : 0}% win · net <b>${netR.toFixed(1)}R</b> ` +
    `<span style="opacity:.55">(your capital-sized equity curve is in the 🤖 Agent tab)</span>` + gateLine;
  const recent = clog.slice().sort((a, b) => (b.entry_ts || "").localeCompare(a.entry_ts || "")).slice(0, 12);
  rowsEl.innerHTML = clog.length
    ? `<div class="tblwrap">` + miniTable(["Date", "Stock", "Dir", "Result"],
        recent.map((t) => [(t.entry_ts || "").slice(0, 10), (t.symbol || "").replace(".NS", ""),
          t.side === "long" ? "long" : "short", _rCol(+(t.r || 0).toFixed(2))]),
        ["left", "left", "left", "right"]) + `</div>`
    : '<p class="empty">No ⭐ Best trades logged yet.</p>';
}

// 🤖 Agent tab roster — all 4 agents at a glance, counts since your Start date
function renderAgentRoster() {
  const el = document.getElementById("agent-roster");
  if (!el) return;
  const rC = (r) => `<span style="color:${r >= 0 ? "#4ade80" : "#f87171"}">${r >= 0 ? "+" : ""}${r.toFixed(1)}R</span>`;
  const since = (d) => (d || "").slice(0, 10) || "—";
  const rows = [];
  const pk = pocketTrades();
  const pkStatus = AG.status === "running" ? "▶ running" : AG.status === "paused" ? "⏸ paused" : "⏹ stopped";
  rows.push(["🏛 Pocket", pkStatus, since(AG.startedAt), pk.length, rC(_netR(pk)), "—"]);
  if (LVL) {
    const ni = (p) => !p.sym.startsWith("^"), gi = (p) => p.sym.startsWith("^");
    const sc = (LVL.closed || []).filter(ni), so = (LVL.open || []).filter(ni);
    rows.push(["⚡ Scalper", "☁ cloud", since(LVL.started), sc.length, rC(_netR(sc)), so.length]);
    const gc = (LVL.closed || []).filter(gi), go = (LVL.open || []).filter(gi);
    rows.push([`<span style="opacity:.75">↳ 💎 Gem</span>`, "☁ cloud", since(LVL.started), gc.length, rC(_netR(gc)), go.length]);
  } else rows.push(["⚡ Scalper", "☁ cloud", "—", "…", "", ""]);
  if (DEF) rows.push(["🛡 Defense", "☁ cloud", since(DEF.started), (DEF.closed || []).length, rC(_netR(DEF.closed || [])), (DEF.open || []).length]);
  else rows.push(["🛡 Defense", "☁ cloud", "—", "…", "", ""]);
  el.innerHTML = miniTable(["Agent", "Status", "Since", "Closed", "Net R", "Open"], rows,
    ["left", "left", "left", "right", "right", "right"]);
}

function renderBooks() {
  renderLedger(LVL, "lvl-audition");
  renderLedger(DEF, "def-audition");
  renderBookCombined();
  renderPocketLog();
  renderAgentRoster();
  renderTrades();
}

let tradesFilter = localStorage.getItem("tradesFilter") || "all";
function renderTrades() {
  const list = document.getElementById("trades-list");
  if (!list) return;
  const nm = (s) => s.replace(".NS", "").replace("^NSEBANK", "BankNifty").replace("^NSEI", "Nifty");
  // gather every trade from both ledgers, tagged by engine + open/closed
  const all = [];
  const add = (st, eng) => {
    if (!st) return;
    (st.open || []).forEach((t) => all.push({ ...t, eng, live: true }));
    (st.closed || []).forEach((t) => all.push({ ...t, eng, live: false }));
  };
  add(LVL, "scalp"); add(DEF, "defense");
  // Gem = the index trades living inside the Scalper ledger
  all.forEach((t) => { if (t.eng === "scalp" && t.sym && t.sym.startsWith("^")) t.eng = "gem"; });
  // 🏛 Pocket ⭐ swing trades (since Start) — same card shape (stocks only; no single
  // target — scale-out — so tgt is null and the card adapts)
  pocketTrades().forEach((t) => all.push({
    sym: t.symbol, eng: "pocket", d: t.side === "long" ? 1 : -1, tf: 120, lvl: "0.5–0.618",
    entry: t.entry, stop: t.sl, tgt: null, r: t.r, reason: "swing", pnl: null,
    risk_rs: null, ts: t.entry_ts, exit_ts: t.exit_ts, live: false,   // was t.ts — a field the writer never emits (audit C1)
  }));
  // detect collisions: same symbol + same entry ts across the two books
  const key = (t) => t.sym + "|" + t.ts;
  const seen = {};
  all.forEach((t) => { seen[key(t)] = (seen[key(t)] || 0) + 1; });
  all.forEach((t) => { t.collide = seen[key(t)] > 1; });

  const cnt = document.getElementById("trades-count");
  if (cnt) cnt.textContent = all.length ? `${all.length} total` : "none yet";

  // filter chips
  const fbox = document.getElementById("trades-filter");
  if (fbox && !fbox.dataset.built) {
    fbox.dataset.built = "1";
    [["all", "All"], ["pocket", "🏛 Pocket"], ["scalp", "⚡ Scalper"], ["defense", "🛡 Defense"],
     ["gem", "💎 Gem"], ["open", "Open"]].forEach(([v, l]) => {
      const b = document.createElement("button");
      b.className = "tf"; b.dataset.f = v; b.textContent = l;
      b.onclick = () => { tradesFilter = v; localStorage.setItem("tradesFilter", v); renderTrades(); };
      fbox.appendChild(b);
    });
  }
  if (fbox) [...fbox.children].forEach((b) => b.className = "tf" + (b.dataset.f === tradesFilter ? " active" : ""));

  let rows = all;
  if (tradesFilter === "open") rows = rows.filter((t) => t.live);
  else if (tradesFilter !== "all") rows = rows.filter((t) => t.eng === tradesFilter);
  // newest first (by exit for closed, entry for open)
  rows.sort((a, b) => (b.exit_ts || b.ts || "").localeCompare(a.exit_ts || a.ts || ""));

  if (!rows.length) { list.innerHTML = "<div>No trades yet in this view.</div>"; return; }
  // one aligned table instead of a pile of cards (owner, 2026-07-09: "so cluttered")
  const ct = (s) => (s || "").slice(5, 16).replace("T", " ");
  const durTx = (a, b) => {
    if (!a) return "—";
    const m = Math.max(0, ((b ? new Date(b) : new Date()) - new Date(a)) / 60000);
    return m < 60 ? `${Math.round(m)}m` : m < 1440 ? `${(m / 60).toFixed(1)}h` : `${(m / 1440).toFixed(1)}d`;
  };
  const ENG_SHORT = { pocket: "🏛", scalp: "⚡", defense: "🛡", gem: "💎" };
  window.TRADEMAP = {};
  const trows = rows.map((t, i) => {
    window.TRADEMAP[i] = t;
    const riskPts = Math.abs(t.entry - t.stop);
    let res, inr = "—";
    if (t.live) {
      const bd = getBookData(t.sym), lb = bd && bd.bars && bd.bars[bd.bars.length - 1];
      const cr = (lb && riskPts) ? ((lb.close - t.entry) / riskPts) * (t.d === 1 ? 1 : -1) : null;
      res = `<span class="pill">holding</span>${cr != null ? ` <b style="color:${cr >= 0 ? "#4ade80" : "#f87171"}">${cr >= 0 ? "+" : ""}${cr.toFixed(2)}R</b>` : ""}`;
      if (cr != null && t.risk_rs) inr = _inrCol(cr * t.risk_rs);
    } else {
      res = _rCol(t.r);
      if (t.pnl != null) inr = _inrCol(t.pnl);
      else if (t.risk_rs && t.r != null) inr = _inrCol(t.r * t.risk_rs);
    }
    const rr = (t.tgt != null && riskPts) ? `1:${(Math.abs(t.tgt - t.entry) / riskPts).toFixed(1)}` : "—";
    return [
      `<b>${nm(t.sym)}</b>${t.collide ? ` <span title="filled both books">⚡🛡</span>` : ""}`,
      ENG_SHORT[t.eng] || t.eng,
      t.d === 1 ? `<span style="color:#4ade80">long</span>` : `<span style="color:#f0556d">short</span>`,
      `${t.tf / 60}H@${t.lvl}`,
      res, inr, rr,
      `${ct(t.ts)} → ${t.live ? "…" : ct(t.exit_ts)} · ${durTx(t.ts, t.exit_ts)}`,
      t.live ? "—" : (t.reason || "—"),
      `<a href="#" onclick="showTradeChart(${i});return false" title="chart">📈</a>`];
  });
  list.innerHTML = `<div class="tblwrap">` + miniTable(
    ["stock", "book", "side", "combo", "result", "₹ P&L", "R:R", "in → out · held", "why", ""],
    trows, ["left", "left", "left", "left", "left", "right", "right", "left", "left", "center"]) + `</div>`;
}
const _inrCol = (n) => `<span style="color:${n >= 0 ? "#4ade80" : "#f87171"}">${n >= 0 ? "+" : "−"}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}</span>`;
function showTradeChart(i) { const t = window.TRADEMAP[i]; if (t) showBookChart(t.sym, t); }

// 🎯 Resting orders — the limit orders armed right now (an active fib's untouched traded
// levels, price still on the approach side). Each row opens the fib on the chart.
function renderResting() {
  const el = document.getElementById("resting-list");
  const cnt = document.getElementById("resting-count");
  if (!el) return;
  const orders = (RESTING && RESTING.orders) || [];
  if (cnt) cnt.textContent = orders.length;
  if (!orders.length) {
    el.innerHTML = RESTING
      ? `<p class="empty">Nothing armed right now — no active fib has an untouched traded level waiting. Orders appear here as fibs finalize.</p>`
      : "waiting for cloud data…";
    return;
  }
  window.RESTMAP = {};
  const rows = orders.map((o, i) => {
    window.RESTMAP[i] = o;
    const long = o.d === 1;
    const dist = o.price ? (o.entry - o.price) / o.price * 100 : 0;   // signed % price→entry
    const risk = Math.abs(o.entry - o.stop), rew = Math.abs(o.tgt - o.entry);
    const rr = risk ? (rew / risk).toFixed(1) : "—";
    return [
      ENG_BADGE[o.eng] || o.eng,
      `<b>${_nmS(o.sym)}</b>`,
      o.eng === "gamma" ? `${gLabel(o.lvl)}${o.wall != null ? ` → ${o.wall}` : ""}` : `${o.tf / 60}H@${o.lvl}`,
      long ? `<span style="color:#4ade80">long</span>` : `<span style="color:#f87171">short</span>`,
      o.entry, o.stop, o.tgt, `1:${rr}`,
      `<span style="opacity:.7">${dist >= 0 ? "+" : ""}${dist.toFixed(2)}%</span>`,
      `<a href="#" onclick="showRestChart(${i});return false" title="open chart">📈</a>`,
    ];
  });
  el.innerHTML = `<div class="tblwrap">` + miniTable(
    ["engine", "stock", "level", "side", "entry", "stop", "target", "R:R", "to fill", ""],
    rows, ["left", "left", "left", "left", "right", "right", "right", "right", "right", "center"]) + `</div>`;
}
function showRestChart(i) { const o = window.RESTMAP[i]; if (o) showBookChart(o.sym, o); }

let BOOKUNIV = null, BOOKCHARTS = null, BOOKHTF = null, RESTING = null, GAMMA = null, PGAMMA = null;
// 🎲 Gamma map (Phase 1: display the dealer-gamma flip level + walls per underlying).
// The gamma paper ENGINE is built on top of this once the live map is verified.
function renderGamma() {
  const el = document.getElementById("gamma-list");
  const cnt = document.getElementById("gamma-count");
  const gen = document.getElementById("gamma-gen");
  if (!el) return;
  // ---- the engine ledger: the equity curve that answers "does gamma pay?" ----
  const eng = document.getElementById("gamma-engine");
  if (eng) {
    if (PGAMMA) {
      const open = PGAMMA.open || [], closed = PGAMMA.closed || [];
      const netR = closed.reduce((s, t) => s + (t.r || 0), 0);
      const wr = closed.length ? Math.round(closed.filter((t) => t.r > 0).length / closed.length * 100) : 0;
      const _cap = PGAMMA.capital || 450000;
      const eq = PGAMMA.equity != null ? PGAMMA.equity : _cap;
      // P&L = realized only (capital top-ups are NOT profit); fallback for old files
      const pnl = PGAMMA.realized != null ? Math.round(PGAMMA.realized) : eq - _cap;
      const topup = (PGAMMA.capital_adds || []).reduce((s, a) => s + (a.amount || 0), 0);
      // the live-fidelity checks: option-R vs underlying-R, and 1-real-lot feasibility
      const optMatched = closed.filter((t) => t.opt_r != null);
      const optUndR = optMatched.reduce((s, t) => s + (t.r || 0), 0);
      const optOptR = optMatched.reduce((s, t) => s + (t.opt_r || 0), 0);
      const lotKnown = [...open, ...closed].filter((t) => t.lots != null);
      const lotFail = lotKnown.filter((t) => t.lots === 0);
      const modeR = (m) => closed.filter((t) => t.mode === m).reduce((s, t) => s + (t.r || 0), 0);
      // near-expiry (≤5 days) vs far — the gamma pull is strongest as expiry approaches
      const bucketR = (near) => closed.filter((t) => t.dte != null && (near ? t.dte <= 5 : t.dte > 5));
      const nearArr = bucketR(true), farArr = bucketR(false);
      const sumR = (a) => a.reduce((s, t) => s + (t.r || 0), 0);
      // "potential vs booked": how far winners actually ran vs the fixed 1.5R we booked —
      // tells us if the target is too tight (should R be variable, as discussed).
      const wins = closed.filter((t) => t.r > 0 && t.potential_r != null);
      const avgBooked = wins.length ? wins.reduce((s, t) => s + t.r, 0) / wins.length : 0;
      const avgPot = wins.length ? wins.reduce((s, t) => s + (t.potential_r || 0), 0) / wins.length : 0;
      // do pins survive a trend? pins on calm days vs runs days (appears once a runs day happens)
      const pins = closed.filter((t) => t.mode === "pin");
      const pinRunArr = pins.filter((t) => t.mkt === "runs");
      const pinCalmR = sumR(pins.filter((t) => t.mkt === "sticky"));
      window.GTMAP = {};
      let _gi = 0;
      const GCOLS = ["mode", "stock", "side", "entry", "stop", "target", "to exp", "result", "₹ P&L", "potential", "option", "opt ₹ (in→out)", "lots", ""];
      const GALIGN = ["left", "left", "left", "right", "right", "right", "right", "right", "right", "right", "left", "right", "right", "center"];
      const _inr = (n) => `<span style="color:${n >= 0 ? "#4ade80" : "#f87171"}">${n >= 0 ? "+" : "−"}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}</span>`;
      const gRow = (t) => {
        const i = _gi++; window.GTMAP[i] = t;
        const optDesc = t.opt_strike != null ? `${t.opt_strike} ${t.opt_type || ""}` : "—";
        const optOut = t.opt_exit != null ? t.opt_exit : (t._open && t.opt_cur != null ? `${t.opt_cur}<span style="opacity:.5"> live</span>` : null);
        const optPx = t.opt_entry != null
          ? `${t.opt_entry}${optOut != null ? ` → ${optOut}` : ""}` +
            (t.opt_r != null ? ` <span style="color:#38bdf8">(${t.opt_r > 0 ? "+" : ""}${(+t.opt_r).toFixed(1)}R)</span>` : "")
          : "—";
        // the rupees this trade actually made/lost: R × the ₹ risked at entry (owner ask)
        const pnlCell = (!t._open && t.r != null && t.risk_rs) ? _inr(t.r * t.risk_rs) : "—";
        return [`🎲 ${gLabel(t.mode)}`, `<b>${_nmS(t.sym)}</b>`,
          t.d === 1 ? `<span style="color:#4ade80">long</span>` : `<span style="color:#f0556d">short</span>`,
          t.entry, t.stop, t.tgt, t.dte != null ? `${t.dte}d` : "—",
          t._open ? `<span class="pill">holding</span>` : _rCol(t.r),
          pnlCell,
          t.potential_r != null ? `<span style="color:#a855f7">+${(+t.potential_r).toFixed(1)}R</span>` : "—",
          optDesc, optPx,
          // real F&O sizing: how many lots the ₹5k risk bought, and the lot size
          t.lots != null
            ? (t.lots > 0 ? `${t.lots} × ${t.lot_size || "?"}`
                          : `<span style="color:#fbbf24">0 ⚠ (1 lot > risk)</span>`)
            : "—",
          `<a href="#" onclick="showGammaChart(${i});return false" title="chart">📈</a>`];
      };
      // each section's SUMMARY carries its own totals — read the day/week without expanding
      const gSect = (label, list, openIt) => {
        if (!list.length) return "";
        const done = list.filter((t) => !t._open && t.r != null);
        const netR = done.reduce((s, t) => s + t.r, 0);
        const netInr = done.reduce((s, t) => s + t.r * (t.risk_rs || 0), 0);
        const stats = done.length
          ? ` <span style="font-weight:400">· <b style="color:${netR >= 0 ? "#4ade80" : "#f87171"}">${netR >= 0 ? "+" : ""}${netR.toFixed(1)}R</b> · ${_inr(netInr)}</span>`
          : "";
        return `<details class="sect" ${openIt ? "open" : ""}><summary>${label} <span class="pill">${list.length}</span>${stats}</summary>` +
          `<div class="tblwrap sect-body">` + miniTable(GCOLS, list.map(gRow), GALIGN) + `</div></details>`;
      };
      // live/holding shown first; exited trades bucketed by exit date so old history folds away
      const liveT = open.map((t) => ({ ...t, _open: true }));
      const exitedT = closed.slice().sort((a, b) => (b.exit_ts || "").localeCompare(a.exit_ts || ""));
      // CALENDAR buckets (owner ask): Today · Yesterday · each recent day by name ·
      // "Week of …" inside the month · then whole months. Only Today opens by default —
      // every fold's summary already shows count + net R + net ₹, so no scrolling needed.
      const _now = new Date();
      const _sTd = new Date(_now.getFullYear(), _now.getMonth(), _now.getDate());
      const gBucket = (ts) => {
        if (!ts) return "📅 (no exit time)";     // never bucket a null under "January 1970"
        const d = new Date(ts);
        const dd = new Date(d.getFullYear(), d.getMonth(), d.getDate());
        const days = Math.round((_sTd - dd) / 86400000);
        if (days <= 0) return "📅 Today";
        if (days === 1) return "📅 Yesterday";
        if (days < 7)
          return "📅 " + d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
        if (d.getFullYear() === _now.getFullYear() && d.getMonth() === _now.getMonth()) {
          const mon = new Date(dd); mon.setDate(dd.getDate() - ((dd.getDay() + 6) % 7));
          return "📅 Week of " + mon.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
        }
        return "📅 " + d.toLocaleString("en-IN", { month: "long", year: "numeric" });
      };
      const gOrder = [], gGroups = {};
      exitedT.forEach((t) => { const b = gBucket(t.exit_ts); if (!gGroups[b]) { gGroups[b] = []; gOrder.push(b); } gGroups[b].push(t); });
      // ---- STATUS STRIP: what is active vs on hold RIGHT NOW, and why (owner ask) ----
      const _running = PGAMMA.market_regime === "runs";
      const _mc = typeof DATA !== "undefined" && DATA && DATA.market_ctx;
      let _why = "";
      if (!_running && _mc && _mc.vix_hi) {
        _why = `<br><span style="opacity:.7">⚠ VIX is high but price ${PGAMMA.day_move_pct != null ? `has only moved ${PGAMMA.day_move_pct}%` : "isn't moving much"} — it takes VIX <b>plus</b> a ≥0.6% move (or a ±1% move alone) to count as running.</span>`;
      }
      const statusStrip =
        `<div style="margin:2px 0 10px;padding:7px 10px;border:1px solid #243150;border-radius:8px;font-size:12.5px;line-height:1.5">` +
        (_running
          ? `🥣 sticky trades: <b style="color:#4ade80">ACTIVE</b> <span style="opacity:.7">(with-the-run only — against-the-run go to the shadow book)</span><br>` +
            `⛰️ running trades: <b style="color:#4ade80">ACTIVE</b>`
          : `🥣 sticky trades: <b style="color:#4ade80">ACTIVE</b> <span style="opacity:.7">(all of them)</span><br>` +
            `⛰️ running trades: <b style="color:#fbbf24">ON HOLD</b> <span style="opacity:.7">(market is sticky — any that fire go to the shadow book, not the trade book)</span>`) +
        _why + `</div>`;
      eng.innerHTML = statusStrip +
        `<div style="margin:4px 0 10px"><b style="font-size:18px">₹${eq.toLocaleString("en-IN")}</b> ` +
        `<span style="color:${pnl >= 0 ? "#4ade80" : "#f87171"}">${pnl >= 0 ? "+" : "−"}₹${Math.abs(pnl).toLocaleString("en-IN")}</span> ` +
        `· net ${_rCol(+netR.toFixed(2))} · ${closed.length} closed (${wr}% win) · ${open.length} open` +
        (PGAMMA.started ? ` · since ${fmtAge(PGAMMA.started)}` : "") +
        `<br><span style="opacity:.65;font-size:12px">💰 capital ₹${_cap.toLocaleString("en-IN")} · risk 1% = ₹${Math.round(_cap * 0.01).toLocaleString("en-IN")}/trade — full-coverage paper sizing${topup ? " (re-based)" : ""}</span>` +
        (PGAMMA.market_regime ? `<br><span style="opacity:.65;font-size:12px">🌐 market (Nifty) right now: ${PGAMMA.market_regime === "runs" ? `⛰️ <b>running</b> — running trades ON${(PGAMMA.runs_via || []).length ? ` <span style="opacity:.8">(${PGAMMA.runs_via.join(" · ")})</span>` : ""}` : "🥣 <b>sticky</b> — running trades paused (sticky trades only)"}</span>` : "") +
        `</div>` +
        (() => {  // the 10 diagnostic lines fold into one card — digest on the summary
          const gateNets = ["counter-run", "market-sticky", "cooldown", "overnight-order"]
            .map((k) => (PGAMMA.shadow_closed || []).filter((t) => t.skip === k))
            .filter((g) => g.length)
            .map((g) => g.reduce((s2, t) => s2 + (t.r || 0), 0));
          const bad = gateNets.filter((n) => n >= 0).length;
          const digest = gateNets.length
            ? (bad ? `<span style="color:#fbbf24;font-weight:600">⚠ ${bad} gate${bad > 1 ? "s" : ""} to review</span>`
                   : `<span style="color:#4ade80;font-weight:600">all gates saving ✓</span>`)
            : "";
          return `<details class="sect"><summary>📊 study lines ${digest}</summary><div class="sect-body" style="font-size:12px;line-height:1.7">`;
        })() +
        `<span style="opacity:.75">🥣 sticky trades ${modeR("pin").toFixed(1)}R · ⛰️ running trades ${modeR("squeeze").toFixed(1)}R — which half carries it</span>` +
        `<br><span style="opacity:.65;font-size:12px">🗓 ≤5d to expiry ${sumR(nearArr).toFixed(1)}R (${nearArr.length}) · &gt;5d ${sumR(farArr).toFixed(1)}R (${farArr.length}) — is the edge only near expiry?</span>` +
        (wins.length ? `<br><span style="opacity:.65;font-size:12px">📏 winners booked <b>+${avgBooked.toFixed(1)}R</b> but could've reached <b style="color:#a855f7">+${avgPot.toFixed(1)}R potential</b> — ${avgPot > avgBooked * 1.4 ? "target may be too tight" : "1.5R target looks about right"}</span>` : "") +
        (pinRunArr.length ? `<br><span style="opacity:.65;font-size:12px">🥣 sticky trades: in a sticky market <b>${pinCalmR.toFixed(1)}R</b> vs in a running market <b>${sumR(pinRunArr).toFixed(1)}R</b> (${pinRunArr.length}) — do they survive when the market runs?</span>` : "") +
        (() => {  // EVERY gate carries a scorecard: what its blocked trades WOULD have done
          const line = (skip, label) => {
            const g = (PGAMMA.shadow_closed || []).filter((t) => t.skip === skip);
            if (!g.length) return "";
            const gr = g.reduce((s, t) => s + (t.r || 0), 0);
            return `<br><span style="opacity:.65;font-size:12px">⛔ gate: ${label} would've made <b>${gr >= 0 ? "+" : ""}${gr.toFixed(1)}R</b> (${g.length}) — ${gr < 0 ? "the gate is saving money ✓" : "the gate may be costing money — consider reverting"}</span>`;
          };
          return line("counter-run", "sticky trades blocked for fighting a running market") +
                 line("market-sticky", "running trades blocked in a sticky market") +
                 line("cooldown", "re-entries blocked within 60m of a stop-out") +
                 line("overnight-order", "yesterday's leftover orders (mornings start fresh)") +
                 line("lot-too-big", "names where 1 lot exceeds the trade budget") +
                 line("runs-aligned", "sticky trades blocked in a running market (weather table)") +
                 line("vix-high", "sticky trades blocked while VIX is high (weather table)") +
                 line("day-breaker", "trades blocked after a −5R day (circuit breaker)") +
                 (() => {  // 🧪 BE-study: twins (breakeven@+0.75R) raced against their real siblings
                   const tw = (PGAMMA.shadow_closed || []).filter((t) => t.skip === "study-be75");
                   if (!tw.length) return "";
                   const key = (t) => t.sym + "|" + t.ts;
                   const real = {};
                   (PGAMMA.closed || []).forEach((t) => { real[key(t)] = t; });
                   const pairs = tw.filter((t) => real[key(t)]);
                   if (!pairs.length) return "";
                   const twR = pairs.reduce((s2, t) => s2 + (t.r || 0), 0);
                   const reR = pairs.reduce((s2, t) => s2 + (real[key(t)].r || 0), 0);
                   return `<br><span style="opacity:.65;font-size:12px">🧪 breakeven study: twins <b>${twR >= 0 ? "+" : ""}${twR.toFixed(1)}R</b> vs the same real trades <b>${reR >= 0 ? "+" : ""}${reR.toFixed(1)}R</b> (${pairs.length} pairs) — ${twR > reR ? "breakeven may help ⚠" : "the pure ladder is winning ✓"}</span>`;
                 })();
        })() +
        (optMatched.length ? `<br><span style="opacity:.65;font-size:12px">🧾 option-price check (paper): stock math says <b>${optUndR >= 0 ? "+" : ""}${optUndR.toFixed(1)}R</b>, the actual option paid <b style="color:#38bdf8">${optOptR >= 0 ? "+" : ""}${optOptR.toFixed(1)}R</b> (${optMatched.length} matched) — do they agree?</span>` : "") +
        (lotKnown.length ? `<br><span style="opacity:.65;font-size:12px">📦 real lots: ${lotFail.length ? `<b style="color:#fbbf24">${lotFail.length} of ${lotKnown.length}</b> fills where even 1 lot exceeds the ₹10k budget (routed to shadow)` : `all ${lotKnown.length} sized fills fit ≥1 real lot`}</span>` : "") +
        `</div></details>` +
        gSect("🔴 Live / holding", liveT, true) +
        (gOrder.length
          ? gOrder.map((b) => gSect(b, gGroups[b], b === "📅 Today")).join("")
          : (liveT.length ? "" : `<p class="empty" style="margin:4px 0">No gamma trades yet — the engine started ${PGAMMA.started ? fmtAge(PGAMMA.started) : "now"} and fills forward as price reaches the armed levels.</p>`));
    } else {
      eng.innerHTML = `<p class="set-note">Gamma engine ledger loads with the next scan…</p>`;
    }
  }
  // ---- the gamma engine's OWN armed orders (its home, not the shared Resting tab) ----
  const armEl = document.getElementById("gamma-armed");
  const armCnt = document.getElementById("gamma-armed-count");
  if (armEl) {
    const armed = (PGAMMA && PGAMMA.armed) || [];
    if (armCnt) armCnt.textContent = armed.length;
    if (!armed.length) {
      armEl.innerHTML = PGAMMA ? `<p class="empty">Nothing armed right now — orders appear as fibs… er, walls line up.</p>` : "waiting for cloud data…";
    } else {
      window.GAMAP = {};
      const rows = armed.slice(0, 60).map((o, i) => {
        window.GAMAP[i] = o;
        const dist = o.price ? (o.entry - o.price) / o.price * 100 : 0;
        const risk = Math.abs(o.entry - o.stop), rew = Math.abs(o.tgt - o.entry);
        const rr = risk ? (rew / risk).toFixed(1) : "—";
        return [`🎲 ${gLabel(o.mode)}${o.wall != null ? ` → ${o.wall}` : ""}`, `<b>${_nmS(o.sym)}</b>`,
          o.d === 1 ? `<span style="color:#4ade80">long</span>` : `<span style="color:#f0556d">short</span>`,
          o.entry, o.stop, o.tgt, o.dte != null ? `${o.dte}d` : "—", `1:${rr}`,
          `<span style="opacity:.7">${dist >= 0 ? "+" : ""}${dist.toFixed(2)}%</span>`,
          `<a href="#" onclick="showGammaArmed(${i});return false" title="chart">📈</a>`];
      });
      armEl.innerHTML = `<div class="tblwrap">` + miniTable(
        ["order", "stock", "side", "entry", "stop", "target", "to exp", "R:R", "to fill", ""], rows,
        ["left", "left", "left", "right", "right", "right", "right", "right", "right", "center"]) + `</div>`;
    }
  }
  const maps = (GAMMA && GAMMA.maps) || {};
  const syms = Object.keys(maps);
  if (cnt) cnt.textContent = syms.length;
  if (gen && GAMMA && GAMMA.generated) gen.textContent = "updated " + fmtAge(GAMMA.generated);
  if (!syms.length) {
    el.innerHTML = GAMMA
      ? `<p class="empty">No gamma maps yet — they compute ~every 30 min from the live Fyers option chain during market hours.</p>`
      : "waiting for cloud data…";
    return;
  }
  // index first, then by biggest wall strength
  syms.sort((a, b) => (a.startsWith("^") ? -1 : 0) - (b.startsWith("^") ? -1 : 0)
    || (((maps[b].walls || [])[0] || {}).strength || 0) - (((maps[a].walls || [])[0] || {}).strength || 0));
  const rows = syms.map((s) => {
    const m = maps[s];
    const pos = m.regime === "positive";
    const regTxt = pos
      ? `<span style="color:#4ade80">🥣 sticky</span>`
      : `<span style="color:#f0556d">⛰️ running</span>`;
    const wallTxt = (m.walls || []).slice(0, 3)
      .map((w) => `${w.strike}`).join(" · ") || "—";
    const flip = m.flip == null ? "—" : m.flip;
    const dexp = m.expiry_days == null ? "" : ` · ${m.expiry_days}d`;
    return [_nmS(s), m.spot, flip, regTxt, wallTxt, `${m.sigma != null ? (m.sigma * 100).toFixed(0) + "%" : "—"}${dexp}`];
  });
  el.innerHTML = `<div class="tblwrap">` + miniTable(
    ["underlying", "spot", "flip", "regime", "top walls", "iv"],
    rows, ["left", "right", "right", "left", "left", "right"]) + `</div>`;
}
function showUnivChart(i) { const s = window.UNIVMAP[i]; if (s) showBookChart(s + ".NS", null); }
function showGammaChart(i) { const t = window.GTMAP[i]; if (t) showBookChart(t.sym, t); }
function showGammaArmed(i) { const o = window.GAMAP[i]; if (o) showBookChart(o.sym, { ...o, eng: "gamma" }); }

// book chart data is split across two files: book_charts.json (recent 5m + levels,
// refreshed every scan → live 5m chart) and book_charts_htf.json (deep 1H/2H history,
// ~twice/hr). Merge them per symbol so drawBookChart sees one object with all series.
function getBookData(symbol) {
  const pick = (M) => M && (M[symbol] || M[symbol + ".NS"] || M[symbol.replace(".NS", "")]);
  const b = pick(BOOKCHARTS), h = pick(BOOKHTF);
  if (!b && !h) return null;
  return Object.assign({}, b || {}, h || {});   // htf adds bars60 / bars120
}

// Get a trade's fib leg (origin/top). New fills store it exactly. For older Defense fills
// saved before leg-capture, recover it from entry + target — Defense targets are FIXED fib
// ratios, so the algebra is exact. (Scalper's "struct" target has no fixed ratio, so those
// can't be reconstructed and just show entry/stop/target.)
const DEF_TGT_RATIO = { "60|0.786": 0, "60|0.886": 0.618, "120|0.886": 0.618 };
function reconstructLeg(t) {
  if (t.origin != null && t.top != null) return { origin: t.origin, top: t.top, exact: true };
  if (t.eng !== "defense" || t.tgt == null || t.entry == null) return null;
  const Rt = DEF_TGT_RATIO[`${t.tf}|${t.lvl}`], Re = +t.lvl;
  if (Rt == null || Re === Rt) return null;
  const rng = (t.entry - t.tgt) / (Re - Rt);      // signed (origin - top)
  const top = t.entry - rng * Re, origin = top + rng;
  return { origin: +origin.toFixed(2), top: +top.toFixed(2), exact: false };
}
function renderUniverse() {
  const el = document.getElementById("univ-list");
  if (!el) return;
  const nm = (s) => s.replace(".NS", "").replace("^NSEBANK", "BankNifty").replace("^NSEI", "Nifty");
  const ranked = (BOOKUNIV && BOOKUNIV.stocks_ranked) ? BOOKUNIV.stocks_ranked : [];
  const inUniverse = new Set((BOOKUNIV && BOOKUNIV.top) ? BOOKUNIV.top : []);
  const list = ranked.filter((r) => inUniverse.has(r.sym));   // already liquidity-sorted
  const cnt = document.getElementById("univ-count");
  if (cnt) cnt.textContent = list.length ? `${list.length} stocks` : "loading…";
  if (!list.length) {
    el.innerHTML = "<div>waiting for the next cloud scan…</div>";
    return;
  }
  const inr = (n) => n >= 1000 ? "₹" + (n / 1000).toFixed(1) + "k cr" : "₹" + Math.round(n) + " cr";
  window.UNIVMAP = {};   // index-map pattern — symbols like M&M must never be string-injected into onclick
  const rows = list.map((r, i) => {
    window.UNIVMAP[i] = r.sym;
    return `<tr style="border-bottom:1px solid #1b2740">` +
      `<td style="padding:3px 8px;opacity:.6">${i + 1}</td>` +
      `<td style="padding:3px 8px"><b>${nm(r.sym)}</b></td>` +
      `<td style="padding:3px 8px;text-align:right">${inr(r.val_cr)}<span style="opacity:.5">/day</span></td>` +
      `<td style="padding:3px 8px;text-align:right">` +
      `<button class="tf" style="padding:1px 8px" onclick="showUnivChart(${i})">📈 chart</button></td>` +
      `</tr>`;
  }).join("");
  el.innerHTML =
    `<table style="border-collapse:collapse;width:100%;white-space:nowrap"><thead><tr style="opacity:.7">` +
    `<td style="padding:3px 8px">#</td><td style="padding:3px 8px">Stock</td>` +
    `<td style="padding:3px 8px;text-align:right">Traded value</td>` +
    `<td style="padding:3px 8px;text-align:right">Chart</td></tr></thead><tbody>${rows}</tbody></table>`;
}

// resample a stored 5m series up to tfMin-minute candles (buckets aligned to IST midnight
// so 1H/2H candles read naturally on the IST axis). Never goes finer than the stored bars.
function resampleBars(src, tfMin) {
  if (!src || !src.length) return src || [];
  const IST = 19800;
  const base = src.length > 1 ? Math.max(1, Math.round((src[1].time - src[0].time) / 60)) : 5;
  if (tfMin <= base) return src;
  const bkt = tfMin * 60, out = [];
  let cur = null;
  for (const b of src) {
    const start = Math.floor((b.time + IST) / bkt) * bkt - IST;
    if (!cur || cur.time !== start) {
      if (cur) out.push(cur);
      cur = { time: start, open: b.open, high: b.high, low: b.low, close: b.close };
    } else {
      cur.high = Math.max(cur.high, b.high);
      cur.low = Math.min(cur.low, b.low);
      cur.close = b.close;
    }
  }
  if (cur) out.push(cur);
  return out;
}

// pick the right candle series for a book chart at timeframe tf: use the server-emitted
// native 1H/2H series (deep history) when present, else resample the 5m series (15m, or
// old data with no native series). Falls back gracefully for pre-multi-series feeds.
function bookBarsFor(bc, tf) {
  if (!bc) return [];
  // fall through on EMPTY native series too, not just missing ones (audit m3a)
  const pick = (a, b) => (a && a.length ? a : b);
  const src = tf === "120" ? pick(bc.bars120, bc.bars)
    : tf === "60" ? pick(bc.bars60, bc.bars)
      : bc.bars;
  return resampleBars(src || [], +tf);
}

function setBookTF(tf) {
  if (!bookCtx) return;
  bookCtx.tf = String(tf);
  renderTFButtons();
  drawBookChart();
}

// open a book chart (Trades / Universe): store context, switch the on-chart TF buttons to
// book mode, then draw. Changing TF re-runs drawBookChart() with a fresh resample.
function showBookChart(symbol, trade) {
  chartMode = "book";
  curSymbol = null;                        // detach leg-chart state
  const tf = trade && trade.eng === "gamma" ? (trade.mode === "squeeze" ? "15" : "5")  // pins decide on 5m, squeeze on 15m
    : (trade && BOOK_TFS.includes(String(trade.tf)) ? String(trade.tf) : "120");
  bookCtx = { symbol, trade, tf };
  renderTFButtons();
  drawBookChart();
}

function drawBookChart() {
  const { symbol, trade, tf } = bookCtx;
  const bc = getBookData(symbol);              // merged 5m + deep 1H/2H
  const nmc = symbol.replace(".NS", "").replace("^NSEBANK", "BankNifty").replace("^NSEI", "Nifty");
  const sec = document.getElementById("chart-section");
  sec.hidden = false;
  if (typeof chartCollapsed !== "undefined" && chartCollapsed) setChartCollapsed(false);
  const ap = document.getElementById("adjust-panel"); if (ap) ap.hidden = true;
  const _isGamma = trade && trade.eng === "gamma";
  const _tradeLabel = !trade ? "" : _isGamma
    ? ` — ${ENG_BADGE.gamma} ${trade.d === 1 ? "long" : "short"} ${gLabel(trade.mode || trade.lvl)}${trade.wall != null ? ` → wall ${trade.wall}` : ""}`
    : ` — ${ENG_BADGE[trade.eng] || ""} ${trade.d === 1 ? "long" : "short"} ${trade.tf / 60}H@${trade.lvl}`;
  document.getElementById("chart-symbol").textContent = nmc + _tradeLabel +
    `  · ${tfLabel(tf)} chart`;
  const tv = document.getElementById("tv-link");
  if (tv) tv.href = "https://www.tradingview.com/chart/?symbol=NSE:" + nmc.toUpperCase();
  const mount = document.getElementById("chart");
  mount.innerHTML = "";
  if (chartObj) { chartObj.remove(); chartObj = null; }
  const leg = document.getElementById("legend");
  if (!bc || !bc.bars || !bc.bars.length || typeof LightweightCharts === "undefined") {
    mount.innerHTML = `<p class="empty">No chart for ${nmc} yet — book charts appear after the next cloud scan (during market hours).</p>`;
    if (leg) leg.innerHTML = "";
    sec.scrollIntoView({ behavior: "smooth" }); return;
  }
  const IST = 19800, p2 = (n) => String(n).padStart(2, "0");
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const istTick = (t, ty) => { const d = new Date((t + IST) * 1000);
    if (ty === 0) return String(d.getUTCFullYear()); if (ty === 1) return MON[d.getUTCMonth()];
    if (ty === 2) return String(d.getUTCDate()); return p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()); };
  const istFull = (t) => { const d = new Date((t + IST) * 1000);
    return `${d.getUTCDate()} ${MON[d.getUTCMonth()]} '${String(d.getUTCFullYear()).slice(2)}  ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())} IST`; };
  chartObj = LightweightCharts.createChart(mount, {
    autoSize: true, layout: { background: { color: "#131c2e" }, textColor: "#e6edf6" },
    grid: { vertLines: { color: "#1b2740" }, horzLines: { color: "#1b2740" } },
    timeScale: { timeVisible: true, borderColor: "#243150", tickMarkFormatter: istTick },
    rightPriceScale: { borderColor: "#243150" }, crosshair: { mode: 0 },
    localization: { timeFormatter: istFull },
  });
  const series = chartObj.addCandlestickSeries({
    upColor: "#2ec27e", downColor: "#f0556d", wickUpColor: "#2ec27e",
    wickDownColor: "#f0556d", borderVisible: false });
  bookSeries = series;                 // handle so the 60s auto-refresh can tick it live
  const vbars = bookBarsFor(bc, tf);
  series.setData(vbars);
  const LS = LightweightCharts.LineStyle;
  // track every drawn line so the price scale can be expanded to include them — a resting
  // order's fib sits BELOW the current candles, so without this the levels scroll off-screen.
  const plPrices = [];
  const pl = (price, color, title) => { if (price != null) { plPrices.push(price); series.createPriceLine(
    { price, color, lineWidth: 1, lineStyle: LS.Dashed, axisLabelVisible: true, title }); } };
  const bars = vbars, snap = (t) => {   // nearest bar time so markers always show
    let best = bars[0].time, dm = Infinity;
    for (const b of bars) { const d = Math.abs(b.time - t); if (d < dm) { dm = d; best = b.time; } }
    return best;
  };
  if (trade) {
    // the exact fib leg this trade fired from — the full labelled ratio ladder plus the
    // leg anchored in time (origin candle → top candle) — so the drawing is verifiable
    // against the candles. Only on trades taken with the leg-aware scan; older trades
    // fall back to entry/stop/target only.
    let hasFib = false, O = null, T = null, legExact = true;
    const mk = [];
    const legRec = reconstructLeg(trade);
    if (legRec) {
      hasFib = true; O = legRec.origin; T = legRec.top; legExact = legRec.exact;
      // ratio 0 = top (impulse end), ratio 1 = origin (impulse start). Any level:
      //   price = top + (origin - top) * ratio   — works for longs and shorts alike.
      const fibP = (L) => T + (O - T) * L;
      // use the trade's own stored level prices where available (exact), else compute
      const lvP = (k) => (trade.lv && trade.lv[k] != null) ? trade.lv[k] : fibP(+k);
      const rows = [
        ["0 (top)", T, "#94a3b8"],
        ["0.382", fibP(0.382), "#8b5cf6"],
        ["0.5", lvP("0.5"), "#c084fc"],
        ["0.618", lvP("0.618"), "#a855f7"],
        ["0.786", lvP("0.786"), "#f0abfc"],
        ["0.886", lvP("0.886"), "#e879f9"],
        ["1.0 (origin)", O, "#64748b"],
      ];
      for (const [lab, price, c] of rows) if (price != null) pl(price, c, `${lab}  ${(+price).toFixed(2)}`);
      // owner's reverse-fib LADDER (2026-07-09): for DEEP entries (0.786/0.886) measure
      // the RETRACEMENT leg (top → entry) and mark the bounce rungs — .382/.5/.618 are
      // the classic exits; a rung BROKEN is a promotion to the next; through .886 the
      // move "flies". Each rung labelled with its R. Display only — exits unchanged.
      if (trade.lvl != null && +trade.lvl >= 0.7 && trade.entry != null && trade.stop != null) {
        const risk0 = Math.abs(trade.entry - trade.stop) || null;
        const fall = T - trade.entry;                     // signed: works for longs and shorts
        for (const [bf, blab, bc2] of [
          [0.382, "bounce 38%", "#22d3ee"], [0.5, "bounce ½", "#22d3ee"],
          [0.618, "bounce .618", "#22d3ee"], [0.786, "bounce .786", "#0e7490"],
          [0.886, "bounce .886 → fly", "#0e7490"]]) {
          const p = trade.entry + bf * fall;
          const rTxt = risk0 ? ` +${(Math.abs(p - trade.entry) / risk0).toFixed(1)}R` : "";
          pl(p, bc2, `${blab}${rTxt}  ${p.toFixed(2)}`);
        }
      }
      // anchor the leg in time only when the pivot timestamps were stored (new fills):
      // a dotted line origin → top + labelled pivot markers
      const oT = trade.origin_ts ? snap(Math.floor(new Date(trade.origin_ts).getTime() / 1000)) : null;
      const tT = trade.top_ts ? snap(Math.floor(new Date(trade.top_ts).getTime() / 1000)) : null;
      if (oT != null && tT != null && oT !== tT) {
        const legLine = chartObj.addLineSeries({ color: "#cbd5e1", lineWidth: 1,
          lineStyle: LS.Dotted, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        legLine.setData([{ time: oT, value: O }, { time: tT, value: T }].sort((a, b) => a.time - b.time));
        const up = trade.d === 1;
        mk.push({ time: oT, position: up ? "belowBar" : "aboveBar", color: "#cbd5e1", shape: "circle", text: `${trade.tf / 60}H start` });
        mk.push({ time: tT, position: up ? "aboveBar" : "belowBar", color: "#cbd5e1", shape: "circle", text: "leg end" });
      }
    }
    if (_isGamma && trade.wall != null) pl(trade.wall, "#e879f9", "🎲 wall " + trade.wall);
    pl(trade.entry, "#60a5fa", "entry " + trade.entry);
    pl(trade.stop, "#f87171", "stop " + trade.stop);
    pl(trade.tgt, "#4ade80", "target " + trade.tgt);
    if (trade.ts) mk.push({ time: snap(Math.floor(new Date(trade.ts).getTime() / 1000)),
      position: "belowBar", color: "#60a5fa", shape: "arrowUp", text: "IN" });
    if (trade.exit_ts) mk.push({ time: snap(Math.floor(new Date(trade.exit_ts).getTime() / 1000)),
      position: "aboveBar", color: "#fde047", shape: "arrowDown", text: "OUT" });
    if (mk.length) series.setMarkers(mk.sort((a, b) => a.time - b.time));
    const tgtTxt = trade.tgt == null ? "scale-out" :
      `${trade.tgt} <b>R:R</b> 1:${Math.abs(trade.entry - trade.stop) ? (Math.abs(trade.tgt - trade.entry) / Math.abs(trade.entry - trade.stop)).toFixed(1) : "—"}`;
    if (leg) leg.innerHTML = `<b>${ENG_BADGE[trade.eng] || trade.eng}${_isGamma ? ` · ${gLabel(trade.mode || trade.lvl)}${trade.wall != null ? ` → wall ${trade.wall}` : ""}` : ` · ${trade.tf / 60}H leg`}</b> · ` +
      `entry ${trade.entry} · stop ${trade.stop} · target ${tgtTxt}` +
      (trade.r != null ? ` · result <b>${trade.r >= 0 ? "+" : ""}${trade.r}R</b>`
        : (trade.ts ? " · <b>holding</b>" : " · <b>resting (not yet filled)</b>")) +
      (hasFib ? ` · <span style="opacity:.7">leg ${O} → ${T}${legExact ? "" : " (reconstructed)"}</span>` : "");
  } else if (bc.levels) {
    const L = bc.levels;
    const f382 = (L.top != null && L.origin != null) ? L.top + (L.origin - L.top) * 0.382 : null;
    const rows = [
      ["0 (top)", L.top, "#94a3b8"],
      ["0.382", f382, "#8b5cf6"],
      ["0.5", L["0.5"], "#c084fc"],
      ["0.618", L["0.618"], "#a855f7"],
      ["0.786", L["0.786"], "#f0abfc"],
      ["0.886", L["0.886"], "#e879f9"],
      ["1.0 (origin)", L.origin, "#64748b"],
    ];
    for (const [lab, price, c] of rows) if (price != null) pl(price, c, `${lab}  ${(+price).toFixed(2)}`);
    if (leg) leg.innerHTML = "Current 2H fib: 0 (top) → 1.0 (origin) · .382 / .5 / .618 / .786 / .886";
  }
  // expand the price scale to include every drawn line, so fib levels below (or above) the
  // current candles stay visible — essential for resting orders, whose fib is off-price.
  if (plPrices.length) {
    const lo = Math.min(...plPrices), hi = Math.max(...plPrices);
    series.applyOptions({
      autoscaleInfoProvider: (orig) => {
        const r = orig();
        const pr = r && r.priceRange ? r.priceRange : { minValue: lo, maxValue: hi };
        return { priceRange: { minValue: Math.min(pr.minValue, lo), maxValue: Math.max(pr.maxValue, hi) },
          margins: r ? r.margins : undefined };
      },
    });
  }
  sec.scrollIntoView({ behavior: "smooth" });
}

const ENG_BADGE = { pocket: "🏛 Pocket", scalp: "⚡ Scalper", defense: "🛡 Defense", gem: "💎 Gem", gamma: "🎲 Gamma" };
// THE vocabulary (owner, final): market is 🥣 sticky or ⛰️ running; trades are "sticky"
// and "running" trades. pin/squeeze survive only as invisible code labels, never shown.
const gLabel = (m) => m === "pin" ? "sticky" : m === "squeeze" ? "running" : m;
function tradeCard(nm, t, idx) {
  const noTgt = t.tgt == null;   // Pocket scales out — no single target level
  const long = t.d === 1;
  const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
  // level ladder: stop / entry / target mapped to a vertical bar (skip target if none)
  const pts = noTgt ? [t.stop, t.entry] : [t.stop, t.tgt, t.entry];
  const lo = Math.min(...pts), hi = Math.max(...pts);
  const span = (hi - lo) || 1;
  const y = (p) => 92 - 84 * (p - lo) / span;   // 8..92 within a 100-tall svg
  let exitP = null;
  if (!t.live && typeof t.r === "number") {      // reconstruct exit price from R (gross≈net+cost)
    const risk = Math.abs(t.entry - t.stop);
    exitP = long ? t.entry + (t.r + 0.05) * risk : t.entry - (t.r + 0.05) * risk;
  }
  const line = (p, col, lab) => p == null ? "" :
    `<line x1="26" y1="${y(p).toFixed(1)}" x2="150" y2="${y(p).toFixed(1)}" stroke="${col}" stroke-width="1.5"/>` +
    `<text x="154" y="${(y(p) + 3).toFixed(1)}" fill="${col}" font-size="9">${lab} ${p}</text>`;
  const ladder =
    `<svg viewBox="0 0 250 100" width="230" height="92" style="flex:0 0 auto">` +
    line(t.tgt, "#4ade80", "target") +
    line(t.entry, "#60a5fa", "entry") +
    line(t.stop, "#f87171", "stop") +
    (exitP != null ? `<circle cx="88" cy="${y(exitP).toFixed(1)}" r="3" fill="#fde047"/>` +
      `<text x="94" y="${(y(exitP) + 3).toFixed(1)}" fill="#fde047" font-size="9">exit ${exitP.toFixed(1)}</text>` : "") +
    `</svg>`;
  const win = !t.live && t.r > 0;
  const statusPill = t.live
    ? `<span class="pill">holding</span>`
    : `<span class="pill ${win ? "win" : ""}">${t.reason}${win ? "" : ""}</span>`;
  const fmt = (s) => (s || "").replace("T", " ").slice(0, 16);
  const dur = (a, b) => {                       // human duration between two ISO stamps
    if (!a) return "—";
    const m = Math.max(0, ((b ? new Date(b) : new Date()) - new Date(a)) / 60000);
    if (m < 60) return `${Math.round(m)} min`;
    if (m < 60 * 24) return `${(m / 60).toFixed(1)} hr`;
    return `${(m / 60 / 24).toFixed(1)} days`;
  };
  // instrument (options for level books; Pocket is the future+hedge swing)
  const opt = t.eng === "pocket" ? "future + protective option (swing)"
    : (long ? "call (slightly-ITM)" : "put (slightly-ITM)");
  const optNote = t.eng === "pocket" ? "" : ` <span style="opacity:.6">· real strike/spread with Fyers option chain (coming)</span>`;
  // R:R geometry (Pocket scales out — no single target)
  const riskPts = Math.abs(t.entry - t.stop), rewPts = noTgt ? null : Math.abs(t.tgt - t.entry);
  const rr = (noTgt || !riskPts) ? "—" : (rewPts / riskPts).toFixed(1);
  // LIVE R for an open trade, off the freshest 5m close (owner ask, 2026-07-09)
  let curR = null, curPx = null;
  if (t.live && riskPts) {
    const bd = getBookData(t.sym), lb = bd && bd.bars && bd.bars[bd.bars.length - 1];
    if (lb) { curPx = lb.close; curR = (curPx - t.entry) / riskPts * (long ? 1 : -1); }
  }
  const curTxt = curR == null ? "" :
    ` · now <b style="color:${curR >= 0 ? "#4ade80" : "#f87171"}">${curR >= 0 ? "+" : ""}${curR.toFixed(2)}R</b>`;
  const hasPnl = t.pnl != null;
  const pnlB = hasPnl ? ` · <b style="color:${t.pnl >= 0 ? "#4ade80" : "#f87171"}">${t.pnl >= 0 ? "+" : "−"}${inr(Math.abs(t.pnl))}</b>` : "";
  const pnlLine = t.live
    ? `<div>Status: <b>open</b>${curTxt}${curPx != null ? ` <span style="opacity:.6">(px ${curPx})</span>` : ""} · held ${dur(t.ts, null)} so far</div>`
    : `<div><b style="color:${t.r >= 0 ? "#4ade80" : "#f87171"}">${t.r >= 0 ? "+" : ""}${t.r}R</b>${pnlB} · exit reason: ${t.reason}</div>`;
  const rowsHtml =
    `<div>Instrument: <b>${nm(t.sym)} ${opt}</b>${optNote}</div>` +
    `<div>Entry <b>${t.entry}</b> · Stop <b>${t.stop}</b> · Target <b>${noTgt ? "scale-out" : t.tgt}</b></div>` +
    `<div>Risk ${riskPts.toFixed(2)} pts${t.risk_rs != null ? ` (${inr(t.risk_rs)})` : ""}` +
    `${noTgt ? "" : ` · Reward ${rewPts.toFixed(2)} pts · <b>R:R 1:${rr}</b>`}</div>` +
    `<div>Entered <b>${fmt(t.ts)}</b>${t.live ? "" : ` · Exited <b>${fmt(t.exit_ts)}</b> · held <b>${dur(t.ts, t.exit_ts)}</b>`}</div>` +
    pnlLine;
  // compact always-visible metrics line (shown even when the card is collapsed)
  const ct = (s) => (s || "").slice(5, 16).replace("T", " ");   // MM-DD HH:MM
  const rrTxt = noTgt ? "" : ` · R:R 1:${rr}`;
  const summaryInfo = t.live
    ? `<div style="font-size:.9em;opacity:.9;margin-top:2px">holding${curTxt} · in ${ct(t.ts)}` +
      `${noTgt ? "" : ` · target ${t.tgt}`}${rrTxt} · held ${dur(t.ts, null)}</div>`
    : `<div style="font-size:.9em;margin-top:2px">` +
      `<b style="color:${t.r >= 0 ? "#4ade80" : "#f87171"}">${t.r >= 0 ? "+" : ""}${t.r}R</b>${pnlB}${rrTxt} · ` +
      `in ${ct(t.ts)} → out ${ct(t.exit_ts)} (${dur(t.ts, t.exit_ts)}) · ${t.reason}</div>`;
  return `<details class="set-note" style="border-left:3px solid ${win ? "#4ade80" : t.live ? "#60a5fa" : "#f87171"};padding-left:8px;margin:6px 0">` +
    `<summary><b>${nm(t.sym)}</b> · ${ENG_BADGE[t.eng]} · ${long ? "long" : "short"} · ${t.tf / 60}H@${t.lvl}` +
    `${t.collide ? " · ⚡+🛡 both books" : ""} ${statusPill}${summaryInfo}</summary>` +
    `<div style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap;margin-top:6px">${ladder}` +
    `<div style="min-width:210px;flex:1">${rowsHtml}` +
    `<button class="tf" style="margin-top:6px" onclick="showTradeChart(${idx})">📈 Expand chart</button></div></div>` +
    (t.collide ? `<div style="margin-top:4px;opacity:.8">Same touch filled both books — one harvests the quick move, the other holds for days.</div>` : "") +
    `</details>`;
}

let BOOKBT = null;   // the Book's yearly backtest (docs/backtest_book.json) — static
function renderBookBacktest() {
  const el = document.getElementById("bt-book");
  if (!el || !BOOKBT) return;
  const E = BOOKBT.engines || {};
  const cols = [["old", "Old (L+S)", "pocket_old"], ["pocket", "🏛 Pocket", "pocket"],
                ["scalp", "⚡ Scalper", "scalper"], ["def", "🛡 Defense", "defense"],
                ["book", "📚 Book", "book"]];
  const cell = (v) => v === undefined ? "–" :
    `<td style="text-align:right;padding:2px 8px;color:${v < 0 ? "#f87171" : "#4ade80"}">` +
    `${v >= 0 ? "+" : ""}${v.toFixed(1)}</td>`;
  let rows = (BOOKBT.years || []).map((y) =>
    `<tr><td style="padding:2px 8px">${y}</td>` +
    cols.map(([, , k]) => cell((E[k] || {})[y])).join("") + "</tr>").join("");
  const sum = (k) => (BOOKBT.years || []).reduce((s, y) => s + ((E[k] || {})[y] || 0), 0);
  rows += `<tr style="border-top:1px solid #334"><td style="padding:2px 8px"><b>Total</b></td>` +
    cols.map(([, , k]) => cell(sum(k))).join("") + "</tr>";
  el.innerHTML =
    `<table style="border-collapse:collapse;white-space:nowrap"><thead><tr>` +
    `<td style="padding:2px 8px"><b>Year</b></td>` +
    cols.map(([, h]) => `<td style="text-align:right;padding:2px 8px"><b>${h}</b></td>`).join("") +
    `</tr></thead><tbody>${rows}</tbody></table>`;
}
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
  const p = RISK_PLANS[AG.risk] || RISK_PLANS["1"];   // never crash on a foreign risk value
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
    return;   // the History ⭐ log is rendered separately (renderPocketLog), not here
  }
  // paper ledger: the persistent cloud log (never rolls off; kept by the cron whether
  // you're online or not) — fall back to the rolling history until the first log lands.
  const src = (PL && PL.trades && PL.trades.length)
    ? PL.trades.map((t) => ({ ...t, ctx: { pass: t.ctx_pass } }))
    : (m.history || []);
  let tr = src.filter((h) => h.ctx && h.ctx.pass && !isIndex(h.symbol) && h.entry_ts);
  tr = tr.slice().sort((a, b) => a.entry_ts.localeCompare(b.entry_ts));
  // Date-parse the window: entry_ts is IST (+05:30), startedAt/pausedAt are UTC (Z) —
  // a string compare wrongly included ~5.5h of pre-Start trades in the equity curve.
  const _t0 = new Date(AG.startedAt).getTime();
  const _t1 = AG.status === "paused" && AG.pausedAt ? new Date(AG.pausedAt).getTime() : Infinity;
  const evs = tr.filter((h) => { const e = new Date(h.entry_ts).getTime(); return e >= _t0 && e <= _t1; })
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
  const pnl = eq - capV - added, cls = pnl >= 0 ? "win" : "loss";
  // pipeline: ⭐ setups the scanner is watching RIGHT NOW — a booked trade needs one of
  // these to fill and then close (after your start date), so days with 0 trades are normal
  const pipe = (m.watchlist || []).filter((w) => w.ctx && w.ctx.pass && !isIndex(w.symbol));
  const inTr = pipe.filter((w) => w.state === "in_trade").length;
  rep.innerHTML = mline +
    `<b>Paper equity: ₹${Math.round(eq).toLocaleString("en-IN")}</b> ` +
    `(<span class="${cls}">${pnl >= 0 ? "+" : ""}₹${Math.round(pnl).toLocaleString("en-IN")}</span>` +
    (added ? ` · ₹${added.toLocaleString("en-IN")} added` : "") + `) · ` +
    `${n} trades · ${n ? Math.round((100 * wins) / n) : 0}% win · worst dip ${(dd * 100).toFixed(1)}%` +
    `<br>Pipeline: <b>${pipe.length}</b> ⭐ setup${pipe.length === 1 ? "" : "s"} being watched` +
    (inTr ? ` (${inTr} in trade — booked here when they close)` : "") +
    ` · expect a few completed ⭐ trades per month, not per day.` +
    `<br>Note: the live Fyers feed is active. Pocket is deliberately selective (⭐ context-gated), so it trades far less often than the cloud books — the full 4-engine performance is in 📜 History → Paper trades.`;
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
      if (!RISK_PLANS[AG.risk]) AG.risk = "1";   // remote copy may carry a legacy risk value
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
      if (!RISK_PLANS[AG.risk]) AG.risk = "1";   // remote copy may carry a legacy risk value
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
  [["live", "📡 Live"], ["agent", "🤖 Agent"], ["trades", "📒 Trades"], ["resting", "🎯 Resting"], ["gamma", "🎲 Gamma"], ["history", "📜 History"], ["legs", "✅ Legs"], ["guide", "📖 Guide"]].forEach(([v, l]) => {
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
  ["live", "agent", "trades", "resting", "gamma", "history", "legs", "guide"].forEach((v) => {
    const el = $("#tab-" + v);
    if (el) el.hidden = mainTab !== v;
  });
  if (mainTab === "resting") renderResting();
  if (mainTab === "gamma") renderGamma();
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
