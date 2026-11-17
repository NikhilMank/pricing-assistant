import { useEffect, useMemo, useState } from "react";
import { getHealth, getProduct } from "./api.js";
import AgentCards from "./AgentCards.jsx";
import Plot from "./Plot.jsx";
import { curveSpec, scatterSpec, timeChartSpec, unrestrictedText, weekFacts, weekly } from "./charts.js";
import { TIER_ACTION, TIER_LABEL, boundNote, eur, pct } from "./format.js";

// The promotion check (computed in precompute.py): does the result still hold without the promotion days?
const PROMO_MESSAGE = {
  holds: ["ok", "Good sign.", "Even if we ignore the weeks with promotions, customers still buy less when the price goes up. The result does not rest on discounts alone."],
  weaker: ["warn", "Be careful.", "Without the promotion weeks, the effect of price looks weaker and less certain. Part of the result comes from discounts, which also bring extra visitors."],
  noisy: ["warn", "Be careful.", "Outside the promotion weeks, the data shows no clear reaction to price. The result rests mostly on the discount periods."],
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

export default function ProductView({ id, market }) {
  const [product, setProduct] = useState(null);
  const [error, setError] = useState("");
  const [refitOn, setRefitOn] = useState(false); // the "Custom recalculation" link only shows where the server can do it

  useEffect(() => {
    setProduct(null);
    setError("");
    getProduct(id).then(setProduct).catch((e) => setError(e.message));
  }, [id]);

  useEffect(() => {
    getHealth().then((h) => setRefitOn(Boolean(h.refit))).catch(() => setRefitOn(false));
  }, []);

  const m = product?.markets[market];
  const weeks = useMemo(() => (m ? weekly(m.history) : []), [m]);

  const timeChart = useMemo(() => (m ? timeChartSpec(m, weeks) : null), [m, weeks]);
  const scatter = useMemo(() => (m ? scatterSpec(m, weeks) : null), [m, weeks]);
  const curve = useMemo(() => (m ? curveSpec(m) : null), [m]);

  if (error) return <main><p className="error">{error}</p><a href="#/">Back to the overview</a></main>;
  if (!product || !m) return <main><p>Loading…</p></main>;

  const e = product.elasticity;
  return (
    <main>
      <a href="#/" className="back">← All products</a>
      {refitOn && (
        <a className="refit-link" href={`#/refit/${id}/${market}`} target="_blank" rel="noopener">Custom recalculation</a>
      )}
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
