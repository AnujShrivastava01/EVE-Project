import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.factories import PASSWORD, future, signup_and_login

SIGNUP = "/api/v1/auth/signup"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


def valid_signup(**overrides) -> dict:
    return {"name": "Asha Verma", "email": "asha@example.com", "password": PASSWORD, **overrides}


class TestSignup:
    def test_success_never_returns_password_material(self, client: TestClient) -> None:
        response = client.post(SIGNUP, json=valid_signup())
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "asha@example.com"
        assert body["is_active"] is True
        assert "password" not in body and "password_hash" not in body
        assert PASSWORD not in response.text

    def test_email_is_normalised_to_lowercase(self, client: TestClient) -> None:
        response = client.post(SIGNUP, json=valid_signup(email="Asha@Example.COM"))
        assert response.json()["email"] == "asha@example.com"
        duplicate = client.post(SIGNUP, json=valid_signup(email="ASHA@example.com"))
        assert duplicate.status_code == 409

    def test_duplicate_email(self, client: TestClient) -> None:
        assert client.post(SIGNUP, json=valid_signup()).status_code == 201
        response = client.post(SIGNUP, json=valid_signup(name="Someone Else"))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"

    @pytest.mark.parametrize("email", ["not-an-email", "a@", "@example.com", "", "a b@example.com"])
    def test_invalid_email(self, client: TestClient, email: str) -> None:
        response = client.post(SIGNUP, json=valid_signup(email=email))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize("password", ["short1", "onlyletters", "12345678", "a" * 200 + "1", ""])
    def test_weak_or_invalid_password(self, client: TestClient, password: str) -> None:
        response = client.post(SIGNUP, json=valid_signup(password=password))
        assert response.status_code == 422

    def test_validation_errors_do_not_echo_the_password(self, client: TestClient) -> None:
        response = client.post(SIGNUP, json=valid_signup(password="onlyletters"))
        assert "onlyletters" not in response.text

    def test_missing_fields_and_blank_name(self, client: TestClient) -> None:
        assert client.post(SIGNUP, json={}).status_code == 422
        assert client.post(SIGNUP, json=valid_signup(name="   ")).status_code == 422

    def test_invalid_json_body(self, client: TestClient) -> None:
        response = client.post(
            SIGNUP, content="{not json", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestLogin:
    def test_success_returns_bearer_token(self, client: TestClient) -> None:
        client.post(SIGNUP, json=valid_signup())
        response = client.post(LOGIN, json={"email": "ASHA@example.com", "password": PASSWORD})
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == get_settings().access_token_expire_minutes * 60
        claims = jwt.decode(
            body["access_token"], get_settings().jwt_secret_key, algorithms=["HS256"]
        )
        assert {"sub", "exp", "iat"} <= claims.keys()

    def test_wrong_password(self, client: TestClient) -> None:
        client.post(SIGNUP, json=valid_signup())
        response = client.post(LOGIN, json={"email": "asha@example.com", "password": "Wrong-pass1"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_unknown_user_gets_the_same_response(self, client: TestClient) -> None:
        response = client.post(LOGIN, json={"email": "ghost@example.com", "password": PASSWORD})
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Incorrect email or password"

    def test_oauth2_form_endpoint_used_by_swagger(self, client: TestClient) -> None:
        client.post(SIGNUP, json=valid_signup())
        response = client.post(
            "/api/v1/auth/token", data={"username": "asha@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["access_token"]


class TestProtectedEndpoints:
    def test_me_returns_current_user(self, client: TestClient) -> None:
        headers = signup_and_login(client, "me@example.com", name="Me Myself")
        response = client.get(ME, headers=headers)
        assert response.status_code == 200
        assert response.json()["email"] == "me@example.com"
        assert "password_hash" not in response.json()

    @pytest.mark.parametrize(
        "path", [ME, "/api/v1/bookings", "/api/v1/bookings/00000000-0000-0000-0000-000000000000"]
    )
    def test_no_token_is_401(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"
        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize("header", ["Bearer garbage", "Bearer ", "Basic abc", "Token abc"])
    def test_bad_token_is_401(self, client: TestClient, header: str) -> None:
        assert client.get(ME, headers={"Authorization": header}).status_code == 401

    def test_expired_token_is_401(self, client: TestClient) -> None:
        from datetime import timedelta

        from app.core.security import create_access_token

        signup = client.post(SIGNUP, json=valid_signup()).json()
        token, _ = create_access_token(signup["id"], expires_delta=timedelta(seconds=-1))
        response = client.get(ME, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_TOKEN"

    def test_disabled_user_is_403(self, client: TestClient, db) -> None:
        from app.db.models import User

        headers = signup_and_login(client, "gone@example.com")
        user = db.query(User).filter_by(email="gone@example.com").one()
        user.is_active = False
        db.commit()
        response = client.get(ME, headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "USER_INACTIVE"


def test_future_helper_is_in_the_future() -> None:  # guards the shared test helper
    from datetime import UTC, datetime

    assert future() > datetime.now(UTC)
