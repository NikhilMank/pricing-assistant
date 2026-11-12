import { useEffect, useMemo, useState } from "react";
import { getProduct } from "./api.js";
import AgentCards from "./AgentCards.jsx";
import Plot from "./Plot.jsx";
import { TIER_ACTION, TIER_LABEL, boundNote, eur, pct } from "./format.js";

const BLUE = "#2f6db5", ORANGE = "#d9822b", GREY = "#9aa5b1", GREEN = "#2e8b57";

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
          marker: { color: "#c5d6ea" }, yaxis: "y" },
        { type: "scatter", mode: "lines", x: m.history.date, y: m.history.price, name: "Price", yaxis: "y2",
          line: { color: BLUE, width: 2, shape: "hv" } },
        { type: "scatter", mode: "markers", x: promoDays.map((i) => m.history.date[i]), y: promoDays.map((i) => m.history.price[i]),
          name: "Promotion days (price below 90% of usual)", yaxis: "y2", marker: { color: ORANGE, size: 5 } },
      ],
      layout: {
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
      marker: { color, size: 7, opacity: 0.7 },
    });
    return {
      data: [trace(false, "Normal weeks", BLUE), trace(true, "Weeks with promotion days", ORANGE)],
      layout: { xaxis: { title: "Average price in the week (EUR)" }, yaxis: { title: "Units sold in the week", rangemode: "tozero" } },
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
          { x: m.usual, yref: "paper", y: 1.02, text: "usual", showarrow: false, font: { color: "#52606d" } },
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
          <span>Confidence</span>
          <b className={`badge big ${m.tier}`}>{TIER_LABEL[m.tier]}</b>
          <small>{TIER_ACTION[m.tier]}</small>
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
        <p className="muted">Each dot is one week. Promotions bring extra visitors, so orange weeks can look better than the price alone explains.</p>
        <Plot data={scatter.data} layout={scatter.layout} height={320} />
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
