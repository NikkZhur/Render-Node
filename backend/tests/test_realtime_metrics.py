from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.blender.devices import GpuDiscovery
from app.events.hub import EventHub
from app.system.metrics import SystemMetricsService
from app.system.schemas import StorageStatus


def receive_type(websocket: object, expected: str) -> dict[str, object]:
    for _ in range(10):
        event = websocket.receive_json()  # type: ignore[attr-defined,no-any-return]
        if event.get("type") == expected:
            return event  # type: ignore[no-any-return]
    raise AssertionError(f"WebSocket event {expected!r} was not received")


def test_websocket_subscription_job_events_and_metrics(app: FastAPI) -> None:
    with TestClient(app) as client, client.websocket_connect("/api/v1/events") as websocket:
        ready = receive_type(websocket, "connection.ready")
        assert ready["resync"] == "/api/v1/jobs"
        websocket.send_json({"action": "subscribe", "job_ids": []})
        subscribed = receive_type(websocket, "subscription.updated")
        assert subscribed["job_ids"] == []

        created = client.post(
            "/api/v1/jobs",
            json={
                "name": "Realtime",
                "blender_version": "4.5.11",
                "engine": "CYCLES",
                "device": "CPU",
                "gpu_ids": [],
                "frame_mode": "SINGLE",
                "frame_start": 1,
                "frame_end": None,
            },
        )
        assert created.status_code == 201
        event = receive_type(websocket, "job.created")
        assert event["job_id"] == created.json()["id"]

        metrics = client.get("/api/v1/system/metrics")
        assert metrics.status_code == 200
        body = metrics.json()
        assert body["cpus"]
        assert isinstance(body["gpus"], list)
        assert body["storages"]
        assert body["websocket_clients"] == 1
        assert body["storages"][0]["status"] in {"healthy", "low_space"}


async def test_event_hub_filters_jobs_and_requests_rest_resync_on_overflow() -> None:
    hub = EventHub(queue_size=1)
    subscription = await hub.connect()
    selected_job = uuid4()
    await hub.subscribe(subscription, {selected_job})

    await hub.publish("render.progress", job_id=str(uuid4()), progress=0.1)
    assert subscription.queue.empty()
    await hub.publish("render.progress", job_id=str(selected_job), progress=0.2)
    event = await subscription.queue.get()
    assert event["job_id"] == str(selected_job)

    await hub.publish("render.log", job_id=str(selected_job), line="one")
    await hub.publish("render.log", job_id=str(selected_job), line="two")
    overflow = await subscription.queue.get()
    assert overflow["type"] == "resync.required"
    assert overflow["reason"] == "client_event_queue_overflow"
    assert UUID(str(subscription.id)) == subscription.id
    await hub.disconnect(subscription)


async def test_metrics_marks_storage_low_when_absolute_threshold_is_crossed(
    tmp_path: Path,
) -> None:
    service = SystemMetricsService(
        GpuDiscovery(),
        EventHub(),
        storage_paths=[("Workspace", tmp_path)],
        low_space_percent=1,
        low_space_bytes=10**18,
        interval_seconds=1,
    )

    metrics = await service.collect()

    assert metrics.storages[0].status is StorageStatus.LOW_SPACE
