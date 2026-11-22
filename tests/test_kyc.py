"""KYC identity + ownership-verification test suite (Phase 1).

Run:  python tests/test_kyc.py   (from backend/, .venv active, dev DB
migrated to b1f4e2c9d8a7+)

Covers: consent gate, PAN flow + encryption-at-rest, status transitions,
L1 listing gate, duplicate-PAN risk event, ownership claim + reviewer
decision, maker-checker (POA → admin finalize), RBAC, doc-access logging,
response envelope.

NOTE: writes demo data into the configured database (local dev only).
"""
import io
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.core.crypto import decrypt, hmac_id
from app.core.database import SessionLocal
from app.main import app
from app.models import KycProfile, Role, User, UserRole

client = TestClient(app)
PASS, FAIL = "PASS", "FAIL"
results = []

RUN_ID = random.randint(10**8, 10**9 - 1)
_n = [0]
def _identity():
    _n[0] += 1
    return f"+91{RUN_ID}{_n[0]}", f"kyc-{RUN_ID}-{_n[0]}@demo.com"


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


def pan(tag):
    return f"{tag}{(RUN_ID % 9000) + 1000}Z"[:10].ljust(10, "X")


PDF = b"%PDF-1.4 fake doc bytes " + bytes(RUN_ID % 997)
JPEG = b"\xff\xd8\xff\xe0" + bytes(50)


def accept_requirements(token):
    client.post("/api/v1/users/me/verification/accept-terms",
                headers=auth(token), json={"version": "1.0"})
    client.post("/api/v1/users/me/verification/owner-declaration",
                headers=auth(token), json={"role": "OWNER"})


r = client.get("/api/v1/meta/filters")
FILTERS = r.json()["data"]
LISTING = {
    "title": "KYC test flat", "description": "test",
    "listing_type": "RENT",
    "property_type_id": next(t["id"] for t in FILTERS["property_types"]
                             if t["code"] == "APARTMENT"),
    "room_configuration_id": next(r_["id"] for r_ in
                                  FILTERS["room_configurations"]
                                  if r_["code"] == "2BHK"),
    "bathrooms": 1, "carpet_area_sqft": 700,
    "furnishing": "UNFURNISHED", "rent_amount": 15000,
    "location": {"address_line1": "1 KYC Lane",
                 "locality": f"KycLoc{RUN_ID}", "city": "Pune",
                 "state": "Maharashtra", "pincode": "411001"},
    "media": [{"media_type": "IMAGE",
               "url": "https://img.example.com/k.jpg", "is_primary": True}],
    "amenity_ids": [],
}

s1_id, s1, s1_email = register("Kyc Seller One")
s2_id, s2, _ = register("Kyc Seller Two")
s3_id, s3, s3_email = register("Kyc Seller Three")
rev_id, rev, rev_email = register("Kyc Reviewer")
_, buyer, _ = register("Kyc Buyer", role="BUYER")
admin_id, _, admin_email = register("Kyc Admin")
grant(rev_id, "KYC_REVIEWER")
grant(admin_id, "ADMIN")
rev, admin = relogin(rev_email), relogin(admin_email)

# ---------- 1. consent & PAN ----------
r = client.post("/api/v1/kyc/identity/pan", headers=auth(s1),
                json={"pan": pan("ABCDE"), "name_as_per_pan": "Kyc Seller One"})
check("PAN blocked before consent", r.status_code == 400
      and r.json()["error"]["code"] == "CONSENT_REQUIRED", r.text[:160])

r = client.post("/api/v1/kyc/consent", headers=auth(s1), json={})
check("consent recorded", r.status_code == 200
      and r.json()["success"] is True
      and "data" in r.json(), r.text[:160])

r = client.post("/api/v1/kyc/identity/pan", headers=auth(s1),
                json={"pan": "123456789A", "name_as_per_pan": "Kyc Seller One"})
check("bad PAN format rejected", r.status_code == 400
      and r.json()["error"]["code"] == "PAN_FORMAT")

r = client.post("/api/v1/kyc/identity/pan", headers=auth(s1),
                json={"pan": pan("ABCDE"), "name_as_per_pan": "Kyc Seller One"})
check("PAN verified via mock provider", r.status_code == 200, r.text[:200])

# encrypted at rest: raw PAN must never appear in the row
db = SessionLocal()
prof = db.query(KycProfile).filter(KycProfile.user_id == s1_id).one()
check("PAN encrypted at rest (no plaintext, hash present)",
      pan("ABCDE") not in (prof.pan_encrypted or "")
      and prof.pan_hash == hmac_id(pan("ABCDE"))
      and decrypt(prof.pan_encrypted) == pan("ABCDE"))
db.close()

# ---------- 2. submit → AUTO_VERIFIED → L1 ----------
r = client.post("/api/v1/kyc/submit", headers=auth(s1))
d = r.json()["data"]
check("clean KYC auto-verifies → level 1",
      d["profile"]["status"] == "AUTO_VERIFIED"
      and d["kyc_level"] == 1 and d["kyc_status"] == "VERIFIED", r.text[:200])
check("PAN masked in response", d["profile"]["pan_masked"] == "ABCDE****Z",
      d["profile"]["pan_masked"])

# ---------- 3. L1 gate on listing submit ----------
accept_requirements(s2)
r = client.post("/api/v1/properties", headers=auth(s2), json=LISTING)
prop2 = r.json()["data"]["id"]
r = client.post(f"/api/v1/properties/{prop2}/submit", headers=auth(s2))
check("submit blocked without KYC (403 KYC_REQUIRED)",
      r.status_code == 403 and r.json()["error"]["code"] == "KYC_REQUIRED",
      r.text[:160])

accept_requirements(s1)
r = client.post("/api/v1/properties", headers=auth(s1), json=LISTING)
prop1 = r.json()["data"]["id"]
r = client.post(f"/api/v1/properties/{prop1}/submit", headers=auth(s1))
check("L1 seller submits listing", r.status_code == 200, r.text[:160])

# ---------- 4. duplicate PAN detection ----------
client.post("/api/v1/kyc/consent", headers=auth(s2), json={})
r = client.post("/api/v1/kyc/identity/pan", headers=auth(s2),
                json={"pan": pan("ABCDE"), "name_as_per_pan": "Impostor"})
check("same PAN on 2nd account accepted but flagged", r.status_code == 200)
r = client.post("/api/v1/kyc/submit", headers=auth(s2))
check("duplicate-PAN account routed to UNDER_REVIEW",
      r.json()["data"]["profile"]["status"] == "UNDER_REVIEW", r.text[:160])

r = client.get(f"/api/v1/admin/fraud/users/{s2_id}/risk-history",
               headers=auth(admin))
check("DUPLICATE_PAN_HASH risk event recorded",
      any(e["event_type"] == "DUPLICATE_PAN_HASH"
          for e in r.json()["data"]["events"]), r.text[:300])

# ---------- 5. invalid transitions ----------
r = client.post("/api/v1/kyc/submit", headers=auth(buyer))
check("submit without PAN → KYC_INCOMPLETE",
      r.status_code == 400 and r.json()["error"]["code"] == "KYC_INCOMPLETE")

# ---------- 6. ownership claim + docs ----------
meta = client.get("/api/v1/kyc/meta").json()["data"]
ct = next(c for c in meta["claim_types"] if c["code"] == "INDIVIDUAL_OWNER")
poa_ct = next(c for c in meta["claim_types"] if c["code"] == "POA_HOLDER")
deed_dt = next(d for d in meta["document_types"] if d["code"] == "SALE_DEED")
tax_dt = next(d for d in meta["document_types"]
              if d["code"] == "PROPERTY_TAX_RECEIPT")

r = client.post(f"/api/v1/properties/{prop1}/ownership-claim",
                headers=auth(s1),
                json={"claim_type_id": ct["id"],
                      "owner_name_on_doc": "Kyc Seller One"})
check("ownership claim created", r.status_code == 200
      and r.json()["data"]["status"] == "SUBMITTED", r.text[:200])
claim1 = r.json()["data"]["id"]
check("name match scored 100", r.json()["data"]["name_match_score"] == 100)

r = client.post(f"/api/v1/properties/{prop1}/ownership-claim/documents",
                headers=auth(s1),
                json={"doc_type_id": deed_dt["id"], "filename": "deed.pdf",
                      "mime": "application/pdf", "size_bytes": len(PDF)})
doc1 = r.json()["data"]["document_id"]
check("claim doc init returns upload url", r.status_code == 200
      and "upload_url" in r.json()["data"])

r = client.post(f"/api/v1/kyc/documents/{doc1}/content",
                headers={**auth(s1), "Content-Type": "application/pdf"},
                content=b"not-a-pdf")
check("MIME sniff rejects wrong bytes", r.status_code == 400
      and r.json()["error"]["code"] == "BAD_FILE_TYPE")

r = client.post(f"/api/v1/kyc/documents/{doc1}/content",
                headers={**auth(s1), "Content-Type": "application/pdf"},
                content=PDF)
check("PDF content accepted + sha256 set",
      r.status_code == 200 and r.json()["data"]["sha256"], r.text[:160])

r = client.post(f"/api/v1/properties/{prop2}/ownership-claim/documents",
                headers=auth(s2),
                json={"doc_type_id": tax_dt["id"], "filename": "same.pdf",
                      "mime": "application/pdf", "size_bytes": len(PDF)})
doc2 = r.json()["data"]["document_id"]
r = client.post(f"/api/v1/kyc/documents/{doc2}/content",
                headers={**auth(s2), "Content-Type": "application/pdf"},
                content=PDF)
check("identical bytes cross-account → DOC_REUSE fail check",
      r.status_code == 200)
r = client.get(f"/api/v1/admin/fraud/users/{s2_id}/risk-history",
               headers=auth(admin))
check("SUSPECTED_TAMPERED_DOC risk event on byte-reuse",
      any(e["event_type"] == "SUSPECTED_TAMPERED_DOC"
          for e in r.json()["data"]["events"]), r.text[:300])

# ---------- 7. reviewer queue + decision ----------
r = client.get("/api/v1/admin/kyc/queue", headers=auth(buyer))
check("buyer blocked from reviewer queue (RBAC)", r.status_code == 403)

r = client.get("/api/v1/admin/kyc/queue", headers=auth(rev))
check("reviewer sees queue with both case types", r.status_code == 200
      and {"KYC_PROFILE", "OWNERSHIP_CLAIM"}
      <= {i["subject_type"] for i in r.json()["data"]}, r.text[:300])

r = client.post(
    f"/api/v1/admin/kyc/cases/OWNERSHIP_CLAIM/{claim1}/decision",
    headers=auth(rev), json={"decision": "APPROVE"})
check("reviewer approves clean claim → APPROVED",
      r.json()["data"]["status"] == "APPROVED", r.text[:200])

# normal admin approval still gates ACTIVE — verified claim is separate
r = client.post(f"/api/v1/admin/properties/{prop1}/approve",
                headers=auth(admin))
check("admin approves listing through existing lifecycle",
      r.json()["data"]["status"] == "ACTIVE", r.text[:200])

r = client.get(f"/api/v1/properties/{prop1}", headers=auth(buyer))
check("listing shows verification_level 2 + verified owner",
      r.json()["data"]["verification_level"] == 2
      and r.json()["data"]["is_owner_verified"] is True, r.text[:300])

# ---------- 8. maker-checker: POA claim needs admin finalize ----------
client.post("/api/v1/kyc/consent", headers=auth(s3), json={})
client.post("/api/v1/kyc/identity/pan", headers=auth(s3),
            json={"pan": pan("POAHL"), "name_as_per_pan": "Kyc Seller Three"})
client.post("/api/v1/kyc/submit", headers=auth(s3))
accept_requirements(s3)
r = client.post("/api/v1/properties", headers=auth(s3), json=LISTING)
prop3 = r.json()["data"]["id"]

r = client.post(f"/api/v1/properties/{prop3}/ownership-claim",
                headers=auth(s3),
                json={"claim_type_id": poa_ct["id"],
                      "owner_name_on_doc": "Real Owner Name"})
claim3 = r.json()["data"]["id"]

r = client.post(
    f"/api/v1/admin/kyc/cases/OWNERSHIP_CLAIM/{claim3}/decision",
    headers=auth(rev), json={"decision": "APPROVE"})
check("reviewer APPROVE on POA → RECOMMEND (maker-checker)",
      r.json()["data"].get("pending") == "ADMIN_FINALIZE", r.text[:200])

r = client.get(f"/api/v1/properties/{prop3}/ownership-claim",
               headers=auth(s3))
check("claim still not approved after reviewer recommend",
      r.json()["data"]["status"] != "APPROVED")

r = client.post(
    f"/api/v1/admin/kyc/cases/OWNERSHIP_CLAIM/{claim3}/decision",
    headers=auth(admin), json={"decision": "APPROVE"})
check("admin finalizes → APPROVED",
      r.json()["data"]["status"] == "APPROVED", r.text[:200])

# ---------- 9. self-review + request-info + rejection ----------
grant(s3_id, "KYC_REVIEWER")
s3r = relogin(s3_email)
r = client.post(
    f"/api/v1/admin/kyc/cases/OWNERSHIP_CLAIM/{claim3}/decision",
    headers=auth(s3r), json={"decision": "REJECT", "reason_id": 1})
check("reviewer blocked from own case", r.status_code == 403)

prof2 = SessionLocal().query(KycProfile).filter(
    KycProfile.user_id == s2_id).one()
r = client.post(f"/api/v1/admin/kyc/cases/KYC_PROFILE/{prof2.id}/request-info",
                headers=auth(rev), json={"notes": "Upload masked Aadhaar"})
check("request-info → NEEDS_MORE_INFO",
      r.json()["data"]["status"] == "NEEDS_MORE_INFO")

r = client.post("/api/v1/kyc/submit", headers=auth(s2))
check("resubmit after request-info → UNDER_REVIEW",
      r.json()["data"]["profile"]["status"] == "UNDER_REVIEW")

r = client.post(
    f"/api/v1/admin/kyc/cases/KYC_PROFILE/{prof2.id}/decision",
    headers=auth(rev), json={"decision": "REJECT"})
check("reject without reason refused", r.status_code == 400)

dup_reason = next(x for x in meta["rejection_reasons"]
                  if x["code"] == "DUPLICATE_IDENTITY")
r = client.post(
    f"/api/v1/admin/kyc/cases/KYC_PROFILE/{prof2.id}/decision",
    headers=auth(admin),
    json={"decision": "REJECT", "reason_id": dup_reason["id"]})
check("admin rejects duplicate-PAN profile",
      r.json()["data"]["status"] == "REJECTED")

# ---------- 10. doc access logging ----------
r = client.get(f"/api/v1/admin/kyc/documents/{doc1}/url", headers=auth(rev))
check("doc url issued to reviewer", r.status_code == 200
      and "content" in r.json()["data"]["url"])

r = client.get(f"/api/v1/admin/kyc/documents/{doc1}/content",
               headers=auth(rev))
check("decrypted doc streams to reviewer",
      r.status_code == 200 and r.content == PDF)

r = client.get("/api/v1/admin/audit-logs", headers=auth(admin))
check("document access logged twice (url+view)",
      sum(1 for x in r.json()["data"]
          if x["action"] in ("KYC_DOC_ACCESS", "KYC_DOC_VIEW")
          and x["target_id"] == str(doc1)) >= 2, r.text[:300])

r = client.get(f"/api/v1/admin/kyc/documents/{doc1}/content",
               headers=auth(buyer))
check("buyer cannot fetch documents", r.status_code == 403)

# ---------- 11. case view + envelope ----------
r = client.get(f"/api/v1/admin/kyc/cases/OWNERSHIP_CLAIM/{claim3}",
               headers=auth(admin))
d = r.json()
check("case view envelope + detail",
      d["success"] is True and d["data"]["status"] == "APPROVED"
      and d["data"]["requires_admin_review"] is True
      and len(d["data"]["reviews"]) >= 2, r.text[:300])

r = client.get("/api/v1/kyc/me", headers=auth(s1))
check("/kyc/me envelope + masked PAN",
      r.json()["success"] is True
      and r.json()["data"]["profile"]["pan_masked"].startswith("ABCDE"))

# ---------- summary ----------
failed = [n for n, c in results if not c]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", failed)
    raise SystemExit(1)
