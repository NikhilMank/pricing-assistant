"""Lightweight evaluation of the agents' output.

Checks, for a handful of example cases:
  1. structure       the output has the expected fields and types
  2. numbers         every number the agents cite appears in the data we gave them
  3. price safety    final price is not below cost and is inside the allowed range
  4. tier rules      low tier holds the usual price, medium moves at most 5%
  5. reject          a rejected proposal falls back to the usual price
  6. revision cap    the strategist runs at most twice
Plus a few tests of the safety rules and the revision loop with deliberately bad inputs.
Plus tests of the custom refit (no model needed): with default options it reproduces the saved official numbers,
its prices obey the same rules, and invalid options are rejected.

Run from the backend folder:
  ../.venv/bin/python -W ignore evals.py          offline, with the fake model (free, instant)
  ../.venv/bin/python -W ignore evals.py --live   with the real model (Gemini on Vertex AI, or LLM_PROVIDER=bedrock for Claude on Bedrock)
Exit code is 1 if any check fails.
"""
import importlib.util
import math
import os
import re
import sys

if "--live" not in sys.argv:
    os.environ["LLM_PROVIDER"] = "fake"
else:
    # real model: Vertex AI (Gemini) unless LLM_PROVIDER=bedrock is set
    os.environ["LLM_PROVIDER"] = "bedrock" if os.environ.get("LLM_PROVIDER") == "bedrock" else "vertex"

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


def mentions_promotions(texts: list[str]) -> bool:
    return any("promotion" in text.lower() for text in texts)


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

    if p["promotion_check"] in ("weaker", "noisy"):
        a = r["analysis"]
        check(mentions_promotions([a["summary"], a["confidence_note"], a["caveat"]]),
              f"the analyst mentions the promotion check ({p['promotion_check']})")

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

    check(not mentions_promotions(["Buyers are price-sensitive and the usual price is 50.95 EUR."]),
          "the promotion-mention check catches a text that ignores the warning")
    check(mentions_promotions(["Without the weeks with promotions the effect looks weaker."]),
          "the promotion-mention check accepts a text that mentions it")

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


def close(a: float, b: float, tol: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


# Products for the refit tests: SKU-1000 and SKU-1013 high, SKU-1015 medium, SKU-1006 low (main-model tier).
REFIT_PRODUCTS = ["SKU-1000", "SKU-1013", "SKU-1015", "SKU-1006"]
# Settings tried for the price rules; "min_tested_days" 7 and 28 change the allowed range, the last one changes the fit.
REFIT_SETTINGS = [{}, {"min_tested_days": 7}, {"min_tested_days": 28}, {"exclude_promotion_days": True},
                  {"include_market": False}, {"include_weekday": False, "include_month": False}]


def refit_tests() -> None:
    """The custom refit against the saved official results. Uses the refit code directly, no model and no server."""
    print("\nCustom refit (compared with the saved official results)")
    if importlib.util.find_spec("statsmodels") is None:
        print("    [SKIP] statsmodels is not installed, so the refit is not available here")
        return
    from pydantic import ValidationError

    from app import main as api
    from app.model import fit_custom
    from app.optimizer import recommend

    saved = results()

    def refit(pid: str, **options) -> dict:
        return api.custom_refit(pid, api.RefitOptions(**options))

    features = api.features_df()

    # (a) default options reproduce the official main model (variant D)
    for pid in REFIT_PRODUCTS[:3]:
        official, d = saved["products"][pid], refit(pid)
        c, oe = d["custom"], official["elasticity"]
        check(close(c["elasticity"], oe["elasticity"], 6e-4) and close(c["se"], oe["se"], 6e-4),
              f"{pid}: default refit gives the official elasticity and standard error",
              f"{c['elasticity']}/{c['se']} vs {oe['elasticity']}/{oe['se']}")
        check(c["tier"] == official["variant_tiers"]["D"], f"{pid}: default refit gives the main-model tier",
              f"{c['tier']} vs {official['variant_tiers']['D']}")
        pdf = features[features["product_id"] == pid]
        fit, _ = fit_custom(pdf)
        for market, g in pdf.groupby("market"):
            rec, s = recommend(g, fit, c["tier"]), find_series(pid, market)
            check(all(close(rec[k], s[k], 1e-3) for k in ("best", "low", "high", "usual")),
                  f"{pid} {market}: default refit gives the official best price and allowed range")
        if c["tier"] == official["tier"]:  # same tier as the official one: the whole recommendation must match
            for m in d["markets"]:
                cu, of = m["custom"], m["official"]
                check(close(cu["final"], of["final"], 6e-3) and close(cu["move_pct"], of["move_pct"], 5e-4)
                      and close(cu["profit_change_pct"], of["profit_change_pct"], 5e-4),
                      f"{pid} {m['market']}: default refit gives the official price, change and profit change")

    # (b) leaving out promotion days gives the same result as the promotion check
    for pid in REFIT_PRODUCTS:
        stored = saved["products"][pid]["promo_check"]
        d = refit(pid, exclude_promotion_days=True)
        check(close(d["custom"]["elasticity"], stored["elasticity"], 6e-4) and d["days_removed"] == stored["promo_days"],
              f"{pid}: leaving out promotion days matches the promotion check",
              f"{d['custom']['elasticity']}/{d['days_removed']} vs {stored['elasticity']}/{stored['promo_days']}")

    # (b2) every part of the model can be switched off on its own, and only that part leaves the model
    all_rows = api.features_df()
    pdf_one = all_rows[all_rows["product_id"] == "SKU-1000"]
    term_of = {"include_market": "market", "include_black_friday_flag": "bf", "include_weekday": "dow",
               "include_month": "month", "include_year": "year", "include_ad_spend": "ad"}
    for option, term in term_of.items():
        fit, _ = fit_custom(pdf_one, **{option: False})
        left_out = [k for k in term_of.values() if not any(n == k or n.startswith(f"C({k})") for n in fit.params.index)]
        check(left_out == [term] and "lp" in fit.params.index, f"switching off only {option} removes only {term} (price stays)", f"left out: {left_out}")
    fit, _ = fit_custom(pdf_one, **{option: False for option in term_of})
    check(list(fit.params.index) == ["Intercept", "lp"] and fit.bse["lp"] > 0,
          "everything switched off: only the price is left and the fit still works")
    check(api.RefitOptions().model_dump() == {**{o: True for o in term_of}, "exclude_promotion_days": False, "min_tested_days": 14},
          "default options switch every part on and keep promotion days")

    # (c) invalid options are rejected, valid ones accepted
    bad = [{"min_tested_days": 10}, {"min_tested_days": "14"}, {"min_tested_days": 0}, {"colour": "red"},
           {"include_year": "maybe"}, {"include_year": 1}, {"exclude_promotion_days": None},
           {"include_market": "yes"}, {"include_weekday": 0}, {"include_month": None}, {"include_price": False}]
    not_rejected = []
    for options in bad:
        try:
            api.RefitOptions(**options)
            not_rejected.append(options)
        except ValidationError:
            pass
    check(not not_rejected, f"invalid options are rejected ({len(bad)} cases)", f"accepted: {not_rejected}")
    accepted = all(api.RefitOptions(min_tested_days=n).min_tested_days == n for n in (7, 14, 28))
    check(accepted, "the three allowed values for tested days (7, 14, 28) are accepted")
    for pid in ("SKU-1008", "SKU-9999"):
        try:
            refit(pid)
            check(False, f"unknown or delisted product {pid} is refused")
        except api.HTTPException as e:
            check(e.status_code == 404, f"unknown or delisted product {pid} is refused", f"status {e.status_code}")

    # (d) every custom price obeys the range and cost rules; (e) the tier rules still hold
    rows = low_cases = 0
    problems = []
    for pid in REFIT_PRODUCTS:
        for options in REFIT_SETTINGS:
            d = refit(pid, **options)
            tier = d["custom"]["tier"]
            low_cases += tier == "low"
            for m in d["markets"]:
                cu, cost = m["custom"], find_series(pid, m["market"])["cost"]
                rows += 1
                if not (cu["low"] - 6e-3 <= cu["final"] <= cu["high"] + 6e-3 and cu["final"] >= cost - 6e-3):
                    problems.append(f"{pid} {m['market']} {options}: price {cu['final']} outside {cu['low']}..{cu['high']} or below cost {cost}")
                if tier == "low" and not (close(cu["final"], cu["usual"], 6e-3) and abs(cu["move_pct"]) < 1e-9):
                    problems.append(f"{pid} {m['market']} {options}: low tier moved the price")
                if tier == "medium" and abs(cu["move_pct"]) > 0.05 + 1e-3:
                    problems.append(f"{pid} {m['market']} {options}: medium tier moved more than 5%")
    check(not problems, f"all {rows} custom prices are inside the allowed range, above cost and follow the tier rules",
          "; ".join(problems[:3]))
    check(low_cases > 0, "case setup: at least one custom refit lands in the low tier (so the hold rule was tested)")

    # (f) the profit curve for the Custom refit charts: only on request, and with default options it is the official curve
    check(all("curve" not in m["custom"] for m in refit("SKU-1000")["markets"]),
          "no profit curve is sent unless it is asked for (Refit all stays light)")
    for pid in REFIT_PRODUCTS[:3]:
        d = api.custom_refit(pid, api.RefitOptions(), curves=True)
        for m in d["markets"]:
            s, cu = find_series(pid, m["market"]), m["custom"]
            same_grid = len(cu["curve"]) == len(s["curve"]) and all(
                close(a["price"], b["price"], 6e-3) and a["in_range"] == b["in_range"] for a, b in zip(cu["curve"], s["curve"]))
            same_profit = same_grid and all(close(a["profit"], b["profit"], 0.011) for a, b in zip(cu["curve"], s["curve"]))
            check(same_grid and same_profit, f"{pid} {m['market']}: default custom profit curve equals the official curve")
            prices = [p["price"] for p in cu["curve"]]
            check(min(prices) >= cu["usual"] * 0.8 - 0.01 and max(prices) <= cu["usual"] * 1.2 + 0.01 and min(prices) >= s["cost"],
                  f"{pid} {m['market']}: curve prices stay within 20% of the usual price and above cost")
            u, o = cu["unrestricted"], s["unrestricted"]
            check(all((u[k] is None and o[k] is None) or (u[k] is not None and o[k] is not None and close(u[k], o[k], 0.011))
                      for k in ("point", "from", "to")),
                  f"{pid} {m['market']}: default unrestricted optimum equals the official one")
    changed = api.custom_refit("SKU-1000", api.RefitOptions(include_ad_spend=False), curves=True)["markets"][1]["custom"]["curve"]
    official_curve = find_series("SKU-1000", "DE")["curve"]
    check(any(not close(a["profit"], b["profit"], 0.011) for a, b in zip(changed, official_curve)),
          "case setup: a changed setting gives a different curve (the comparison can show a difference)")

    # No usable range: the price must stay at the usual price. The real data never gets here with 7, 14 or 28 days,
    # so both cases are made on purpose.
    pdf = features[features["product_id"] == "SKU-1000"]
    fit, _ = fit_custom(pdf)
    g = pdf[pdf["market"] == "DE"]
    none_tested = recommend(g, fit, "high", min_run=10_000)
    check("note" in none_tested and close(none_tested["final"], none_tested["usual"], 6e-3)
          and none_tested["move_pct"] == 0 and none_tested["low"] == none_tested["high"] == none_tested["usual"],
          "no price held long enough: the usual price is kept, with a note")
    usual, cost = none_tested["usual"], none_tested["cost"]
    other = [p for p in g["unit_price_eur"].unique() if 0.8 * usual <= p <= 1.2 * usual and abs(p - usual) > 0.01 * usual and p > cost]
    odd = g.copy()
    odd["run_days"] = 1
    odd.loc[odd["unit_price_eur"] == other[0], "run_days"] = 30  # the only tested price is not the usual one
    outside = recommend(odd, fit, "high", min_run=14)
    check("note" in outside and close(outside["final"], usual, 6e-3),
          "tested prices that do not include the usual price: the usual price is kept, with a note")

    check(not close(1.0, 2.0, 6e-4) and close(1.0, 1.0004, 6e-4), "the comparison used in these checks can fail")


def main() -> int:
    mode = f"LIVE ({os.environ['LLM_PROVIDER']})" if "--live" in sys.argv else "offline (fake model)"
    print(f"Agent evaluation: {mode}")
    for case in CASES:
        run_case(case)
    rule_tests()
    refit_tests()
    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
