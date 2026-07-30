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
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    configured = Settings(env=Environment.TEST, workspace=tmp_path)
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
            yield http_client


@pytest.fixture
async def job_settings(tmp_path: Path) -> Settings:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite3'}"
    configured = Settings(
        env=Environment.TEST,
        workspace=tmp_path,
        database_url=database_url,
        max_upload_gb=0.000001,
        max_zip_files=2,
        max_zip_extracted_gb=0.000001,
        render_scheduler_enabled=False,
    )
    alembic_config = Config("alembic.ini")
    alembic_config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    return configured


@pytest.fixture
def job_app(job_settings: Settings) -> FastAPI:
    return create_app(job_settings)


@pytest.fixture
async def job_client(job_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with job_app.router.lifespan_context(job_app):
        transport = ASGITransport(app=job_app)
        async with AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client
