"""KYC identity + property ownership verification — Phase 1.

Two separate verifications:
- Identity KYC (once per user) → L1 → may submit listings.
- Ownership claim (once per listing) → L2 → "document-verified owner" badge.
  We never claim title verification — only that documents were reviewed.

Every status transition goes through _set_status() (valid transitions defined
in one place) and is written to kyc_status_history; staff actions also go to
kyc_reviews + admin_audit_logs. Risk signals feed broker_service.record_event.
"""
import difflib
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt, encrypt, hmac_id, mask_pan
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.models import (
    ConsentRecord,
    KycCheck,
    KycDocument,
    KycDocumentType,
    KycProfile,
    KycRejectionReason,
    KycReview,
    KycStatusHistory,
    Property,
    PropertyLocation,
    PropertyOwnershipClaim,
    SellerClaimType,
    User,
    enums,
)
from app.services import broker_service
from app.services.audit_service import log_admin_action
from app.services.providers import get_provider
from app.services.storage import (
    ALLOWED_MIMES,
    get_storage,
    new_storage_key,
    sha256_hex,
    sniff_mime,
)

KPS = enums.KycProfileStatus
OCS = enums.OwnershipClaimStatus
RD = enums.ReviewDecision
ST = enums.KycSubjectType

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
DOC_MAX_BYTES = 8 * 1024 * 1024

# valid transitions — the single source of truth
PROFILE_TRANSITIONS: dict[KPS, set[KPS]] = {
    KPS.NOT_STARTED: {KPS.IN_PROGRESS},
    KPS.IN_PROGRESS: {KPS.SUBMITTED},
    KPS.SUBMITTED: {KPS.AUTO_VERIFIED, KPS.UNDER_REVIEW},
    KPS.AUTO_VERIFIED: {KPS.APPROVED, KPS.UNDER_REVIEW, KPS.EXPIRED},
    KPS.UNDER_REVIEW: {KPS.APPROVED, KPS.REJECTED, KPS.NEEDS_MORE_INFO},
    KPS.NEEDS_MORE_INFO: {KPS.SUBMITTED, KPS.REJECTED},
    KPS.APPROVED: {KPS.UNDER_REVIEW, KPS.EXPIRED},
    KPS.REJECTED: {KPS.IN_PROGRESS},
    KPS.EXPIRED: {KPS.IN_PROGRESS},
}
OPEN_PROFILE_STATES = {KPS.SUBMITTED, KPS.UNDER_REVIEW, KPS.AUTO_VERIFIED,
                       KPS.APPROVED}

CLAIM_TRANSITIONS: dict[OCS, set[OCS]] = {
    OCS.NOT_STARTED: {OCS.SUBMITTED},
    OCS.SUBMITTED: {OCS.UNDER_REVIEW, OCS.APPROVED, OCS.REJECTED,
                    OCS.NEEDS_MORE_INFO},
    OCS.UNDER_REVIEW: {OCS.APPROVED, OCS.REJECTED, OCS.NEEDS_MORE_INFO},
    OCS.NEEDS_MORE_INFO: {OCS.SUBMITTED, OCS.REJECTED},
    OCS.APPROVED: set(),
    OCS.REJECTED: set(),
}
OPEN_CLAIM_STATES = {OCS.SUBMITTED, OCS.UNDER_REVIEW, OCS.NEEDS_MORE_INFO}


def _v(x):
    return x.value if hasattr(x, "value") else x


def _cfg(db: Session) -> dict:
    return broker_service.get_config(db)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- history / audit ----------------

def _history(db, subject_type, subject_id, old, new, actor_id, reason=None):
    db.add(KycStatusHistory(
        subject_type=subject_type, subject_id=subject_id,
        old_status=old, new_status=new, actor_id=actor_id, reason=reason,
    ))


def _set_status(db, subject, subject_type, new_status, actor_id=None,
                reason=None):
    old, new = _v(subject.status), _v(new_status)
    if subject_type == ST.KYC_PROFILE:
        allowed = PROFILE_TRANSITIONS[KPS(old)]
        code = "INVALID_KYC_TRANSITION"
        label = "KYC"
    else:
        allowed = CLAIM_TRANSITIONS[OCS(old)]
        code = "INVALID_CLAIM_TRANSITION"
        label = "claim"
    if new not in {s.value for s in allowed}:
        raise bad_request(code, f"Cannot move {label} {old} → {new}")
    _history(db, subject_type, subject.id, old, new, actor_id, reason)
    subject.status = new
    db.flush()


def _sync_user_kyc(db, profile: KycProfile, user: User) -> None:
    """Keep the coarse users.kyc_status/kyc_level in step with the profile."""
    st = _v(profile.status)
    if st in ("AUTO_VERIFIED", "APPROVED"):
        user.kyc_status = "VERIFIED"
        user.kyc_level = max(user.kyc_level, 1)
        profile.level = 1
    elif st in ("IN_PROGRESS", "SUBMITTED", "UNDER_REVIEW",
                "NEEDS_MORE_INFO"):
        user.kyc_status = "PENDING"
    elif st == "REJECTED":
        user.kyc_status = "REJECTED"
        user.kyc_level = 0
        profile.level = 0
    else:
        user.kyc_status = "NOT_SUBMITTED"


# ---------------- consent ----------------

def record_consent(db: Session, user: User, purpose: str, ip: str | None,
                   ua: str | None) -> ConsentRecord:
    rec = ConsentRecord(
        user_id=user.id, purpose=purpose,
        notice_version=settings.kyc_notice_version, ip=ip, user_agent=ua,
    )
    db.add(rec)
    _get_or_create_profile(db, user)  # profile exists → IN_PROGRESS
    db.commit()
    return rec


def has_consent(db: Session, user_id: int) -> bool:
    return db.scalar(
        select(func.count(ConsentRecord.id)).where(
            ConsentRecord.user_id == user_id,
            ConsentRecord.purpose == "KYC_IDENTITY",
            ConsentRecord.withdrawn_at.is_(None),
        )
    ) > 0


# ---------------- profile & PAN ----------------

def get_profile(db: Session, user_id: int) -> KycProfile | None:
    return db.scalar(select(KycProfile).where(KycProfile.user_id == user_id))


def _get_or_create_profile(db: Session, user: User) -> KycProfile:
    profile = get_profile(db, user.id)
    if profile is None:
        profile = KycProfile(user_id=user.id, status=KPS.IN_PROGRESS)
        db.add(profile)
        db.flush()
        _history(db, ST.KYC_PROFILE, profile.id, None, "IN_PROGRESS", user.id)
    elif _v(profile.status) == "NOT_STARTED":
        _set_status(db, profile, ST.KYC_PROFILE, KPS.IN_PROGRESS, user.id)
    return profile


def submit_pan(db: Session, user: User, pan: str, name: str) -> KycProfile:
    pan = pan.strip().upper()
    name = name.strip()
    if not PAN_RE.match(pan):
        raise bad_request("PAN_FORMAT", "Invalid PAN format (ABCDE1234F)")
    if not has_consent(db, user.id):
        raise bad_request("CONSENT_REQUIRED", "Accept the KYC notice first")

    profile = _get_or_create_profile(db, user)
    st = _v(profile.status)
    if st in ("SUBMITTED", "UNDER_REVIEW", "AUTO_VERIFIED", "APPROVED"):
        raise bad_request("KYC_LOCKED",
                          f"KYC already {st.lower()} — PAN cannot be changed")

    provider = get_provider()
    res = provider.verify_pan(pan, name)
    db.add(KycCheck(
        user_id=user.id, check_type="PAN", provider=provider.name,
        result=res.result, score=res.score, provider_ref=res.provider_ref,
        detail={"name_match": res.detail.get("name_match")},
    ))
    if res.result == "FAIL":
        db.commit()
        raise bad_request("PAN_VERIFY_FAILED",
                          "PAN could not be verified — check the number")

    # duplicate identity check (hash only — PAN never stored or compared raw)
    dup_hash = hmac_id(pan)
    other = db.scalar(
        select(KycProfile).where(KycProfile.pan_hash == dup_hash,
                                 KycProfile.user_id != user.id)
    )
    profile.pan_encrypted = encrypt(pan)
    profile.pan_hash = dup_hash
    profile.name_as_per_pan = name
    if res.canonical_name:
        profile.name_as_per_pan = res.canonical_name

    if other is not None:
        db.add(KycCheck(user_id=user.id, check_type="PAN_DEDUP",
                        provider="internal", result="FAIL",
                        detail={"conflict_user_id": other.user_id}))
        broker_service.record_event(
            db, user=user, event_type="DUPLICATE_PAN_HASH",
            delta=_cfg(db)["score.duplicate_pan_hash"],
            reason="PAN already linked to another account",
            related_user_id=other.user_id,
            dedup_key=f"dup-pan:{user.id}",
        )
    elif res.result == "REVIEW":
        db.add(KycCheck(user_id=user.id, check_type="PAN_NAME_MATCH",
                        provider=provider.name, result="REVIEW",
                        score=res.score))
        broker_service.record_event(
            db, user=user, event_type="KYC_NAME_MISMATCH",
            delta=_cfg(db)["score.kyc_name_mismatch"],
            reason="Name does not exactly match PAN records",
            dedup_key=f"kyc-namematch:{user.id}",
        )
    db.commit()
    return profile


# ---------------- documents ----------------

def init_document(db: Session, user: User, doc_type_id: int, filename: str,
                  mime: str, size_bytes: int,
                  property_id: int | None = None) -> KycDocument:
    if mime not in ALLOWED_MIMES:
        raise bad_request("BAD_MIME",
                          "Allowed types: JPEG, PNG, WebP, PDF")
    if size_bytes <= 0 or size_bytes > min(DOC_MAX_BYTES,
                                           settings.kyc_max_doc_mb * 1024 * 1024):
        raise bad_request("FILE_TOO_LARGE",
                          f"Max {settings.kyc_max_doc_mb} MB per document")
    dt = db.get(KycDocumentType, doc_type_id)
    if dt is None or not dt.is_active:
        raise bad_request("BAD_DOC_TYPE", "Unknown document type")
    if property_id is not None:
        prop = db.get(Property, property_id)
        if prop is None or prop.owner_id != user.id:
            raise forbidden("Not your listing")
        if _v(dt.applies_to) == "USER":
            raise bad_request("BAD_DOC_TYPE",
                              "This document type is for identity, not property")

    doc = KycDocument(
        owner_user_id=user.id, property_id=property_id,
        doc_type_id=doc_type_id,
        storage_key=new_storage_key(user.id, filename),
        mime=mime, size_bytes=size_bytes,
    )
    db.add(doc)
    db.flush()
    _history(db, ST.KYC_DOCUMENT, doc.id, None, "PENDING", user.id)
    db.commit()
    return doc


def upload_document_content(db: Session, user: User, doc_id: int,
                            data: bytes, content_type: str | None) -> KycDocument:
    doc = db.get(KycDocument, doc_id)
    if doc is None:
        raise not_found("DOC_NOT_FOUND", "Document not found")
    if doc.owner_user_id != user.id:
        raise forbidden("Not your document")
    if not data or len(data) > settings.kyc_max_doc_mb * 1024 * 1024:
        raise bad_request("FILE_TOO_LARGE", "File empty or too large")
    sniffed = sniff_mime(data, doc.mime)
    if sniffed != doc.mime:
        raise bad_request("MIME_MISMATCH",
                          f"File content is {sniffed}, expected {doc.mime}")

    digest = sha256_hex(data)
    # same file already used by a *different* account → reuse/tamper signal
    reused = db.scalar(
        select(KycDocument).where(KycDocument.sha256 == digest,
                                  KycDocument.owner_user_id != user.id)
    )
    doc.sha256 = digest
    doc.size_bytes = len(data)
    get_storage().put(doc.storage_key, data)
    doc.uploaded_at = _now()
    doc.status = "PENDING"
    if reused is not None:
        broker_service.record_event(
            db, user=user, event_type="SUSPECTED_TAMPERED_DOC",
            delta=_cfg(db)["score.suspected_tampered_doc"],
            reason="Identical file bytes already uploaded by another account",
            property_id=doc.property_id,
            dedup_key=f"doc-sha:{doc.id}",
        )
        db.add(KycCheck(user_id=user.id, check_type="DOC_REUSE",
                        provider="internal", result="FAIL",
                        detail={"conflict_doc_id": reused.id}))

    # re-submitting a NEEDS_MORE_INFO claim/profile happens automatically
    if doc.property_id:
        claim = _latest_claim(db, doc.property_id)
        if claim and _v(claim.status) == "NEEDS_MORE_INFO":
            _set_status(db, claim, ST.OWNERSHIP_CLAIM, OCS.SUBMITTED, user.id)
    db.commit()
    return doc


def list_documents(db: Session, user_id: int,
                   property_id: int | None = None) -> list[KycDocument]:
    q = select(KycDocument).where(KycDocument.owner_user_id == user_id)
    if property_id is not None:
        q = q.where(KycDocument.property_id == property_id)
    return list(db.scalars(q.order_by(KycDocument.id.desc())).all())


# ---------------- submit for review ----------------

def submit_kyc(db: Session, user: User) -> KycProfile:
    profile = get_profile(db, user.id)
    if profile is None or not profile.pan_hash:
        raise bad_request("KYC_INCOMPLETE", "Complete PAN verification first")
    st = _v(profile.status)
    if st in ("AUTO_VERIFIED", "APPROVED"):
        return profile
    if st == "SUBMITTED":
        return profile
    if st not in ("IN_PROGRESS", "NEEDS_MORE_INFO", "REJECTED"):
        raise bad_request("INVALID_KYC_TRANSITION",
                          f"Cannot submit KYC in state {st}")

    _set_status(db, profile, ST.KYC_PROFILE, KPS.SUBMITTED, user.id)

    failed = db.scalar(
        select(func.count(KycCheck.id)).where(
            KycCheck.user_id == user.id, KycCheck.result == "FAIL")
    )
    needs_review = db.scalar(
        select(func.count(KycCheck.id)).where(
            KycCheck.user_id == user.id, KycCheck.result == "REVIEW")
    )
    if failed or needs_review:
        _set_status(db, profile, ST.KYC_PROFILE, KPS.UNDER_REVIEW, user.id,
                    "Routed to manual review by auto-checks")
    else:
        _set_status(db, profile, ST.KYC_PROFILE, KPS.AUTO_VERIFIED, user.id,
                    "All checks passed")
        profile.verified_at = _now()
    _sync_user_kyc(db, profile, user)
    db.commit()
    return profile


# ---------------- ownership claims ----------------

def _latest_claim(db: Session, property_id: int) -> PropertyOwnershipClaim | None:
    return db.scalar(
        select(PropertyOwnershipClaim)
        .where(PropertyOwnershipClaim.property_id == property_id)
        .order_by(PropertyOwnershipClaim.id.desc()).limit(1)
    )


def get_claim(db: Session, property_id: int) -> PropertyOwnershipClaim | None:
    return _latest_claim(db, property_id)


def _name_score(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 0
    na, nb = " ".join(a.upper().split()), " ".join(b.upper().split())
    if na == nb:
        return 100
    return round(difflib.SequenceMatcher(None, na, nb).ratio() * 100)


def create_claim(db: Session, user: User, prop: Property, claim_type_id: int,
                 owner_name_on_doc: str) -> PropertyOwnershipClaim:
    if prop.owner_id != user.id:
        raise forbidden("Not your listing")
    if user.kyc_level < 1:
        raise ApiError(403, "KYC_REQUIRED",
                       "Complete identity KYC before declaring ownership")
    existing = _latest_claim(db, prop.id)
    if existing and _v(existing.status) in {s.value for s in OPEN_CLAIM_STATES | {OCS.APPROVED}}:
        raise bad_request("CLAIM_EXISTS",
                          f"An ownership claim is already {_v(existing.status).lower()}")
    ct = db.get(SellerClaimType, claim_type_id)
    if ct is None or not ct.is_active:
        raise bad_request("BAD_CLAIM_TYPE", "Unknown claim type")

    claim = PropertyOwnershipClaim(
        property_id=prop.id, claim_type_id=claim_type_id,
        owner_name_on_doc=owner_name_on_doc.strip(),
    )
    db.add(claim)
    db.flush()
    _history(db, ST.OWNERSHIP_CLAIM, claim.id, None, "SUBMITTED", user.id)

    profile = get_profile(db, user.id)
    score = _name_score(owner_name_on_doc,
                        profile.name_as_per_pan if profile else user.name)
    claim.name_match_score = score
    if score < 60 and ct.code in ("INDIVIDUAL_OWNER", "CO_OWNER"):
        broker_service.record_event(
            db, user=user, event_type="KYC_NAME_MISMATCH",
            delta=_cfg(db)["score.kyc_name_mismatch"],
            reason=f"Claim owner name score {score} vs KYC name",
            property_id=prop.id, dedup_key=f"claim-namematch:{claim.id}",
        )
    db.commit()
    return claim


# ---------------- reviewer queue & decisions ----------------

def _subject(db, subject_type: str, subject_id: int):
    if subject_type == "KYC_PROFILE":
        obj = db.get(KycProfile, subject_id)
    else:
        obj = db.get(PropertyOwnershipClaim, subject_id)
    if obj is None:
        raise not_found("CASE_NOT_FOUND", "Case not found")
    return obj


def _case_owner(db, subject_type: str, subject) -> User:
    if subject_type == "KYC_PROFILE":
        return db.get(User, subject.user_id)
    prop = db.get(Property, subject.property_id)
    return db.get(User, prop.owner_id)


def _needs_maker_checker(db, subject_type: str, subject, owner: User) -> bool:
    threshold = _cfg(db)["kyc.maker_checker_risk_threshold"]
    if owner.risk_score >= threshold:
        return True
    if subject_type == "OWNERSHIP_CLAIM":
        ct = db.get(SellerClaimType, subject.claim_type_id)
        if ct and ct.requires_admin_review:
            return True
        if subject.name_match_score is not None and subject.name_match_score < 60:
            return True
    else:
        if db.scalar(select(func.count(KycCheck.id)).where(
                KycCheck.user_id == owner.id, KycCheck.result == "REVIEW")):
            return True
    return False


def decide(db: Session, reviewer: User, subject_type: str, subject_id: int,
           decision: str, reason_id: int | None,
           notes: str | None) -> dict:
    subject = _subject(db, subject_type, subject_id)
    owner = _case_owner(db, subject_type, subject)
    if owner.id == reviewer.id:
        raise forbidden("Reviewers cannot decide their own case")

    st = _v(subject.status)
    dec = RD(decision)
    is_admin = any(r.role.code == "ADMIN" for r in reviewer.roles)
    maker_checker = _needs_maker_checker(db, subject_type, subject, owner)

    if dec == RD.REJECT and reason_id is None:
        raise bad_request("REASON_REQUIRED", "Rejections need a reason_id")

    # reviewer recommending on a high-risk case → pending admin finalize
    if dec == RD.APPROVE and maker_checker and not is_admin:
        dec = RD.RECOMMEND_APPROVE

    db.add(KycReview(
        subject_type=subject_type, subject_id=subject_id,
        reviewer_id=reviewer.id, decision=dec,
        reason_id=reason_id, notes=notes,
    ))
    log_admin_action(db, reviewer, f"KYC_{dec.value}", subject_type.lower(),
                     subject_id, notes,
                     {"reason_id": reason_id, "maker_checker": maker_checker})

    if dec == RD.RECOMMEND_APPROVE:
        subject.reviewed_by = reviewer.id
        db.commit()
        return {"status": st, "pending": "ADMIN_FINALIZE"}

    if dec == RD.APPROVE:
        if subject_type == "KYC_PROFILE":
            if st not in ("UNDER_REVIEW", "AUTO_VERIFIED", "SUBMITTED"):
                raise bad_request("INVALID_KYC_TRANSITION",
                                  f"Cannot approve profile in {st}")
            if st == "SUBMITTED":
                _set_status(db, subject, ST.KYC_PROFILE, KPS.UNDER_REVIEW,
                            reviewer.id)
            _set_status(db, subject, ST.KYC_PROFILE, KPS.APPROVED, reviewer.id)
            subject.verified_at = _now()
            subject.reviewed_by = reviewer.id
            subject.finalized_by = reviewer.id
            _sync_user_kyc(db, subject, owner)
        else:
            if st == "SUBMITTED":
                _set_status(db, subject, ST.OWNERSHIP_CLAIM, OCS.UNDER_REVIEW,
                            reviewer.id)
            _set_status(db, subject, ST.OWNERSHIP_CLAIM, OCS.APPROVED,
                        reviewer.id)
            subject.reviewed_by = reviewer.id
            subject.finalized_by = reviewer.id
            prop = db.get(Property, subject.property_id)
            prop.verification_level = 2
            prop.is_owner_verified = True
            prop.ownership_verified_at = _now()
        db.commit()
        return {"status": _v(subject.status)}

    if dec == RD.REJECT:
        if subject_type == "KYC_PROFILE":
            if st == "SUBMITTED":
                _set_status(db, subject, ST.KYC_PROFILE, KPS.UNDER_REVIEW,
                            reviewer.id)
            _set_status(db, subject, ST.KYC_PROFILE, KPS.REJECTED,
                        reviewer.id)
            subject.reviewed_by = reviewer.id
            subject.finalized_by = reviewer.id
            _sync_user_kyc(db, subject, owner)
            _risk_on_reject(db, owner, reason_id, None)
        else:
            if st == "SUBMITTED":
                _set_status(db, subject, ST.OWNERSHIP_CLAIM, OCS.UNDER_REVIEW,
                            reviewer.id)
            _set_status(db, subject, ST.OWNERSHIP_CLAIM, OCS.REJECTED,
                        reviewer.id)
            subject.reviewed_by = reviewer.id
            subject.finalized_by = reviewer.id
            _risk_on_reject(db, owner, reason_id, subject.property_id)
        db.commit()
        return {"status": _v(subject.status)}

    raise bad_request("BAD_DECISION", "decision must be APPROVE or REJECT")


def _risk_on_reject(db, owner: User, reason_id: int | None,
                    property_id: int | None) -> None:
    code = None
    if reason_id:
        r = db.get(KycRejectionReason, reason_id)
        code = r.code if r else None
    if code == "SUSPECTED_TAMPERED":
        broker_service.record_event(
            db, user=owner, event_type="SUSPECTED_TAMPERED_DOC",
            delta=_cfg(db)["score.suspected_tampered_doc"],
            reason="Document rejected as suspected tampered",
            property_id=property_id,
            dedup_key=f"tampered:{owner.id}:{property_id or 0}",
        )
    elif code == "ADDRESS_MISMATCH":
        broker_service.record_event(
            db, user=owner, event_type="KYC_ADDRESS_MISMATCH",
            delta=_cfg(db)["score.kyc_address_mismatch"],
            reason="Ownership doc address mismatch confirmed",
            property_id=property_id,
            dedup_key=f"addr-mismatch:{owner.id}:{property_id or 0}",
        )


def request_info(db: Session, reviewer: User, subject_type: str,
                 subject_id: int, reason_id: int | None,
                 notes: str | None) -> dict:
    if not notes:
        raise bad_request("NOTE_REQUIRED",
                          "Request-more-info needs a note for the seller")
    subject = _subject(db, subject_type, subject_id)
    owner = _case_owner(db, subject_type, subject)
    if owner.id == reviewer.id:
        raise forbidden("Reviewers cannot act on their own case")

    if subject_type == "KYC_PROFILE":
        if _v(subject.status) == "SUBMITTED":
            _set_status(db, subject, ST.KYC_PROFILE, KPS.UNDER_REVIEW,
                        reviewer.id)
        _set_status(db, subject, ST.KYC_PROFILE, KPS.NEEDS_MORE_INFO,
                    reviewer.id, notes)
        _sync_user_kyc(db, subject, owner)
    else:
        if _v(subject.status) == "SUBMITTED":
            _set_status(db, subject, ST.OWNERSHIP_CLAIM, OCS.UNDER_REVIEW,
                        reviewer.id)
        _set_status(db, subject, ST.OWNERSHIP_CLAIM, OCS.NEEDS_MORE_INFO,
                    reviewer.id, notes)

    db.add(KycReview(
        subject_type=subject_type, subject_id=subject_id,
        reviewer_id=reviewer.id, decision=RD.REQUEST_INFO,
        reason_id=reason_id, notes=notes,
    ))
    log_admin_action(db, reviewer, "KYC_REQUEST_INFO", subject_type.lower(),
                     subject_id, notes)
    db.commit()
    return {"status": _v(subject.status)}


def escalate(db: Session, reviewer: User, subject_type: str, subject_id: int,
             notes: str | None) -> dict:
    subject = _subject(db, subject_type, subject_id)
    owner = _case_owner(db, subject_type, subject)
    if owner.id == reviewer.id:
        raise forbidden("Reviewers cannot act on their own case")
    if subject_type == "KYC_PROFILE" and _v(subject.status) == "SUBMITTED":
        _set_status(db, subject, ST.KYC_PROFILE, KPS.UNDER_REVIEW, reviewer.id)
    if subject_type == "OWNERSHIP_CLAIM" and _v(subject.status) == "SUBMITTED":
        _set_status(db, subject, ST.OWNERSHIP_CLAIM, OCS.UNDER_REVIEW,
                    reviewer.id)
    db.add(KycReview(
        subject_type=subject_type, subject_id=subject_id,
        reviewer_id=reviewer.id, decision=RD.ESCALATE, notes=notes,
    ))
    log_admin_action(db, reviewer, "KYC_ESCALATE", subject_type.lower(),
                     subject_id, notes)
    db.commit()
    return {"status": _v(subject.status), "escalated": True}


# ---------------- queue / case view ----------------

def review_queue(db: Session, status: str | None, claim_type: str | None,
                 min_risk: int | None, sla_breached: bool | None,
                 page: int, limit: int) -> tuple[list[dict], int]:
    cfg = _cfg(db)
    sla_hours = cfg["kyc.review_sla_hours"]
    items: list[dict] = []

    pq = (select(KycProfile, User)
          .join(User, User.id == KycProfile.user_id)
          .where(KycProfile.status.in_(["SUBMITTED", "UNDER_REVIEW",
                                       "NEEDS_MORE_INFO"])))
    if status:
        pq = pq.where(KycProfile.status == status)
    for profile, u in db.execute(pq).all():
        age_h = (_now() - profile.updated_at).total_seconds() / 3600
        items.append({
            "subject_type": "KYC_PROFILE", "subject_id": profile.id,
            "user_id": u.id, "user_name": u.name,
            "status": _v(profile.status), "risk_score": u.risk_score,
            "claim_type": None, "city": None,
            "age_hours": round(age_h, 1),
            "sla_breached": age_h > sla_hours,
            "requires_admin_review": _needs_maker_checker(
                db, "KYC_PROFILE", profile, u),
            "created_at": profile.created_at,
        })

    cq = (select(PropertyOwnershipClaim, User, Property)
          .join(Property, Property.id == PropertyOwnershipClaim.property_id)
          .join(User, User.id == Property.owner_id)
          .where(PropertyOwnershipClaim.status.in_(
              ["SUBMITTED", "UNDER_REVIEW", "NEEDS_MORE_INFO"])))
    if status:
        cq = cq.where(PropertyOwnershipClaim.status == status)
    for claim, u, prop in db.execute(cq).all():
        ct = db.get(SellerClaimType, claim.claim_type_id)
        if claim_type and (ct is None or ct.code != claim_type):
            continue
        loc = db.scalar(select(PropertyLocation).where(
            PropertyLocation.property_id == prop.id))
        age_h = (_now() - claim.updated_at).total_seconds() / 3600
        items.append({
            "subject_type": "OWNERSHIP_CLAIM", "subject_id": claim.id,
            "user_id": u.id, "user_name": u.name,
            "status": _v(claim.status), "risk_score": u.risk_score,
            "claim_type": ct.code if ct else None,
            "city": loc.city if loc else None,
            "age_hours": round(age_h, 1),
            "sla_breached": age_h > sla_hours,
            "requires_admin_review": _needs_maker_checker(
                db, "OWNERSHIP_CLAIM", claim, u),
            "created_at": claim.created_at,
        })

    if min_risk is not None:
        items = [i for i in items if i["risk_score"] >= min_risk]
    if sla_breached:
        items = [i for i in items if i["sla_breached"]]
    # priority: SLA breach → risk → age
    items.sort(key=lambda i: (not i["sla_breached"], -i["risk_score"],
                              i["age_hours"]))
    total = len(items)
    return items[(page - 1) * limit: page * limit], total


def case_detail(db: Session, subject_type: str, subject_id: int) -> dict:
    subject = _subject(db, subject_type, subject_id)
    owner = _case_owner(db, subject_type, subject)
    history = list(db.scalars(
        select(KycStatusHistory)
        .where(KycStatusHistory.subject_type == subject_type,
               KycStatusHistory.subject_id == subject_id)
        .order_by(KycStatusHistory.id)
    ))
    reviews = list(db.scalars(
        select(KycReview)
        .where(KycReview.subject_type == subject_type,
               KycReview.subject_id == subject_id)
        .order_by(KycReview.id)
    ))
    out = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "status": _v(subject.status),
        "owner": {"id": owner.id, "name": owner.name,
                  "risk_score": owner.risk_score,
                  "account_status": _v(owner.account_status),
                  "kyc_level": owner.kyc_level},
        "history": [{"old": h.old_status, "new": h.new_status,
                     "actor_id": h.actor_id, "reason": h.reason,
                     "at": h.created_at} for h in history],
        "reviews": [{"decision": _v(r.decision), "reviewer_id": r.reviewer_id,
                     "reason_id": r.reason_id, "notes": r.notes,
                     "at": r.created_at} for r in reviews],
        "requires_admin_review": _needs_maker_checker(
            db, subject_type, subject, owner),
    }
    if subject_type == "KYC_PROFILE":
        checks = list(db.scalars(select(KycCheck).where(
            KycCheck.user_id == owner.id).order_by(KycCheck.id)))
        docs = list_documents(db, owner.id, property_id=None)
        out["subject"] = {
            "name_as_per_pan": subject.name_as_per_pan,
            "pan_masked": mask_pan(decrypt(subject.pan_encrypted)),
            "aadhaar_last4": subject.aadhaar_last4,
            "level": subject.level,
            "verified_at": subject.verified_at,
            "submitted_at": subject.updated_at,
        }
        out["checks"] = [{"type": c.check_type, "result": c.result,
                          "provider": c.provider, "score": c.score,
                          "at": c.created_at} for c in checks]
        out["documents"] = [{"id": d.id, "doc_type": d.doc_type.code,
                             "status": _v(d.status), "mime": d.mime,
                             "size_bytes": d.size_bytes,
                             "uploaded_at": d.uploaded_at} for d in docs]
    else:
        prop = db.get(Property, subject.property_id)
        ct = db.get(SellerClaimType, subject.claim_type_id)
        docs = list_documents(db, owner.id, property_id=subject.property_id)
        profile = get_profile(db, owner.id)
        out["subject"] = {
            "property_id": subject.property_id,
            "property_title": prop.title,
            "property_status": _v(prop.status),
            "claim_type": ct.code if ct else None,
            "claim_type_name": ct.name if ct else None,
            "owner_name_on_doc": subject.owner_name_on_doc,
            "kyc_name": profile.name_as_per_pan if profile else None,
            "name_match_score": subject.name_match_score,
            "address_match_score": subject.address_match_score,
        }
        out["documents"] = [{"id": d.id, "doc_type": d.doc_type.code,
                             "status": _v(d.status), "mime": d.mime,
                             "size_bytes": d.size_bytes,
                             "uploaded_at": d.uploaded_at} for d in docs]
    # linked accounts: same phone never happens (unique), so same PAN hash
    # conflict + shared doc sha are the links we can actually show
    out["linked_accounts"] = _linked_accounts(db, owner)
    return out


def _linked_accounts(db: Session, owner: User) -> list[dict]:
    links = []
    profile = get_profile(db, owner.id)
    if profile and profile.pan_hash:
        for p in db.scalars(select(KycProfile).where(
                KycProfile.pan_hash == profile.pan_hash,
                KycProfile.user_id != owner.id)):
            links.append({"user_id": p.user_id, "signal": "SAME_PAN"})
    doc_hashes = select(KycDocument.sha256).where(
        KycDocument.owner_user_id == owner.id,
        KycDocument.sha256.is_not(None))
    for d in db.scalars(select(KycDocument).where(
            KycDocument.sha256.in_(doc_hashes),
            KycDocument.owner_user_id != owner.id)):
        links.append({"user_id": d.owner_user_id, "signal": "SAME_DOC_BYTES"})
    return links


# ---------------- serializers ----------------

def profile_out(db: Session, user: User) -> dict:
    profile = get_profile(db, user.id)
    docs = list_documents(db, user.id, property_id=None)
    out = {
        "kyc_status": _v(user.kyc_status),
        "kyc_level": user.kyc_level,
        "has_consent": has_consent(db, user.id),
        "profile": None,
        "documents": [{"id": d.id, "doc_type": d.doc_type.code,
                       "status": _v(d.status), "mime": d.mime,
                       "size_bytes": d.size_bytes,
                       "uploaded_at": d.uploaded_at} for d in docs],
    }
    if profile:
        last_reject = db.scalar(
            select(KycReview).where(
                KycReview.subject_type == "KYC_PROFILE",
                KycReview.subject_id == profile.id,
                KycReview.decision == "REJECT")
            .order_by(KycReview.id.desc()).limit(1))
        reason = db.get(KycRejectionReason, last_reject.reason_id) \
            if last_reject and last_reject.reason_id else None
        out["profile"] = {
            "status": _v(profile.status),
            "level": profile.level,
            "name_as_per_pan": profile.name_as_per_pan,
            "pan_masked": mask_pan(decrypt(profile.pan_encrypted)),
            "aadhaar_last4": profile.aadhaar_last4,
            "verified_at": profile.verified_at,
            "rejection_reason": reason.name if reason else None,
            "review_sla_hours": _cfg(db)["kyc.review_sla_hours"],
        }
    return out
