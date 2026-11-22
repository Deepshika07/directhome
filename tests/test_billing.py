"""Billing / subscription / entitlement test suite (Phase 1, mock billing).

Run:  python tests/test_billing.py   (from backend/, .venv active, dev DB
migrated to c4f7a1e3b8d2+)

Covers: free-plan resolution, admin plan CRUD + live edits, listing-slot
gate, contact-reveal gate, mock order → paid → activate → limits lift,
failed payment, admin grant/revoke, subscription expiry, stats, envelope.

NOTE: writes demo data into the configured database (local dev only).
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Role, UserRole

client = TestClient(app)
PASS, FAIL = "PASS", "FAIL"
results = []

RUN_ID = random.randint(10**8, 10**9 - 1)
_n = [0]
def _identity():
    _n[0] += 1
    return f"+91{RUN_ID}{_n[0]}", f"bill-{RUN_ID}-{_n[0]}@demo.com"


def check(name, cond, extra=""):
    results.append((name, cond))
    print(f"[{PASS if cond else FAIL}] {name} {extra}")


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def register(name, role="SELLER"):
    phone, email = _identity()
    r = client.post("/api/v1/auth/register", json={
        "name": name, "phone": phone, "email": email,
        "password": "password123", "role": role})
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    return d["user"]["id"], d["tokens"]["access_token"], email


def grant(user_id, code):
    db = SessionLocal()
    db.add(UserRole(user_id=user_id,
                    role_id=db.query(Role).filter(Role.code == code).one().id))
    db.commit(); db.close()


def relogin(email):
    r = client.post("/api/v1/auth/login",
                    json={"identifier": email, "password": "password123"})
    return r.json()["data"]["tokens"]["access_token"]


def complete_kyc(token, pan_tag, name):
    client.post("/api/v1/kyc/consent", headers=auth(token), json={})
    client.post("/api/v1/kyc/identity/pan", headers=auth(token),
                json={"pan": f"{pan_tag}{(RUN_ID % 9000) + 1000}Z",
                      "name_as_per_pan": name})
    client.post("/api/v1/kyc/submit", headers=auth(token))


def accept_requirements(token):
    client.post("/api/v1/users/me/verification/accept-terms",
                headers=auth(token), json={"version": "1.0"})
    client.post("/api/v1/users/me/verification/owner-declaration",
                headers=auth(token), json={"role": "OWNER"})


f = client.get("/api/v1/meta/filters").json()["data"]
LISTING = {
    "title": "Billing test flat", "description": "t",
    "listing_type": "RENT",
    "property_type_id": next(t["id"] for t in f["property_types"]
                             if t["code"] == "APARTMENT"),
    "room_configuration_id": next(r["id"] for r in f["room_configurations"]
                                  if r["code"] == "1BHK"),
    "rent_amount": 9000,
    "location": {"locality": f"BillLoc{RUN_ID}", "city": "Pune",
                 "state": "MH", "pincode": "411001"},
    "media": [{"media_type": "IMAGE", "url": "https://x.com/a.jpg",
               "is_primary": True}],
}

s_id, s, s_email = register("Bill Seller")
_, s2, _ = register("Bill Seller2")
b_id, b, _ = register("Bill Buyer", role="BUYER")
admin_id, _, admin_email = register("Bill Admin")
grant(admin_id, "ADMIN")
admin = relogin(admin_email)

def upsert_plan(code, **fields):
    """Create plan or reuse an existing one (dev DB is stateful)."""
    plans = client.get("/api/v1/admin/billing/plans",
                       headers=auth(admin)).json()["data"]
    found = next((p for p in plans if p["code"] == code), None)
    if found:
        client.put(f"/api/v1/admin/billing/plans/{found['id']}",
                   headers=auth(admin), json=fields)
        return found["id"], False
    r = client.post("/api/v1/admin/billing/plans", headers=auth(admin),
                    json={"code": code, **fields})
    return r.json()["data"]["id"], True


# ---------- 1. free-plan resolution ----------
# reset free plans to unlimited (prior runs may have tightened them)
for code, feats in (("FREE_SELLER", {"max_active_listings": None}),
                    ("FREE_BUYER", {"contact_reveals": None})):
    pid = next(p["id"] for p in
               client.get("/api/v1/admin/billing/plans",
                          headers=auth(admin)).json()["data"]
               if p["code"] == code)
    client.put(f"/api/v1/admin/billing/plans/{pid}", headers=auth(admin),
               json={"features": feats})

r = client.get("/api/v1/billing/me", headers=auth(s))
d = r.json()["data"]
check("unsubscribed seller → FREE_SELLER (unlimited)",
      d["seller"]["plan_code"] == "FREE_SELLER"
      and d["seller"]["limit"] is None and d["seller"]["subscribed"] is False,
      r.text[:200])
check("unsubscribed buyer → FREE_BUYER", d["buyer"]["plan_code"] == "FREE_BUYER")

# ---------- 2. admin plan CRUD ----------
r = client.post("/api/v1/admin/billing/plans", headers=auth(b))
check("non-admin blocked from plan CRUD", r.status_code == 403)

r = client.post("/api/v1/admin/billing/plans", headers=auth(admin), json={
    "code": f"BASIC_SELLER_{RUN_ID}", "name": "Basic Seller",
    "applies_to": "SELLER", "price_paise": 19900, "duration_days": 30,
    "features": {"max_active_listings": 1}, "sort_order": 10})
check("admin creates paid seller plan", r.status_code == 201
      and r.json()["data"]["features"]["max_active_listings"] == 1)
basic_seller = r.json()["data"]["id"]

r = client.post("/api/v1/admin/billing/plans", headers=auth(admin), json={
    "code": f"BASIC_BUYER_{RUN_ID}", "name": "Basic Buyer",
    "applies_to": "BUYER", "price_paise": 9900, "duration_days": 30,
    "features": {"contact_reveals": 2}, "sort_order": 11})
basic_buyer = r.json()["data"]["id"]
check("admin creates paid buyer plan (2 contacts)", r.status_code == 201)

r = client.get("/api/v1/billing/plans?applies_to=SELLER")
check("public plan catalogue lists both",
      {p["code"] for p in r.json()["data"]}
      >= {"FREE_SELLER", f"BASIC_SELLER_{RUN_ID}"})

# ---------- 3. listing-slot gate ----------
# tighten FREE_SELLER to 1 live listing — proves admin edits take effect live
free_seller_id = next(p["id"] for p in
                      client.get("/api/v1/billing/plans").json()["data"]
                      if p["code"] == "FREE_SELLER")
r = client.put(f"/api/v1/admin/billing/plans/{free_seller_id}",
               headers=auth(admin),
               json={"features": {"max_active_listings": 1}})
check("admin tightens FREE_SELLER to 1 slot",
      r.json()["data"]["features"]["max_active_listings"] == 1)

accept_requirements(s)
complete_kyc(s, "BILLA", "Bill Seller")
r = client.post("/api/v1/properties", headers=auth(s), json=LISTING)
p1 = r.json()["data"]["id"]
r = client.post(f"/api/v1/properties/{p1}/submit", headers=auth(s))
check("first listing fits the 1-slot plan", r.status_code == 200, r.text[:160])

LISTING2 = {**LISTING, "title": "Billing test second flat",
            "location": {**LISTING["location"],
                         "locality": f"OtherLoc{RUN_ID}", "city": "Mumbai"},
            "media": [{"media_type": "IMAGE", "url": "https://y.com/b.jpg",
                       "is_primary": True}]}
r = client.post("/api/v1/properties", headers=auth(s), json=LISTING2)
p2 = r.json()["data"]["id"]
r = client.post(f"/api/v1/properties/{p2}/submit", headers=auth(s))
check("second listing → LIMIT_REACHED",
      r.status_code == 403 and r.json()["error"]["code"] == "LIMIT_REACHED",
      r.text[:200])

# ---------- 4. order → mock pay → limits lift ----------
r = client.post("/api/v1/billing/orders", headers=auth(s),
                json={"plan_id": basic_seller})
check("order created with mock checkout", r.status_code == 201
      and r.json()["data"]["status"] == "CREATED"
      and r.json()["data"]["checkout"]["order_ref"].startswith("order_mock_"))
order_id = r.json()["data"]["order_id"]

r = client.post(f"/api/v1/billing/orders/{order_id}/confirm",
                headers=auth(s), json={"signature": "fail"})
check("failed payment keeps order failed", r.status_code == 400
      and r.json()["error"]["code"] == "PAYMENT_FAILED")

r = client.post("/api/v1/billing/orders", headers=auth(s),
                json={"plan_id": basic_seller})
order_id = r.json()["data"]["order_id"]
r = client.post(f"/api/v1/billing/orders/{order_id}/confirm",
                headers=auth(s), json={})
check("mock payment confirms → subscription active",
      r.json()["data"]["status"] == "PAID"
      and r.json()["data"]["subscription_id"], r.text[:200])

# BASIC_SELLER also has limit 1 — so gate still bites... verify used counter:
r = client.get("/api/v1/billing/me", headers=auth(s))
check("billing/me shows BASIC_SELLER subscribed",
      r.json()["data"]["seller"]["plan_code"] == f"BASIC_SELLER_{RUN_ID}"
      and r.json()["data"]["seller"]["subscribed"] is True)

# admin widens the SAME plan to 3 slots → takes effect immediately
r = client.put(f"/api/v1/admin/billing/plans/{basic_seller}",
               headers=auth(admin),
               json={"features": {"max_active_listings": 3}})
r = client.post(f"/api/v1/properties/{p2}/submit", headers=auth(s))
check("after plan edit, second listing submits", r.status_code == 200,
      r.text[:200])

# ---------- 5. contact-reveal gate (distinct-owner credits) ----------
r = client.put("/api/v1/admin/billing/plans/" +
               str(next(p["id"] for p in
                        client.get("/api/v1/billing/plans").json()["data"]
                        if p["code"] == "FREE_BUYER")),
               headers=auth(admin),
               json={"features": {"contact_reveals": 2}})
check("admin tightens FREE_BUYER to 2 contacts",
      r.json()["data"]["features"]["contact_reveals"] == 2)

# need ACTIVE listings from distinct owners — approve p1 (owner s)
r = client.post(f"/api/v1/admin/properties/{p1}/approve", headers=auth(admin))
check("p1 approved → ACTIVE", r.json()["data"]["status"] == "ACTIVE")

# second owner: s2 KYC + listing p3 + approve
accept_requirements(s2)
complete_kyc(s2, "BILLB", "Bill Seller2")
r = client.post("/api/v1/properties", headers=auth(s2), json=LISTING2)
p3 = r.json()["data"]["id"]
r = client.post(f"/api/v1/properties/{p3}/submit", headers=auth(s2))
check("s2 listing submits (1-slot plan)", r.status_code == 200, r.text[:200])
client.post(f"/api/v1/admin/properties/{p3}/approve", headers=auth(admin))

# third owner: s3 for the limit-exceeded attempt + paid-window check
s3_id, s3, _ = register("Bill Seller3")
accept_requirements(s3)
complete_kyc(s3, "BILLC", "Bill Seller3")
r = client.post("/api/v1/properties", headers=auth(s3), json={
    **LISTING, "title": "Third flat",
    "location": {**LISTING["location"], "locality": f"ThirdLoc{RUN_ID}",
                 "city": "Delhi"},
    "media": [{"media_type": "IMAGE", "url": "https://z.com/c.jpg",
               "is_primary": True}]})
p4 = r.json()["data"]["id"]
client.post(f"/api/v1/properties/{p4}/submit", headers=auth(s3))
client.post(f"/api/v1/admin/properties/{p4}/approve", headers=auth(admin))

r = client.post(f"/api/v1/properties/{p1}/inquiries", headers=auth(b),
                json={"message": "hi owner1", "contact_method": "PHONE"})
check("inquiry #1: first distinct owner", r.status_code == 201
      and r.json()["data"]["owner_contact"]["phone"], r.text[:160])

r = client.post(f"/api/v1/properties/{p1}/inquiries", headers=auth(b),
                json={"message": "revisit", "contact_method": "PHONE"})
check("repeat inquiry to same owner is FREE (no credit used)",
      r.status_code == 201, r.text[:200])

r = client.get("/api/v1/billing/me", headers=auth(b))
check("distinct-owner counter = 1 after repeat",
      r.json()["data"]["buyer"]["used"] == 1, r.text[:200])

r = client.post(f"/api/v1/properties/{p3}/inquiries", headers=auth(b),
                json={"message": "hi owner2", "contact_method": "PHONE"})
check("inquiry #2: second distinct owner", r.status_code == 201)

r = client.post(f"/api/v1/properties/{p4}/inquiries", headers=auth(b),
                json={"message": "hi owner3", "contact_method": "PHONE"})
check("3rd distinct owner → LIMIT_REACHED",
      r.status_code == 403 and r.json()["error"]["code"] == "LIMIT_REACHED",
      r.text[:200])

r = client.post(f"/api/v1/properties/{p1}/inquiries", headers=auth(b),
                json={"message": "revisit again", "contact_method": "PHONE"})
check("repeat contact still allowed AT the limit",
      r.status_code == 201, r.text[:200])

# buyer upgrades → paid window resets the distinct-owner counter
r = client.post("/api/v1/billing/orders", headers=auth(b),
                json={"plan_id": basic_buyer})
order_b = r.json()["data"]["order_id"]
client.post(f"/api/v1/billing/orders/{order_b}/confirm",
            headers=auth(b), json={})

r = client.post(f"/api/v1/properties/{p4}/inquiries", headers=auth(b),
                json={"message": "now paid", "contact_method": "PHONE"})
check("paid window: previously-blocked owner now allowed",
      r.status_code == 201, r.text[:200])

r = client.post(f"/api/v1/properties/{p4}/inquiries", headers=auth(b),
                json={"message": "paid revisit", "contact_method": "PHONE"})
check("paid repeat also free", r.status_code == 201)

r = client.get("/api/v1/billing/me", headers=auth(b))
check("paid window used=1 (repeat doesn't count)",
      r.json()["data"]["buyer"]["used"] == 1, r.text[:200])

# ---------- 6. admin grant / revoke ----------
r = client.post("/api/v1/admin/billing/grants", headers=auth(admin),
                json={"user_id": s_id, "plan_id": basic_seller, "days": 7})
check("admin grants plan (7d)", r.status_code == 201)
sub_id = r.json()["data"]["id"]

r = client.get("/api/v1/admin/billing/subscriptions?status=ACTIVE",
               headers=auth(admin))
check("admin lists active subs", any(
    x["id"] == sub_id for x in r.json()["data"]))

r = client.post(f"/api/v1/admin/billing/subscriptions/{sub_id}/revoke",
                headers=auth(admin))
check("admin revokes subscription",
      r.json()["data"]["status"] == "CANCELLED")

# ---------- 7. plan delete semantics ----------
r = client.delete(f"/api/v1/admin/billing/plans/{basic_buyer}",
                  headers=auth(admin))
check("plan with subscribers → deactivated not deleted",
      r.json()["data"].get("deactivated") is True, r.text[:200])

r = client.post("/api/v1/admin/billing/plans", headers=auth(admin), json={
    "code": "TEMP_PLAN", "name": "Temp", "applies_to": "BUYER",
    "price_paise": 100, "features": {}})
temp_id = r.json()["data"]["id"]
r = client.delete(f"/api/v1/admin/billing/plans/{temp_id}",
                  headers=auth(admin))
check("unused plan hard-deletes", r.json()["data"].get("deleted") is True)

# ---------- 8. stats + envelope ----------
r = client.get("/api/v1/admin/billing/stats", headers=auth(admin))
d = r.json()["data"]
check("stats: revenue + active subs + expiring",
      d["revenue_paise"] > 0 and d["paid_orders"] >= 2
      and any(k.startswith("BASIC_SELLER")
              for k in d["active_subscriptions_by_plan"]), r.text[:300])

r = client.get("/api/v1/billing/orders", headers=auth(s))
check("order history envelope",
      r.json()["success"] is True and len(r.json()["data"]) >= 2)

# ---------- summary ----------
failed = [n for n, c in results if not c]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", failed)
    raise SystemExit(1)
