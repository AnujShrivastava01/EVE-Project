import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
    UserInactiveError,
)
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.models import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import TokenResponse

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)

    def signup(self, name: str, email: str, password: str) -> User:
        if self.users.get_by_email(email):
            raise EmailAlreadyRegisteredError()
        user = User(name=name, email=email, password_hash=hash_password(password))
        try:
            self.users.add(user)
            self.db.commit()
        except IntegrityError as exc:
            # Two concurrent signups with the same email: the UNIQUE constraint decides.
            self.db.rollback()
            raise EmailAlreadyRegisteredError() from exc
        logger.info("user_signed_up", extra={"user_id": str(user.id)})
        return user

    def login(self, email: str, password: str) -> TokenResponse:
        user = self.users.get_by_email(email)
        # Always verify a hash (even for unknown emails) so response time doesn't reveal
        # whether an account exists. The error message is identical for both cases.
        password_ok = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)
        if not user or not password_ok:
            logger.warning("login_failed")
            raise InvalidCredentialsError()
        if not user.is_active:
            raise UserInactiveError()
        token, expires_in = create_access_token(str(user.id))
        logger.info("user_logged_in", extra={"user_id": str(user.id)})
        return TokenResponse(access_token=token, expires_in=expires_in)

    def get_user_from_token(self, token: str) -> User:
        subject = decode_access_token(token)
        try:
            user = self.users.get_by_id(uuid.UUID(subject))
        except ValueError as exc:
            raise InvalidTokenError() from exc
        if user is None:
            raise InvalidTokenError()
        if not user.is_active:
            raise UserInactiveError()
        return user
