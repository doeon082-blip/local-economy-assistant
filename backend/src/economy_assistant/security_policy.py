"""Fail-closed action checks, token comparison and rate limiting used by API routes."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import secrets
import threading
import time

class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"

@dataclass(frozen=True)
class ActionRequest:
    tool: str
    sends_data_outside: bool = False
    changes_data: bool = False

@dataclass(frozen=True)
class SecurityPolicy:
    allowed_tools: frozenset[str]
    approval_required_tools: frozenset[str] = frozenset()
    allow_external_sends: bool = False
    allow_data_changes: bool = False

def require_allowed(request: ActionRequest, policy: SecurityPolicy, *, trusted_user_approved: bool = False) -> None:
    if request.tool not in policy.allowed_tools:
        raise PermissionError("도구가 허용 목록에 없습니다.")
    if request.sends_data_outside and not policy.allow_external_sends:
        raise PermissionError("외부 전송이 정책으로 차단되었습니다.")
    if request.changes_data and not policy.allow_data_changes:
        raise PermissionError("데이터 변경이 정책으로 차단되었습니다.")
    if request.tool in policy.approval_required_tools and not trusted_user_approved:
        raise PermissionError("사용자의 명시적 승인(요청 버튼)이 필요합니다.")


def tokens_match(supplied: str, expected: str) -> bool:
    """Constant-time comparison that is safe for non-ASCII input.

    ``secrets.compare_digest`` raises TypeError for non-ASCII ``str`` values, which
    would turn a malformed header into an HTTP 500. Comparing UTF-8 bytes avoids that.
    """
    if not supplied or not expected:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


class RateLimiter:
    """Thread-safe sliding-window limiter keyed by client (in-memory, per process)."""

    def __init__(self, window_seconds: float = 60.0, max_keys: int = 10_000, clock=time.monotonic) -> None:
        self._window = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        return hits

    def is_blocked(self, key: str, limit: int) -> bool:
        with self._lock:
            now = self._clock()
            blocked = len(self._prune(key, now)) >= limit
            if not self._hits[key]:
                del self._hits[key]
            return blocked

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            if len(self._hits) >= self._max_keys and key not in self._hits:
                # Memory bound: drop the oldest-seen keys instead of growing without limit.
                for stale in list(self._hits)[: self._max_keys // 10 or 1]:
                    del self._hits[stale]
            self._prune(key, now).append(now)

    def allow(self, key: str, limit: int) -> bool:
        """Record a request and return False once ``limit`` is exceeded in the window."""
        if self.is_blocked(key, limit):
            return False
        self.record(key)
        return True
