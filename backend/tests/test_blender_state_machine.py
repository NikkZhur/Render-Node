import pytest

from app.blender.exceptions import BlenderConflictError
from app.blender.state_machine import transition_operation, transition_runtime
from app.blender.types import OperationState, RuntimeState


def test_runtime_and_operation_transitions_are_explicit() -> None:
    assert (
        transition_runtime(RuntimeState.DOWNLOADING, RuntimeState.DOWNLOADED)
        is RuntimeState.DOWNLOADED
    )
    assert (
        transition_operation(OperationState.RUNNING, OperationState.COMPLETED)
        is OperationState.COMPLETED
    )
    with pytest.raises(BlenderConflictError):
        transition_runtime(RuntimeState.INSTALLED, RuntimeState.DOWNLOADED)
    with pytest.raises(BlenderConflictError):
        transition_operation(OperationState.COMPLETED, OperationState.RUNNING)
