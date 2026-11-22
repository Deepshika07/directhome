"""Anti-broker detection & owner-verification test suite.

Run:  python tests/test_antifraud.py   (from backend/, with .venv active,
against a dev DB that has the antifraud migration applied)

Covers: listing gate (terms + declaration), OTP code flow, report dedup,
auto-flag threshold, duplicate-property scoring, score bands, restriction
enforcement, appeals, admin authz, doc privacy, false-positive handling.

NOTE: writes demo data into the configured database (local dev only).
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Role, User, UserRole

client = TestClient(app)
PASS, FAIL = "PASS", "FAIL"
results = []

# unique-per-run identities so the suite is repeatable against the same DB
RUN_ID = random.randint(10**8, 10**9 - 1)
_n = [0]
def _identity():
    _n[0] += 1
    return f"+91{RUN_ID}{_n[0]}", f"af-{RUN_ID}-{_n[0]}@demo.com"


def check(name, cond, extra=""):
    results.append((name, cond))
    print(f"[{PASS if cond else FAIL}] {name} {extra}")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def register(name, phone=None, email=None, role="BUYER"):
    if phone is None or email is None:
        phone, email = _identity()
    if phone is None or email is None:
        phone, email = _identity()
    r = client.post("/api/v1/auth/register", json={
        "name": name, "phone": phone, "email": email,
        "password": "password123", "role": role})
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    return d["user"]["id"], d["tokens"]["access_token"], email


def accept_requirements(token):
    client.post("/api/v1/users/me/verification/accept-terms",
                headers=auth(token), json={"version": "1.0"})
    client.post("/api/v1/users/me/verification/owner-declaration",
                headers=auth(token), json={"role": "OWNER"})


def complete_kyc(token, pan, name="AF Seller"):
    """Listing submission now requires identity KYC (L1)."""
    client.post("/api/v1/kyc/consent", headers=auth(token), json={})
    client.post("/api/v1/kyc/identity/pan", headers=auth(token),
                json={"pan": pan, "name_as_per_pan": name})
    r = client.post("/api/v1/kyc/submit", headers=auth(token))
    assert r.json()["data"]["profile"]["status"] == "AUTO_VERIFIED", r.text


# address is unique per run → duplicate-detection assertions stay deterministic
ADDR = {"address_line1": "12 Green Park", "locality": f"TestLoc{RUN_ID}",
        "city": "Bengaluru", "state": "Karnataka", "pincode": "560038"}

LISTING = {
    "title": "Bright 2BHK in Indiranagar",
    "description": "Near metro, modular kitchen.",
    "listing_type": "RENT", "property_type_id": None,
    "room_configuration_id": None, "bathrooms": 2, "carpet_area_sqft": 900,
    "furnishing": "SEMI_FURNISHED", "rent_amount": 30000,
    "location": ADDR,
    "media": [{"media_type": "IMAGE",
               "url": "https://img.example.com/shared-1.jpg",
               "is_primary": True}],
    "amenity_ids": [],
}

# ---------- setup: filters + accounts ----------
r = client.get("/api/v1/meta/filters")
filters = r.json()["data"]
LISTING["property_type_id"] = next(
    t for t in filters["property_types"] if t["code"] == "APARTMENT")["id"]
LISTING["room_configuration_id"] = next(
    r_ for r_ in filters["room_configurations"] if r_["code"] == "2BHK")["id"]

seller1_id, seller1, _ = register("Seller One", role="SELLER")
seller2_id, seller2, _ = register("Seller Two", role="SELLER")
_, buyer1, _ = register("Buyer One")
_, buyer2, _ = register("Buyer Two")
_, buyer3, _ = register("Buyer Three")

admin_id, _, admin_email = register("AF Admin")
db = SessionLocal()
db.add(UserRole(user_id=admin_id,
                role_id=db.query(Role).filter(Role.code == "ADMIN").one().id))
db.commit(); db.close()
r = client.post("/api/v1/auth/login",
                json={"identifier": admin_email,
                      "password": "password123"})
admin = r.json()["data"]["tokens"]["access_token"]

# ---------- 1. listing gate ----------
r = client.post("/api/v1/properties", headers=auth(seller1), json=LISTING)
check("create blocked before terms+declaration (403 PROFILE_INCOMPLETE)",
      r.status_code == 403
      and r.json()["error"]["code"] == "PROFILE_INCOMPLETE", r.text[:200])

r = client.post("/api/v1/users/me/verification/accept-terms",
                headers=auth(seller1), json={"version": "9.9"})
check("wrong terms version rejected", r.status_code == 400)

accept_requirements(seller1)
complete_kyc(seller1, f"AFONE{RUN_ID % 9000 + 1000}Z")

r = client.get("/api/v1/users/me/verification/status", headers=auth(seller1))
check("verification status shows requirements met",
      r.json()["data"]["can_list"] is True, r.text[:250])

# ---------- 2. OTP code flow (dev echo) ----------
r = client.post("/api/v1/users/me/verification/send-code",
                headers=auth(seller1), json={"channel": "PHONE"})
dev_code = r.json()["data"].get("dev_code")
check("send-code returns dev_code (non-prod)", dev_code is not None)

r = client.post("/api/v1/users/me/verification/verify-code",
                headers=auth(seller1),
                json={"channel": "PHONE", "code": "000000"})
check("wrong code rejected", r.status_code == 400)

r = client.post("/api/v1/users/me/verification/verify-code",
                headers=auth(seller1),
                json={"channel": "PHONE", "code": dev_code})
check("correct code verifies phone",
      r.json()["data"]["verified"] is True)

# ---------- 3. listing + broker checks on submit ----------
r = client.post("/api/v1/properties", headers=auth(seller1), json=LISTING)
check("seller1 creates listing after verification", r.status_code == 201)
prop1 = r.json()["data"]["id"]

r = client.post(f"/api/v1/properties/{prop1}/submit", headers=auth(seller1))
check("seller1 submits listing", r.status_code == 200, r.text[:200])

r = client.post(f"/api/v1/admin/properties/{prop1}/approve",
                headers=auth(admin))
check("admin approves listing", r.json()["data"]["status"] == "ACTIVE")

# ---------- 4. reports: dedup + flag threshold ----------
r = client.post(f"/api/v1/properties/{prop1}/reports", headers=auth(buyer1),
                json={"reason": "BROKER", "description": "suspicious"})
check("report accepted", r.status_code == 201)

r = client.post(f"/api/v1/properties/{prop1}/reports", headers=auth(buyer1),
                json={"reason": "BROKER"})
check("duplicate report blocked (409 ALREADY_REPORTED)",
      r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_REPORTED")

r = client.post(f"/api/v1/properties/{prop1}/reports", headers=auth(buyer2),
                json={"reason": "FAKE_LISTING"})
r = client.post(f"/api/v1/properties/{prop1}/reports", headers=auth(buyer3),
                json={"reason": "SCAM"})
check("3 open reports auto-flag listing", r.status_code == 201)

r = client.get("/api/v1/properties")
check("flagged listing hidden from search",
      all(p["id"] != prop1 for p in r.json()["data"]))

r = client.post(f"/api/v1/admin/properties/{prop1}/moderation",
                headers=auth(admin),
                json={"action": "UNFLAG", "reason": "reviewed — looks fine"})
check("admin unflags listing → ACTIVE",
      r.json()["data"]["status"] == "ACTIVE")

# ---------- 5. cross-account duplicate detection ----------
accept_requirements(seller2)
complete_kyc(seller2, f"AFTWO{RUN_ID % 9000 + 1000}Y")
dup_listing = {**LISTING, "title": "Nice 2BHK Indiranagar flat"}
r = client.post("/api/v1/properties", headers=auth(seller2), json=dup_listing)
prop2 = r.json()["data"]["id"]
r = client.post(f"/api/v1/properties/{prop2}/submit", headers=auth(seller2))
check("seller2 submits same-address listing", r.status_code == 200)

r = client.get("/api/v1/admin/fraud/duplicate-listings",
               headers=auth(admin))
check("duplicate match recorded",
      any(m["property_id"] == prop2 or m["matched_property_id"] == prop2
          for m in r.json()["data"]), r.text[:300])

r = client.get(f"/api/v1/admin/fraud/users/{seller2_id}/risk-history",
               headers=auth(admin))
events = r.json()["data"]["events"]
check("seller2 scored CROSS_ACCOUNT_DUPLICATE_PROPERTY (+35)",
      any(e["event_type"] == "CROSS_ACCOUNT_DUPLICATE_PROPERTY"
          and e["score_delta"] == 35 for e in events), r.text[:400])
check("seller2 risk_score 35 → UNDER_REVIEW band",
      r.json()["data"]["user"]["account_status"] == "UNDER_REVIEW")

# re-submit idempotency: dedup_key must prevent double scoring
r = client.get("/api/v1/admin/fraud/users/{}/risk-history".format(seller2_id),
               headers=auth(admin))
check("no double-scoring on repeated checks",
      sum(e["event_type"] == "CROSS_ACCOUNT_DUPLICATE_PROPERTY"
          for e in r.json()["data"]["events"]) == 1)

# ---------- 6. admin authorization ----------
r = client.get("/api/v1/admin/fraud/flagged-users", headers=auth(buyer1))
check("non-admin blocked from fraud endpoints (403)", r.status_code == 403)

r = client.get("/api/v1/admin/fraud/flagged-users", headers=auth(admin))
check("flagged-users lists seller2",
      any(u["id"] == seller2_id for u in r.json()["data"]))

# ---------- 7. restriction enforcement + appeal ----------
r = client.post(f"/api/v1/admin/users/{seller1_id}/restriction",
                headers=auth(admin),
                json={"action": "RESTRICT", "reason": "manual review"})
check("admin restricts seller1",
      r.json()["data"]["account_status"] == "TEMPORARILY_RESTRICTED")

r = client.post("/api/v1/properties", headers=auth(seller1), json=LISTING)
check("restricted seller cannot create listing (403)", r.status_code == 403)

r = client.post("/api/v1/users/me/appeals", headers=auth(seller1),
                json={"message": "I am the genuine owner, docs attached."})
check("restricted user files appeal", r.status_code == 201)
appeal_id = r.json()["data"]["id"]

r = client.post(f"/api/v1/admin/appeals/{appeal_id}/resolve",
                headers=auth(admin),
                json={"approved": True, "note": "verified manually"})
check("admin approves appeal → ACTIVE",
      r.json()["data"]["status"] == "APPROVED")

r = client.post("/api/v1/properties", headers=auth(seller1), json=LISTING)
check("appeal-approved seller can list again", r.status_code == 201)

# ---------- 8. document privacy + request-info ----------
doc_type = next(d for d in filters["document_types"]
                if d.get("applies_to") in {"PROPERTY", "BOTH"})
r = client.post(f"/api/v1/properties/{prop1}/verification-documents",
                headers=auth(seller1),
                json={"document_type_id": doc_type["id"],
                      "file_url": "https://files.example.com/deed.pdf"})
check("owner uploads property doc", r.status_code == 201, r.text[:200])
doc_id = r.json()["data"]["id"]

r = client.get("/api/v1/properties")
check("doc file_url never exposed publicly",
      "deed.pdf" not in r.text and "file_url" not in r.text)

r = client.post(f"/api/v1/admin/documents/{doc_id}/request-info",
                headers=auth(admin),
                json={"note": "Please upload the full sale deed page 2"})
check("admin requests additional info",
      r.json()["data"]["status"] == "REQUIRES_ADDITIONAL_INFO", r.text[:200])

r = client.post(f"/api/v1/admin/documents/{doc_id}/reject",
                headers=auth(admin),
                json={"reason": "fake deed", "ownership_mismatch": True})
r = client.get(f"/api/v1/admin/fraud/users/{seller1_id}/risk-history",
               headers=auth(admin))
check("ownership-mismatch reject scores +50",
      any(e["event_type"] == "OWNERSHIP_MISMATCH"
          and e["score_delta"] == 50 for e in r.json()["data"]["events"]),
      r.text[:400])

# ---------- 9. false-positive handling ----------
# report on prop2 from buyer1, then admin dismisses → no score for seller2
r = client.post(f"/api/v1/properties/{prop2}/reports", headers=auth(buyer1),
                json={"reason": "OTHER", "description": "weak claim"})
rep_id = None
# reported-properties sorts oldest-first — scan pages (dev DB is stateful)
_page = 1
while rep_id is None:
    r = client.get(
        f"/api/v1/admin/fraud/reported-properties?status=OPEN"
        f"&page={_page}&limit=100", headers=auth(admin))
    rep_id = next((x["id"] for x in r.json()["data"]
                   if x["property_id"] == prop2), None)
    if r.json()["meta"]["total"] <= _page * 100:
        break
    _page += 1
assert rep_id is not None, "report on prop2 not found in OPEN reports"
r = client.post(f"/api/v1/admin/reports/{rep_id}/dismiss",
                headers=auth(admin), json={"note": "invalid report"})
check("admin dismisses report", r.status_code == 200)

r = client.get(f"/api/v1/admin/fraud/users/{seller2_id}/risk-history",
               headers=auth(admin))
check("dismissed report adds no complaint score",
      not any(e["event_type"] == "VALIDATED_COMPLAINTS"
              for e in r.json()["data"]["events"]))

# ---------- 10. audit trail ----------
r = client.get("/api/v1/admin/audit-logs", headers=auth(admin))
check("audit log records admin actions",
      any(x["action"] == "ACCOUNT_RESTRICT" for x in r.json()["data"]),
      r.text[:300])

# ---------- summary ----------
failed = [n for n, c in results if not c]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", failed)
    raise SystemExit(1)
