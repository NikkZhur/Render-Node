"""Bounded, version-independent results emitted by the trusted inspection scripts."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SceneMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = Field(min_length=1, max_length=128)
    frame_start: int = Field(ge=-1_048_574, le=1_048_574)
    frame_end: int = Field(ge=-1_048_574, le=1_048_574)
    frame_step: int = Field(ge=1, le=1_048_574)
    resolution_x: int = Field(ge=1, le=65_536)
    resolution_y: int = Field(ge=1, le=65_536)
    resolution_percentage: int = Field(ge=1, le=32_767)
    camera: str | None = Field(default=None, max_length=1024)
    use_osl: bool = False
    use_guiding: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.frame_end < self.frame_start:
            raise ValueError("Scene frame range is reversed")
        return self

    @property
    def frame_count(self) -> int:
        return (self.frame_end - self.frame_start) // self.frame_step + 1


class DeviceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backends: list[Literal["CUDA", "OPTIX"]] = Field(max_length=2)
