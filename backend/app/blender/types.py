"""Blender registry enums and immutable image manifest."""

from enum import StrEnum


class RuntimeSource(StrEnum):
    BUNDLED = "bundled"
    OFFICIAL = "official"
    MANUAL = "manual"


class RuntimeState(StrEnum):
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"


class OperationKind(StrEnum):
    DOWNLOAD = "download"
    UPLOAD = "upload"
    INSTALL = "install"


class OperationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


BUNDLED_VERSIONS = ("5.2.0", "4.5.11", "4.2.22", "4.1.1", "3.6.23")
DEFAULT_ACTIVE_VERSION = "4.5.11"
SUPPORTED_VERSIONS = frozenset(BUNDLED_VERSIONS)
