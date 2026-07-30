import pytest
from pydantic import ValidationError

from app.jobs.schemas import JobCreate
from app.jobs.types import ComputeDevice, FrameMode, RenderEngine


def valid_payload() -> dict[str, object]:
    return {
        "name": "Studio scene",
        "blender_version": "4.5.11",
        "engine": RenderEngine.CYCLES,
        "device": ComputeDevice.OPTIX,
        "gpu_ids": [0, 1],
        "frame_mode": FrameMode.RANGE,
        "frame_start": 1,
        "frame_end": 240,
    }


def test_job_create_normalizes_name() -> None:
    payload = valid_payload()
    payload["name"] = "  Studio scene  "

    assert JobCreate.model_validate(payload).name == "Studio scene"


@pytest.mark.parametrize(
    "changes",
    [
        {"device": "CPU", "gpu_ids": [0]},
        {"device": "OPTIX", "gpu_ids": []},
        {"gpu_ids": [0, 0]},
        {"frame_mode": "RANGE", "frame_start": 10, "frame_end": 1},
        {"frame_mode": "SINGLE", "frame_start": None, "frame_end": None},
        {"frame_mode": "ALL", "frame_start": 1, "frame_end": None},
        {"blender_version": "4.5"},
    ],
)
def test_job_create_rejects_inconsistent_configuration(changes: dict[str, object]) -> None:
    payload = valid_payload()
    payload.update(changes)

    with pytest.raises(ValidationError):
        JobCreate.model_validate(payload)
