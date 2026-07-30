from app.jobs.exceptions import ServiceError


class ArtifactNotFoundError(ServiceError):
    def __init__(self, message: str = "Artifact was not found") -> None:
        super().__init__("artifact_not_found", message)


class ArtifactRejectedError(ServiceError):
    pass
