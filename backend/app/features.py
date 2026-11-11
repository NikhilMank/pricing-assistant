import numpy as np
import pandas as pd

from app.data import load_daily


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add the columns the demand model and the optimizer need."""
    df = df.sort_values(["product_id", "market", "date"]).reset_index(drop=True)

    # Price elasticity is the coefficient on the log of the price
    df["lp"] = np.log(df["unit_price_eur"])

    # Black Friday control: 22 to 30 November
    df["bf"] = ((df["date"].dt.month == 11) & df["date"].dt.day.between(22, 30)).astype(int)

    # Ad spend control (log scale, +1 so a day with 0 spend still works). Stays empty where ad data is missing.
    df["ad"] = np.log1p(df["ad_spend_eur"])

    # Calendar controls: weekly pattern, season, and the growth from 2024 to 2025
    df["dow"] = df["date"].dt.day_of_week
    df["month"] = df["date"].dt.month
    df["year"] = df["date"].dt.year

    # A "run" is a stretch of consecutive days at the exact same price in one product-market.
    # run_days = the length of the run this row belongs to (used later to find "tested" prices).
    series = df.groupby(["product_id", "market"])
    price_changed = series["unit_price_eur"].diff().fillna(0) != 0
    df["run_id"] = price_changed.groupby([df["product_id"], df["market"]]).cumsum()
    df["run_days"] = df.groupby(["product_id", "market", "run_id"])["date"].transform("size")
    return df


if __name__ == "__main__":
    df = add_features(load_daily())
    print("new columns:", ["lp", "bf", "dow", "month", "year", "run_id", "run_days"])
    print("rows flagged Black Friday:", int(df["bf"].sum()), f"({df['bf'].mean():.1%})")
    runs = df.drop_duplicates(["product_id", "market", "run_id"])
    print("price runs:", len(runs), "| median length (days):", runs["run_days"].median())
    print("runs of 14+ days (tested prices):", f"{(runs['run_days'] >= 14).mean():.0%}")
    one = runs[(runs["product_id"] == "SKU-1000") & (runs["market"] == "DE")]
    print("\nSKU-1000 DE: first 8 price runs")
    print(one[["date", "unit_price_eur", "run_days"]].head(8).to_string(index=False))
