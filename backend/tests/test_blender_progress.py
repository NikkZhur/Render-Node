import pytest

from app.blender.progress import parse_progress


@pytest.mark.parametrize(
    ("line", "expected_frame"),
    [
        (
            "Fra:1 Mem:1654.49M | Time:06:04.84 | Remaining:04:05.45 | "
            "Scene, ViewLayer | Sample 48/512",
            1,
        ),
        ("Fra: 12 | Path Tracing Sample 64 / 128", 12),
        ("Fra:12 Mem:14.00M | Time:00:01.00 | Rendering 64 / 128 samples", 12),
    ],
)
def test_progress_parser_accepts_current_and_legacy_blender_samples(
    line: str, expected_frame: int
) -> None:
    update = parse_progress(line)
    assert update is not None
    assert update.frame == expected_frame
    expected_sample = 48 if expected_frame == 1 else 64
    expected_total = 512 if expected_frame == 1 else 128
    assert update.sample == expected_sample
    assert update.total_samples == expected_total
    assert update.frame_progress == expected_sample / expected_total


def test_progress_parser_accepts_fake_protocol() -> None:
    fake = parse_progress("RENDER_NODE_PROGRESS 0.375 frame=7")

    assert fake is not None
    assert fake.frame == 7
    assert fake.frame_progress == 0.375


def test_unknown_or_malformed_progress_is_ignored() -> None:
    assert parse_progress("ordinary Blender diagnostic") is None
    update = parse_progress("Fra:2 | Sample 3/0")
    assert update is not None
    assert update.frame_progress is None
