"""Engine, session factory and seed data."""
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
load_dotenv()

import models  # noqa: E402
from models import Base, MerchantService, SimState, TransactionLedger, UserWallet  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./elasticpay.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

BASE_BUDGET = 60.0

SEED_SERVICES = [
    dict(name="StreamFlix", slug="streamflix", category="streaming", tagline="Video streaming",
         monthly_cap_price=15.00, day_pass_price=1.99, weekend_pass_price=3.99),
    dict(name="CloudDev Pro", slug="clouddev", category="dev-tools", tagline="Cloud AI IDE",
         monthly_cap_price=20.00, day_pass_price=2.49, weekend_pass_price=4.99),
    dict(name="AlphaRead", slug="alpharead", category="finance", tagline="Financial terminal & news",
         monthly_cap_price=12.00, day_pass_price=1.49, weekend_pass_price=2.99),
]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed(db, reset: bool = False):
    """Seed on the 20th of the month: 19 dormant days already saved, $0 charged this cycle."""
    if reset:
        for table in (TransactionLedger, MerchantService, UserWallet, SimState):
            db.query(table).delete()
        db.commit()
    if db.query(MerchantService).count():
        return
    today = datetime.utcnow()
    cycle_start = datetime(today.year, today.month, 1, 9, 0)
    sim_now = cycle_start + timedelta(days=19)  # the 20th
    db.add(SimState(id=1, sim_now=sim_now, cycle_start=cycle_start, scenario="A"))
    db.add(UserWallet(id=1, monthly_budget_limit=BASE_BUDGET, current_month_spend=0.0,
                      paypal_payer_id=None, auto_approve_threshold=5.0))
    for s in SEED_SERVICES:
        db.add(MerchantService(**s, current_cycle_spend=0.0, status="DORMANT",
                               access_expires_at=None, dormant_days_saved=19.0, lifetime_saved=0.0))
    db.commit()


def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
