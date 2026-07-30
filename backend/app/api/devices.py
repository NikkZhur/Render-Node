"""Minimal render-device inventory used by Job setup."""

import asyncio
from typing import cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.blender.devices import GpuDiscovery

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceResponse(BaseModel):
    id: int = Field(ge=0)
    uuid: str
    name: str
    memory_total_bytes: int = Field(ge=0)
    available: bool = True


@router.get("", response_model=list[DeviceResponse])
async def list_devices(request: Request) -> list[DeviceResponse]:
    discovery = cast(GpuDiscovery, request.app.state.gpu_discovery)
    devices = await asyncio.to_thread(discovery.discover)
    return [
        DeviceResponse(
            id=device.id,
            uuid=device.uuid,
            name=device.name,
            memory_total_bytes=device.memory_total_bytes,
        )
        for device in devices
    ]
