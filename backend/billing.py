"""Cycle clock, entitlement state machine, receipts and serialization."""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta

from agents import SCENARIOS, TIERS
from database import BASE_BUDGET
from models import MerchantService, SimState, TransactionLedger, UserWallet

CYCLE_DAYS = 30
TIGHT_BUDGET_LEFT = 3.00


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def get_sim(db) -> SimState:
    return db.get(SimState, 1)


def get_wallet(db) -> UserWallet:
    return db.get(UserWallet, 1)


def cycle_end(sim: SimState) -> datetime:
    return sim.cycle_start + timedelta(days=CYCLE_DAYS)


def apply_scenario(db, key: str):
    sim, wallet = get_sim(db), get_wallet(db)
    sim.scenario = key
    wallet.monthly_budget_limit = round(wallet.current_month_spend + TIGHT_BUDGET_LEFT, 2) if key == "C" else BASE_BUDGET
    db.commit()


# ------------------------------------------------------------------ time machine
def _accrue_dormancy(db, old: datetime, new: datetime):
    for svc in db.query(MerchantService).all():
        if svc.status == "DORMANT":
            dormant = new - old
        elif svc.status == "ACTIVE_PASS" and svc.access_expires_at:
            dormant = max(timedelta(0), new - max(old, svc.access_expires_at))
        else:
            dormant = timedelta(0)
        svc.dormant_days_saved = round(svc.dormant_days_saved + dormant.total_seconds() / 86400, 2)


def expire_passes(db):
    sim = get_sim(db)
    for svc in db.query(MerchantService).all():
        if svc.status == "ACTIVE_PASS" and svc.access_expires_at and svc.access_expires_at <= sim.sim_now:
            svc.status, svc.access_expires_at = "DORMANT", None
    db.commit()


def _reset_cycle(db, sim: SimState) -> list[str]:
    notes = []
    wallet = get_wallet(db)
    for svc in db.query(MerchantService).all():
        saved = max(0.0, svc.monthly_cap_price - svc.current_cycle_spend)
        svc.lifetime_saved = round(svc.lifetime_saved + saved, 2)
        notes.append(f"{svc.name}: cycle closed at ${svc.current_cycle_spend:.2f} of ${svc.monthly_cap_price:.2f} cap "
                     f"(saved ${saved:.2f} vs flat plan) -> DORMANT, $0.00 auto-renewal")
        svc.current_cycle_spend, svc.status, svc.access_expires_at = 0.0, "DORMANT", None
    wallet.current_month_spend = 0.0
    sim.cycle_start = sim.sim_now
    wallet.monthly_budget_limit = TIGHT_BUDGET_LEFT if sim.scenario == "C" else BASE_BUDGET
    return notes


def advance_time(db, delta: timedelta) -> dict:
    sim = get_sim(db)
    remaining, notes, resets = delta, [], 0
    while remaining > timedelta(0):
        step = min(remaining, max(timedelta(0), cycle_end(sim) - sim.sim_now))
        if step > timedelta(0):
            _accrue_dormancy(db, sim.sim_now, sim.sim_now + step)
            sim.sim_now += step
            remaining -= step
        db.flush()
        expire_passes(db)
        if sim.sim_now >= cycle_end(sim):
            notes += _reset_cycle(db, sim)
            resets += 1
    db.commit()
    return {"cycle_reset": resets > 0, "notes": notes}


# ------------------------------------------------------------------ entitlement
def receipt_hash(ledger: TransactionLedger) -> str:
    payload = "|".join(str(x) for x in (ledger.id, ledger.service_id, ledger.pass_type, f"{ledger.final_charged_amount:.2f}",
                                       ledger.paypal_order_id, ledger.paypal_capture_id))
    secret = os.getenv("RECEIPT_SECRET", "elasticpay-dev").encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def activate_entitlement(db, ledger: TransactionLedger, capture_id: str, payer_id: str | None = None) -> bool:
    """Idempotent: flips the service to ACTIVE_PASS / CAP_UNLOCKED once a capture is confirmed."""
    if ledger.status == "COMPLETED":
        return False
    svc, sim, wallet = db.get(MerchantService, ledger.service_id), get_sim(db), get_wallet(db)
    charge = ledger.final_charged_amount
    svc.current_cycle_spend = round(svc.current_cycle_spend + charge, 2)
    wallet.current_month_spend = round(wallet.current_month_spend + charge, 2)
    if payer_id:
        wallet.paypal_payer_id = payer_id
    if ledger.pass_type == "MONTHLY" or svc.current_cycle_spend >= svc.monthly_cap_price - 0.005:
        svc.status, svc.access_expires_at = "CAP_UNLOCKED", cycle_end(sim)
    else:
        start = svc.access_expires_at if svc.status == "ACTIVE_PASS" and svc.access_expires_at and svc.access_expires_at > sim.sim_now else sim.sim_now
        svc.status, svc.access_expires_at = "ACTIVE_PASS", start + timedelta(hours=TIERS[ledger.pass_type]["hours"])
    ledger.paypal_capture_id, ledger.status = capture_id, "COMPLETED"
    ledger.receipt_hash = receipt_hash(ledger)
    db.commit()
    return True


# ------------------------------------------------------------------ serialization / agent inputs
def serialize_service(svc: MerchantService, sim: SimState) -> dict:
    cap_left = max(0.0, round(svc.monthly_cap_price - svc.current_cycle_spend, 2))
    hours_left = 0.0
    if svc.status != "DORMANT" and svc.access_expires_at:
        hours_left = max(0.0, (svc.access_expires_at - sim.sim_now).total_seconds() / 3600)
    return {
        "id": svc.id, "name": svc.name, "slug": svc.slug, "category": svc.category, "tagline": svc.tagline,
        "monthly_cap_price": svc.monthly_cap_price, "day_pass_price": svc.day_pass_price, "weekend_pass_price": svc.weekend_pass_price,
        "current_cycle_spend": svc.current_cycle_spend, "cap_remaining": cap_left,
        "progress_pct": round(min(100.0, svc.current_cycle_spend / svc.monthly_cap_price * 100), 1),
        "status": svc.status, "access_expires_at": iso(svc.access_expires_at), "hours_remaining": round(hours_left, 2),
        "dormant_days_saved": round(svc.dormant_days_saved, 1),
        "dollars_saved": round(svc.lifetime_saved + cap_left, 2),
    }


def serialize_ledger(row: TransactionLedger, svc_names: dict) -> dict:
    return {
        "id": row.id, "timestamp": iso(row.timestamp), "sim_time": iso(row.sim_time), "service_id": row.service_id,
        "service_name": svc_names.get(row.service_id, "?"), "scenario_context": row.scenario_context,
        "buyer_agent_reasoning": row.buyer_agent_reasoning, "merchant_agent_offer": row.merchant_agent_offer,
        "pass_type": row.pass_type, "gross_price": row.gross_price, "proration_credit_applied": row.proration_credit_applied,
        "final_charged_amount": row.final_charged_amount, "paypal_order_id": row.paypal_order_id,
        "paypal_capture_id": row.paypal_capture_id, "status": row.status, "settlement_mode": row.settlement_mode,
        "receipt_hash": row.receipt_hash, "agent_trace": row.agent_trace,
    }


def build_agent_inputs(db, svc: MerchantService):
    sim, wallet = get_sim(db), get_wallet(db)
    purchases = db.query(TransactionLedger).filter(
        TransactionLedger.service_id == svc.id, TransactionLedger.status == "COMPLETED",
        TransactionLedger.sim_time >= sim.cycle_start).count()
    service = {k: v for k, v in serialize_service(svc, sim).items() if k in (
        "id", "name", "category", "monthly_cap_price", "day_pass_price", "weekend_pass_price", "current_cycle_spend", "status")}
    wallet_d = {"budget_left": round(max(0.0, wallet.monthly_budget_limit - wallet.current_month_spend), 2),
                "auto_approve_threshold": wallet.auto_approve_threshold}
    history = {"purchases_this_cycle": purchases, "dormant_days": svc.dormant_days_saved}
    return service, wallet_d, SCENARIOS[sim.scenario], history
