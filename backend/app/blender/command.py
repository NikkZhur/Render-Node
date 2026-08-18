"""Backend-owned Blender argument construction and exact version adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.blender.types import RuntimeSource
from app.jobs.models import Job
from app.jobs.types import FrameMode
from app.storage.jobs import contained_path

ADAPTER_ROOT = Path(__file__).with_name("adapters")
FRAME_DISTRIBUTION_SCRIPT = ADAPTER_ROOT / "frame_distribution.py"
VERSION_SCRIPTS = {
    "3.6.23": ADAPTER_ROOT / "configure_3_6.py",
    "4.1.1": ADAPTER_ROOT / "configure_4_1.py",
    "4.2.22": ADAPTER_ROOT / "configure_4_2.py",
    "4.5.11": ADAPTER_ROOT / "configure_4_5.py",
    "5.2.0": ADAPTER_ROOT / "configure_5_2.py",
}


class CommandBuildError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RenderCommand:
    arguments: tuple[str, ...]
    job_directory: Path
    output_directory: Path
    log_path: Path


@dataclass(frozen=True, slots=True)
class FrameWorker:
    index: int
    count: int


class BlenderExecutableResolver:
    def __init__(
        self,
        *,
        bundled_root: Path,
        installed_root: Path,
    ) -> None:
        self._bundled_root = bundled_root
        self._installed_root = installed_root

    def binary(self, version: str, source: RuntimeSource) -> Path:
        root = self._bundled_root if source is RuntimeSource.BUNDLED else self._installed_root
        return root / version / "blender"


def build_render_command(
    job: Job,
    *,
    binary: Path,
    jobs_root: Path,
    worker: FrameWorker | None = None,
) -> RenderCommand:
    if job.scene_path is None:
        raise CommandBuildError("Job has no scene path")
    script = VERSION_SCRIPTS.get(job.blender_version)
    if script is None:
        raise CommandBuildError(f"Blender {job.blender_version} has no verified adapter")
    if not script.is_file():
        raise CommandBuildError("Trusted Blender configure script is missing")
    if worker is not None and (
        worker.count < 1 or worker.index < 0 or worker.index >= worker.count
    ):
        raise CommandBuildError("Frame worker index is outside the worker group")
    if worker is not None and not FRAME_DISTRIBUTION_SCRIPT.is_file():
        raise CommandBuildError("Trusted frame distribution script is missing")

    job_directory = contained_path(jobs_root, jobs_root / str(job.id))
    scene_path = contained_path(job_directory, job_directory / job.scene_path)
    output_directory = contained_path(job_directory, job_directory / "output")
    log_name = "blender.log" if worker is None else f"blender-gpu-{worker.index}.log"
    log_path = contained_path(job_directory, job_directory / "logs" / log_name)
    output_pattern = contained_path(output_directory, output_directory / "frame_####")

    arguments = [
        str(binary),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        str(scene_path),
        "--render-output",
        str(output_pattern),
        "--python-exit-code",
        "1",
        "--python",
        str(script),
    ]
    if worker is not None and job.frame_mode is not FrameMode.SINGLE:
        arguments.extend(("--python", str(FRAME_DISTRIBUTION_SCRIPT)))
    if job.frame_mode is FrameMode.SINGLE:
        if job.frame_start is None:
            raise CommandBuildError("Single-frame job is missing its frame")
        arguments.extend(("--render-frame", str(job.frame_start)))
    elif job.frame_mode is FrameMode.RANGE and worker is None:
        if job.frame_start is None or job.frame_end is None:
            raise CommandBuildError("Frame-range job is incomplete")
        arguments.extend(
            (
                "--frame-start",
                str(job.frame_start),
                "--frame-end",
                str(job.frame_end),
                "--render-anim",
            )
        )
    else:
        arguments.append("--render-anim")
    arguments.extend(
        (
            "--",
            "--render-node-engine",
            job.engine.value,
            "--cycles-device",
            job.device.value,
        )
    )
    if worker is not None:
        arguments.extend(
            (
                "--render-node-frame-mode",
                job.frame_mode.value,
                "--render-node-worker-index",
                str(worker.index),
                "--render-node-worker-count",
                str(worker.count),
            )
        )
        if job.frame_mode is FrameMode.RANGE:
            if job.frame_start is None or job.frame_end is None:
                raise CommandBuildError("Frame-range job is incomplete")
            arguments.extend(
                (
                    "--render-node-frame-start",
                    str(job.frame_start),
                    "--render-node-frame-end",
                    str(job.frame_end),
                )
            )
    return RenderCommand(
        arguments=tuple(arguments),
        job_directory=job_directory,
        output_directory=output_directory,
        log_path=log_path,
    )
