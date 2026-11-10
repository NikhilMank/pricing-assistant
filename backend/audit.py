"""Data audit: prints the facts about the dataset that shape the modelling choices.

Run from the backend folder:  ../.venv/bin/python audit.py
"""
from app.data import load_daily

PROMO_DISCOUNT = 0.90  # a day is a "promo candidate" if price < 90% of the usual price

df = load_daily()
series = df.groupby(["product_id", "market"])  # one series = one product in one market

# The usual price of a series = the price it spent most days at
df["usual_price"] = series["unit_price_eur"].transform(lambda s: s.mode().iloc[0])
df["is_promo"] = df["unit_price_eur"] < PROMO_DISCOUNT * df["usual_price"]
df["month"] = df["date"].dt.to_period("M")

print("== 1. Price variation per series (product x market) ==")
n_prices = series["unit_price_eur"].nunique()
print(f"series: {len(n_prices)}")
print("distinct prices per series:")
print(n_prices.describe().round(1).to_string())
print(f"series with 3 or fewer distinct prices: {(n_prices <= 3).sum()}")

print("\n== 2. Promo candidates (price < 90% of the usual price) ==")
print(f"share of days that are promo candidates: {df['is_promo'].mean():.1%}")
promo_by_month = df.groupby("month")["is_promo"].mean().sort_values(ascending=False)
print("months with the highest share of promo days:")
print(promo_by_month.head(5).map("{:.1%}".format).to_string())
by_promo = df.groupby("is_promo")["units_sold"].mean()
print(f"avg units/day, normal: {by_promo[False]:.2f}  promo: {by_promo[True]:.2f}")
print("(not a price effect yet: promos also bring traffic, which is the confounding problem)")

print("\n== 3. Trends over time ==")
print("total units by year and market:")
print(df.groupby([df["date"].dt.year.rename("year"), "market"])["units_sold"].sum().unstack().to_string())
monthly = df.groupby("month")["units_sold"].sum()
print("total units by month (all products, all markets):")
print(monthly.to_string())

print("\n== 4. Data quality ==")
print("share of blank ad_spend by market:")
print(df.groupby("market")["ad_spend_eur"].apply(lambda s: s.isna().mean()).map("{:.1%}".format).to_string())
print("share of blank ad_spend by year:")
print(df.groupby(df["date"].dt.year)["ad_spend_eur"].apply(lambda s: s.isna().mean()).map("{:.1%}".format).to_string())
print(f"share of days with zero units sold: {(df['units_sold'] == 0).mean():.1%}")
print("units_sold percentiles (50/99/99.9/max):",
      df["units_sold"].quantile([0.5, 0.99, 0.999, 1.0]).tolist())
print(f"days with price <= cost: {(df['unit_price_eur'] <= df['unit_cost_eur']).sum()}")
