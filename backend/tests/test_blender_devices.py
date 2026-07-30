# ruff: noqa: N802

from types import SimpleNamespace

from httpx import AsyncClient

from app.blender.devices import GpuDiscovery


class FakeNvml:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.shutdown_called = False

    def nvmlInit(self) -> None:
        if self.fail:
            raise RuntimeError("no driver")

    def nvmlShutdown(self) -> None:
        self.shutdown_called = True

    def nvmlDeviceGetCount(self) -> int:
        return 1

    def nvmlDeviceGetHandleByIndex(self, index: int) -> int:
        return index

    def nvmlDeviceGetName(self, handle: int) -> bytes:
        return b"NVIDIA Test GPU"

    def nvmlDeviceGetUUID(self, handle: int) -> str:
        return "GPU-test"

    def nvmlDeviceGetMemoryInfo(self, handle: int) -> SimpleNamespace:
        return SimpleNamespace(total=24 * 1024**3)


def test_gpu_discovery_returns_stable_inventory_and_shuts_down() -> None:
    nvml = FakeNvml()
    devices = GpuDiscovery(nvml).discover()

    assert devices[0].id == 0
    assert devices[0].uuid == "GPU-test"
    assert devices[0].name == "NVIDIA Test GPU"
    assert devices[0].memory_total_bytes == 24 * 1024**3
    assert nvml.shutdown_called is True


def test_gpu_discovery_without_driver_is_empty() -> None:
    assert GpuDiscovery(FakeNvml(fail=True)).discover() == []


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
