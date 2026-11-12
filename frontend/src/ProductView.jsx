import { useEffect, useMemo, useState } from "react";
import { getProduct } from "./api.js";
import AgentCards from "./AgentCards.jsx";
import Plot from "./Plot.jsx";
import { TIER_ACTION, TIER_LABEL, boundNote, eur, pct } from "./format.js";

const BLUE = "#1f58a6", ORANGE = "#d9822b", GREY = "#4b5563", GREEN = "#1f6f43";
const NAVY = "#14213d", BARS = "#b4bcc6", RED = "#d62828";

// Daily rows are mostly 0 to 3 units, so the charts use weekly totals (7 days each).
function weekly(h) {
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
function weekFacts(weeks) {
  const normal = weeks.filter((w) => !w.promo).map((w) => w.price);
  const promo = weeks.filter((w) => w.promo).map((w) => w.price);
  if (normal.length === 0) return "";
  const lo = Math.min(...normal), hi = Math.max(...normal);
  const spread = ((hi - lo) / lo) * 100;
  const normalText = `Normal weeks: prices from ${eur(lo)} to ${eur(hi)} (${spread.toFixed(0)}% apart).`;
  const promoText = promo.length ? ` Promotion weeks: ${promo.length}, down to ${eur(Math.min(...promo))}.` : " No promotion weeks.";
  return normalText + promoText;
}

// The promotion check (computed in precompute.py): does the result still hold without the promotion days?
const PROMO_MESSAGE = {
  holds: ["ok", "Good sign.", "Even if we ignore the weeks with promotions, customers still buy less when the price goes up. The result does not rest on discounts alone."],
  weaker: ["warn", "Be careful.", "Without the promotion weeks, the effect of price looks weaker. Part of the result comes from discounts, which also bring extra visitors."],
  noisy: ["warn", "Be careful.", "Outside the promotion weeks, the data cannot tell how customers react to price. The result rests mostly on the discount periods."],
};

function PromoCheck({ check, all }) {
  const message = check && PROMO_MESSAGE[check.verdict];
  if (!message) return null;
  const [kind, lead, text] = message;
  return (
    <div className={`promo-check ${kind}`}>
      <b>Does the result depend on promotions?</b>
      <p><strong>{lead}</strong> {text}</p>
      <p className="muted small">
        Price effect for this product (all markets): {all.elasticity.toFixed(2)} using all days (likely between{" "}
        {all.low.toFixed(2)} and {all.high.toFixed(2)}), {check.elasticity.toFixed(2)} without the promotion days
        (likely between {check.low.toFixed(2)} and {check.high.toFixed(2)}). Promotion days: {check.promo_days} of{" "}
        {check.total_days}.
      </p>
    </div>
  );
}

function unrestrictedText(u) {
  if (u.point == null) {
    return "No finite unrestricted optimum: the data cannot rule out that buyers hardly react to price.";
  }
  const range = u.from != null && u.to != null ? `${eur(u.from)} to ${eur(u.to)}` : u.from != null ? `from ${eur(u.from)}, no upper limit` : "unclear";
  return `Unrestricted model optimum (not recommended, no data at these prices): about ${eur(u.point)}. Plausible range: ${range}. It reacts strongly to small changes in the elasticity.`;
}

export default function ProductView({ id, market }) {
  const [product, setProduct] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setProduct(null);
    setError("");
    getProduct(id).then(setProduct).catch((e) => setError(e.message));
  }, [id]);

  const m = product?.markets[market];
  const weeks = useMemo(() => (m ? weekly(m.history) : []), [m]);

  const timeChart = useMemo(() => {
    if (!m) return null;
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
  }, [m, weeks]);

  const scatter = useMemo(() => {
    if (!m) return null;
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
  }, [m, weeks]);

  const curve = useMemo(() => {
    if (!m) return null;
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
          { x: m.usual, yref: "paper", y: 1.02, text: "usual", showarrow: false, font: { color: "#3e4c59" } },
          { x: m.final, yref: "paper", y: 1.1, text: "recommended", showarrow: false, font: { color: GREEN } },
        ],
        margin: { l: 70, r: 30, t: 50, b: 45 },
      },
    };
  }, [m]);

  if (error) return <main><p className="error">{error}</p><a href="#/">Back to the overview</a></main>;
  if (!product || !m) return <main><p>Loading…</p></main>;

  const e = product.elasticity;
  return (
    <main>
      <a href="#/" className="back">← All products</a>
      <h1>{product.name}</h1>
      <p className="muted">{product.product_id} · {product.category} · {product.brand}</p>

      <div className="tabs">
        {Object.keys(product.markets).sort().map((k) => (
          <a key={k} href={`#/product/${id}/${k}`} className={k === market ? "tab active" : "tab"}>{k}</a>
        ))}
      </div>

      <div className="tiles">
        <div className="tile">
          <span>Recommended price</span>
          <b>{eur(m.final)}</b>
          <small>Usual {eur(m.usual)} · change {pct(m.move_pct)}</small>
          {m.bound && <small>{boundNote(m)}</small>}
        </div>
        <div className="tile">
          <span>Model confidence</span>
          <b className={`badge big ${m.tier}`}>{TIER_LABEL[m.tier]}</b>
          <small>{TIER_ACTION[m.tier]}</small>
          <small>Based on all days, including promotions.</small>
        </div>
        <div className="tile">
          <span>Price sensitivity (elasticity, approximate)</span>
          <b>{e.elasticity.toFixed(2)}</b>
          <small>Likely between {e.low.toFixed(2)} and {e.high.toFixed(2)}. About {Math.abs(e.elasticity).toFixed(1)}% fewer sales for every 1% price increase.</small>
        </div>
        <div className="tile">
          <span>Profit change (model estimate)</span>
          <b>{pct(m.profit_change_pct)}</b>
          <small>On the Oct to Dec 2025 days, without Black Friday</small>
        </div>
      </div>
      <p className="muted">
        Allowed range: {eur(m.low)} to {eur(m.high)} (prices the shop held for at least 14 days, within 20% of the usual
        price, above cost {eur(m.cost)}). The confidence level is the weakest of three model versions
        (full, without year, without Black Friday): {Object.entries(product.variant_tiers).map(([k, v]) => `${k}: ${v}`).join(", ")}.
        {!product.ad_control && " This product has no ad-spend data, so advertising could not be taken into account."}
      </p>

      <section className="card">
        <h2>Price and sales over time</h2>
        <Plot data={timeChart.data} layout={timeChart.layout} height={340} />
      </section>
      <section className="card">
        <h2>Price versus sales</h2>
        <p className="muted">Each dot is one week. Promotions bring extra visitors, so red weeks can look better than the price alone explains. {weekFacts(weeks)}</p>
        <Plot data={scatter.data} layout={scatter.layout} height={320} />
        <PromoCheck check={product.promo_check} all={e} />
      </section>
      <section className="card">
        <h2>Profit at different prices</h2>
        <p className="muted">What the model expects the profit to be if only the price were different. The grey dashed part is outside the prices the shop has tested.</p>
        <Plot data={curve.data} layout={curve.layout} height={340} />
        <p className="muted small">{unrestrictedText(m.unrestricted)}</p>
      </section>

      <AgentCards id={id} market={market} />
    </main>
  );
}
