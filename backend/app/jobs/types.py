"""Stable Job enum values shared by persistence and API schemas."""

from enum import StrEnum


class JobStatus(StrEnum):
    CREATED = "created"
    READY = "ready"
    QUEUED = "queued"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisStatus(StrEnum):
    NONE = "none"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


ANALYSIS_ACTIVE = frozenset({AnalysisStatus.PENDING, AnalysisStatus.RUNNING})


class RenderEngine(StrEnum):
    CYCLES = "CYCLES"
    BLENDER_EEVEE = "BLENDER_EEVEE"
    BLENDER_WORKBENCH = "BLENDER_WORKBENCH"


class ComputeDevice(StrEnum):
    CPU = "CPU"
    CUDA = "CUDA"
    OPTIX = "OPTIX"


class GpuRenderMode(StrEnum):
    FRAME_PER_GPU = "FRAME_PER_GPU"
    ALL_GPUS_PER_FRAME = "ALL_GPUS_PER_FRAME"


class FrameMode(StrEnum):
    SINGLE = "SINGLE"
    RANGE = "RANGE"
    ALL = "ALL"
