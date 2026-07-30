"""Blender registry service errors."""

from app.jobs.exceptions import ServiceError


class BlenderNotFoundError(ServiceError):
    def __init__(self, message: str = "Blender runtime was not found") -> None:
        super().__init__("blender_not_found", message)


class BlenderOperationNotFoundError(ServiceError):
    def __init__(self) -> None:
        super().__init__("blender_operation_not_found", "Blender operation was not found")


class BlenderConflictError(ServiceError):
    pass


class BlenderRejectedError(ServiceError):
    pass
