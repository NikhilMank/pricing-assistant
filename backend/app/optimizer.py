"""Price optimizer.

Part 1: for each product-market, the usual price and the allowed price range.
Part 2: try prices inside the range, pick the most profitable one, and move by the confidence tier.
"""
import numpy as np
import pandas as pd

from app.data import load_daily
from app.features import add_features
from app.model import fit_product, live_products, summarize_product

USUAL_DAYS = 90   # the usual price = median of the last 90 days
MAX_MOVE = 0.20   # never go more than 20% away from the usual price
MIN_RUN = 14      # a price is "tested" if it was held at least this many days in a row


def usual_price(series_df: pd.DataFrame) -> float:
    """Median price of the last 90 days of one product-market (rows are sorted by date)."""
    return float(series_df["unit_price_eur"].tail(USUAL_DAYS).median())


def allowed_range(series_df: pd.DataFrame) -> dict:
    """The lowest and highest price the optimizer may recommend for one product-market."""
    usual = usual_price(series_df)
    cost = float(series_df["unit_cost_eur"].iloc[0])  # cost is constant per product
    tested = series_df.loc[series_df["run_days"] >= MIN_RUN, "unit_price_eur"]

    low = max(tested.min(), usual * (1 - MAX_MOVE), cost)
    high = min(tested.max(), usual * (1 + MAX_MOVE))
    return {
        "usual": usual,
        "cost": cost,
        "tested_min": float(tested.min()),
        "tested_max": float(tested.max()),
        "low": float(low),
        "high": float(high),
    }


def all_ranges(df: pd.DataFrame) -> pd.DataFrame:
    """One row per live product-market with its usual price and allowed range."""
    rows = []
    live = df[df["product_id"].isin(live_products(df))]
    for (product, market), g in live.groupby(["product_id", "market"]):
        rows.append({"product_id": product, "market": market, **allowed_range(g)})
    return pd.DataFrame(rows)


GRID_POINTS = 41             # candidate prices between the lowest and highest allowed price
REPLAY_FROM = "2025-10-01"   # replay the days since this date to predict units
MEDIUM_CAP = 0.05            # medium tier: move at most 5% of the usual price
MEDIUM_SHARE = 0.5           # medium tier: go half the way to the best price


def predicted_units(fit, replay_rows: pd.DataFrame, price: float) -> float:
    """Units the model predicts over the replayed days if the price were `price` (only lp changes)."""
    scenario = replay_rows.copy()
    scenario["lp"] = np.log(price)
    return float(fit.predict(scenario).sum())


def profit_at(fit, replay_rows: pd.DataFrame, price: float, cost: float) -> float:
    return (price - cost) * predicted_units(fit, replay_rows, price)


def recommend(series_df: pd.DataFrame, fit, tier: str) -> dict:
    """Best price and the recommended price for one product-market."""
    rng = allowed_range(series_df)
    usual, cost = rng["usual"], rng["cost"]

    # Replay recent days without Black Friday (an unusual period) with only the price changed
    replay = series_df[(series_df["date"] >= REPLAY_FROM) & (series_df["bf"] == 0)]

    candidates = np.append(np.linspace(rng["low"], rng["high"], GRID_POINTS), usual)
    profits = [profit_at(fit, replay, p, cost) for p in candidates]
    best = float(candidates[int(np.argmax(profits))])

    # The tier decides how far we move from the usual price towards the best price
    if tier == "high":
        final = best
    elif tier == "medium":
        step = (best - usual) * MEDIUM_SHARE
        final = usual + float(np.clip(step, -MEDIUM_CAP * usual, MEDIUM_CAP * usual))
    else:
        final = usual

    base_profit = profit_at(fit, replay, usual, cost)
    final_profit = profit_at(fit, replay, final, cost)
    return {
        **rng,
        "tier": tier,
        "best": best,
        "final": round(final, 2),
        "move_pct": final / usual - 1,
        "profit_change_pct": final_profit / base_profit - 1,
        "at_bound": bool(np.isclose(best, rng["low"]) or np.isclose(best, rng["high"])),
    }


def recommend_all(df: pd.DataFrame) -> pd.DataFrame:
    """Recommendation for every live product-market."""
    rows = []
    for product in live_products(df):
        product_df = df[df["product_id"] == product]
        tier = summarize_product(product_df)["tier"]   # weakest of the three variants
        fit = fit_product(product_df, "D")             # the price comes from the full model only
        for market, g in product_df.groupby("market"):
            rows.append({"product_id": product, "market": market, **recommend(g, fit, tier)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = add_features(load_daily())
    ranges = all_ranges(df)

    one = ranges[(ranges["product_id"] == "SKU-1000") & (ranges["market"] == "DE")].iloc[0]
    print("SKU-1000 DE")
    print(f"  usual price {one['usual']:.2f}, cost {one['cost']:.2f}")
    print(f"  tested prices span {one['tested_min']:.2f} to {one['tested_max']:.2f}")
    print(f"  +-20% of usual spans {one['usual'] * 0.8:.2f} to {one['usual'] * 1.2:.2f}")
    print(f"  allowed range {one['low']:.2f} to {one['high']:.2f}")

    print(f"\nproduct-markets: {len(ranges)}")
    print("range is empty (low > high):", int((ranges["low"] > ranges["high"]).sum()))
    print("usual price outside the range:",
          int(((ranges["usual"] < ranges["low"]) | (ranges["usual"] > ranges["high"])).sum()))
    width = (ranges["high"] / ranges["low"] - 1).round(3)
    print("range width (high/low - 1): median", width.median(), "| min", width.min(), "| max", width.max())

    print("\n== Part 2: recommendations ==")
    recs = recommend_all(df)
    move = recs["move_pct"]
    cuts, raises = (move <= -0.005).mean(), (move >= 0.005).mean()
    print(f"cuts {cuts:.0%} | raises {raises:.0%} | hold {1 - cuts - raises:.0%}   (target 27 / 41 / 32)")
    print(f"median |move| {move.abs().median():.1%} | p90 {move.abs().quantile(0.9):.1%}   (target 2.1% / 6.8%)")
    print(f"best price on a bound of the range: {recs['at_bound'].mean():.0%}")
    print("profit change (model estimate): median", f"{recs['profit_change_pct'].median():.1%}",
          "| p90", f"{recs['profit_change_pct'].quantile(0.9):.1%}")
    cols = ["product_id", "market", "tier", "usual", "low", "high", "best", "final", "move_pct", "profit_change_pct"]
    cases = [("SKU-1006", "DE"), ("SKU-1015", "DE"), ("SKU-1013", "DE"), ("SKU-1000", "FR"), ("SKU-1003", "CH"), ("SKU-1000", "DE")]
    show = pd.concat([recs[(recs["product_id"] == p) & (recs["market"] == m)] for p, m in cases])
    print("\nexample product-markets:")
    print(show[cols].round(3).to_string(index=False))
