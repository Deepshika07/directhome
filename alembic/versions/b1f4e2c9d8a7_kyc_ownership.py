"""kyc & ownership verification: identity profiles, claims, reviews, consent

Revision ID: b1f4e2c9d8a7
Revises: a8f3c21d9e04
Create Date: 2026-11-22

- users: kyc_level (L0 browse/draft, L1 submit, L2 ownership verified)
- properties: verification_level, ownership_verified_at
- new enums: kyc_profile_status, ownership_claim_status, review_decision,
  kyc_subject_type
- risk_event_type += DUPLICATE_PAN_HASH, KYC_NAME_MISMATCH,
  SUSPECTED_TAMPERED_DOC, KYC_ADDRESS_MISMATCH
- new tables: kyc_profiles, kyc_checks, kyc_documents,
  property_ownership_claims, kyc_reviews, kyc_status_history, consent_records
- lookups: seller_claim_types, kyc_document_types, kyc_rejection_reasons
- seeds: KYC_REVIEWER role, claim/doc/reason taxonomies, kyc.* fraud_config
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b1f4e2c9d8a7"
down_revision = "a8f3c21d9e04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- new enum types ----
    postgresql.ENUM(
        "NOT_STARTED", "IN_PROGRESS", "SUBMITTED", "AUTO_VERIFIED",
        "UNDER_REVIEW", "NEEDS_MORE_INFO", "APPROVED", "REJECTED", "EXPIRED",
        name="kyc_profile_status",
    ).create(op.get_bind(), checkfirst=True)
    postgresql.ENUM(
        "NOT_STARTED", "SUBMITTED", "UNDER_REVIEW", "NEEDS_MORE_INFO",
        "APPROVED", "REJECTED", name="ownership_claim_status",
    ).create(op.get_bind(), checkfirst=True)
    postgresql.ENUM(
        "APPROVE", "REJECT", "REQUEST_INFO", "ESCALATE", "RECOMMEND_APPROVE",
        name="review_decision",
    ).create(op.get_bind(), checkfirst=True)
    postgresql.ENUM(
        "KYC_PROFILE", "OWNERSHIP_CLAIM", "KYC_DOCUMENT",
        name="kyc_subject_type",
    ).create(op.get_bind(), checkfirst=True)

    # ---- new values on risk_event_type (outside transaction) ----
    with op.get_context().autocommit_block():
        for v in (
            "DUPLICATE_PAN_HASH", "KYC_NAME_MISMATCH",
            "SUSPECTED_TAMPERED_DOC", "KYC_ADDRESS_MISMATCH",
        ):
            op.execute(
                f"ALTER TYPE risk_event_type ADD VALUE IF NOT EXISTS '{v}'"
            )

    # ---- users / properties columns ----
    op.add_column(
        "users",
        sa.Column("kyc_level", sa.SmallInteger(), server_default="0",
                  nullable=False),
    )
    op.add_column(
        "properties",
        sa.Column("verification_level", sa.SmallInteger(),
                  server_default="0", nullable=False),
    )
    op.add_column(
        "properties",
        sa.Column("ownership_verified_at", sa.DateTime(timezone=True)),
    )

    kyc_subject = postgresql.ENUM(
        "KYC_PROFILE", "OWNERSHIP_CLAIM", "KYC_DOCUMENT",
        name="kyc_subject_type", create_type=False,
    )

    # ---- lookup tables ----
    op.create_table(
        "seller_claim_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("required_doc_codes", postgresql.JSONB(),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("requires_admin_review", sa.Boolean(),
                  server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true",
                  nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0",
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "kyc_document_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column(
            "applies_to",
            postgresql.ENUM("USER", "PROPERTY", "BOTH",
                            name="document_applies_to", create_type=False),
            server_default="BOTH", nullable=False,
        ),
        sa.Column("claim_type_codes", postgresql.JSONB(),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true",
                  nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0",
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "kyc_rejection_reasons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("applies_to", kyc_subject, server_default="KYC_PROFILE",
                  nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true",
                  nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0",
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # ---- primary tables ----
    op.create_table(
        "kyc_profiles",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            unique=True, nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "NOT_STARTED", "IN_PROGRESS", "SUBMITTED", "AUTO_VERIFIED",
                "UNDER_REVIEW", "NEEDS_MORE_INFO", "APPROVED", "REJECTED",
                "EXPIRED", name="kyc_profile_status", create_type=False,
            ),
            server_default="NOT_STARTED", nullable=False,
        ),
        sa.Column("level", sa.SmallInteger(), server_default="0",
                  nullable=False),
        sa.Column("name_as_per_pan", sa.String(120)),
        sa.Column("pan_encrypted", sa.Text()),
        sa.Column("pan_hash", sa.String(64)),
        sa.Column("aadhaar_last4", sa.String(4)),
        sa.Column("aadhaar_ref", sa.String(120)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "reviewed_by", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "finalized_by", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kyc_profiles_status", "kyc_profiles", ["status"])
    op.create_index("ix_kyc_profiles_pan_hash", "kyc_profiles", ["pan_hash"])

    op.create_table(
        "kyc_checks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("check_type", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), server_default="mock",
                  nullable=False),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("score", sa.SmallInteger()),
        sa.Column("provider_ref", sa.String(120)),
        sa.Column("detail", postgresql.JSONB(),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kyc_checks_user_id", "kyc_checks", ["user_id"])

    op.create_table(
        "kyc_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "owner_user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "property_id", sa.BigInteger(),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "doc_type_id", sa.Integer(),
            sa.ForeignKey("kyc_document_types.id"), nullable=False,
        ),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "VERIFIED", "REJECTED",
                "REQUIRES_ADDITIONAL_INFO",
                name="document_status", create_type=False,
            ),
            server_default="PENDING", nullable=False,
        ),
        sa.Column(
            "rejection_reason_id", sa.Integer(),
            sa.ForeignKey("kyc_rejection_reasons.id"),
        ),
        sa.Column("version", sa.SmallInteger(), server_default="1",
                  nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kyc_documents_owner", "kyc_documents",
                    ["owner_user_id"])
    op.create_index("ix_kyc_documents_property", "kyc_documents",
                    ["property_id"])
    op.create_index("ix_kyc_documents_sha256", "kyc_documents", ["sha256"])
    op.create_index("ix_kyc_documents_status", "kyc_documents", ["status"])

    op.create_table(
        "property_ownership_claims",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "property_id", sa.BigInteger(),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_type_id", sa.Integer(),
            sa.ForeignKey("seller_claim_types.id"), nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "NOT_STARTED", "SUBMITTED", "UNDER_REVIEW",
                "NEEDS_MORE_INFO", "APPROVED", "REJECTED",
                name="ownership_claim_status", create_type=False,
            ),
            server_default="SUBMITTED", nullable=False,
        ),
        sa.Column("owner_name_on_doc", sa.String(120), nullable=False),
        sa.Column("name_match_score", sa.SmallInteger()),
        sa.Column("address_match_score", sa.SmallInteger()),
        sa.Column(
            "reviewed_by", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "finalized_by", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ownership_claims_property",
                    "property_ownership_claims", ["property_id"])
    op.create_index("ix_ownership_claims_status",
                    "property_ownership_claims", ["status"])

    op.create_table(
        "kyc_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("subject_type", kyc_subject, nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "reviewer_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "decision",
            postgresql.ENUM(
                "APPROVE", "REJECT", "REQUEST_INFO", "ESCALATE",
                "RECOMMEND_APPROVE", name="review_decision",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "reason_id", sa.Integer(),
            sa.ForeignKey("kyc_rejection_reasons.id"),
        ),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kyc_reviews_subject", "kyc_reviews",
                    ["subject_type", "subject_id"])
    op.create_index("ix_kyc_reviews_reviewer", "kyc_reviews", ["reviewer_id"])

    op.create_table(
        "kyc_status_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("subject_type", kyc_subject, nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("old_status", sa.String(40)),
        sa.Column("new_status", sa.String(40), nullable=False),
        sa.Column(
            "actor_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("reason", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kyc_status_history_subject", "kyc_status_history",
                    ["subject_type", "subject_id"])

    op.create_table(
        "consent_records",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("purpose", sa.String(60), nullable=False),
        sa.Column("notice_version", sa.String(20), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("ip", sa.String(45)),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_consent_records_user", "consent_records",
                    ["user_id", "purpose"])

    # ---- seeds ----
    op.execute(
        "INSERT INTO roles (code, name) VALUES ('KYC_REVIEWER', 'KYC Reviewer') "
        "ON CONFLICT (code) DO NOTHING"
    )

    claim_types = sa.table(
        "seller_claim_types",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("required_doc_codes", postgresql.JSONB),
        sa.column("requires_admin_review", sa.Boolean),
        sa.column("sort_order", sa.SmallInteger),
    )
    op.bulk_insert(claim_types, [
        {"code": "INDIVIDUAL_OWNER", "name": "Individual Owner",
         "description": "Sole owner of the property",
         "required_doc_codes": ["SALE_DEED", "PROPERTY_TAX_RECEIPT",
                                "UTILITY_BILL"],
         "requires_admin_review": False, "sort_order": 1},
        {"code": "CO_OWNER", "name": "Co-owner",
         "description": "One of multiple owners; needs co-owner NOC",
         "required_doc_codes": ["SALE_DEED", "PROPERTY_TAX_RECEIPT",
                                "UTILITY_BILL", "OWNER_NOC"],
         "requires_admin_review": False, "sort_order": 2},
        {"code": "POA_HOLDER", "name": "Power of Attorney Holder",
         "description": "Acts for the owner via registered POA",
         "required_doc_codes": ["REGISTERED_POA", "OWNER_ID_PROOF",
                                "PROPERTY_TAX_RECEIPT"],
         "requires_admin_review": True, "sort_order": 3},
        {"code": "BUILDER_DEVELOPER", "name": "Builder / Developer",
         "description": "Developer entity listing own inventory",
         "required_doc_codes": ["GST_CERTIFICATE", "ALLOTMENT_LETTER",
                                "RERA_CERTIFICATE"],
         "requires_admin_review": True, "sort_order": 4},
        {"code": "PG_OPERATOR", "name": "PG Operator",
         "description": "Operates PG with owner's consent",
         "required_doc_codes": ["OWNER_NOC", "OWNER_ID_PROOF",
                                "PROPERTY_TAX_RECEIPT"],
         "requires_admin_review": False, "sort_order": 5},
    ])

    doc_applies = postgresql.ENUM(
        "USER", "PROPERTY", "BOTH",
        name="document_applies_to", create_type=False,
    )
    doc_types = sa.table(
        "kyc_document_types",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("applies_to", doc_applies),
        sa.column("claim_type_codes", postgresql.JSONB),
        sa.column("sort_order", sa.SmallInteger),
    )
    op.bulk_insert(doc_types, [
        {"code": "PAN_CARD", "name": "PAN Card", "applies_to": "USER",
         "claim_type_codes": [], "sort_order": 1},
        {"code": "AADHAAR_MASKED", "name": "Aadhaar (masked / offline e-KYC)",
         "applies_to": "USER", "claim_type_codes": [], "sort_order": 2},
        {"code": "PASSPORT", "name": "Passport", "applies_to": "USER",
         "claim_type_codes": [], "sort_order": 3},
        {"code": "DRIVING_LICENSE", "name": "Driving Licence",
         "applies_to": "USER", "claim_type_codes": [], "sort_order": 4},
        {"code": "VOTER_ID", "name": "Voter ID", "applies_to": "USER",
         "claim_type_codes": [], "sort_order": 5},
        {"code": "SALE_DEED", "name": "Registered Sale / Title Deed",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["INDIVIDUAL_OWNER", "CO_OWNER", "POA_HOLDER"],
         "sort_order": 10},
        {"code": "PROPERTY_TAX_RECEIPT", "name": "Property Tax Receipt",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["INDIVIDUAL_OWNER", "CO_OWNER", "POA_HOLDER",
                              "BUILDER_DEVELOPER", "PG_OPERATOR"],
         "sort_order": 11},
        {"code": "UTILITY_BILL", "name": "Electricity / Water Bill",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["INDIVIDUAL_OWNER", "CO_OWNER", "POA_HOLDER",
                              "PG_OPERATOR"],
         "sort_order": 12},
        {"code": "SOCIETY_SHARE_CERT_NOC",
         "name": "Society Share Certificate / NOC", "applies_to": "PROPERTY",
         "claim_type_codes": ["INDIVIDUAL_OWNER", "CO_OWNER"],
         "sort_order": 13},
        {"code": "ALLOTMENT_LETTER", "name": "Allotment Letter",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["INDIVIDUAL_OWNER", "BUILDER_DEVELOPER"],
         "sort_order": 14},
        {"code": "LAND_RECORD_EXTRACT",
         "name": "State Land Records (7/12, Property Card, Khata, Patta)",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["INDIVIDUAL_OWNER", "CO_OWNER"],
         "sort_order": 15},
        {"code": "RERA_CERTIFICATE", "name": "RERA Registration Certificate",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["BUILDER_DEVELOPER"], "sort_order": 20},
        {"code": "GST_CERTIFICATE", "name": "GST Certificate",
         "applies_to": "USER",
         "claim_type_codes": ["BUILDER_DEVELOPER"], "sort_order": 21},
        {"code": "CIN_PARTNERSHIP_DEED",
         "name": "Company CIN / Partnership Deed", "applies_to": "PROPERTY",
         "claim_type_codes": ["BUILDER_DEVELOPER"], "sort_order": 22},
        {"code": "REGISTERED_POA",
         "name": "Registered Power of Attorney", "applies_to": "PROPERTY",
         "claim_type_codes": ["POA_HOLDER"], "sort_order": 30},
        {"code": "OWNER_ID_PROOF", "name": "Owner ID Proof (POA / NRI / PG)",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["POA_HOLDER", "PG_OPERATOR"], "sort_order": 31},
        {"code": "OWNER_NOC", "name": "Owner No-Objection Certificate",
         "applies_to": "PROPERTY",
         "claim_type_codes": ["CO_OWNER", "PG_OPERATOR"], "sort_order": 32},
    ])

    reasons = sa.table(
        "kyc_rejection_reasons",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("applies_to", kyc_subject),
        sa.column("sort_order", sa.SmallInteger),
    )
    op.bulk_insert(reasons, [
        {"code": "PAN_INVALID", "name": "PAN could not be verified",
         "applies_to": "KYC_PROFILE", "sort_order": 1},
        {"code": "NAME_MISMATCH",
         "name": "Name does not match PAN records",
         "applies_to": "KYC_PROFILE", "sort_order": 2},
        {"code": "DOC_ILLEGIBLE", "name": "Document is unclear or illegible",
         "applies_to": "KYC_DOCUMENT", "sort_order": 3},
        {"code": "DOC_EXPIRED", "name": "Document has expired",
         "applies_to": "KYC_DOCUMENT", "sort_order": 4},
        {"code": "DUPLICATE_IDENTITY",
         "name": "PAN is already linked to another account",
         "applies_to": "KYC_PROFILE", "sort_order": 5},
        {"code": "SUSPECTED_TAMPERED",
         "name": "Document appears altered or tampered",
         "applies_to": "KYC_DOCUMENT", "sort_order": 6},
        {"code": "OWNER_NAME_MISMATCH",
         "name": "Owner name on document does not match your KYC name",
         "applies_to": "OWNERSHIP_CLAIM", "sort_order": 10},
        {"code": "ADDRESS_MISMATCH",
         "name": "Document address does not match the listing address",
         "applies_to": "OWNERSHIP_CLAIM", "sort_order": 11},
        {"code": "INSUFFICIENT_PROOF",
         "name": "Insufficient proof of ownership",
         "applies_to": "OWNERSHIP_CLAIM", "sort_order": 12},
        {"code": "POA_INVALID", "name": "POA document invalid or expired",
         "applies_to": "OWNERSHIP_CLAIM", "sort_order": 13},
    ])

    for key, value, desc in [
        ("score.duplicate_pan_hash", 60,
         "same PAN identity linked to another account"),
        ("score.kyc_name_mismatch", 25,
         "KYC name vs doc/account name mismatch"),
        ("score.suspected_tampered_doc", 70,
         "document flagged as tampered/reused"),
        ("score.kyc_address_mismatch", 20,
         "doc address vs listing address mismatch"),
        ("kyc.maker_checker_risk_threshold", 50,
         "risk score above which approvals need admin finalize"),
        ("kyc.review_sla_hours", 48,
         "target review turnaround for KYC queue"),
    ]:
        op.execute(sa.text(
            "INSERT INTO fraud_config (key, value, description) "
            "VALUES (:k, :v, :d) ON CONFLICT (key) DO NOTHING"
        ).bindparams(k=key, v=value, d=desc))


def downgrade() -> None:
    for t in (
        "consent_records", "kyc_status_history", "kyc_reviews",
        "property_ownership_claims", "kyc_documents", "kyc_checks",
        "kyc_profiles", "kyc_rejection_reasons", "kyc_document_types",
        "seller_claim_types",
    ):
        op.drop_table(t)
    op.drop_column("properties", "ownership_verified_at")
    op.drop_column("properties", "verification_level")
    op.drop_column("users", "kyc_level")
    op.execute("DELETE FROM roles WHERE code = 'KYC_REVIEWER'")
    op.execute(
        "DELETE FROM fraud_config WHERE key IN "
        "('score.duplicate_pan_hash','score.kyc_name_mismatch',"
        "'score.suspected_tampered_doc','score.kyc_address_mismatch',"
        "'kyc.maker_checker_risk_threshold','kyc.review_sla_hours')")
    for name in (
        "kyc_subject_type", "review_decision",
        "ownership_claim_status", "kyc_profile_status",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
