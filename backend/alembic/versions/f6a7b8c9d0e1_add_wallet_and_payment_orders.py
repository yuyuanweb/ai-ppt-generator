"""add wallet balance and payment orders

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "balance",
            sa.Numeric(precision=20, scale=2),
            server_default="0",
            nullable=False,
        ),
    )

    op.create_table(
        "payment_orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("out_trade_no", sa.String(length=64), nullable=False),
        sa.Column("order_type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("pay_amount", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("fee_rate", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("payment_type", sa.String(length=30), nullable=False),
        sa.Column("provider_key", sa.String(length=30), nullable=False),
        sa.Column("payment_trade_no", sa.String(length=128), nullable=True),
        sa.Column("pay_url", sa.Text(), nullable=True),
        sa.Column("qr_code", sa.Text(), nullable=True),
        sa.Column(
            "provider_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("recharge_code", sa.String(length=64), nullable=False),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_orders_out_trade_no"), "payment_orders", ["out_trade_no"], unique=True
    )
    op.create_index(
        op.f("ix_payment_orders_recharge_code"), "payment_orders", ["recharge_code"], unique=True
    )
    op.create_index(op.f("ix_payment_orders_user_id"), "payment_orders", ["user_id"], unique=False)
    op.create_index(op.f("ix_payment_orders_status"), "payment_orders", ["status"], unique=False)
    op.create_index(
        op.f("ix_payment_orders_expires_at"), "payment_orders", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_payment_orders_created_at"), "payment_orders", ["created_at"], unique=False
    )

    op.create_table(
        "balance_ledger",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("balance_after", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_balance_ledger_code"), "balance_ledger", ["code"], unique=True)
    op.create_index(op.f("ix_balance_ledger_user_id"), "balance_ledger", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_balance_ledger_order_id"), "balance_ledger", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_balance_ledger_created_at"), "balance_ledger", ["created_at"], unique=False
    )

    op.create_table(
        "payment_audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("operator", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_audit_logs_order_id"), "payment_audit_logs", ["order_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_payment_audit_logs_order_id"), table_name="payment_audit_logs")
    op.drop_table("payment_audit_logs")
    op.drop_index(op.f("ix_balance_ledger_created_at"), table_name="balance_ledger")
    op.drop_index(op.f("ix_balance_ledger_order_id"), table_name="balance_ledger")
    op.drop_index(op.f("ix_balance_ledger_user_id"), table_name="balance_ledger")
    op.drop_index(op.f("ix_balance_ledger_code"), table_name="balance_ledger")
    op.drop_table("balance_ledger")
    op.drop_index(op.f("ix_payment_orders_created_at"), table_name="payment_orders")
    op.drop_index(op.f("ix_payment_orders_expires_at"), table_name="payment_orders")
    op.drop_index(op.f("ix_payment_orders_status"), table_name="payment_orders")
    op.drop_index(op.f("ix_payment_orders_user_id"), table_name="payment_orders")
    op.drop_index(op.f("ix_payment_orders_recharge_code"), table_name="payment_orders")
    op.drop_index(op.f("ix_payment_orders_out_trade_no"), table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_column("users", "balance")
