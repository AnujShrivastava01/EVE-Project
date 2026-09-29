from fastapi import APIRouter

from app.api.v1 import auth, centres, tests

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(centres.router)
api_router.include_router(tests.router)
