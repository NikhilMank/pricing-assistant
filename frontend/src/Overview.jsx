import { useEffect, useMemo, useState } from "react";
import { getOverview } from "./api.js";
import { TIER_ACTION, TIER_LABEL, TIER_RANK, boundNote, eur, pct } from "./format.js";

const COLUMNS = [
  { key: "name", label: "Product", get: (r) => r.name },
  { key: "market", label: "Market", get: (r) => r.market },
  { key: "latest_price", label: "Latest price", get: (r) => r.latest_price, num: true, show: (r) => eur(r.latest_price) },
  { key: "usual", label: "Usual price", get: (r) => r.usual, num: true, show: (r) => eur(r.usual) },
  { key: "final", label: "Recommended", get: (r) => r.final, num: true, show: (r) => eur(r.final) },
  { key: "move_pct", label: "Change", get: (r) => r.move_pct, num: true, show: (r) => pct(r.move_pct) },
  {
    key: "profit_change_pct", label: "Profit change (model estimate)", get: (r) => r.profit_change_pct, num: true,
    show: (r) => pct(r.profit_change_pct),
  },
  { key: "tier", label: "Confidence", get: (r) => TIER_RANK[r.tier] },
];

export default function Overview() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [sort, setSort] = useState({ key: "default", dir: 1 });
  const [market, setMarket] = useState("all");
  const [tier, setTier] = useState("all");
  const [search, setSearch] = useState("");

  useEffect(() => {
    getOverview().then(setRows).catch((e) => setError(e.message));
  }, []);

  const shown = useMemo(() => {
    if (!rows) return [];
    let list = rows.filter(
      (r) =>
        (market === "all" || r.market === market) &&
        (tier === "all" || r.tier === tier) &&
        (r.name + r.product_id + r.category).toLowerCase().includes(search.toLowerCase())
    );
    if (sort.key === "default") {
      // Most reliable recommendations first, then the biggest expected profit change
      list = [...list].sort((a, b) => TIER_RANK[a.tier] - TIER_RANK[b.tier] || b.profit_change_pct - a.profit_change_pct);
    } else {
      const col = COLUMNS.find((c) => c.key === sort.key);
      list = [...list].sort((a, b) => {
        const x = col.get(a), y = col.get(b);
        return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
      });
    }
    return list;
  }, [rows, market, tier, search, sort]);

  if (error) return <p className="error">Could not load the data: {error}</p>;
  if (!rows) return <p>Loading…</p>;

  const clickSort = (key) => setSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: 1 }));
  const counts = { high: 0, medium: 0, low: 0 };
  rows.forEach((r) => counts[r.tier]++);

  return (
    <main>
      <h1>Which prices should change?</h1>
      <p className="lead">
        {rows.length} product-markets. Recommendations are limited to prices the shop has really tested, so moves are
        small. Click a row for the details and the AI analysis.
      </p>
      <div className="summary">
        {["high", "medium", "low"].map((t) => (
          <div key={t} className="tile">
            <b>{counts[t]}</b>
            <span>{TIER_LABEL[t]} confidence (product-markets)</span>
            <small>{TIER_ACTION[t]}</small>
          </div>
        ))}
      </div>

      <div className="filters">
        <input placeholder="Search product or category" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={market} onChange={(e) => setMarket(e.target.value)}>
          <option value="all">All markets</option>
          <option>DE</option><option>FR</option><option>CH</option>
        </select>
        <select value={tier} onChange={(e) => setTier(e.target.value)}>
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
              <tr key={r.product_id + r.market} onClick={() => (window.location.hash = `#/product/${r.product_id}/${r.market}`)}>
                {COLUMNS.map((c) => {
                  if (c.key === "name")
                    return (
                      <td key={c.key}>
                        <b>{r.name}</b>
                        <div className="muted">{r.product_id} · {r.category}</div>
                      </td>
                    );
                  if (c.key === "tier")
                    return (
                      <td key={c.key}>
                        <span className={`badge ${r.tier}`}>{TIER_LABEL[r.tier]}</span>
                        <div className="muted">
                          {r.tier === "medium" ? "Test price" : r.tier === "low" ? "Hold" : boundNote(r)}
                          {!r.ad_control ? " · no ad data" : ""}
                        </div>
                      </td>
                    );
                  return <td key={c.key} className={c.num ? "num" : ""}>{c.show ? c.show(r) : c.get(r)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && <p className="muted pad">No product matches these filters.</p>}
      </div>
    </main>
  );
}
