"""SQLAlchemy models for ElasticPay."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class UserWallet(Base):
    __tablename__ = "user_wallet"

    id = Column(Integer, primary_key=True)
    monthly_budget_limit = Column(Float, nullable=False, default=60.0)
    current_month_spend = Column(Float, nullable=False, default=0.0)
    paypal_payer_id = Column(String, nullable=True)
    auto_approve_threshold = Column(Float, nullable=False, default=5.0)


class MerchantService(Base):
    __tablename__ = "merchant_service"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True)
    category = Column(String, nullable=False)
    tagline = Column(String, nullable=False, default="")
    monthly_cap_price = Column(Float, nullable=False)
    day_pass_price = Column(Float, nullable=False)
    weekend_pass_price = Column(Float, nullable=False)
    current_cycle_spend = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="DORMANT")  # DORMANT | ACTIVE_PASS | CAP_UNLOCKED
    access_expires_at = Column(DateTime, nullable=True)
    dormant_days_saved = Column(Float, nullable=False, default=0.0)
    lifetime_saved = Column(Float, nullable=False, default=0.0)  # savings from closed cycles


class TransactionLedger(Base):
    __tablename__ = "transaction_ledger"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)  # real wall-clock
    sim_time = Column(DateTime, nullable=False)  # simulated platform clock
    service_id = Column(Integer, ForeignKey("merchant_service.id"), nullable=False)
    scenario_context = Column(Text, nullable=False, default="")
    buyer_agent_reasoning = Column(Text, nullable=False, default="")
    merchant_agent_offer = Column(Text, nullable=False, default="")
    pass_type = Column(String, nullable=False)  # DAY | WEEKEND | MONTHLY | NONE
    gross_price = Column(Float, nullable=False, default=0.0)  # negotiated pass price before proration
    proration_credit_applied = Column(Float, nullable=False, default=0.0)
    final_charged_amount = Column(Float, nullable=False, default=0.0)
    paypal_order_id = Column(String, nullable=True)
    paypal_capture_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="PENDING")  # PENDING|COMPLETED|DECLINED|CANCELLED|FAILED
    settlement_mode = Column(String, nullable=False, default="SIMULATED_SANDBOX")
    receipt_hash = Column(String, nullable=True)
    agent_trace = Column(Text, nullable=False, default="[]")


class SimState(Base):
    """Single-row simulated clock + judge scenario."""

    __tablename__ = "sim_state"

    id = Column(Integer, primary_key=True)
    sim_now = Column(DateTime, nullable=False)
    cycle_start = Column(DateTime, nullable=False)
    scenario = Column(String, nullable=False, default="A")
