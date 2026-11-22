from fastapi import APIRouter

from app.api.v1 import (
    admin,
    admin_billing,
    admin_fraud,
    admin_kyc,
    auth,
    billing,
    documents,
    inquiries,
    kyc,
    meta,
    properties,
    saved,
    verification,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(properties.router)
api_router.include_router(saved.router)
api_router.include_router(inquiries.router)
api_router.include_router(documents.router)
api_router.include_router(meta.router)
api_router.include_router(verification.router)
api_router.include_router(kyc.router)
api_router.include_router(billing.router)
api_router.include_router(admin.router)
api_router.include_router(admin_fraud.router)
api_router.include_router(admin_kyc.router)
api_router.include_router(admin_billing.router)
