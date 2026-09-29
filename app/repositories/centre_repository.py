"""Persistence for the catalogue: centres, tests and the centre<->test price list."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import CentreTest, DiagnosticCentre, DiagnosticTest


def _contains(column, term: str):  # noqa: ANN001, ANN202
    """Case-insensitive substring match with LIKE wildcards escaped."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


class CentreRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- centres -------------------------------------------------------------
    def list_centres(
        self, *, search: str | None, include_inactive: bool, limit: int, offset: int
    ) -> tuple[list[DiagnosticCentre], int]:
        query = select(DiagnosticCentre)
        if not include_inactive:
            query = query.where(DiagnosticCentre.is_active.is_(True))
        if search:
            query = query.where(
                _contains(DiagnosticCentre.name, search)
                | _contains(DiagnosticCentre.location, search)
            )
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        # id as tie-breaker => deterministic pages
        rows = self.db.scalars(
            query.order_by(DiagnosticCentre.name, DiagnosticCentre.id).limit(limit).offset(offset)
        ).all()
        return list(rows), total

    def get_centre(self, centre_id: uuid.UUID) -> DiagnosticCentre | None:
        return self.db.get(DiagnosticCentre, centre_id)

    def get_centre_by_name(self, name: str) -> DiagnosticCentre | None:
        return self.db.scalar(select(DiagnosticCentre).where(DiagnosticCentre.name == name))

    def add_centre(self, centre: DiagnosticCentre) -> DiagnosticCentre:
        self.db.add(centre)
        self.db.flush()
        return centre

    # --- tests ---------------------------------------------------------------
    def list_tests(
        self, *, search: str | None, limit: int, offset: int
    ) -> tuple[list[DiagnosticTest], int]:
        query = select(DiagnosticTest)
        if search:
            query = query.where(
                _contains(DiagnosticTest.name, search)
                | _contains(DiagnosticTest.description, search)
            )
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.scalars(
            query.order_by(DiagnosticTest.name, DiagnosticTest.id).limit(limit).offset(offset)
        ).all()
        return list(rows), total

    def get_test(self, test_id: uuid.UUID) -> DiagnosticTest | None:
        return self.db.get(DiagnosticTest, test_id)

    def get_test_by_name(self, name: str) -> DiagnosticTest | None:
        return self.db.scalar(select(DiagnosticTest).where(DiagnosticTest.name == name))

    def add_test(self, test: DiagnosticTest) -> DiagnosticTest:
        self.db.add(test)
        self.db.flush()
        return test

    # --- centre <-> test -----------------------------------------------------
    def list_centre_tests(
        self,
        centre_id: uuid.UUID,
        *,
        search: str | None,
        only_available: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[CentreTest], int]:
        query = (
            select(CentreTest)
            .join(DiagnosticTest, CentreTest.test_id == DiagnosticTest.id)
            .where(CentreTest.centre_id == centre_id)
        )
        if only_available:
            query = query.where(CentreTest.is_available.is_(True))
        if search:
            query = query.where(_contains(DiagnosticTest.name, search))
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.scalars(
            query.options(joinedload(CentreTest.test))
            .order_by(DiagnosticTest.name, CentreTest.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return list(rows), total

    def get_centre_test(self, centre_id: uuid.UUID, test_id: uuid.UUID) -> CentreTest | None:
        return self.db.scalar(
            select(CentreTest).where(
                CentreTest.centre_id == centre_id, CentreTest.test_id == test_id
            )
        )

    def list_offerings(self, test_id: uuid.UUID) -> list[CentreTest]:
        rows = self.db.scalars(
            select(CentreTest)
            .join(DiagnosticCentre, CentreTest.centre_id == DiagnosticCentre.id)
            .where(CentreTest.test_id == test_id, DiagnosticCentre.is_active.is_(True))
            .options(joinedload(CentreTest.centre))
            .order_by(CentreTest.price, DiagnosticCentre.name, CentreTest.id)
        ).all()
        return list(rows)

    def add_centre_test(self, centre_test: CentreTest) -> CentreTest:
        self.db.add(centre_test)
        self.db.flush()
        return centre_test
