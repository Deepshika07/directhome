"""billing: plans, user_subscriptions, payment_orders + FREE plan seeds

Revision ID: c4f7a1e3b8d2
Revises: b1f4e2c9d8a7
Create Date: 2026-11-22

- new enums: subscription_status, billing_source, payment_status
- new tables: plans, user_subscriptions, payment_orders
- seeds FREE_SELLER / FREE_BUYER plans matching today's free behavior
  (unlimited) — admins tighten limits or add paid plans from the panel
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4f7a1e3b8d2"
down_revision = "b1f4e2c9d8a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    postgresql.ENUM("ACTIVE", "EXPIRED", "CANCELLED",
                    name="subscription_status").create(
        op.get_bind(), checkfirst=True)
    postgresql.ENUM("PAYMENT", "ADMIN_GRANT", "TRIAL",
                    name="billing_source").create(
        op.get_bind(), checkfirst=True)
    postgresql.ENUM("CREATED", "PAID", "FAILED", "REFUNDED",
                    name="payment_status").create(
        op.get_bind(), checkfirst=True)

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("applies_to", sa.String(20), nullable=False),
        sa.Column("price_paise", sa.Integer(), server_default="0",
                  nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR",
                  nullable=False),
        sa.Column("duration_days", sa.Integer()),
        sa.Column("features", postgresql.JSONB(),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true",
                  nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0",
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "payment_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "plan_id", sa.Integer(),
            sa.ForeignKey("plans.id"), nullable=False,
        ),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR",
                  nullable=False),
        sa.Column("provider", sa.String(30), server_default="mock",
                  nullable=False),
        sa.Column("provider_ref", sa.String(120)),
        sa.Column(
            "status",
            postgresql.ENUM("CREATED", "PAID", "FAILED", "REFUNDED",
                            name="payment_status", create_type=False),
            server_default="CREATED", nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_payment_orders_user", "payment_orders", ["user_id"])

    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id", sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "plan_id", sa.Integer(),
            sa.ForeignKey("plans.id"), nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM("ACTIVE", "EXPIRED", "CANCELLED",
                            name="subscription_status", create_type=False),
            server_default="ACTIVE", nullable=False,
        ),
        sa.Column(
            "source",
            postgresql.ENUM("PAYMENT", "ADMIN_GRANT", "TRIAL",
                            name="billing_source", create_type=False),
            server_default="PAYMENT", nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "payment_order_id", sa.BigInteger(),
            sa.ForeignKey("payment_orders.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_subscriptions_user", "user_subscriptions",
                    ["user_id", "status"])
    op.create_index("ix_user_subscriptions_expires", "user_subscriptions",
                    ["expires_at"])

    # FREE plans mirror today's behavior (unlimited). Admins tighten limits
    # or create paid tiers from /admin/billing — no deploy needed.
    plans = sa.table(
        "plans",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("applies_to", sa.String),
        sa.column("price_paise", sa.Integer),
        sa.column("features", postgresql.JSONB),
        sa.column("sort_order", sa.SmallInteger),
    )
    op.bulk_insert(plans, [
        {"code": "FREE_SELLER", "name": "Free Seller", "applies_to": "SELLER",
         "description": "Default plan — unlimited live listings",
         "price_paise": 0,
         "features": {"max_active_listings": None, "boost": False},
         "sort_order": 1},
        {"code": "FREE_BUYER", "name": "Free Buyer", "applies_to": "BUYER",
         "description": "Default plan — unlimited owner contacts",
         "price_paise": 0,
         "features": {"contact_reveals": None},
         "sort_order": 2},
    ])


def downgrade() -> None:
    for t in ("payment_orders", "user_subscriptions", "plans"):
        op.drop_table(t)
    for name in ("payment_status", "billing_source", "subscription_status"):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
