# ruff: noqa: N802

from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.blender.devices import GpuDiscovery, GpuReservationError, GpuScheduler


class FakeNvml:
    def __init__(self, *, fail: bool = False, count: int = 1) -> None:
        self.fail = fail
        self.count = count
        self.order = list(range(count))
        self.shutdown_called = False

    def nvmlInit(self) -> None:
        if self.fail:
            raise RuntimeError("no driver")

    def nvmlShutdown(self) -> None:
        self.shutdown_called = True

    def nvmlDeviceGetCount(self) -> int:
        return self.count

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        return self.order[index]

    def nvmlDeviceGetName(self, handle: int) -> bytes:
        return f"NVIDIA Test GPU {handle}".encode()

    def nvmlDeviceGetUUID(self, handle: int) -> str:
        return f"GPU-test-{handle}"

    def nvmlDeviceGetMemoryInfo(self, handle: int) -> SimpleNamespace:
        return SimpleNamespace(total=24 * 1024**3)


def test_gpu_discovery_returns_stable_inventory_and_shuts_down() -> None:
    nvml = FakeNvml()
    devices = GpuDiscovery(nvml).discover()

    assert devices[0].id == 0
    assert devices[0].uuid == "GPU-test-0"
    assert devices[0].name == "NVIDIA Test GPU 0"
    assert devices[0].memory_total_bytes == 24 * 1024**3
    assert nvml.shutdown_called is True


def test_gpu_discovery_without_driver_is_empty() -> None:
    assert GpuDiscovery(FakeNvml(fail=True)).discover() == []


async def test_gpu_scheduler_reserves_and_releases_single_and_multiple_uuids() -> None:
    scheduler = GpuScheduler(GpuDiscovery(FakeNvml(count=3)))
    await scheduler.initialize()

    multiple = await scheduler.reserve([0, 2])
    assert multiple.uuids == ("GPU-test-0", "GPU-test-2")
    assert scheduler.reserved_uuids == {"GPU-test-0", "GPU-test-2"}
    with pytest.raises(GpuReservationError, match="already reserved"):
        await scheduler.reserve([2])

    await scheduler.release(multiple)
    single = await scheduler.reserve([1])
    assert single.uuids == ("GPU-test-1",)
    await scheduler.release(single)
    assert scheduler.reserved_uuids == set()


async def test_gpu_scheduler_keeps_startup_uuid_when_nvml_indices_reorder() -> None:
    nvml = FakeNvml(count=2)
    scheduler = GpuScheduler(GpuDiscovery(nvml))
    await scheduler.initialize()
    nvml.order = [1, 0]

    allocation = await scheduler.reserve([0])

    assert allocation.uuids == ("GPU-test-0",)
    assert allocation.devices[0].id == 1
    await scheduler.release(allocation)


async def test_device_api_has_a_stable_no_driver_contract(job_client: AsyncClient) -> None:
    response = await job_client.get("/api/v1/devices")

    assert response.status_code == 200
    for device in response.json():
        assert set(device) == {
            "id",
            "uuid",
            "name",
            "memory_total_bytes",
            "available",
        }
