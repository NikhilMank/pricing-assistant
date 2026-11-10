from pathlib import Path

import pandas as pd

# repo_root/data/daily_sales.csv, found relative to this file
DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "daily_sales.csv"


def _parse_dates(col: pd.Series) -> pd.Series:
    """ISO dates (2024-01-01) normally; Excel re-saves can give 01-31-24."""
    try:
        return pd.to_datetime(col, format="ISO8601")
    except ValueError:
        return pd.to_datetime(col, format="%m-%d-%y")


def load_daily(path: Path = DATA_PATH) -> pd.DataFrame:
    """Load the raw daily data: one row per product, market and day."""
    # utf-8-sig drops the hidden BOM character that Excel adds to the first column name
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["date"] = _parse_dates(df["date"])
    return df


if __name__ == "__main__":
    df = load_daily()
    print("rows:", len(df))
    print("columns:", list(df.columns))
    print("date range:", df["date"].min().date(), "to", df["date"].max().date())
    print("blank values per column:")
    print(df.isna().sum().to_string())
