"""Run the model and optimizer once and save everything the API needs to a JSON file.

Why: fitting all products takes about a minute. The API must not do that per request,
so we do it here, at build time, and the API just reads the file.

Run from the backend folder:  ../.venv/bin/python -W ignore precompute.py
"""
import json
from pathlib import Path

from app.data import load_daily
from app.features import add_features
from app.model import fit_product, live_products, promo_check, summarize_product
from app.optimizer import profit_curve, recommend, unrestricted_optimum

OUT = Path(__file__).resolve().parent / "artifacts" / "results.json"


def main() -> None:
    df = add_features(load_daily())
    products, series = {}, []

    for product in live_products(df):
        pdf = df[df["product_id"] == product]
        summary = summarize_product(pdf)
        fit = fit_product(pdf, "D")  # the price comes from the full model only
        d = summary["D"]
        first = pdf.iloc[0]
        products[product] = {
            "product_id": product, "name": first["product_name"], "category": first["category"],
            "brand": first["brand"], "tier": summary["tier"], "ad_control": summary["ad_control"],
            "elasticity": {k: round(d[k], 3) for k in ("elasticity", "se", "low", "high")},
            "variant_tiers": {v: summary[v]["tier"] for v in ("D", "C", "G")},
            "promo_check": {k: (round(v, 3) if isinstance(v, float) else v)
                            for k, v in promo_check(pdf, summary["tier"]).items()},
        }
        for market, g in pdf.groupby("market"):
            rec = recommend(g, fit, summary["tier"])
            latest = float(g["unit_price_eur"].iloc[-1])
            series.append({
                "product_id": product, "market": market, "latest_price": latest,
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in rec.items()},
                "curve": profit_curve(g, fit, rec),
                "unrestricted": unrestricted_optimum(rec["cost"], d["elasticity"], d["low"], d["high"]),
            })
        print("done", product, flush=True)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"products": products, "series": series}))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB): {len(products)} products, {len(series)} product-markets")


if __name__ == "__main__":
    main()
