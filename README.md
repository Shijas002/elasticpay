# ElasticPay - Agent-Negotiated JIT Subscriptions

> **$0.00 until you actually use it.** Instead of a fixed monthly fee, ElasticPay keeps every service `DORMANT` and lets a
> **Personal CFO Agent** negotiate a Just-in-Time pass with the **Merchant Yield Agent** the moment you show intent. Every
> micro-payment credits toward the merchant's monthly cap ("Rent-to-Own"), so you can never pay more than the subscription would have cost.

Built for the **PayPal AI Hackathon 2026** (Agentic Commerce / PayPal + AI), with an **AG Grid** command center and a **Render** blueprint.

## The invariant

```
Charge = min( Negotiated Pass Price , max( 0 , Monthly Cap - Cumulative Cycle Spend ) )
```

Implemented in `backend/agents.py::rent_to_own_charge` (Decimal arithmetic). When `Cumulative Cycle Spend == Monthly Cap` (or a monthly pass is bought)
the service becomes `CAP_UNLOCKED` (UNLIMITED_CYCLE) for the rest of the 30-day window. At day 30 everything resets to `DORMANT` at $0.00.

## Architecture

```
 React + Tailwind + AG Grid                          FastAPI (Python 3.11+)                     PayPal Sandbox
┌───────────────────────────────┐   NDJSON stream   ┌────────────────────────────────────┐
│ Simulator bar (A / B / C,     │ ────────────────► │ POST /api/negotiate-and-unlock     │
│   +24h, +30d)                 │ ◄──────────────── │   Context Agent ─► Merchant Yield  │
│ <ElasticPayGate/> (HTTP 402)  │   live trace      │   Agent ◄─► Buyer CFO Agent        │
│ 3 mock merchant apps          │                   │   └─ tool: execute_paypal_jit_     │   OAuth2 token
│ Negotiation trace modal       │                   │        settlement                  │ ─► POST /v1/oauth2/token
│ AG Grid Command Center        │                   │ paypal_client.py                   │ ─► POST /v2/checkout/orders
│  (matrix + audit ledger)      │                   │ billing.py (cycle clock, state     │ ─► POST /v2/checkout/orders/{id}/capture
└───────────────────────────────┘                   │   machine, HMAC receipts)          │ ◄─ POST /api/webhooks/paypal
                                                    │ SQLite: UserWallet, MerchantService│
                                                    │         TransactionLedger, SimState│
                                                    └────────────────────────────────────┘
```

```
elasticpay/
├── LICENSE  README.md  Dockerfile  render.yaml  .env.example
├── backend/   main.py  agents.py  paypal_client.py  billing.py  models.py  database.py  requirements.txt
└── frontend/  package.json  vite.config.js  tailwind.config.js  src/{App,MiniApps,CommandCenter}.jsx  src/api.js
```

### The three agents (`backend/agents.py`)

| Agent | Inputs | Output |
|---|---|---|
| **Context & Telemetry** | trigger text, time of day, free minutes, calendar pressure, dormancy, purchases this cycle | session minutes, P(use in 30d), expected active days, usage clustering |
| **Merchant Yield** | context report, price book, cycle spend | per-tier ask, with a yield discount (max 20%) that maximises `charge x P(accept)`; headline tier |
| **Buyer CFO** | asks, remaining budget, cap headroom, approval threshold | expected 30-day cost per tier over a low/base/high usage distribution (each path capped by the monthly cap), counter-offer, tier choice, `execute_paypal_jit_settlement` |

* **LLM mode** - set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` or `GROQ_API_KEY` (optionally `LLM_PROVIDER`, `LLM_MODEL`). Each agent answers via a *forced tool call*.
  Model output is never trusted for money: prices, proration and budget checks are recomputed server-side and the model is clamped to guardrails (merchant floor 20%, CFO counter <= 15%, budget).
* **Deterministic mode** (no key) - the same pipeline runs on a multi-variable policy engine, so judges need no API key. The header chip shows which mode is active.
* Charges above the wallet's `auto_approve_threshold` ($5.00) pause for explicit user approval.

### PayPal integration (`backend/paypal_client.py`)

1. `POST /v1/oauth2/token` - client-credentials, token cached.
2. `POST /v2/checkout/orders` - JIT order with `item_total` = negotiated price, `discount` = proration credit, and line-item metadata (merchant, tier, proration credit, remaining cap); `custom_id = EP-<ledger id>`.
3. Buyer approves in a PayPal sandbox popup -> `GET /api/paypal/return` -> `POST /v2/checkout/orders/{id}/capture` -> real `capture_id` stored.
4. `POST /api/webhooks/paypal` - handles `PAYMENT.CAPTURE.COMPLETED`; verified by PayPal signature API when `PAYPAL_WEBHOOK_ID` is set, otherwise by an authoritative order lookup. Activation is idempotent, and an HMAC-SHA256 receipt is written to the ledger.

**Without credentials** the app runs a clearly labelled **SIMULATED_SANDBOX** rail: PayPal-shaped IDs (`SIM...`), the same capture -> webhook -> entitlement path, and a `SIMULATED` badge in the ledger.
It also falls back to this rail if the live sandbox is unreachable (`PAYPAL_FALLBACK_SIMULATE=true`). Simulated IDs are never presented as real PayPal transactions.

## Run locally

```bash
cp .env.example .env            # optional: add PayPal sandbox + LLM keys
# backend
cd backend && pip install -r requirements.txt && uvicorn main:app --reload --port 8000
# frontend (new terminal)
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api -> :8000)
```

Single-process alternative: `cd frontend && npm run build`, then `cd backend && uvicorn main:app` and open http://localhost:8000.
Docker: `docker build -t elasticpay . && docker run -p 8000:8000 --env-file .env elasticpay`.

## PayPal Sandbox setup

1. developer.paypal.com -> **Apps & Credentials** -> Sandbox -> *Create App* -> copy **Client ID** and **Secret** into `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`.
2. Set `PUBLIC_BASE_URL` to where the app is served (`http://localhost:8000` locally, your Render URL when deployed) - PayPal redirects the approval popup there.
3. *(Optional)* **Webhooks** -> add `https://<your-host>/api/webhooks/paypal` for `PAYMENT.CAPTURE.COMPLETED` and set `PAYPAL_WEBHOOK_ID`.
4. **Sandbox credentials for judges:** Dashboard -> *Testing Tools -> Sandbox Accounts* -> use a **Personal** (buyer) account's email and system-generated password at the PayPal popup.
   > Fill in before submission: `buyer email: ________  password: ________`  (the repo ships no real credentials)

## Judge testing guide (about 60 seconds, one browser window)

1. Open the app. Header shows the rail (Sandbox live / Simulated) and agent engine. All three services are `DORMANT`, simulated clock is the **20th** with **19 dormant days** saved.
2. **Scenario A** (default) -> *Merchant Ecosystem* -> **StreamFlix** -> **Launch App**. The 402 gate opens the negotiation: watch Context -> Merchant -> CFO reason and counter in real time, then the PayPal order/capture/webhook trace. The app unlocks (`JIT_ACTIVE`, 24h).
3. Click **Go unlimited - pay only $X more**: the CFO pays only the *remaining* cap (Rent-to-Own proration credit shown), asks for your approval (> $5), and the service flips to `CAP_UNLOCKED`.
4. **Scenario B** + **CloudDev Pro**: heavy intent -> the CFO overrides the merchant's headline and picks a 72h sprint pass.
5. **Scenario C** (only $3.00 left) + **CloudDev Pro**: no tier fits the budget, so the CFO **declines** and the service stays `DORMANT` at $0.00 (audited in the ledger).
6. Time machine: **+24 Hours** expires passes back to `DORMANT`; **+30 Days** closes the cycle (auto-renewal $0.00, savings banked, dormant days accrue).
7. **Financial Command Center**: AG Grid matrix (status badges, proration progress bars, countdown) and the audit ledger (reasoning, tariff, proration discount, charge, Order/Capture IDs). Click a ledger row to replay the agent trace and HMAC receipt. **Reset demo** restores the seed.

## Deploy to Render

`render.yaml` + `Dockerfile` give a 1-click Blueprint: New -> Blueprint -> select this repo -> fill the `sync: false` secrets (`PAYPAL_*`, `PUBLIC_BASE_URL`, optional LLM key). The image builds the React app and serves it from FastAPI on `$PORT`; health check is `/api/health`.
SQLite lives in `/tmp` (ephemeral) and re-seeds on start, which suits a demo.

## Notes & limits

* The merchant apps are mocks we control so the whole journey is testable without third-party paywalls. The platform clock is simulated (that is what the time machine moves).
* "Total $ Saved" = flat monthly cap minus what was paid this cycle (projected) plus banked savings from closed cycles.
* Live PayPal capture requires the buyer to approve in the sandbox popup (PayPal does not allow server-side capture of unapproved orders).

## License

MIT - see [LICENSE](LICENSE).
