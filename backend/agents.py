"""ElasticPay multi-agent layer.

Three agents negotiate each JIT unlock and stream their chain-of-thought as trace events:
  1. Context & Telemetry Agent  - estimates session length + 30-day usage from the trigger.
  2. Merchant Yield Agent       - proposes dynamic micro-tariffs (24h / 72h / early-bird monthly).
  3. Buyer Personal CFO Agent   - checks budget + proximity to the Rent-to-Own cap, counters, and
                                  invokes the `execute_paypal_jit_settlement` tool.

With an LLM key set (OpenAI / Anthropic / Gemini / Groq) each agent reasons through a forced
tool call. Numbers are never trusted from the model: prices, charges and proration are always
recomputed here, and model output is clamped to the guardrails (merchant floor, user budget).
Without a key the same pipeline runs on a deterministic multi-variable policy engine.
"""
from __future__ import annotations

import json
import math
import os
import time
from decimal import ROUND_HALF_UP, Decimal

import httpx

# ----------------------------------------------------------------------------- money / proration
CENT = Decimal("0.01")


def _d(x) -> Decimal:
    return Decimal(str(x))


def money(x) -> float:
    return float(_d(x).quantize(CENT, ROUND_HALF_UP))


def rent_to_own_charge(pass_price: float, monthly_cap: float, cumulative_spend: float) -> float:
    """Charge = min(Negotiated Pass Price, max(0, Monthly Cap - Cumulative Cycle Spend))."""
    charge = min(_d(pass_price), max(Decimal(0), _d(monthly_cap) - _d(cumulative_spend)))
    return float(charge.quantize(CENT, ROUND_HALF_UP))


TIERS = {
    "DAY": {"label": "24-Hour Micro-Pass", "hours": 24},
    "WEEKEND": {"label": "72-Hour Weekend Sprint Pass", "hours": 72},
    "MONTHLY": {"label": "Full Monthly Pass (Early-Bird Bundle)", "hours": 720},
}
MERCHANT_FLOOR_PCT = 0.20   # merchant will never discount more than 20% off list
CFO_MAX_COUNTER_PCT = 0.15  # buyer agent never asks for more than 15% off
DISCOUNT_GRID = (0.0, 0.05, 0.10, 0.15, 0.20)


def list_price(svc: dict, tier: str) -> float:
    return {"DAY": svc["day_pass_price"], "WEEKEND": svc["weekend_pass_price"], "MONTHLY": svc["monthly_cap_price"]}[tier]


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-max(-30, min(30, x))))


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ----------------------------------------------------------------------------- judge scenarios
SCENARIOS = {
    "A": dict(key="A", label="Late Tuesday Night - 45 min free (Casual Intent)", weekday="Tuesday", clock="11:15 PM",
              hour=23.25, free_minutes=45, calendar="8:00 AM meeting tomorrow", calendar_pressure=0.8, intent="casual"),
    "B": dict(key="B", label="Weekend Binge / Hackathon Sprint (Heavy Intent)", weekday="Saturday", clock="10:00 AM",
              hour=10.0, free_minutes=720, calendar="Nothing scheduled until Monday 9:00 AM", calendar_pressure=0.0, intent="heavy"),
    "C": dict(key="C", label="Tight Budget Alert ($3.00 left this month)", weekday="Wednesday", clock="8:30 PM",
              hour=20.5, free_minutes=120, calendar="Free evening, no commitments", calendar_pressure=0.1, intent="moderate"),
}

TRIGGERS = {
    "casual": {"streaming": "clicked 'Watch 1 Episode' on StreamFlix", "dev-tools": "opened CloudDev Pro to fix a single failing unit test",
               "finance": "opened AlphaRead to check one earnings headline"},
    "heavy": {"streaming": "queued a full-season binge on StreamFlix", "dev-tools": "is starting a 12-hour coding marathon on CloudDev Pro",
              "finance": "opened AlphaRead for an all-day market research sprint"},
    "moderate": {"streaming": "wants a movie night on StreamFlix", "dev-tools": "wants to refactor a module in CloudDev Pro",
                 "finance": "wants to read the full market wrap on AlphaRead"},
}
BASE_MINUTES = {"streaming": {"casual": 45, "moderate": 130, "heavy": 600},
                "dev-tools": {"casual": 40, "moderate": 150, "heavy": 720},
                "finance": {"casual": 20, "moderate": 60, "heavy": 360}}
INTENT_SCORE = {"casual": -0.4, "moderate": 0.5, "heavy": 1.6}
CLUSTERING = {"casual": 0.35, "moderate": 0.6, "heavy": 0.9}
HOURLY_VALUE = {"streaming": 0.9, "dev-tools": 1.1, "finance": 0.7}  # $ of perceived value per hour of use


def trigger_text(scn: dict, svc: dict) -> str:
    return f"{scn['weekday']} {scn['clock']} - User {TRIGGERS[scn['intent']][svc['category']]}; calendar: {scn['calendar']}"


# ----------------------------------------------------------------------------- LLM transport
def llm_config() -> dict | None:
    provider = os.getenv("LLM_PROVIDER", "").lower()
    keys = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}
    if not provider:
        provider = next((p for p, k in keys.items() if os.getenv(k)), "")
    if provider not in keys or not os.getenv(keys[provider]):
        return None
    defaults = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini", "groq": "llama-3.3-70b-versatile", "gemini": "gemini-2.5-flash"}
    bases = {"openai": "https://api.openai.com/v1", "groq": "https://api.groq.com/openai/v1",
             "gemini": "https://generativelanguage.googleapis.com/v1beta/openai"}
    return {"provider": provider, "key": os.getenv(keys[provider]), "model": os.getenv("LLM_MODEL") or defaults[provider],
            "base": bases.get(provider)}


def llm_call_tool(system: str, user: str, name: str, description: str, schema: dict) -> dict:
    """Force a single structured tool call and return its arguments."""
    cfg = llm_config()
    if not cfg:
        raise RuntimeError("no LLM configured")
    if cfg["provider"] == "anthropic":
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=45,
                       headers={"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"},
                       json={"model": cfg["model"], "max_tokens": 900, "system": system,
                             "messages": [{"role": "user", "content": user}],
                             "tools": [{"name": name, "description": description, "input_schema": schema}],
                             "tool_choice": {"type": "tool", "name": name}})
        r.raise_for_status()
        block = next(b for b in r.json()["content"] if b["type"] == "tool_use")
        return block["input"]
    r = httpx.post(f"{cfg['base']}/chat/completions", timeout=45, headers={"Authorization": f"Bearer {cfg['key']}"},
                   json={"model": cfg["model"], "temperature": 0.2,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                         "tools": [{"type": "function", "function": {"name": name, "description": description, "parameters": schema}}],
                         "tool_choice": {"type": "function", "function": {"name": name}}})
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])


# ----------------------------------------------------------------------------- analytics shared by agents
def coverage_days(tier: str, ctx: dict) -> float:
    """Expected number of *active usage days* a pass can absorb."""
    if tier == "DAY":
        return 1.0
    if tier == "WEEKEND":
        return 1.0 + 2.0 * ctx["usage_clustering"]
    return ctx["expected_active_days_30d"]


def expected_cycle_cost(svc: dict, ctx: dict, tier: str, price_now: float) -> float:
    """Expected total Rent-to-Own spend this cycle if we buy `tier` now and top up with 24h passes later.
    Evaluated over a low / base / high usage distribution; every path is capped by the remaining monthly cap."""
    cap_left = max(0.0, svc["monthly_cap_price"] - svc["current_cycle_spend"])
    total = 0.0
    for mult, weight in ((0.5, 0.30), (1.0, 0.45), (1.6, 0.25)):
        days = max(1.0, ctx["expected_active_days_30d"] * mult)
        if tier == "MONTHLY":
            cost = min(cap_left, price_now)
        else:
            future = max(0.0, days - coverage_days(tier, ctx)) * svc["day_pass_price"]
            cost = min(cap_left, price_now + future)
        total += weight * cost
    return money(total)


def p_accept(svc: dict, ctx: dict, tier: str, price: float) -> float:
    """Probability the user accepts `price` for `tier`, from perceived value of the usage the pass covers."""
    per_day_value = HOURLY_VALUE[svc["category"]] * ctx["estimated_session_minutes"] / 60 + 0.35
    wtp = min(coverage_days(tier, ctx), ctx["expected_active_days_30d"]) * per_day_value
    return _sigmoid((wtp - price) / (0.4 + 0.1 * price))


def _revenue(svc: dict, ctx: dict, tier: str, price: float) -> float:
    return rent_to_own_charge(price, svc["monthly_cap_price"], svc["current_cycle_spend"]) * p_accept(svc, ctx, tier, price)


# ----------------------------------------------------------------------------- tool schemas
CONTEXT_SCHEMA = {"type": "object", "required": ["estimated_session_minutes", "usage_probability_30d", "expected_active_days_30d",
                                                "usage_clustering", "intent_level", "reasoning"],
                  "properties": {"estimated_session_minutes": {"type": "integer"},
                                 "usage_probability_30d": {"type": "number", "description": "0-1 probability of any further use in the next 30 days"},
                                 "expected_active_days_30d": {"type": "number", "description": "expected distinct active days in the 30-day cycle"},
                                 "usage_clustering": {"type": "number", "description": "0-1; 1 = usage days are back-to-back"},
                                 "intent_level": {"type": "string", "enum": ["casual", "moderate", "heavy"]},
                                 "reasoning": {"type": "string"}}}
MERCHANT_SCHEMA = {"type": "object", "required": ["headline_tier", "discount_pct", "pitch"],
                   "properties": {"headline_tier": {"type": "string", "enum": ["DAY", "WEEKEND", "MONTHLY"]},
                                  "discount_pct": {"type": "object", "description": "fraction off list per tier, 0-0.20",
                                                   "properties": {t: {"type": "number"} for t in TIERS}, "required": list(TIERS)},
                                  "pitch": {"type": "string"}}}
CFO_SCHEMA = {"type": "object", "required": ["pass_type", "counter_discount_pct", "reasoning"],
              "properties": {"pass_type": {"type": "string", "enum": ["DAY", "WEEKEND", "MONTHLY", "NONE"],
                                           "description": "NONE declines the purchase"},
                             "counter_discount_pct": {"type": "number", "description": "extra discount to request, 0-0.15"},
                             "reasoning": {"type": "string"}}}


def ev(agent: str, text: str, **data) -> dict:
    return {"type": "trace", "agent": agent, "text": text, "data": data or None, "ts": time.time()}


# ----------------------------------------------------------------------------- negotiation
class Negotiation:
    """Runs the three agents. Iterate `run()` for streamed trace events; read `.outcome` afterwards."""

    def __init__(self, service: dict, wallet: dict, scenario: dict, history: dict, force_tier: str | None = None):
        self.svc, self.wallet, self.scn, self.hist = service, wallet, scenario, history
        self.force_tier = force_tier if force_tier in TIERS else None
        self.llm = llm_config()
        self.pace = 0.0 if self.llm else float(os.getenv("AGENT_TRACE_PACE", "0.4"))
        self.outcome: dict = {}

    def _emit(self, agent, text, **data):
        if self.pace:
            time.sleep(self.pace)
        return ev(agent, text, **data)

    # ---- 1. Context & Telemetry Agent
    def _context(self):
        s, svc, h = self.scn, self.svc, self.hist
        yield self._emit("CONTEXT", f"Trigger received: {trigger_text(s, svc)}.")
        yield self._emit("CONTEXT", f"Signals: free window {s['free_minutes']} min | calendar pressure {s['calendar_pressure']:.1f} | "
                         f"{h['dormant_days']:.0f} dormant days | {h['purchases_this_cycle']} purchases this cycle | "
                         f"budget left ${self.wallet['budget_left']:.2f}.")
        ctx = None
        if self.llm:
            try:
                yield self._emit("CONTEXT", f"Reasoning with {self.llm['provider']}/{self.llm['model']} ...")
                out = llm_call_tool(
                    "You are the Context & Telemetry Agent of ElasticPay. Estimate realistic usage from the signals. Be quantitative.",
                    json.dumps({"trigger": trigger_text(s, svc), "scenario": s, "service": svc, "history": h}),
                    "report_context_analysis", "Report session and 30-day usage estimates.", CONTEXT_SCHEMA)
                ctx = dict(out, source="llm")
            except Exception as e:  # noqa: BLE001
                yield self._emit("CONTEXT", f"LLM unavailable ({type(e).__name__}); switching to deterministic telemetry model.")
        if ctx is None:
            ctx = self._det_context()
        ctx["estimated_session_minutes"] = int(_clamp(ctx["estimated_session_minutes"], 5, 1440))
        ctx["usage_probability_30d"] = round(_clamp(float(ctx["usage_probability_30d"]), 0.01, 0.99), 2)
        ctx["expected_active_days_30d"] = round(_clamp(float(ctx["expected_active_days_30d"]), 1, 30), 1)
        ctx["usage_clustering"] = round(_clamp(float(ctx["usage_clustering"]), 0.05, 1.0), 2)
        yield self._emit("CONTEXT", ctx["reasoning"])
        yield self._emit("CONTEXT", f"Report -> session ~{ctx['estimated_session_minutes']} min, P(further use in 30d) = "
                         f"{ctx['usage_probability_30d']:.0%}, ~{ctx['expected_active_days_30d']} active days, clustering {ctx['usage_clustering']}.",
                         **{k: v for k, v in ctx.items() if k != "reasoning"})
        return ctx

    def _det_context(self) -> dict:
        s, svc, h = self.scn, self.svc, self.hist
        minutes = min(BASE_MINUTES[svc["category"]][s["intent"]], s["free_minutes"] * 1.15)
        late = s["hour"] >= 22.5 or s["hour"] < 5
        if late:
            minutes *= 0.85
        weekend = s["weekday"] in ("Saturday", "Sunday")
        score = (INTENT_SCORE[s["intent"]] + 0.35 * min(h["purchases_this_cycle"], 3) + (0.3 if weekend else 0.0)
                 - 0.015 * min(h["dormant_days"], 30) - 0.5 * s["calendar_pressure"])
        p = _sigmoid(score)
        cluster = CLUSTERING[s["intent"]]
        days = 1 + 12 * p * cluster
        why = (f"Intent '{s['intent']}' ({INTENT_SCORE[s['intent']]:+.1f}), "
               f"{'weekend boost +0.3, ' if weekend else ''}{'late-night fatigue x0.85 on session length, ' if late else ''}"
               f"calendar pressure -{0.5 * s['calendar_pressure']:.2f}, dormancy -{0.015 * min(h['dormant_days'], 30):.2f}, "
               f"prior purchases +{0.35 * min(h['purchases_this_cycle'], 3):.2f} => logit {score:+.2f}.")
        return {"estimated_session_minutes": round(minutes), "usage_probability_30d": p, "expected_active_days_30d": days,
                "usage_clustering": cluster, "intent_level": s["intent"], "reasoning": why, "source": "deterministic"}

    # ---- 2. Merchant Yield Agent
    def _merchant_open(self, ctx):
        svc = self.svc
        yield self._emit("MERCHANT", f"{svc['name']} yield desk online. Cap ${svc['monthly_cap_price']:.2f}, collected "
                         f"${svc['current_cycle_spend']:.2f} this cycle -> Rent-to-Own headroom "
                         f"${max(0, svc['monthly_cap_price'] - svc['current_cycle_spend']):.2f}.")
        discounts, headline, pitch = None, None, None
        if self.llm:
            try:
                table = {t: {"list": list_price(svc, t),
                             "rent_to_own_charge": rent_to_own_charge(list_price(svc, t), svc["monthly_cap_price"], svc["current_cycle_spend"]),
                             "p_accept_at_list": round(p_accept(svc, ctx, t, list_price(svc, t)), 3)} for t in TIERS}
                out = llm_call_tool(
                    f"You are the Merchant Yield Agent for {svc['name']}. Maximise expected revenue without losing the user to paywall friction. "
                    f"You may discount any tier by at most {MERCHANT_FLOOR_PCT:.0%}. Pick the headline tier to pitch.",
                    json.dumps({"context": ctx, "tiers": table, "service": svc}), "propose_tariffs",
                    "Propose dynamic micro-tariffs.", MERCHANT_SCHEMA)
                discounts = {t: _clamp(float(out["discount_pct"].get(t, 0)), 0, MERCHANT_FLOOR_PCT) for t in TIERS}
                headline, pitch = out["headline_tier"], out["pitch"]
            except Exception as e:  # noqa: BLE001
                yield self._emit("MERCHANT", f"LLM unavailable ({type(e).__name__}); using deterministic yield optimiser.")
        asks = {}
        for tier in TIERS:
            lp = list_price(svc, tier)
            if tier == "MONTHLY" and svc["current_cycle_spend"] > 0:
                pct = 0.0  # never stack a yield discount on top of the Rent-to-Own proration credit
            elif discounts is not None:
                pct = round(discounts[tier], 2)
            else:  # maximise expected revenue = proration-adjusted charge x P(accept); smallest discount on ties
                best, pct = -1.0, 0.0
                for g in DISCOUNT_GRID:
                    rev = _revenue(svc, ctx, tier, money(lp * (1 - g)))
                    if rev > best * 1.02:
                        best, pct = rev, g
            ask = money(lp * (1 - pct))
            charge = rent_to_own_charge(ask, svc["monthly_cap_price"], svc["current_cycle_spend"])
            pa = p_accept(svc, ctx, tier, ask)
            asks[tier] = {"list": lp, "discount_pct": pct, "ask": ask, "charge": charge, "p_accept": round(pa, 3),
                          "expected_revenue": money(charge * pa)}
        if headline not in TIERS:
            headline = max(asks, key=lambda t: asks[t]["expected_revenue"])
        for tier, a in asks.items():
            tag = f" ({a['discount_pct']:.0%} yield discount)" if a["discount_pct"] else ""
            yield self._emit("MERCHANT", f"Offer {TIERS[tier]['label']}: list ${a['list']:.2f} -> ask ${a['ask']:.2f}{tag}; "
                             f"after proration you pay ${a['charge']:.2f}; P(accept) {a['p_accept']:.0%}.", tier=tier, **a)
        yield self._emit("MERCHANT", pitch or f"Headline pitch: {TIERS[headline]['label']} - highest expected revenue "
                         f"(${asks[headline]['expected_revenue']:.2f}) with the lowest bounce risk.", headline=headline)
        return asks, headline

    # ---- 3. Buyer Personal CFO Agent
    def _cfo(self, ctx, asks, headline):
        svc, budget, thr = self.svc, self.wallet["budget_left"], self.wallet["auto_approve_threshold"]
        yield self._emit("CFO", f"Reviewing offers. Monthly budget left ${budget:.2f}; auto-approve threshold ${thr:.2f}; "
                         f"cap progress ${svc['current_cycle_spend']:.2f}/${svc['monthly_cap_price']:.2f}.")
        options = {}
        for tier, a in asks.items():
            o = {"ask": a["ask"], "charge": a["charge"], "feasible": a["charge"] <= budget + 1e-9,
                 "expected_cycle_cost": expected_cycle_cost(svc, ctx, tier, a["ask"])}
            options[tier] = o
            yield self._emit("CFO", f"{tier}: charge ${o['charge']:.2f} | expected 30-day cost ${o['expected_cycle_cost']:.2f} | "
                             f"{'within budget' if o['feasible'] else 'EXCEEDS budget'}.", tier=tier, **o)
        choice, counter, reasoning = None, 0.0, None
        if self.force_tier:
            choice = self.force_tier
            if options[choice]["feasible"]:
                reasoning = (f"User explicitly asked for the {TIERS[choice]['label']}. Rent-to-Own makes this a completion payment: "
                             f"I only pay the ${options[choice]['charge']:.2f} still owed toward the ${svc['monthly_cap_price']:.2f} cap.")
            else:
                choice, reasoning = "NONE", (f"Requested {self.force_tier} costs ${options[self.force_tier]['charge']:.2f} but only "
                                             f"${budget:.2f} of budget remains. Declining.")
        if self.llm and choice is None:
            try:
                out = llm_call_tool(
                    "You are the Buyer Personal CFO Agent. Protect the user's monthly budget, minimise expected 30-day spend using the "
                    "Rent-to-Own cap, and negotiate. Choose NONE if no tier fits the budget. Call execute_paypal_jit_settlement.",
                    json.dumps({"context": ctx, "options": options, "merchant_headline": headline, "budget_left": budget,
                                "auto_approve_threshold": thr, "cap_remaining": money(svc["monthly_cap_price"] - svc["current_cycle_spend"])}),
                    "execute_paypal_jit_settlement", "Settle the chosen pass via PayPal.", CFO_SCHEMA)
                choice, counter, reasoning = out["pass_type"], _clamp(float(out["counter_discount_pct"]), 0, CFO_MAX_COUNTER_PCT), out["reasoning"]
                if choice != "NONE" and (choice not in options or not options[choice]["feasible"]):
                    yield self._emit("CFO", f"Guardrail: model picked '{choice}' which violates budget/validity; re-planning deterministically.")
                    choice = None
                elif choice == "NONE" and any(o["feasible"] for o in options.values()):
                    yield self._emit("CFO", "Guardrail: model declined although a tier fits the budget; re-planning deterministically.")
                    choice = None
            except Exception as e:  # noqa: BLE001
                yield self._emit("CFO", f"LLM unavailable ({type(e).__name__}); using deterministic optimiser.")
        if choice is None:
            feasible = {t: o for t, o in options.items() if o["feasible"]}
            if not feasible:
                cheapest = min(o["charge"] for o in options.values())
                choice, reasoning = "NONE", (f"Cheapest compliant charge is ${cheapest:.2f} but only ${budget:.2f} of monthly budget "
                                             f"remains. Declining to protect the budget; service stays DORMANT at $0.00.")
            else:
                best = min(o["expected_cycle_cost"] for o in feasible.values())
                near = [t for t, o in feasible.items() if o["expected_cycle_cost"] <= best * 1.02 + 0.005]
                choice = min(near, key=lambda t: feasible[t]["charge"])  # tie -> smallest upfront commitment (keeps optionality)
                a = asks[choice]
                tight = a["charge"] > 0.5 * budget
                counter = 0.10 if a["discount_pct"] == 0 else (0.05 if tight else 0.0)
                others = ", ".join(f"{t} ${o['expected_cycle_cost']:.2f}" for t, o in feasible.items() if t != choice) or "no feasible alternatives"
                reasoning = (f"{choice} minimises expected 30-day cost (${feasible[choice]['expected_cycle_cost']:.2f}) vs {others}; "
                             f"~{ctx['expected_active_days_30d']} active days expected, and every dollar counts toward the "
                             f"${svc['monthly_cap_price']:.2f} cap." + (f" Tight budget: charge is {a['charge'] / budget:.0%} of what remains." if tight else ""))
        yield self._emit("CFO", reasoning, choice=choice, counter_discount_pct=counter)
        if choice == "NONE":
            return {"decision": "DECLINE", "reasoning": reasoning}
        if choice != headline:
            yield self._emit("CFO", f"Counter-proposal: not the headline {headline}; I want the {TIERS[choice]['label']}.")
        return {"decision": "BUY", "pass_type": choice, "counter_pct": counter, "reasoning": reasoning}

    def _merchant_respond(self, ctx, asks, choice, counter_pct):
        svc, a = self.svc, asks[choice]
        floor = money(a["list"] * (1 - MERCHANT_FLOOR_PCT))
        final = a["ask"]
        if counter_pct > 0:
            counter_price = money(a["ask"] * (1 - counter_pct))
            yield self._emit("CFO", f"Counter-offer: {TIERS[choice]['label']} at ${counter_price:.2f} ({counter_pct:.0%} below ask).")
            if counter_price >= floor and _revenue(svc, ctx, choice, counter_price) >= 0.9 * _revenue(svc, ctx, choice, a["ask"]):
                final = counter_price
                yield self._emit("MERCHANT", f"Accepted ${final:.2f}: expected revenue holds within 10% and churn risk drops.")
            else:
                final = min(a["ask"], max(floor, money((a["ask"] + counter_price) / 2)))
                yield self._emit("MERCHANT", f"Counter rejected (floor ${floor:.2f}, revenue test failed). Meeting in the middle at ${final:.2f}.")
        else:
            yield self._emit("MERCHANT", f"No counter received. Holding at ${final:.2f}.")
        return final

    def run(self):
        svc = self.svc
        ctx = yield from self._context()
        asks, headline = yield from self._merchant_open(ctx)
        plan = yield from self._cfo(ctx, asks, headline)
        offers = "; ".join(f"{t} ${a['ask']:.2f}" + (f" (-{a['discount_pct']:.0%})" if a["discount_pct"] else "") for t, a in asks.items())
        base = {"context": ctx, "llm_used": bool(self.llm),
                "context_summary": f"{trigger_text(self.scn, svc)} | ~{ctx['estimated_session_minutes']} min, P30d {ctx['usage_probability_30d']:.0%}",
                "merchant_offer": f"{offers} | headline {headline}"}
        if plan["decision"] == "DECLINE":
            self.outcome = dict(base, decision="DECLINE", pass_type="NONE", negotiated_price=0.0, list_price=0.0, charge=0.0,
                                proration_credit=0.0, cfo_reasoning=plan["reasoning"], needs_approval=False)
            return
        tier = plan["pass_type"]
        final = yield from self._merchant_respond(ctx, asks, tier, plan["counter_pct"])
        charge = rent_to_own_charge(final, svc["monthly_cap_price"], svc["current_cycle_spend"])
        credit = money(final - charge)
        yield self._emit("CFO", f"Deal: {TIERS[tier]['label']} at ${final:.2f}. Rent-to-Own: min(${final:.2f}, max(0, ${svc['monthly_cap_price']:.2f} - "
                         f"${svc['current_cycle_spend']:.2f})) = ${charge:.2f}" + (f" (proration credit ${credit:.2f})." if credit else "."))
        self.outcome = dict(base, decision="BUY", pass_type=tier, list_price=asks[tier]["list"], negotiated_price=final, charge=charge,
                            proration_credit=credit, cfo_reasoning=plan["reasoning"],
                            needs_approval=charge > self.wallet["auto_approve_threshold"] + 1e-9)
