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

## Run migrations

Creates all 21 tables + seeds lookup data (roles, property types, room
configurations, amenities, document types):

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
python tests/smoke_test.py   # runs the full workflow end-to-end (29 checks)
```

## Making schema changes

Never edit the database by hand. Change the model, then:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Schema overview (21 tables)

| Group | Tables |
|-------|--------|
| Auth & users | `users`, `roles`, `user_roles`, `otp_verifications`, `refresh_tokens` |
| Taxonomies (lookup, admin-editable) | `property_types`, `room_configurations`, `amenities`, `document_types` |
| Property core | `properties`, `property_locations`, `property_media`, `property_amenities` |
| Verification & lifecycle | `verification_documents`, `property_verifications`, `property_status_history`, `property_availability_history` |
| Marketplace activity | `saved_properties`, `property_views`, `property_inquiries`, `property_reports` |

Key conventions:

- Listing types: `RENT`, `SALE`, `PG`, `SHARED`
- Property lifecycle: `DRAFT` → `PENDING_REVIEW` → `ACTIVE` → `PAUSED` / `RENTED` / `SOLD` / `EXPIRED` / `REJECTED`
- Category-specific attributes go in `properties.extra_attributes` (JSONB) — no
  migration needed for new fields
- One primary media per property is enforced by a partial unique index
- Media types: `IMAGE`, `VIDEO`, `FLOOR_PLAN`, `VIRTUAL_TOUR`
