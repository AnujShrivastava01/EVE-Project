from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentUser, DbSession
from app.middleware.rate_limit import limit_auth
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.error import error_responses
from app.schemas.user import SignupRequest, UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    dependencies=[Depends(limit_auth)],
    responses=error_responses(409, 422, 429),
)
def signup(payload: SignupRequest, db: DbSession) -> UserRead:
    user = AuthService(db).signup(payload.name, payload.email, payload.password)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with email + password (JSON)",
    dependencies=[Depends(limit_auth)],
    responses=error_responses(401, 403, 422, 429),
)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    return AuthService(db).login(payload.email, payload.password)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="OAuth2 password flow (form) — used by Swagger's Authorize button",
    description="Same as `/login` but form-encoded; put your **email** in the `username` field.",
    dependencies=[Depends(limit_auth)],
    responses=error_responses(401, 403, 429),
)
def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession) -> TokenResponse:
    return AuthService(db).login(form.username.strip().lower(), form.password)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Current user",
    responses=error_responses(401, 403),
)
def me(current_user: CurrentUser) -> UserRead:
    return UserRead.model_validate(current_user)
