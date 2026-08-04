"""Bounded upload streaming and safe ZIP extraction."""

from __future__ import annotations

import asyncio
import gzip
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4
from zipfile import ZipFile, ZipInfo, is_zipfile

import aiofiles
import zstandard
from fastapi import UploadFile

from app.jobs.exceptions import JobConflictError, UploadRejectedError, UploadTooLargeError
from app.storage.jobs import JobStorage, contained_path

UPLOAD_CHUNK_BYTES = 1024 * 1024
BLENDER_HEADER = b"BLENDER"
GZIP_HEADER = b"\x1f\x8b"
ZSTD_HEADER = b"\x28\xb5\x2f\xfd"
MAX_ZSTD_WINDOW_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class StoredUpload:
    source_filename: str
    scene_path: str


def safe_metadata_filename(filename: str | None) -> str:
    if filename is None:
        raise UploadRejectedError("missing_filename", "Upload filename is required")
    normalized = filename.replace("\\", "/")
    cleaned = normalized.rsplit("/", maxsplit=1)[-1].strip()
    if not cleaned or len(cleaned) > 255 or any(ord(character) < 32 for character in cleaned):
        raise UploadRejectedError("invalid_filename", "Upload filename is invalid")
    return cleaned


def _validated_member_path(info: ZipInfo) -> PurePosixPath:
    name = info.filename
    if "\\" in name or "\x00" in name or re.match(r"^[A-Za-z]:", name):
        raise UploadRejectedError("unsafe_zip_path", "ZIP contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UploadRejectedError("unsafe_zip_path", "ZIP contains an unsafe path")
    return path


def _validate_member_type(info: ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise UploadRejectedError("encrypted_zip", "Encrypted ZIP entries are not supported")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise UploadRejectedError(
            "unsafe_zip_entry", "ZIP symlinks and special files are not allowed"
        )


def _extract_zip(
    archive_path: Path,
    destination: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> PurePosixPath:
    if not is_zipfile(archive_path):
        raise UploadRejectedError("invalid_zip", "Uploaded file is not a valid ZIP archive")

    with ZipFile(archive_path) as archive:
        file_count = 0
        declared_size = 0
        seen_paths: set[str] = set()
        blend_paths: list[PurePosixPath] = []
        validated: list[tuple[ZipInfo, PurePosixPath]] = []

        for info in archive.infolist():
            _validate_member_type(info)
            member_path = _validated_member_path(info)
            identity = member_path.as_posix().casefold()
            if identity in seen_paths:
                raise UploadRejectedError("duplicate_zip_path", "ZIP contains duplicate paths")
            seen_paths.add(identity)
            validated.append((info, member_path))

            if info.is_dir():
                continue
            file_count += 1
            declared_size += info.file_size
            if file_count > max_files:
                raise UploadRejectedError("too_many_zip_files", "ZIP contains too many files")
            if declared_size > max_bytes:
                raise UploadTooLargeError(
                    "zip_extracted_too_large", "ZIP extracted size exceeds the configured limit"
                )
            if member_path.suffix.lower() == ".blend":
                blend_paths.append(member_path)

        if len(blend_paths) != 1:
            raise UploadRejectedError(
                "zip_scene_count",
                "ZIP must contain exactly one .blend scene",
                details={"blend_file_count": len(blend_paths)},
            )

        actual_size = 0
        destination.mkdir(parents=True, exist_ok=False)
        for info, member_path in validated:
            target = contained_path(destination, destination.joinpath(*member_path.parts))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                while chunk := source.read(UPLOAD_CHUNK_BYTES):
                    actual_size += len(chunk)
                    if actual_size > max_bytes:
                        raise UploadTooLargeError(
                            "zip_extracted_too_large",
                            "ZIP extracted size exceeds the configured limit",
                        )
                    output.write(chunk)
        return blend_paths[0]


class UploadStorage:
    def __init__(
        self,
        job_storage: JobStorage,
        *,
        max_upload_bytes: int,
        max_zip_files: int,
        max_zip_extracted_bytes: int,
    ) -> None:
        self._jobs = job_storage
        self._max_upload_bytes = max_upload_bytes
        self._max_zip_files = max_zip_files
        self._max_zip_extracted_bytes = max_zip_extracted_bytes

    async def store(self, job_id: UUID, upload: UploadFile) -> StoredUpload:
        source_filename = safe_metadata_filename(upload.filename)
        suffix = Path(source_filename).suffix.lower()
        if suffix not in {".blend", ".zip"}:
            raise UploadRejectedError(
                "unsupported_upload_type", "Only .blend and .zip uploads are accepted"
            )

        job_directory = self._jobs.job_directory(job_id)
        temp_path = contained_path(job_directory, job_directory / "temp" / f"upload-{uuid4()}.part")
        staging = contained_path(job_directory, job_directory / "temp" / f"input-{uuid4()}")
        input_directory = contained_path(job_directory, job_directory / "input")
        backup = contained_path(job_directory, job_directory / "temp" / f"backup-{uuid4()}")

        try:
            await self._stream(upload, temp_path)
            if suffix == ".blend":
                await self._validate_blend(temp_path)

                def stage_blend() -> None:
                    staging.mkdir()
                    temp_path.replace(staging / "scene.blend")

                await asyncio.to_thread(stage_blend)
                scene_relative = PurePosixPath("scene.blend")
            else:
                scene_relative = await asyncio.to_thread(
                    _extract_zip,
                    temp_path,
                    staging,
                    max_files=self._max_zip_files,
                    max_bytes=self._max_zip_extracted_bytes,
                )

            await asyncio.to_thread(self._commit_input, staging, input_directory, backup)
            return StoredUpload(
                source_filename=source_filename,
                scene_path=f"input/{scene_relative.as_posix()}",
            )
        except FileExistsError as exc:
            raise JobConflictError(
                "job_already_uploaded", "Job already has an uploaded scene"
            ) from exc
        finally:
            await upload.close()
            await asyncio.to_thread(temp_path.unlink, missing_ok=True)
            if await asyncio.to_thread(staging.exists):
                await asyncio.to_thread(shutil.rmtree, staging)

    @staticmethod
    def _commit_input(staging: Path, destination: Path, backup: Path) -> None:
        replaced = destination.exists()
        if replaced:
            destination.replace(backup)
        try:
            staging.replace(destination)
        except Exception:
            if replaced and backup.exists():
                backup.replace(destination)
            raise
        if replaced:
            shutil.rmtree(backup, ignore_errors=True)

    async def _stream(self, upload: UploadFile, destination: Path) -> None:
        size = 0
        async with aiofiles.open(destination, "xb") as output:
            while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > self._max_upload_bytes:
                    raise UploadTooLargeError(
                        "upload_too_large", "Upload exceeds the configured size limit"
                    )
                await output.write(chunk)
        if size == 0:
            raise UploadRejectedError("empty_upload", "Upload cannot be empty")

    @staticmethod
    async def _validate_blend(path: Path) -> None:
        async with aiofiles.open(path, "rb") as source:
            header = await source.read(len(BLENDER_HEADER))
        if header == BLENDER_HEADER:
            return
        if header.startswith(GZIP_HEADER):
            valid = await asyncio.to_thread(_compressed_blend_has_header, path, "gzip")
        elif header.startswith(ZSTD_HEADER):
            valid = await asyncio.to_thread(_compressed_blend_has_header, path, "zstd")
        else:
            valid = False
        if not valid:
            raise UploadRejectedError(
                "invalid_blend", "Uploaded file does not have a valid Blender header"
            )


def _compressed_blend_has_header(path: Path, compression: str) -> bool:
    try:
        if compression == "gzip":
            with gzip.open(path, "rb") as source:
                return source.read(len(BLENDER_HEADER)) == BLENDER_HEADER

        decompressor = zstandard.ZstdDecompressor(max_window_size=MAX_ZSTD_WINDOW_BYTES)
        with path.open("rb") as compressed, decompressor.stream_reader(compressed) as source:
            return source.read(len(BLENDER_HEADER)) == BLENDER_HEADER
    except (EOFError, OSError, zstandard.ZstdError):
        return False
