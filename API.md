# DirectHome API — v1

Base URL: `http://localhost:8000/api/v1` · Interactive docs: `http://localhost:8000/docs`

## Conventions

- **Auth**: send `Authorization: Bearer <access_token>` on protected endpoints.
- **Response envelope** — every response:
  ```json
  { "success": true, "data": { ... }, "meta": { "page": 1, "limit": 20, "total": 120 } }
  ```
  `meta` is present only on paginated list endpoints.
- **Errors**:
  ```json
  { "success": false, "error": { "code": "PROPERTY_NOT_FOUND", "message": "Property not found" } }
  ```
  `code` is stable for programmatic handling; `message` is human-readable.
- **Pagination**: `?page=1&limit=20` (limit max 100).
- **Enum values** are always UPPERCASE strings.

## Listing lifecycle

`DRAFT` → `PENDING_REVIEW` (submit) → `ACTIVE` (admin approve) → `PAUSED` / `RENTED` / `SOLD` / `EXPIRED`. `REJECTED` (admin) can be edited and resubmitted. Only `ACTIVE` listings appear in public search. Editing an `ACTIVE` listing pushes it back to `PENDING_REVIEW`.

---

## Auth

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/auth/register` | — | Create account + return tokens |
| POST | `/auth/login` | — | Login with phone/email + password |
| POST | `/auth/refresh` | — | Swap refresh token → new token pair (old refresh revoked) |
| POST | `/auth/logout` | — | Revoke a refresh token |
| GET | `/auth/me` | Bearer | Current profile, roles, kyc_status |

**POST /auth/register**
```json
{ "name": "Demo Seller", "phone": "+919810000001", "email": "s@x.com",
  "password": "password123", "role": "SELLER" }
```
`role`: `BUYER` | `SELLER`. Response: `{ user, tokens: { access_token, refresh_token, token_type, expires_in } }`. `expires_in` = access-token seconds (1800).

**POST /auth/login** — `{ "identifier": "seller@demo.com" | "+91…", "password": "…" }` → same shape as register.

**POST /auth/refresh** — `{ "refresh_token": "…" }` → `{ access_token, refresh_token, … }` (store BOTH — the old refresh token dies).

**GET /auth/me** → user object: `id, name, email, phone, roles: ["SELLER"], kyc_status: NOT_SUBMITTED|PENDING|VERIFIED|REJECTED, phone_verified, email_verified, is_active, created_at`.

---

## KYC documents (seller)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/users/me/documents` | Bearer | Submit a document (file URL) |
| GET | `/users/me/documents` | Bearer | My documents + statuses |

**POST** body: `{ "document_type_id": 1, "file_url": "https://…/aadhaar.pdf", "property_id": null }`
`document_type_id` comes from `/meta/filters` → `document_types` (Aadhaar=1, PAN=2, …). Sets `kyc_status` → `PENDING`. Optional `property_id` attaches the doc to a listing.

---

## Meta / filters (public)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/meta/filters` | All dropdown values in one call: `listing_types`, `furnishing_options`, `property_types` (id/code/name/category), `room_configurations` (id/code/name/bedrooms), `amenities`, `document_types` |
| GET | `/locations/suggest?q=vel` | Autocomplete: `[{ type: "CITY"|"LOCALITY", label, city, locality }]` — only locations that have listings |

---

## Public browse (no auth)

**GET /properties** — search. All params optional, composable:

| Param | Type | Meaning |
|-------|------|---------|
| `listing_type` | RENT\|SALE\|PG\|SHARED | |
| `city` / `locality` / `pincode` | string | Location filter |
| `category` | RESIDENTIAL\|LAND\|COMMERCIAL\|PG | Property-type group |
| `property_types` | csv codes | `APARTMENT,VILLA` |
| `room_configurations` | csv codes | `1RK,2BHK,STUDIO` |
| `min_bedrooms` / `max_bedrooms` | int | Range filter |
| `furnishing` | UNFURNISHED\|SEMI_FURNISHED\|FULLY_FURNISHED | |
| `min_price` / `max_price` | number | Applies to rent for rentals, sale_price for sales |
| `min_area` / `max_area` | int sqft | Carpet or plot area |
| `available_from` | YYYY-MM-DD | Available on/before this date |
| `owner_verified` | true | Only verified-owner listings |
| `q` | string | Title search |
| `sort` | newest\|price_asc\|price_desc\|trust_score | default `newest` |
| `page` / `limit` | int | Pagination |

Returns array of **cards**: `id, title, listing_type, property_type, room_configuration, bedrooms, bathrooms, furnishing, rent_amount, sale_price, area_sqft, locality, city, primary_image, trust_score, is_owner_verified, status, available_from, created_at`.

**GET /properties/{id}** — full detail; everything in the card plus `description, security_deposit, maintenance_amount, price_negotiable, carpet/built_up/plot_area_sqft, floor_number, total_floors, availability_status, last_verified_at, extra_attributes, location{…}, media[{media_type,url,thumbnail_url,is_primary}], amenities["Parking",…], owner{id,name,is_verified}`. Records a view (send optional `X-Session-Id` header for anonymous dedup). **Owner phone is never in this response.**

---

## Seller — listing management (Bearer + SELLER role)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/properties` | Create draft — everything in one call |
| PUT | `/properties/{id}` | Edit (DRAFT/REJECTED/PAUSED/ACTIVE; ACTIVE → back to PENDING_REVIEW) |
| DELETE | `/properties/{id}` | Delete — only while DRAFT or REJECTED |
| GET | `/users/me/properties` | My listings, all statuses (dashboard) |
| POST | `/properties/{id}/submit` | DRAFT/REJECTED → PENDING_REVIEW (server checks completeness) |
| POST | `/properties/{id}/pause` | ACTIVE → PAUSED |
| POST | `/properties/{id}/activate` | PAUSED → ACTIVE |
| POST | `/properties/{id}/mark-rented` | → RENTED |
| POST | `/properties/{id}/mark-sold` | → SOLD |
| POST | `/properties/{id}/availability?available=true` | Confirm still-available (freshness) |
| GET | `/owner/inquiries` | Leads received on my listings |

**POST /properties** body:
```json
{
  "title": "Spacious 2BHK in Velachery",
  "description": "Near Phoenix Mall",
  "listing_type": "RENT",
  "property_type_id": 1,
  "room_configuration_id": 3,
  "bathrooms": 2, "carpet_area_sqft": 1050,
  "floor_number": 3, "total_floors": 8,
  "furnishing": "SEMI_FURNISHED",
  "rent_amount": 25000, "security_deposit": 100000, "maintenance_amount": 2000,
  "sale_price": null, "price_negotiable": false,
  "available_from": "2026-10-01",
  "extra_attributes": { "facing": "east", "cabins": 4 },
  "location": { "locality": "Velachery", "city": "Chennai", "state": "Tamil Nadu",
                "pincode": "600042", "address_line1": "…", "latitude": 12.98, "longitude": 80.22 },
  "media": [ { "media_type": "IMAGE", "url": "https://…", "is_primary": true },
             { "media_type": "VIDEO", "url": "https://…" } ],
  "amenity_ids": [1, 2, 3]
}
```
Rules: `RENT|PG|SHARED` need `rent_amount`; `SALE` needs `sale_price` (enforced at submit; drafts may omit). `property_type_id` / `room_configuration_id` / `amenity_ids` come from `/meta/filters`. `extra_attributes` is free-form JSON for category-specific fields (plot dimensions, cabins, sharing type…). Max one `is_primary` media.

**PUT /properties/{id}** — same shape, all fields optional; only sent fields change. `location`, `media`, `amenity_ids` replace fully when sent.

**Submit completeness check** (server-enforced): description, location, ≥1 IMAGE media, price per listing type, room config for residential. Returns `LISTING_INCOMPLETE` listing what's missing.

---

## Buyer actions (Bearer)

| Method | Path | Purpose |
|--------|------|---------|
| POST / DELETE | `/properties/{id}/save` | Save / unsave favorite |
| GET | `/users/me/saved-properties` | My saved listings (cards) |
| POST | `/properties/{id}/inquiries` | Contact owner — **returns seller phone** |
| GET | `/users/me/inquiries` | My sent inquiries |
| PATCH | `/inquiries/{id}` | (owner only) set status CONTACTED\|RESPONDED\|CLOSED |
| POST | `/properties/{id}/reports` | Report listing |

**POST /properties/{id}/inquiries** body: `{ "message": "Still available?", "contact_method": "WHATSAPP" }` (`PHONE|WHATSAPP|EMAIL|PLATFORM`). Response adds `owner_contact: { name, phone }` — this is the only place the seller's number is exposed.

**POST /properties/{id}/reports** body: `{ "reason": "FAKE_LISTING", "description": "…" }` — reasons: `FAKE_LISTING, ALREADY_RENTED, ALREADY_SOLD, WRONG_INFORMATION, BROKER, DUPLICATE, SCAM, INAPPROPRIATE, OTHER`.

---

## Admin (Bearer + ADMIN role)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/properties?status=PENDING_REVIEW` | Moderation queue (any status filter) |
| POST | `/admin/properties/{id}/approve` | → ACTIVE (goes live) |
| POST | `/admin/properties/{id}/reject` | → REJECTED, body `{ "reason": "…" }` |
| GET | `/admin/documents?status=PENDING` | KYC review queue (incl. `user_name`) |
| POST | `/admin/documents/{id}/verify` | Approve → owner `kyc_status=VERIFIED` |
| POST | `/admin/documents/{id}/reject` | Reject, body `{ "reason": "…" }` → `kyc_status=REJECTED` |
| GET | `/admin/reports?status=OPEN` | Report queue |
| POST | `/admin/reports/{id}/resolve` · `/dismiss` | Close a report |
| GET | `/admin/users?q=&kyc_status=` | User list with roles |
| POST | `/admin/users/{id}/deactivate` · `/activate` | Ban / unban |

---

## Error codes

`VALIDATION_ERROR, UNAUTHORIZED, FORBIDDEN, PROPERTY_NOT_FOUND, PHONE_TAKEN, EMAIL_TAKEN, LISTING_INCOMPLETE, INVALID_STATUS_TRANSITION, NOT_EDITABLE, CANNOT_DELETE, ALREADY_SAVED, NOT_SAVED, OWN_LISTING, INVALID_CONTACT_METHOD, INVALID_REASON, INVALID_DOC_TYPE, ALREADY_REVIEWED, INQUIRY_NOT_FOUND, REPORT_NOT_FOUND, DOCUMENT_NOT_FOUND, USER_NOT_FOUND, ROLE_MISSING`

## Demo data

Seeded test accounts (local dev): `seller@demo.com` / `password123` (SELLER, KYC-verified, owns demo 2BHK listing), `buyer@demo.com` / `password123` (BUYER), `admin@demo.com` / `adminpass123` (ADMIN).
