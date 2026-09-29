from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.diagnostic_test import CentreTest


class DiagnosticCentre(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "diagnostic_centres"

    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())

    centre_tests: Mapped[list["CentreTest"]] = relationship(back_populates="centre")
