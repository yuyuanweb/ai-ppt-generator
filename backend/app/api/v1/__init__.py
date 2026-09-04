from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.deck import router as decks_router
from app.api.v1.design import router as design_router
from app.api.v1.health import router as health_router
from app.api.v1.media import router as media_router
from app.api.v1.outlines import router as outlines_router
from app.api.v1.payment import router as payment_router
from app.api.v1.payment import webhook_router as payment_webhook_router
from app.api.v1.projects import router as projects_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(design_router)
api_router.include_router(projects_router)
api_router.include_router(outlines_router)
api_router.include_router(decks_router)
api_router.include_router(media_router)
api_router.include_router(payment_router)
api_router.include_router(payment_webhook_router)
