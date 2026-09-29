"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-29 17:42:27.475999

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial schema (users, catalogue, bookings, payments, webhook_events)."""
    op.create_table(
        "diagnostic_centres",
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagnostic_centres")),
    )
    op.create_index(
        op.f("ix_diagnostic_centres_name"), "diagnostic_centres", ["name"], unique=False
    )
    op.create_table(
        "diagnostic_tests",
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagnostic_tests")),
        sa.UniqueConstraint("name", name=op.f("uq_diagnostic_tests_name")),
    )
    op.create_table(
        "users",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PROCESSED",
                "IGNORED",
                "FAILED",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_webhook_events_event_id")),
    )
    op.create_table(
        "centre_tests",
        sa.Column("centre_id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.CheckConstraint("price >= 0", name=op.f("ck_centre_tests_price_non_negative")),
        sa.ForeignKeyConstraint(
            ["centre_id"],
            ["diagnostic_centres.id"],
            name=op.f("fk_centre_tests_centre_id_diagnostic_centres"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["diagnostic_tests.id"],
            name=op.f("fk_centre_tests_test_id_diagnostic_tests"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_centre_tests")),
        sa.UniqueConstraint("centre_id", "test_id", name="uq_centre_tests_centre_id_test_id"),
    )
    op.create_index(op.f("ix_centre_tests_centre_id"), "centre_tests", ["centre_id"], unique=False)
    op.create_index(op.f("ix_centre_tests_test_id"), "centre_tests", ["test_id"], unique=False)
    op.create_table(
        "bookings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("centre_test_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "CONFIRMED",
                "FAILED",
                "CANCELLED",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.CheckConstraint("amount >= 0", name=op.f("ck_bookings_amount_non_negative")),
        sa.ForeignKeyConstraint(
            ["centre_test_id"],
            ["centre_tests.id"],
            name=op.f("fk_bookings_centre_test_id_centre_tests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_bookings_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
    )
    op.create_index(
        op.f("ix_bookings_centre_test_id"), "bookings", ["centre_test_id"], unique=False
    )
    op.create_index(
        "ix_bookings_user_id_created_at", "bookings", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_bookings_active_user_slot",
        "bookings",
        ["user_id", "centre_test_id", "appointment_at"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'CONFIRMED')"),
    )
    op.create_table(
        "payments",
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SUCCESS",
                "FAILED",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.CheckConstraint("amount >= 0", name=op.f("ck_payments_amount_non_negative")),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_payments_booking_id_bookings"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("provider_payment_id", name=op.f("uq_payments_provider_payment_id")),
    )
    op.create_index(op.f("ix_payments_booking_id"), "payments", ["booking_id"], unique=False)
    op.create_index(
        "ix_payments_status_created_at", "payments", ["status", "created_at"], unique=False
    )
    op.create_index(
        "uq_payments_active_per_booking",
        "payments",
        ["booking_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'SUCCESS')"),
    )


def downgrade() -> None:
    """Drop everything created by upgrade()."""
    op.drop_index(
        "uq_payments_active_per_booking",
        table_name="payments",
        postgresql_where=sa.text("status IN ('PENDING', 'SUCCESS')"),
    )
    op.drop_index("ix_payments_status_created_at", table_name="payments")
    op.drop_index(op.f("ix_payments_booking_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(
        "uq_bookings_active_user_slot",
        table_name="bookings",
        postgresql_where=sa.text("status IN ('PENDING', 'CONFIRMED')"),
    )
    op.drop_index("ix_bookings_user_id_created_at", table_name="bookings")
    op.drop_index(op.f("ix_bookings_centre_test_id"), table_name="bookings")
    op.drop_table("bookings")
    op.drop_index(op.f("ix_centre_tests_test_id"), table_name="centre_tests")
    op.drop_index(op.f("ix_centre_tests_centre_id"), table_name="centre_tests")
    op.drop_table("centre_tests")
    op.drop_table("webhook_events")
    op.drop_table("users")
    op.drop_table("diagnostic_tests")
    op.drop_index(op.f("ix_diagnostic_centres_name"), table_name="diagnostic_centres")
    op.drop_table("diagnostic_centres")
