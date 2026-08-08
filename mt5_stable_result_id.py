"""Stable optimization result ids — must match lib/optimizer/reporter-store.ts."""

from __future__ import annotations

import hashlib

JOB_EVENT_PASS_ID = 0
VALIDATION_RESULT_EVENT = "validation_result"


def stable_result_id(
    *,
    run_id: str,
    job_index: int,
    event_type: str = VALIDATION_RESULT_EVENT,
    pass_id: int | None = None,
) -> str:
    """sha256 hex of `{runId}:{jobIndex}:{eventType}:{passId}` (TS stableResultId)."""
    resolved_pass = JOB_EVENT_PASS_ID if pass_id is None else pass_id
    payload = f"{run_id}:{job_index}:{event_type}:{resolved_pass}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
