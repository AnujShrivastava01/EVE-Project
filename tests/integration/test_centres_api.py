import uuid
from decimal import Decimal
from types import SimpleNamespace

import fakeredis
import redis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.main import app
from app.repositories.centre_repository import CentreRepository
from app.services.centre_service import CentreService

CENTRES = "/api/v1/centres"


class TestListCentres:
    def test_lists_active_centres_ordered_by_name(
        self, client: TestClient, catalogue: SimpleNamespace
    ) -> None:
        body = client.get(CENTRES).json()
        assert [c["name"] for c in body["items"]] == ["Alpha Diagnostics", "Beta Labs"]
        assert body["total"] == 2
        assert {k: body[k] for k in ("limit", "offset")} == {"limit": 20, "offset": 0}

    def test_include_inactive(self, client: TestClient, catalogue: SimpleNamespace) -> None:
        body = client.get(CENTRES, params={"include_inactive": True}).json()
        assert body["total"] == 3

    def test_pagination_is_deterministic_and_complete(
        self, client: TestClient, catalogue: SimpleNamespace
    ) -> None:
        first = client.get(CENTRES, params={"limit": 1, "offset": 0}).json()
        second = client.get(CENTRES, params={"limit": 1, "offset": 1}).json()
        beyond = client.get(CENTRES, params={"limit": 1, "offset": 5}).json()
        assert [c["name"] for c in first["items"]] == ["Alpha Diagnostics"]
        assert [c["name"] for c in second["items"]] == ["Beta Labs"]
        assert first["total"] == second["total"] == 2
        assert beyond["items"] == []

    def test_invalid_pagination_parameters(self, client: TestClient) -> None:
        assert client.get(CENTRES, params={"limit": 0}).status_code == 422
        assert client.get(CENTRES, params={"limit": 1000}).status_code == 422
        assert client.get(CENTRES, params={"offset": -1}).status_code == 422

    def test_search_is_case_insensitive_and_wildcards_are_literal(
        self, client: TestClient, catalogue: SimpleNamespace
    ) -> None:
        assert [
            c["name"] for c in client.get(CENTRES, params={"search": "ALPHA"}).json()["items"]
        ] == ["Alpha Diagnostics"]
        assert client.get(CENTRES, params={"search": "pune"}).json()["total"] == 1  # location too
        assert client.get(CENTRES, params={"search": "%"}).json()["total"] == 0


class TestCentreDetailAndTests:
    def test_get_centre(self, client: TestClient, catalogue: SimpleNamespace) -> None:
        response = client.get(f"{CENTRES}/{catalogue.alpha.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Alpha Diagnostics"

    def test_nonexistent_centre_and_invalid_uuid(self, client: TestClient) -> None:
        missing = client.get(f"{CENTRES}/{uuid.uuid4()}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "CENTRE_NOT_FOUND"
        assert client.get(f"{CENTRES}/not-a-uuid").status_code == 422
        assert client.get(f"{CENTRES}/{uuid.uuid4()}/tests").status_code == 404

    def test_centre_tests_with_prices(self, client: TestClient, catalogue: SimpleNamespace) -> None:
        body = client.get(f"{CENTRES}/{catalogue.alpha.id}/tests").json()
        assert [t["name"] for t in body["items"]] == ["CBC", "HbA1c", "Lipid Profile"]
        prices = {t["name"]: t["price"] for t in body["items"]}
        assert prices == {"CBC": "350.00", "HbA1c": "550.00", "Lipid Profile": "650.00"}

    def test_only_available_and_search_filters(
        self, client: TestClient, catalogue: SimpleNamespace
    ) -> None:
        url = f"{CENTRES}/{catalogue.alpha.id}/tests"
        available = client.get(url, params={"only_available": True}).json()
        assert [t["name"] for t in available["items"]] == ["CBC", "HbA1c"]
        found = client.get(url, params={"search": "lipid"}).json()
        assert [t["name"] for t in found["items"]] == ["Lipid Profile"]
        assert found["items"][0]["is_available"] is False

    def test_test_catalogue_endpoints(self, client: TestClient, catalogue: SimpleNamespace) -> None:
        listing = client.get("/api/v1/tests", params={"search": "cbc"}).json()
        assert listing["total"] == 1
        detail = client.get(f"/api/v1/tests/{catalogue.cbc.id}").json()
        # cheapest first; inactive centres are not listed
        assert [(o["centre_name"], o["price"]) for o in detail["offered_at"]] == [
            ("Beta Labs", "300.00"),
            ("Alpha Diagnostics", "350.00"),
        ]
        assert client.get(f"/api/v1/tests/{uuid.uuid4()}").status_code == 404


class TestCaching:
    def test_miss_then_hit(
        self,
        client: TestClient,
        catalogue: SimpleNamespace,
        fake_redis: fakeredis.FakeRedis,
        monkeypatch,
    ) -> None:
        db_calls = []
        original = CentreRepository.list_centres

        def spy(self, **kwargs):
            db_calls.append(kwargs)
            return original(self, **kwargs)

        monkeypatch.setattr(CentreRepository, "list_centres", spy)
        assert not fake_redis.keys("eve:cache:*")

        miss = client.get(CENTRES).json()  # MISS: hits PostgreSQL, populates Redis
        assert len(db_calls) == 1
        assert fake_redis.keys("eve:cache:catalogue:*:centres:*")

        hit = client.get(CENTRES).json()  # HIT: served from Redis
        assert len(db_calls) == 1
        assert hit == miss

    def test_keys_differ_per_query_and_entries_have_a_ttl(
        self, client: TestClient, catalogue: SimpleNamespace, fake_redis: fakeredis.FakeRedis
    ) -> None:
        client.get(CENTRES, params={"limit": 1})
        client.get(CENTRES, params={"limit": 2})
        client.get(f"{CENTRES}/{catalogue.alpha.id}")
        client.get(f"{CENTRES}/{catalogue.alpha.id}/tests")
        keys = [k for k in fake_redis.keys("eve:cache:catalogue:v*") if "version" not in k]
        assert len(keys) == 4
        assert all(0 < fake_redis.ttl(k) <= 60 for k in keys)

    def test_not_found_is_not_cached(
        self, client: TestClient, fake_redis: fakeredis.FakeRedis
    ) -> None:
        client.get(f"{CENTRES}/{uuid.uuid4()}")
        assert not [k for k in fake_redis.keys("eve:cache:*") if "version" not in k]

    def test_price_change_invalidates_cached_pages(
        self,
        client: TestClient,
        db: Session,
        catalogue: SimpleNamespace,
        fake_redis: fakeredis.FakeRedis,
    ) -> None:
        url = f"{CENTRES}/{catalogue.alpha.id}/tests"
        before = {t["name"]: t["price"] for t in client.get(url).json()["items"]}
        assert before["CBC"] == "350.00"

        from app.core.cache import Cache

        CentreService(db, Cache(fake_redis, 60)).update_centre_test(
            catalogue.alpha.id, catalogue.cbc.id, price=Decimal("375.00"), is_available=False
        )
        after = {
            t["name"]: (t["price"], t["is_available"]) for t in client.get(url).json()["items"]
        }
        assert after["CBC"] == ("375.00", False)  # fresh data, not the stale cached page

    def test_api_still_works_when_redis_is_down(
        self, client: TestClient, catalogue: SimpleNamespace
    ) -> None:
        dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
        app.dependency_overrides[get_redis] = lambda: dead
        response = client.get(CENTRES)
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert client.get(f"{CENTRES}/{catalogue.alpha.id}/tests").status_code == 200

    def test_readiness_reports_degraded_without_redis(
        self, client: TestClient, monkeypatch
    ) -> None:
        import app.main as main_module

        dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
        monkeypatch.setattr(main_module, "get_redis", lambda: dead)
        body = client.get("/health/ready").json()
        assert body["status"] == "degraded"
        assert body["checks"] == {"postgres": "ok", "redis": "down"}
