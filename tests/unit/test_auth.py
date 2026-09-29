import uuid
from datetime import timedelta

import jwt
import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
    UserInactiveError,
)
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    sign_webhook_body,
    verify_password,
    verify_webhook_signature_header,
)
from app.services.auth_service import AuthService
from tests.factories import PASSWORD, create_user


class TestPasswordHashing:
    def test_hash_is_not_plaintext_and_verifies(self) -> None:
        hashed = hash_password("Sup3rSecret")
        assert hashed != "Sup3rSecret"
        assert hashed.startswith("$argon2")
        assert verify_password("Sup3rSecret", hashed)

    def test_wrong_password_and_garbage_hash_do_not_verify(self) -> None:
        hashed = hash_password("Sup3rSecret")
        assert not verify_password("other", hashed)
        assert not verify_password("Sup3rSecret", "not-a-hash")

    def test_same_password_gets_different_salts(self) -> None:
        assert hash_password("Sup3rSecret") != hash_password("Sup3rSecret")


class TestJwt:
    def test_roundtrip(self) -> None:
        token, expires_in = create_access_token("user-123")
        assert decode_access_token(token) == "user-123"
        assert expires_in == get_settings().access_token_expire_minutes * 60

    def test_expired_token_rejected(self) -> None:
        token, _ = create_access_token("user-123", expires_delta=timedelta(seconds=-5))
        with pytest.raises(InvalidTokenError):
            decode_access_token(token)

    def test_tampered_or_foreign_token_rejected(self) -> None:
        forged = jwt.encode({"sub": "user-123", "exp": 9999999999}, "wrong-key-" * 5, "HS256")
        with pytest.raises(InvalidTokenError):
            decode_access_token(forged)
        with pytest.raises(InvalidTokenError):
            decode_access_token("not.a.jwt")

    def test_token_without_expiry_rejected(self) -> None:
        settings = get_settings()
        token = jwt.encode({"sub": "u"}, settings.jwt_secret_key, settings.jwt_algorithm)
        with pytest.raises(InvalidTokenError):
            decode_access_token(token)


class TestWebhookSignature:
    def test_valid_and_invalid_signatures(self) -> None:
        body = b'{"event_id": "evt_1"}'
        good = sign_webhook_body(body, "s3cret")
        assert verify_webhook_signature_header(body, good, "s3cret")
        assert not verify_webhook_signature_header(body + b" ", good, "s3cret")
        assert not verify_webhook_signature_header(body, good, "other-secret")
        assert not verify_webhook_signature_header(body, "", "s3cret")


class TestAuthService:
    def test_signup_stores_hash_not_password(self, db: Session) -> None:
        user = AuthService(db).signup("Asha", "asha@example.com", PASSWORD)
        assert user.password_hash != PASSWORD
        assert user.is_active

    def test_duplicate_email_rejected(self, db: Session) -> None:
        AuthService(db).signup("Asha", "asha@example.com", PASSWORD)
        with pytest.raises(EmailAlreadyRegisteredError):
            AuthService(db).signup("Other", "asha@example.com", PASSWORD)

    def test_login_success_returns_usable_token(self, db: Session) -> None:
        user = create_user(db, "login@example.com")
        token = AuthService(db).login("login@example.com", PASSWORD)
        assert token.token_type == "bearer"
        assert AuthService(db).get_user_from_token(token.access_token).id == user.id

    def test_login_wrong_password_and_unknown_email_look_identical(self, db: Session) -> None:
        create_user(db, "login@example.com")
        with pytest.raises(InvalidCredentialsError) as wrong_password:
            AuthService(db).login("login@example.com", "wrong-password1")
        with pytest.raises(InvalidCredentialsError) as unknown_email:
            AuthService(db).login("nobody@example.com", PASSWORD)
        assert wrong_password.value.message == unknown_email.value.message

    def test_inactive_user_cannot_login_or_use_token(self, db: Session) -> None:
        user = create_user(db, "inactive@example.com")
        token, _ = create_access_token(str(user.id))
        user.is_active = False
        db.commit()
        with pytest.raises(UserInactiveError):
            AuthService(db).login("inactive@example.com", PASSWORD)
        with pytest.raises(UserInactiveError):
            AuthService(db).get_user_from_token(token)

    def test_token_for_deleted_or_malformed_subject_rejected(self, db: Session) -> None:
        ghost, _ = create_access_token(str(uuid.uuid4()))
        malformed, _ = create_access_token("not-a-uuid")
        with pytest.raises(InvalidTokenError):
            AuthService(db).get_user_from_token(ghost)
        with pytest.raises(InvalidTokenError):
            AuthService(db).get_user_from_token(malformed)
