"""Server-generated and contained per-job filesystem paths."""

from __future__ import annotations

import asyncio
import shutil
import stat
from pathlib import Path
from uuid import UUID


def contained_path(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("path escapes its storage root") from exc
    return resolved_candidate


class JobStorage:
    def __init__(self, jobs_root: Path) -> None:
        self.jobs_root = jobs_root.resolve()

    def job_directory(self, job_id: UUID) -> Path:
        return contained_path(self.jobs_root, self.jobs_root / str(job_id))

    async def prepare_root(self) -> None:
        await asyncio.to_thread(self.jobs_root.mkdir, parents=True, exist_ok=True)

    async def cleanup_runtime_temporaries(self) -> None:
        await asyncio.to_thread(self._cleanup_runtime_temporaries_sync)

    async def create_job(self, job_id: UUID) -> None:
        job_directory = self.job_directory(job_id)

        def create() -> None:
            try:
                job_directory.mkdir(parents=False, exist_ok=False)
                for child in ("output", "preview", "logs", "temp"):
                    (job_directory / child).mkdir()
            except Exception:
                if job_directory.exists():
                    shutil.rmtree(job_directory)
                raise

        await asyncio.to_thread(create)

    async def delete_job(self, job_id: UUID) -> None:
        job_directory = self.job_directory(job_id)
        if await asyncio.to_thread(job_directory.exists):
            await asyncio.to_thread(shutil.rmtree, job_directory)

    async def delete_input(self, job_id: UUID) -> None:
        input_directory = self.job_directory(job_id) / "input"
        await asyncio.to_thread(self._remove_path, input_directory)

    async def reset_runtime(self, job_id: UUID) -> None:
        job_directory = self.job_directory(job_id)
        await asyncio.to_thread(self._reset_runtime_sync, job_directory)

    async def scene_exists(self, job_id: UUID, scene_path: str | None) -> bool:
        if scene_path is None:
            return False
        return await asyncio.to_thread(self._scene_exists_sync, job_id, scene_path)

    def _cleanup_runtime_temporaries_sync(self) -> None:
        for job_directory in self.jobs_root.iterdir():
            try:
                UUID(job_directory.name)
            except ValueError:
                continue
            if job_directory.is_symlink() or not job_directory.is_dir():
                continue
            self._replace_directory(job_directory / "temp")

    @classmethod
    def _reset_runtime_sync(cls, job_directory: Path) -> None:
        for name in ("output", "preview", "logs", "temp"):
            cls._replace_directory(job_directory / name)

    def _scene_exists_sync(self, job_id: UUID, scene_path: str) -> bool:
        job_directory = self.job_directory(job_id)
        try:
            path = contained_path(job_directory, job_directory / scene_path)
            file_stat = path.lstat()
        except (OSError, ValueError):
            return False
        return stat.S_ISREG(file_stat.st_mode) and not path.is_symlink()

    @staticmethod
    def _replace_directory(path: Path) -> None:
        JobStorage._remove_path(path)
        path.mkdir()

    @staticmethod
    def _remove_path(path: Path) -> None:
        try:
            file_stat = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISDIR(file_stat.st_mode) and not stat.S_ISLNK(file_stat.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()
