"""Automatic checks on an AI analysis. No model is called here: plain Python only.

The same checks are used in two places:
- evals.py runs them on example cases (and on deliberately bad inputs) to test the agents.
- agents.analyze() runs run_checks() on every live analysis and the product page shows the result.
A failed check never changes the price that is shown: the price comes from the hard rules in agents.decide_final().
"""
import math
import re

# Numbers that may appear without being in the payload: our own rules (5% cap, 14 days, 20% range) and the
# "1%" in the usual way of explaining elasticity ("for every 1% price increase, sales drop by about X%").
ALLOWED_CONSTANTS = {1.0, 5.0, 14.0, 20.0}
VERDICTS = {"approve", "adjust", "reject"}
ACTIONS = {"hold", "raise", "cut", "test_price"}


# ---------------------------------------------------------------- the building blocks (also used by evals.py)

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


def analysis_texts(r: dict) -> list[str]:
    """Every text the agents wrote (the numbers check reads all of them)."""
    return [r["analysis"]["summary"], r["analysis"]["confidence_note"], r["analysis"]["caveat"],
            r["proposal"]["reasoning"], r["review"]["comment"]]


def unknown_numbers(r: dict) -> list[float]:
    """Numbers in the agents' texts that are not in the data they were given."""
    p = r["payload"]
    known = payload_numbers(p)
    return [x for x in cited_numbers(analysis_texts(r), p) if not number_is_known(x, known)]


def analyst_mentions_promotions(r: dict) -> bool:
    a = r["analysis"]
    return mentions_promotions([a["summary"], a["confidence_note"], a["caveat"]])


def price_not_below_cost(p: dict, price: float) -> bool:
    return price >= p["unit_cost"]


def price_in_allowed_range(p: dict, price: float) -> bool:
    return p["allowed_price_low"] - 0.005 <= price <= p["allowed_price_high"] + 0.005


def low_tier_holds_usual(p: dict, price: float) -> bool:
    return math.isclose(price, p["usual_price"], abs_tol=0.005)


def medium_move_percent(p: dict, price: float) -> float:
    return abs(price / p["usual_price"] - 1) * 100


def medium_tier_within_cap(p: dict, price: float, cap_pct: float) -> bool:
    return medium_move_percent(p, price) <= cap_pct + 0.05


def high_tier_within_recommendation(p: dict, price: float) -> bool:
    """High tier: the price lies between the usual price and the recommended price (never bolder than the model)."""
    low, high = sorted((p["usual_price"], p["recommended_price"]))
    return low - 0.005 <= price <= high + 0.005


# ---------------------------------------------------------------- the checks shown on the product page

def _item(check_id: str, label: str, passed: bool, detail: str = "") -> dict:
    return {"id": check_id, "label": label, "passed": bool(passed), "detail": "" if passed else detail}


def run_checks(result: dict) -> list[dict]:
    """Checks on one analyze() result. Returns [{"id", "label", "passed", "detail"}, ...]. Never raises and never
    calls a model. detail says what is wrong and is empty when the check passed."""
    from app import agents  # imported here: agents imports this module, so a top-level import would be circular

    checks = []

    def add(check_id: str, label: str, test) -> None:
        """test() returns (passed, detail). If a check itself cannot run, that shows as a failed check."""
        try:
            passed, detail = test()
        except Exception as e:  # e.g. the result is missing a field
            passed, detail = False, f"could not be checked ({type(e).__name__})"
        checks.append(_item(check_id, label, passed, detail))

    try:
        p, f = result["payload"], result["final"]
    except Exception:
        return [_item("structure", "The answer has all the expected parts", False, "the result is incomplete")]

    def structure():
        return check_structure(result), "a part of the answer is missing or has the wrong type"

    def numbers():
        unknown = unknown_numbers(result)
        return not unknown, "numbers that are not in the data: " + ", ".join(f"{x:g}" for x in unknown)

    def promotion():
        return analyst_mentions_promotions(result), f"the promotion check is '{p['promotion_check']}' but the analysis does not mention it"

    def not_below_cost():
        return price_not_below_cost(p, f["price"]), f"{f['price']} EUR is below the cost of {p['unit_cost']} EUR"

    def in_range():
        return price_in_allowed_range(p, f["price"]), (
            f"{f['price']} EUR is outside the allowed range of {p['allowed_price_low']} to {p['allowed_price_high']} EUR")

    def tier_rule():
        tier = p["confidence_tier"]
        if tier == "low":
            return low_tier_holds_usual(p, f["price"]), f"low confidence must keep the usual price of {p['usual_price']} EUR, but the price is {f['price']} EUR"
        if tier == "medium":
            move = medium_move_percent(p, f["price"])
            return medium_tier_within_cap(p, f["price"], agents.MEDIUM_CAP_PCT), (
                f"medium confidence may move the price by at most {agents.MEDIUM_CAP_PCT:g}%, but it moves {move:.1f}%")
        return high_tier_within_recommendation(p, f["price"]), (
            f"the price {f['price']} EUR is beyond the recommended {p['recommended_price']} EUR")

    def revision_cap():
        runs = result["strategist_runs"]
        return runs <= agents.MAX_STRATEGIST_RUNS, f"the strategist ran {runs} times, the limit is {agents.MAX_STRATEGIST_RUNS}"

    add("structure", "The answer has all the expected parts", structure)
    add("cited_numbers", "Every number in the texts is in the data", numbers)
    if p.get("promotion_check") in ("weaker", "noisy"):  # only when the promotion check found a problem
        add("promotion_mention", "The analysis mentions the promotion check", promotion)
    add("not_below_cost", "The final price is not below cost", not_below_cost)
    add("in_range", "The final price is inside the allowed range", in_range)
    add("tier_rule", "The confidence level's price rule holds", tier_rule)
    add("revision_cap", "The number of revisions is within the limit", revision_cap)
    return checks
