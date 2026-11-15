"""antifraud & verification: account statuses, risk engine, dup detection, appeals, audit

Revision ID: a8f3c21d9e04
Revises: 5df9079cea83
Create Date: 2026-11-09

- users: account_status, risk_score, owner declaration, terms acceptance
- properties: address_fingerprint
- property_reports: validated, resolution_note, unique (property_id, reported_by)
- new enums: account_status, risk_event_type, duplicate_match_status,
  appeal_status, owner_declaration_role
- enum values added: property_status FLAGGED/SUSPENDED,
  document_status REQUIRES_ADDITIONAL_INFO, report_reason INCORRECT_OWNERSHIP
- new tables: user_risk_events, fraud_config, duplicate_matches, appeals,
  admin_audit_logs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a8f3c21d9e04"
down_revision = "5df9079cea83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- new enum types ----
    account_status = postgresql.ENUM(
        "ACTIVE", "UNDER_REVIEW", "TEMPORARILY_RESTRICTED", "SUSPENDED",
        "PERMANENTLY_BANNED", name="account_status",
    )
    account_status.create(op.get_bind(), checkfirst=True)
    risk_event_type = postgresql.ENUM(
        "UNRELATED_LISTINGS", "MULTI_ACCOUNT_PHONE",
        "CROSS_ACCOUNT_DUPLICATE_PROPERTY", "REPEATED_CONTENT",
        "OWNERSHIP_MISMATCH", "VALIDATED_COMPLAINTS", name="risk_event_type",
    )
    risk_event_type.create(op.get_bind(), checkfirst=True)
    duplicate_match_status = postgresql.ENUM(
        "UNREVIEWED", "CONFIRMED", "DISMISSED", name="duplicate_match_status",
    )
    duplicate_match_status.create(op.get_bind(), checkfirst=True)
    appeal_status = postgresql.ENUM(
        "OPEN", "APPROVED", "REJECTED", name="appeal_status",
    )
    appeal_status.create(op.get_bind(), checkfirst=True)
    owner_declaration_role = postgresql.ENUM(
        "OWNER", "AUTHORIZED_REPRESENTATIVE", name="owner_declaration_role",
    )
    owner_declaration_role.create(op.get_bind(), checkfirst=True)

    # ---- new values on existing enums (must run outside a transaction) ----
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE property_status ADD VALUE IF NOT EXISTS 'FLAGGED'")
        op.execute("ALTER TYPE property_status ADD VALUE IF NOT EXISTS 'SUSPENDED'")
        op.execute(
            "ALTER TYPE document_status ADD VALUE IF NOT EXISTS 'REQUIRES_ADDITIONAL_INFO'"
        )
        op.execute(
            "ALTER TYPE report_reason ADD VALUE IF NOT EXISTS 'INCORRECT_OWNERSHIP'"
        )

    # ---- users columns ----
    op.add_column(
        "users",
        sa.Column(
            "account_status",
            postgresql.ENUM(
                "ACTIVE", "UNDER_REVIEW", "TEMPORARILY_RESTRICTED",
                "SUSPENDED", "PERMANENTLY_BANNED", name="account_status",
                create_type=False,
            ),
            server_default="ACTIVE", nullable=False,
        ),
    )
    op.add_column(
        "users", sa.Column("account_status_reason", sa.String(255))
    )
    op.add_column(
        "users",
        sa.Column("account_status_changed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "users",
        sa.Column("risk_score", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "owner_declaration_role",
            postgresql.ENUM("OWNER", "AUTHORIZED_REPRESENTATIVE",
                    name="owner_declaration_role", create_type=False),
        ),
    )
    op.add_column(
        "users",
        sa.Column("owner_declaration_at", sa.DateTime(timezone=True)),
    )
    op.add_column("users", sa.Column("terms_version", sa.String(20)))
    op.add_column(
        "users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True))
    )

    # ---- properties: normalized-address fingerprint for dup detection ----
    op.add_column(
        "properties", sa.Column("address_fingerprint", sa.String(64))
    )
    op.create_index(
        "ix_properties_address_fingerprint",
        "properties",
        ["address_fingerprint"],
    )

    # ---- property_reports: dedup + validation ----
    op.add_column(
        "property_reports", sa.Column("resolution_note", sa.Text())
    )
    op.add_column(
        "property_reports",
        sa.Column("validated", sa.Boolean(), server_default="false",
                  nullable=False),
    )
    op.create_unique_constraint(
        "uq_report_property_reporter",
        "property_reports",
        ["property_id", "reported_by"],
    )

    # ---- new tables ----
    op.create_table(
        "user_risk_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                "UNRELATED_LISTINGS", "MULTI_ACCOUNT_PHONE",
                "CROSS_ACCOUNT_DUPLICATE_PROPERTY", "REPEATED_CONTENT",
                "OWNERSHIP_MISMATCH", "VALIDATED_COMPLAINTS",
                name="risk_event_type", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("score_delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column(
            "property_id", sa.BigInteger(),
            sa.ForeignKey("properties.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "related_user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "report_id", sa.BigInteger(),
            sa.ForeignKey("property_reports.id", ondelete="SET NULL"),
        ),
        sa.Column("dedup_key", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_user_risk_events_user_id", "user_risk_events", ["user_id"]
    )
    op.create_index(
        "ix_user_risk_events_created_at", "user_risk_events", ["created_at"]
    )

    op.create_table(
        "fraud_config",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column(
            "updated_by", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    op.create_table(
        "duplicate_matches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "property_id", sa.BigInteger(),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matched_property_id", sa.BigInteger(),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "matched_owner_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("score", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "signals", postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM("UNREVIEWED", "CONFIRMED", "DISMISSED",
                    name="duplicate_match_status", create_type=False),
            server_default="UNREVIEWED", nullable=False,
        ),
        sa.Column(
            "reviewed_by", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "property_id", "matched_property_id", name="uq_duplicate_pair"
        ),
    )
    op.create_index(
        "ix_duplicate_matches_status", "duplicate_matches", ["status"]
    )

    op.create_table(
        "appeals",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "property_id", sa.BigInteger(),
            sa.ForeignKey("properties.id", ondelete="SET NULL"),
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "status", postgresql.ENUM("OPEN", "APPROVED", "REJECTED",
                              name="appeal_status", create_type=False),
            server_default="OPEN", nullable=False,
        ),
        sa.Column(
            "reviewed_by", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("resolution_note", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_appeals_user_id", "appeals", ["user_id"])
    op.create_index("ix_appeals_status", "appeals", ["status"])

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "admin_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(60), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column(
            "extra", postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_admin_audit_logs_admin_id", "admin_audit_logs", ["admin_id"]
    )
    op.create_index(
        "ix_admin_audit_logs_target",
        "admin_audit_logs",
        ["target_type", "target_id"],
    )
    op.create_index(
        "ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"]
    )

    # ---- seed fraud engine defaults (editable via /admin/fraud/config) ----
    fraud_config = sa.table(
        "fraud_config",
        sa.column("key", sa.String), sa.column("value", sa.Integer),
        sa.column("description", sa.String),
    )
    op.bulk_insert(
        fraud_config,
        [
            {"key": "score.unrelated_listings", "value": 30,
             "description": "5+ listings across unrelated localities"},
            {"key": "score.multi_account_phone", "value": 40,
             "description": "same phone on multiple accounts"},
            {"key": "score.cross_account_duplicate_property", "value": 35,
             "description": "same address under different accounts"},
            {"key": "score.repeated_content", "value": 20,
             "description": "reused images/descriptions"},
            {"key": "score.ownership_mismatch", "value": 50,
             "description": "ownership details mismatch"},
            {"key": "score.validated_complaints", "value": 40,
             "description": "multiple validated fraud complaints"},
            {"key": "threshold.flag_for_review", "value": 30,
             "description": "score → UNDER_REVIEW"},
            {"key": "threshold.restrict", "value": 50,
             "description": "score → TEMPORARILY_RESTRICTED"},
            {"key": "threshold.suspend", "value": 70,
             "description": "score → SUSPENDED"},
            {"key": "listing.unrelated_count", "value": 5,
             "description": "distinct localities to trigger spread rule"},
            {"key": "report.flag_threshold", "value": 3,
             "description": "open reports to flag a listing"},
            {"key": "report.validated_threshold", "value": 2,
             "description": "validated fraud reports to score owner"},
            {"key": "content.similarity_pct", "value": 85,
             "description": "title/description similarity %"},
            {"key": "listing.require_verified_channel", "value": 0,
             "description": "1 = require phone/email verified to list"},
        ],
    )


def downgrade() -> None:
    op.drop_table("admin_audit_logs")
    op.drop_table("appeals")
    op.drop_table("duplicate_matches")
    op.drop_table("fraud_config")
    op.drop_table("user_risk_events")
    op.drop_constraint(
        "uq_report_property_reporter", "property_reports", type_="unique"
    )
    op.drop_column("property_reports", "validated")
    op.drop_column("property_reports", "resolution_note")
    op.drop_index("ix_properties_address_fingerprint", "properties")
    op.drop_column("properties", "address_fingerprint")
    for col in (
        "terms_accepted_at", "terms_version", "owner_declaration_at",
        "owner_declaration_role", "risk_score", "account_status_changed_at",
        "account_status_reason", "account_status",
    ):
        op.drop_column("users", col)
    for name in (
        "owner_declaration_role", "appeal_status", "duplicate_match_status",
        "risk_event_type", "account_status",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
