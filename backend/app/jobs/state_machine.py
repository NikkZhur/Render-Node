"""The only authority for Job status transitions."""

from __future__ import annotations

from app.jobs.exceptions import JobConflictError
from app.jobs.types import JobStatus

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset({JobStatus.READY}),
    JobStatus.READY: frozenset({JobStatus.QUEUED}),
    JobStatus.QUEUED: frozenset({JobStatus.RENDERING, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.RENDERING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.CANCELLED: frozenset({JobStatus.QUEUED}),
    JobStatus.COMPLETED: frozenset(),
}


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise JobConflictError(
            "invalid_job_transition",
            f"Job cannot transition from {current.value} to {target.value}",
            details={"current_status": current.value, "target_status": target.value},
        )
    return target
