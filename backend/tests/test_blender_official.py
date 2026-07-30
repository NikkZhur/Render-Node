from __future__ import annotations

import pytest

from app.blender.official import (
    CatalogError,
    parse_branch_releases,
    parse_manifest,
    parse_release_branches,
)


def test_catalog_parser_selects_exact_linux_x64_patch_archives() -> None:
    root = '<a href="Blender3.5/">old</a><a href="Blender4.5/">current</a>'
    page = """
    <a href="blender-4.5.11-linux-x64.tar.xz">linux</a>
    <a href="blender-4.5.11-macos-arm64.dmg">mac</a>
    <a href="https://evil.example/blender-4.5.12-linux-x64.tar.xz">evil</a>
    """

    assert parse_release_branches(root) == ["Blender4.5/"]
    releases = parse_branch_releases("https://download.blender.org/release/Blender4.5/", page)
    assert [(item.version, item.filename) for item in releases] == [
        ("4.5.11", "blender-4.5.11-linux-x64.tar.xz")
    ]
    assert releases[0].archive_url.startswith("https://download.blender.org/release/")


def test_manifest_requires_one_exact_filename_entry() -> None:
    filename = "blender-4.5.11-linux-x64.tar.xz"
    digest = "a" * 64
    assert parse_manifest(f"{digest}  {filename}\n", filename) == digest

    with pytest.raises(CatalogError):
        parse_manifest(f"{digest}  other.tar.xz\n", filename)
    with pytest.raises(CatalogError):
        parse_manifest(f"{digest}  {filename}\n{digest}  {filename}\n", filename)
    with pytest.raises(CatalogError, match="unknown format"):
        parse_manifest(f"comment\n{digest}  {filename}\n", filename)
