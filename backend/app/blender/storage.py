"""Constrained archive storage and installation primitives."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import signal
import stat
import tarfile
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import aiofiles
from fastapi import UploadFile

from app.blender.exceptions import BlenderRejectedError


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


class BlenderStorage:
    def __init__(
        self,
        versions_root: Path,
        downloads_root: Path,
        quarantine_root: Path,
        *,
        max_archive_bytes: int,
        max_extracted_bytes: int,
        max_files: int,
    ) -> None:
        self.versions_root = versions_root
        self.downloads_root = downloads_root
        self.quarantine_root = quarantine_root
        self.max_archive_bytes = max_archive_bytes
        self.max_extracted_bytes = max_extracted_bytes
        self.max_files = max_files

    async def prepare(self) -> None:
        for path in (self.versions_root, self.downloads_root, self.quarantine_root):
            await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)

    async def cleanup_quarantine(self) -> None:
        await asyncio.to_thread(self._clear_directory, self.quarantine_root)

    async def cleanup_unreferenced_downloads(self, referenced: set[Path]) -> None:
        resolved_references = {path.resolve() for path in referenced}
        await asyncio.to_thread(self._remove_unreferenced, self.downloads_root, resolved_references)

    def download_path(self, operation_id: UUID) -> Path:
        return self.downloads_root / f"{operation_id}.archive"

    def quarantine_path(self, operation_id: UUID) -> Path:
        return self.quarantine_root / f"{operation_id}.archive"

    async def store_upload(self, upload: UploadFile, operation_id: UUID) -> tuple[Path, str, int]:
        filename = upload.filename or ""
        if not filename.lower().endswith((".tar.xz", ".tar.bz2")):
            await upload.close()
            raise BlenderRejectedError(
                "invalid_blender_archive_type", "Manual Blender archive must be .tar.xz or .tar.bz2"
            )
        destination = self.quarantine_path(operation_id)
        digest = hashlib.sha256()
        processed = 0
        available_bytes = (await asyncio.to_thread(shutil.disk_usage, self.quarantine_root)).free
        try:
            async with aiofiles.open(destination, "xb") as target:
                while chunk := await upload.read(1024 * 1024):
                    processed += len(chunk)
                    if processed > self.max_archive_bytes:
                        raise BlenderRejectedError(
                            "blender_archive_too_large", "Blender archive exceeds its size limit"
                        )
                    if processed > available_bytes:
                        raise BlenderRejectedError(
                            "blender_storage_full",
                            "Not enough free space for the Blender archive",
                        )
                    digest.update(chunk)
                    await target.write(chunk)
        except Exception:
            await self.remove_file(destination)
            raise
        finally:
            await upload.close()
        return destination, digest.hexdigest(), processed

    async def promote_quarantine(self, source: Path, operation_id: UUID) -> Path:
        destination = self.download_path(operation_id)
        await asyncio.to_thread(os.replace, source, destination)
        return destination

    async def verify_sha256(self, archive: Path, expected: str) -> str:
        actual = await asyncio.to_thread(self._sha256, archive)
        if actual != expected:
            raise BlenderRejectedError(
                "blender_checksum_mismatch", "Archive does not match the official SHA-256"
            )
        return actual

    async def install(self, archive: Path, version: str) -> Path:
        if not _inside(archive, self.downloads_root):
            raise BlenderRejectedError("unsafe_archive_path", "Archive path escaped storage")
        temporary = self.versions_root / f".install-{uuid4()}"
        destination = self.versions_root / version
        if destination.exists():
            raise BlenderRejectedError(
                "blender_already_installed", "Version directory already exists"
            )
        try:
            await asyncio.to_thread(temporary.mkdir, parents=False, exist_ok=False)
            runtime_root = await asyncio.to_thread(self._extract, archive, temporary)
            await self._validate_binary(runtime_root / "blender", version)
            await asyncio.to_thread(os.replace, runtime_root, destination)
            return destination
        finally:
            if temporary.exists():
                await asyncio.to_thread(shutil.rmtree, temporary)

    async def delete_runtime(self, version: str) -> None:
        path = self.versions_root / version
        if _inside(path, self.versions_root) and path.exists():
            await asyncio.to_thread(shutil.rmtree, path)

    async def remove_file(self, path: Path) -> None:
        exists = await asyncio.to_thread(path.exists)
        if exists and (_inside(path, self.downloads_root) or _inside(path, self.quarantine_root)):
            await asyncio.to_thread(path.unlink, missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _extract(self, archive: Path, destination: Path) -> Path:
        try:
            bundle = tarfile.open(archive, mode="r:*")
        except (tarfile.TarError, OSError) as exc:
            raise BlenderRejectedError(
                "invalid_blender_archive", "Archive cannot be opened"
            ) from exc
        with bundle:
            members: list[tarfile.TarInfo] = []
            total = 0
            top_levels: set[str] = set()
            for member in bundle:
                members.append(member)
                if len(members) > self.max_files:
                    raise BlenderRejectedError(
                        "blender_archive_too_many_files", "Archive has too many files"
                    )
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts:
                    raise BlenderRejectedError(
                        "unsafe_blender_archive", "Archive contains an unsafe path"
                    )
                top_levels.add(name.parts[0])
                if member.ischr() or member.isblk() or member.isfifo() or member.issparse():
                    raise BlenderRejectedError(
                        "unsafe_blender_archive", "Archive contains a special file"
                    )
                total += max(member.size, 0)
                if total > self.max_extracted_bytes:
                    raise BlenderRejectedError(
                        "blender_archive_too_large", "Expanded archive is too large"
                    )
                if member.issym() or member.islnk():
                    target = PurePosixPath(member.linkname)
                    if target.is_absolute() or ".." in target.parts:
                        raise BlenderRejectedError(
                            "unsafe_blender_archive", "Archive contains an unsafe link"
                        )
            if len(top_levels) != 1:
                raise BlenderRejectedError(
                    "invalid_blender_archive", "Archive needs one top-level directory"
                )
            if total > shutil.disk_usage(destination).free:
                raise BlenderRejectedError(
                    "blender_storage_full", "Not enough free space to install Blender"
                )
            try:
                bundle.extractall(destination, members=members, filter="data")
            except (tarfile.TarError, OSError) as exc:
                raise BlenderRejectedError(
                    "unsafe_blender_archive", "Archive extraction failed"
                ) from exc
        runtime_root = destination / next(iter(top_levels))
        if not runtime_root.is_dir():
            raise BlenderRejectedError("invalid_blender_archive", "Runtime directory is missing")
        return runtime_root

    @staticmethod
    async def _validate_binary(binary: Path, version: str) -> None:
        is_executable = await asyncio.to_thread(BlenderStorage._is_executable, binary)
        if not is_executable:
            raise BlenderRejectedError("invalid_blender_runtime", "Blender executable is missing")
        process = await asyncio.create_subprocess_exec(
            str(binary),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=binary.parent,
            env={
                "HOME": str(binary.parent),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "TMPDIR": str(binary.parent),
            },
            close_fds=True,
            start_new_session=True,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.communicate()
            raise BlenderRejectedError(
                "invalid_blender_runtime", "Blender version check timed out"
            ) from None
        first_line = output.decode("utf-8", errors="replace").splitlines()[:1]
        if (
            process.returncode != 0
            or not first_line
            or not first_line[0].startswith(f"Blender {version}")
        ):
            raise BlenderRejectedError(
                "invalid_blender_runtime", "Blender binary reported another version"
            )

    @staticmethod
    def _is_executable(binary: Path) -> bool:
        try:
            return binary.is_file() and bool(binary.stat().st_mode & stat.S_IXUSR)
        except OSError:
            return False

    @staticmethod
    def _clear_directory(root: Path) -> None:
        for item in root.iterdir():
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)

    @staticmethod
    def _remove_unreferenced(root: Path, referenced: set[Path]) -> None:
        for item in root.iterdir():
            if item.resolve() in referenced:
                continue
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)
