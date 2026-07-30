"""Validated state transitions for runtimes and operations."""

from app.blender.exceptions import BlenderConflictError
from app.blender.types import OperationState, RuntimeState

_RUNTIME_TRANSITIONS = {
    RuntimeState.AVAILABLE: {RuntimeState.DOWNLOADING},
    RuntimeState.DOWNLOADING: {RuntimeState.DOWNLOADED, RuntimeState.FAILED},
    RuntimeState.DOWNLOADED: {RuntimeState.INSTALLING, RuntimeState.FAILED},
    RuntimeState.INSTALLING: {RuntimeState.INSTALLED, RuntimeState.FAILED},
    RuntimeState.FAILED: {
        RuntimeState.DOWNLOADING,
        RuntimeState.DOWNLOADED,
        RuntimeState.INSTALLING,
    },
    RuntimeState.INSTALLED: set(),
}
_OPERATION_TRANSITIONS = {
    OperationState.PENDING: {OperationState.RUNNING, OperationState.FAILED},
    OperationState.RUNNING: {OperationState.COMPLETED, OperationState.FAILED},
    OperationState.COMPLETED: set(),
    OperationState.FAILED: set(),
}


def transition_runtime(current: RuntimeState, target: RuntimeState) -> RuntimeState:
    if target not in _RUNTIME_TRANSITIONS[current]:
        raise BlenderConflictError(
            "invalid_blender_state_transition",
            f"Cannot transition Blender runtime from {current.value} to {target.value}",
        )
    return target


def transition_operation(current: OperationState, target: OperationState) -> OperationState:
    if target not in _OPERATION_TRANSITIONS[current]:
        raise BlenderConflictError(
            "invalid_blender_operation_transition",
            f"Cannot transition Blender operation from {current.value} to {target.value}",
        )
    return target
