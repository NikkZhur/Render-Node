"""Transient per-process and overall render progress state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from app.blender.progress import FramePlan, ProgressUpdate


@dataclass(slots=True)
class _SingleProcessProgress:
    plan: FramePlan | None = None
    active_frame: int | None = None
    frame_progress: float = 0.0
    completed_frames: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def set_plan(self, plan: FramePlan) -> None:
        if plan.worker != 0 or plan.assigned != plan.total:
            raise RuntimeError("Single Blender process reported a distributed frame plan")
        if self.plan is not None and self.plan != plan:
            raise RuntimeError("Blender reported inconsistent frame plans")
        self.plan = plan
        if self.active_frame is None:
            self.active_frame = plan.start

    def _frame_index(self, frame: int | None) -> int | None:
        plan = self.plan
        if plan is None or frame is None or not plan.start <= frame <= plan.end:
            return None
        offset = frame - plan.start
        return offset // plan.step if offset % plan.step == 0 else None

    def record(self, update: ProgressUpdate) -> float | None:
        if update.frame is not None:
            if update.frame != self.active_frame:
                self.frame_progress = 0.0
            self.active_frame = update.frame
        if update.frame_progress is not None:
            self.frame_progress = max(self.frame_progress, update.frame_progress)
        return self.overall()

    def complete_frame(self, frame: int) -> float | None:
        index = self._frame_index(frame)
        if index is not None:
            self.completed_frames = max(self.completed_frames, index + 1)
        return self.overall()

    def overall(self) -> float | None:
        if self.plan is None or not self.plan.total:
            # A per-frame fraction cannot stand in for unknown animation progress.
            return None
        index = self._frame_index(self.active_frame)
        rendered = max(
            self.completed_frames,
            index + self.frame_progress if index is not None else 0.0,
        )
        return min(rendered / self.plan.total, 1.0)


@dataclass(slots=True)
class _WorkerProgress:
    assigned_frames: int | None = None
    completed_frames: set[int] = field(default_factory=set)
    active_frame: int | None = None
    frame_progress: float = 0.0

    def record_frame(self, frame_number: int, frame_progress: float) -> None:
        if self.active_frame is not None and self.active_frame != frame_number:
            self.completed_frames.add(self.active_frame)
            self.frame_progress = 0.0
        self.active_frame = frame_number
        if frame_number not in self.completed_frames:
            self.frame_progress = max(self.frame_progress, frame_progress)

    def complete_frame(self, frame_number: int) -> None:
        self.completed_frames.add(frame_number)
        if self.active_frame == frame_number:
            self.active_frame = None
            self.frame_progress = 0.0

    def overall(self) -> float | None:
        if not self.assigned_frames:
            return None
        completed = min(len(self.completed_frames), self.assigned_frames)
        partial = (
            self.frame_progress
            if self.active_frame is not None and self.active_frame not in self.completed_frames
            else 0.0
        )
        return min((completed + partial) / self.assigned_frames, 1.0)


@dataclass(slots=True)
class _DistributedProgress:
    worker_ids: tuple[str, ...]
    total_frames: int | None = None
    completed_frames: set[int] = field(default_factory=set)
    active_frames: dict[str, tuple[int, float]] = field(default_factory=dict)
    workers: dict[str, _WorkerProgress] = field(default_factory=dict)
    frame_plans: dict[str, FramePlan] = field(default_factory=dict)
    registered_outputs: set[Path] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def worker_for_frame(self, frame_number: int) -> str | None:
        if len(self.worker_ids) == 1:
            return self.worker_ids[0]
        worker_count = len(self.worker_ids)
        for worker_id, plan in self.frame_plans.items():
            first_frame = plan.start + plan.worker * plan.step
            worker_step = plan.step * worker_count
            if (
                worker_step > 0
                and first_frame <= frame_number <= plan.end
                and (frame_number - first_frame) % worker_step == 0
            ):
                return worker_id
        return None

    def overall(self) -> float | None:
        if not self.total_frames:
            return None
        partial_by_frame: dict[int, float] = {}
        for frame_number, frame_progress in self.active_frames.values():
            if frame_number not in self.completed_frames:
                partial_by_frame[frame_number] = max(
                    partial_by_frame.get(frame_number, 0.0), frame_progress
                )
        completed = min(len(self.completed_frames), self.total_frames)
        return min((completed + sum(partial_by_frame.values())) / self.total_frames, 1.0)
