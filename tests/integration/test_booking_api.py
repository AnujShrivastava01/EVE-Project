import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.factories import book, future, pay

BOOKINGS = "/api/v1/bookings"


class TestCreateBooking:
    def test_success_returns_pending_booking_with_server_side_amount(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        response = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "PENDING"
        assert body["amount"] == "350.00"
        assert body["centre_name"] == "Alpha Diagnostics"
        assert body["test_name"] == "CBC"

    def test_client_cannot_choose_amount_or_status(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        response = book(
            client,
            auth_headers,
            catalogue.alpha.id,
            catalogue.cbc.id,
            amount="0.01",
            status="CONFIRMED",
        )
        assert response.status_code == 201
        assert response.json()["amount"] == "350.00"
        assert response.json()["status"] == "PENDING"

    def test_requires_authentication(self, client: TestClient, catalogue: SimpleNamespace) -> None:
        response = client.post(
            BOOKINGS,
            json={
                "centre_id": str(catalogue.alpha.id),
                "test_id": str(catalogue.cbc.id),
                "appointment_at": future().isoformat(),
            },
        )
        assert response.status_code == 401

    def test_unknown_centre_and_unknown_test(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        r1 = book(client, auth_headers, uuid.uuid4(), catalogue.cbc.id)
        assert (r1.status_code, r1.json()["error"]["code"]) == (404, "CENTRE_NOT_FOUND")
        r2 = book(client, auth_headers, catalogue.alpha.id, uuid.uuid4())
        assert (r2.status_code, r2.json()["error"]["code"]) == (404, "TEST_NOT_FOUND")

    def test_test_not_offered_at_this_centre(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        response = book(client, auth_headers, catalogue.beta.id, catalogue.hba1c.id)
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "CENTRE_TEST_NOT_FOUND",
        )

    def test_unavailable_test_and_inactive_centre(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        r1 = book(client, auth_headers, catalogue.alpha.id, catalogue.lipid.id)
        assert (r1.status_code, r1.json()["error"]["code"]) == (409, "TEST_UNAVAILABLE")
        r2 = book(client, auth_headers, catalogue.closed.id, catalogue.cbc.id)
        assert (r2.status_code, r2.json()["error"]["code"]) == (409, "CENTRE_UNAVAILABLE")

    @pytest.mark.parametrize(
        "when",
        [future(days=-2), future(days=0, hours=0), future(days=400)],
        ids=["past", "too-soon", "too-far"],
    )
    def test_invalid_appointment_time(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace, when
    ) -> None:
        response = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id, when=when)
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "INVALID_APPOINTMENT",
        )

    def test_naive_datetime_and_garbage_are_validation_errors(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        for value in ("2030-01-01T10:00:00", "tomorrow", ""):
            response = client.post(
                BOOKINGS,
                headers=auth_headers,
                json={
                    "centre_id": str(catalogue.alpha.id),
                    "test_id": str(catalogue.cbc.id),
                    "appointment_at": value,
                },
            )
            assert response.status_code == 422, value

    def test_invalid_uuids_in_body(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            BOOKINGS,
            headers=auth_headers,
            json={"centre_id": "nope", "test_id": "nope", "appointment_at": future().isoformat()},
        )
        assert response.status_code == 422

    def test_duplicate_submission_is_a_409(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        when = future(days=3)
        assert (
            book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id, when).status_code
            == 201
        )
        again = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id, when)
        assert (again.status_code, again.json()["error"]["code"]) == (409, "DUPLICATE_BOOKING")


class TestReadBookings:
    def test_list_get_and_status_filter(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        first = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id, future(2)).json()
        second = book(
            client, auth_headers, catalogue.alpha.id, catalogue.hba1c.id, future(3)
        ).json()
        client.post(f"{BOOKINGS}/{first['id']}/cancel", headers=auth_headers)

        listing = client.get(BOOKINGS, headers=auth_headers).json()
        assert listing["total"] == 2
        assert [b["id"] for b in listing["items"]] == [second["id"], first["id"]]  # newest first
        only_cancelled = client.get(BOOKINGS, headers=auth_headers, params={"status": "CANCELLED"})
        assert [b["id"] for b in only_cancelled.json()["items"]] == [first["id"]]
        assert (
            client.get(BOOKINGS, headers=auth_headers, params={"status": "BOGUS"}).status_code
            == 422
        )

        one = client.get(f"{BOOKINGS}/{second['id']}", headers=auth_headers)
        assert one.status_code == 200 and one.json()["id"] == second["id"]

    def test_pagination(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        for days in (2, 3, 4):
            book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id, future(days))
        page = client.get(BOOKINGS, headers=auth_headers, params={"limit": 2, "offset": 2}).json()
        assert (page["total"], len(page["items"])) == (3, 1)

    def test_nonexistent_booking_and_invalid_uuid(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        missing = client.get(f"{BOOKINGS}/{uuid.uuid4()}", headers=auth_headers)
        assert (missing.status_code, missing.json()["error"]["code"]) == (404, "BOOKING_NOT_FOUND")
        assert client.get(f"{BOOKINGS}/123", headers=auth_headers).status_code == 422


class TestAuthorization:
    def test_users_cannot_see_or_cancel_each_others_bookings(
        self,
        client: TestClient,
        auth_headers: dict,
        other_headers: dict,
        catalogue: SimpleNamespace,
    ) -> None:
        booking = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id).json()

        read = client.get(f"{BOOKINGS}/{booking['id']}", headers=other_headers)
        assert (read.status_code, read.json()["error"]["code"]) == (403, "BOOKING_ACCESS_DENIED")
        cancel = client.post(f"{BOOKINGS}/{booking['id']}/cancel", headers=other_headers)
        assert cancel.status_code == 403
        assert client.get(BOOKINGS, headers=other_headers).json()["total"] == 0

        # ...and the owner's booking is untouched.
        still = client.get(f"{BOOKINGS}/{booking['id']}", headers=auth_headers).json()
        assert still["status"] == "PENDING"

    def test_no_endpoint_lets_clients_set_status(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        booking = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id).json()
        url = f"{BOOKINGS}/{booking['id']}"
        for method in ("put", "patch", "delete"):
            response = client.request(
                method.upper(), url, headers=auth_headers, json={"status": "CONFIRMED"}
            )
            assert response.status_code == 405
        assert client.get(url, headers=auth_headers).json()["status"] == "PENDING"


class TestCancel:
    def test_cancel_pending_booking(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        booking = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id).json()
        response = client.post(f"{BOOKINGS}/{booking['id']}/cancel", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"

    def test_cancel_confirmed_booking(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        booking = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id).json()
        assert pay(client, auth_headers, booking["id"], "success").status_code == 201
        response = client.post(f"{BOOKINGS}/{booking['id']}/cancel", headers=auth_headers)
        assert response.json()["status"] == "CANCELLED"

    def test_invalid_transitions_are_409(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        cancelled = book(
            client, auth_headers, catalogue.alpha.id, catalogue.cbc.id, future(2)
        ).json()
        client.post(f"{BOOKINGS}/{cancelled['id']}/cancel", headers=auth_headers)
        again = client.post(f"{BOOKINGS}/{cancelled['id']}/cancel", headers=auth_headers)
        assert (again.status_code, again.json()["error"]["code"]) == (
            409,
            "INVALID_BOOKING_TRANSITION",
        )

        failed = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id, future(3)).json()
        pay(client, auth_headers, failed["id"], "failure")
        response = client.post(f"{BOOKINGS}/{failed['id']}/cancel", headers=auth_headers)
        assert response.status_code == 409

    def test_cancel_unknown_booking(self, client: TestClient, auth_headers: dict) -> None:
        assert (
            client.post(f"{BOOKINGS}/{uuid.uuid4()}/cancel", headers=auth_headers).status_code
            == 404
        )

    def test_cancel_blocked_while_payment_pending(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace, db: Session
    ) -> None:
        booking = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id).json()
        pay(client, auth_headers, booking["id"], "pending")
        response = client.post(f"{BOOKINGS}/{booking['id']}/cancel", headers=auth_headers)
        assert (response.status_code, response.json()["error"]["code"]) == (
            409,
            "PAYMENT_IN_PROGRESS",
        )
