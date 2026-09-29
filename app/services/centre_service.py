"""Read-mostly catalogue service. Reads go through Redis (when available); writes invalidate it.

Booking never uses cached data for prices: BookingService reads centre_tests straight from
PostgreSQL, so a stale cache can never cause a wrong charge.
"""

import logging
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.cache import CATALOGUE_NAMESPACE, Cache
from app.core.exceptions import (
    CentreNotFoundError,
    CentreTestNotFoundError,
    DiagnosticTestNotFoundError,
)
from app.db.models import CentreTest
from app.repositories.centre_repository import CentreRepository
from app.schemas.centre import CentreRead, CentreTestRead
from app.schemas.test import CentreOffering, DiagnosticTestDetail, DiagnosticTestRead
from app.utils.pagination import Page, PaginationParams

logger = logging.getLogger(__name__)


def _normalize_search(search: str | None) -> str | None:
    """Trim + lowercase so "Apollo " and "apollo" share one cache entry; blank means no filter."""
    if not search:
        return None
    return search.strip().lower() or None


class CentreService:
    def __init__(self, db: Session, cache: Cache | None = None) -> None:
        self.db = db
        self.repo = CentreRepository(db)
        self.cache = cache

    # --- centres (cached) ----------------------------------------------------
    def list_centres(
        self, *, search: str | None, include_inactive: bool, page: PaginationParams
    ) -> Page[CentreRead]:
        search = _normalize_search(search)
        key = ("centres", search or "", include_inactive, page.limit, page.offset)
        cached = self._cache_get(*key)
        if cached:
            return Page[CentreRead].model_validate_json(cached)

        rows, total = self.repo.list_centres(
            search=search, include_inactive=include_inactive, limit=page.limit, offset=page.offset
        )
        result = Page[CentreRead](
            items=[CentreRead.model_validate(row) for row in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )
        self._cache_set(*key, value=result)
        return result

    def get_centre(self, centre_id: uuid.UUID) -> CentreRead:
        key = ("centre", centre_id)
        cached = self._cache_get(*key)
        if cached:
            return CentreRead.model_validate_json(cached)

        centre = self.repo.get_centre(centre_id)
        if centre is None:
            raise CentreNotFoundError()
        result = CentreRead.model_validate(centre)
        self._cache_set(*key, value=result)
        return result

    def list_centre_tests(
        self,
        centre_id: uuid.UUID,
        *,
        search: str | None,
        only_available: bool,
        page: PaginationParams,
    ) -> Page[CentreTestRead]:
        search = _normalize_search(search)
        key = ("centre-tests", centre_id, search or "", only_available, page.limit, page.offset)
        cached = self._cache_get(*key)
        if cached:
            return Page[CentreTestRead].model_validate_json(cached)

        if self.repo.get_centre(centre_id) is None:
            raise CentreNotFoundError()
        rows, total = self.repo.list_centre_tests(
            centre_id,
            search=search,
            only_available=only_available,
            limit=page.limit,
            offset=page.offset,
        )
        result = Page[CentreTestRead](
            items=[
                CentreTestRead(
                    test_id=row.test_id,
                    name=row.test.name,
                    description=row.test.description,
                    price=row.price,
                    is_available=row.is_available,
                )
                for row in rows
            ],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )
        self._cache_set(*key, value=result)
        return result

    # --- test catalogue (not cached: not in the hot path) -------------------------
    def list_tests(self, *, search: str | None, page: PaginationParams) -> Page[DiagnosticTestRead]:
        rows, total = self.repo.list_tests(
            search=_normalize_search(search), limit=page.limit, offset=page.offset
        )
        return Page[DiagnosticTestRead](
            items=[DiagnosticTestRead.model_validate(row) for row in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )

    def get_test(self, test_id: uuid.UUID) -> DiagnosticTestDetail:
        test = self.repo.get_test(test_id)
        if test is None:
            raise DiagnosticTestNotFoundError()
        offerings = self.repo.list_offerings(test_id)
        return DiagnosticTestDetail(
            id=test.id,
            name=test.name,
            description=test.description,
            offered_at=[
                CentreOffering(
                    centre_id=o.centre_id,
                    centre_name=o.centre.name,
                    price=o.price,
                    is_available=o.is_available,
                )
                for o in offerings
            ],
        )

    # --- writes (invalidate the cache) ----------------------------------------------
    def update_centre_test(
        self,
        centre_id: uuid.UUID,
        test_id: uuid.UUID,
        *,
        price: Decimal | None = None,
        is_available: bool | None = None,
    ) -> CentreTest:
        """Change a centre's price / availability for a test. Existing bookings keep the amount
        they were created with; new bookings see the new price immediately."""
        centre_test = self.repo.get_centre_test(centre_id, test_id)
        if centre_test is None:
            raise CentreTestNotFoundError()
        if price is not None:
            centre_test.price = price
        if is_available is not None:
            centre_test.is_available = is_available
        self.db.commit()
        self.invalidate_cache()
        return centre_test

    def invalidate_cache(self) -> None:
        if self.cache:
            self.cache.invalidate(CATALOGUE_NAMESPACE)

    # --- cache helpers -------------------------------------------------------------
    def _cache_get(self, *parts: object) -> str | None:
        if not self.cache:
            return None
        hit = self.cache.get(CATALOGUE_NAMESPACE, *parts)
        logger.debug("cache_lookup", extra={"key_parts": [str(p) for p in parts], "hit": bool(hit)})
        return hit

    def _cache_set(self, *parts: object, value: Page | CentreRead) -> None:
        if self.cache:
            self.cache.set(CATALOGUE_NAMESPACE, *parts, value=value)
