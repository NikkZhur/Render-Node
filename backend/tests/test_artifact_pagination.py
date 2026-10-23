from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import event

from app.artifacts.models import Artifact
from app.artifacts.service import ArtifactService
from app.artifacts.types import ArtifactKind
from app.config import Settings
from app.storage.database import Database


async def create_job(client: AsyncClient) -> UUID:
    response = await client.post(
        "/api/v1/jobs",
        json={
            "name": "Gallery pagination",
            "blender_version": "5.2.0",
            "engine": "CYCLES",
            "device": "CPU",
            "gpu_ids": [],
            "frame_mode": "ALL",
        },
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def artifact(job_id: UUID, kind: ArtifactKind, frame: int | None) -> Artifact:
    return Artifact(
        id=uuid4(),
        job_id=job_id,
        kind=kind,
        frame=frame,
        filename=f"frame_{frame}.png",
        relative_path=f"output/frame_{frame}.png",
        content_type="image/png",
        size_bytes=120,
    )


@pytest.fixture
async def gallery(
    job_client: AsyncClient, job_app: FastAPI
) -> tuple[UUID, dict[int, UUID], dict[int, UUID]]:
    job_id = await create_job(job_client)
    other_job_id = await create_job(job_client)
    # Insert out of frame order and leave gaps, as live GPU workers do.
    originals = {
        frame: artifact(job_id, ArtifactKind.FRAME_ORIGINAL, frame)
        for frame in reversed(range(1, 246, 2))
    }
    previews = {
        frame: artifact(job_id, ArtifactKind.FRAME_PREVIEW, frame)
        for frame in originals
        if frame % 4 == 1
    }
    database = cast(Database, job_app.state.database)
    async with database.session_factory() as session, session.begin():
        session.add_all(
            [
                *originals.values(),
                *previews.values(),
                artifact(job_id, ArtifactKind.FRAME_PREVIEW, 300),
                artifact(job_id, ArtifactKind.BLENDER_LOG, None),
                artifact(job_id, ArtifactKind.FRAME_ORIGINAL, None),
                artifact(other_job_id, ArtifactKind.FRAME_ORIGINAL, 3),
                artifact(other_job_id, ArtifactKind.FRAME_PREVIEW, 3),
            ]
        )
    return (
        job_id,
        {frame: item.id for frame, item in originals.items()},
        {frame: item.id for frame, item in previews.items()},
    )


async def test_frame_pages_have_stable_order_totals_and_optional_previews(
    job_client: AsyncClient, gallery: tuple[UUID, dict[int, UUID], dict[int, UUID]]
) -> None:
    job_id, originals, previews = gallery
    ordered_frames = sorted(originals)
    received_frames = []
    for page in range(1, 5):
        response = await job_client.get(f"/api/v1/jobs/{job_id}/frames?page={page}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["page"] == page
        assert payload["page_size"] == 50
        assert payload["total"] == 123
        assert payload["pages"] == 3
        assert [item["frame"] for item in payload["items"]] == ordered_frames[
            (page - 1) * 50 : page * 50
        ]
        for item in payload["items"]:
            frame = item["frame"]
            received_frames.append(frame)
            assert item["filename"] == f"frame_{frame}.png"
            assert item["size_bytes"] == 120
            assert item["original_artifact_id"] == str(originals[frame])
            assert item["original_url"] == f"/api/v1/jobs/{job_id}/frames/{frame}/original"
            if frame in previews:
                assert item["preview_artifact_id"] == str(previews[frame])
                assert item["preview_url"] == f"/api/v1/jobs/{job_id}/frames/{frame}/preview"
            else:
                assert item["preview_artifact_id"] is None
                assert item["preview_url"] is None
    assert received_frames == ordered_frames


@contextmanager
def artifact_reads(database: Database) -> Iterator[tuple[list[str], list[Artifact]]]:
    statements: list[str] = []
    loaded: list[Artifact] = []

    def capture_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("SELECT") and "FROM artifacts" in statement:
            statements.append(statement)

    def capture_load(instance: Artifact, _context: object) -> None:
        loaded.append(instance)

    event.listen(database.engine.sync_engine, "before_cursor_execute", capture_query)
    event.listen(Artifact, "load", capture_load)
    try:
        yield statements, loaded
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", capture_query)
        event.remove(Artifact, "load", capture_load)


@pytest.mark.parametrize("page_size", [1, 50])
async def test_frame_page_queries_and_loaded_records_are_bounded(
    job_client: AsyncClient,
    job_app: FastAPI,
    gallery: tuple[UUID, dict[int, UUID], dict[int, UUID]],
    page_size: int,
) -> None:
    job_id, _, _ = gallery
    with artifact_reads(cast(Database, job_app.state.database)) as (statements, loaded):
        response = await job_client.get(
            f"/api/v1/jobs/{job_id}/frames?page=2&page_size={page_size}"
        )
    assert response.status_code == 200
    assert len(response.json()["items"]) == page_size
    # A count, bounded originals, and one batch of previews; no per-frame queries.
    assert len(statements) <= 3
    assert any("LIMIT" in statement and "OFFSET" in statement for statement in statements)
    assert len([item for item in loaded if item.kind == ArtifactKind.FRAME_ORIGINAL]) == page_size
    assert len(loaded) <= page_size * 2
    page_frames = {item["frame"] for item in response.json()["items"]}
    assert all(item.job_id == job_id and item.frame in page_frames for item in loaded)


async def test_log_artifact_filter_does_not_load_frame_records(
    job_client: AsyncClient,
    job_app: FastAPI,
    gallery: tuple[UUID, dict[int, UUID], dict[int, UUID]],
) -> None:
    job_id, originals, previews = gallery
    with artifact_reads(cast(Database, job_app.state.database)) as (statements, loaded):
        response = await job_client.get(f"/api/v1/jobs/{job_id}/artifacts?kind=blender_log")
    assert response.status_code == 200
    assert [item["kind"] for item in response.json()] == ["blender_log"]
    assert len(statements) == 1
    assert len(loaded) == 1
    assert loaded[0].kind == ArtifactKind.BLENDER_LOG
    assert loaded[0].job_id == job_id

    # Existing callers may still explicitly retrieve the complete inventory.
    inventory = await job_client.get(f"/api/v1/jobs/{job_id}/artifacts")
    assert inventory.status_code == 200
    assert len(inventory.json()) == len(originals) + len(previews) + 3
    invalid = await job_client.get(f"/api/v1/jobs/{job_id}/artifacts?kind=invalid")
    assert invalid.status_code == 422


async def test_empty_and_missing_job_frame_pages(job_client: AsyncClient) -> None:
    job_id = await create_job(job_client)
    response = await job_client.get(f"/api/v1/jobs/{job_id}/frames?page=2&page_size=10")
    assert response.status_code == 200
    assert response.json() == {"items": [], "page": 2, "page_size": 10, "total": 0, "pages": 0}
    missing = await job_client.get(f"/api/v1/jobs/{uuid4()}/frames")
    assert missing.status_code == 404


async def test_signed_scene_frames_remain_distinct_and_downloadable(
    job_client: AsyncClient, job_app: FastAPI, job_settings: Settings
) -> None:
    job_id = await create_job(job_client)
    service = cast(ArtifactService, job_app.state.artifact_service)
    output = job_settings.jobs_root / str(job_id) / "output"
    content_by_frame = {}
    for frame in (-2, 0, 2):
        image = Image.new("RGB", (1, 1), color=(frame + 2, 0, 0))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        content_by_frame[frame] = buffer.getvalue()
        path = output / f"frame_{frame:05d}.png"
        await asyncio.to_thread(path.write_bytes, content_by_frame[frame])
        await service.register_output(job_id, path)

    assert await service.reconcile_outputs(job_id) == 0
    response = await job_client.get(f"/api/v1/jobs/{job_id}/frames")
    assert response.status_code == 200
    assert response.json()["total"] == 3
    items = response.json()["items"]
    assert [item["frame"] for item in items] == [-2, 0, 2]
    assert len({item["original_artifact_id"] for item in items}) == 3
    assert len({item["preview_artifact_id"] for item in items}) == 3
    for item in items:
        original = await job_client.get(item["original_url"])
        assert original.status_code == 200
        assert original.content == content_by_frame[item["frame"]]
        preview = await job_client.get(item["preview_url"])
        assert preview.status_code == 200
