# ruff: noqa: N802

from types import SimpleNamespace

import pynvml
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette.testclient import TestClient

from app.blender.devices import GpuDiscovery, GpuReservationError, GpuScheduler


class FakeNvml:
    NVMLError_NotSupported = pynvml.NVMLError_NotSupported
    NVMLError_FunctionNotFound = pynvml.NVMLError_FunctionNotFound
    NVML_DEVICE_MIG_ENABLE = pynvml.NVML_DEVICE_MIG_ENABLE
    NVML_TEMPERATURE_GPU = pynvml.NVML_TEMPERATURE_GPU

    def __init__(self, *, fail: bool = False, count: int = 1) -> None:
        self.fail = fail
        self.count = count
        self.order = list(range(count))
        self.shutdown_called = False
        self.unavailable_handles: set[int] = set()

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
        if handle in self.unavailable_handles:
            raise pynvml.NVMLError_NoPermission
        return SimpleNamespace(total=24 * 1024**3, used=2 * 1024**3)

    def nvmlDeviceGetMigMode(self, handle: int) -> tuple[int, int]:
        return (0, 0)

    def nvmlDeviceGetUtilizationRates(self, handle: int) -> SimpleNamespace:
        return SimpleNamespace(gpu=0, memory=10)

    def nvmlDeviceGetTemperature(self, handle: int, sensor: int) -> int:
        return 42

    def nvmlDeviceGetPowerUsage(self, handle: int) -> int:
        return 40_000


class FakeMigNvml(FakeNvml):
    """A physical GPU and two MIG parents with sparse, restricted child slots."""

    def __init__(self) -> None:
        super().__init__(count=3)
        self.slots: dict[int, list[int | None]] = {1: [10, None, 11], 2: [20]}
        self.unavailable_handles.update(self.slots)
        self.blocked_slots: set[tuple[int, int]] = set()
        self.blocked_modes: set[int] = set()

    def nvmlDeviceGetMigMode(self, handle: int) -> tuple[int, int]:
        if handle in self.blocked_modes:
            raise pynvml.NVMLError_NoPermission
        return (1, 1) if handle in self.slots else (0, 0)

    def nvmlDeviceGetMaxMigDeviceCount(self, handle: int) -> int:
        return 4

    def nvmlDeviceGetMigDeviceHandleByIndex(self, handle: int, index: int) -> int:
        if (handle, index) in self.blocked_slots:
            raise pynvml.NVMLError_NoPermission
        slots = self.slots[handle]
        value = slots[index] if index < len(slots) else None
        if value is None:
            raise pynvml.NVMLError_NotFound
        return value

    def nvmlDeviceGetUUID(self, handle: int) -> str:
        if handle >= 10:
            return f"MIG-test-{handle}"
        return super().nvmlDeviceGetUUID(handle)

    def nvmlDeviceGetName(self, handle: int) -> bytes:
        if handle >= 10:
            return b"NVIDIA Test GPU MIG 1g.24gb"
        return super().nvmlDeviceGetName(handle)

    def nvmlDeviceGetUtilizationRates(self, handle: int) -> SimpleNamespace:
        if handle >= 10:
            raise pynvml.NVMLError_NotSupported
        return super().nvmlDeviceGetUtilizationRates(handle)

    def nvmlDeviceGetTemperature(self, handle: int, sensor: int) -> int:
        if handle >= 10:
            raise pynvml.NVMLError_NotSupported
        return super().nvmlDeviceGetTemperature(handle, sensor)

    def nvmlDeviceGetPowerUsage(self, handle: int) -> int:
        if handle >= 10:
            raise pynvml.NVMLError_NoPermission
        return super().nvmlDeviceGetPowerUsage(handle)


def test_gpu_discovery_returns_stable_inventory_and_shuts_down() -> None:
    nvml = FakeNvml()
    devices = GpuDiscovery(nvml).discover()

    assert devices[0].id == 0
    assert devices[0].uuid == "GPU-test-0"
    assert devices[0].name == "NVIDIA Test GPU 0"
    assert devices[0].memory_total_bytes == 24 * 1024**3
    assert nvml.shutdown_called is True


def test_gpu_discovery_without_driver_is_empty() -> None:
    nvml = FakeNvml(fail=True)
    discovery = GpuDiscovery(nvml)
    assert discovery.discover() == []
    assert discovery.metrics() == []
    assert nvml.shutdown_called is False


@pytest.mark.parametrize(
    "error", [pynvml.NVMLError_NotSupported, pynvml.NVMLError_FunctionNotFound]
)
def test_physical_gpus_without_mig_support_remain_available(
    error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    nvml = FakeNvml()

    def unavailable_mode(handle: int) -> tuple[int, int]:
        raise error

    monkeypatch.setattr(nvml, "nvmlDeviceGetMigMode", unavailable_mode)
    discovery = GpuDiscovery(nvml)

    assert discovery.discover()[0].uuid == "GPU-test-0"
    metric = discovery.metrics()[0]
    assert metric.utilization_percent == 0
    assert metric.temperature_celsius == 42
    assert metric.power_watts == 40


def test_mixed_inventory_uses_mig_children_and_preserves_unknown_metrics() -> None:
    nvml = FakeMigNvml()
    discovery = GpuDiscovery(nvml)

    devices = discovery.discover()
    metrics = discovery.metrics()

    assert [device.uuid for device in devices] == [
        "GPU-test-0",
        "MIG-test-10",
        "MIG-test-11",
        "MIG-test-20",
    ]
    assert [(item.id, item.uuid) for item in metrics] == [(item.id, item.uuid) for item in devices]
    for metric in metrics[1:]:
        assert "MIG 1g.24gb" in metric.name
        assert metric.memory_total_bytes == 24 * 1024**3
        assert metric.memory_used_bytes == 2 * 1024**3
        assert metric.utilization_percent is None
        assert metric.temperature_celsius is None
        assert metric.power_watts is None
    assert metrics[0].utilization_percent == 0
    assert nvml.shutdown_called is True


def test_one_inaccessible_device_does_not_hide_or_renumber_healthy_devices() -> None:
    nvml = FakeMigNvml()
    discovery = GpuDiscovery(nvml)
    discovery.discover()
    nvml.unavailable_handles.update({0, 10})

    assert [(item.id, item.uuid) for item in discovery.discover()] == [
        (2, "MIG-test-11"),
        (3, "MIG-test-20"),
    ]
    assert [(item.id, item.uuid) for item in discovery.metrics()] == [
        (2, "MIG-test-11"),
        (3, "MIG-test-20"),
    ]
    nvml.unavailable_handles.difference_update({0, 10})
    assert [item.id for item in discovery.discover()] == [0, 1, 2, 3]


def test_empty_or_inaccessible_mig_slots_never_fall_back_to_parent_gpu() -> None:
    nvml = FakeMigNvml()
    nvml.slots[1] = []
    nvml.blocked_slots.add((2, 0))
    discovery = GpuDiscovery(nvml)

    assert [item.uuid for item in discovery.discover()] == ["GPU-test-0"]
    assert [item.uuid for item in discovery.metrics()] == ["GPU-test-0"]


def test_unknown_mig_mode_does_not_expose_the_parent_as_a_render_device() -> None:
    nvml = FakeMigNvml()
    nvml.blocked_modes.add(1)

    assert [item.uuid for item in GpuDiscovery(nvml).discover()] == ["GPU-test-0", "MIG-test-20"]


def test_inventory_and_metrics_keep_logical_ids_after_nvml_reordering() -> None:
    nvml = FakeMigNvml()
    discovery = GpuDiscovery(nvml)
    original = [(item.id, item.uuid) for item in discovery.discover()]
    nvml.order = [2, 0, 1]
    nvml.slots[1].reverse()

    assert [(item.id, item.uuid) for item in discovery.discover()] == original
    assert [(item.id, item.uuid) for item in discovery.metrics()] == original


async def test_mig_reservations_use_the_same_uuid_lifecycle_as_physical_gpus() -> None:
    nvml = FakeMigNvml()
    scheduler = GpuScheduler(GpuDiscovery(nvml))
    await scheduler.initialize()

    allocation = await scheduler.reserve([1, 3])
    assert allocation.uuids == ("MIG-test-10", "MIG-test-20")
    with pytest.raises(GpuReservationError, match="already reserved"):
        await scheduler.reserve([3])
    await scheduler.release(allocation)
    assert scheduler.reserved_uuids == set()

    # Recreating a MIG instance produces a new UUID, even in the same slot.
    nvml.slots[1][0] = 12
    with pytest.raises(GpuReservationError, match="unavailable"):
        await scheduler.reserve([1])
    assert scheduler.reserved_uuids == set()
    remaining = await scheduler.reserve([0, 3])
    assert remaining.uuids == ("GPU-test-0", "MIG-test-20")
    await scheduler.release(remaining)


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
    assert allocation.devices[0].id == 0
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


def test_mig_device_api_and_realtime_metrics_share_identity_and_nullability(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery = GpuDiscovery(FakeMigNvml())
    monkeypatch.setattr("app.lifespan.GpuDiscovery", lambda: discovery)
    browser_headers = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}

    with TestClient(app) as client:
        claim = client.post(
            "/api/v1/access/claim",
            headers={**browser_headers, "x-render-node-request": "1"},
        )
        assert claim.status_code == 200
        devices = client.get("/api/v1/devices", headers=browser_headers).json()
        response = client.get("/api/v1/system/metrics", headers=browser_headers)
        assert response.status_code == 200
        metrics = response.json()["gpus"]
        assert [(item["id"], item["uuid"]) for item in metrics] == [
            (item["id"], item["uuid"]) for item in devices
        ]
        assert len(metrics) == 4
        assert metrics[1]["utilization_percent"] is None
        assert metrics[1]["memory_total_bytes"] == 24 * 1024**3
        with client.websocket_connect("/api/v1/events", headers=browser_headers) as websocket:
            for _ in range(10):
                event = websocket.receive_json()
                if event["type"] == "system.metrics_updated":
                    assert event["metrics"]["gpus"] == metrics
                    break
            else:
                pytest.fail("No realtime GPU metrics received")
