"""Strict client for the official Blender release archive."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiofiles
import httpx

OFFICIAL_ORIGIN = "https://download.blender.org"
RELEASE_ROOT = f"{OFFICIAL_ORIGIN}/release/"
BRANCH_RE = re.compile(r"^Blender(?P<major>\d+)\.(?P<minor>\d+)/$")
ARCHIVE_RE = re.compile(r"^blender-(?P<version>\d+\.\d+\.\d+)-linux-x64\.tar\.(?:xz|bz2)$")
SHA256_RE = re.compile(r"^(?P<digest>[0-9a-fA-F]{64})  (?P<filename>[^/\r\n]+)$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class CatalogError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OfficialRelease:
    version: str
    filename: str
    archive_url: str
    manifest_url: str


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href is not None:
            self.hrefs.append(href)


def parse_release_branches(document: str) -> list[str]:
    parser = _Links()
    parser.feed(document)
    branches = {
        href
        for href in parser.hrefs
        if (match := BRANCH_RE.fullmatch(href))
        and (int(match["major"]), int(match["minor"])) >= (3, 6)
    }
    return sorted(branches)


def _official_url(base: str, relative: str) -> str:
    url = urljoin(base, relative)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "download.blender.org":
        raise CatalogError("Official catalog produced a non-official URL")
    return url


def parse_branch_releases(branch_url: str, document: str) -> list[OfficialRelease]:
    parser = _Links()
    parser.feed(document)
    releases: dict[str, OfficialRelease] = {}
    for href in parser.hrefs:
        filename = href.rsplit("/", maxsplit=1)[-1]
        if href != filename:
            continue
        match = ARCHIVE_RE.fullmatch(filename)
        if match is None:
            continue
        version = match["version"]
        release = OfficialRelease(
            version=version,
            filename=filename,
            archive_url=_official_url(branch_url, filename),
            manifest_url=_official_url(branch_url, f"blender-{version}.sha256"),
        )
        existing = releases.get(version)
        if existing is not None and existing.filename != filename:
            raise CatalogError(f"Multiple Linux x64 archives for Blender {version}")
        releases[version] = release
    return list(releases.values())


def parse_manifest(document: str, filename: str) -> str:
    matches: list[str] = []
    for raw_line in document.splitlines():
        if not raw_line:
            continue
        match = SHA256_RE.fullmatch(raw_line)
        if match is None:
            raise CatalogError("Official checksum manifest has an unknown format")
        if match["filename"] == filename:
            matches.append(match["digest"].lower())
    if len(matches) != 1:
        raise CatalogError(f"Official checksum manifest has no unique entry for {filename}")
    return matches[0]


def version_key(version: str) -> tuple[int, int, int]:
    if VERSION_RE.fullmatch(version) is None:
        raise CatalogError("Invalid Blender patch version")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


class OfficialCatalog:
    """TTL-cached catalog whose URLs are confined to the official archive."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        download_timeout_seconds: int = 3600,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(30, read=download_timeout_seconds),
            headers={"User-Agent": "Render-Node/0.1"},
        )
        self._owns_client = client is None
        self._releases: dict[str, OfficialRelease] = {}
        self._checksums: dict[str, str] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def releases(self) -> list[OfficialRelease]:
        await self._refresh_if_stale()
        return sorted(
            self._releases.values(), key=lambda item: version_key(item.version), reverse=True
        )

    async def release(self, version: str) -> OfficialRelease:
        if VERSION_RE.fullmatch(version) is None:
            raise CatalogError("Invalid Blender patch version")
        await self._refresh_if_stale()
        try:
            return self._releases[version]
        except KeyError as exc:
            raise CatalogError(
                f"Blender {version} is not in the official Linux x64 catalog"
            ) from exc

    async def checksum(self, release: OfficialRelease) -> str:
        cached = self._checksums.get(release.version)
        if cached is not None:
            return cached
        response = await self._get(release.manifest_url, max_bytes=2 * 1024 * 1024)
        digest = parse_manifest(response, release.filename)
        self._checksums[release.version] = digest
        return digest

    async def identify_digest(self, digest: str) -> tuple[OfficialRelease, str] | None:
        releases = await self.releases()
        semaphore = asyncio.Semaphore(8)

        async def load(item: OfficialRelease) -> tuple[OfficialRelease, str] | None:
            async with semaphore:
                try:
                    return item, await self.checksum(item)
                except (CatalogError, httpx.HTTPError, UnicodeError, OSError, ValueError):
                    return None

        for result in await asyncio.gather(*(load(item) for item in releases)):
            if result is not None and result[1] == digest.lower():
                return result
        return None

    async def download_archive(
        self,
        release: OfficialRelease,
        destination: Path,
        *,
        expected_sha256: str,
        max_bytes: int,
        on_progress: Callable[[int, int | None], Awaitable[None]],
    ) -> tuple[str, int]:
        if not release.archive_url.startswith(f"{OFFICIAL_ORIGIN}/release/"):
            raise CatalogError("Refusing a non-official Blender archive URL")
        digest = hashlib.sha256()
        processed = 0
        async with self._client.stream("GET", release.archive_url) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            total = int(content_length) if content_length and content_length.isdigit() else None
            if total is not None and total > max_bytes:
                raise CatalogError("Blender archive exceeds its size limit")
            disk_usage = await asyncio.to_thread(shutil.disk_usage, destination.parent)
            if total is not None and total > disk_usage.free:
                raise CatalogError("Not enough free space for the Blender archive")
            async with aiofiles.open(destination, "xb") as target:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    processed += len(chunk)
                    if processed > max_bytes:
                        raise CatalogError("Blender archive exceeds its size limit")
                    digest.update(chunk)
                    await target.write(chunk)
                    await on_progress(processed, total)
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise CatalogError("Downloaded archive does not match the official SHA-256")
        return actual, processed

    async def _refresh_if_stale(self) -> None:
        if self._releases and time.monotonic() < self._expires_at:
            return
        async with self._lock:
            if self._releases and time.monotonic() < self._expires_at:
                return
            root = await self._get(RELEASE_ROOT, max_bytes=4 * 1024 * 1024)
            branches = parse_release_branches(root)
            if not branches:
                raise CatalogError("Official release archive contains no supported branches")
            pages = await asyncio.gather(
                *(
                    self._get(_official_url(RELEASE_ROOT, branch), max_bytes=8 * 1024 * 1024)
                    for branch in branches
                )
            )
            releases: dict[str, OfficialRelease] = {}
            for branch, page in zip(branches, pages, strict=True):
                branch_url = _official_url(RELEASE_ROOT, branch)
                for release in parse_branch_releases(branch_url, page):
                    releases[release.version] = release
            if not releases:
                raise CatalogError("Official archive contains no Linux x64 releases")
            self._releases = releases
            self._expires_at = time.monotonic() + self._ttl_seconds

    async def _get(self, url: str, *, max_bytes: int) -> str:
        if not url.startswith(f"{OFFICIAL_ORIGIN}/"):
            raise CatalogError("Refusing a non-official Blender URL")
        try:
            response = await self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CatalogError("Official Blender archive request failed") from exc
        if len(response.content) > max_bytes:
            raise CatalogError("Official catalog response exceeds its size limit")
        return response.content.decode("utf-8", errors="strict")
