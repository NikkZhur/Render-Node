from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Environment, Settings


def test_database_url_follows_workspace(tmp_path: Path) -> None:
    settings = Settings(env=Environment.TEST, workspace=tmp_path)

    assert settings.database_path == tmp_path / "database" / "render-node.sqlite3"


def test_allowed_origins_accept_comma_separated_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "RENDER_NODE_ALLOWED_ORIGINS",
        "http://localhost:5173,https://render.example.com",
    )

    settings = Settings(env=Environment.TEST)

    assert settings.cors_origins == [
        "http://localhost:5173",
        "https://render.example.com",
    ]


def test_database_driver_must_be_async_sqlite() -> None:
    with pytest.raises(ValidationError, match=r"sqlite\+aiosqlite"):
        Settings(database_url="postgresql://localhost/render-node")


def test_environment_uses_documented_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_NODE_ENV", "production")

    assert Settings().env is Environment.PRODUCTION
