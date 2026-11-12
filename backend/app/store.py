"""Loads the saved model results (made by precompute.py). Shared by the API, the agents and the evals."""
import json
from functools import lru_cache
from pathlib import Path

RESULTS_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "results.json"


@lru_cache
def results() -> dict:
    """The saved model output. Loaded once per process."""
    return json.loads(RESULTS_PATH.read_text())


def find_series(product_id: str, market: str) -> dict | None:
    for s in results()["series"]:
        if s["product_id"] == product_id and s["market"] == market:
            return s
    return None
