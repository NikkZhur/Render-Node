from enum import StrEnum


class ArtifactKind(StrEnum):
    FRAME_ORIGINAL = "frame_original"
    FRAME_PREVIEW = "frame_preview"
    BLENDER_LOG = "blender_log"
    FRAMES_ZIP = "frames_zip"
