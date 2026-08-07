"""Run the development backend and frontend as one supervised process."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

ROOT = Path(__file__).resolve().parents[1]
SHUTDOWN_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.2
BACKEND_READY_TIMEOUT_SECONDS = 60.0
BACKEND_READY_REQUEST_TIMEOUT_SECONDS = 1.0
BACKEND_READY_URL = "http://127.0.0.1:8000/ready"
# Dev-container port forwarding requires binding beyond loopback.
DEVELOPMENT_HOST = "0.0.0.0"  # noqa: S104
stop_signal: int | None = None


@dataclass(frozen=True, slots=True)
class Service:
    """A development process launched from a fixed repository directory."""

    name: str
    command: tuple[str, ...]
    cwd: Path


SERVICES = (
    Service(
        name="backend",
        command=(
            "uv",
            "run",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            DEVELOPMENT_HOST,
            "--port",
            "8000",
        ),
        cwd=ROOT / "backend",
    ),
    Service(
        name="frontend",
        command=("npm", "run", "dev"),
        cwd=ROOT / "frontend",
    ),
)


def request_shutdown(signum: int, _frame: FrameType | None) -> None:
    """Record a terminal signal so cleanup runs in the normal control flow."""

    global stop_signal
    stop_signal = signum


def terminate_process_groups(processes: list[subprocess.Popen[bytes]]) -> None:
    """Stop every service process group, escalating after a short grace period."""

    running = [process for process in processes if process.poll() is None]
    for process in running:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
    while running and time.monotonic() < deadline:
        running = [process for process in running if process.poll() is None]
        if running:
            time.sleep(POLL_INTERVAL_SECONDS)

    for process in running:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    for process in processes:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            continue


def require_commands() -> bool:
    """Report missing development tools before making any state changes."""

    missing = [command for command in ("uv", "npm") if shutil.which(command) is None]
    if not missing:
        return True

    print(f"[dev] Missing required command(s): {', '.join(missing)}", flush=True)
    return False


def run_migrations() -> bool:
    """Bring the local SQLite database to the current Alembic head."""

    print("[dev] Applying database migrations...", flush=True)
    result = subprocess.run(
        ("uv", "run", "alembic", "upgrade", "head"),  # noqa: S607
        cwd=ROOT / "backend",
        check=False,
    )
    return result.returncode == 0


def start_service(service: Service) -> subprocess.Popen[bytes]:
    """Launch one service in its own process group."""

    print(f"[dev] Starting {service.name}: {' '.join(service.command)}", flush=True)
    return subprocess.Popen(  # noqa: S603
        service.command,
        cwd=service.cwd,
        start_new_session=True,
    )


def backend_is_ready() -> bool:
    """Probe the public readiness endpoint without depending on frontend proxying."""

    try:
        with urllib.request.urlopen(
            BACKEND_READY_URL,
            timeout=BACKEND_READY_REQUEST_TIMEOUT_SECONDS,
        ) as response:
            return response.status == 200
    except (OSError, TimeoutError, urllib.error.URLError):
        return False


def wait_for_backend_ready(process: subprocess.Popen[bytes]) -> bool:
    """Wait for backend readiness before allowing the frontend to start."""

    print(f"[dev] Waiting for backend readiness: {BACKEND_READY_URL}", flush=True)
    deadline = time.monotonic() + BACKEND_READY_TIMEOUT_SECONDS
    while True:
        return_code = process.poll()
        if return_code is not None:
            print(f"[dev] Backend exited before readiness with code {return_code}.", flush=True)
            return False
        if backend_is_ready():
            print("[dev] Backend is ready; starting frontend.", flush=True)
            return True
        if stop_signal is not None:
            print("[dev] Startup interrupted while waiting for backend readiness.", flush=True)
            return False
        if time.monotonic() >= deadline:
            print(
                f"[dev] Backend did not become ready within "
                f"{BACKEND_READY_TIMEOUT_SECONDS:.0f} seconds.",
                flush=True,
            )
            return False
        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> int:
    """Start both services and keep their lifecycle coupled."""

    if not require_commands() or not run_migrations():
        return 1

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    processes: list[subprocess.Popen[bytes]] = []
    try:
        backend_process = start_service(SERVICES[0])
        processes.append(backend_process)
        if not wait_for_backend_ready(backend_process):
            return_code = backend_process.poll()
            return return_code if return_code not in {None, 0} else 1

        processes.append(start_service(SERVICES[1]))

        while stop_signal is None:
            for service, process in zip(SERVICES, processes, strict=True):
                return_code = process.poll()
                if return_code is None:
                    continue
                print(
                    f"[dev] {service.name} exited with code {return_code}; stopping all services.",
                    flush=True,
                )
                return return_code if return_code != 0 else 1
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        terminate_process_groups(processes)

    print("[dev] Backend and frontend stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
