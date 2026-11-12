import { useState } from "react";
import { runAnalysis } from "./api.js";
import { eur } from "./format.js";

// Runs the three AI agents for one product-market and shows what each one said.
export default function AgentCards({ id, market }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = () => {
    setLoading(true);
    setError("");
    runAnalysis(id, market)
      .then(setResult)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  // Forget the old result when another product or market is opened
  const current = result && result.product_id === id && result.market === market ? result : null;

  return (
    <section className="card">
      <h2>AI analysis</h2>
      <p className="muted">
        Three AI agents read the numbers above and explain them. They never calculate prices: our code does that and
        also enforces the safety rules (never below cost, always inside the tested range).
      </p>
      <button className="primary" onClick={run} disabled={loading}>
        {loading ? "Analysing… this takes about 20 seconds" : current ? "Run again" : "Run AI analysis"}
      </button>
      {error && <p className="error">{error}</p>}

      {current && (
        <>
          <div className={`decision ${current.final.status}`}>
            {current.final.status === "approved" ? (
              <>Approved price: <b>{eur(current.final.price)}</b></>
            ) : (
              <>No change: showing the usual price <b>{eur(current.final.price)}</b></>
            )}
            <div className="muted">{current.final.note}</div>
          </div>
          <div className="agents">
            <div className="agent">
              <h3>1. Analyst</h3>
              <p>{current.analysis.summary}</p>
              <p className="muted">{current.analysis.confidence_note}</p>
              <p className="muted">{current.analysis.caveat}</p>
            </div>
            <div className="agent">
              <h3>2. Strategist</h3>
              <p><b>{current.proposal.action.replace("_", " ")}</b>: {eur(current.proposal.price)}</p>
              <p>{current.proposal.reasoning}</p>
              {current.strategist_runs > 1 && <p className="muted">Revised once after the reviewer's feedback.</p>}
            </div>
            <div className="agent">
              <h3>3. Risk reviewer</h3>
              <p><span className={`badge v-${current.review.verdict}`}>{current.review.verdict}</span></p>
              <p>{current.review.comment}</p>
            </div>
          </div>
          {current.cached && <p className="muted">Shown from the cache.</p>}
        </>
      )}
    </section>
  );
}
