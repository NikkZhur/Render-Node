from __future__ import annotations

import gzip
import stat
from collections.abc import AsyncIterator
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
import zstandard
from httpx import AsyncClient

from app.config import Settings
from tests.test_job_api import job_payload


def zip_bytes(entries: list[tuple[str | ZipInfo, bytes]]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


async def create_job(client: AsyncClient) -> str:
    response = await client.post("/api/v1/jobs", json=job_payload())
    assert response.status_code == 201
    return str(response.json()["id"])


async def test_safe_zip_preserves_contained_assets(
    job_client: AsyncClient, job_settings: Settings
) -> None:
    job_id = await create_job(job_client)
    archive = zip_bytes(
        [
            ("project/scene.blend", b"BLENDER-v300"),
            ("project/textures/albedo.txt", b"texture"),
        ]
    )

    response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("project.zip", archive, "application/zip")},
    )

    assert response.status_code == 200
    input_root = job_settings.jobs_root / job_id / "input"
    assert (input_root / "project" / "scene.blend").read_bytes() == b"BLENDER-v300"
    assert (input_root / "project" / "textures" / "albedo.txt").read_bytes() == b"texture"


@pytest.mark.parametrize(
    "content",
    [
        gzip.compress(b"BLENDER-v280"),
        zstandard.ZstdCompressor().compress(b"BLENDER-v300"),
    ],
)
async def test_compressed_blend_upload_is_accepted(job_client: AsyncClient, content: bytes) -> None:
    job_id = await create_job(job_client)

    response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("compressed.blend", content, "application/octet-stream")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize(
    "content",
    [
        gzip.compress(b"not-a-blend"),
        zstandard.ZstdCompressor().compress(b"not-a-blend"),
    ],
)
async def test_compressed_non_blend_is_rejected(job_client: AsyncClient, content: bytes) -> None:
    job_id = await create_job(job_client)

    response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("invalid.blend", content, "application/octet-stream")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_blend"


@pytest.mark.parametrize(
    ("filename", "content", "expected_code"),
    [
        ("scene.txt", b"BLENDER-v300", "unsupported_upload_type"),
        ("scene.blend", b"not-blender", "invalid_blend"),
        ("scene.blend", b"", "empty_upload"),
        ("scene.blend", b"BLENDER" + b"x" * 2000, "upload_too_large"),
    ],
)
async def test_invalid_direct_upload_is_rejected_and_cleaned(
    job_client: AsyncClient,
    job_settings: Settings,
    filename: str,
    content: bytes,
    expected_code: str,
) -> None:
    job_id = await create_job(job_client)

    response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code in {413, 422}
    assert response.json()["error"]["code"] == expected_code
    job_root = job_settings.jobs_root / job_id
    assert not (job_root / "input").exists()
    assert list((job_root / "temp").iterdir()) == []
    assert (await job_client.get(f"/api/v1/jobs/{job_id}")).json()["status"] == "created"


def symlink_entry() -> ZipInfo:
    info = ZipInfo("scene.blend")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize(
    ("entries", "expected_code"),
    [
        ([("../escape.blend", b"BLENDER")], "unsafe_zip_path"),
        ([("/absolute.blend", b"BLENDER")], "unsafe_zip_path"),
        ([("..\\escape.blend", b"BLENDER")], "unsafe_zip_path"),
        ([(symlink_entry(), b"target.blend")], "unsafe_zip_entry"),
        ([("scene.txt", b"asset")], "zip_scene_count"),
        ([("a.blend", b"BLENDER"), ("b.blend", b"BLENDER")], "zip_scene_count"),
        (
            [("scene.blend", b"BLENDER"), ("asset-a", b"a"), ("asset-b", b"b")],
            "too_many_zip_files",
        ),
        (
            [("scene.blend", b"BLENDER"), ("asset", b"x" * 2000)],
            "zip_extracted_too_large",
        ),
        (
            [("Scene.blend", b"BLENDER"), ("scene.blend", b"BLENDER")],
            "duplicate_zip_path",
        ),
    ],
)
async def test_unsafe_zip_is_rejected_without_escape(
    job_client: AsyncClient,
    job_settings: Settings,
    entries: list[tuple[str | ZipInfo, bytes]],
    expected_code: str,
) -> None:
    job_id = await create_job(job_client)

    response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        files={"file": ("scene.zip", zip_bytes(entries), "application/zip")},
    )

    assert response.status_code in {413, 422}
    assert response.json()["error"]["code"] == expected_code
    job_root = job_settings.jobs_root / job_id
    assert not (job_root / "input").exists()
    assert list((job_root / "temp").iterdir()) == []
    assert not (job_settings.workspace / "escape.blend").exists()


async def test_second_upload_is_rejected(job_client: AsyncClient) -> None:
    job_id = await create_job(job_client)
    upload = {"file": ("scene.blend", b"BLENDER-v300", "application/octet-stream")}
    first_response = await job_client.post(f"/api/v1/jobs/{job_id}/uploads", files=upload)
    assert first_response.status_code == 200

    response = await job_client.post(f"/api/v1/jobs/{job_id}/uploads", files=upload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_already_uploaded"


async def test_request_body_limit_rejects_before_multipart_parsing(
    job_client: AsyncClient,
) -> None:
    job_id = await create_job(job_client)

    response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        content=b"x" * (2 * 1024 * 1024),
        headers={"content-type": "multipart/form-data; boundary=missing"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_body_too_large"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_streamed_request_body_is_limited_without_content_length(
    job_client: AsyncClient,
) -> None:
    job_id = await create_job(job_client)

    async def oversized_chunks() -> AsyncIterator[bytes]:
        yield (
            b"--upload-boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="scene.blend"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
        )
        for _ in range(3):
            yield b"x" * (512 * 1024)
        yield b"\r\n--upload-boundary--\r\n"

    response = await job_client.post(
        f"/api/v1/jobs/{job_id}/uploads",
        content=oversized_chunks(),
        headers={"content-type": "multipart/form-data; boundary=upload-boundary"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_body_too_large"
