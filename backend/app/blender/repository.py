"""Persistence access for Blender runtimes and operations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.blender.models import BlenderOperation, BlenderRuntime
from app.blender.types import OperationState


class BlenderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_runtime(self, version: str) -> BlenderRuntime | None:
        return await self._session.get(BlenderRuntime, version)

    async def list_runtimes(self) -> list[BlenderRuntime]:
        result = await self._session.scalars(
            select(BlenderRuntime).order_by(BlenderRuntime.version.desc())
        )
        return list(result)

    async def active_runtime(self) -> BlenderRuntime | None:
        value: BlenderRuntime | None = await self._session.scalar(
            select(BlenderRuntime).where(BlenderRuntime.active)
        )
        return value

    async def add_runtime(self, runtime: BlenderRuntime) -> BlenderRuntime:
        self._session.add(runtime)
        await self._session.flush()
        return runtime

    async def delete_runtime(self, runtime: BlenderRuntime) -> None:
        await self._session.delete(runtime)

    async def get_operation(self, operation_id: UUID) -> BlenderOperation | None:
        return await self._session.get(BlenderOperation, operation_id)

    async def add_operation(self, operation: BlenderOperation) -> BlenderOperation:
        self._session.add(operation)
        await self._session.flush()
        return operation

    async def has_mutating_operation(self) -> bool:
        statement = select(BlenderOperation.id).where(
            BlenderOperation.state.in_([OperationState.PENDING, OperationState.RUNNING])
        )
        return (await self._session.scalar(statement.limit(1))) is not None

    async def unfinished_operations(self) -> list[BlenderOperation]:
        result = await self._session.scalars(
            select(BlenderOperation).where(
                BlenderOperation.state.in_([OperationState.PENDING, OperationState.RUNNING])
            )
        )
        return list(result)
