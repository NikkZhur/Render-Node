"""Server-generated and contained per-job filesystem paths."""

from __future__ import annotations

import asyncio
import shutil
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
        input_directory = contained_path(
            self.job_directory(job_id), self.job_directory(job_id) / "input"
        )
        if await asyncio.to_thread(input_directory.exists):
            await asyncio.to_thread(shutil.rmtree, input_directory)
