"""API. Serves the precomputed model results (artifacts/results.json) and the daily history for charts.

Run locally from the backend folder:  ../.venv/bin/uvicorn app.main:app --reload
"""
import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.data import load_daily
from app.store import find_series, results

PROMO_DISCOUNT = 0.90  # a day is marked as a promo day if the price is below 90% of the usual price

app = FastAPI(title="Pricing Assistant API")

# Which websites may call this API from a browser. Set CORS_ORIGINS="https://xyz.cloudfront.net" when deployed.
origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])


@lru_cache
def daily() -> object:
    """The raw daily data (used for the charts). Loaded once per process."""
    return load_daily()


@app.get("/api/health")
def health():
    return {"status": "ok", "product_markets": len(results()["series"])}


@app.get("/api/overview")
def overview():
    """One row per product-market for the landing table (no heavy chart data)."""
    products = results()["products"]
    rows = []
    for s in results()["series"]:
        p = products[s["product_id"]]
        rows.append({
            "product_id": s["product_id"], "name": p["name"], "category": p["category"], "market": s["market"],
            "tier": s["tier"], "ad_control": p["ad_control"],
            "latest_price": s["latest_price"], "usual": s["usual"], "final": s["final"],
            "move_pct": s["move_pct"], "profit_change_pct": s["profit_change_pct"],
            "at_bound": s["at_bound"], "bound": _which_bound(s),
        })
    return rows


def _which_bound(s: dict):
    """Which edge of the allowed range the best price sits on, if any (for the label 'capped at ...')."""
    if not s["at_bound"]:
        return None
    return "lowest" if abs(s["best"] - s["low"]) < abs(s["best"] - s["high"]) else "highest"


@app.get("/api/product/{product_id}")
def product_detail(product_id: str):
    """Everything for one product: model info, and per market the history, recommendation and profit curve."""
    product = results()["products"].get(product_id)
    if product is None:
        raise HTTPException(404, f"Unknown or delisted product: {product_id}")

    markets = {}
    for s in results()["series"]:
        if s["product_id"] != product_id:
            continue
        g = daily()
        g = g[(g["product_id"] == product_id) & (g["market"] == s["market"])].sort_values("date")
        history = {
            "date": g["date"].dt.strftime("%Y-%m-%d").tolist(),
            "price": g["unit_price_eur"].tolist(),
            "units": g["units_sold"].tolist(),
            "promo": (g["unit_price_eur"] < PROMO_DISCOUNT * s["usual"]).tolist(),
        }
        markets[s["market"]] = {**s, "bound": _which_bound(s), "history": history}
    return {**product, "markets": markets}


@app.post("/api/analyze/{product_id}/{market}")
def analyze_product_market(product_id: str, market: str):
    """Run the three agents (Analyst, Strategist, Risk reviewer) for one product-market."""
    if find_series(product_id, market) is None:
        raise HTTPException(404, f"Unknown product-market: {product_id} {market}")
    from app.agents import analyze  # imported here so the API starts fast
    try:
        return analyze(product_id, market)
    except RuntimeError as e:  # cost guard reached
        raise HTTPException(429, str(e))
    except Exception as e:  # model unreachable, invalid answer, ...
        raise HTTPException(502, f"The AI analysis failed: {e}")


# If the frontend has been built (frontend/dist), serve it too: one process serves the whole app.
# Must come after the API routes above so that /api/... is matched first.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
