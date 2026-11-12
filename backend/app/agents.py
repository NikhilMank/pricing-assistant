"""Three AI agents that interpret the ML result: Analyst -> Strategist -> Risk reviewer.

Rules of the design:
- The AI never calculates. All numbers (elasticity, tier, price range, recommended price) come from our code
  and are handed to the agents in a payload. The agents only write about them.
- The hard rules (price above cost, inside the allowed range, tier rules, max one revision) are plain Python,
  outside the AI, so they can't be talked around.
- LangGraph runs the flow. One conditional arrow sends the reviewer's "adjust" back to the strategist, once.
"""
import json
import os
import re
from typing import TypedDict

from app.store import find_series, results

MAX_STRATEGIST_RUNS = 2  # first proposal + at most one revision
MEDIUM_CAP_PCT = 5.0


# ---------------------------------------------------------------- 1. The payload (what the agents see)

def build_payload(product: dict, series: dict) -> dict:
    """Finished numbers for one product-market. No ad spend, no raw data: only what the agents need."""
    el = product["elasticity"]
    tier_rule = {
        "high": "move fully to the model's best price",
        "medium": f"test price: move half the way to the best price, at most {MEDIUM_CAP_PCT:g}% of the usual price",
        "low": "hold the usual price",
    }[series["tier"]]
    return {
        "product_id": product["product_id"], "product_name": product["name"], "category": product["category"],
        "market": series["market"],
        "confidence_tier": series["tier"], "tier_rule": tier_rule,
        "elasticity": round(el["elasticity"], 2),
        "elasticity_interval_low": round(el["low"], 2), "elasticity_interval_high": round(el["high"], 2),
        "usual_price": round(series["usual"], 2), "latest_price": round(series["latest_price"], 2),
        "unit_cost": round(series["cost"], 2),
        "allowed_price_low": round(series["low"], 2), "allowed_price_high": round(series["high"], 2),
        "model_best_price": round(series["best"], 2),
        "recommended_price": round(series["final"], 2),
        "move_percent": round(series["move_pct"] * 100, 1),
        "profit_change_percent": round(series["profit_change_pct"] * 100, 1),
        "capped_at": ("lowest" if abs(series["best"] - series["low"]) < abs(series["best"] - series["high"]) else "highest")
        if series["at_bound"] else None,
    }


# ---------------------------------------------------------------- 2. The hard rules (plain Python)

def price_violations(payload: dict, price: float) -> list[str]:
    """Everything wrong with a proposed price. Empty list = fine. The evals use this too."""
    problems = []
    if price < payload["unit_cost"]:
        problems.append("price is below cost")
    if not payload["allowed_price_low"] - 0.005 <= price <= payload["allowed_price_high"] + 0.005:
        problems.append("price is outside the allowed range")
    # The strategist may be more careful than the recommendation, never bolder:
    # the price must lie between the usual price and the recommended price.
    corridor_low = min(payload["usual_price"], payload["recommended_price"])
    corridor_high = max(payload["usual_price"], payload["recommended_price"])
    if not corridor_low - 0.005 <= price <= corridor_high + 0.005:
        problems.append("price goes beyond what the confidence tier allows")
    return problems


# ---------------------------------------------------------------- 3. The language model (swappable)

class FakeChat:
    """Offline stand-in for the real model: fixed, rule-based answers built from the payload.
    Used for tests and evals without cost. Set FAKE_REVIEWER_VERDICT=adjust|reject to test the loop."""

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        system, user = messages[0].content, messages[1].content
        payload = json.loads(user.split("DATA: ", 1)[1].split("\n", 1)[0])  # the DATA line is one line of JSON
        p = payload
        if "ROLE: analyst" in system:
            out = {
                "summary": f"{p['product_name']} in {p['market']} has an elasticity of {p['elasticity']} "
                           f"(interval {p['elasticity_interval_low']} to {p['elasticity_interval_high']}), so the "
                           f"confidence is {p['confidence_tier']}. The usual price is {p['usual_price']} EUR and the "
                           f"model's best allowed price is {p['model_best_price']} EUR.",
                "confidence_note": f"The tier is {p['confidence_tier']}: {p['tier_rule']}.",
                "caveat": "Estimates are approximate; promotions may make buyers look more price-sensitive than they are.",
            }
        elif "ROLE: strategist" in system:
            move = p["move_percent"]
            action = "hold" if abs(move) < 0.5 else ("test_price" if p["confidence_tier"] == "medium"
                                                     else ("raise" if move > 0 else "cut"))
            out = {
                "action": action, "price": p["recommended_price"],
                "reasoning": f"Move from {p['usual_price']} EUR to {p['recommended_price']} EUR ({move}%); "
                             f"the model estimates a profit change of {p['profit_change_percent']}%.",
            }
        else:
            verdict = os.environ.get("FAKE_REVIEWER_VERDICT", "approve")
            out = {"verdict": verdict,
                   "comment": f"The proposal stays inside the allowed range of {p['allowed_price_low']} to "
                              f"{p['allowed_price_high']} EUR and follows the {p['confidence_tier']} tier rule."}
        return AIMessage(content=json.dumps(out))


def get_chat_model():
    """One place that decides which model is used. LLM_PROVIDER=bedrock (default) or fake."""
    provider = os.environ.get("LLM_PROVIDER", "bedrock")
    if provider == "fake":
        return FakeChat()
    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse  # imported here so startup stays fast
        return ChatBedrockConverse(
            model=os.environ.get("BEDROCK_MODEL_ID", "eu.anthropic.claude-haiku-4-5-20251001-v1:0"),
            region_name=os.environ.get("BEDROCK_REGION", "eu-central-1"),
            temperature=0, max_tokens=600,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


def invoke_with_retry(llm, messages, waits=(3, 8, 15)):
    """Call the model; if the provider says 'too many requests', wait a little and try again."""
    import time
    for wait in waits:
        try:
            return llm.invoke(messages)
        except Exception as e:
            if "Throttling" not in type(e).__name__ and "Too many requests" not in str(e):
                raise
            time.sleep(wait)
    return llm.invoke(messages)  # last try: let any error through


def ask_json(llm, system: str, user: str, keys: list[str]) -> dict:
    """Ask the model, expect a JSON object with the given keys. Tries twice, then raises."""
    from langchain_core.messages import HumanMessage, SystemMessage
    last_error = None
    for _ in range(2):
        text = invoke_with_retry(llm, [SystemMessage(content=system), HumanMessage(content=user)]).content
        if isinstance(text, list):  # some models return a list of content blocks
            text = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in text)
        match = re.search(r"\{.*\}", text, re.S)
        try:
            obj = json.loads(match.group(0)) if match else None
            if isinstance(obj, dict) and all(k in obj for k in keys):
                return obj
            last_error = f"missing keys {keys}"
        except json.JSONDecodeError as e:
            last_error = str(e)
    raise ValueError(f"Model did not return valid JSON ({last_error})")


# ---------------------------------------------------------------- 4. The three roles

COMMON = ("You write for a non-technical business manager at a lamp retailer. Use plain words, short sentences. "
          "The numbers in DATA were calculated by our system. NEVER calculate or invent numbers. "
          "Quote numbers exactly as they appear in DATA (same rounding). Do not derive new numbers (for example "
          "do not state what a 10% price change would do to sales); describe such effects in words only. "
          "Answer with one JSON object and nothing else.")

ANALYST = ("ROLE: analyst\n" + COMMON + "\nExplain what the data says for this product in this market: how price-sensitive "
           "buyers are (elasticity), how sure we are (confidence tier and interval) and what the recommendation is. "
           'Return {"summary": 2-3 sentences, "confidence_note": 1 sentence, "caveat": 1 sentence about uncertainty}.')

STRATEGIST = ("ROLE: strategist\n" + COMMON + "\nPropose the pricing action. You may only choose a price between usual_price and "
              "recommended_price (inclusive): you can be more careful than the recommendation, never bolder. "
              "If REVIEWER FEEDBACK is given, address it. "
              'Return {"action": "hold" | "raise" | "cut" | "test_price", "price": number from DATA, "reasoning": 2 sentences}.')

REVIEWER = ("ROLE: reviewer\n" + COMMON + "\nYou are the risk reviewer. Check the proposal against DATA: is the price inside the allowed "
            "range and above cost, does it follow the tier rule, is the reasoning honest about uncertainty and free of "
            "claims not supported by DATA? Verdicts: approve, adjust (fixable, say what to change) or reject (not safe: "
            "price outside the range, below cost, or a claim that contradicts DATA). Wording problems are 'adjust', not 'reject'. "
            'Return {"verdict": "approve" | "adjust" | "reject", "comment": 1-2 sentences}.')


class State(TypedDict, total=False):
    payload: dict
    analysis: dict
    proposal: dict
    review: dict
    strategist_runs: int
    final: dict


def build_graph(llm):
    """Analyst -> Strategist -> Reviewer -> (back to Strategist once if 'adjust') -> Finalize."""
    from langgraph.graph import END, START, StateGraph

    def analyst(state: State):
        user = "DATA: " + json.dumps(state["payload"])
        return {"analysis": ask_json(llm, ANALYST, user, ["summary", "confidence_note", "caveat"])}

    def strategist(state: State):
        user = "DATA: " + json.dumps(state["payload"]) + "\nANALYSIS: " + json.dumps(state["analysis"])
        if state.get("review"):  # second run: show the reviewer's feedback
            user += "\nREVIEWER FEEDBACK: " + state["review"]["comment"]
        proposal = ask_json(llm, STRATEGIST, user, ["action", "price", "reasoning"])
        return {"proposal": proposal, "strategist_runs": state.get("strategist_runs", 0) + 1}

    def reviewer(state: State):
        user = ("DATA: " + json.dumps(state["payload"]) + "\nPROPOSAL: " + json.dumps(state["proposal"]))
        return {"review": ask_json(llm, REVIEWER, user, ["verdict", "comment"])}

    def route_after_review(state: State):
        # The cap is enforced here in code, not by the AI: one revision at most.
        if state["review"]["verdict"] == "adjust" and state["strategist_runs"] < MAX_STRATEGIST_RUNS:
            return "revise"
        return "done"

    def finalize(state: State):
        return {"final": decide_final(state["payload"], state["proposal"], state["review"])}

    graph = StateGraph(State)
    graph.add_node("analyst", analyst)
    graph.add_node("strategist", strategist)
    graph.add_node("reviewer", reviewer)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "analyst")
    graph.add_edge("analyst", "strategist")
    graph.add_edge("strategist", "reviewer")
    graph.add_conditional_edges("reviewer", route_after_review, {"revise": "strategist", "done": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()


def decide_final(payload: dict, proposal: dict, review: dict) -> dict:
    """Plain-Python decision. Only an approved proposal that passes the hard rules is shown; otherwise the usual price."""
    usual = payload["usual_price"]
    try:
        price = float(proposal["price"])
    except (TypeError, ValueError):
        return {"price": usual, "status": "rejected", "note": "The proposal had no valid price."}
    problems = price_violations(payload, price)
    if review["verdict"] == "approve" and not problems:
        return {"price": round(price, 2), "status": "approved", "note": "Approved by the risk reviewer."}
    if problems:
        note = "Rejected by the safety rules: " + "; ".join(problems) + "."
    elif review["verdict"] == "adjust":
        note = "The reviewer still asked for changes after the one allowed revision, so the usual price is kept."
    else:
        note = "Rejected by the risk reviewer."
    return {"price": usual, "status": "rejected", "note": note}


# ---------------------------------------------------------------- 5. Entry point with a small cache and cost guard

_cache: dict = {}
_llm_runs = 0
MAX_RUNS_PER_PROCESS = int(os.environ.get("MAX_ANALYSES", "300"))  # cost guard


def analyze(product_id: str, market: str) -> dict:
    """Run the three agents for one product-market. Results are cached (lost when the container restarts)."""
    global _llm_runs
    provider = os.environ.get("LLM_PROVIDER", "bedrock")
    key = (product_id, market, provider)
    if key in _cache:
        return {**_cache[key], "cached": True}

    series = find_series(product_id, market)
    product = results()["products"].get(product_id)
    if series is None or product is None:
        raise KeyError(f"Unknown product-market: {product_id} {market}")
    if _llm_runs >= MAX_RUNS_PER_PROCESS:
        raise RuntimeError("Analysis limit reached for now. Please try again later.")

    payload = build_payload(product, series)
    _llm_runs += 1
    state = build_graph(get_chat_model()).invoke({"payload": payload})
    out = {"product_id": product_id, "market": market, "payload": payload,
           "analysis": state["analysis"], "proposal": state["proposal"], "review": state["review"],
           "strategist_runs": state["strategist_runs"], "final": state["final"]}
    _cache[key] = out
    return {**out, "cached": False}


if __name__ == "__main__":
    import sys
    pid, mkt = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("SKU-1000", "DE")
    print(json.dumps(analyze(pid, mkt), indent=2))
