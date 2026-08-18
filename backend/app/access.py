"""Ephemeral first-browser sessions and one-time pairing invitations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

SESSION_ENTROPY_BYTES = 32
INVITATION_ENTROPY_BYTES = 32
SESSION_LIFETIME_SECONDS = 7 * 24 * 60 * 60
INVITATION_LIFETIME_SECONDS = 5 * 60
MAX_BROWSER_SESSIONS = 5
PRODUCTION_COOKIE_NAME = "__Host-render_node_session"
DEVELOPMENT_COOKIE_NAME = "render_node_dev_session"


class AccessManagerError(Exception):
    """Base class for expected access lifecycle failures."""


class NodeAlreadyClaimedError(AccessManagerError):
    """The automatic first-browser slot was already taken."""


class BrowserSessionRequiredError(AccessManagerError):
    """A valid browser session was not presented."""


class DeviceLimitReachedError(AccessManagerError):
    """All browser session slots are occupied."""


class InvalidInvitationError(AccessManagerError):
    """An invitation is unknown, expired, or already consumed."""


@dataclass(frozen=True, slots=True)
class SessionGrant:
    token: str
    expires_at: float
    session_count: int


@dataclass(frozen=True, slots=True)
class InvitationGrant:
    token: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class AccessSnapshot:
    claimed: bool
    session_count: int
    invitation_count: int


@dataclass(frozen=True, slots=True)
class _ExpiringRecord:
    expires_at: float


class AccessManager:
    """Own the in-memory, single-process browser access state."""

    def __init__(
        self,
        *,
        production: bool,
        clock: Callable[[], float] = time.time,
        session_lifetime_seconds: int = SESSION_LIFETIME_SECONDS,
        invitation_lifetime_seconds: int = INVITATION_LIFETIME_SECONDS,
        max_sessions: int = MAX_BROWSER_SESSIONS,
    ) -> None:
        self.production = production
        self.cookie_name = PRODUCTION_COOKIE_NAME if production else DEVELOPMENT_COOKIE_NAME
        self.session_lifetime_seconds = session_lifetime_seconds
        self.invitation_lifetime_seconds = invitation_lifetime_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._claimed = False
        self._sessions: dict[bytes, _ExpiringRecord] = {}
        self._invitations: dict[bytes, _ExpiringRecord] = {}
        self._lock = asyncio.Lock()

    async def claim(self, existing_token: str | None) -> SessionGrant:
        async with self._lock:
            now = self._clock()
            self._purge_expired(now)
            existing_digest = self._find_digest(self._sessions, existing_token)
            if existing_digest is not None and existing_token is not None:
                record = self._sessions[existing_digest]
                return SessionGrant(existing_token, record.expires_at, len(self._sessions))
            if self._claimed:
                raise NodeAlreadyClaimedError

            token, digest = self._new_token(SESSION_ENTROPY_BYTES)
            expires_at = now + self.session_lifetime_seconds
            self._sessions[digest] = _ExpiringRecord(expires_at=expires_at)
            self._claimed = True
            return SessionGrant(token, expires_at, len(self._sessions))

    async def authenticate(self, token: str | None) -> bool:
        async with self._lock:
            now = self._clock()
            self._purge_expired(now)
            return self._find_digest(self._sessions, token) is not None

    async def create_invitation(self) -> InvitationGrant:
        async with self._lock:
            now = self._clock()
            self._purge_expired(now)
            if len(self._sessions) >= self.max_sessions:
                raise DeviceLimitReachedError
            available_slots = self.max_sessions - len(self._sessions)
            while len(self._invitations) >= available_slots:
                del self._invitations[next(iter(self._invitations))]
            token, digest = self._new_token(INVITATION_ENTROPY_BYTES)
            expires_at = now + self.invitation_lifetime_seconds
            self._invitations[digest] = _ExpiringRecord(expires_at=expires_at)
            return InvitationGrant(token=token, expires_at=expires_at)

    async def redeem(self, invitation_token: str) -> SessionGrant:
        async with self._lock:
            now = self._clock()
            self._purge_expired(now)
            invitation_digest = self._find_digest(self._invitations, invitation_token)
            if invitation_digest is None:
                raise InvalidInvitationError
            if len(self._sessions) >= self.max_sessions:
                raise DeviceLimitReachedError

            del self._invitations[invitation_digest]
            session_token, session_digest = self._new_token(SESSION_ENTROPY_BYTES)
            expires_at = now + self.session_lifetime_seconds
            self._sessions[session_digest] = _ExpiringRecord(expires_at=expires_at)
            return SessionGrant(session_token, expires_at, len(self._sessions))

    async def reset(self) -> None:
        async with self._lock:
            self._sessions.clear()
            self._invitations.clear()
            self._claimed = False

    async def snapshot(self) -> AccessSnapshot:
        async with self._lock:
            self._purge_expired(self._clock())
            return AccessSnapshot(
                claimed=self._claimed,
                session_count=len(self._sessions),
                invitation_count=len(self._invitations),
            )

    def remaining_session_seconds(self, expires_at: float) -> int:
        return max(0, int(expires_at - self._clock()))

    @staticmethod
    def _new_token(entropy_bytes: int) -> tuple[str, bytes]:
        token = secrets.token_urlsafe(entropy_bytes)
        return token, AccessManager._digest(token)

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("ascii")).digest()

    @classmethod
    def _find_digest(
        cls,
        records: dict[bytes, _ExpiringRecord],
        token: str | None,
    ) -> bytes | None:
        if token is None or not token or len(token) > 512 or not token.isascii():
            return None
        candidate = cls._digest(token)
        for digest in records:
            if hmac.compare_digest(candidate, digest):
                return digest
        return None

    def _purge_expired(self, now: float) -> None:
        self._sessions = {
            digest: record for digest, record in self._sessions.items() if record.expires_at > now
        }
        self._invitations = {
            digest: record
            for digest, record in self._invitations.items()
            if record.expires_at > now
        }
