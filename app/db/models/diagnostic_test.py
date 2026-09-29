import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.diagnostic_centre import DiagnosticCentre


class DiagnosticTest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Catalogue entry (e.g. "CBC"). Price lives on CentreTest because centres price differently."""

    __tablename__ = "diagnostic_tests"

    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    centre_tests: Mapped[list["CentreTest"]] = relationship(back_populates="test")


class CentreTest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A test offered by a centre, with that centre's price. Source of truth for booking amounts."""

    __tablename__ = "centre_tests"
    __table_args__ = (
        UniqueConstraint("centre_id", "test_id", name="uq_centre_tests_centre_id_test_id"),
        CheckConstraint("price >= 0", name="price_non_negative"),
    )

    centre_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("diagnostic_centres.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    test_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("diagnostic_tests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())

    centre: Mapped[DiagnosticCentre] = relationship(back_populates="centre_tests")
    test: Mapped[DiagnosticTest] = relationship(back_populates="centre_tests")
