import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import PUBLISH_BLOCKING, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import bad_request, conflict
from app.core.ratelimit import rate_limit
from app.core.security import hash_token
from app.models import (
    Appeal,
    DocumentType,
    OtpVerification,
    PropertyStatusHistory,
    User,
    VerificationDocument,
)
from app.schemas.antifraud import (
    AppealIn,
    OwnerDeclarationIn,
    SendCodeIn,
    TermsAcceptIn,
    VerificationDocIn,
    VerifyCodeIn,
)
from app.schemas.common import PageParams, ok
from app.schemas.misc import DocumentOut
from app.services import broker_service as broker
from app.services import property_service as svc

router = APIRouter(tags=["verification"])

_CODE_TTL_MINUTES = 10
_CODE_MAX_ATTEMPTS = 5


def _v(x):
    return x.value if hasattr(x, "value") else x


def _listing_requirements(db: Session, user: User) -> list[str]:
    missing = []
    if not user.owner_declaration_at:
        missing.append("owner declaration")
    if not user.terms_accepted_at:
        missing.append("anti-broker terms acceptance")
    if not user.name or not user.phone:
        missing.append("profile name/phone")
    if broker.get_config(db).get("listing.require_verified_channel"):
        if not (user.phone_verified or user.email_verified):
            missing.append("phone or email verification")
    return missing


# ---------------- verification status ----------------

@router.get("/users/me/verification/status")
def verification_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Full verification + restriction state for the current user."""
    status = _v(user.account_status)
    missing = _listing_requirements(db, user)
    return ok(
        {
            "kyc_status": _v(user.kyc_status),
            "phone_verified": user.phone_verified,
            "email_verified": user.email_verified,
            "account_status": status,
            "account_status_reason": user.account_status_reason,
            "account_status_changed_at": user.account_status_changed_at,
            "owner_declaration": (
                {
                    "role": _v(user.owner_declaration_role),
                    "accepted_at": user.owner_declaration_at,
                }
                if user.owner_declaration_at
                else None
            ),
            "terms": {
                "accepted_version": user.terms_version,
                "accepted_at": user.terms_accepted_at,
                "current_version": broker.CURRENT_TERMS_VERSION,
            },
            "can_list": status not in PUBLISH_BLOCKING and not missing,
            "missing_requirements": missing,
        }
    )


@router.post("/users/me/verification/accept-terms")
def accept_terms(
    payload: TermsAcceptIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept the anti-broker Terms & Conditions (versioned)."""
    if payload.version != broker.CURRENT_TERMS_VERSION:
        raise bad_request(
            "TERMS_VERSION_MISMATCH",
            f"Current terms version is {broker.CURRENT_TERMS_VERSION}",
        )
    user.terms_version = payload.version
    user.terms_accepted_at = datetime.now(timezone.utc)
    db.commit()
    return ok({"terms_version": user.terms_version,
               "terms_accepted_at": user.terms_accepted_at})


@router.post("/users/me/verification/owner-declaration")
def owner_declaration(
    payload: OwnerDeclarationIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Declare listing as owner or authorized representative."""
    role = payload.role.upper()
    if role not in {"OWNER", "AUTHORIZED_REPRESENTATIVE"}:
        raise bad_request(
            "INVALID_DECLARATION",
            "role must be OWNER or AUTHORIZED_REPRESENTATIVE",
        )
    user.owner_declaration_role = role
    user.owner_declaration_at = datetime.now(timezone.utc)
    db.commit()
    return ok(
        {
            "owner_declaration_role": role,
            "owner_declaration_at": user.owner_declaration_at,
        }
    )


# ---------------- phone / email verification codes ----------------

@router.post(
    "/users/me/verification/send-code",
    dependencies=[Depends(rate_limit("send-code", 5, 300))],
)
def send_code(
    payload: SendCodeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a verification code to phone or email.

    NOTE: no SMS/e-mail provider is wired yet — in non-production the code is
    returned in the response so the flow is testable end-to-end.
    """
    channel = payload.channel.upper()
    if channel == "PHONE":
        identifier, purpose, otp_channel = user.phone, "PHONE_VERIFY", "SMS"
    elif channel == "EMAIL":
        if not user.email:
            raise bad_request("NO_EMAIL", "Add an email to your profile first")
        identifier, purpose, otp_channel = (
            user.email, "EMAIL_VERIFY", "EMAIL"
        )
    else:
        raise bad_request("INVALID_CHANNEL", "channel must be PHONE or EMAIL")

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        OtpVerification(
            user_id=user.id,
            identifier=identifier,
            channel=otp_channel,
            purpose=purpose,
            code_hash=hash_token(code),
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=_CODE_TTL_MINUTES),
        )
    )
    db.commit()
    data = {"sent": True, "channel": channel, "expires_in": _CODE_TTL_MINUTES * 60}
    if settings.app_env != "production":
        data["dev_code"] = code
    return ok(data)


@router.post("/users/me/verification/verify-code")
def verify_code(
    payload: VerifyCodeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify a code sent via send-code → marks phone/email verified."""
    channel = payload.channel.upper()
    identifier = user.phone if channel == "PHONE" else user.email
    purpose = f"{channel}_VERIFY"
    if channel not in {"PHONE", "EMAIL"} or not identifier:
        raise bad_request("INVALID_CHANNEL", "channel must be PHONE or EMAIL")

    row = db.scalar(
        select(OtpVerification)
        .where(
            OtpVerification.identifier == identifier,
            OtpVerification.purpose == purpose,
            OtpVerification.consumed_at.is_(None),
        )
        .order_by(OtpVerification.created_at.desc())
    )
    if row is None or row.expires_at < datetime.now(timezone.utc):
        raise bad_request("CODE_EXPIRED", "Request a new verification code")
    if row.attempts >= _CODE_MAX_ATTEMPTS:
        raise bad_request("TOO_MANY_ATTEMPTS", "Request a new verification code")
    if row.code_hash != hash_token(payload.code.strip()):
        row.attempts += 1
        db.commit()
        raise bad_request("INVALID_CODE", "Incorrect verification code")

    row.consumed_at = datetime.now(timezone.utc)
    if channel == "PHONE":
        user.phone_verified = True
    else:
        user.email_verified = True
    db.commit()
    return ok({"verified": True, "channel": channel})


# ---------------- property ownership verification ----------------

@router.post(
    "/properties/{property_id}/verification-documents",
    status_code=201,
    dependencies=[Depends(rate_limit("verif-doc", 10, 300))],
)
def submit_verification_document(
    property_id: int,
    payload: VerificationDocIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Attach an ownership document (sale deed, tax receipt…) to MY listing.

    file_url must point to storage the client uploaded to (S3 presigned flow).
    The file itself is only ever visible to the owner and admins.
    """
    prop = svc.get_owned_property(db, property_id, user)
    ext = payload.file_url.rsplit("?", 1)[0].rsplit(".", 1)[-1].lower()
    if ext not in {"pdf", "jpg", "jpeg", "png", "webp", "heic"}:
        raise bad_request(
            "INVALID_FILE_TYPE",
            "file_url must be a pdf or image (pdf, jpg, jpeg, png, webp, heic)",
        )
    doc_type = db.get(DocumentType, payload.document_type_id)
    if doc_type is None or not doc_type.is_active:
        raise bad_request("INVALID_DOC_TYPE", "Unknown document_type_id")
    if _v(doc_type.applies_to) not in {"PROPERTY", "BOTH"}:
        raise bad_request(
            "INVALID_DOC_TYPE",
            "This document type cannot be used for property verification",
        )
    doc = VerificationDocument(
        user_id=user.id,
        property_id=prop.id,
        document_type_id=doc_type.id,
        file_url=payload.file_url,
    )
    db.add(doc)
    db.commit()
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"))


@router.get("/properties/{property_id}/verification-status")
def property_verification_status(
    property_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Moderation state of MY listing: status, rejection reason, docs, flags."""
    prop = svc.get_owned_property(db, property_id, user)
    rejection = db.scalar(
        select(PropertyStatusHistory.reason)
        .where(
            PropertyStatusHistory.property_id == prop.id,
            PropertyStatusHistory.new_status == "REJECTED",
            PropertyStatusHistory.reason.is_not(None),
        )
        .order_by(PropertyStatusHistory.created_at.desc())
    )
    docs = db.scalars(
        select(VerificationDocument)
        .where(VerificationDocument.property_id == prop.id)
        .order_by(VerificationDocument.created_at.desc())
    ).all()
    from app.models import DuplicateMatch

    dup = db.scalar(
        select(DuplicateMatch.id).where(
            DuplicateMatch.property_id == prop.id,
            DuplicateMatch.status == "UNREVIEWED",
        )
    )
    return ok(
        {
            "property_id": prop.id,
            "status": _v(prop.status),
            "rejection_reason": rejection,
            "pending_review": _v(prop.status) == "PENDING_REVIEW",
            "flagged": _v(prop.status) == "FLAGGED",
            "duplicate_flag": dup is not None,
            "documents": [
                DocumentOut.model_validate(d).model_dump(mode="json")
                for d in docs
            ],
        }
    )


# ---------------- appeals ----------------

@router.post("/users/me/appeals", status_code=201)
def create_appeal(
    payload: AppealIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Appeal an account restriction or a flagged listing. One open at a time."""
    status = _v(user.account_status)
    can_appeal = status in {"UNDER_REVIEW", "TEMPORARILY_RESTRICTED", "SUSPENDED"}
    if payload.property_id is not None:
        prop = svc.get_owned_property(db, payload.property_id, user)
        can_appeal = can_appeal or _v(prop.status) in {"FLAGGED", "SUSPENDED"}
    if not can_appeal:
        raise bad_request(
            "NOTHING_TO_APPEAL",
            "Your account and listings are in good standing",
        )
    open_appeal = db.scalar(
        select(Appeal.id).where(
            Appeal.user_id == user.id, Appeal.status == "OPEN"
        )
    )
    if open_appeal:
        raise conflict("APPEAL_OPEN", "You already have an open appeal")
    appeal = Appeal(
        user_id=user.id,
        property_id=payload.property_id,
        message=payload.message,
    )
    db.add(appeal)
    db.commit()
    return ok(
        {
            "id": appeal.id,
            "status": _v(appeal.status),
            "created_at": appeal.created_at.isoformat(),
        }
    )


@router.get("/users/me/appeals")
def my_appeals(
    page_params: PageParams = Depends(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Appeal)
        .where(Appeal.user_id == user.id)
        .order_by(Appeal.created_at.desc())
        .offset(page_params.offset)
        .limit(page_params.limit)
    ).all()
    return ok(
        [
            {
                "id": a.id,
                "property_id": a.property_id,
                "message": a.message,
                "status": _v(a.status),
                "resolution_note": a.resolution_note,
                "created_at": a.created_at.isoformat(),
                "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
            }
            for a in rows
        ]
    )
