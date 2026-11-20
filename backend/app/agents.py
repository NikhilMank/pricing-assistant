"""Three AI agents that interpret the ML result: Analyst -> Strategist -> Risk reviewer.

Rules of the design:
- The AI never calculates. All numbers (elasticity, tier, price range, recommended price) come from our code
  and are handed to the agents in a payload. The agents only write about them.
- The hard rules (price above cost, inside the allowed range, tier rules, the limits on the revision loop) are
  plain Python, outside the AI, so they can't be talked around.
- LangGraph runs the flow. The reviewer sees the data, the analysis and the proposal. If it says "adjust", it also
  says where the fix starts: the analysis (back to the analyst, then the strategist again) or the proposal (back to
  the strategist). The loop is capped in code (MAX_STRATEGIST_RUNS passes, LOOP_SECONDS_LIMIT seconds).
- The reviewer can use a stronger model than the other two agents (get_chat_model("reviewer")).
"""
import json
import os
import re
import time
from typing import TypedDict

from app.checks import run_checks
from app.store import find_series, results

MAX_STRATEGIST_RUNS = 3  # first proposal + at most two revisions (each pass through the strategist is one review)
LOOP_SECONDS_LIMIT = 300  # Cloud Run ends a request after 600 seconds, so a new pass only starts if it is expected to end before 300 seconds (room is left for a slow pass)
# Seconds one model call may take. Normal calls take 10 to 30 seconds; some hang for minutes, so a model is given up on
# early and the next one answers. The last model of a list has no fallback and is slower, so it gets longer.
CALL_TIMEOUT = 45  # the analyst and the strategist
REVIEWER_CALL_TIMEOUT = 60  # the reviewer thinks longer
LAST_RESORT_TIMEOUT = 90  # the last model of a list
CHAIN_SECONDS_LIMIT = 120  # after this long the next model of a hierarchy is not tried any more
COOLDOWN_BUSY = 60  # a model that was busy or too slow is skipped for this many seconds (it would only cost more waiting)
COOLDOWN_UNAVAILABLE = 600  # a model this project may not use or that does not exist
_cooling: dict = {}  # model name -> time (time.monotonic) until which it is skipped; shared by all requests of this process
MEDIUM_CAP_PCT = 5.0


# ---------------------------------------------------------------- 1. The payload (what the agents see)

# Plain sentences for the promotion check (computed in model.promo_check). Words only, no numbers.
PROMO_NOTES = {
    "holds": "Promotion check: even without the weeks with promotions, buyers still react to price in the same way.",
    "weaker": "Promotion check: without the weeks with promotions, the effect of price looks weaker and less certain, "
              "so part of the result comes from discounts.",
    "noisy": "Promotion check: without the weeks with promotions, the data shows no clear reaction to price, "
             "so the result rests mostly on discounts.",
    None: "No promotion check applies to this product.",
}


def build_payload(product: dict, series: dict) -> dict:
    """Finished numbers for one product-market. No ad spend, no raw data: only what the agents need."""
    el = product["elasticity"]
    verdict = (product.get("promo_check") or {}).get("verdict")  # holds | weaker | noisy | None
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
        "promotion_check": verdict or "none", "promotion_check_note": PROMO_NOTES[verdict],
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
    Used for tests and evals without cost. To test the loop, set FAKE_REVIEWER_VERDICT=adjust (every time), adjust_once
    (first review only) or reject, and FAKE_REVIEWER_FIX=analysis|proposal (where the fix starts)."""

    def __init__(self):
        self.reviews = 0

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
                "caveat": "Estimates are approximate; promotions may make buyers look more price-sensitive than they are."
                          + (" " + p["promotion_check_note"] if p["promotion_check"] in ("weaker", "noisy") else ""),
            }
        elif "ROLE: strategist" in system:
            move = p["move_percent"]
            action = "hold" if abs(move) < 0.5 else ("test_price" if p["confidence_tier"] == "medium"
                                                     else ("raise" if move > 0 else "cut"))
            out = {
                "action": action, "price": p["recommended_price"],
                "reasoning": f"Move from {p['usual_price']} EUR to {p['recommended_price']} EUR ({move}%); "
                             f"the model estimates a profit change of {p['profit_change_percent']}%."
                             + (" Part of this case rests on promotion weeks." if p["promotion_check"] in ("weaker", "noisy") else ""),
            }
        else:
            verdict = os.environ.get("FAKE_REVIEWER_VERDICT", "approve")
            self.reviews += 1
            if verdict == "adjust_once":
                verdict = "adjust" if self.reviews == 1 else "approve"
            out = {"verdict": verdict,
                   "comment": f"The proposal stays inside the allowed range of {p['allowed_price_low']} to "
                              f"{p['allowed_price_high']} EUR and follows the {p['confidence_tier']} tier rule.",
                   "fix": os.environ.get("FAKE_REVIEWER_FIX", "proposal")}
        return AIMessage(content=json.dumps(out))


class FallbackChat:
    """A list of models, best first. A call goes to the first model; if that one is busy (rate limit), too slow or
    not available to this project, the next model answers, so an analysis still gets an answer. Any other error
    (a bad request, for example) is not hidden: it is raised at once. `used` is the model that answered last."""

    def __init__(self, models: list[str], build):
        self.models, self._build, self._clients, self.used = list(models), build, {}, None

    def build(self, name: str):
        if name not in self._clients:
            self._clients[name] = self._build(name)
        return self._clients[name]

    def invoke(self, messages):
        started, last = time.monotonic(), None
        # models that just failed are skipped for a while (unless all of them did: then they are all tried again)
        ready = [m for m in self.models if _cooling.get(m, 0) <= started] or self.models
        for name in ready:
            try:
                answer = self.build(name).invoke(messages)
                self.used = name
                return answer
            except Exception as e:
                if not (is_busy(e) or is_timeout(e) or is_unavailable(e)):
                    raise
                last = e
                _cooling[name] = time.monotonic() + (COOLDOWN_BUSY if is_busy(e) or is_timeout(e) else COOLDOWN_UNAVAILABLE)
                if time.monotonic() - started > CHAIN_SECONDS_LIMIT:  # no time left for the next model
                    break
        raise last


def model_info(llm) -> dict | None:
    """Which model answered last and which one was preferred, for the result (None for the offline fake model)."""
    if isinstance(llm, FallbackChat) and llm.used:
        return {"model": llm.used, "preferred": llm.models[0]}
    return None


def get_chat_model(role: str = "agent"):
    """One place that decides which model is used. LLM_PROVIDER=vertex (default, Gemini on Google Cloud), bedrock (Claude on AWS) or fake.
    role="agent" is for the analyst and the strategist, role="reviewer" for the risk reviewer, which gets a stronger
    model (VERTEX_REVIEWER_MODEL, BEDROCK_REVIEWER_MODEL_ID). On Vertex each role has a list of models: its own model first,
    then its own hierarchy in order, without duplicates. The analyst and the strategist use VERTEX_MODEL and
    VERTEX_AGENT_HIERARCHY (carbon, hydrogen, 3.8 flash, never argon); the reviewer uses VERTEX_REVIEWER_MODEL and
    VERTEX_MODEL_HIERARCHY (argon, carbon, hydrogen, 3.8 flash). The next model is used if one is busy or unavailable."""
    provider = os.environ.get("LLM_PROVIDER", "vertex")
    reviewer = role == "reviewer"
    if provider == "fake":
        return FakeChat()
    if provider == "vertex":
        if reviewer:
            listed = os.environ.get("VERTEX_MODEL_HIERARCHY", "gemini-4-argon,gemini-4-carbon,gemini-4-hydrogen,gemini-3.8-flash")
            first = os.environ.get("VERTEX_REVIEWER_MODEL", "gemini-4-argon")
        else:
            listed = os.environ.get("VERTEX_AGENT_HIERARCHY", "gemini-4-carbon,gemini-4-hydrogen,gemini-3.8-flash")
            first = os.environ.get("VERTEX_MODEL", "gemini-4-carbon")
        hierarchy = [m.strip() for m in listed.split(",") if m.strip()]
        effort = os.environ.get("VERTEX_REVIEWER_REASONING", "high") if reviewer else os.environ.get("VERTEX_REASONING", "high")

        names = list(dict.fromkeys([first] + hierarchy))  # its own model first, then its list in order, no duplicates

        def build(name):
            from langchain_google_genai import ChatGoogleGenerativeAI  # imported here so startup stays fast
            # No API key: on Cloud Run the service's own Google identity is used (locally: gcloud auth application-default login)
            return ChatGoogleGenerativeAI(
                model=name,
                vertexai=True,
                project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                location=os.environ.get("VERTEX_LOCATION", "global"),
                # Gemini 4 "thinks" before answering and the thinking counts as output: high for all three agents,
                # and leave room for the JSON
                # One try per call (the default is 6 tries, which made a rate limit last minutes): the fallback chain and
                # invoke_with_retry do the waiting, and a call that takes too long is stopped
                timeout=LAST_RESORT_TIMEOUT if name == names[-1] else (REVIEWER_CALL_TIMEOUT if reviewer else CALL_TIMEOUT), max_retries=1,
                temperature=0, max_output_tokens=8000, reasoning_effort=effort,
            )

        return FallbackChat(names, build)
    if provider == "bedrock":
        default_model = os.environ.get("BEDROCK_MODEL_ID", "eu.anthropic.claude-haiku-4-5-20251001-v1:0")
        model_id = os.environ.get("BEDROCK_REVIEWER_MODEL_ID", default_model) if reviewer else default_model

        def build(name):
            from langchain_aws import ChatBedrockConverse  # imported here so startup stays fast
            return ChatBedrockConverse(model=name, region_name=os.environ.get("BEDROCK_REGION", "eu-central-1"),
                                       temperature=0, max_tokens=600)

        return FallbackChat([model_id], build)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")


class ModelBusyError(RuntimeError):
    """The model could not answer in time: rate limit or too slow. A RuntimeError on purpose: the API answers 429
    with this message, which the page shows as it is."""


def provider_name() -> str:
    """Who runs the model, for messages that say the problem is not in this app."""
    return {"vertex": "Google's", "bedrock": "Amazon's"}.get(os.environ.get("LLM_PROVIDER", "vertex"), "The")


def is_busy(e: Exception) -> bool:
    """Worth waiting for or moving on from: a rate limit, a dropped connection or a service that is down for a moment."""
    if "Throttling" in type(e).__name__ or "Too many requests" in str(e) or "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
        return True  # Bedrock / Gemini rate limits
    text = (type(e).__name__ + " " + str(e)).lower()
    return any(w in text for w in ("remoteprotocolerror", "readerror", "connecterror", "peer closed connection", "connection reset",
                                   "connection aborted", "service unavailable", "503 unavailable", "internal error"))


def is_unavailable(e: Exception) -> bool:
    """The model does not exist or this project may not use it (so the next model of the hierarchy should be tried)."""
    text = (type(e).__name__ + " " + str(e)).lower()
    return any(w in text for w in ("not_found", "not found", "permission_denied", "permission denied", "404", "403"))


def is_timeout(e: Exception) -> bool:
    text = (type(e).__name__ + " " + str(e)).lower()
    return "timeout" in text or "timed out" in text or "deadline" in text


def invoke_with_retry(llm, messages, waits=(3, 8)):
    """Call the model; if the provider says 'too many requests', wait a little and try again (twice at most).
    A rate limit that stays, or a call that takes too long, ends in a short ModelBusyError instead of a long wait."""
    import time
    for attempt in range(len(waits) + 1):
        try:
            return llm.invoke(messages)
        except Exception as e:
            if is_busy(e):
                if attempt < len(waits):
                    time.sleep(waits[attempt])
                    continue
                raise ModelBusyError(f"{provider_name()} AI models are busy right now (too many requests). "
                                     "This is on their side, not a fault in this app. Please try again in a minute.") from e
            if is_timeout(e):  # no second try: it would take as long again
                raise ModelBusyError(f"{provider_name()} AI models took too long to answer. "
                                 "This is on their side, not a fault in this app. Please try again in a minute.") from e
            raise


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
          "Quote numbers exactly as they appear in DATA (same rounding). All prices are in EUR: always write EUR, never francs "
          "or any other currency. Describe every price change as a move from usual_price to the proposed price, because "
          "the profit change in DATA is measured from usual_price; mention latest_price only if you say that it is the latest price. Do not derive new numbers (for example "
          "do not state what a 10% price change would do to sales); describe such effects in words only. "
          "Answer with one JSON object and nothing else.")

ANALYST = ("ROLE: analyst\n" + COMMON + "\nExplain what the data says for this product in this market: how price-sensitive "
           "buyers are (elasticity), how sure we are (confidence tier and interval) and what the recommendation is. "
           "DATA has a promotion_check. If it is 'weaker' or 'noisy', do not describe the confidence as simply high or "
           "medium: say in the caveat, in plain words, what promotion_check_note says. If it is 'holds', you may say the result "
           "also holds without the promotion weeks. If REVIEWER FEEDBACK is given, your earlier analysis was found weak: fix "
           "exactly what it criticises. If EARLIER ATTEMPTS is given, those are your earlier analyses that were already sent back, each with "
           "the reviewer's comment: do not repeat their mistakes. "
           'Return {"summary": 2-3 sentences, "confidence_note": 1 sentence, "caveat": 1-2 sentences about uncertainty}.')

STRATEGIST = ("ROLE: strategist\n" + COMMON + "\nPropose the pricing action. You may only choose a price between usual_price and "
              "recommended_price (inclusive): you can be more careful than the recommendation, never bolder. "
              "If DATA has promotion_check 'weaker' or 'noisy', say in your reasoning that part of the case rests on "
              "promotion weeks. If REVIEWER FEEDBACK is given, address it. "
              "If EARLIER ATTEMPTS is given, those are your earlier proposals that were already sent back, each with the reviewer's "
              "comment: do not repeat their mistakes. "
              'Return {"action": "hold" | "raise" | "cut" | "test_price", "price": number from DATA, "reasoning": 2 sentences}.')

REVIEWER = ("ROLE: reviewer\n" + COMMON + "\nYou are the risk reviewer. You get DATA (what the analyst and the strategist were given), "
            "the ANALYSIS the analyst wrote from it and the PROPOSAL the strategist wrote from both. Check the ANALYSIS against DATA: "
            "does it quote the numbers correctly, describe the confidence correctly (tier and interval), and is its caveat honest about "
            "uncertainty? If promotion_check is 'weaker' or 'noisy' and the analysis does not say so in plain words, the analysis is weak. "
            "Then check the PROPOSAL against DATA: is the price inside the allowed range and above cost, does it follow the tier rule, "
            "is the reasoning honest about uncertainty and free of claims not supported by DATA, and does it agree with the analysis? "
            "If promotion_check is 'weaker' or 'noisy' and the reasoning does not mention that part of the case rests on promotion "
            "weeks, that is a wording problem. Verdicts: approve, adjust (fixable, say what to change) or reject (not safe: "
            "price outside the range, below cost, or a claim that contradicts DATA). Wording problems are 'adjust', not 'reject'. "
            "With 'adjust', say where the fix starts in \"fix\": 'analysis' if the analysis itself is wrong or too weak (the analyst "
            "then writes it again and the strategist starts over), 'proposal' if only the proposal needs changes. "
            "If EARLIER REVIEWS is given, this analysis or proposal was already sent back before. Each earlier entry shows what you "
            "judged then (the analysis and proposal, your verdict and your comment). Check whether each earlier complaint is now fixed: "
            "approve if it is and nothing else is wrong, and do not repeat a complaint that has been fixed; if it is not fixed, say so. "
            'Return {"verdict": "approve" | "adjust" | "reject", "fix": "analysis" | "proposal", "comment": 1-2 sentences}.')


class State(TypedDict, total=False):
    payload: dict
    analysis: dict
    proposal: dict
    review: dict
    analyst_runs: int
    strategist_runs: int
    started_at: float  # time.monotonic() when the run began, for the time limit
    models: dict  # which model answered for each agent, and which one was preferred
    history: list  # every finished review with the analysis and proposal it judged, so the reviewer can see what it asked before
    final: dict


def earlier_attempts(history: list, fix: str, key: str) -> list:
    """What an agent wrote before and was sent back for ('adjust' with this fix), with the reviewer's comment on it.
    The latest review is left out: the agent gets it as REVIEWER FEEDBACK."""
    return [{key: e[key], "reviewer_comment": e["comment"]}
            for e in history[:-1] if e["verdict"] == "adjust" and e["fix"] == fix]


def build_graph(llm, reviewer_llm=None):
    """Analyst -> Strategist -> Reviewer -> (back to the Analyst or the Strategist if 'adjust') -> Finalize.
    llm writes the analysis and the proposal; reviewer_llm (default: the same model) reviews."""
    from langgraph.graph import END, START, StateGraph

    reviewer_llm = reviewer_llm or llm

    def analyst(state: State):
        user = "DATA: " + json.dumps(state["payload"])
        if state.get("review"):  # a repeat run: the reviewer found the analysis weak
            user += "\nREVIEWER FEEDBACK: " + state["review"]["comment"]
            before = earlier_attempts(state["history"], "analysis", "analysis")
            if before:
                user += "\nEARLIER ATTEMPTS: " + json.dumps(before)
        analysis = ask_json(llm, ANALYST, user, ["summary", "confidence_note", "caveat"])
        return {"analysis": analysis, "analyst_runs": state.get("analyst_runs", 0) + 1,
                "models": {**state.get("models", {}), "analyst": model_info(llm)}}

    def strategist(state: State):
        user = "DATA: " + json.dumps(state["payload"]) + "\nANALYSIS: " + json.dumps(state["analysis"])
        review = state.get("review")
        if review and review["fix"] == "proposal":  # a repeat run: show the reviewer's feedback
            user += "\nREVIEWER FEEDBACK: " + review["comment"]
        before = earlier_attempts(state.get("history", []), "proposal", "proposal")
        if before:
            user += "\nEARLIER ATTEMPTS: " + json.dumps(before)
        proposal = ask_json(llm, STRATEGIST, user, ["action", "price", "reasoning"])
        return {"proposal": proposal, "strategist_runs": state.get("strategist_runs", 0) + 1,
                "models": {**state.get("models", {}), "strategist": model_info(llm)}}

    def reviewer(state: State):
        user = ("DATA: " + json.dumps(state["payload"]) + "\nANALYSIS: " + json.dumps(state["analysis"])
                + "\nPROPOSAL: " + json.dumps(state["proposal"]))
        history = state.get("history", [])
        if history:  # a repeat review: show what was judged and asked before
            user += "\nEARLIER REVIEWS: " + json.dumps(history)
        review = ask_json(reviewer_llm, REVIEWER, user, ["verdict", "comment"])
        if review.get("fix") not in ("analysis", "proposal"):  # missing or odd value: the safe default
            review["fix"] = "proposal"
        entry = {"review_number": len(history) + 1, "analysis": state["analysis"], "proposal": state["proposal"],
                 "verdict": review["verdict"], "fix": review["fix"], "comment": review["comment"]}
        return {"review": review, "history": history + [entry],
                "models": {**state.get("models", {}), "reviewer": model_info(reviewer_llm)}}

    def route_after_review(state: State):
        # The limits are enforced here in code, not by the AI: a number of passes and a time limit. A new pass is
        # expected to take as long as the passes so far on average (plus a quarter); it must end within the limit.
        elapsed = time.monotonic() - state["started_at"]
        out_of_time = elapsed + elapsed / state["strategist_runs"] * 1.25 > LOOP_SECONDS_LIMIT
        if state["review"]["verdict"] == "adjust" and state["strategist_runs"] < MAX_STRATEGIST_RUNS and not out_of_time:
            return "analyst" if state["review"]["fix"] == "analysis" else "strategist"
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
    graph.add_conditional_edges("reviewer", route_after_review,
                                {"analyst": "analyst", "strategist": "strategist", "done": "finalize"})
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
        note = "The reviewer still asked for changes when the limit on revisions was reached, so the usual price is kept."
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
    provider = os.environ.get("LLM_PROVIDER", "vertex")
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
    graph = build_graph(get_chat_model(), get_chat_model("reviewer"))
    state = graph.invoke({"payload": payload, "started_at": time.monotonic()})
    out = {"product_id": product_id, "market": market, "payload": payload,
           "analysis": state["analysis"], "proposal": state["proposal"], "review": state["review"],
           "analyst_runs": state["analyst_runs"], "strategist_runs": state["strategist_runs"],
           "models": state.get("models", {}), "final": state["final"]}
    out["checks"] = run_checks(out)  # plain Python, no model call; shown on the product page, never changes the price
    _cache[key] = out
    return {**out, "cached": False}


if __name__ == "__main__":
    import sys
    pid, mkt = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("SKU-1000", "DE")
    print(json.dumps(analyze(pid, mkt), indent=2))
