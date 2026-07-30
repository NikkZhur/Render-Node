import pytest

from app.jobs.exceptions import JobConflictError
from app.jobs.state_machine import ALLOWED_TRANSITIONS, transition_job
from app.jobs.types import JobStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [(current, target) for current, targets in ALLOWED_TRANSITIONS.items() for target in targets],
)
def test_all_declared_transitions_are_allowed(current: JobStatus, target: JobStatus) -> None:
    assert transition_job(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.CREATED, JobStatus.QUEUED),
        (JobStatus.READY, JobStatus.COMPLETED),
        (JobStatus.RENDERING, JobStatus.READY),
        (JobStatus.COMPLETED, JobStatus.QUEUED),
    ],
)
def test_invalid_transitions_are_rejected(current: JobStatus, target: JobStatus) -> None:
    with pytest.raises(JobConflictError) as error:
        transition_job(current, target)

    assert error.value.code == "invalid_job_transition"
    assert error.value.details == {
        "current_status": current.value,
        "target_status": target.value,
    }
