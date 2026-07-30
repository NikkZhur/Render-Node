"""Top-level API router composition."""

from fastapi import APIRouter

from app.api.artifacts import router as artifacts_router
from app.api.blender import router as blender_router
from app.api.devices import router as devices_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.system import router as system_router
from app.api.uploads import router as uploads_router

router = APIRouter()
router.include_router(health_router)

# Domain routers are added below this prefix in later phases.
api_v1_router = APIRouter()
api_v1_router.include_router(jobs_router)
api_v1_router.include_router(uploads_router)
api_v1_router.include_router(blender_router)
api_v1_router.include_router(devices_router)
api_v1_router.include_router(events_router)
api_v1_router.include_router(artifacts_router)
api_v1_router.include_router(system_router)
