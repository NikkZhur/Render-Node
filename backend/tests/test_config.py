from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import DeploymentProfile, Environment, RunnerMode, Settings


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
    monkeypatch.setenv("RENDER_NODE_AUTH_TOKEN", "a" * 32)
    monkeypatch.setenv("RENDER_NODE_ALLOWED_ORIGINS", "https://render.example.com")
    monkeypatch.setenv("RENDER_NODE_RUNNER_MODE", "disabled")

    assert Settings().env is Environment.PRODUCTION


def test_legacy_unsandboxed_flag_maps_to_explicit_local_trusted_mode() -> None:
    settings = Settings(
        env=Environment.DEVELOPMENT,
        runner_mode=RunnerMode.DISABLED,
        allow_unsandboxed_runner=True,
    )

    assert settings.runner_mode is RunnerMode.LOCAL_TRUSTED


def test_local_trusted_runner_requires_single_tenant_profile_in_production() -> None:
    with pytest.raises(ValidationError, match="single_tenant"):
        Settings(
            env=Environment.PRODUCTION,
            runner_mode=RunnerMode.LOCAL_TRUSTED,
            auth_token="x" * 32,
            allowed_origins=["https://render.example.com"],
        )


def test_single_tenant_profile_allows_local_runner_in_production() -> None:
    settings = Settings(
        env=Environment.PRODUCTION,
        deployment_profile=DeploymentProfile.SINGLE_TENANT,
        runner_mode=RunnerMode.LOCAL_TRUSTED,
        auth_token="x" * 32,
        allowed_origins=["https://render.example.com"],
    )

    assert settings.deployment_profile is DeploymentProfile.SINGLE_TENANT
    assert settings.runner_mode is RunnerMode.LOCAL_TRUSTED
