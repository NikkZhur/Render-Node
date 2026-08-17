"""Blender registry application service and operation lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import UploadFile

from app.blender.exceptions import (
    BlenderConflictError,
    BlenderNotFoundError,
    BlenderOperationNotFoundError,
    BlenderRejectedError,
)
from app.blender.models import BlenderOperation, BlenderRuntime
from app.blender.official import CatalogError, OfficialCatalog, OfficialRelease
from app.blender.repository import BlenderRepository
from app.blender.state_machine import transition_operation, transition_runtime
from app.blender.storage import BlenderStorage
from app.blender.types import (
    BUNDLED_VERSIONS,
    SUPPORTED_VERSIONS,
    OperationKind,
    OperationState,
    RuntimeSource,
    RuntimeState,
)
from app.events.hub import EventHub
from app.jobs.models import utc_now
from app.jobs.repository import JobRepository
from app.storage.database import Database


class BlenderService:
    def __init__(
        self,
        database: Database,
        storage: BlenderStorage,
        catalog: OfficialCatalog,
        event_hub: EventHub,
        *,
        bundled_root: Path = Path("/opt/render-node/blender"),
    ) -> None:
        self._database = database
        self._storage = storage
        self._catalog = catalog
        self._events = event_hub
        self._bundled_root = bundled_root
        self._mutation_lock = asyncio.Lock()
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    async def initialize(self) -> None:
        """Recover interrupted work and reconcile verified image runtimes."""
        bundled_versions: set[str] = set()
        for version in BUNDLED_VERSIONS:
            binary = self._bundled_root / version / "blender"
            try:
                await self._storage.validate_binary(binary, version)
            except BlenderRejectedError:
                continue
            bundled_versions.add(version)

        async with self._database.session_factory() as session:
            installed_runtimes = [
                (runtime.version, runtime.source)
                for runtime in await BlenderRepository(session).list_runtimes()
                if runtime.source is not RuntimeSource.BUNDLED
                and runtime.state is RuntimeState.INSTALLED
            ]

        valid_installed_versions: set[str] = set()
        invalid_installed_versions: dict[str, str] = {}
        for version, _source in installed_runtimes:
            binary = self._storage.versions_root / version / "blender"
            try:
                await self._storage.validate_binary(binary, version)
            except BlenderRejectedError as exc:
                invalid_installed_versions[version] = exc.message
                continue
            valid_installed_versions.add(version)

        runtime_directories_to_remove = set(invalid_installed_versions)
        async with self._database.session_factory() as session, session.begin():
            repository = BlenderRepository(session)
            for operation in await repository.unfinished_operations():
                operation.state = OperationState.FAILED
                operation.error = "Operation was interrupted by a service restart"
                operation.finished_at = utc_now()
            for runtime in await repository.list_runtimes():
                if (
                    runtime.source is RuntimeSource.BUNDLED
                    and runtime.version not in bundled_versions
                ):
                    await repository.delete_runtime(runtime)
                    continue
                if runtime.version in invalid_installed_versions:
                    runtime.state = RuntimeState.FAILED
                    runtime.active = False
                    runtime.error = invalid_installed_versions[runtime.version]
                    runtime.updated_at = utc_now()
                if runtime.state in {RuntimeState.DOWNLOADING, RuntimeState.INSTALLING}:
                    runtime.state = RuntimeState.FAILED
                    runtime.error = "Operation was interrupted by a service restart"
                    runtime.updated_at = utc_now()
            for version in bundled_versions:
                existing_runtime = await repository.get_runtime(version)
                if existing_runtime is None:
                    await repository.add_runtime(
                        BlenderRuntime(
                            version=version,
                            source=RuntimeSource.BUNDLED,
                            state=RuntimeState.INSTALLED,
                            supported=True,
                            active=False,
                        )
                    )
                else:
                    previous_source = existing_runtime.source
                    keep_active = existing_runtime.active and (
                        previous_source is RuntimeSource.BUNDLED
                        or existing_runtime.version in valid_installed_versions
                    )
                    if previous_source is not RuntimeSource.BUNDLED:
                        runtime_directories_to_remove.add(version)
                    existing_runtime.source = RuntimeSource.BUNDLED
                    existing_runtime.state = RuntimeState.INSTALLED
                    existing_runtime.supported = True
                    existing_runtime.active = keep_active
                    existing_runtime.archive_path = None
                    existing_runtime.official_filename = None
                    existing_runtime.expected_sha256 = None
                    existing_runtime.verified_sha256 = None
                    existing_runtime.operation_id = None
                    existing_runtime.error = None
                    existing_runtime.updated_at = utc_now()

            active_runtimes = [
                runtime for runtime in await repository.list_runtimes() if runtime.active
            ]
            if len(active_runtimes) > 1:
                for runtime in active_runtimes:
                    runtime.active = False
                    runtime.updated_at = utc_now()

            referenced_archives = {
                Path(runtime.archive_path)
                for runtime in await repository.list_runtimes()
                if runtime.archive_path
            }

        for version in runtime_directories_to_remove:
            # A broken runtime must not make application readiness depend on
            # whether its leftover directory can be removed during startup.
            with contextlib.suppress(OSError):
                await self._storage.delete_runtime(version)
        await self._storage.cleanup_quarantine()
        await self._storage.cleanup_unreferenced_downloads(referenced_archives)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._catalog.close()

    async def list_runtimes(self) -> list[BlenderRuntime]:
        async with self._database.session_factory() as session:
            return await BlenderRepository(session).list_runtimes()

    async def releases(self) -> list[OfficialRelease]:
        try:
            return await self._catalog.releases()
        except (CatalogError, OSError) as exc:
            raise BlenderRejectedError(
                "official_catalog_unavailable", "Official Blender catalog is unavailable"
            ) from exc

    async def get_operation(self, operation_id: UUID) -> BlenderOperation:
        async with self._database.session_factory() as session:
            operation = await BlenderRepository(session).get_operation(operation_id)
            if operation is None:
                raise BlenderOperationNotFoundError
            return operation

    async def start_download(self, version: str) -> BlenderOperation:
        try:
            release = await self._catalog.release(version)
            checksum = await self._catalog.checksum(release)
        except CatalogError as exc:
            raise BlenderNotFoundError(str(exc)) from exc
        async with self._mutation_lock:
            async with self._database.session_factory() as session, session.begin():
                repository = BlenderRepository(session)
                await self._ensure_idle(repository)
                runtime = await repository.get_runtime(version)
                if runtime is not None and runtime.state is RuntimeState.INSTALLED:
                    raise BlenderConflictError(
                        "blender_already_installed", "Blender version is already installed"
                    )
                if runtime is None:
                    runtime = BlenderRuntime(
                        version=version,
                        source=RuntimeSource.OFFICIAL,
                        state=RuntimeState.DOWNLOADING,
                        supported=version in SUPPORTED_VERSIONS,
                        active=False,
                    )
                    await repository.add_runtime(runtime)
                else:
                    runtime.state = transition_runtime(runtime.state, RuntimeState.DOWNLOADING)
                    runtime.source = RuntimeSource.OFFICIAL
                runtime.official_filename = release.filename
                runtime.expected_sha256 = checksum
                runtime.verified_sha256 = None
                runtime.archive_path = None
                runtime.error = None
                runtime.updated_at = utc_now()
                operation = await repository.add_operation(
                    BlenderOperation(
                        kind=OperationKind.DOWNLOAD,
                        version=version,
                        state=OperationState.PENDING,
                    )
                )
                runtime.operation_id = operation.id
            self._spawn(operation.id, self._download(operation.id, release, checksum))
            return operation

    async def upload(self, upload: UploadFile) -> BlenderOperation:
        async with self._mutation_lock:
            async with self._database.session_factory() as session, session.begin():
                repository = BlenderRepository(session)
                await self._ensure_idle(repository)
                operation = await repository.add_operation(
                    BlenderOperation(kind=OperationKind.UPLOAD, state=OperationState.PENDING)
                )
        await self._set_running(operation.id)
        try:
            quarantine, digest, size = await self._storage.store_upload(upload, operation.id)
            await self._set_progress(operation.id, size, size)
        except Exception as exc:
            await self._fail(operation.id, self._public_error(exc))
            return await self.get_operation(operation.id)
        self._spawn(
            operation.id,
            self._verify_upload(operation.id, quarantine, digest),
        )
        return await self.get_operation(operation.id)

    async def _verify_upload(self, operation_id: UUID, quarantine: Path, digest: str) -> None:
        destination: Path | None = None
        try:
            identified = await self._catalog.identify_digest(digest)
            if identified is None:
                raise BlenderRejectedError(
                    "unknown_blender_checksum",
                    "Archive SHA-256 is not present in the official release manifests",
                )
            release, expected = identified
            destination = await self._storage.promote_quarantine(quarantine, operation_id)
            async with self._database.session_factory() as session, session.begin():
                repository = BlenderRepository(session)
                runtime = await repository.get_runtime(release.version)
                if runtime is not None and runtime.state is RuntimeState.INSTALLED:
                    raise BlenderConflictError(
                        "blender_already_installed", "Blender version is already installed"
                    )
                if runtime is None:
                    runtime = BlenderRuntime(
                        version=release.version,
                        source=RuntimeSource.MANUAL,
                        state=RuntimeState.DOWNLOADED,
                        supported=release.version in SUPPORTED_VERSIONS,
                        active=False,
                    )
                    await repository.add_runtime(runtime)
                else:
                    runtime.state = RuntimeState.DOWNLOADED
                    runtime.source = RuntimeSource.MANUAL
                runtime.archive_path = str(destination)
                runtime.official_filename = release.filename
                runtime.expected_sha256 = expected
                runtime.verified_sha256 = digest
                runtime.operation_id = operation_id
                runtime.error = None
                runtime.updated_at = utc_now()
                current = await repository.get_operation(operation_id)
                if current is None:
                    raise BlenderOperationNotFoundError
                current.version = release.version
            await self._complete(operation_id)
        except BaseException as exc:
            await self._storage.remove_file(quarantine)
            if destination is not None:
                await self._storage.remove_file(destination)
            await self._fail(operation_id, self._public_error(exc))
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def start_install(self, version: str) -> BlenderOperation:
        async with self._mutation_lock:
            async with self._database.session_factory() as session, session.begin():
                repository = BlenderRepository(session)
                await self._ensure_idle(repository)
                runtime = await repository.get_runtime(version)
                if runtime is None:
                    raise BlenderNotFoundError
                can_install = runtime.state in {RuntimeState.DOWNLOADED, RuntimeState.FAILED}
                if not can_install or not runtime.archive_path:
                    raise BlenderConflictError(
                        "blender_not_downloaded",
                        "Blender archive must be downloaded before installation",
                    )
                runtime.state = transition_runtime(runtime.state, RuntimeState.INSTALLING)
                runtime.updated_at = utc_now()
                operation = await repository.add_operation(
                    BlenderOperation(
                        kind=OperationKind.INSTALL,
                        version=version,
                        state=OperationState.PENDING,
                    )
                )
                runtime.operation_id = operation.id
            self._spawn(operation.id, self._install(operation.id, version))
            return operation

    async def activate(self, version: str) -> BlenderRuntime:
        async with self._mutation_lock:
            async with self._database.session_factory() as session, session.begin():
                repository = BlenderRepository(session)
                await self._ensure_idle(repository)
                runtime = await repository.get_runtime(version)
                if runtime is None:
                    raise BlenderNotFoundError
                if runtime.state is not RuntimeState.INSTALLED:
                    raise BlenderConflictError(
                        "blender_not_installed",
                        "Only an installed Blender version can be activated",
                    )
                if await JobRepository(session).active_count() > 0:
                    raise BlenderConflictError(
                        "active_jobs_block_activation",
                        "Blender activation is blocked while jobs are queued or rendering",
                    )
                for item in await repository.list_runtimes():
                    item.active = item.version == version
                    item.updated_at = utc_now()
                return runtime

    async def delete(self, version: str) -> None:
        async with self._mutation_lock:
            archive: Path | None = None
            async with self._database.session_factory() as session, session.begin():
                repository = BlenderRepository(session)
                await self._ensure_idle(repository)
                runtime = await repository.get_runtime(version)
                if runtime is None:
                    raise BlenderNotFoundError
                if runtime.source is RuntimeSource.BUNDLED:
                    raise BlenderConflictError(
                        "bundled_blender_cannot_be_deleted", "Bundled versions cannot be deleted"
                    )
                if runtime.active or await JobRepository(session).reference_count(version):
                    raise BlenderConflictError(
                        "blender_version_in_use",
                        "Active or job-bound Blender version cannot be deleted",
                    )
                if runtime.archive_path:
                    archive = Path(runtime.archive_path)
                await repository.delete_runtime(runtime)
            await self._storage.delete_runtime(version)
            if archive is not None:
                await self._storage.remove_file(archive)

    async def wait(self, operation_id: UUID) -> None:
        task = self._tasks.get(operation_id)
        if task is not None:
            await task

    async def _download(self, operation_id: UUID, release: OfficialRelease, checksum: str) -> None:
        destination = self._storage.download_path(operation_id)
        await self._set_running(operation_id)
        try:
            digest, _ = await self._catalog.download_archive(
                release,
                destination,
                expected_sha256=checksum,
                max_bytes=self._storage.max_archive_bytes,
                on_progress=lambda done, total: self._set_progress(operation_id, done, total),
            )
            async with self._database.session_factory() as session, session.begin():
                runtime = await BlenderRepository(session).get_runtime(release.version)
                if runtime is None:
                    raise BlenderNotFoundError
                runtime.state = transition_runtime(runtime.state, RuntimeState.DOWNLOADED)
                runtime.archive_path = str(destination)
                runtime.verified_sha256 = digest
                runtime.updated_at = utc_now()
            await self._complete(operation_id)
        except BaseException as exc:
            await self._storage.remove_file(destination)
            await self._fail_runtime(release.version, self._public_error(exc))
            await self._fail(operation_id, self._public_error(exc))
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _install(self, operation_id: UUID, version: str) -> None:
        await self._set_running(operation_id)
        try:
            async with self._database.session_factory() as session:
                runtime = await BlenderRepository(session).get_runtime(version)
                if runtime is None or not runtime.archive_path or not runtime.expected_sha256:
                    raise BlenderNotFoundError
                archive = Path(runtime.archive_path)
                expected = runtime.expected_sha256
            digest = await self._storage.verify_sha256(archive, expected)
            await self._storage.install(archive, version)
            async with self._database.session_factory() as session, session.begin():
                runtime = await BlenderRepository(session).get_runtime(version)
                if runtime is None:
                    raise BlenderNotFoundError
                runtime.state = transition_runtime(runtime.state, RuntimeState.INSTALLED)
                runtime.verified_sha256 = digest
                runtime.error = None
                runtime.updated_at = utc_now()
            await self._complete(operation_id)
        except BaseException as exc:
            await self._fail_runtime(version, self._public_error(exc))
            await self._fail(operation_id, self._public_error(exc))
            if isinstance(exc, asyncio.CancelledError):
                raise

    def _spawn(self, operation_id: UUID, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks[operation_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(operation_id, None))

    async def _set_running(self, operation_id: UUID) -> None:
        async with self._database.session_factory() as session, session.begin():
            operation = await BlenderRepository(session).get_operation(operation_id)
            if operation is None:
                raise BlenderOperationNotFoundError
            operation.state = transition_operation(operation.state, OperationState.RUNNING)
        await self._events.publish(
            "blender.operation_progress",
            operation_id=str(operation_id),
            state=operation.state.value,
            progress=operation.progress,
            bytes_processed=operation.bytes_processed,
            bytes_total=operation.bytes_total,
        )

    async def _set_progress(self, operation_id: UUID, done: int, total: int | None) -> None:
        async with self._database.session_factory() as session, session.begin():
            operation = await BlenderRepository(session).get_operation(operation_id)
            if operation is None:
                raise BlenderOperationNotFoundError
            operation.bytes_processed = done
            operation.bytes_total = total
            operation.progress = min(done / total, 1.0) if total else 0.0
        await self._events.publish(
            "blender.operation_progress",
            operation_id=str(operation_id),
            state=operation.state.value,
            progress=operation.progress,
            bytes_processed=done,
            bytes_total=total,
        )

    async def _complete(self, operation_id: UUID) -> None:
        async with self._database.session_factory() as session, session.begin():
            operation = await BlenderRepository(session).get_operation(operation_id)
            if operation is None:
                raise BlenderOperationNotFoundError
            operation.state = transition_operation(operation.state, OperationState.COMPLETED)
            operation.progress = 1.0
            operation.finished_at = utc_now()
        await self._events.publish(
            "blender.operation_completed",
            operation_id=str(operation_id),
            state=operation.state.value,
            progress=operation.progress,
        )

    async def _fail(self, operation_id: UUID, message: str) -> None:
        failed = False
        async with self._database.session_factory() as session, session.begin():
            operation = await BlenderRepository(session).get_operation(operation_id)
            if operation is not None and operation.state in {
                OperationState.PENDING,
                OperationState.RUNNING,
            }:
                operation.state = OperationState.FAILED
                operation.error = message
                operation.finished_at = utc_now()
                failed = True
        if failed:
            await self._events.publish(
                "blender.operation_failed",
                operation_id=str(operation_id),
                state=OperationState.FAILED.value,
                error=message,
            )

    async def _fail_runtime(self, version: str, message: str) -> None:
        async with self._database.session_factory() as session, session.begin():
            runtime = await BlenderRepository(session).get_runtime(version)
            if runtime is not None and runtime.state is not RuntimeState.INSTALLED:
                runtime.state = RuntimeState.FAILED
                runtime.error = message
                runtime.updated_at = utc_now()

    @staticmethod
    async def _ensure_idle(repository: BlenderRepository) -> None:
        if await repository.has_mutating_operation():
            raise BlenderConflictError(
                "blender_operation_in_progress", "Another Blender operation is already running"
            )

    @staticmethod
    def _public_error(exc: BaseException) -> str:
        if isinstance(exc, (BlenderRejectedError, BlenderConflictError)):
            return exc.message
        if isinstance(exc, asyncio.CancelledError):
            return "Operation was interrupted by service shutdown"
        if isinstance(exc, CatalogError):
            return str(exc)
        return "Blender operation failed"
