"""Best-effort Blender stdout progress parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

FRAME_RE = re.compile(r"\bFra:\s*(?P<frame>\d+)\b")
SAMPLE_RES = (
    re.compile(
        r"\bSample\s+(?P<sample>\d+)\s*/\s*(?P<total>\d+)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bRendering\s+(?P<sample>\d+)\s*/\s*(?P<total>\d+)\s+samples?\b",
        re.IGNORECASE,
    ),
)
FAKE_PROGRESS_RE = re.compile(
    r"^RENDER_NODE_PROGRESS\s+(?P<progress>0(?:\.\d+)?|1(?:\.0+)?)"
    r"(?:\s+frame=(?P<frame>\d+))?$"
)


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    frame: int | None
    sample: int | None
    total_samples: int | None
    frame_progress: float | None


def parse_progress(line: str) -> ProgressUpdate | None:
    fake = FAKE_PROGRESS_RE.fullmatch(line.strip())
    if fake is not None:
        return ProgressUpdate(
            frame=int(fake["frame"]) if fake["frame"] else None,
            sample=None,
            total_samples=None,
            frame_progress=float(fake["progress"]),
        )

    frame_match = FRAME_RE.search(line)
    sample_match: re.Match[str] | None = None
    for sample_pattern in SAMPLE_RES:
        sample_match = sample_pattern.search(line)
        if sample_match is not None:
            break
    if frame_match is None and sample_match is None:
        return None
    sample = int(sample_match["sample"]) if sample_match else None
    total = int(sample_match["total"]) if sample_match else None
    progress = min(sample / total, 1.0) if sample is not None and total else None
    return ProgressUpdate(
        frame=int(frame_match["frame"]) if frame_match else None,
        sample=sample,
        total_samples=total,
        frame_progress=progress,
    )
