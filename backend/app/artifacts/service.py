"""Artifact registration, preview generation, and contained file delivery."""

from __future__ import annotations

import asyncio
import math
import mimetypes
import os
import re
import stat
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image

from app.artifacts.exceptions import ArtifactNotFoundError, ArtifactRejectedError
from app.artifacts.models import Artifact
from app.artifacts.repository import ArtifactRepository
from app.artifacts.schemas import FramePageResponse, FrameResponse
from app.artifacts.types import ArtifactKind
from app.events.hub import EventHub
from app.jobs.repository import JobRepository
from app.storage.database import Database
from app.storage.jobs import JobStorage, contained_path

FRAME_PATTERN = re.compile(r"(?P<frame>\d{1,8})(?=\.[^.]+$)")
ORIGINAL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".exr"}
PREVIEW_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    path: Path
    filename: str
    content_type: str


class ArtifactService:
    def __init__(
        self,
        database: Database,
        storage: JobStorage,
        event_hub: EventHub,
        *,
        preview_max_width: int,
        preview_max_height: int,
        preview_max_pixels: int,
        max_zip_bytes: int,
    ) -> None:
        self._database = database
        self._storage = storage
        self._events = event_hub
        self._preview_size = (preview_max_width, preview_max_height)
        self._preview_max_pixels = preview_max_pixels
        self._max_zip_bytes = max_zip_bytes
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def register_output(self, job_id: UUID, output_path: Path) -> None:
        job_root = self._storage.job_directory(job_id)
        output_root = contained_path(job_root, job_root / "output")
        if await asyncio.to_thread(output_path.is_symlink):
            raise ArtifactRejectedError(
                "unsafe_render_output", "Render output must not be a symbolic link"
            )
        path = contained_path(output_root, output_path)
        if path.suffix.lower() not in ORIGINAL_EXTENSIONS:
            return
        frame_match = FRAME_PATTERN.search(path.name)
        if frame_match is None:
            return
        frame = int(frame_match["frame"])
        lock = self._locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            file_stat = await asyncio.to_thread(path.stat)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ArtifactRejectedError(
                    "unsafe_render_output", "Render output must be a regular contained file"
                )
            original = await self._upsert(
                job_id,
                ArtifactKind.FRAME_ORIGINAL,
                path,
                frame=frame,
                filename=f"frame_{frame:04d}{path.suffix.lower()}",
                content_type=self._content_type(path),
                size_bytes=file_stat.st_size,
            )
            preview: Artifact | None = None
            if path.suffix.lower() in PREVIEW_EXTENSIONS:
                preview_path = contained_path(
                    job_root, job_root / "preview" / f"frame_{frame:04d}.png"
                )
                try:
                    await asyncio.to_thread(self._create_preview, path, preview_path)
                    preview_stat = await asyncio.to_thread(preview_path.stat)
                    preview = await self._upsert(
                        job_id,
                        ArtifactKind.FRAME_PREVIEW,
                        preview_path,
                        frame=frame,
                        filename=preview_path.name,
                        content_type="image/png",
                        size_bytes=preview_stat.st_size,
                    )
                except (
                    OSError,
                    ValueError,
                    Image.DecompressionBombError,
                    Image.DecompressionBombWarning,
                ):
                    preview = None

        original_url = f"/api/v1/jobs/{job_id}/frames/{frame}/original"
        preview_url = (
            f"/api/v1/jobs/{job_id}/frames/{frame}/preview" if preview is not None else None
        )
        if preview is not None:
            await self._events.publish(
                "render.preview_ready",
                job_id=str(job_id),
                artifact_id=str(preview.id),
                frame=frame,
                url=preview_url,
            )
        await self._events.publish(
            "render.frame_ready",
            job_id=str(job_id),
            frame=frame,
            artifact_id=str(original.id),
            preview_url=preview_url,
            original_url=original_url,
            downloadable=True,
        )

    async def register_log(self, job_id: UUID) -> Artifact | None:
        job_root = self._storage.job_directory(job_id)
        log_path = contained_path(job_root, job_root / "logs" / "blender.log")
        if not await asyncio.to_thread(log_path.is_file):
            return None
        file_stat = await asyncio.to_thread(log_path.stat)
        artifact = await self._upsert(
            job_id,
            ArtifactKind.BLENDER_LOG,
            log_path,
            frame=None,
            filename="blender.log",
            content_type="text/plain; charset=utf-8",
            size_bytes=file_stat.st_size,
        )
        await self._events.publish(
            "render.log_ready",
            job_id=str(job_id),
            artifact_id=str(artifact.id),
            url=f"/api/v1/jobs/{job_id}/logs/blender",
        )
        return artifact

    async def list_artifacts(self, job_id: UUID) -> list[Artifact]:
        await self._require_job(job_id)
        async with self._database.session_factory() as session:
            return await ArtifactRepository(session).list_for_job(job_id)

    async def frame_page(self, job_id: UUID, *, page: int, page_size: int) -> FramePageResponse:
        await self._require_job(job_id)
        async with self._database.session_factory() as session:
            repository = ArtifactRepository(session)
            all_originals = await repository.list_all_frames(job_id, ArtifactKind.FRAME_ORIGINAL)
            originals = all_originals[(page - 1) * page_size : page * page_size]
            items: list[FrameResponse] = []
            for original in originals:
                if original.frame is None:
                    continue
                preview = await repository.get_by_kind_frame(
                    job_id, ArtifactKind.FRAME_PREVIEW, original.frame
                )
                items.append(
                    FrameResponse(
                        frame=original.frame,
                        filename=original.filename,
                        size_bytes=original.size_bytes,
                        original_artifact_id=original.id,
                        original_url=(f"/api/v1/jobs/{job_id}/frames/{original.frame}/original"),
                        preview_artifact_id=preview.id if preview is not None else None,
                        preview_url=(
                            f"/api/v1/jobs/{job_id}/frames/{original.frame}/preview"
                            if preview is not None
                            else None
                        ),
                    )
                )
        total = len(all_originals)
        return FramePageResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            pages=math.ceil(total / page_size) if total else 0,
        )

    async def artifact_file(self, job_id: UUID, artifact_id: UUID) -> ArtifactFile:
        artifact = await self._artifact(job_id, artifact_id)
        return await self._delivery(job_id, artifact)

    async def frame_file(self, job_id: UUID, frame: int, kind: ArtifactKind) -> ArtifactFile:
        async with self._database.session_factory() as session:
            artifact = await ArtifactRepository(session).get_by_kind_frame(job_id, kind, frame)
        if artifact is None:
            raise ArtifactNotFoundError("Requested frame artifact was not found")
        return await self._delivery(job_id, artifact)

    async def log_file(self, job_id: UUID) -> ArtifactFile:
        await self._require_job(job_id)
        job_root = self._storage.job_directory(job_id)
        path = contained_path(job_root, job_root / "logs" / "blender.log")
        if not await asyncio.to_thread(path.is_file):
            raise ArtifactNotFoundError("Blender log is not available")
        return ArtifactFile(path=path, filename="blender.log", content_type="text/plain")

    async def log_tail(self, job_id: UUID, *, lines: int) -> list[str]:
        delivery = await self.log_file(job_id)
        return await asyncio.to_thread(self._read_tail, delivery.path, lines)

    async def frames_zip(self, job_id: UUID) -> ArtifactFile:
        await self._require_job(job_id)
        lock = self._locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            async with self._database.session_factory() as session:
                originals = await ArtifactRepository(session).list_all_frames(
                    job_id, ArtifactKind.FRAME_ORIGINAL
                )
            if not originals:
                raise ArtifactNotFoundError("No completed frames are available")
            job_root = self._storage.job_directory(job_id)
            destination = contained_path(job_root, job_root / "output" / "frames.zip")
            temporary = contained_path(job_root, job_root / "temp" / f"frames-{uuid4()}.zip.part")
            deliveries = [await self._delivery(job_id, item) for item in originals]
            files = [(delivery.path, delivery.filename) for delivery in deliveries]
            try:
                await asyncio.to_thread(self._write_zip, temporary, files)
                size = (await asyncio.to_thread(temporary.stat)).st_size
                if size > self._max_zip_bytes:
                    raise ArtifactRejectedError(
                        "frames_zip_too_large", "Frames ZIP exceeds the result size limit"
                    )
                await asyncio.to_thread(os.replace, temporary, destination)
            finally:
                if await asyncio.to_thread(temporary.exists):
                    await asyncio.to_thread(temporary.unlink)
            artifact = await self._upsert(
                job_id,
                ArtifactKind.FRAMES_ZIP,
                destination,
                frame=None,
                filename="frames.zip",
                content_type="application/zip",
                size_bytes=size,
            )
        return await self._delivery(job_id, artifact)

    async def delete(self, job_id: UUID, artifact_id: UUID) -> None:
        lock = self._locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            async with self._database.session_factory() as session, session.begin():
                repository = ArtifactRepository(session)
                artifact = await repository.get(artifact_id)
                if artifact is None or artifact.job_id != job_id:
                    raise ArtifactNotFoundError
                path = (await self._delivery(job_id, artifact)).path
                await repository.delete(artifact)
            if await asyncio.to_thread(path.exists):
                await asyncio.to_thread(path.unlink)

    async def _artifact(self, job_id: UUID, artifact_id: UUID) -> Artifact:
        async with self._database.session_factory() as session:
            artifact = await ArtifactRepository(session).get(artifact_id)
        if artifact is None or artifact.job_id != job_id:
            raise ArtifactNotFoundError
        return artifact

    async def _upsert(
        self,
        job_id: UUID,
        kind: ArtifactKind,
        path: Path,
        *,
        frame: int | None,
        filename: str,
        content_type: str,
        size_bytes: int,
    ) -> Artifact:
        job_root = self._storage.job_directory(job_id)
        relative_path = str(contained_path(job_root, path).relative_to(job_root))
        async with self._database.session_factory() as session, session.begin():
            repository = ArtifactRepository(session)
            artifact = await repository.get_by_kind_frame(job_id, kind, frame)
            if artifact is None:
                artifact = await repository.add(
                    Artifact(
                        job_id=job_id,
                        kind=kind,
                        relative_path=relative_path,
                        filename=filename,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        frame=frame,
                    )
                )
            else:
                artifact.relative_path = relative_path
                artifact.filename = filename
                artifact.content_type = content_type
                artifact.size_bytes = size_bytes
            return artifact

    async def _require_job(self, job_id: UUID) -> None:
        async with self._database.session_factory() as session:
            if await JobRepository(session).get(job_id) is None:
                raise ArtifactNotFoundError("Job was not found")

    async def _delivery(self, job_id: UUID, artifact: Artifact) -> ArtifactFile:
        return await asyncio.to_thread(self._delivery_sync, job_id, artifact)

    def _delivery_sync(self, job_id: UUID, artifact: Artifact) -> ArtifactFile:
        job_root = self._storage.job_directory(job_id)
        path = contained_path(job_root, job_root / artifact.relative_path)
        if not path.is_file() or path.is_symlink():
            raise ArtifactNotFoundError("Artifact file is unavailable")
        return ArtifactFile(
            path=path, filename=artifact.filename, content_type=artifact.content_type
        )

    def _create_preview(self, source: Path, destination: Path) -> None:
        Image.MAX_IMAGE_PIXELS = self._preview_max_pixels
        temporary = destination.with_suffix(f".{uuid4()}.part")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as image:
                    image.load()
                    if image.width * image.height > self._preview_max_pixels:
                        raise ValueError("image exceeds preview pixel limit")
                    image.thumbnail(self._preview_size)
                    preview_image = (
                        image
                        if image.mode in {"RGB", "RGBA"}
                        else image.convert("RGBA" if "A" in image.getbands() else "RGB")
                    )
                    preview_image.save(temporary, format="PNG", optimize=True)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _content_type(path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    @staticmethod
    def _read_tail(path: Path, lines: int) -> list[str]:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            source.seek(max(0, size - 256 * 1024))
            content = source.read().decode("utf-8", errors="replace")
        return content.splitlines()[-lines:]

    @staticmethod
    def _write_zip(destination: Path, files: list[tuple[Path, str]]) -> None:
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as archive:
            for path, filename in files:
                archive.write(path, arcname=filename)
