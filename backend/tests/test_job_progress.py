from app.blender.progress import FramePlan
from app.jobs.progress import _DistributedProgress


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
