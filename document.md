# ElasticPay - A to Z Learning Guide

This guide explains every file in the project, what it does, and how the pieces work together. Read it top to bottom once, then keep it as a reference.

---

## 1. The idea in one minute

Normal subscriptions charge a fixed fee every month, even if you use the service for one evening. ElasticPay flips this:

1. Every service starts **DORMANT** and costs **$0.00**.
2. When you try to use a service, your **Buyer CFO Agent** and the merchant's **Yield Agent** negotiate a short pass (24h, 72h, or monthly).
3. You pay through **PayPal**. Every payment counts toward the merchant's **monthly cap**, so you never pay more than the normal subscription price. This is called **Rent-to-Own**.
4. When the pass expires, the service goes back to DORMANT at $0.

### The one formula to remember

```
Charge = min( Negotiated Pass Price , max( 0 , Monthly Cap - Cumulative Cycle Spend ) )
```

Example: StreamFlix cap is $15.00. You already paid $1.59. A monthly pass is listed at $15.00. You are charged `min(15.00, 15.00 - 1.59) = $13.41`. The $1.59 difference is the **proration credit**.

### Entitlement states

| Status in database | Shown in UI as | Meaning |
|---|---|---|
| `DORMANT` | DORMANT | No access, $0 charged |
| `ACTIVE_PASS` | JIT_ACTIVE | A 24h/72h pass is running |
| `CAP_UNLOCKED` | CAP_UNLOCKED | Cap reached or monthly pass bought; unlimited until the cycle ends |

---

## 2. Project layout

```
elasticpay/
├── LICENSE                  MIT license
├── README.md                Short overview + judge test guide
├── document.md              This file
├── Dockerfile               Builds frontend + backend into one image
├── render.yaml              Render.com 1-click deploy blueprint
├── .env.example             All environment variables, documented
├── .gitignore
├── backend/
│   ├── requirements.txt     Python dependencies
│   ├── models.py            Database tables
│   ├── database.py          DB connection + seed data
│   ├── paypal_client.py     PayPal Sandbox API client
│   ├── agents.py            The 3 AI agents + proration math
│   ├── billing.py           Clock, state machine, receipts
│   └── main.py              FastAPI endpoints
└── frontend/
    ├── package.json  vite.config.js  tailwind.config.js  postcss.config.js  index.html
    └── src/
        ├── main.jsx         React entry point
        ├── index.css        Tailwind + small custom classes
        ├── api.js           Calls to the backend (incl. streaming)
        ├── App.jsx          Main UI, simulator bar, gate, negotiation modal
        ├── MiniApps.jsx     The 3 fake merchant apps
        └── CommandCenter.jsx  AG Grid dashboard
```

---

## 3. Backend, file by file

### 3.1 `backend/models.py` - the database tables

Uses SQLAlchemy. Four tables:

- **UserWallet** (one row): `monthly_budget_limit`, `current_month_spend`, `paypal_payer_id`, `auto_approve_threshold` (default $5; charges above this need user approval).
- **MerchantService** (three rows: StreamFlix, CloudDev Pro, AlphaRead): prices (`monthly_cap_price`, `day_pass_price`, `weekend_pass_price`), `current_cycle_spend`, `status`, `access_expires_at`, `dormant_days_saved`, and `lifetime_saved` (savings banked from finished cycles).
- **TransactionLedger**: one row per negotiation outcome - reasoning of the agents, offer text, `pass_type`, `gross_price` (negotiated price), `proration_credit_applied`, `final_charged_amount`, PayPal `order_id` and `capture_id`, `status` (PENDING, COMPLETED, DECLINED, CANCELLED, FAILED), `settlement_mode` (real sandbox or simulated), `receipt_hash`, and the full `agent_trace` as JSON.
- **SimState** (one row): the **simulated clock** (`sim_now`), `cycle_start`, and the chosen judge `scenario` (A/B/C). This is what the time machine moves.

### 3.2 `backend/database.py` - connection and seed

- Loads `.env`, creates the SQLite engine (`DATABASE_URL`), and a session factory.
- `seed()` creates the starting data: simulated date is the **20th** of the month, cycle started on the 1st, each service has **19 dormant days saved**, spend is $0.
- Seed prices: StreamFlix cap $15 / 24h $1.99 / 72h $3.99; CloudDev Pro $20 / $2.49 / $4.99; AlphaRead $12 / $1.49 / $2.99. Wallet budget is $60.
- `seed(reset=True)` wipes everything (used by the "Reset demo" button).

### 3.3 `backend/paypal_client.py` - talking to PayPal

Class `PayPalClient` with these steps:

1. `access_token()` - `POST /v1/oauth2/token` with client id/secret, caches the token until it nearly expires.
2. `create_order()` - `POST /v2/checkout/orders`. The order carries line-item data: `item_total` = negotiated price, `discount` = proration credit, so `amount = charge`. The item description includes merchant, tier, proration credit and remaining cap. `custom_id = EP-<ledger id>` links PayPal back to our ledger row. A `PayPal-Request-Id` header makes calls idempotent.
3. `capture_order()` - `POST /v2/checkout/orders/{id}/capture`; returns the real `capture_id`. If PayPal says already captured, it fetches the order instead.
4. `verify_webhook()` - calls PayPal's signature-verification API.
5. `simulate_order()` / `simulate_capture()` - fake IDs starting with `SIM`, used when no credentials exist.

`paypal.configured` is true only if both `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` are set. `paypal.mode` is `PAYPAL_SANDBOX` or `SIMULATED_SANDBOX`.

### 3.4 `backend/agents.py` - the brain

**Money helpers**
- `rent_to_own_charge(price, cap, spent)` implements the formula using `Decimal` so cents never drift.
- `TIERS`: DAY (24h), WEEKEND (72h), MONTHLY (720h).

**Judge scenarios (`SCENARIOS`)**
- A: Tuesday 11:15 PM, 45 free minutes, meeting tomorrow, casual intent.
- B: Saturday 10:00 AM, 12 free hours, heavy intent.
- C: Wednesday evening, moderate intent, only $3.00 budget left.

**LLM transport (`llm_config`, `llm_call_tool`)**
- Picks a provider from env keys (Anthropic, OpenAI, Gemini, Groq). Each agent call is a **forced tool call** so the answer is structured JSON.
- If no key exists, agents use deterministic logic instead.

**Shared math**
- `coverage_days` - how many active usage days a pass absorbs (weekend pass covers more if usage is clustered).
- `expected_cycle_cost` - expected total spend this cycle if you buy a tier now and top up later with day passes. It averages three usage cases (low 0.5x, base 1x, high 1.6x with weights 30/45/25%), each capped by the remaining monthly cap.
- `p_accept` - probability the user accepts a price (a sigmoid of perceived value minus price).

**Class `Negotiation`** runs the three agents in order and yields **trace events** (these stream to the UI):

1. **Context & Telemetry Agent** - turns the trigger into numbers: session minutes, probability of further use in 30 days, expected active days, clustering. Deterministic version: a score from intent, weekend boost, late-night fatigue, calendar pressure, dormancy and prior purchases, passed through a sigmoid.
2. **Merchant Yield Agent** - for each tier picks a discount (0-20%) that maximises `charge x P(accept)`, then names a headline tier. It never stacks a yield discount on top of the Rent-to-Own credit for the monthly tier.
3. **Buyer CFO Agent** - drops tiers that exceed the remaining budget, picks the tier with the lowest expected 30-day cost (ties go to the smaller upfront charge), and may counter-offer (10% off, or 5% if budget is tight). If nothing fits the budget it **declines**. If an LLM picks an invalid tier, a guardrail overrides it.
4. Merchant responds to the counter: accepts if the price is above its floor (80% of list) and revenue stays within 10%; otherwise meets in the middle.
5. Final charge is recomputed with the formula; `needs_approval` is set if charge > auto-approve threshold.

The result is stored in `neg.outcome`. `force_tier` lets the user force MONTHLY ("Go unlimited" button).

### 3.5 `backend/billing.py` - clock, state machine, receipts

- `advance_time(db, delta)` - moves the simulated clock. For each service it adds **dormant days** (only the time a service was actually not accessible). If it crosses day 30, `_reset_cycle` runs: banks savings (`cap - spend`), zeroes spend, sets everything DORMANT, resets the wallet's monthly spend.
- `expire_passes` - any `ACTIVE_PASS` whose expiry is in the past becomes DORMANT.
- `activate_entitlement(db, ledger, capture_id)` - **idempotent** (safe to call twice). Adds the charge to cycle spend and wallet spend; sets `CAP_UNLOCKED` if monthly pass or cap reached, else `ACTIVE_PASS` with expiry = now + pass hours (extends an existing pass); marks the ledger COMPLETED and writes the receipt.
- `receipt_hash` - HMAC-SHA256 over ledger id, service, tier, amount, order id, capture id, using `RECEIPT_SECRET`.
- `apply_scenario` - scenario C sets budget so exactly $3.00 remains; A and B restore the $60 budget.
- `serialize_*` - converts rows to JSON for the API. `build_agent_inputs` gathers what the agents need.

### 3.6 `backend/main.py` - the API

All routes live under `/api`.

| Route | Purpose |
|---|---|
| `GET /health` | Health check (Render uses it) |
| `GET /state` | Everything the UI needs: services, wallet, clock, scenarios, PayPal/LLM mode |
| `GET /services` | Just the services |
| `GET /ledger` | All ledger rows, newest first |
| `GET /ledger/status/{order_id}` | Status of one order (UI polls this during PayPal approval) |
| `POST /scenario` | Choose A, B or C |
| `POST /simulate-time` | Advance hours/days (time machine) |
| `POST /reset` | Reseed the demo |
| `POST /negotiate-and-unlock` | The main flow; returns a **streaming NDJSON** response |
| `GET /paypal/return` and `/paypal/cancel` | Where the PayPal popup lands |
| `POST /webhooks/paypal` | Receives `PAYMENT.CAPTURE.COMPLETED` |

**How `/negotiate-and-unlock` works** (`_stream` generator):
1. Reject if the service is already `CAP_UNLOCKED` (HTTP 409).
2. Run `Negotiation`, sending each trace line to the browser as it happens.
3. If DECLINE: write a DECLINED ledger row and send a `result` event.
4. If charge > threshold: store a **proposal** in memory, send `approval_required`, and stop. The UI later calls again with `proposal_id` to continue.
5. `_settle`: re-check the formula and budget against live state, create a PENDING ledger row, log the `execute_paypal_jit_settlement` tool call, then:
   - **Live sandbox**: create the PayPal order and send `paypal_approval` (with the approve URL). Capture happens later in `/paypal/return`.
   - **Simulated**: create fake order, fake capture, build a webhook event, and run `handle_capture_event` so the same path as real life is exercised.
6. Final `result` event includes the new state.

**Webhook security** (`/webhooks/paypal`): with `PAYPAL_WEBHOOK_ID` it verifies PayPal's signature; otherwise it asks PayPal for the order and confirms the capture; simulated `SIM` orders are accepted only in simulated mode. At the bottom, `main.py` also serves the built React app (used in Docker/Render).

---

## 4. Frontend, file by file

### 4.1 Config files
- `package.json` - React 18, Vite, Tailwind 3, `ag-grid-community` + `ag-grid-react` 34, `lucide-react` icons.
- `vite.config.js` - React plugin and a dev proxy: `/api` goes to `localhost:8000`.
- `tailwind.config.js`, `postcss.config.js` - Tailwind setup and custom dark colors.
- `index.html`, `src/main.jsx`, `src/index.css` - entry point and a few shared classes (`btn`, `card`, `pop` animation).

### 4.2 `src/api.js`
Small fetch wrappers plus `negotiate(body, onEvent)`, which reads the response stream, splits on newlines, parses each JSON line and calls `onEvent`. That is how the negotiation appears live.

### 4.3 `src/App.jsx` - the main screen
- **State**: services/wallet/clock (`state`), `ledger`, accumulated `telemetry`, current tab, open `modal`.
- **SimulatorBar**: scenario buttons A/B/C, **+24 Hours**, **+30 Days**, **Reset demo**, and the simulated clock.
- **ElasticPayGate**: if the service is DORMANT it shows an "HTTP 402 Payment Required" screen with **Launch App**. Otherwise it shows the real app plus a status banner (and **Go unlimited** while on a pass).
- **NegotiationModal**: shows agent messages as colored bubbles, then depending on phase: approval prompt (Approve/Decline), PayPal popup waiting, success, declined, cancelled or error.
- **Event handling in `run()`**: `trace` adds a bubble; `approval_required` shows the approve button; `paypal_approval` opens the PayPal popup and polls order status every 2s (also listens for the popup's `postMessage`); `result` updates state; `error` shows a message.

### 4.4 `src/MiniApps.jsx`
Three fake merchant apps (StreamFlix poster grid and player, CloudDev code editor with "AI Assist", AlphaRead tickers and headlines). They exist so judges can test the whole journey without a real paywall.

### 4.5 `src/CommandCenter.jsx` - AG Grid dashboard
- Registers AG Grid modules and a dark theme.
- **KPI cards**: spend vs flat plans, saved, dormant days, budget left.
- **Grid 1 - Subscription & Proration Matrix**: custom cell renderers for the status badge and the **progress bar** (spend vs cap), plus dormant days, dollars saved and expiration countdown.
- **Grid 2 - Audit Ledger**: timestamp, reasoning, tariff badge, proration discount, charged amount, rail badge (PayPal Sandbox or Simulated), Order ID, Capture ID, status. Sorting and filtering are on. Click a row to replay its agent trace and HMAC receipt.
- **Live agent telemetry** panel shows the latest trace lines.

---

## 5. Deployment files

- `Dockerfile` - stage 1 builds the React app with Node; stage 2 installs Python deps, copies the backend and the built `dist`, and runs `uvicorn` on `$PORT`.
- `render.yaml` - Render blueprint: Docker web service, health check `/api/health`, secrets marked `sync: false` so you enter them in the Render dashboard.
- `.env.example` - every variable: PayPal credentials, `PAYPAL_BASE_URL`, `PAYPAL_WEBHOOK_ID`, `PAYPAL_FALLBACK_SIMULATE`, `PUBLIC_BASE_URL`, LLM keys, `DATABASE_URL`, `RECEIPT_SECRET`, `AGENT_TRACE_PACE`.

---

## 6. End-to-end walkthrough (what happens when you click "Launch App")

1. Browser calls `POST /api/negotiate-and-unlock {service_id}`.
2. Backend loads the service, wallet, scenario and history, and starts `Negotiation`.
3. Context Agent reports usage estimates; Merchant offers three tiers; CFO compares costs and budget, picks a tier, counters; Merchant answers. Each step streams to the modal.
4. Final price goes through the Rent-to-Own formula.
5. If above $5, you approve. Then settlement runs: PayPal order, capture, webhook.
6. `activate_entitlement` flips the status and sets the expiry. The gate sees the new state and shows the app.
7. Ledger row is stored with reasoning, trace and receipt; the AG Grid tables update.

### Try these to see each feature
- **Scenario A + StreamFlix**: cheap 24h pass, merchant gives a yield discount.
- Then **Go unlimited**: proration credit appears, approval required, CAP_UNLOCKED.
- **Scenario B + CloudDev Pro**: CFO rejects the merchant's monthly headline and counters with a 72h pass.
- **Scenario C + CloudDev Pro**: no tier fits $3.00, so the CFO declines.
- **+24 Hours**: passes expire to DORMANT. **+30 Days**: cycle closes and resets.

---

## 7. Running it

```bash
# backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Without any keys it runs in **simulated sandbox + deterministic agents** mode. Add PayPal sandbox credentials for real orders, and any one LLM key for model-driven agents.

## 8. Safety design to notice
- The model never decides money; the server recomputes prices, proration and budget.
- Webhook handling is idempotent and verified.
- Declines and cancellations are audited too.
- Simulated transactions are always labelled and never pass as real PayPal ones.

## 9. Known limits
- Live PayPal capture needs a buyer to approve in the sandbox popup (PayPal requires this).
- Pending approval proposals are held in memory, so a server restart drops them.
- The clock is simulated; it does not tick by itself.
- I verified simulated mode end to end; the live PayPal and LLM paths were not run because no credentials were available.

## 10. Ideas to explore next
- Change prices in `database.py` and watch the CFO choices change.
- Tweak weights in `Negotiation._det_context` and `expected_cycle_cost`.
- Add a fourth service by adding a seed row, an entry in `APPS` (`MiniApps.jsx`) and categories in `agents.py` dictionaries.
