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
| `verified_only` | true | Only document-verified owners (verification_level ≥ 2) |
| `q` | string | Title search |
| `sort` | newest\|price_asc\|price_desc\|trust_score | default `newest` |
| `page` / `limit` | int | Pagination |

Returns array of **cards**: `id, title, listing_type, property_type, room_configuration, bedrooms, bathrooms, furnishing, rent_amount, sale_price, area_sqft, locality, city, primary_image, trust_score, is_owner_verified, verification_level, status, available_from, created_at`. `verification_level`: 0 none · 1 identity KYC only · 2 ownership documents reviewed ("document-verified owner" badge).

**GET /properties/{id}** — full detail; everything in the card plus `description, security_deposit, maintenance_amount, price_negotiable, carpet/built_up/plot_area_sqft, floor_number, total_floors, availability_status, last_verified_at, extra_attributes, location{…}, media[{media_type,url,thumbnail_url,is_primary}], amenities["Parking",…], owner{id,name,is_verified}`. Records a view (send optional `X-Session-Id` header for anonymous dedup). **Owner phone is never in this response.**

---

## Seller — listing management (Bearer + SELLER role)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/properties` | Create draft — everything in one call |
| PUT | `/properties/{id}` | Edit (DRAFT/REJECTED/PAUSED/ACTIVE; ACTIVE → back to PENDING_REVIEW) |
| DELETE | `/properties/{id}` | Delete — only while DRAFT or REJECTED |
| GET | `/users/me/properties` | My listings, all statuses (dashboard) |
| POST | `/properties/{id}/submit` | DRAFT/REJECTED → PENDING_REVIEW — requires identity KYC (L1), else `403 KYC_REQUIRED` |
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

## Verification & anti-broker (Bearer)

**Listing prerequisites** — sellers must accept anti-broker T&C + declare ownership before creating/submitting/editing listings (`PROFILE_INCOMPLETE` otherwise). Restricted/suspended/banned accounts are blocked at publish.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/users/me/verification/status` | kyc/account status, declaration, terms, `can_list`, `missing_requirements` |
| POST | `/users/me/verification/accept-terms` | `{ "version": "1.0" }` — must match current terms version |
| POST | `/users/me/verification/owner-declaration` | `{ "role": "OWNER" \| "AUTHORIZED_REPRESENTATIVE" }` |
| POST | `/users/me/verification/send-code` | `{ "channel": "PHONE" \| "EMAIL" }` — dev echoes `dev_code` outside production |
| POST | `/users/me/verification/verify-code` | `{ "channel", "code" }` → sets phone/email verified |
| POST | `/properties/{id}/verification-documents` | `{ "document_type_id", "file_url" }` — property doc, owner only; pdf/img types only |
| GET | `/properties/{id}/verification-status` | Owner: status, `rejection_reason`, docs, `duplicate_flag` |
| POST | `/users/me/appeals` | `{ "message", "property_id?" }` — appeal a restriction/flag |
| GET | `/users/me/appeals` | My appeals |

**Reports** — `POST /properties/{id}/reports` is one-per-user-per-property (`409 ALREADY_REPORTED`); `INCORRECT_OWNERSHIP` added to reasons. ≥3 OPEN reports (config `report.flag_threshold`) auto-FLAG the listing — hides it from search without scoring the owner.

**Risk engine** — `user_risk_events` store scored events (deduped). Score → status: ≥30 `UNDER_REVIEW`, ≥50 `TEMPORARILY_RESTRICTED`, ≥70 `SUSPENDED` — auto-escalate only; de-escalation and bans are manual admin actions. Rule scores/thresholds editable via `GET/POST /admin/fraud/config`.

## KYC & ownership verification — Phase 1 (Bearer)

Two separate verifications. **Identity KYC** (once per user) → `kyc_level` 1 → required to submit listings. **Ownership claim** (once per listing) → `verification_level` 2 → "document-verified owner" badge. Verified identity does NOT imply ownership; we never claim title verification.

**Trust levels** — L0 browse/save/draft · L1 submit listings · L2 verified-owner badge · L3 (future) geo/site check. Existing sellers are grandfathered at L0 until their next submit/edit, which then requires KYC.

**KYC profile statuses** — `NOT_STARTED → IN_PROGRESS → SUBMITTED → AUTO_VERIFIED|UNDER_REVIEW → APPROVED|REJECTED|NEEDS_MORE_INFO → SUBMITTED (resubmit) → EXPIRED`. Claim statuses: `SUBMITTED → UNDER_REVIEW → APPROVED|REJECTED|NEEDS_MORE_INFO → SUBMITTED`.

### Seller flow

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/kyc/consent` | `{ purpose?: "KYC_IDENTITY" }` — records notice version, IP, UA. Required before PAN |
| GET | `/kyc/me` | Full KYC state: `kyc_status`, `kyc_level`, `has_consent`, `profile{status, level, name_as_per_pan, pan_masked, aadhaar_last4, verified_at, rejection_reason, review_sla_hours}`, `documents[]` |
| GET | `/kyc/meta` | `claim_types` (INDIVIDUAL_OWNER, CO_OWNER, POA_HOLDER, BUILDER_DEVELOPER, PG_OPERATOR + `required_doc_codes`, `requires_admin_review`), `document_types`, `rejection_reasons`, `notice_version`, `grievance_email`, badge copy |
| POST | `/kyc/identity/pan` | `{ pan: "ABCDE1234F", name_as_per_pan }` → provider verify + dup-hash check. PAN stored AES-GCM-encrypted; only masked `ABCDE****F` ever returned |
| POST | `/kyc/identity/digilocker/initiate` · GET `…/callback` | Stub — provider not configured yet (`supported: false`) |
| POST | `/kyc/documents` | `{ doc_type_id, filename, mime, size_bytes }` → `{ document_id, upload_url }` (JPEG/PNG/WebP/PDF, ≤8MB) |
| POST | `/kyc/documents/{id}/content` | Raw bytes body — server sniffs magic bytes vs declared MIME, sha256-dedups across accounts, encrypts to storage |
| POST | `/kyc/submit` | Finalize → `AUTO_VERIFIED` (clean) or `UNDER_REVIEW` (flags/dup PAN) |

### Ownership claim flow (requires L1)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/properties/{id}/ownership-claim` | `{ claim_type_id, owner_name_on_doc }` → claim + `name_match_score` vs KYC name |
| POST | `/properties/{id}/ownership-claim/documents` | Same init shape as `/kyc/documents`, attached to the listing — doc types restricted to `applies_to ≠ USER` |
| GET | `/properties/{id}/ownership-claim` | Claim status + docs (owner, or staff) |

New uploads while a claim is `NEEDS_MORE_INFO` auto-resubmit it to `SUBMITTED`.

### Reviewer / admin queue (KYC_REVIEWER or ADMIN)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/kyc/queue` | `?status=&claim_type=&min_risk=&sla_breached=&page=&limit=` — mixed KYC_PROFILE + OWNERSHIP_CLAIM items, priority-sorted (SLA breach → risk → age) |
| GET | `/admin/kyc/cases/{KYC_PROFILE\|OWNERSHIP_CLAIM}/{id}` | Case view: subject, checks, documents, linked accounts (same PAN/doc bytes), history, reviews, `requires_admin_review` |
| POST | `/admin/kyc/cases/{type}/{id}/decision` | `{ decision: "APPROVE"\|"REJECT", reason_id?, notes? }` — REJECT needs `reason_id`. High-risk cases (risk ≥ `kyc.maker_checker_risk_threshold`, POA/builder claim types, name mismatch) → reviewer APPROVE becomes `RECOMMEND_APPROVE` pending `ADMIN_FINALIZE` |
| POST | `/admin/kyc/cases/{type}/{id}/request-info` | `{ notes (required), reason_id? }` → `NEEDS_MORE_INFO` |
| POST | `/admin/kyc/cases/{type}/{id}/escalate` | `{ notes? }` — flags for admin attention, stays in queue |
| GET | `/admin/kyc/documents/{id}/url` | Returns a content path — access logged (`KYC_DOC_ACCESS`) |
| GET | `/admin/kyc/documents/{id}/content` | Decrypted doc bytes — view logged (`KYC_DOC_VIEW`) |

Reviewers cannot act on their own cases. Approve side-effects: profile → `kyc_level=1`, `users.kyc_status=VERIFIED`; claim → `verification_level=2`, `is_owner_verified=true`. Fraud-flavored rejections (tampered doc, address mismatch) create `user_risk_events` via the existing engine (`SUSPECTED_TAMPERED_DOC` +70, `DUPLICATE_PAN_HASH` +60, `KYC_NAME_MISMATCH` +25, `KYC_ADDRESS_MISMATCH` +20 — editable in `fraud_config`).

Security: PAN/Aadhaar never logged; PAN masked for reviewers (`unmask` is not exposed yet); every staff doc access is audit-logged; consent + full status history retained per DPDP purpose-limitation.

## Admin — fraud & moderation (Bearer + ADMIN)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/verifications/pending` | PENDING_REVIEW queue + owner risk/KYC context |
| GET | `/admin/fraud/flagged-users?min_score=&account_status=` | Users by risk score |
| GET | `/admin/fraud/users/{id}/risk-history` | Risk events for a user |
| GET | `/admin/fraud/reported-properties?status=` | Reports + property_title + reporter_name |
| GET | `/admin/fraud/duplicate-listings?status=` | Detected duplicate pairs |
| POST | `/admin/fraud/duplicate-matches/{id}/confirm` · `/dismiss` | Review a pair |
| POST | `/admin/properties/{id}/moderation` | `{ action: UNFLAG\|SUSPEND\|REINSTATE, reason }` |
| POST | `/admin/users/{id}/restriction` | `{ action: RESTRICT\|SUSPEND\|UNSUSPEND\|BAN, reason }` |
| POST | `/admin/documents/{id}/request-info` | `{ note }` → REQUIRES_ADDITIONAL_INFO |
| POST | `/admin/documents/{id}/reject` | `{ reason, ownership_mismatch? }` — mismatch scores +50 |
| POST | `/admin/reports/{id}/resolve` | `{ validated?: true, note? }` — validated reports score owner |
| GET | `/admin/appeals?status=` · POST `/admin/appeals/{id}/resolve` | Appeal queue; approve → account ACTIVE |
| GET/POST | `/admin/fraud/config` | View/update rule scores & thresholds |
| GET | `/admin/audit-logs?action=&target_type=` | Admin + system action trail |

---

## Billing & subscriptions — Phase 1 (mock provider)

Entitlements live in `plans.features` JSONB — admins create/edit plans freely, no deploys. Known feature keys: `max_active_listings` (SELLER — concurrent PENDING_REVIEW+ACTIVE+PAUSED+FLAGGED listings), `contact_reveals` (BUYER — **distinct properties** per window; re-contacting the same listing is always free even at the limit, but the same owner on a *different* listing consumes another credit). `null` = unlimited. Seeded `FREE_SELLER`/`FREE_BUYER` are unlimited (mirrors pre-billing behavior) until an admin tightens them.

**Gate points:** `POST /properties/{id}/submit` checks listing slots; `POST /properties/{id}/inquiries` checks contact quota. Both return `403 LIMIT_REACHED` when exhausted — show the plan catalogue to upgrade. No plan configured at all → `403 PLAN_REQUIRED`.

**Subscription scope:** a user's effective plan = their ACTIVE subscription's plan for that scope, else the FREE plan. Usage window = subscription window (free-plan users count all-time). Plan edits apply **live** — existing subscribers see new limits immediately (no snapshots yet).

### User-facing

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/billing/plans?applies_to=SELLER\|BUYER` | Public catalogue — drives the pricing page |
| GET | `/billing/me` | `{seller:{plan_code,subscribed,limit,used,expires_at}, buyer:{…}}` — remaining quota for the UI |
| POST | `/billing/orders` | `{ plan_id }` → `CREATED` + `checkout{provider,order_ref,amount_paise,checkout_url}` — free plans short-circuit to `PAID` + `subscription_id` |
| POST | `/billing/orders/{id}/confirm` | `{ signature? }` — mock: pass `"fail"` to simulate failure. Paid → activates subscription (replaces any ACTIVE sub for the same scope) |
| GET | `/billing/orders` | My order history |

### Admin (Bearer + ADMIN)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/billing/plans` | All plans incl. inactive |
| POST | `/admin/billing/plans` | `{ code (A-Z0-9_), name, applies_to, price_paise, duration_days?, features{}, is_active?, sort_order? }` |
| PUT | `/admin/billing/plans/{id}` | Partial update — price/limits/description apply live to all users on that plan |
| DELETE | `/admin/billing/plans/{id}` | Soft-delete (deactivate) if subscribed; hard-delete if unused |
| GET | `/admin/billing/subscriptions?status=&plan_id=&page=` | All subscriptions |
| POST | `/admin/billing/grants` | `{ user_id, plan_id, days? }` — comp a user (support/testing); `source=ADMIN_GRANT` |
| POST | `/admin/billing/subscriptions/{id}/revoke` | → `CANCELLED` |
| GET | `/admin/billing/stats` | `paid_orders`, `revenue_paise`, `active_subscriptions_by_plan`, `expiring_next_7_days` |

**Razorpay later:** provider interface in `app/services/providers/billing_provider.py` (`create_order`/`verify_payment`). Real integration = implement `RazorpayProvider` (order create via API, signature verify, webhook for `payment.captured`) + env keys (`key_id`, `key_secret`, webhook secret) — business logic unchanged.

---

## Error codes

`VALIDATION_ERROR, UNAUTHORIZED, FORBIDDEN, PROPERTY_NOT_FOUND, PHONE_TAKEN, EMAIL_TAKEN, LISTING_INCOMPLETE, INVALID_STATUS_TRANSITION, NOT_EDITABLE, CANNOT_DELETE, ALREADY_SAVED, NOT_SAVED, OWN_LISTING, INVALID_CONTACT_METHOD, INVALID_REASON, INVALID_DOC_TYPE, ALREADY_REVIEWED, INQUIRY_NOT_FOUND, REPORT_NOT_FOUND, DOCUMENT_NOT_FOUND, USER_NOT_FOUND, ROLE_MISSING, KYC_REQUIRED, CONSENT_REQUIRED, PAN_FORMAT, PAN_VERIFY_FAILED, KYC_INCOMPLETE, KYC_LOCKED, BAD_MIME, FILE_TOO_LARGE, BAD_FILE_TYPE, MIME_MISMATCH, BAD_DOC_TYPE, BAD_CLAIM_TYPE, CLAIM_EXISTS, CLAIM_NOT_FOUND, INVALID_KYC_TRANSITION, INVALID_CLAIM_TRANSITION, REASON_REQUIRED, NOTE_REQUIRED, BAD_DECISION, BAD_SUBJECT_TYPE, CASE_NOT_FOUND, DOC_NOT_FOUND, PLAN_REQUIRED, LIMIT_REACHED, PLAN_NOT_FOUND, PLAN_CODE_TAKEN, ORDER_NOT_FOUND, ORDER_CLOSED, PAYMENT_FAILED, SUB_NOT_FOUND, SUB_CLOSED`

## Demo data

Seeded test accounts (local dev): `seller@demo.com` / `password123` (SELLER, KYC-verified, owns demo 2BHK listing), `buyer@demo.com` / `password123` (BUYER), `admin@demo.com` / `adminpass123` (ADMIN).
