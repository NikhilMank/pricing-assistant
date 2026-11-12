"""Demand model: one Poisson regression per product.

The coefficient on lp (= log price) is the price elasticity.
We fit three variants (D, C, G) and the final confidence tier is the weakest of the three.
"""
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

from app.data import load_daily
from app.features import add_features

Z = 1.96  # 95% confidence interval: estimate +/- 1.96 x standard error

# The controls each variant uses. D = full model, C = without year, G = without Black Friday.
D_TERMS = ["lp", "C(market)", "bf", "C(dow)", "C(month)", "C(year)"]
VARIANTS = {
    "D": D_TERMS,
    "C": [t for t in D_TERMS if t != "C(year)"],
    "G": [t for t in D_TERMS if t != "bf"],
}

TIER_RANK = {"low": 0, "medium": 1, "high": 2}

# A day counts as a promotion day if its price is below 90% of the usual price of that product-market.
# The app draws the red dots with the same rule (see PROMO_DISCOUNT in main.py).
PROMO_DISCOUNT = 0.90
USUAL_DAYS = 90  # usual price = median of the last 90 days (same as the optimizer)


def has_ad_data(product_df: pd.DataFrame) -> bool:
    """9 products have no ad spend at all. They are fitted without the ad term."""
    return bool(product_df["ad"].notna().all())


def build_formula(variant: str, with_ad: bool) -> str:
    terms = list(VARIANTS[variant])
    if with_ad:
        terms.append("ad")
    return "units_sold ~ " + " + ".join(terms)


def fit_product(product_df: pd.DataFrame, variant: str = "D"):
    """Fit one Poisson model for one product (all its markets together)."""
    formula = build_formula(variant, has_ad_data(product_df))
    model = smf.glm(formula, data=product_df, family=sm.families.Poisson())
    return model.fit(cov_type="HC1")  # HC1 = robust standard errors


def tier_from_interval(elasticity: float, se: float) -> str:
    """Confidence tier from the UPPER end of the interval (the least negative value we can't rule out)."""
    upper = elasticity + Z * se
    if upper < -1:
        return "high"    # sure buyers are sensitive: raising the price loses more than it gains
    if upper < 0:
        return "medium"  # buyers react, but a raise might still pay off
    return "low"         # not even sure that a higher price lowers sales


def summarize_product(product_df: pd.DataFrame) -> dict:
    """Fit the three variants and combine them. Price recommendation later uses variant D only."""
    out = {"product_id": product_df["product_id"].iloc[0], "ad_control": has_ad_data(product_df)}
    tiers = []
    for variant in VARIANTS:
        res = fit_product(product_df, variant)
        e, se = float(res.params["lp"]), float(res.bse["lp"])
        tier = tier_from_interval(e, se)
        out[variant] = {"elasticity": e, "se": se, "low": e - Z * se, "high": e + Z * se, "tier": tier}
        tiers.append(tier)
    out["tier"] = min(tiers, key=TIER_RANK.get)  # weakest of the three
    return out


def promo_check(product_df: pd.DataFrame, final_tier: str) -> dict:
    """Does the result still hold when promotion days are left out?

    Same model (variant D), fitted again on the normal-price days only. The tier rule is applied to the new interval
    and compared with the product's final tier. Informational only: tiers and prices do not change.
    verdict: "holds" (same tier or better), "weaker" (lower tier, but the estimate itself is still negative),
    "noisy" (lower tier and the estimate is 0 or positive: no clear reaction to price), None (nothing to say:
    no promotion days, or already low with all days, or the fit failed).
    """
    usual = product_df.groupby("market")["unit_price_eur"].transform(lambda s: s.tail(USUAL_DAYS).median())
    is_promo = product_df["unit_price_eur"] < PROMO_DISCOUNT * usual
    out = {"promo_days": int(is_promo.sum()), "total_days": int(len(product_df)),
           "elasticity": None, "low": None, "high": None, "tier": None, "verdict": None}
    if out["promo_days"] == 0:
        return out
    try:
        res = fit_product(product_df[~is_promo], "D")
    except Exception:  # e.g. too few rows left to fit: say nothing rather than guess
        return out
    e, se = float(res.params["lp"]), float(res.bse["lp"])
    if not (se == se and se > 0):  # nan or zero: the fit did not work
        return out
    tier = tier_from_interval(e, se)
    out.update(elasticity=e, low=e - Z * se, high=e + Z * se, tier=tier)
    if final_tier != "low":
        if TIER_RANK[tier] >= TIER_RANK[final_tier]:
            out["verdict"] = "holds"
        elif e >= 0:
            out["verdict"] = "noisy"   # without promotions the data shows no reaction (or the opposite)
        else:
            out["verdict"] = "weaker"  # still negative, only smaller and less certain
    return out


def live_products(df: pd.DataFrame) -> list[str]:
    """Products still on sale at the end of the data (SKU-1008 stops in mid-2025)."""
    last = df.groupby("product_id")["date"].max()
    return sorted(last[last == df["date"].max()].index)


def fit_all(df: pd.DataFrame) -> list[dict]:
    products = live_products(df)
    return [summarize_product(df[df["product_id"] == p]) for p in products]


if __name__ == "__main__":
    df = add_features(load_daily())

    one = summarize_product(df[df["product_id"] == "SKU-1000"])
    print("SKU-1000 (ad term:", one["ad_control"], ")")
    for v in VARIANTS:
        r = one[v]
        print(f"  variant {v}: b={r['elasticity']:.2f}, se={r['se']:.2f}, "
              f"interval {r['low']:.2f} to {r['high']:.2f} -> {r['tier']}")
    print("  final tier (weakest):", one["tier"])

    results = fit_all(df)
    tiers = pd.Series([r["tier"] for r in results]).value_counts()
    print(f"\n{len(results)} live products, final tiers:",
          {t: int(tiers.get(t, 0)) for t in ["high", "medium", "low"]})
    print("target (other agent, with ad): high 34, medium 20, low 5")
    print("without ad control:", sum(not r["ad_control"] for r in results), "products")
    print("median elasticity (variant D):",
          round(pd.Series([r["D"]["elasticity"] for r in results]).median(), 2))
