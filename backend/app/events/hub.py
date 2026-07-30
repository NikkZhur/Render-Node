"""Bounded non-persistent event fan-out with explicit REST resynchronization."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

Event = dict[str, Any]


@dataclass(slots=True)
class EventSubscription:
    id: UUID
    queue: asyncio.Queue[Event]
    job_ids: set[UUID] | None = None


class EventHub:
    def __init__(self, *, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscriptions: dict[UUID, EventSubscription] = {}
        self._lock = asyncio.Lock()
        self._sequence = 0

    @property
    def client_count(self) -> int:
        return len(self._subscriptions)

    async def connect(self) -> EventSubscription:
        subscription = EventSubscription(id=uuid4(), queue=asyncio.Queue(maxsize=self._queue_size))
        async with self._lock:
            self._subscriptions[subscription.id] = subscription
        return subscription

    async def disconnect(self, subscription: EventSubscription) -> None:
        async with self._lock:
            self._subscriptions.pop(subscription.id, None)

    async def subscribe(self, subscription: EventSubscription, job_ids: set[UUID] | None) -> None:
        async with self._lock:
            current = self._subscriptions.get(subscription.id)
            if current is not None:
                current.job_ids = job_ids

    async def publish(self, event_type: str, **payload: Any) -> Event:
        async with self._lock:
            self._sequence += 1
            event: Event = {
                "type": event_type,
                "sequence": self._sequence,
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                **payload,
            }
            subscriptions = list(self._subscriptions.values())

        job_id = self._event_job_id(event)
        for subscription in subscriptions:
            if not self._matches(subscription, event_type, job_id):
                continue
            if subscription.queue.full():
                self._replace_with_resync(subscription)
                continue
            subscription.queue.put_nowait(event)
        return event

    @staticmethod
    def _event_job_id(event: Event) -> UUID | None:
        raw_job_id = event.get("job_id")
        if not isinstance(raw_job_id, str):
            return None
        try:
            return UUID(raw_job_id)
        except ValueError:
            return None

    @staticmethod
    def _matches(subscription: EventSubscription, event_type: str, job_id: UUID | None) -> bool:
        if job_id is None or event_type == "job.created":
            return True
        return subscription.job_ids is None or job_id in subscription.job_ids

    def _replace_with_resync(self, subscription: EventSubscription) -> None:
        while not subscription.queue.empty():
            subscription.queue.get_nowait()
        subscription.queue.put_nowait(
            {
                "type": "resync.required",
                "sequence": self._sequence,
                "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "reason": "client_event_queue_overflow",
            }
        )
