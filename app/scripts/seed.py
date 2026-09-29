"""Idempotent seed script:  python -m app.scripts.seed

Safe to run repeatedly: rows are matched by name, missing ones are created and existing
prices/availability are updated (through the service, so the Redis cache is invalidated).
All data is fictional and for demonstration only.
"""

import logging
from decimal import Decimal

from app.core.cache import Cache
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.redis import get_redis
from app.db.database import SessionLocal
from app.db.models import CentreTest, DiagnosticCentre, DiagnosticTest
from app.repositories.centre_repository import CentreRepository
from app.services.centre_service import CentreService

logger = logging.getLogger("app.seed")

CENTRES = [
    ("CityCare Diagnostics", "12 MG Road, Bengaluru"),
    ("Apollo Diagnostics", "45 Anna Salai, Chennai"),
    ("HealthPlus Labs", "8 Park Street, Kolkata"),
]

TESTS = [
    ("CBC", "Complete Blood Count: red/white cells, haemoglobin and platelets."),
    ("Lipid Profile", "Cholesterol (total, HDL, LDL) and triglycerides."),
    ("Thyroid Profile", "T3, T4 and TSH hormone levels."),
    ("HbA1c", "Average blood-sugar level over the last ~3 months."),
    ("Liver Function Test", "Enzymes and proteins that indicate liver health."),
]

# (centre, test, price, is_available). Not every centre offers every test, and one test is
# temporarily unavailable so the TEST_UNAVAILABLE path can be demonstrated.
OFFERINGS = [
    ("CityCare Diagnostics", "CBC", "350.00", True),
    ("CityCare Diagnostics", "Lipid Profile", "650.00", True),
    ("CityCare Diagnostics", "Thyroid Profile", "700.00", True),
    ("CityCare Diagnostics", "HbA1c", "550.00", True),
    ("CityCare Diagnostics", "Liver Function Test", "800.00", True),
    ("Apollo Diagnostics", "CBC", "400.00", True),
    ("Apollo Diagnostics", "Lipid Profile", "700.00", True),
    ("Apollo Diagnostics", "Thyroid Profile", "750.00", True),
    ("Apollo Diagnostics", "HbA1c", "600.00", True),
    ("Apollo Diagnostics", "Liver Function Test", "850.00", False),
    ("HealthPlus Labs", "CBC", "300.00", True),
    ("HealthPlus Labs", "Lipid Profile", "600.00", True),
    ("HealthPlus Labs", "HbA1c", "500.00", True),
    ("HealthPlus Labs", "Liver Function Test", "750.00", True),
]


def seed() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        repo = CentreRepository(db)
        service = CentreService(db, Cache(get_redis(), settings.cache_ttl_seconds))

        centres = {}
        for name, location in CENTRES:
            centres[name] = repo.get_centre_by_name(name) or repo.add_centre(
                DiagnosticCentre(name=name, location=location)
            )
        tests = {}
        for name, description in TESTS:
            tests[name] = repo.get_test_by_name(name) or repo.add_test(
                DiagnosticTest(name=name, description=description)
            )

        created = updated = 0
        for centre_name, test_name, price, available in OFFERINGS:
            centre, test = centres[centre_name], tests[test_name]
            existing = repo.get_centre_test(centre.id, test.id)
            if existing is None:
                repo.add_centre_test(
                    CentreTest(
                        centre_id=centre.id,
                        test_id=test.id,
                        price=Decimal(price),
                        is_available=available,
                    )
                )
                created += 1
            elif existing.price != Decimal(price) or existing.is_available != available:
                service.update_centre_test(
                    centre.id, test.id, price=Decimal(price), is_available=available
                )
                updated += 1
        db.commit()
        service.invalidate_cache()
    logger.info("seed_completed", extra={"rows_created": created, "rows_updated": updated})


if __name__ == "__main__":
    setup_logging(get_settings().log_level)
    seed()
