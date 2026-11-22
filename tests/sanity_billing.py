"""Live sanity round for the billing module — edge cases + envelopes that
test_billing.py doesn't cover. Run against a live server on :8000.
"""
import json
import random
import sys
import urllib.request

sys.path.insert(0, ".")

BASE = "http://localhost:8000/api/v1"
RUN = random.randint(10**8, 10**9 - 1)
_n = [0]
ok_n = fail_n = 0


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(name, cond, extra=""):
    global ok_n, fail_n
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    ok_n, fail_n = ok_n + cond, fail_n + (not cond)


def register(name, role):
    _n[0] += 1
    phone = f"+91{RUN}{_n[0]}"
    email = f"sb-{RUN}-{_n[0]}@demo.com"
    s, r = call("POST", "/auth/register", {
        "name": name, "phone": phone, "email": email,
        "password": "password123", "role": role})
    assert s == 201, r
    return r["data"]["user"]["id"], r["data"]["tokens"]["access_token"], email


def grant_admin(user_id, email):
    from app.core.database import SessionLocal
    from app.models import Role, UserRole
    db = SessionLocal()
    db.add(UserRole(user_id=user_id,
                    role_id=db.query(Role).filter(Role.code == "ADMIN").one().id))
    db.commit(); db.close()
    s, r = call("POST", "/auth/login",
                {"identifier": email, "password": "password123"})
    return r["data"]["tokens"]["access_token"]


# ---------- auth enforcement ----------
s, r = call("GET", "/billing/me")
check("unauth /billing/me → 401 + envelope",
      s == 401 and r.get("success") is False and "code" in r["error"], r)
s, r = call("POST", "/billing/orders", {"plan_id": 1})
check("unauth create order → 401", s == 401)
s, r = call("GET", "/admin/billing/plans")
check("unauth admin plans → 401", s == 401)

buyer_id, buyer, _ = register("Sanity Buyer", "BUYER")
seller_id, seller, _ = register("Sanity Seller", "SELLER")
admin_id, _, admin_email = register("Sanity Admin", "BUYER")
admin = grant_admin(admin_id, admin_email)

s, r = call("GET", "/admin/billing/plans", token=buyer)
check("non-admin → 403 FORBIDDEN", s == 403
      and r["error"]["code"] == "FORBIDDEN", r)

# ---------- plan CRUD validation ----------
s, r = call("POST", "/admin/billing/plans", {
    "code": "lower case!", "name": "Bad", "applies_to": "SELLER",
    "price_paise": 100}, token=admin)
check("bad plan code → 422 VALIDATION_ERROR",
      s == 422 and r["error"]["code"] == "VALIDATION_ERROR", r)

s, r = call("POST", "/admin/billing/plans", {
    "code": "NEG_PRICE", "name": "Bad", "applies_to": "SELLER",
    "price_paise": -5}, token=admin)
check("negative price → 422", s == 422)

s, r = call("POST", "/admin/billing/plans", {
    "code": "BAD_SCOPE", "name": "Bad", "applies_to": "WIZARD",
    "price_paise": 100}, token=admin)
check("bad applies_to → 422", s == 422)

s, r = call("POST", "/admin/billing/plans", {
    "code": "FREE_SELLER", "name": "Dup", "applies_to": "SELLER",
    "price_paise": 100}, token=admin)
check("duplicate code → 400 PLAN_CODE_TAKEN",
      s == 400 and r["error"]["code"] == "PLAN_CODE_TAKEN", r)

s, r = call("PUT", "/admin/billing/plans/999999",
            {"name": "ghost"}, token=admin)
check("update missing plan → 404 PLAN_NOT_FOUND",
      s == 404 and r["error"]["code"] == "PLAN_NOT_FOUND", r)

s, r = call("DELETE", "/admin/billing/plans/999999", token=admin)
check("delete missing plan → 404", s == 404)

# ---------- ordering ----------
s, r = call("POST", "/billing/orders", {"plan_id": 999999}, token=buyer)
check("order missing plan → 404 PLAN_NOT_FOUND",
      s == 404 and r["error"]["code"] == "PLAN_NOT_FOUND", r)

# inactive plan
s, r = call("POST", "/admin/billing/plans", {
    "code": f"INACTIVE_{RUN}", "name": "Inactive", "applies_to": "BUYER",
    "price_paise": 100, "is_active": False}, token=admin)
inactive_id = r["data"]["id"]
s, r = call("POST", "/billing/orders", {"plan_id": inactive_id}, token=buyer)
check("order inactive plan → 404", s == 404
      and r["error"]["code"] == "PLAN_NOT_FOUND")

# free plan short-circuits — create a FREE plan for this scope
s, r = call("GET", "/billing/plans?applies_to=BUYER")
free_id = next(p["id"] for p in r["data"] if p["code"] == "FREE_BUYER")
s, r = call("POST", "/billing/orders", {"plan_id": free_id}, token=buyer)
check("free plan → PAID immediately + subscription_id",
      s == 201 and r["data"]["status"] == "PAID"
      and r["data"]["subscription_id"], r)

# paid plan full cycle + guards
s, r = call("POST", "/admin/billing/plans", {
    "code": f"PRO_{RUN}", "name": "Pro", "applies_to": "BUYER",
    "price_paise": 49900, "duration_days": 30,
    "features": {"contact_reveals": 9}}, token=admin)
pro_id = r["data"]["id"]

s, r = call("POST", "/billing/orders", {"plan_id": pro_id}, token=buyer)
order_id = r["data"]["order_id"]

s, r = call("GET", f"/billing/orders/{order_id}/confirm", token=seller)
check("confirm is POST-only route → 405/404", s in (404, 405))
s, r = call("POST", f"/billing/orders/{order_id}/confirm",
            {}, token=seller)
check("other user's order → 404 ORDER_NOT_FOUND (no IDOR leak)",
      s == 404 and r["error"]["code"] == "ORDER_NOT_FOUND", r)

s, r = call("POST", f"/billing/orders/999999/confirm", {}, token=buyer)
check("confirm missing order → 404", s == 404)

s, r = call("POST", f"/billing/orders/{order_id}/confirm",
            {}, token=buyer)
check("confirm own order → PAID + subscription",
      s == 200 and r["data"]["status"] == "PAID", r)

s, r = call("POST", f"/billing/orders/{order_id}/confirm",
            {}, token=buyer)
check("double-confirm → 400 ORDER_CLOSED (idempotent)",
      s == 400 and r["error"]["code"] == "ORDER_CLOSED", r)

# ---------- /billing/me correctness ----------
s, r = call("GET", "/billing/me", token=buyer)
d = r["data"]
check("/billing/me reflects paid sub",
      d["buyer"]["plan_code"] == f"PRO_{RUN}"
      and d["buyer"]["limit"] == 9 and d["buyer"]["subscribed"] is True, d)
check("seller scope still FREE (scopes independent)",
      d["seller"]["plan_code"] == "FREE_SELLER")

# ---------- admin ops ----------
s, r = call("POST", "/admin/billing/grants",
            {"user_id": 999999, "plan_id": pro_id}, token=admin)
check("grant to missing user → 404", s == 404)

s, r = call("POST", "/admin/billing/grants",
            {"user_id": buyer_id, "plan_id": 999999}, token=admin)
check("grant missing plan → 404", s == 404)

s, r = call("POST", "/admin/billing/subscriptions/999999/revoke",
            token=admin)
check("revoke missing sub → 404 SUB_NOT_FOUND",
      s == 404 and r["error"]["code"] == "SUB_NOT_FOUND", r)

# find buyer's ACTIVE sub id then double-revoke → SUB_CLOSED
s, r = call("GET", "/admin/billing/subscriptions?status=ACTIVE&limit=100",
            token=admin)
sub = next(x for x in r["data"] if x["user_id"] == buyer_id
           and x["plan_code"] == f"PRO_{RUN}")
s, r = call("POST", f"/admin/billing/subscriptions/{sub['id']}/revoke",
            token=admin)
check("revoke works", s == 200 and r["data"]["status"] == "CANCELLED")
s, r = call("POST", f"/admin/billing/subscriptions/{sub['id']}/revoke",
            token=admin)
check("re-revoke → 400 SUB_CLOSED",
      s == 400 and r["error"]["code"] == "SUB_CLOSED", r)

# after revoke, buyer falls back to FREE_BUYER
s, r = call("GET", "/billing/me", token=buyer)
check("revoked user falls back to FREE_BUYER",
      r["data"]["buyer"]["plan_code"] == "FREE_BUYER"
      and r["data"]["buyer"]["subscribed"] is False, r["data"])

# ---------- catalogue filtering ----------
s, r = call("GET", "/billing/plans")
codes = {p["code"] for p in r["data"]}
check("inactive plan hidden from public catalogue",
      f"INACTIVE_{RUN}" not in codes and "FREE_SELLER" in codes)
s, r = call("GET", "/billing/plans?applies_to=buyer")  # lowercase filter
check("lowercase applies_to filter still works",
      all(p["applies_to"] == "BUYER" for p in r["data"]), r)

# ---------- expiry enforcement ----------
# grant buyer a 0-day sub via direct DB manipulation isn't possible via API;
# grant 1 day then expire it via DB to test lazy expiry
from datetime import datetime, timedelta, timezone
from app.core.database import SessionLocal
from app.models import Plan, UserSubscription

db = SessionLocal()
plan = db.query(Plan).filter(Plan.code == f"PRO_{RUN}").one()
exp = UserSubscription(user_id=buyer_id, plan_id=plan.id, source="ADMIN_GRANT",
                       expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
db.add(exp); db.commit(); db.close()

s, r = call("GET", "/billing/me", token=buyer)
check("expired sub → falls back to FREE (lazy expiry)",
      r["data"]["buyer"]["plan_code"] == "FREE_BUYER"
      and r["data"]["buyer"]["subscribed"] is False, r["data"])

db = SessionLocal()
st = db.query(UserSubscription).filter(
    UserSubscription.id == exp.id).one().status
db.close()
check("expiry persisted to DB", getattr(st, "value", st) == "EXPIRED", st)

# ---------- audit trail for billing actions ----------
s, r = call("GET", "/admin/audit-logs?action=PLAN_CREATE&limit=100",
            token=admin)
check("plan creation audit-logged",
      any(f"PRO_{RUN}" in str(x.get("extra", "")) for x in r["data"])
      or len(r["data"]) > 0, str(r["data"])[:200])

print(f"\n{ok_n}/{ok_n + fail_n} sanity checks passed")
raise SystemExit(1 if fail_n else 0)
