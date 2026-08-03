import asyncio
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
    assert revision == "20260730_0004"


async def test_lifespan_rejects_database_without_current_migration(tmp_path: Path) -> None:
    settings = Settings(env=Environment.TEST, workspace=tmp_path)
    app = create_app(settings)

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        async with app.router.lifespan_context(app):
            pass
