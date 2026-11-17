"""API. Serves the precomputed model results (artifacts/results.json) and the daily history for charts.

Run locally from the backend folder:  ../.venv/bin/uvicorn app.main:app --reload
"""
import importlib.util
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictBool

from app.data import load_daily
from app.store import find_series, results

PROMO_DISCOUNT = 0.90  # a day is marked as a promo day if the price is below 90% of the usual price
MAX_REFITS = int(os.environ.get("MAX_REFITS", "300"))  # cost guard: custom refits per server process

app = FastAPI(title="Pricing Assistant API")

# Which websites may call this API from a browser. Set CORS_ORIGINS="https://your-site.example" if the frontend is served from another address.
origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])


@lru_cache
def daily() -> object:
    """The raw daily data (used for the charts). Loaded once per process."""
    return load_daily()


def refit_available() -> bool:
    """The custom recalculation needs statsmodels. It is installed everywhere now; this check stays as a guard (501) in case it is missing."""
    return importlib.util.find_spec("statsmodels") is not None  # only looks, does not import


@app.get("/api/health")
def health():
    return {"status": "ok", "product_markets": len(results()["series"]), "refit": refit_available()}


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


class RefitOptions(BaseModel):
    """The only settings the custom refit accepts. Anything else (or a wrong type) is rejected with 422."""
    model_config = ConfigDict(extra="forbid")
    include_market: StrictBool = True
    include_black_friday_flag: StrictBool = True
    include_weekday: StrictBool = True
    include_month: StrictBool = True
    include_year: StrictBool = True
    include_ad_spend: StrictBool = True            # ignored (with a note) for products without ad data
    exclude_promotion_days: StrictBool = False     # leave out days priced below 90% of the usual price
    min_tested_days: Literal[7, 14, 28] = 14       # days a price must be held in a row to count as tested


_refit_cache: dict = {}   # (product, options) -> result, in memory only; nothing is saved to disk
_refit_lock = threading.Lock()
_refit_count = 0


@lru_cache
def features_df():
    """The daily data with the model columns added. Only needed by the custom refit; loaded once per process."""
    from app.features import add_features
    return add_features(daily())


def _num(x, digits=4):
    return None if x is None else round(float(x), digits)


def _custom_refit(product_id: str, product: dict, options: RefitOptions, curves: bool = False) -> dict:
    """Fit one product with the chosen options and build the price recommendation for each of its markets.
    With curves=True each market also gets the custom profit curve (for the charts on the Custom refit page)."""
    from app.model import Z, fit_custom, tier_from_interval  # imported here: needs statsmodels
    from app.optimizer import profit_curve, recommend, unrestricted_optimum

    pdf = features_df()
    pdf = pdf[pdf["product_id"] == product_id]
    fit, info = fit_custom(pdf, options.include_year, options.include_black_friday_flag,
                           options.include_ad_spend, options.exclude_promotion_days,
                           options.include_market, options.include_weekday, options.include_month)
    e, se = float(fit.params["lp"]), float(fit.bse["lp"])
    if not (se == se and se > 0):  # nan or zero: the fit did not work
        raise ValueError("the model could not be fitted with these options")
    tier = tier_from_interval(e, se)  # same rule as the official tier, but from this single model version

    rows = []
    for market, g in pdf.groupby("market"):
        # same optimizer, same tier rules (half-step for medium, hold for low) as the official recommendation
        rec = recommend(g, fit, tier, min_run=options.min_tested_days)
        official = find_series(product_id, market)
        custom = {
            "usual": _num(rec["usual"], 2), "low": _num(rec["low"], 2), "high": _num(rec["high"], 2),
            "final": _num(rec["final"], 2), "move_pct": _num(rec["move_pct"]),
            "profit_change_pct": _num(rec["profit_change_pct"]),
            "bound": _which_bound(rec), "note": rec.get("note"),
        }
        if curves:  # same grid and same rule as the official curve, but from the custom fit
            custom["curve"] = profit_curve(g, fit, rec)
            custom["unrestricted"] = unrestricted_optimum(rec["cost"], e, e - Z * se, e + Z * se)
        rows.append({
            "market": market,
            "latest_price": official["latest_price"],
            "custom": custom,
            "official": {
                "tier": official["tier"], "usual": official["usual"], "low": official["low"], "high": official["high"],
                "final": official["final"], "move_pct": official["move_pct"],
                "profit_change_pct": official["profit_change_pct"], "bound": _which_bound(official),
            },
        })
    return {
        "product_id": product_id, "name": product["name"], "category": product["category"],
        "ad_control": product["ad_control"], "ad_spend_ignored": info["ad_ignored"],
        "days_removed": info["days_removed"],
        "options": options.model_dump(),
        "custom": {"elasticity": _num(e), "se": _num(se), "low": _num(e - Z * se), "high": _num(e + Z * se), "tier": tier},
        "official": {"elasticity": product["elasticity"], "tier": product["tier"],
                     "main_model_tier": product["variant_tiers"]["D"]},
        "markets": rows,
    }


@app.post("/api/refit/{product_id}")
def custom_refit(product_id: str, options: RefitOptions, curves: bool = False):
    """Custom refit of one product (all its markets) with the chosen options. Never changes the official results.
    ?curves=true adds the custom profit curve per market (used by "Refit this product", not by "Refit all")."""
    global _refit_count
    if not refit_available():
        raise HTTPException(501, "Refit is not available in this deployment")
    product = results()["products"].get(product_id)
    if product is None:
        raise HTTPException(404, f"Unknown or delisted product: {product_id}")

    key = (product_id, tuple(options.model_dump().items()), curves)
    with _refit_lock:
        if key in _refit_cache:
            return _refit_cache[key]
        if _refit_count >= MAX_REFITS:
            raise HTTPException(429, "The limit of custom refits for this server has been reached. Try again later.")
        _refit_count += 1
    try:
        out = _custom_refit(product_id, product, options, curves)
    except Exception as e:  # e.g. too few rows left, or a fit that does not converge
        raise HTTPException(422, f"Could not refit this product with these options: {e}")
    _refit_cache[key] = out
    return out


# If the frontend has been built (frontend/dist), serve it too: one process serves the whole app.
# Must come after the API routes above so that /api/... is matched first.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
