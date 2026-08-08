"""FastAPI startup and shutdown lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.artifacts.service import ArtifactService
from app.blender.command import BlenderExecutableResolver
from app.blender.devices import GpuDiscovery
from app.blender.official import OfficialCatalog
from app.blender.runner import RunnerLimits, SandboxRunner
from app.blender.sandbox import SandboxPolicy
from app.blender.service import BlenderService
from app.blender.storage import BlenderStorage
from app.config import Settings
from app.events.hub import EventHub
from app.jobs.locks import JobLocks
from app.jobs.manager import JobManager
from app.jobs.service import JobService
from app.jobs.upload_service import JobUploadService
from app.storage.database import Database
from app.storage.jobs import JobStorage
from app.storage.repository import DatabaseHealthRepository
from app.storage.uploads import UploadStorage
from app.system.metrics import SystemMetricsService

EXPECTED_DATABASE_REVISION = "20260730_0004"


async def _prepare_storage(settings: Settings) -> BlenderStorage:
    await asyncio.to_thread(settings.workspace.mkdir, parents=True, exist_ok=True)
    if settings.database_path is not None:
        await asyncio.to_thread(settings.database_path.parent.mkdir, parents=True, exist_ok=True)
    job_storage = JobStorage(settings.jobs_root)
    await job_storage.prepare_root()
    await job_storage.cleanup_runtime_temporaries()
    blender_storage = BlenderStorage(
        settings.blender_versions_root,
        settings.blender_downloads_root,
        settings.blender_quarantine_root,
        max_archive_bytes=settings.max_blender_archive_bytes,
        max_extracted_bytes=settings.max_blender_extracted_bytes,
        max_files=settings.max_blender_archive_files,
    )
    await blender_storage.prepare()
    return blender_storage


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    blender_storage = await _prepare_storage(settings)

    database = Database(settings.database_url)
    repository = DatabaseHealthRepository(database.session_factory)
    job_storage = JobStorage(settings.jobs_root)
    locks = JobLocks()
    event_hub = EventHub(queue_size=settings.event_queue_size)
    artifact_service = ArtifactService(
        database,
        job_storage,
        event_hub,
        preview_max_width=settings.preview_max_width,
        preview_max_height=settings.preview_max_height,
        preview_max_pixels=settings.preview_max_megapixels * 1_000_000,
        max_zip_bytes=settings.max_render_output_bytes,
    )
    upload_storage = UploadStorage(
        job_storage,
        max_upload_bytes=settings.max_upload_bytes,
        max_zip_files=settings.max_zip_files,
        max_zip_extracted_bytes=settings.max_zip_extracted_bytes,
    )
    app.state.database = database
    app.state.database_health_repository = repository
    app.state.event_hub = event_hub
    app.state.artifact_service = artifact_service
    sandbox_policy = SandboxPolicy(
        environment=settings.env,
        deployment_profile=settings.deployment_profile,
        runner_mode=settings.runner_mode,
    )
    runner = SandboxRunner(
        sandbox_policy,
        RunnerLimits(
            timeout_seconds=settings.render_timeout_seconds,
            terminate_grace_seconds=settings.render_terminate_grace_seconds,
            max_output_bytes=settings.max_render_output_bytes,
            max_log_bytes=settings.max_render_log_bytes,
            memory_bytes=settings.worker_memory_bytes,
            pids=settings.worker_pids_limit,
        ),
    )
    gpu_discovery = GpuDiscovery()
    job_manager = JobManager(
        database,
        runner,
        BlenderExecutableResolver(
            bundled_root=Path("/opt/render-node/blender"),
            installed_root=settings.blender_versions_root,
            override=settings.blender_executable_override,
        ),
        gpu_discovery,
        artifact_service,
        event_hub,
        jobs_root=settings.jobs_root,
        enabled=settings.render_scheduler_enabled,
        poll_seconds=settings.render_scheduler_poll_seconds,
    )
    app.state.gpu_discovery = gpu_discovery
    app.state.job_manager = job_manager
    app.state.job_service = JobService(
        database, job_storage, locks, event_hub, artifact_service, job_manager
    )
    job_upload_service = JobUploadService(database, upload_storage, job_storage, locks, event_hub)
    app.state.job_upload_service = job_upload_service
    blender_service = BlenderService(
        database,
        blender_storage,
        OfficialCatalog(
            ttl_seconds=settings.blender_catalog_ttl_seconds,
            download_timeout_seconds=settings.blender_download_timeout_seconds,
        ),
        event_hub,
    )
    app.state.blender_service = blender_service
    system_metrics_service = SystemMetricsService(
        gpu_discovery,
        event_hub,
        storage_paths=[
            ("Workspace", settings.workspace),
            ("Jobs", settings.jobs_root),
            ("Blender versions", settings.blender_versions_root),
        ],
        low_space_percent=settings.low_space_percent,
        low_space_bytes=int(settings.low_space_gb * 1024**3),
        interval_seconds=settings.metrics_interval_seconds,
    )
    app.state.system_metrics_service = system_metrics_service

    try:
        if not await repository.is_available():
            raise RuntimeError("SQLite readiness probe failed")
        revision = await repository.schema_revision()
        if revision != EXPECTED_DATABASE_REVISION:
            raise RuntimeError(
                "Database migration required: run `uv run alembic upgrade head` "
                f"(current={revision or 'none'}, expected={EXPECTED_DATABASE_REVISION})"
            )
        sandbox_policy.ensure_startup_ready(scheduler_enabled=settings.render_scheduler_enabled)
        await job_upload_service.recover_incomplete_uploads()
        await blender_service.initialize()
        await job_manager.start()
        await system_metrics_service.start()
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        await system_metrics_service.shutdown()
        await job_manager.shutdown()
        await blender_service.shutdown()
        await database.dispose()
