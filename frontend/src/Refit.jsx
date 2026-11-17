import { useEffect, useMemo, useRef, useState } from "react";
import { getOverview, getProduct, runRefit } from "./api.js";
import { compareCurveSpec, plainOptimumText, scatterSpec, timeChartSpec, weekFacts, weekly } from "./charts.js";
import Plot from "./Plot.jsx";
import { TIER_LABEL, TIER_RANK, boundNote, eur, pct } from "./format.js";

// Custom refit page: the manager changes the model settings and sees the result next to the official one.
// Nothing here is saved, and the official results (overview, product pages) never change.

const DEFAULTS = {
  include_market: true,
  include_black_friday_flag: true,
  include_weekday: true,
  include_month: true,
  include_year: true,
  include_ad_spend: true,
  exclude_promotion_days: false,
  min_tested_days: 14,
};

// The parts of the calculation that can be switched off (the price always stays: how sales react to it is what is measured).
// Each entry: key of the setting, switch label, hint, name used in the "Ignoring" sentence, group on the screen.
const PARTS = [
  ["include_weekday", "Weekday patterns", "Lets weekdays differ, for example weekends.", "weekday patterns", "When sales happen"],
  ["include_month", "Month of the year", "Some months sell better than others, for example December.", "month", "When sales happen"],
  ["include_year", "Year (2024 vs 2025)", "Even at the same prices, 2025 can sell more or less than 2024, for reasons not in the data.", "year", "When sales happen"],
  ["include_black_friday_flag", "Black Friday", "The days 22 to 30 November.", "Black Friday", "When sales happen"],
  ["include_market", "Differences between markets", "Lets each market sell at its own level.", "market differences", "Where and how"],
  ["include_ad_spend", "Advertising spend", "Days with more advertising can sell more. Products without ad data are calculated without it.", "advertising spend", "Where and how"],
];
const GROUPS = ["When sales happen", "Where and how"];

// One sentence with the settings of a run, shown above the results (and under the switches)
function optionsText(o) {
  const off = PARTS.filter(([key]) => !o[key]).map((part) => part[3]);
  return [
    off.length ? `Ignoring: ${off.join(", ")}` : "Taking everything into account",
    o.exclude_promotion_days ? "promotion days left out" : "promotion days kept",
    `prices kept for ${o.min_tested_days} days or more count as tested`,
  ].join(", ");
}

// One switch with its explanation (a real checkbox, drawn as a switch by the CSS)
function Switch({ label, hint, checked, disabled, onChange }) {
  return (
    <label className="switch-row">
      <input type="checkbox" className="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span><b>{label}</b><span className="muted">{hint}</span></span>
    </label>
  );
}

// A choice between a few values, drawn as a row of buttons (real radio inputs, so arrow keys and screen readers work)
function Choice({ title, hint, name, value, choices, officialValue, disabled, onChange }) {
  return (
    <div className="choice" role="radiogroup" aria-label={title}>
      <b>{title}</b>
      <span className="muted">{hint}</span>
      <div className="segmented">
        {choices.map(([v, label]) => (
          <label key={v} className={v === value ? "selected" : ""}>
            <input type="radio" name={name} value={v} checked={v === value} disabled={disabled} onChange={() => onChange(v)} />
            <span>{label}</span>
            {v === officialValue && <small className="official-mark">official</small>}
          </label>
        ))}
      </div>
    </div>
  );
}

const CONFIDENCE_NOTE = "Confidence here comes from this one calculation. The homepage shows the most cautious of three calculations (everything taken into account, without year, without Black Friday), so it can be lower.";

// Plain words for every kind of failure
function plainError(e) {
  if (e.status === 501) return "Custom recalculation is not available on this site. It works on the Google Cloud Run version.";
  if (e.status === 429) return "The limit of custom recalculations for this server has been reached. Try again later.";
  if (e.status === 422) return `These settings could not be used. ${e.message}`;
  if (e.status === 404) return e.message;
  if (e.status) return `The server could not do the calculation (error ${e.status}). ${e.message}`;
  return "Could not reach the server. Check the connection and try again.";
}

// Text of one entry in the product picker, for example "Pendant Light 'Malia' (SKU-1000)"
const label = (p) => `${p.name} (${p.id})`;

// One table row per product-market, with the custom numbers and the official ones next to them
function rowsFrom(d) {
  return d.markets.map((m) => ({
    key: d.product_id + m.market,
    product_id: d.product_id, name: d.name, category: d.category, market: m.market, ad_control: d.ad_control,
    latest_price: m.latest_price,
    tier: d.custom.tier, official_tier: d.official.main_model_tier,
    usual: m.custom.usual, final: m.custom.final, move_pct: m.custom.move_pct,
    profit_change_pct: m.custom.profit_change_pct, bound: m.custom.bound, note: m.custom.note,
    official: m.official,
  }));
}

const COLUMNS = [
  { key: "name", label: "Product", get: (r) => r.name },
  { key: "market", label: "Market", get: (r) => r.market },
  { key: "latest_price", label: "Latest price", get: (r) => r.latest_price, num: true },
  { key: "usual", label: "Usual price", get: (r) => r.usual, num: true },
  { key: "final", label: "Recommended", get: (r) => r.final, num: true },
  { key: "move_pct", label: "Change", get: (r) => r.move_pct, num: true },
  { key: "profit_change_pct", label: "Expected profit change (estimate)", get: (r) => r.profit_change_pct, num: true },
  { key: "tier", label: "Confidence", get: (r) => TIER_RANK[r.tier] },
];

// A number with the official value underneath; it is marked when the custom value differs
function Pair({ custom, official, format, differs }) {
  return (
    <>
      <span className={differs ? "changed" : ""}>{format(custom)}</span>
      <div className="muted">Official {format(official)}</div>
    </>
  );
}

function moveKind(m) {
  return m <= -0.005 ? "cuts" : m >= 0.005 ? "raises" : "hold";
}

const count = (list, key) => list.reduce((acc, x) => ({ ...acc, [key(x)]: (acc[key(x)] || 0) + 1 }), {});

function Summary({ rows, total, products, stopped }) {
  const tiers = { custom: count(rows, (r) => r.tier), official: count(rows, (r) => r.official_tier) };
  const moves = { custom: count(rows, (r) => moveKind(r.move_pct)), official: count(rows, (r) => moveKind(r.official.move_pct)) };
  const pair = (c, key) => `${c.custom[key] || 0} vs ${c.official[key] || 0}`;
  return (
    <div className="card custom-summary">
      <h2>Custom recalculation compared with official</h2>
      <p>
        <b>Confidence:</b> High {pair(tiers, "high")} · Medium {pair(tiers, "medium")} · Low {pair(tiers, "low")}
      </p>
      <p>
        <b>Price moves:</b> Cuts {pair(moves, "cuts")} · Raises {pair(moves, "raises")} · Hold {pair(moves, "hold")}
      </p>
      <p className="muted small">
        Numbers are product-markets ({rows.length} from {products} of {total} products{stopped ? ", stopped early" : ""}),
        custom vs official. Official confidence here is the calculation with everything taken into account. The homepage
        uses the most cautious of three calculations, so it can be lower.
      </p>
    </div>
  );
}

// The custom result of one product: tiles, table with the official numbers next to it, and the charts.
// Used for "Recalculate this product" and for a click on a row of "Recalculate all products".
// data = POST /api/refit/{id}?curves=true, official = GET /api/product/{id} (sales history and official curve).
function ProductResult({ data, options, official, officialError, startMarket, extra }) {
  const [chartMarket, setChartMarket] = useState(startMarket);
  const officialMarket = official?.markets[chartMarket];
  const customMarket = data.markets.find((m) => m.market === chartMarket);
  const weeks = useMemo(() => (officialMarket ? weekly(officialMarket.history) : []), [officialMarket]);
  const compareChart = useMemo(
    () => (officialMarket && customMarket ? compareCurveSpec(officialMarket, customMarket.custom) : null),
    [officialMarket, customMarket]);
  const timeChart = useMemo(() => (officialMarket ? timeChartSpec(officialMarket, weeks) : null), [officialMarket, weeks]);
  const scatterChart = useMemo(() => (officialMarket ? scatterSpec(officialMarket, weeks) : null), [officialMarket, weeks]);

  return (
    <>
    <section className="card">
      <h2>Custom recalculation results: {data.name}</h2>
      <p className="muted">{data.product_id} · {optionsText(options)}.</p>
      {extra?.(chartMarket)}
      <div className="tiles">
        <div className="tile">
          <span>How strongly sales react to price (approximate)</span>
          <b>{data.custom.elasticity.toFixed(2)}</b>
          <small>Likely between {data.custom.low.toFixed(2)} and {data.custom.high.toFixed(2)}.</small>
          <small>Official: {data.official.elasticity.elasticity.toFixed(2)}</small>
        </div>
        <div className="tile">
          <span>Confidence</span>
          <b className={`badge big ${data.custom.tier}`}>{TIER_LABEL[data.custom.tier]}</b>
          <small>Official, everything taken into account: {TIER_LABEL[data.official.main_model_tier]}</small>
          <small>Official, homepage: {TIER_LABEL[data.official.tier]}</small>
        </div>
      </div>
      <p className="muted small">
        {CONFIDENCE_NOTE}
        {data.days_removed > 0 && ` ${data.days_removed} promotion days were left out of the calculation.`}
        {data.ad_spend_ignored && " This product has no ad-spend data, so the ad setting had no effect."}
      </p>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Market</th><th className="num">Latest price</th><th className="num">Usual price</th>
              <th className="num">Allowed range</th><th className="num">Recommended</th>
              <th className="num">Change</th><th className="num">Expected profit change (estimate)</th>
            </tr>
          </thead>
          <tbody>
            {data.markets.map((m) => (
              <tr key={m.market} className="plain">
                <td><b>{m.market}</b></td>
                <td className="num">{eur(m.latest_price)}</td>
                <td className="num">{eur(m.custom.usual)}</td>
                <td className="num">{eur(m.custom.low)} to {eur(m.custom.high)}
                  <div className="muted">Official {eur(m.official.low)} to {eur(m.official.high)}</div></td>
                <td className="num"><Pair custom={m.custom.final} official={m.official.final} format={eur}
                  differs={Math.abs(m.custom.final - m.official.final) >= 0.005} />
                  <div className="muted">{m.custom.note || boundNote(m.custom)}</div></td>
                <td className="num"><Pair custom={m.custom.move_pct} official={m.official.move_pct} format={pct}
                  differs={Math.abs(m.custom.move_pct - m.official.move_pct) >= 0.0005} /></td>
                <td className="num"><Pair custom={m.custom.profit_change_pct} official={m.official.profit_change_pct} format={pct}
                  differs={Math.abs(m.custom.profit_change_pct - m.official.profit_change_pct) >= 0.0005} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
    <div className="chart-head">
      <h2>Charts</h2>
      <div className="tabs">
        {data.markets.map((m) => (
          <button key={m.market} className={m.market === chartMarket ? "tab active" : "tab"}
            onClick={() => setChartMarket(m.market)}>{m.market}</button>
        ))}
      </div>
    </div>
    {officialError && <p className="muted">{officialError}</p>}
    {!officialError && !official && <p className="muted">Loading the sales history…</p>}
    {compareChart && (
      <>
        <section className="card">
          <h2>Profit at different prices</h2>
          <p className="muted">
            What we expect the profit to be if only the price were different. Blue is the official curve,
            violet the custom one. The dashed parts are outside the prices the shop has tested.
          </p>
          <Plot data={compareChart.data} layout={compareChart.layout} height={360} />
          {customMarket.custom.unrestricted && (
            <p className="muted small">Custom: {plainOptimumText(customMarket.custom.unrestricted)}</p>
          )}
        </section>
        <section className="card">
          <h2>Price and sales over time</h2>
          <Plot data={timeChart.data} layout={timeChart.layout} height={340} />
        </section>
        <section className="card">
          <h2>Price versus sales</h2>
          <p className="muted">Each dot is one week. Promotions bring extra visitors, so red weeks can look better than the price alone explains. {weekFacts(weeks)}</p>
          <Plot data={scatterChart.data} layout={scatterChart.layout} height={320} />
        </section>
      </>
    )}
    </>
  );
}

// id and market are optional: an address like #/refit/SKU-1000/DE starts with that product picked, #/refit with none
export default function Refit({ id, market }) {
  const [options, setOptions] = useState(DEFAULTS);
  const [pickedId, setPickedId] = useState(id);        // the product "Recalculate this product" works on
  const [products, setProducts] = useState([]);        // [{ id, name }] for the picker
  const [pickText, setPickText] = useState("");
  const [one, setOne] = useState(null);          // result of "Recalculate this product"
  const [oneOptions, setOneOptions] = useState(DEFAULTS);
  const [oneBusy, setOneBusy] = useState(false);
  const [oneError, setOneError] = useState("");
  const [productData, setProductData] = useState(null);  // sales history and official curve (same data as the product page)
  const [productError, setProductError] = useState("");

  const [rows, setRows] = useState([]);          // result rows of "Recalculate all", filled one product at a time
  const [runOptions, setRunOptions] = useState(DEFAULTS);
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [done, setDone] = useState(0);
  const [total, setTotal] = useState(0);
  const [current, setCurrent] = useState("");
  const [stopped, setStopped] = useState(false);
  const [allError, setAllError] = useState("");
  const [skipped, setSkipped] = useState([]);
  const abortRef = useRef(null);

  // A click on a row of the list shows that product's custom result and charts (with the settings of the run)
  const [detail, setDetail] = useState(null);   // { id, market, loading } | { id, market, error } | { id, market, data, official, officialError }
  const detailToken = useRef(0);                 // an older request must not overwrite a newer click
  const officialCache = useRef(new Map());       // product id -> promise of GET /api/product/{id}
  const detailRef = useRef(null);

  const [sort, setSort] = useState({ key: "default", dir: 1 });
  const [marketFilter, setMarketFilter] = useState("all");
  const [tierFilter, setTierFilter] = useState("all");
  const [search, setSearch] = useState("");

  // Leaving the page stops a running "Recalculate all"
  useEffect(() => () => abortRef.current?.abort(), []);

  // A click on a row far down the list jumps straight to the panel above it (at once, before the charts load:
  // the panel only grows downwards, so its top stays in view)
  const panelKey = detail ? `${detail.id}-${detail.market}` : "";
  useEffect(() => {
    if (panelKey) detailRef.current?.scrollIntoView({ behavior: "auto", block: "start" });
  }, [panelKey]);

  // Closing the panel takes the reader back to the row that was clicked
  function closeDetail() {
    const row = document.querySelector("tr.picked");
    detailToken.current++; setDetail(null);
    requestAnimationFrame(() => row?.scrollIntoView({ behavior: "auto", block: "center" }));
  }

  // The charts need the sales history and the official curve; they do not depend on the settings, so load them once
  useEffect(() => {
    setProductData(null);
    setProductError("");
    if (!pickedId) return;
    getProduct(pickedId).then(setProductData).catch(() => setProductError("The sales history could not be loaded, so the charts are not shown."));
  }, [pickedId]);

  // The product list for the picker (one entry per product, by name)
  useEffect(() => {
    getOverview().then((rows) => {
      const seen = new Map(rows.map((r) => [r.product_id, r.name]));
      const list = [...seen].map(([pid, name]) => ({ id: pid, name })).sort((a, b) => a.name.localeCompare(b.name));
      setProducts(list);
      const first = list.find((p) => p.id === id);
      if (first) setPickText(label(first));
    }).catch(() => {});   // without the list "Recalculate all products" still works
  }, [id]);

  const set = (key, value) => setOptions((o) => ({ ...o, [key]: value }));

  // Typing or choosing in the picker: the product is picked when the text is exactly one of the entries
  function pick(text) {
    setPickText(text);
    const found = products.find((p) => label(p) === text);
    const next = found ? found.id : null;
    if (next !== pickedId) { setPickedId(next); setOne(null); setOneError(""); }
  }
  const busy = oneBusy || running;

  // Only one result is on screen at a time: starting a recalculation removes the other result
  function clearAll() {
    setRows([]); setSkipped([]); setAllError(""); setStopped(false); setDone(0); setTotal(0); setCurrent("");
    setStarted(false);
    detailToken.current++; setDetail(null);
  }

  function loadOfficial(pid) {
    if (!officialCache.current.has(pid)) {
      const promise = getProduct(pid);
      promise.catch(() => officialCache.current.delete(pid));   // a failed load is tried again on the next click
      officialCache.current.set(pid, promise);
    }
    return officialCache.current.get(pid);
  }

  async function openDetail(r) {
    if (running) return;
    const token = ++detailToken.current;
    setDetail({ id: r.product_id, market: r.market, loading: true });
    let result;
    try {
      const [data, official] = await Promise.all([
        runRefit(r.product_id, runOptions, undefined, true),
        loadOfficial(r.product_id).catch(() => null),
      ]);
      result = { id: r.product_id, market: r.market, data, official,
        officialError: official ? "" : "The sales history could not be loaded, so the charts are not shown." };
    } catch (e) {
      result = { id: r.product_id, market: r.market, error: plainError(e) };
    }
    if (token === detailToken.current) setDetail(result);
  }

  async function refitOne() {
    clearAll();
    setOne(null);
    setOneBusy(true);
    setOneError("");
    const used = { ...options };
    try {
      const d = await runRefit(pickedId, used, undefined, true);
      setOne(d);
      setOneOptions(used);
    } catch (e) {
      setOneError(plainError(e));
    }
    setOneBusy(false);
  }

  async function refitAll() {
    const used = { ...options };
    const controller = new AbortController();
    abortRef.current = controller;
    setOne(null); setOneError("");
    clearAll();
    setRunOptions(used); setStarted(true); setRunning(true);

    let ids;
    try {
      ids = [...new Set((await getOverview()).map((r) => r.product_id))].sort();
    } catch (e) {
      setAllError(plainError(e));
      setRunning(false);
      return;
    }
    setTotal(ids.length);

    const collected = [];
    const failed = [];
    let completed = 0;
    for (let i = 0; i < ids.length; i++) {
      if (controller.signal.aborted) break;
      setCurrent(ids[i]);
      try {
        collected.push(...rowsFrom(await runRefit(ids[i], used, controller.signal)));
        setRows([...collected]);
      } catch (e) {
        if (e.name === "AbortError") break;
        if (e.status === 422 || e.status === 404) {   // this product cannot be calculated: note it, carry on
          failed.push(ids[i]);
          setSkipped([...failed]);
        } else {                                       // 501, 429, network: stop and say why
          setAllError(plainError(e));
          break;
        }
      }
      completed = i + 1;
      setDone(completed);
    }
    setStopped(completed < ids.length);
    setRunning(false);
  }

  const cancel = () => abortRef.current?.abort();

  const shown = useMemo(() => {
    let list = rows.filter(
      (r) =>
        (marketFilter === "all" || r.market === marketFilter) &&
        (tierFilter === "all" || r.tier === tierFilter) &&
        (r.name + r.product_id + r.category).toLowerCase().includes(search.toLowerCase())
    );
    if (sort.key === "default") {
      list = [...list].sort((a, b) => TIER_RANK[a.tier] - TIER_RANK[b.tier] || b.profit_change_pct - a.profit_change_pct);
    } else {
      const col = COLUMNS.find((c) => c.key === sort.key);
      list = [...list].sort((a, b) => {
        const x = col.get(a), y = col.get(b);
        return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
      });
    }
    return list;
  }, [rows, marketFilter, tierFilter, search, sort]);

  const clickSort = (key) => setSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: 1 }));
  const finished = started && !running && rows.length > 0;
  const productsDone = new Set(rows.map((r) => r.product_id)).size;

  return (
    <main>
      {id ? <a href={`#/product/${id}/${market}`} className="back">← Back to {id} {market}</a>
        : <a href="#/" className="back">← All products</a>}
      <h1>Custom recalculation</h1>
      <p className="lead">
        Change what the price calculation takes into account and see what it would recommend. The results below are for
        trying things out: the official recommendations on the other pages do not change, and nothing here is saved.
      </p>

      <section className="card">
        <h2>What should the calculation take into account?</h2>
        <div className="setting-groups">
          {GROUPS.map((group) => (
            <div className="setting-group" key={group}>
              <h3>{group}</h3>
              {PARTS.filter((part) => part[4] === group).map(([key, label, hint]) => (
                <Switch key={key} label={label} hint={hint} checked={options[key]} disabled={busy} onChange={(v) => set(key, v)} />
              ))}
            </div>
          ))}
          <div className="setting-group">
            <h3>Which days and prices to use</h3>
            <Switch label="Leave out promotion days" hint="Days priced below 90% of the usual price."
              checked={options.exclude_promotion_days} disabled={busy} onChange={(v) => set("exclude_promotion_days", v)} />
            <Choice title="Tested prices" name="min_tested_days" value={options.min_tested_days} officialValue={14} disabled={busy}
              hint="Only prices the shop has really tried can be recommended. A price counts as tested if it stayed unchanged for at least:"
              choices={[[7, "7 days"], [14, "14 days"], [28, "28 days"]]} onChange={(v) => set("min_tested_days", v)} />
            <span className="muted small">A longer time is stricter: fewer prices are allowed and the advice stays closer to
              what the shop has already done.</span>
          </div>
        </div>
        <p className="muted small">The price is always taken into account: how sales react to the price is what is measured.</p>
        <p className="muted small">Your choice: {optionsText(options)}.</p>
        <div className="picker">
          <label htmlFor="product-pick"><b>Product</b> <span className="muted">(for "Recalculate this product")</span></label>
          <input id="product-pick" list="product-list" value={pickText} disabled={busy} placeholder="Type a name or choose from the list"
            onChange={(e) => pick(e.target.value)} />
          <datalist id="product-list">
            {products.map((p) => <option key={p.id} value={label(p)} />)}
          </datalist>
        </div>
        <div className="actions">
          <button className="primary" onClick={refitOne} disabled={busy || !pickedId}
            title={pickedId ? "" : "Choose a product first"}>{oneBusy ? "Recalculating…" : "Recalculate this product"}</button>
          <button className="primary" onClick={refitAll} disabled={busy}>Recalculate all products</button>
          {running && <button className="secondary" onClick={cancel}>Cancel</button>}
          <button className="link" onClick={() => setOptions(DEFAULTS)} disabled={busy}>Back to official settings</button>
        </div>
      </section>

      {oneError && <p className="error">{oneError}</p>}

      {one && (
        <ProductResult key={one.product_id} data={one} options={oneOptions} official={productData} officialError={productError}
          startMarket={market && one.markets.some((m) => m.market === market) ? market : one.markets[0].market} />
      )}

      {started && (
        <section>
          <h2>Custom recalculation results: all products</h2>
          <p className="muted">{optionsText(runOptions)}.</p>
          {(running || (total > 0 && done < total && !allError)) && (
            <div className="progress">
              <div className="bar"><div style={{ width: `${total ? (done / total) * 100 : 0}%` }} /></div>
              <span className="muted">
                {running ? `Recalculating product ${Math.min(done + 1, total || 1)} of ${total || "…"} (${current || "starting"})` : ""}
                {!running && stopped ? `Stopped after ${done} of ${total} products.` : ""}
              </span>
            </div>
          )}
          {allError && <p className="error">{allError}</p>}
          {skipped.length > 0 && (
            <p className="muted">
              {!running && rows.length === 0 && total > 0 && skipped.length === total
                ? "None of the products could be calculated with these settings."
                : `These products could not be calculated with these settings and are not in the list: ${skipped.slice(0, 6).join(", ")}${skipped.length > 6 ? ` and ${skipped.length - 6} more` : ""}.`}
            </p>
          )}
          {finished && <Summary rows={rows} total={total} products={productsDone} stopped={stopped || Boolean(allError)} />}

          {detail && (
            <div ref={detailRef} className="detail-panel">
              {detail.loading && <p className="muted">Loading {detail.id}…</p>}
              {detail.error && <p className="error">{detail.error}</p>}
              {detail.data && (
                <ProductResult key={`${detail.id}-${detail.market}`} data={detail.data} options={runOptions} official={detail.official}
                  officialError={detail.officialError} startMarket={detail.market}
                  extra={(m) => (
                    <p className="detail-actions">
                      <a href={`#/product/${detail.id}/${m}`} target="_blank" rel="noopener">Open the official product page</a>
                    </p>
                  )} />
              )}
              <button className="link close" onClick={closeDetail}>Close</button>
            </div>
          )}

          {rows.length > 0 && (
            <>
              <div className="filters">
                <input placeholder="Search product or category" value={search} onChange={(e) => setSearch(e.target.value)} />
                <select value={marketFilter} onChange={(e) => setMarketFilter(e.target.value)}>
                  <option value="all">All markets</option>
                  <option>DE</option><option>FR</option><option>CH</option>
                </select>
                <select value={tierFilter} onChange={(e) => setTierFilter(e.target.value)}>
                  <option value="all">All confidence levels</option>
                  <option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
                </select>
                <button className="link" onClick={() => setSort({ key: "default", dir: 1 })}>Reset sort</button>
              </div>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      {COLUMNS.map((c) => (
                        <th key={c.key} className={c.num ? "num" : ""} onClick={() => clickSort(c.key)}>
                          {c.label} {sort.key === c.key ? (sort.dir === 1 ? "▲" : "▼") : ""}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r) => (
                      <tr key={r.key} className={running ? "waiting" : detail?.id === r.product_id && detail?.market === r.market ? "picked" : ""}
                        tabIndex={running ? undefined : 0} aria-label={`Show the custom result for ${r.name} in ${r.market}`}
                        onClick={() => openDetail(r)} onKeyDown={(e) => e.key === "Enter" && openDetail(r)}>
                        <td><b>{r.name}</b><div className="muted">{r.product_id} · {r.category}</div></td>
                        <td>{r.market}</td>
                        <td className="num">{eur(r.latest_price)}</td>
                        <td className="num">{eur(r.usual)}</td>
                        <td className="num"><Pair custom={r.final} official={r.official.final} format={eur}
                          differs={Math.abs(r.final - r.official.final) >= 0.005} /></td>
                        <td className="num"><Pair custom={r.move_pct} official={r.official.move_pct} format={pct}
                          differs={Math.abs(r.move_pct - r.official.move_pct) >= 0.0005} /></td>
                        <td className="num"><Pair custom={r.profit_change_pct} official={r.official.profit_change_pct} format={pct}
                          differs={Math.abs(r.profit_change_pct - r.official.profit_change_pct) >= 0.0005} /></td>
                        <td>
                          <span className={`badge ${r.tier}`}>{TIER_LABEL[r.tier]}</span>
                          <div className="muted">
                            {r.note || (r.tier === "medium" ? "Test price" : r.tier === "low" ? "Hold" : boundNote(r))}
                            {!r.ad_control ? " · no ad data" : ""}
                          </div>
                          <div className="muted">Official {TIER_LABEL[r.official_tier]}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {shown.length === 0 && <p className="muted pad">No product matches these filters.</p>}
              </div>
              <p className="muted small">
                {CONFIDENCE_NOTE}
                A click on a row shows its custom result and charts.
              </p>
            </>
          )}
        </section>
      )}
    </main>
  );
}
