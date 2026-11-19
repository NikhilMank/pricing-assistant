// Chart settings shared by the product page and the Custom refit page. Each function only builds the
// data and layout for Plotly (see Plot.jsx); nothing is fetched or drawn here.
import { eur } from "./format.js";

export const BLUE = "#1f58a6", GREY = "#4b5563", GREEN = "#1f6f43";
export const NAVY = "#14213d", BARS = "#b4bcc6", RED = "#d62828";
export const VIOLET = "#5b3fb5", LIGHT_VIOLET = "#b7a8e6"; // the Custom refit page

// Daily rows are mostly 0 to 3 units, so the charts use weekly totals (7 days each).
export function weekly(h) {
  const weeks = [];
  for (let i = 0; i < h.date.length; i += 7) {
    const end = Math.min(i + 7, h.date.length);
    let units = 0, priceSum = 0, promo = false;
    for (let j = i; j < end; j++) {
      units += h.units[j];
      priceSum += h.price[j];
      promo = promo || h.promo[j];
    }
    weeks.push({ date: h.date[i], units, price: priceSum / (end - i), promo });
  }
  return weeks;
}

// Plain facts about the weekly points, so each product shows its own price range (no conclusion drawn for the reader)
export function weekFacts(weeks) {
  const normal = weeks.filter((w) => !w.promo).map((w) => w.price);
  const promo = weeks.filter((w) => w.promo).map((w) => w.price);
  if (normal.length === 0) return "";
  const lo = Math.min(...normal), hi = Math.max(...normal);
  const spread = ((hi - lo) / lo) * 100;
  const normalText = `Normal weeks: prices from ${eur(lo)} to ${eur(hi)} (${spread.toFixed(0)}% apart).`;
  const promoText = promo.length ? ` Promotion weeks: ${promo.length}, down to ${eur(Math.min(...promo))}.` : " No promotion weeks.";
  return normalText + promoText;
}

export function unrestrictedText(u) {
  if (u.point == null) {
    return "No finite unrestricted optimum: the data cannot rule out that buyers hardly react to price.";
  }
  const range = u.from != null && u.to != null ? `${eur(u.from)} to ${eur(u.to)}` : u.from != null ? `from ${eur(u.from)}, no upper limit` : "unclear";
  return `Unrestricted model optimum (not recommended, no data at these prices): about ${eur(u.point)}. Plausible range: ${range}. It reacts strongly to small changes in the elasticity.`;
}

// Weekly units (bars) with the price line, promotion days and the usual price. m = one market of GET /api/product/{id}.
export function timeChartSpec(m, weeks) {
  const promoDays = m.history.date.map((d, i) => i).filter((i) => m.history.promo[i]);
  return {
    data: [
      { type: "bar", x: weeks.map((w) => w.date), y: weeks.map((w) => w.units), name: "Units sold per week",
        marker: { color: BARS }, yaxis: "y", hovertemplate: "%{y} units" },
      { type: "scatter", mode: "lines", x: m.history.date, y: m.history.price, name: "Price", yaxis: "y2",
        line: { color: NAVY, width: 2.5, shape: "hv" }, hovertemplate: "€%{y:.2f}" },
      { type: "scatter", mode: "markers", x: promoDays.map((i) => m.history.date[i]), y: promoDays.map((i) => m.history.price[i]),
        name: "Promotion days (price below 90% of usual)", yaxis: "y2", marker: { color: RED, size: 7 }, hovertemplate: "€%{y:.2f} (promotion)" },
      { type: "scatter", mode: "lines", x: [m.history.date[0], m.history.date[m.history.date.length - 1]], y: [m.usual, m.usual],
        name: `Usual price (${eur(m.usual)})`, yaxis: "y2", line: { color: GREY, width: 2, dash: "dot" }, hovertemplate: "€%{y:.2f}" },
    ],
    layout: {
      hovermode: "x unified",
      yaxis: { title: "Units per week", rangemode: "tozero" },
      yaxis2: { title: "Price (EUR)", overlaying: "y", side: "right", showgrid: false },
    },
  };
}

// One dot per week: average price against units sold
export function scatterSpec(m, weeks) {
  const pick = (flag) => weeks.filter((w) => w.promo === flag);
  const trace = (flag, name, color) => ({
    type: "scatter", mode: "markers", name, x: pick(flag).map((w) => w.price), y: pick(flag).map((w) => w.units),
    marker: { color, size: 8, opacity: 0.85, line: { color: "#fff", width: 0.5 } },
  });
  return {
    data: [trace(false, "Normal weeks", NAVY), trace(true, "Weeks with promotion days", RED)],
    layout: {
      xaxis: { title: "Average price in the week (EUR)" },
      yaxis: { title: "Units sold in the week", range: [0, Math.max(...weeks.map((w) => w.units), 1) * 1.15] },
      shapes: [{ type: "line", x0: m.usual, x1: m.usual, yref: "paper", y0: 0, y1: 1, line: { color: GREY, width: 2, dash: "dot" } }],
      // label sits above the plot, to the right of the line, so the line never runs through the text
      annotations: [{ x: m.usual, yref: "paper", y: 1, yanchor: "bottom", xanchor: "left", xshift: 6, text: `usual ${eur(m.usual)}`, showarrow: false, font: { color: "#3e4c59" } }],
      margin: { l: 55, r: 55, t: 40, b: 45 },
    },
  };
}

// The official profit curve: solid where the prices were tested, grey dashed where the model has no data
export function curveSpec(m) {
  const inRange = m.curve.filter((p) => p.in_range);
  const line = (x, color) => ({ type: "line", x0: x, x1: x, yref: "paper", y0: 0, y1: 1, line: { color, width: 2, dash: "dot" } });
  return {
    data: [
      { type: "scatter", mode: "lines", name: "No data here: indication only", x: m.curve.map((p) => p.price),
        y: m.curve.map((p) => p.profit), line: { color: GREY, dash: "dash", width: 2 } },
      { type: "scatter", mode: "lines", name: "Allowed range (tested prices)", x: inRange.map((p) => p.price),
        y: inRange.map((p) => p.profit), line: { color: BLUE, width: 3 } },
    ],
    layout: {
      xaxis: { title: "Price (EUR)" },
      yaxis: { title: "Model profit, Oct to Dec 2025 days (EUR)" },
      shapes: [line(m.usual, GREY), line(m.final, GREEN)],
      annotations: [
        { x: m.usual, yref: "paper", y: 1.02, yanchor: "bottom", xanchor: "left", xshift: 6, text: "usual", showarrow: false, font: { color: "#3e4c59" } },
        { x: m.final, yref: "paper", y: 1.02, yanchor: "bottom", xanchor: "right", xshift: -6, text: "recommended", showarrow: false, font: { color: GREEN } },
      ],
      margin: { l: 70, r: 30, t: 50, b: 45 },
    },
  };
}

// Custom refit page: the official curve and the custom curve on the same axes.
// official = one market of GET /api/product/{id}; custom = the custom part of one market of POST /api/refit/{id}?curves=true
export function compareCurveSpec(official, custom) {
  const tested = (curve) => curve.filter((p) => p.in_range);
  const line = (x, color) => ({ type: "line", x0: x, x1: x, yref: "paper", y0: 0, y1: 1, line: { color, width: 2, dash: "dot" } });
  // the lower of the two recommended prices gets its label on the left of its line, the higher one on the right
  const customRight = custom.final >= official.final;
  // yshift is in pixels above the plot: the usual price label sits on the lower row, both recommendation labels on the
  // upper row. They are on opposite sides of their lines, so they cannot touch each other however close the prices are.
  const label = (x, text, color, right, yshift) => ({
    x, yref: "paper", y: 1, yshift, yanchor: "bottom", xanchor: right ? "left" : "right", xshift: right ? 6 : -6,
    text, showarrow: false, font: { color, size: 12 },
  });
  return {
    data: [
      { type: "scatter", mode: "lines", name: "Official: no data at these prices", x: official.curve.map((p) => p.price),
        y: official.curve.map((p) => p.profit), line: { color: GREY, dash: "dash", width: 2 } },
      { type: "scatter", mode: "lines", name: "Official: prices the shop has tested", x: tested(official.curve).map((p) => p.price),
        y: tested(official.curve).map((p) => p.profit), line: { color: BLUE, width: 6 }, opacity: 0.55 },
      { type: "scatter", mode: "lines", name: "Custom: no data at these prices", x: custom.curve.map((p) => p.price),
        y: custom.curve.map((p) => p.profit), line: { color: LIGHT_VIOLET, dash: "dash", width: 2 } },
      { type: "scatter", mode: "lines", name: "Custom: prices the shop has tested", x: tested(custom.curve).map((p) => p.price),
        y: tested(custom.curve).map((p) => p.profit), line: { color: VIOLET, width: 2.5 } },
    ],
    layout: {
      xaxis: { title: "Price (EUR)" },
      yaxis: { title: "Expected profit, Oct to Dec 2025 days (EUR)" },
      shapes: [line(official.usual, GREY), line(official.final, GREEN), line(custom.final, VIOLET)],
      annotations: [
        label(official.usual, `usual price ${eur(official.usual)}`, "#3e4c59", true, 4),
        label(official.final, `official recommendation ${eur(official.final)}`, GREEN, !customRight, 24),
        label(custom.final, `custom recommendation ${eur(custom.final)}`, VIOLET, customRight, 24),
      ],
      legend: { orientation: "h", y: -0.32 },  // four entries: lower than the default so it clears the axis title
      margin: { l: 70, r: 20, t: 75, b: 60 },
    },
  };
}

// Plain-words version of unrestrictedText, used only on the Custom recalculation page
export function plainOptimumText(u) {
  if (u.point == null) {
    return "Without any limits there is no best price: the data cannot rule out that buyers hardly react to price.";
  }
  const range = u.from != null && u.to != null ? `${eur(u.from)} to ${eur(u.to)}` : u.from != null ? `from ${eur(u.from)}, no upper limit` : "unclear";
  return `Without any limits the calculation would suggest about ${eur(u.point)} (not recommended: the shop has no sales data at such prices). Plausible range: ${range}. This number changes a lot with small changes in how strongly sales react to price.`;
}
