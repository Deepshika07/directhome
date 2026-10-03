from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    documents,
    inquiries,
    meta,
    properties,
    saved,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(properties.router)
api_router.include_router(saved.router)
api_router.include_router(inquiries.router)
api_router.include_router(documents.router)
api_router.include_router(meta.router)
api_router.include_router(admin.router)
