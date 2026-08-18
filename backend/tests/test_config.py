from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Environment, ResourceIsolationMode, Settings


def test_database_url_follows_workspace(tmp_path: Path) -> None:
    settings = Settings(env=Environment.TEST, workspace=tmp_path, database_url="")

    assert settings.database_path == tmp_path / "database" / "render-node.sqlite3"


def test_bundled_blender_root_is_configurable(tmp_path: Path) -> None:
    bundled_root = tmp_path / "bundled"

    settings = Settings(env=Environment.TEST, bundled_blender_root=bundled_root)

    assert settings.bundled_blender_root == bundled_root.resolve()


def test_database_driver_must_be_async_sqlite() -> None:
    with pytest.raises(ValidationError, match=r"sqlite\+aiosqlite"):
        Settings(database_url="postgresql://localhost/render-node")


def test_environment_uses_documented_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_NODE_ENV", "production")
    monkeypatch.setenv("RENDER_NODE_AUTH_TOKEN", "a" * 32)
    monkeypatch.setenv("RENDER_NODE_RUNNER_MODE", "disabled")
    monkeypatch.setenv("RENDER_NODE_RESOURCE_ISOLATION_MODE", "strict")

    assert Settings().env is Environment.PRODUCTION


def test_legacy_scene_trust_gate_variables_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_NODE_RUNNER_MODE", "disabled")
    monkeypatch.setenv("RENDER_NODE_DEPLOYMENT_PROFILE", "isolated_worker")
    monkeypatch.setenv("RENDER_NODE_ALLOW_UNSANDBOXED_RUNNER", "false")

    settings = Settings()

    assert not hasattr(settings, "runner_mode")
    assert not hasattr(settings, "deployment_profile")
    assert not hasattr(settings, "allow_unsandboxed_runner")


def test_production_rejects_trusted_resource_fallback() -> None:
    with pytest.raises(ValidationError, match="resource_isolation_mode=strict"):
        Settings(
            env=Environment.PRODUCTION,
            resource_isolation_mode=ResourceIsolationMode.TRUSTED,
            auth_token="x" * 32,
        )
