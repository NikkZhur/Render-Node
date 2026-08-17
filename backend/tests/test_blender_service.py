from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
import time
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient

from app.blender import storage as blender_storage_module
from app.blender.exceptions import BlenderConflictError, BlenderRejectedError
from app.blender.models import BlenderOperation, BlenderRuntime
from app.blender.official import OfficialRelease
from app.blender.repository import BlenderRepository
from app.blender.service import BlenderService
from app.blender.storage import BlenderStorage
from app.blender.types import OperationKind, OperationState, RuntimeSource, RuntimeState
from app.config import Settings
from app.events.hub import EventHub
from app.main import create_app
from app.storage.database import Database


def fake_blender_archive(version: str) -> bytes:
    output = io.BytesIO()
    script = (
        f"#!/bin/sh\nprintf 'Blender {version}\\n'\n"
        'printf "%s" "${BACKEND_SECRET-unset}" > "$PWD/validation-env.txt"\n'
    ).encode()
    with tarfile.open(fileobj=output, mode="w:xz") as bundle:
        directory = tarfile.TarInfo(f"blender-{version}-linux-x64")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        bundle.addfile(directory)
        executable = tarfile.TarInfo(f"blender-{version}-linux-x64/blender")
        executable.mode = 0o755
        executable.size = len(script)
        bundle.addfile(executable, io.BytesIO(script))
    return output.getvalue()


def fake_blender_executable(path: Path, output: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
    path.chmod(0o755)


def wait_for_file(path: Path, timeout_seconds: float = 2) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            return path.read_text()
        except FileNotFoundError:
            time.sleep(0.01)
    raise AssertionError(f"file was not created: {path}")


class FakeCatalog:
    def __init__(self, version: str, archive: bytes) -> None:
        self.version = version
        self.archive = archive
        self.digest = hashlib.sha256(archive).hexdigest()
        self.item = OfficialRelease(
            version=version,
            filename=f"blender-{version}-linux-x64.tar.xz",
            archive_url=(
                f"https://download.blender.org/release/Blender{'.'.join(version.split('.')[:2])}/"
                f"blender-{version}-linux-x64.tar.xz"
            ),
            manifest_url=(
                f"https://download.blender.org/release/Blender{'.'.join(version.split('.')[:2])}/"
                f"blender-{version}.sha256"
            ),
        )

    async def close(self) -> None:
        pass

    async def releases(self) -> list[OfficialRelease]:
        return [self.item]

    async def release(self, version: str) -> OfficialRelease:
        if version != self.version:
            raise RuntimeError("unknown release")
        return self.item

    async def checksum(self, release: OfficialRelease) -> str:
        return self.digest

    async def identify_digest(self, digest: str) -> tuple[OfficialRelease, str] | None:
        return (self.item, self.digest) if digest == self.digest else None

    async def download_archive(
        self,
        release: OfficialRelease,
        destination: Path,
        *,
        expected_sha256: str,
        max_bytes: int,
        on_progress: object,
    ) -> tuple[str, int]:
        assert len(self.archive) <= max_bytes
        await asyncio.to_thread(destination.write_bytes, self.archive)
        await on_progress(len(self.archive), len(self.archive))  # type: ignore[operator]
        return self.digest, len(self.archive)


def make_service(settings: Settings, catalog: FakeCatalog) -> tuple[BlenderService, Database]:
    database = Database(settings.database_url)
    storage = BlenderStorage(
        settings.blender_versions_root,
        settings.blender_downloads_root,
        settings.blender_quarantine_root,
        max_archive_bytes=settings.max_blender_archive_bytes,
        max_extracted_bytes=settings.max_blender_extracted_bytes,
        max_files=settings.max_blender_archive_files,
    )
    return (
        BlenderService(
            database,
            storage,
            catalog,
            EventHub(),
            bundled_root=settings.bundled_blender_root,
        ),
        database,
    )  # type: ignore[arg-type]


async def test_official_download_install_and_explicit_activation(
    job_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = "4.6.1"
    monkeypatch.setenv("BACKEND_SECRET", "must-not-leak")
    catalog = FakeCatalog(version, fake_blender_archive(version))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    await service.initialize()
    try:
        download = await service.start_download(version)
        await service.wait(download.id)
        runtime = next(item for item in await service.list_runtimes() if item.version == version)
        assert runtime.state is RuntimeState.DOWNLOADED
        assert runtime.active is False
        assert runtime.verified_sha256 == catalog.digest

        install = await service.start_install(version)
        await service.wait(install.id)
        runtime = next(item for item in await service.list_runtimes() if item.version == version)
        assert runtime.state is RuntimeState.INSTALLED
        assert runtime.active is False
        assert (job_settings.blender_versions_root / version / "blender").is_file()
        assert (
            job_settings.blender_versions_root / version / "validation-env.txt"
        ).read_text() == "unset"

        activated = await service.activate(version)
        assert activated.active is True
        assert sum(item.active for item in await service.list_runtimes()) == 1
    finally:
        await service.shutdown()
        await database.dispose()


async def test_install_adopts_valid_runtime_left_outside_registry(
    job_settings: Settings,
) -> None:
    version = "4.6.7"
    catalog = FakeCatalog(version, fake_blender_archive(version))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    await service.initialize()
    try:
        download = await service.start_download(version)
        await service.wait(download.id)
        runtime = next(item for item in await service.list_runtimes() if item.version == version)
        assert runtime.archive_path is not None

        existing = await service._storage.install(Path(runtime.archive_path), version)
        assert (existing / "blender").is_file()

        install = await service.start_install(version)
        await service.wait(install.id)

        operation = await service.get_operation(install.id)
        runtime = next(item for item in await service.list_runtimes() if item.version == version)
        assert operation.state is OperationState.COMPLETED
        assert runtime.state is RuntimeState.INSTALLED
    finally:
        await service.shutdown()
        await database.dispose()


def test_failed_install_keeps_verified_archive_available() -> None:
    runtime = BlenderRuntime(
        version="4.6.8",
        source=RuntimeSource.OFFICIAL,
        state=RuntimeState.FAILED,
        supported=False,
        active=False,
        archive_path="/workspace/blender/downloads/archive",
        expected_sha256="a" * 64,
        verified_sha256="a" * 64,
    )

    assert runtime.archive_available is True
    runtime.verified_sha256 = "b" * 64
    assert runtime.archive_available is False


async def test_manual_exact_archive_installs_and_modified_archive_is_rejected(
    job_settings: Settings,
) -> None:
    version = "4.6.2"
    archive = fake_blender_archive(version)
    catalog = FakeCatalog(version, archive)
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    await service.initialize()
    try:
        upload = UploadFile(filename="untrusted-name.tar.xz", file=io.BytesIO(archive))
        operation = await service.upload(upload)
        await service.wait(operation.id)
        operation = await service.get_operation(operation.id)
        assert operation.state is OperationState.COMPLETED
        assert operation.version == version
        runtime = next(item for item in await service.list_runtimes() if item.version == version)
        assert runtime.source is RuntimeSource.MANUAL
        assert runtime.state is RuntimeState.DOWNLOADED

        install = await service.start_install(version)
        await service.wait(install.id)
        assert (await service.get_operation(install.id)).state is OperationState.COMPLETED

        bad_upload = UploadFile(filename="modified.tar.xz", file=io.BytesIO(archive + b"modified"))
        rejected = await service.upload(bad_upload)
        await service.wait(rejected.id)
        rejected = await service.get_operation(rejected.id)
        assert rejected.state is OperationState.FAILED
        assert list(job_settings.blender_quarantine_root.iterdir()) == []
    finally:
        await service.shutdown()
        await database.dispose()


async def test_safe_extract_rejects_parent_path(job_settings: Settings) -> None:
    archive_path = job_settings.blender_downloads_root / "unsafe.archive"
    archive_path.parent.mkdir(parents=True)
    with tarfile.open(archive_path, mode="w:xz") as bundle:
        entry = tarfile.TarInfo("../escape")
        entry.size = 1
        bundle.addfile(entry, io.BytesIO(b"x"))
    storage = BlenderStorage(
        job_settings.blender_versions_root,
        job_settings.blender_downloads_root,
        job_settings.blender_quarantine_root,
        max_archive_bytes=1024 * 1024,
        max_extracted_bytes=1024 * 1024,
        max_files=10,
    )
    await storage.prepare()
    with pytest.raises(BlenderRejectedError, match="unsafe path"):
        await storage.install(archive_path, "4.6.3")
    assert not (job_settings.workspace / "escape").exists()


@pytest.mark.parametrize(
    ("reported", "expected", "accepted"),
    [
        ("Blender 4.1.1", "4.1.1", True),
        ("Blender 5.2.0 LTS", "5.2.0", True),
        ("Blender 4.1.10", "4.1.1", False),
        ("not Blender output", "4.1.1", False),
        ("", "4.1.1", False),
    ],
)
async def test_binary_validation_requires_exact_patch_version(
    tmp_path: Path,
    reported: str,
    expected: str,
    accepted: bool,
) -> None:
    binary = tmp_path / "blender"
    fake_blender_executable(binary, reported)

    if accepted:
        await BlenderStorage.validate_binary(binary, expected)
    else:
        with pytest.raises(BlenderRejectedError, match="another version"):
            await BlenderStorage.validate_binary(binary, expected)


async def test_binary_validation_wraps_exec_format_error(tmp_path: Path) -> None:
    binary = tmp_path / "blender"
    binary.write_bytes(b"not an executable format")
    binary.chmod(0o755)

    with pytest.raises(BlenderRejectedError, match="could not be started"):
        await BlenderStorage.validate_binary(binary, "4.1.1")


@pytest.mark.parametrize("kind", ["missing", "permission", "nonzero"])
async def test_binary_validation_wraps_expected_runtime_failures(
    tmp_path: Path,
    kind: str,
) -> None:
    binary = tmp_path / "blender"
    if kind == "permission":
        binary.write_text("#!/bin/sh\nprintf 'Blender 4.1.1\\n'\n")
    elif kind == "nonzero":
        binary.write_text("#!/bin/sh\nprintf 'Blender 4.1.1\\n'\nexit 3\n")
        binary.chmod(0o755)

    with pytest.raises(BlenderRejectedError):
        await BlenderStorage.validate_binary(binary, "4.1.1")


async def test_binary_validation_timeout_kills_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "blender"
    pid_path = tmp_path / "validator.pid"
    binary.write_text(f"#!/bin/sh\nprintf '%s' $$ > '{pid_path}'\nsleep 60\n")
    binary.chmod(0o755)
    monkeypatch.setattr(blender_storage_module, "BINARY_VALIDATION_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(BlenderRejectedError, match="timed out"):
        await BlenderStorage.validate_binary(binary, "4.1.1")

    pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_binary_validation_cancellation_kills_process_group(tmp_path: Path) -> None:
    binary = tmp_path / "blender"
    pid_path = tmp_path / "cancelled-validator.pid"
    binary.write_text(f"#!/bin/sh\nprintf '%s' $$ > '{pid_path}'\nsleep 60\n")
    binary.chmod(0o755)
    validation = asyncio.create_task(BlenderStorage.validate_binary(binary, "4.1.1"))
    pid = int(await asyncio.to_thread(wait_for_file, pid_path))

    validation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await validation

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_install_does_not_adopt_symlinked_runtime_directory(
    job_settings: Settings,
) -> None:
    version = "4.6.9"
    storage = BlenderStorage(
        job_settings.blender_versions_root,
        job_settings.blender_downloads_root,
        job_settings.blender_quarantine_root,
        max_archive_bytes=1024 * 1024,
        max_extracted_bytes=1024 * 1024,
        max_files=10,
    )
    await storage.prepare()
    archive = job_settings.blender_downloads_root / "verified.archive"
    archive.write_bytes(b"verified elsewhere")
    outside = job_settings.workspace / "outside-runtime"
    outside.mkdir()
    (job_settings.blender_versions_root / version).symlink_to(outside, target_is_directory=True)

    with pytest.raises(BlenderRejectedError) as rejected:
        await storage.install(archive, version)

    assert rejected.value.code == "invalid_blender_runtime"
    assert (job_settings.blender_versions_root / version).is_symlink()


async def test_bundled_registry_api_and_delete_guard(job_client: object) -> None:
    response = await job_client.get("/api/v1/blender/versions")  # type: ignore[attr-defined]
    assert response.status_code == 200
    versions = response.json()
    assert {item["version"] for item in versions} == {"5.2.0", "4.1.1"}
    assert [item["version"] for item in versions if item["active"]] == ["5.2.0"]
    assert all(item["source"] == "bundled" for item in versions)

    deleted = await job_client.delete("/api/v1/blender/versions/5.2.0")  # type: ignore[attr-defined]
    assert deleted.status_code == 409
    assert deleted.json()["error"]["code"] == "bundled_blender_cannot_be_deleted"


async def test_initialize_reconciles_removed_bundled_versions(job_settings: Settings) -> None:
    catalog = FakeCatalog("4.6.0", fake_blender_archive("4.6.0"))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    async with database.session_factory() as session, session.begin():
        await BlenderRepository(session).add_runtime(
            BlenderRuntime(
                version="4.5.11",
                source=RuntimeSource.BUNDLED,
                state=RuntimeState.INSTALLED,
                supported=True,
                active=True,
            )
        )
    try:
        await service.initialize()
        runtimes = await service.list_runtimes()
        assert {runtime.version for runtime in runtimes} == {"5.2.0", "4.1.1"}
        assert [runtime.version for runtime in runtimes if runtime.active] == []
    finally:
        await service.shutdown()
        await database.dispose()


@pytest.mark.parametrize("source", [RuntimeSource.OFFICIAL, RuntimeSource.MANUAL])
async def test_initialize_invalidates_missing_user_runtime_and_preserves_retry_archive(
    job_settings: Settings,
    source: RuntimeSource,
) -> None:
    version = "4.6.10"
    archive = fake_blender_archive(version)
    digest = hashlib.sha256(archive).hexdigest()
    archive_path = job_settings.blender_downloads_root / "retry.archive"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(archive)
    runtime_root = job_settings.blender_versions_root / version
    runtime_root.mkdir(parents=True)
    (runtime_root / "incomplete-runtime").write_text("broken")
    catalog = FakeCatalog(version, archive)
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    async with database.session_factory() as session, session.begin():
        await BlenderRepository(session).add_runtime(
            BlenderRuntime(
                version=version,
                source=source,
                state=RuntimeState.INSTALLED,
                supported=False,
                active=True,
                archive_path=str(archive_path),
                expected_sha256=digest,
                verified_sha256=digest,
            )
        )
    try:
        await service.initialize()
        runtime = next(item for item in await service.list_runtimes() if item.version == version)

        assert runtime.state is RuntimeState.FAILED
        assert runtime.active is False
        assert runtime.archive_available is True
        assert archive_path.is_file()
        assert not runtime_root.exists()
        assert all(not item.active for item in await service.list_runtimes())

        install = await service.start_install(version)
        await service.wait(install.id)
        runtime = next(item for item in await service.list_runtimes() if item.version == version)
        assert runtime.state is RuntimeState.INSTALLED
        assert runtime.active is False
    finally:
        await service.shutdown()
        await database.dispose()


async def test_initialize_promotes_download_collision_without_orphaned_metadata(
    job_settings: Settings,
) -> None:
    version = "4.1.1"
    archive_path = job_settings.blender_downloads_root / "duplicate.archive"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(b"verified duplicate")
    catalog = FakeCatalog("4.6.0", fake_blender_archive("4.6.0"))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    async with database.session_factory() as session, session.begin():
        await BlenderRepository(session).add_runtime(
            BlenderRuntime(
                version=version,
                source=RuntimeSource.OFFICIAL,
                state=RuntimeState.DOWNLOADED,
                supported=True,
                active=False,
                archive_path=str(archive_path),
                official_filename="blender-4.1.1-linux-x64.tar.xz",
                expected_sha256="a" * 64,
                verified_sha256="a" * 64,
            )
        )
    try:
        await service.initialize()
        runtime = next(item for item in await service.list_runtimes() if item.version == version)

        assert runtime.source is RuntimeSource.BUNDLED
        assert runtime.state is RuntimeState.INSTALLED
        assert runtime.active is False
        assert runtime.archive_path is None
        assert runtime.official_filename is None
        assert runtime.expected_sha256 is None
        assert runtime.verified_sha256 is None
        assert runtime.operation_id is None
        assert not archive_path.exists()
    finally:
        await service.shutdown()
        await database.dispose()


async def test_initialize_promotes_valid_installed_collision_and_preserves_activation(
    job_settings: Settings,
) -> None:
    version = "5.2.0"
    runtime_root = job_settings.blender_versions_root / version
    fake_blender_executable(runtime_root / "blender", f"Blender {version}")
    archive_path = job_settings.blender_downloads_root / "installed-duplicate.archive"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(b"verified duplicate")
    catalog = FakeCatalog("4.6.0", fake_blender_archive("4.6.0"))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    async with database.session_factory() as session, session.begin():
        await BlenderRepository(session).add_runtime(
            BlenderRuntime(
                version=version,
                source=RuntimeSource.MANUAL,
                state=RuntimeState.INSTALLED,
                supported=True,
                active=True,
                archive_path=str(archive_path),
                official_filename=f"blender-{version}-linux-x64.tar.xz",
                expected_sha256="a" * 64,
                verified_sha256="a" * 64,
                operation_id=UUID("55555555-5555-4555-8555-555555555555"),
            )
        )
    try:
        await service.initialize()
        runtime = next(item for item in await service.list_runtimes() if item.version == version)

        assert runtime.source is RuntimeSource.BUNDLED
        assert runtime.state is RuntimeState.INSTALLED
        assert runtime.active is True
        assert runtime.archive_path is None
        assert runtime.official_filename is None
        assert runtime.expected_sha256 is None
        assert runtime.verified_sha256 is None
        assert runtime.operation_id is None
        assert not archive_path.exists()
        assert not runtime_root.exists()
    finally:
        await service.shutdown()
        await database.dispose()


async def test_initialize_removes_bundled_runtime_when_binary_is_missing(
    job_settings: Settings,
) -> None:
    missing_root = job_settings.workspace / "missing-bundled"
    settings = job_settings.model_copy(update={"bundled_blender_root": missing_root})
    catalog = FakeCatalog("4.6.0", fake_blender_archive("4.6.0"))
    service, database = make_service(settings, catalog)
    await service._storage.prepare()
    async with database.session_factory() as session, session.begin():
        await BlenderRepository(session).add_runtime(
            BlenderRuntime(
                version="4.1.1",
                source=RuntimeSource.BUNDLED,
                state=RuntimeState.INSTALLED,
                supported=True,
                active=True,
            )
        )
    try:
        await service.initialize()

        assert await service.list_runtimes() == []
    finally:
        await service.shutdown()
        await database.dispose()


async def test_initialize_ignores_corrupted_bundled_runtime(
    job_settings: Settings,
) -> None:
    broken_binary = job_settings.bundled_blender_root / "4.1.1" / "blender"
    broken_binary.write_bytes(b"not an executable format")
    broken_binary.chmod(0o755)
    catalog = FakeCatalog("4.6.0", fake_blender_archive("4.6.0"))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    async with database.session_factory() as session, session.begin():
        await BlenderRepository(session).add_runtime(
            BlenderRuntime(
                version="4.1.1",
                source=RuntimeSource.BUNDLED,
                state=RuntimeState.INSTALLED,
                supported=True,
                active=True,
            )
        )
    try:
        await service.initialize()

        runtimes = await service.list_runtimes()
        assert [runtime.version for runtime in runtimes] == ["5.2.0"]
        assert runtimes[0].active is False
    finally:
        await service.shutdown()
        await database.dispose()


async def test_only_one_mutating_operation(job_settings: Settings) -> None:
    version = "4.6.4"
    catalog = FakeCatalog(version, fake_blender_archive(version))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    await service.initialize()
    try:
        first = await service.start_download(version)
        with pytest.raises(BlenderConflictError):
            await service.start_download("4.6.4")
        await service.wait(first.id)
    finally:
        await service.shutdown()
        await database.dispose()


async def test_official_operation_api_contract(job_app: FastAPI, job_settings: Settings) -> None:
    version = "4.6.5"
    catalog = FakeCatalog(version, fake_blender_archive(version))
    async with job_app.router.lifespan_context(job_app):
        storage = BlenderStorage(
            job_settings.blender_versions_root,
            job_settings.blender_downloads_root,
            job_settings.blender_quarantine_root,
            max_archive_bytes=job_settings.max_blender_archive_bytes,
            max_extracted_bytes=job_settings.max_blender_extracted_bytes,
            max_files=job_settings.max_blender_archive_files,
        )
        service = BlenderService(
            job_app.state.database,
            storage,
            catalog,  # type: ignore[arg-type]
            job_app.state.event_hub,
            bundled_root=job_settings.bundled_blender_root,
        )
        await storage.prepare()
        await service.initialize()
        job_app.state.blender_service = service
        transport = ASGITransport(app=job_app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                accepted = await client.post(f"/api/v1/blender/releases/{version}/download")
                assert accepted.status_code == 202
                operation_id = accepted.json()["operation_id"]
                await service.wait(UUID(operation_id))
                operation = await client.get(f"/api/v1/blender/operations/{operation_id}")
                assert operation.json()["state"] == "completed"

                install = await client.post(f"/api/v1/blender/versions/{version}/install")
                assert install.status_code == 202
                await service.wait(UUID(install.json()["operation_id"]))
                activated = await client.post(f"/api/v1/blender/versions/{version}/activate")
                assert activated.status_code == 200
                assert activated.json()["active"] is True

                restored_default = await client.post("/api/v1/blender/versions/5.2.0/activate")
                assert restored_default.status_code == 200
                deleted = await client.delete(f"/api/v1/blender/versions/{version}")
                assert deleted.status_code == 204
                assert not (job_settings.blender_versions_root / version).exists()
                assert list(job_settings.blender_downloads_root.iterdir()) == []
                versions = await client.get("/api/v1/blender/versions")
                assert version not in {item["version"] for item in versions.json()}
        finally:
            await service.shutdown()


async def test_empty_registry_stays_ready_until_explicit_install_and_activation(
    job_settings: Settings,
) -> None:
    version = "4.5.11"
    settings = job_settings.model_copy(
        update={"bundled_blender_root": job_settings.workspace / "missing-bundled"}
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        storage = BlenderStorage(
            settings.blender_versions_root,
            settings.blender_downloads_root,
            settings.blender_quarantine_root,
            max_archive_bytes=settings.max_blender_archive_bytes,
            max_extracted_bytes=settings.max_blender_extracted_bytes,
            max_files=settings.max_blender_archive_files,
        )
        catalog = FakeCatalog(version, fake_blender_archive(version))
        service = BlenderService(
            app.state.database,
            storage,
            catalog,  # type: ignore[arg-type]
            app.state.event_hub,
            bundled_root=settings.bundled_blender_root,
        )
        await storage.prepare()
        await service.initialize()
        app.state.blender_service = service
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.get("/ready")).status_code == 200
                assert (await client.get("/api/v1/blender/versions")).json() == []

                payload = {
                    "name": "Needs runtime",
                    "blender_version": version,
                    "engine": "BLENDER_EEVEE",
                    "device": "CPU",
                    "gpu_ids": [],
                    "frame_mode": "SINGLE",
                    "frame_start": 1,
                    "frame_end": None,
                }
                blocked = await client.post("/api/v1/jobs", json=payload)
                assert blocked.status_code == 409
                assert blocked.json()["error"]["code"] == "active_blender_missing"

                download = await client.post(f"/api/v1/blender/releases/{version}/download")
                await service.wait(UUID(download.json()["operation_id"]))
                install = await client.post(f"/api/v1/blender/versions/{version}/install")
                await service.wait(UUID(install.json()["operation_id"]))
                installed = await client.get("/api/v1/blender/versions")
                assert installed.json()[0]["active"] is False

                activated = await client.post(f"/api/v1/blender/versions/{version}/activate")
                assert activated.status_code == 200
                created = await client.post("/api/v1/jobs", json=payload)
                assert created.status_code == 201
                assert created.json()["blender_version"] == version
        finally:
            await service.shutdown()


async def test_restart_fails_operations_and_cleans_abandoned_files(job_settings: Settings) -> None:
    version = "4.6.6"
    catalog = FakeCatalog(version, fake_blender_archive(version))
    service, database = make_service(job_settings, catalog)
    await service._storage.prepare()
    await service.initialize()
    abandoned_quarantine = job_settings.blender_quarantine_root / "abandoned.archive"
    abandoned_download = job_settings.blender_downloads_root / "abandoned.archive"
    await asyncio.to_thread(abandoned_quarantine.write_bytes, b"partial")
    await asyncio.to_thread(abandoned_download.write_bytes, b"partial")
    try:
        async with database.session_factory() as session, session.begin():
            repository = BlenderRepository(session)
            await repository.add_operation(
                BlenderOperation(
                    kind=OperationKind.DOWNLOAD,
                    version=version,
                    state=OperationState.RUNNING,
                )
            )
            await repository.add_runtime(
                BlenderRuntime(
                    version=version,
                    source=RuntimeSource.OFFICIAL,
                    state=RuntimeState.DOWNLOADING,
                    supported=False,
                    active=False,
                )
            )

        await service.initialize()
        operations = []
        async with database.session_factory() as session:
            operations = await BlenderRepository(session).unfinished_operations()
        runtime = next(item for item in await service.list_runtimes() if item.version == version)
        assert operations == []
        assert runtime.state is RuntimeState.FAILED
        assert not abandoned_quarantine.exists()
        assert not abandoned_download.exists()
    finally:
        await service.shutdown()
        await database.dispose()
