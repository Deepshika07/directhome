# DirectHome Backend

FastAPI + SQLAlchemy + Alembic + PostgreSQL backend for the DirectHome marketplace.

## Prerequisites

- Python 3.12+
- PostgreSQL running locally (database `directhome` must exist)

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Database connection

Copy `.env.example` to `.env` and set your credentials:

```
DIRECTHOME_DATABASE_URL=postgresql+psycopg://postgres:root@localhost:5432/directhome
```

Current local setup uses user `postgres` / password `root`. (Note: there is no
`root` role on this Postgres install — `postgres` is the superuser. To create a
`root` role later: `CREATE ROLE root LOGIN PASSWORD 'root' SUPERUSER;`)

All app env vars use the `DIRECTHOME_` prefix so a globally-exported
`DATABASE_URL` from other projects cannot override them.

### KYC / verification env vars

| Var | Purpose |
|-----|---------|
| `DIRECTHOME_KYC_ENC_KEY` | Base64 32-byte AES-GCM key encrypting PAN + document blobs. Generate: `openssl rand -base64 32`. **Required in production** — unset falls back to a key derived from `SECRET_KEY` (dev only). Rotating it without re-encrypting breaks reads. |
| `DIRECTHOME_KYC_HMAC_KEY` | Secret pepper for `pan_hash` dedup. Unset → derived from `SECRET_KEY`. Rotating it orphans existing hashes (duplicates stop matching). |
| `DIRECTHOME_KYC_STORAGE_DIR` | Root for encrypted document blobs (default `./var/kyc-docs`, gitignored). In production mount a persistent volume — blobs are AES-GCM ciphertext on disk. Swap to an S3/Lightsail bucket later by implementing `DocStorage` in `app/services/storage.py` — no caller changes. |
| `DIRECTHOME_KYC_MAX_DOC_MB` | Per-document upload cap (default 8). |
| `DIRECTHOME_KYC_NOTICE_VERSION` | Privacy-notice version recorded in `consent_records` (default `1.0`). Bump when copy changes. |
| `DIRECTHOME_KYC_GRIEVANCE_EMAIL` | DPDP grievance contact surfaced in `/kyc/meta`. |

Verification provider (`app/services/providers/kyc_provider.py`): Phase 1 uses `MockProvider` — PAN checks are simulated, reviewers approve manually. A real vendor (Digio/HyperVerge/IDfy…) implements the same `KycProvider` interface; select it in `get_provider()`. DigiLocker endpoints exist as stubs returning `supported: false`.

Operational notes: run migrations before first boot (`alembic upgrade head` seeds claim types, doc types, rejection reasons, `KYC_REVIEWER` role, `kyc.*` fraud-config). Grant reviewers via `user_roles` → `KYC_REVIEWER`. Staff document views are audit-logged; PAN is masked in every API response.

### Billing / subscriptions

Migration `c4f7a1e3b8d2` adds `plans`, `user_subscriptions`, `payment_orders` and seeds unlimited `FREE_SELLER`/`FREE_BUYER` plans (mirrors pre-billing behavior). Entitlements live in `plans.features` JSONB (`max_active_listings`, `contact_reveals`) — admins edit prices/limits live via `/admin/billing/*`, no env vars needed.

Phase 1 uses `MockBillingProvider` (`app/services/providers/billing_provider.py`) — `POST /billing/orders` → `POST …/confirm` simulates payment. Razorpay later: implement the same `BillingProvider` interface + set `key_id`/`key_secret`/webhook secret env vars.

## Run migrations

Creates all tables + seeds lookup data (roles incl. KYC_REVIEWER, property
types, room configurations, amenities, document types, KYC claim/doc/reason
taxonomies, fraud-config weights, FREE plans):

```bash
alembic upgrade head
```

Useful commands:

```bash
alembic current          # show applied version
alembic history          # list migrations
alembic downgrade -1     # roll back one migration
alembic downgrade base   # drop everything
```

## Run the API

```bash
uvicorn app.main:app --reload
# -> http://localhost:8000/health
# -> http://localhost:8000/docs  (OpenAPI)
```

See `API.md` for the full endpoint reference (for the UI developer).

## Test

```bash
python tests/smoke_test.py    # end-to-end workflow (needs a fresh/empty dev DB)
python tests/test_kyc.py      # KYC identity + ownership verification (39 checks)
python tests/test_antifraud.py # anti-broker engine (33 checks)
python tests/test_billing.py   # plans, entitlements, mock payments (29 checks)
```

## Making schema changes

Never edit the database by hand. Change the model, then:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Schema overview (34 tables)

| Group | Tables |
|-------|--------|
| Auth & users | `users`, `roles`, `user_roles`, `otp_verifications`, `refresh_tokens` |
| Taxonomies (lookup, admin-editable) | `property_types`, `room_configurations`, `amenities`, `document_types` |
| Property core | `properties`, `property_locations`, `property_media`, `property_amenities` |
| Verification & lifecycle | `verification_documents`, `property_verifications`, `property_status_history`, `property_availability_history` |
| Marketplace activity | `saved_properties`, `property_views`, `property_inquiries`, `property_reports` |
| Anti-broker engine | `user_risk_events`, `fraud_config`, `duplicate_matches`, `appeals`, `admin_audit_logs` |
| KYC & ownership | `kyc_profiles`, `kyc_checks`, `kyc_documents`, `property_ownership_claims`, `kyc_reviews`, `kyc_status_history`, `consent_records` + lookups `seller_claim_types`, `kyc_document_types`, `kyc_rejection_reasons` |
| Billing | `plans`, `user_subscriptions`, `payment_orders` |

Key conventions:

- Listing types: `RENT`, `SALE`, `PG`, `SHARED`
- Property lifecycle: `DRAFT` → `PENDING_REVIEW` → `ACTIVE` → `PAUSED` / `RENTED` / `SOLD` / `EXPIRED` / `REJECTED`
- Category-specific attributes go in `properties.extra_attributes` (JSONB) — no
  migration needed for new fields
- One primary media per property is enforced by a partial unique index
- Media types: `IMAGE`, `VIDEO`, `FLOOR_PLAN`, `VIRTUAL_TOUR`
