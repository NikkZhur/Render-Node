from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient

from app.blender.exceptions import BlenderConflictError, BlenderRejectedError
from app.blender.models import BlenderOperation, BlenderRuntime
from app.blender.official import OfficialRelease
from app.blender.repository import BlenderRepository
from app.blender.service import BlenderService
from app.blender.storage import BlenderStorage
from app.blender.types import OperationKind, OperationState, RuntimeSource, RuntimeState
from app.config import Settings
from app.events.hub import EventHub
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
    return BlenderService(database, storage, catalog, EventHub()), database  # type: ignore[arg-type]


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
        assert [runtime.version for runtime in runtimes if runtime.active] == ["5.2.0"]
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
