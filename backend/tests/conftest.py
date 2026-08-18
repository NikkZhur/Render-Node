from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Environment, Settings
from app.jobs.manager import JobManager
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    configured = Settings(
        env=Environment.TEST,
        workspace=tmp_path,
        database_url="",
        bundled_blender_root=tmp_path / "bundled",
    )
    for version in ("5.2.0", "4.1.1"):
        binary = configured.bundled_blender_root / version / "blender"
        binary.parent.mkdir(parents=True)
        binary.write_text(f"#!/bin/sh\nprintf 'Blender {version}\\n'\n")
        binary.chmod(0o755)
    assert configured.database_path is not None
    configured.database_path.parent.mkdir(parents=True)
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = configured.database_url
    command.upgrade(alembic_config, "head")
    return configured


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            activated = await http_client.post("/api/v1/blender/versions/5.2.0/activate")
            assert activated.status_code == 200
            yield http_client


@pytest.fixture
async def job_settings(tmp_path: Path) -> Settings:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite3'}"
    configured = Settings(
        env=Environment.TEST,
        workspace=tmp_path,
        bundled_blender_root=tmp_path / "bundled",
        database_url=database_url,
        max_upload_gb=0.000001,
        max_zip_files=2,
        max_zip_extracted_gb=0.000001,
        render_scheduler_enabled=False,
    )
    for version in ("5.2.0", "4.1.1"):
        binary = configured.bundled_blender_root / version / "blender"
        binary.parent.mkdir(parents=True)
        binary.write_text(f"#!/bin/sh\nprintf 'Blender {version}\\n'\n")
        binary.chmod(0o755)
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    return configured


@pytest.fixture
def job_app(job_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    async def start_without_scheduler(manager: JobManager) -> None:
        await manager._recover_interrupted()

    monkeypatch.setattr(JobManager, "start", start_without_scheduler)
    return create_app(job_settings.model_copy(update={"render_scheduler_enabled": True}))


@pytest.fixture
async def job_client(job_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with job_app.router.lifespan_context(job_app):
        transport = ASGITransport(app=job_app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            activated = await http_client.post("/api/v1/blender/versions/5.2.0/activate")
            assert activated.status_code == 200
            yield http_client
