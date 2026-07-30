from app.blender.progress import parse_progress


def test_progress_parser_accepts_blender_samples_and_fake_protocol() -> None:
    update = parse_progress("Fra:12 Mem:14.00M | Time:00:01.00 | Rendering 64 / 128 samples")
    assert update is not None
    assert update.frame == 12
    assert update.sample == 64
    assert update.total_samples == 128
    assert update.frame_progress == 0.5

    fake = parse_progress("RENDER_NODE_PROGRESS 0.375 frame=7")
    assert fake is not None
    assert fake.frame == 7
    assert fake.frame_progress == 0.375


def test_unknown_or_malformed_progress_is_ignored() -> None:
    assert parse_progress("ordinary Blender diagnostic") is None
    update = parse_progress("Fra:2 | Rendering 3 / 0 samples")
    assert update is not None
    assert update.frame_progress is None
