"""Lightweight evaluation of the agents' output.

Checks, for a handful of example cases:
  1. structure       the output has the expected fields and types
  2. numbers         every number the agents cite appears in the data we gave them
  3. price safety    final price is not below cost and is inside the allowed range
  4. tier rules      low tier holds the usual price, medium moves at most 5%
  5. reject          a rejected proposal falls back to the usual price
  6. revision cap    the strategist runs at most twice
Plus a few tests of the safety rules and the revision loop with deliberately bad inputs.

Run from the backend folder:
  ../.venv/bin/python -W ignore evals.py          offline, with the fake model (free, instant)
  ../.venv/bin/python -W ignore evals.py --live   with the real model on Bedrock (about 20 s per case)
Exit code is 1 if any check fails.
"""
import math
import os
import re
import sys

if "--live" not in sys.argv:
    os.environ["LLM_PROVIDER"] = "fake"
else:
    os.environ["LLM_PROVIDER"] = "bedrock"

from app import agents  # noqa: E402  (after the environment is set)
from app.store import find_series, results  # noqa: E402

# Example cases. "expect" are facts about the case itself, to notice if the data or model ever changes.
CASES = [
    {"id": "SKU-1006", "market": "DE", "why": "low tier: must hold the usual price", "expect": {"confidence_tier": "low"}},
    {"id": "SKU-1015", "market": "DE", "why": "medium tier: test price, at most 5%", "expect": {"confidence_tier": "medium"}},
    {"id": "SKU-1013", "market": "DE", "why": "high tier: best price on the upper bound", "expect": {"confidence_tier": "high", "capped_at": "highest"}},
    {"id": "SKU-1000", "market": "FR", "why": "high tier: best price inside the range", "expect": {"confidence_tier": "high", "capped_at": None}},
    {"id": "SKU-1003", "market": "CH", "why": "latest price differs from the usual price", "expect": {}, "latest_differs": True},
]

# Numbers that may appear without being in the payload: our own rules (5% cap, 14 days, 20% range) and the
# "1%" in the usual way of explaining elasticity ("for every 1% price increase, sales drop by about X%").
ALLOWED_CONSTANTS = {1.0, 5.0, 14.0, 20.0}
VERDICTS = {"approve", "adjust", "reject"}
ACTIONS = {"hold", "raise", "cut", "test_price"}

failures = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"    [{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail and not ok else ""))
    failures += 0 if ok else 1


def payload_numbers(payload: dict) -> set[float]:
    """All numbers we gave the agents (absolute values, so '-1.98' and '1.98' both count)."""
    return {abs(float(v)) for v in payload.values() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def cited_numbers(texts: list[str], payload: dict) -> list[float]:
    """Numbers written in the agents' texts, ignoring product ids like SKU-1000."""
    found = []
    for text in texts:
        cleaned = re.sub(r"SKU-\d+", "", text)
        found += [abs(float(x)) for x in re.findall(r"-?\d+(?:\.\d+)?", cleaned.replace("−", "-"))]
    return found


def number_is_known(x: float, known: set[float]) -> bool:
    return x in ALLOWED_CONSTANTS or any(math.isclose(x, v, abs_tol=0.051) for v in known)


def check_structure(r: dict) -> bool:
    a, p, rv, f = r["analysis"], r["proposal"], r["review"], r["final"]
    return (all(isinstance(a.get(k), str) and a[k] for k in ("summary", "confidence_note", "caveat"))
            and p.get("action") in ACTIONS and isinstance(p.get("price"), (int, float))
            and isinstance(p.get("reasoning"), str)
            and rv.get("verdict") in VERDICTS and isinstance(rv.get("comment"), str)
            and isinstance(f.get("price"), (int, float)) and f.get("status") in {"approved", "rejected"})


def run_case(case: dict) -> None:
    print(f"\n{case['id']} {case['market']}: {case['why']}")
    payload_now = agents.build_payload(results()["products"][case["id"]], find_series(case["id"], case["market"]))
    for key, value in case["expect"].items():
        check(payload_now[key] == value, f"case setup: {key} == {value}", f"is {payload_now[key]}")

    if case.get("latest_differs"):
        gap = (payload_now["latest_price"] / payload_now["usual_price"] - 1) * 100
        check(abs(gap) >= 1, "case setup: latest price differs from the usual price by 1% or more", f"gap {gap:.1f}%")

    try:
        r = agents.analyze(case["id"], case["market"])
    except Exception as e:  # e.g. the model is unreachable: count it as a failure, keep going
        check(False, "the agents ran", f"{type(e).__name__}: {str(e)[:150]}")
        return
    p, f = r["payload"], r["final"]

    check("ad_spend" not in str(p), "no ad spend is given to the agents")
    check(check_structure(r), "output structure is valid")

    texts = [r["analysis"]["summary"], r["analysis"]["confidence_note"], r["analysis"]["caveat"],
             r["proposal"]["reasoning"], r["review"]["comment"]]
    known = payload_numbers(p)
    unknown = [x for x in cited_numbers(texts, p) if not number_is_known(x, known)]
    check(not unknown, "every cited number appears in the input data", f"not in data: {unknown}")
    if unknown and "--show" in sys.argv:
        for t in texts:
            print("      text:", t)

    check(f["price"] >= p["unit_cost"], "final price is not below cost", f"{f['price']} < {p['unit_cost']}")
    check(p["allowed_price_low"] - 0.005 <= f["price"] <= p["allowed_price_high"] + 0.005,
          "final price is inside the allowed range", f"{f['price']}")

    if p["confidence_tier"] == "low":
        check(math.isclose(f["price"], p["usual_price"], abs_tol=0.005), "low tier holds the usual price", f"{f['price']}")
    if p["confidence_tier"] == "medium":
        move = abs(f["price"] / p["usual_price"] - 1) * 100
        check(move <= agents.MEDIUM_CAP_PCT + 0.05, "medium tier moves at most 5%", f"{move:.1f}%")
    if f["status"] == "rejected":
        check(math.isclose(f["price"], p["usual_price"], abs_tol=0.005), "rejected gives the usual price", f"{f['price']}")
    check(r["strategist_runs"] <= agents.MAX_STRATEGIST_RUNS, "revision cap respected", f"{r['strategist_runs']} runs")


def rule_tests() -> None:
    """Deliberately bad inputs, using the fake model for the loop tests (so this part is always offline)."""
    print("\nSafety rules and revision loop (bad inputs on purpose)")
    series = find_series("SKU-1015", "DE")
    payload = agents.build_payload(results()["products"]["SKU-1015"], series)
    approve = {"verdict": "approve", "comment": "ok"}

    below_cost = {"price": payload["unit_cost"] - 1}
    check(agents.decide_final(payload, below_cost, approve)["status"] == "rejected", "price below cost is rejected")
    too_bold = {"price": payload["allowed_price_high"] + 5}
    check(agents.decide_final(payload, too_bold, approve)["price"] == payload["usual_price"],
          "price outside the range falls back to the usual price")
    beyond_tier = {"price": payload["recommended_price"] + 1}
    check(agents.decide_final(payload, beyond_tier, approve)["status"] == "rejected",
          "price bolder than the tier allows is rejected")
    check(agents.decide_final(payload, {"price": payload["recommended_price"]}, approve)["status"] == "approved",
          "a safe, approved proposal passes")

    known = payload_numbers(payload)
    invented = cited_numbers(["A 10% price rise would cut sales by about 15%."], payload)
    check(any(not number_is_known(x, known) for x in invented), "the number check catches an invented number (15%)")
    honest = cited_numbers([f"The usual price is {payload['usual_price']} EUR."], payload)
    check(all(number_is_known(x, known) for x in honest), "the number check accepts a number from the data")

    saved = (os.environ.get("LLM_PROVIDER"), os.environ.get("FAKE_REVIEWER_VERDICT"))
    os.environ["LLM_PROVIDER"] = "fake"
    for verdict in ("adjust", "reject"):
        os.environ["FAKE_REVIEWER_VERDICT"] = verdict
        agents._cache.clear()
        r = agents.analyze("SKU-1015", "DE")
        runs_ok = r["strategist_runs"] == (2 if verdict == "adjust" else 1)
        check(runs_ok, f"reviewer says '{verdict}': strategist runs {2 if verdict == 'adjust' else 1} time(s)",
              f"{r['strategist_runs']}")
        check(r["final"]["status"] == "rejected" and r["final"]["price"] == payload["usual_price"],
              f"reviewer says '{verdict}': usual price is shown")
    os.environ.pop("FAKE_REVIEWER_VERDICT", None)
    if saved[0]:
        os.environ["LLM_PROVIDER"] = saved[0]
    agents._cache.clear()


def main() -> int:
    mode = "LIVE (Bedrock)" if "--live" in sys.argv else "offline (fake model)"
    print(f"Agent evaluation: {mode}")
    for case in CASES:
        run_case(case)
    rule_tests()
    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
