"""End-to-end smoke test of the DirectHome V1 workflow.

Run:  python tests/smoke_test.py   (from backend/, with .venv active)
Covers: register/login -> KYC -> admin verify -> create listing -> submit ->
admin approve -> public search -> detail view -> save -> inquiry (phone
reveal) -> report -> availability -> pause/activate -> negative checks.

NOTE: writes demo data into the configured database (local dev only).
"""

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


def check(name, cond, extra=""):
    results.append((name, cond))
    print(f"[{PASS if cond else FAIL}] {name} {extra}")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- 1. auth ----------
r = client.post("/api/v1/auth/register", json={
    "name": "Demo Seller", "phone": "+919810000001",
    "email": "seller@demo.com", "password": "password123", "role": "SELLER"})
check("register seller", r.status_code == 201, r.text[:200])
seller = r.json()["data"]
seller_token = seller["tokens"]["access_token"]

r = client.post("/api/v1/auth/register", json={
    "name": "Demo Buyer", "phone": "+919810000002",
    "email": "buyer@demo.com", "password": "password123", "role": "BUYER"})
check("register buyer", r.status_code == 201, r.text[:200])
buyer = r.json()["data"]
buyer_token = buyer["tokens"]["access_token"]

r = client.post("/api/v1/auth/register", json={
    "name": "Admin User", "phone": "+919810000003",
    "email": "admin@demo.com", "password": "adminpass123", "role": "BUYER"})
admin_id = r.json()["data"]["user"]["id"]
admin_token = r.json()["data"]["tokens"]["access_token"]
# grant ADMIN role directly (registration is limited to BUYER/SELLER)
db = SessionLocal()
admin_role = db.query(Role).filter(Role.code == "ADMIN").one()
db.add(UserRole(user_id=admin_id, role_id=admin_role.id))
db.commit()
db.close()

r = client.post("/api/v1/auth/login",
                json={"identifier": "admin@demo.com", "password": "adminpass123"})
check("login admin", r.status_code == 200, r.text[:200])
admin_token = r.json()["data"]["tokens"]["access_token"]

r = client.get("/api/v1/auth/me", headers=auth(seller_token))
check("auth/me returns roles", r.status_code == 200
      and "SELLER" in r.json()["data"]["roles"], r.text[:200])

# ---------- 2. KYC ----------
r = client.get("/api/v1/meta/filters")
filters = r.json()["data"]
doc_type = next(d for d in filters["document_types"] if d["code"] == "AADHAAR_CARD")
check("meta/filters", r.status_code == 200 and len(filters["property_types"]) > 0)

r = client.post("/api/v1/users/me/documents", headers=auth(seller_token),
                json={"document_type_id": doc_type["id"],
                      "file_url": "https://files.example.com/aadhaar.pdf"})
check("seller submits KYC doc", r.status_code == 201, r.text[:200])
doc_id = r.json()["data"]["id"]

r = client.get("/api/v1/admin/documents?status=PENDING", headers=auth(admin_token))
check("admin doc queue", r.status_code == 200 and r.json()["meta"]["total"] >= 1)

r = client.post(f"/api/v1/admin/documents/{doc_id}/verify", headers=auth(admin_token))
check("admin verifies doc", r.status_code == 200
      and r.json()["data"]["status"] == "VERIFIED", r.text[:200])

r = client.get("/api/v1/auth/me", headers=auth(seller_token))
check("seller kyc_status VERIFIED", r.json()["data"]["kyc_status"] == "VERIFIED")

# ---------- 3. listing lifecycle ----------
ptype = next(t for t in filters["property_types"] if t["code"] == "APARTMENT")
rconf = next(r_ for r_ in filters["room_configurations"] if r_["code"] == "2BHK")
amenity_ids = [a["id"] for a in filters["amenities"][:3]]

payload = {
    "title": "Spacious 2BHK in Velachery",
    "description": "Near Phoenix Mall, covered parking.",
    "listing_type": "RENT",
    "property_type_id": ptype["id"],
    "room_configuration_id": rconf["id"],
    "bathrooms": 2, "carpet_area_sqft": 1050,
    "floor_number": 3, "total_floors": 8,
    "furnishing": "SEMI_FURNISHED",
    "rent_amount": 25000, "security_deposit": 100000,
    "maintenance_amount": 2000,
    "available_from": "2026-10-01",
    "location": {"locality": "Velachery", "city": "Chennai",
                 "state": "Tamil Nadu", "pincode": "600042"},
    "media": [
        {"media_type": "IMAGE", "url": "https://img.example.com/1.jpg",
         "is_primary": True},
        {"media_type": "IMAGE", "url": "https://img.example.com/2.jpg"},
        {"media_type": "VIDEO", "url": "https://img.example.com/tour.mp4"},
    ],
    "amenity_ids": amenity_ids,
}

r = client.post("/api/v1/properties", headers=auth(buyer_token), json=payload)
check("buyer cannot create listing (403)", r.status_code == 403, r.text[:150])

r = client.post("/api/v1/properties", headers=auth(seller_token), json=payload)
check("seller creates draft", r.status_code == 201
      and r.json()["data"]["status"] == "DRAFT", r.text[:300])
prop_id = r.json()["data"]["id"]

r = client.post(f"/api/v1/properties/{prop_id}/submit", headers=auth(seller_token))
check("submit -> PENDING_REVIEW", r.status_code == 200
      and r.json()["data"]["status"] == "PENDING_REVIEW", r.text[:200])

r = client.get("/api/v1/properties?city=Chennai")
check("pending listing hidden from public search",
      all(p["id"] != prop_id for p in r.json()["data"]))

r = client.post(f"/api/v1/admin/properties/{prop_id}/approve",
                headers=auth(admin_token))
check("admin approves -> ACTIVE", r.status_code == 200
      and r.json()["data"]["status"] == "ACTIVE", r.text[:200])

# ---------- 4. public browse ----------
r = client.get("/api/v1/properties?city=Chennai&listing_type=RENT"
               "&room_configurations=2BHK&min_price=20000&max_price=30000")
found = [p for p in r.json()["data"] if p["id"] == prop_id]
check("public search finds active listing", len(found) == 1, r.text[:200])

r = client.get(f"/api/v1/properties/{prop_id}")
d = r.json()["data"]
check("public detail (no owner phone)",
      r.status_code == 200 and "phone" not in str(d["owner"]))

r = client.get("/api/v1/locations/suggest?q=Vel")
check("location suggest", r.status_code == 200
      and any(s["type"] == "LOCALITY" for s in r.json()["data"]), r.text[:200])

# ---------- 5. buyer actions ----------
r = client.post(f"/api/v1/properties/{prop_id}/save")
check("save requires auth (401)", r.status_code == 401)

r = client.post(f"/api/v1/properties/{prop_id}/save", headers=auth(buyer_token))
check("buyer saves listing", r.status_code == 201)

r = client.get("/api/v1/users/me/saved-properties", headers=auth(buyer_token))
check("saved list", any(p["id"] == prop_id for p in r.json()["data"]))

r = client.post(f"/api/v1/properties/{prop_id}/inquiries",
                headers=auth(buyer_token),
                json={"message": "Is it still available?", "contact_method": "WHATSAPP"})
check("inquiry reveals owner phone",
      r.status_code == 201
      and r.json()["data"]["owner_contact"]["phone"] == "+919810000001",
      r.text[:300])
inquiry_id = r.json()["data"]["inquiry"]["id"]

r = client.get("/api/v1/owner/inquiries", headers=auth(seller_token))
check("seller sees lead", any(i["id"] == inquiry_id for i in r.json()["data"]))

r = client.patch(f"/api/v1/inquiries/{inquiry_id}", headers=auth(seller_token),
                 json={"status": "CONTACTED"})
check("seller updates lead status", r.json()["data"]["status"] == "CONTACTED")

r = client.post(f"/api/v1/properties/{prop_id}/reports", headers=auth(buyer_token),
                json={"reason": "WRONG_INFORMATION", "description": "test"})
check("buyer reports listing", r.status_code == 201)
r = client.get("/api/v1/admin/reports?status=OPEN", headers=auth(admin_token))
check("admin report queue", r.json()["meta"]["total"] >= 1)

# ---------- 6. availability + lifecycle ----------
r = client.post(f"/api/v1/properties/{prop_id}/availability?available=true",
                headers=auth(seller_token))
check("availability confirm", r.status_code == 200
      and r.json()["data"]["availability_status"] == "AVAILABLE")

r = client.post(f"/api/v1/properties/{prop_id}/pause", headers=auth(seller_token))
check("pause", r.json()["data"]["status"] == "PAUSED")
r = client.post(f"/api/v1/properties/{prop_id}/activate", headers=auth(seller_token))
check("activate", r.json()["data"]["status"] == "ACTIVE")

r = client.get("/api/v1/users/me/properties", headers=auth(seller_token))
check("seller dashboard list", any(p["id"] == prop_id for p in r.json()["data"]))

# ---------- summary ----------
failed = [n for n, c in results if not c]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
if failed:
    print("FAILED:", failed)
    raise SystemExit(1)
