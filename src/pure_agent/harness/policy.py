"""Harness: retry policy, timeout policy, trace, span.

Phase 5 implementation. Provides the production-grade "harness" around
AIAgentLoop and Subagent runs.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "h") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ─── retry policy ─────────────────────────────────────────────────────────


class RetryStrategy(str, Enum):
    NONE = "none"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIXED = "fixed"


@dataclass
class RetryPolicy:
    """Retry policy for tool / subagent operations."""

    max_attempts: int = 3
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    initial_backoff_s: float = 1.0
    max_backoff_s: float = 30.0
    retryable_errors: list[str] = field(default_factory=lambda: ["tool_error", "tool_timeout"])

    def backoff(self, attempt: int) -> float:
        if self.strategy == RetryStrategy.NONE:
            return 0.0
        if self.strategy == RetryStrategy.FIXED:
            return self.initial_backoff_s
        if self.strategy == RetryStrategy.LINEAR:
            return min(self.max_backoff_s, self.initial_backoff_s * attempt)
        # exponential
        return min(
            self.max_backoff_s, self.initial_backoff_s * (2 ** (attempt - 1))
        )

    def should_retry(self, next_attempt: int, error_code: str | None) -> bool:
        """Should we try `next_attempt` again after the previous attempt failed?

        `next_attempt` is 1-indexed: should_retry(1, ...) means "do we retry after
        attempt 1 failed?" — i.e. are we going into attempt 2?
        """
        # We've already done `next_attempt - 1` tries. Will the next one be
        # attempt #next_attempt? Stop if it would exceed max_attempts.
        if next_attempt > self.max_attempts:
            return False
        if error_code is None:
            return True
        if not self.retryable_errors:
            return True
        return error_code in self.retryable_errors


async def with_retry(
    fn: Callable[..., Any],
    *args: Any,
    policy: RetryPolicy,
    on_retry: Callable[[int, Exception, float], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Run an async callable with retry policy.

    on_retry(attempt, exception, backoff_s) is called before each retry.
    """
    last_exc: Exception | None = None
    # attempts_done: number of attempts that have completed (1-indexed)
    for attempts_done in range(1, policy.max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            # Build an error code from the exception's explicit .code attribute
            # (if any) OR the str of the exception (which usually contains the
            # domain-specific code like "tool_error", "tool_timeout").
            err_code = getattr(e, "code", None) or str(e) or type(e).__name__
            # about to try attempt #(attempts_done + 1) — should we?
            if not policy.should_retry(attempts_done + 1, str(err_code)):
                raise
            backoff = policy.backoff(attempts_done)
            if on_retry:
                on_retry(attempts_done, e, backoff)
            if backoff > 0:
                await asyncio.sleep(backoff)
    if last_exc:
        raise last_exc
    raise RuntimeError("retry loop exited without result")


# ─── timeout policy ───────────────────────────────────────────────────────


@dataclass
class TimeoutPolicy:
    """Total timeout for an operation (not per-call)."""

    per_call_s: float = 120.0
    per_total_s: float | None = None  # None = no total limit

    def check(self, elapsed: float) -> None:
        from pure_agent.agent.watchdog import WatchdogTimeout

        if self.per_total_s is not None and elapsed > self.per_total_s:
            raise WatchdogTimeout("harness_total", self.per_total_s, elapsed)


# ─── trace + span ────────────────────────────────────────────────────────


@dataclass
class Trace:
    """A trace record (one operation or one event)."""

    id: str = field(default_factory=lambda: _new_id("tr"))
    session_id: str | None = None
    parent_id: str | None = None
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=_now_iso)
    duration_ms: int = 0


@dataclass
class Span:
    """A timed operation that can be recorded to DB."""

    name: str
    trace: Trace
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def end(self, **attrs: Any) -> Trace:
        self.ended_at = time.monotonic()
        self.attributes.update(attrs)
        self.trace.duration_ms = int((self.ended_at - self.started_at) * 1000)
        self.trace.payload = {**self.trace.payload, **self.attributes}
        return self.trace


class Tracer:
    """Records traces to memory (and optionally DB)."""

    def __init__(self, *, session_id: str | None = None, db: Any | None = None) -> None:
        self.session_id = session_id
        self.db = db
        self.traces: list[Trace] = []

    @contextmanager
    def span(self, name: str, *, parent_id: str | None = None, **attrs: Any):
        trace = Trace(
            session_id=self.session_id,
            parent_id=parent_id,
            event_type=name,
            payload=attrs,
        )
        sp = Span(name=name, trace=trace, attributes=attrs)
        try:
            yield sp
        finally:
            sp.end()
            self._record(trace)

    def _record(self, trace: Trace) -> None:
        self.traces.append(trace)
        if self.db is not None:
            try:
                self.db.conn.execute(
                    "INSERT INTO traces (id, session_id, turn_id, event_type, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        trace.id,
                        trace.session_id,
                        trace.parent_id,
                        trace.event_type,
                        json.dumps(trace.payload, ensure_ascii=False),
                        trace.started_at,
                    ),
                )
            except Exception:
                pass

    def all(self) -> list[Trace]:
        return list(self.traces)

    def clear(self) -> None:
        self.traces.clear()


__all__ = [
    "RetryPolicy",
    "RetryStrategy",
    "TimeoutPolicy",
    "Trace",
    "Span",
    "Tracer",
    "with_retry",
]
