"""PayPal Sandbox client: OAuth2 + Orders v2 create/capture + webhook verification,
with a clearly-labelled simulated verifier when no credentials are configured."""
import os
import time
import uuid

import httpx


class PayPalError(Exception):
    pass


def _fmt(x: float) -> str:
    return f"{x:.2f}"


class PayPalClient:
    def __init__(self):
        self._token = None
        self._token_exp = 0.0

    # -- configuration is read lazily so .env loading order never matters
    @property
    def base_url(self) -> str:
        return os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET"))

    @property
    def mode(self) -> str:
        return "PAYPAL_SANDBOX" if self.configured else "SIMULATED_SANDBOX"

    @property
    def fallback_enabled(self) -> bool:
        return os.getenv("PAYPAL_FALLBACK_SIMULATE", "true").lower() == "true"

    @property
    def webhook_id(self) -> str:
        return os.getenv("PAYPAL_WEBHOOK_ID", "")

    # -- 1. POST /v1/oauth2/token
    def access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        r = httpx.post(
            f"{self.base_url}/v1/oauth2/token",
            auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if r.status_code != 200:
            raise PayPalError(f"OAuth failed ({r.status_code}): {r.text[:200]}")
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + int(body.get("expires_in", 300))
        return self._token

    def _call(self, method: str, path: str, json_body=None, request_id: str | None = None):
        headers = {
            "Authorization": f"Bearer {self.access_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # idempotency key
        r = httpx.request(method, f"{self.base_url}{path}", json=json_body, headers=headers, timeout=25)
        if r.status_code >= 400:
            raise PayPalError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    # -- 2. POST /v2/checkout/orders
    def create_order(self, *, merchant_name: str, pass_label: str, pass_type: str, gross: float,
                     proration_credit: float, remaining_cap: float, custom_id: str,
                     return_url: str, cancel_url: str) -> dict:
        charge = round(gross - proration_credit, 2)
        desc = (f"Merchant: {merchant_name} | Tier: {pass_type} | Proration credit: ${_fmt(proration_credit)}"
                f" | Remaining monthly cap: ${_fmt(remaining_cap)}")[:127]
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": custom_id,
                "custom_id": custom_id,
                "description": f"ElasticPay JIT pass - {merchant_name}"[:127],
                "soft_descriptor": "ELASTICPAY"[:22],
                "amount": {
                    "currency_code": "USD",
                    "value": _fmt(charge),
                    "breakdown": {
                        "item_total": {"currency_code": "USD", "value": _fmt(gross)},
                        "discount": {"currency_code": "USD", "value": _fmt(proration_credit)},
                    },
                },
                "items": [{
                    "name": f"{merchant_name} - {pass_label}"[:127],
                    "description": desc,
                    "sku": f"{pass_type}-{custom_id}"[:127],
                    "quantity": "1",
                    "category": "DIGITAL_GOODS",
                    "unit_amount": {"currency_code": "USD", "value": _fmt(gross)},
                }],
            }],
            "payment_source": {"paypal": {"experience_context": {
                "brand_name": "ElasticPay",
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
                "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                "return_url": return_url,
                "cancel_url": cancel_url,
            }}},
        }
        order = self._call("POST", "/v2/checkout/orders", body, request_id=str(uuid.uuid4()))
        approve = next((l["href"] for l in order.get("links", []) if l.get("rel") in ("payer-action", "approve")), None)
        return {"id": order["id"], "status": order.get("status"), "approve_url": approve, "raw": order}

    # -- 3. POST /v2/checkout/orders/{id}/capture
    def capture_order(self, order_id: str) -> dict:
        try:
            data = self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id=f"cap-{order_id}")
        except PayPalError as e:
            if "ORDER_ALREADY_CAPTURED" not in str(e):
                raise
            data = self.get_order(order_id)
        cap = data["purchase_units"][0]["payments"]["captures"][0]
        return {
            "capture_id": cap["id"],
            "status": cap.get("status", data.get("status")),
            "amount": float(cap["amount"]["value"]),
            "payer_id": (data.get("payer") or {}).get("payer_id"),
            "raw": data,
        }

    def get_order(self, order_id: str) -> dict:
        return self._call("GET", f"/v2/checkout/orders/{order_id}")

    # -- webhook signature verification
    def verify_webhook(self, headers: dict, event: dict) -> bool:
        body = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": self.webhook_id,
            "webhook_event": event,
        }
        res = self._call("POST", "/v1/notifications/verify-webhook-signature", body)
        return res.get("verification_status") == "SUCCESS"

    # -- simulated verifier (no credentials / API unreachable). IDs are PayPal-shaped but prefixed SIM.
    @staticmethod
    def simulate_order() -> str:
        return "SIM" + uuid.uuid4().hex[:14].upper()

    @staticmethod
    def simulate_capture() -> str:
        return "SIMCAP" + uuid.uuid4().hex[:11].upper()


paypal = PayPalClient()
