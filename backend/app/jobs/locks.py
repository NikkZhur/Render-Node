"""Per-job serialization for filesystem/database operations on one node."""

import asyncio
from uuid import UUID


class JobLocks:
    def __init__(self) -> None:
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get(self, job_id: UUID) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(job_id, asyncio.Lock())
