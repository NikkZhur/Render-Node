import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Environment, Settings
from app.main import create_app


async def test_migrations_reach_current_head(tmp_path: Path) -> None:
    database_path = tmp_path / "new-workspace" / "database" / "migration.sqlite3"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url

    await asyncio.to_thread(command.upgrade, config, "head")

    assert database_path.is_file()
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()
    assert revision == "20261023_0006"


async def test_gpu_render_mode_migration_preserves_existing_job_semantics(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, config, "20260730_0004")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                id, name, source_filename, scene_path, status, blender_version,
                engine, device, gpu_ids, frame_mode, frame_start, frame_end,
                current_frame, progress, process_pid, created_at, started_at,
                finished_at, exit_code, error
            ) VALUES (
                ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0.0,
                NULL, ?, NULL, NULL, NULL, NULL
            )
            """,
            (
                "00000000000040008000000000000001",
                "Legacy multi GPU",
                "created",
                "4.1.1",
                "CYCLES",
                "OPTIX",
                "[0, 1]",
                "RANGE",
                1,
                2,
                "2026-08-18 00:00:00",
            ),
        )

    await asyncio.to_thread(command.upgrade, config, "head")

    with sqlite3.connect(database_path) as connection:
        mode = connection.execute(
            "SELECT gpu_render_mode, analysis_status, analysis_id, scene_metadata, auto_configured "
            "FROM jobs WHERE name = ?",
            ("Legacy multi GPU",),
        ).fetchone()
    assert mode == ("ALL_GPUS_PER_FRAME", "none", None, None, 0)


async def test_lifespan_rejects_database_without_current_migration(tmp_path: Path) -> None:
    settings = Settings(env=Environment.TEST, workspace=tmp_path, database_url="")
    app = create_app(settings)

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        async with app.router.lifespan_context(app):
            pass
