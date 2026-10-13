"""ElasticPay FastAPI app: services, negotiation (NDJSON stream), time machine, ledger, PayPal webhook."""
from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import billing
from agents import SCENARIOS, TIERS, Negotiation, ev, llm_config, rent_to_own_charge
from database import SessionLocal, get_db, init_db, seed
from models import MerchantService, TransactionLedger
from paypal_client import PayPalError, paypal


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="ElasticPay", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
api = APIRouter(prefix="/api")

# proposals awaiting human approval (charge > auto_approve_threshold): proposal_id -> (service_id, outcome, trace)
PROPOSALS: dict[str, tuple[int, dict, list]] = {}


def public_base() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def line(obj: dict) -> str:
    return json.dumps(obj, default=str) + "\n"


# ----------------------------------------------------------------------------- state
def full_state(db) -> dict:
    billing.expire_passes(db)
    sim, wallet = billing.get_sim(db), billing.get_wallet(db)
    services = [billing.serialize_service(s, sim) for s in db.query(MerchantService).order_by(MerchantService.id)]
    llm = llm_config()
    return {
        "services": services,
        "wallet": {"monthly_budget_limit": wallet.monthly_budget_limit, "current_month_spend": wallet.current_month_spend,
                   "budget_left": round(max(0.0, wallet.monthly_budget_limit - wallet.current_month_spend), 2),
                   "auto_approve_threshold": wallet.auto_approve_threshold, "paypal_payer_id": wallet.paypal_payer_id},
        "sim": {"now": billing.iso(sim.sim_now), "cycle_start": billing.iso(sim.cycle_start), "cycle_end": billing.iso(billing.cycle_end(sim)),
                "cycle_day": (sim.sim_now - sim.cycle_start).days + 1, "scenario": sim.scenario},
        "scenarios": list(SCENARIOS.values()),
        "paypal": {"mode": paypal.mode, "webhook_verification": bool(paypal.webhook_id)},
        "llm": {"enabled": bool(llm), "provider": llm["provider"] if llm else None, "model": llm["model"] if llm else None},
        "flat_plan_total": round(sum(s["monthly_cap_price"] for s in services), 2),
    }


@api.get("/health")
def health():
    return {"ok": True}


@api.get("/state")
def state(db=Depends(get_db)):
    return full_state(db)


@api.get("/services")
def services(db=Depends(get_db)):
    return full_state(db)["services"]


@api.get("/ledger")
def ledger(db=Depends(get_db)):
    names = {s.id: s.name for s in db.query(MerchantService)}
    rows = db.query(TransactionLedger).order_by(TransactionLedger.id.desc()).all()
    return [billing.serialize_ledger(r, names) for r in rows]


@api.get("/ledger/status/{order_id}")
def ledger_status(order_id: str, db=Depends(get_db)):
    row = db.query(TransactionLedger).filter_by(paypal_order_id=order_id).first()
    if not row:
        raise HTTPException(404, "unknown order")
    return {"status": row.status, "capture_id": row.paypal_capture_id}


class ScenarioReq(BaseModel):
    scenario: str


@api.post("/scenario")
def set_scenario(req: ScenarioReq, db=Depends(get_db)):
    if req.scenario not in SCENARIOS:
        raise HTTPException(400, "scenario must be A, B or C")
    billing.apply_scenario(db, req.scenario)
    return full_state(db)


class TimeReq(BaseModel):
    hours: int = 0
    days: int = 0


@api.post("/simulate-time")
def simulate_time(req: TimeReq, db=Depends(get_db)):
    delta = timedelta(hours=req.hours, days=req.days)
    if delta <= timedelta(0) or delta > timedelta(days=90):
        raise HTTPException(400, "advance between 1 hour and 90 days")
    result = billing.advance_time(db, delta)
    return {**result, "state": full_state(db)}


@api.post("/reset")
def reset(db=Depends(get_db)):
    PROPOSALS.clear()
    seed(db, reset=True)
    return full_state(db)


# ----------------------------------------------------------------------------- negotiation + JIT settlement
class NegotiateReq(BaseModel):
    service_id: int | None = None
    proposal_id: str | None = None
    force_tier: str | None = None  # user-initiated upgrade, e.g. MONTHLY to complete Rent-to-Own


@api.post("/negotiate-and-unlock")
def negotiate_and_unlock(req: NegotiateReq, db=Depends(get_db)):
    if req.proposal_id:
        if req.proposal_id not in PROPOSALS:
            raise HTTPException(404, "proposal expired")
        service_id = PROPOSALS[req.proposal_id][0]
    else:
        service_id = req.service_id
    svc = db.get(MerchantService, service_id) if service_id else None
    if not svc:
        raise HTTPException(404, "unknown service")
    if svc.status == "CAP_UNLOCKED":
        raise HTTPException(409, "service already UNLIMITED_CYCLE for this cycle")
    return StreamingResponse(_stream(service_id, req.proposal_id, req.force_tier), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _stream(service_id: int, proposal_id: str | None, force_tier: str | None = None):
    db = SessionLocal()
    try:
        billing.expire_passes(db)
        svc = db.get(MerchantService, service_id)
        trace: list[dict] = []
        if proposal_id:
            _, outcome, trace = PROPOSALS.pop(proposal_id)
            e = ev("SYSTEM", "Human approval received for a charge above the auto-approve threshold. Proceeding to settlement.")
            trace.append(e)
            yield line(e)
        else:
            service, wallet, scenario, history = billing.build_agent_inputs(db, svc)
            neg = Negotiation(service, wallet, scenario, history, force_tier)
            for e in neg.run():
                trace.append(e)
                yield line(e)
            outcome = neg.outcome
            if outcome["decision"] == "DECLINE":
                row = _new_ledger(db, svc, outcome, trace, status="DECLINED", mode="NONE")
                yield line({"type": "result", "status": "DECLINED", "ledger": _ledger_dict(db, row), "state": full_state(db)})
                return
            if outcome["needs_approval"]:
                pid = uuid.uuid4().hex[:12]
                e = ev("SYSTEM", f"Charge ${outcome['charge']:.2f} exceeds the ${wallet['auto_approve_threshold']:.2f} auto-approve threshold - "
                       "waiting for explicit user approval.")
                trace.append(e)
                PROPOSALS[pid] = (service_id, outcome, trace)
                yield line(e)
                yield line({"type": "approval_required", "proposal_id": pid, "outcome": {k: outcome[k] for k in (
                    "pass_type", "list_price", "negotiated_price", "charge", "proration_credit")}})
                return
        yield from _settle(db, svc, outcome, trace)
    except Exception as e:  # noqa: BLE001
        yield line({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        db.close()


def _new_ledger(db, svc, outcome, trace, status, mode, charge=None, credit=None, gross=None) -> TransactionLedger:
    sim = billing.get_sim(db)
    row = TransactionLedger(
        sim_time=sim.sim_now, service_id=svc.id, scenario_context=outcome["context_summary"],
        buyer_agent_reasoning=outcome["cfo_reasoning"], merchant_agent_offer=outcome["merchant_offer"],
        pass_type=outcome["pass_type"], gross_price=outcome["negotiated_price"] if gross is None else gross,
        proration_credit_applied=outcome["proration_credit"] if credit is None else credit,
        final_charged_amount=outcome["charge"] if charge is None else charge,
        status=status, settlement_mode=mode, agent_trace=json.dumps(trace, default=str))
    db.add(row)
    db.commit()
    return row


def _ledger_dict(db, row):
    return billing.serialize_ledger(row, {s.id: s.name for s in db.query(MerchantService)})


def _settle(db, svc: MerchantService, outcome: dict, trace: list):
    """CFO tool call -> PayPal create order -> (approval) -> capture -> webhook -> entitlement."""
    wallet = billing.get_wallet(db)
    # Re-apply the invariant against live state (it may have changed while awaiting approval).
    charge = rent_to_own_charge(outcome["negotiated_price"], svc.monthly_cap_price, svc.current_cycle_spend)
    credit = round(outcome["negotiated_price"] - charge, 2)
    budget_left = wallet.monthly_budget_limit - wallet.current_month_spend
    if charge <= 0 or charge > budget_left + 1e-9:
        e = ev("SYSTEM", f"Settlement aborted: charge ${charge:.2f} no longer fits (budget left ${budget_left:.2f}).")
        trace.append(e)
        yield line(e)
        row = _new_ledger(db, svc, outcome, trace, "DECLINED", "NONE", charge=0.0, credit=0.0)
        yield line({"type": "result", "status": "DECLINED", "ledger": _ledger_dict(db, row), "state": full_state(db)})
        return
    mode = paypal.mode
    row = _new_ledger(db, svc, outcome, trace, "PENDING", mode, charge=charge, credit=credit)

    def out(agent, text, **data):
        e = ev(agent, text, **data)
        trace.append(e)
        row.agent_trace = json.dumps(trace, default=str)
        db.commit()
        return line(e)

    args = {"service": svc.name, "pass_type": outcome["pass_type"], "gross_usd": outcome["negotiated_price"],
            "proration_credit_usd": credit, "charge_usd": charge, "remaining_cap_usd": round(svc.monthly_cap_price - svc.current_cycle_spend - charge, 2)}
    yield out("CFO", f"tool_call execute_paypal_jit_settlement({json.dumps(args)})", tool="execute_paypal_jit_settlement", args=args)

    pass_label = TIERS[outcome["pass_type"]]["label"]
    if mode == "PAYPAL_SANDBOX":
        try:
            order = paypal.create_order(
                merchant_name=svc.name, pass_label=pass_label, pass_type=outcome["pass_type"], gross=outcome["negotiated_price"],
                proration_credit=credit, remaining_cap=max(0.0, svc.monthly_cap_price - svc.current_cycle_spend - charge),
                custom_id=f"EP-{row.id}", return_url=f"{public_base()}/api/paypal/return", cancel_url=f"{public_base()}/api/paypal/cancel")
            row.paypal_order_id = order["id"]
            db.commit()
            yield out("PAYPAL", f"POST /v2/checkout/orders -> {order['id']} ({order['status']}), ${charge:.2f} with line-item metadata.",
                      order_id=order["id"])
            yield line({"type": "paypal_approval", "order_id": order["id"], "approve_url": order["approve_url"], "ledger_id": row.id,
                        "charge": charge, "credit": credit})
            return  # capture happens in /paypal/return after the buyer approves in the sandbox popup
        except (PayPalError, Exception) as e:  # noqa: BLE001
            if not paypal.fallback_enabled:
                row.status = "FAILED"
                db.commit()
                yield out("PAYPAL", f"Sandbox error: {e}")
                yield line({"type": "error", "message": str(e)})
                return
            row.settlement_mode = "SIMULATED_SANDBOX"
            yield out("PAYPAL", f"Live sandbox unreachable ({str(e)[:140]}). Falling back to the simulated sandbox verifier.")

    order_id, capture_id = paypal.simulate_order(), paypal.simulate_capture()
    row.paypal_order_id = order_id
    db.commit()
    yield out("PAYPAL", f"[simulated sandbox] POST /v2/checkout/orders -> {order_id} (CREATED), ${charge:.2f} incl. proration credit ${credit:.2f}.")
    yield out("PAYPAL", f"[simulated sandbox] POST /v2/checkout/orders/{order_id}/capture -> {capture_id} (COMPLETED).")
    event = {"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {
        "id": capture_id, "status": "COMPLETED", "custom_id": f"EP-{row.id}",
        "amount": {"currency_code": "USD", "value": f"{charge:.2f}"},
        "supplementary_data": {"related_ids": {"order_id": order_id}}}}
    handled = handle_capture_event(db, event, verified="simulated")
    yield out("WEBHOOK", f"PAYMENT.CAPTURE.COMPLETED received -> entitlement {handled['entitlement']}; "
              f"receipt {handled['receipt'][:16]}...", receipt=handled["receipt"])
    db.refresh(row)
    yield line({"type": "result", "status": "COMPLETED", "ledger": _ledger_dict(db, row), "state": full_state(db)})


# ----------------------------------------------------------------------------- PayPal return + webhook
def _popup(message: str, ok: bool, order_id: str = "") -> HTMLResponse:
    color = "#34d399" if ok else "#f87171"
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8><title>ElasticPay</title>
<body style="font-family:system-ui;background:#0b1020;color:#e5e7eb;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h2 style="color:{color}">{message}</h2><p>You can close this window.</p></div>
<script>try{{window.opener&&window.opener.postMessage({{type:'elasticpay:{'paid' if ok else 'cancelled'}',order_id:'{order_id}'}},'*')}}catch(e){{}}
setTimeout(()=>window.close(),900)</script></body>""")


@api.get("/paypal/return")
def paypal_return(token: str, db=Depends(get_db)):
    row = db.query(TransactionLedger).filter_by(paypal_order_id=token).first()
    if not row:
        return _popup("Unknown order", False)
    try:
        cap = paypal.capture_order(token)
        event = {"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {
            "id": cap["capture_id"], "status": cap["status"], "custom_id": f"EP-{row.id}",
            "supplementary_data": {"related_ids": {"order_id": token}}}}
        handle_capture_event(db, event, verified="server-captured", payer_id=cap.get("payer_id"))
        return _popup("Payment captured - access unlocked", True, token)
    except PayPalError as e:
        row.status = "FAILED"
        db.commit()
        return _popup(f"Capture failed: {str(e)[:120]}", False, token)


@api.get("/paypal/cancel")
def paypal_cancel(token: str = "", db=Depends(get_db)):
    row = db.query(TransactionLedger).filter_by(paypal_order_id=token).first()
    if row and row.status == "PENDING":
        row.status = "CANCELLED"
        row.final_charged_amount = 0.0
        db.commit()
    return _popup("Payment cancelled - service stays DORMANT at $0.00", False, token)


def handle_capture_event(db, event: dict, verified: str, payer_id: str | None = None) -> dict:
    res = event["resource"]
    order_id = (res.get("supplementary_data") or {}).get("related_ids", {}).get("order_id")
    row = None
    if res.get("custom_id", "").startswith("EP-"):
        row = db.get(TransactionLedger, int(res["custom_id"][3:]))
    if not row and order_id:
        row = db.query(TransactionLedger).filter_by(paypal_order_id=order_id).first()
    if not row:
        raise HTTPException(404, "no matching ledger entry")
    changed = billing.activate_entitlement(db, row, res["id"], payer_id)
    svc = db.get(MerchantService, row.service_id)
    return {"entitlement": svc.status if changed else f"{svc.status} (already processed)", "receipt": row.receipt_hash or "",
            "ledger_id": row.id, "verified": verified}


@api.post("/webhooks/paypal")
async def paypal_webhook(request: Request, db=Depends(get_db)):
    raw = await request.body()
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON")
    if event.get("event_type") != "PAYMENT.CAPTURE.COMPLETED":
        return {"ignored": event.get("event_type")}
    res = event.get("resource", {})
    order_id = (res.get("supplementary_data") or {}).get("related_ids", {}).get("order_id", "")
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        if paypal.configured and paypal.webhook_id:
            if not paypal.verify_webhook(headers, event):
                raise HTTPException(401, "webhook signature verification failed")
            verified = "signature"
        elif paypal.configured and not order_id.startswith("SIM"):
            # no webhook id configured: confirm authoritatively with PayPal instead of trusting the payload
            order = paypal.get_order(order_id)
            caps = order["purchase_units"][0].get("payments", {}).get("captures", [])
            if not any(c["id"] == res.get("id") and c["status"] == "COMPLETED" for c in caps):
                raise HTTPException(401, "capture not confirmed by PayPal")
            verified = "order-lookup"
        elif order_id.startswith("SIM"):
            verified = "simulated"
        else:
            raise HTTPException(401, "unverifiable webhook")
    except PayPalError as e:
        raise HTTPException(502, str(e))
    return handle_capture_event(db, event, verified)


app.include_router(api)

# ----------------------------------------------------------------------------- serve built UI (Docker / Render)
DIST = os.getenv("FRONTEND_DIST") or os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(DIST):
    assets = os.path.join(DIST, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = os.path.join(DIST, path)
        if path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(DIST, "index.html"))
