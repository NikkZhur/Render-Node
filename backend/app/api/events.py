"""WebSocket transport for the in-process event hub."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.events.hub import EventHub, EventSubscription

router = APIRouter(tags=["events"])
MAX_SUBSCRIBED_JOBS = 1000


async def _send_events(websocket: WebSocket, subscription: EventSubscription) -> None:
    while True:
        await websocket.send_json(await subscription.queue.get())


def _parse_subscription(message: dict[str, Any]) -> set[UUID] | None:
    if message.get("action") != "subscribe":
        raise ValueError("Unsupported WebSocket action")
    raw_job_ids = message.get("job_ids")
    if raw_job_ids is None:
        return None
    if not isinstance(raw_job_ids, list) or len(raw_job_ids) > MAX_SUBSCRIBED_JOBS:
        raise ValueError("job_ids must be a bounded list or null")
    if not all(isinstance(item, str) for item in raw_job_ids):
        raise ValueError("job_ids must contain only UUID strings")
    try:
        return {UUID(item) for item in raw_job_ids}
    except ValueError as exc:
        raise ValueError("job_ids contains an invalid UUID") from exc


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    hub = cast(EventHub, websocket.app.state.event_hub)
    await websocket.accept()
    subscription = await hub.connect()
    await websocket.send_json(
        {
            "type": "connection.ready",
            "client_id": str(subscription.id),
            "resync": f"{websocket.app.state.settings.api_prefix}/jobs",
        }
    )
    sender = asyncio.create_task(_send_events(websocket, subscription))
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.send_json(
                    {"type": "connection.error", "message": "Expected a JSON object"}
                )
                continue
            try:
                job_ids = _parse_subscription(message)
            except ValueError as exc:
                await websocket.send_json({"type": "connection.error", "message": str(exc)})
                continue
            await hub.subscribe(subscription, job_ids)
            await websocket.send_json(
                {
                    "type": "subscription.updated",
                    "job_ids": None if job_ids is None else [str(item) for item in job_ids],
                }
            )
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        await hub.disconnect(subscription)
