import pytest

from app.blender.progress import FramePlan, ProgressUpdate
from app.jobs.progress import _DistributedProgress, _SingleProcessProgress


def test_single_process_progress_uses_frame_ordinals_and_scene_step() -> None:
    tracker = _SingleProcessProgress()
    tracker.set_plan(FramePlan(total=3, worker=0, assigned=3, start=2, end=9, step=3))

    assert tracker.record(ProgressUpdate(2, 512, 512, 1.0)) == pytest.approx(1 / 3)
    assert tracker.record(ProgressUpdate(5, None, None, None)) == pytest.approx(1 / 3)
    # Sample-only lines retain the current frame instead of falling back to start.
    assert tracker.record(ProgressUpdate(None, 256, 512, 0.5)) == pytest.approx(1 / 2)
    assert tracker.record(ProgressUpdate(8, 256, 512, 0.5)) == pytest.approx(5 / 6)
    assert tracker.complete_frame(8) == 1.0


def test_animation_progress_is_unknown_without_the_scene_plan() -> None:
    tracker = _SingleProcessProgress()

    assert tracker.record(ProgressUpdate(1, 512, 512, 1.0)) is None
    assert tracker.complete_frame(1) is None


def test_single_process_progress_handles_outputs_without_samples_and_duplicates() -> None:
    tracker = _SingleProcessProgress()
    tracker.set_plan(FramePlan(total=3, worker=0, assigned=3, start=-2, end=3, step=2))

    assert tracker.complete_frame(-2) == pytest.approx(1 / 3)
    assert tracker.complete_frame(-2) == pytest.approx(1 / 3)
    assert tracker.complete_frame(0) == pytest.approx(2 / 3)
    assert tracker.complete_frame(1) == pytest.approx(2 / 3)  # Not in this frame sequence.
    assert tracker.complete_frame(2) == 1.0
    assert tracker.record(ProgressUpdate(0, 256, 512, 0.5)) == 1.0


def test_single_frame_progress_is_the_frame_fraction() -> None:
    tracker = _SingleProcessProgress()
    tracker.set_plan(FramePlan(total=1, worker=0, assigned=1, start=7, end=7, step=1))

    assert tracker.record(ProgressUpdate(None, 400, 512, 400 / 512)) == pytest.approx(400 / 512)
    assert tracker.complete_frame(7) == 1.0


def test_distributed_progress_routes_shared_outputs_to_the_assigned_worker() -> None:
    tracker = _DistributedProgress(worker_ids=("GPU-a", "GPU-b", "GPU-c"))
    for worker_index, worker_id in enumerate(tracker.worker_ids):
        tracker.frame_plans[worker_id] = FramePlan(
            total=120,
            worker=worker_index,
            assigned=40,
            start=1,
            end=120,
            step=1,
        )

    assert tracker.worker_for_frame(79) == "GPU-a"
    assert tracker.worker_for_frame(80) == "GPU-b"
    assert tracker.worker_for_frame(81) == "GPU-c"
    assert tracker.worker_for_frame(121) is None
