from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest


class FakeProcess:
    def __init__(self, return_codes: Iterator[int | None]) -> None:
        self._return_codes = return_codes
        self._last_return_code: int | None = None

    def poll(self) -> int | None:
        self._last_return_code = next(self._return_codes, self._last_return_code)
        return self._last_return_code


@pytest.fixture
def dev_script(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "dev.py"
    spec = importlib.util.spec_from_file_location("render_node_dev_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.stop_signal = None
    return module


def test_wait_for_backend_ready_retries_until_probe_succeeds(
    dev_script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_results = iter((False, True))
    sleeps: list[float] = []
    process = FakeProcess(iter((None, None)))

    monkeypatch.setattr(dev_script, "backend_is_ready", lambda: next(probe_results))
    monkeypatch.setattr(dev_script.time, "sleep", sleeps.append)

    assert dev_script.wait_for_backend_ready(process)
    assert sleeps == [dev_script.POLL_INTERVAL_SECONDS]


def test_main_starts_frontend_only_after_backend_is_ready(
    dev_script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeProcess(iter((None, None)))
    frontend = FakeProcess(iter((7,)))
    started: list[str] = []

    def start_service(service: object) -> FakeProcess:
        service_name = service.name
        started.append(service_name)
        return backend if service_name == "backend" else frontend

    def wait_for_backend_ready(process: FakeProcess) -> bool:
        assert process is backend
        assert started == ["backend"]
        return True

    monkeypatch.setattr(dev_script, "require_commands", lambda: True)
    monkeypatch.setattr(dev_script, "run_migrations", lambda: True)
    monkeypatch.setattr(dev_script, "start_service", start_service)
    monkeypatch.setattr(dev_script, "wait_for_backend_ready", wait_for_backend_ready)
    monkeypatch.setattr(dev_script, "terminate_process_groups", lambda _processes: None)
    monkeypatch.setattr(dev_script.signal, "signal", lambda *_args: None)

    assert dev_script.main() == 7
    assert started == ["backend", "frontend"]


def test_main_does_not_start_frontend_when_backend_is_not_ready(
    dev_script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeProcess(iter((None,)))
    started: list[str] = []

    def start_service(service: object) -> FakeProcess:
        started.append(service.name)
        return backend

    monkeypatch.setattr(dev_script, "require_commands", lambda: True)
    monkeypatch.setattr(dev_script, "run_migrations", lambda: True)
    monkeypatch.setattr(dev_script, "start_service", start_service)
    monkeypatch.setattr(dev_script, "wait_for_backend_ready", lambda _process: False)
    monkeypatch.setattr(dev_script, "terminate_process_groups", lambda _processes: None)
    monkeypatch.setattr(dev_script.signal, "signal", lambda *_args: None)

    assert dev_script.main() == 1
    assert started == ["backend"]
