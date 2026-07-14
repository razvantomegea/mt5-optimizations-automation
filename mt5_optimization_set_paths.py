"""Resolve favorite strategy .set files under reports/Best and reports/Favorites."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPORTS_PREFIX = "reports/"


def _normalize_slashes(candidate: str) -> str:
    return candidate.replace("\\", "/")


def _is_windows_absolute_path(candidate: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]:/", _normalize_slashes(candidate)))


def _basename(file_path: str) -> str:
    segments = [part for part in _normalize_slashes(file_path).split("/") if part]
    return segments[-1] if segments else file_path


def _extract_reports_segments(candidate: str) -> list[str] | None:
    normalized = _normalize_slashes(candidate)
    reports_index = normalized.lower().find(REPORTS_PREFIX)
    if reports_index == -1:
        return None
    segments = [
        part
        for part in normalized[reports_index + len(REPORTS_PREFIX) :].split("/")
        if part
    ]
    return segments or None


def _repo_reports_path(repo_root: Path, *segments: str) -> Path:
    return repo_root / "reports" / Path(*segments)


def _resolve_windows_absolute_report_path(
    repo_root: Path,
    candidate: str,
    *,
    bucket_dir: Path,
) -> Path:
    segments = _extract_reports_segments(candidate)
    if segments:
        return _repo_reports_path(repo_root, *segments)
    return bucket_dir / "sets" / _basename(candidate)


def _resolve_relative_report_path(
    repo_root: Path,
    candidate: str,
    *,
    bucket_dir: Path,
) -> Path:
    normalized = _normalize_slashes(candidate)
    if normalized.startswith(REPORTS_PREFIX):
        segments = [part for part in normalized[len(REPORTS_PREFIX) :].split("/") if part]
        if segments:
            return _repo_reports_path(repo_root, *segments)
    if "/" not in normalized:
        return bucket_dir / "sets" / normalized
    return (repo_root / normalized).resolve()


def normalize_report_path(
    repo_root: Path,
    candidate: str,
    *,
    bucket_dir: Path,
) -> Path:
    path = Path(candidate)
    if path.is_absolute():
        return path.resolve()
    if _is_windows_absolute_path(candidate):
        return _resolve_windows_absolute_report_path(
            repo_root,
            candidate,
            bucket_dir=bucket_dir,
        )
    return _resolve_relative_report_path(
        repo_root,
        candidate,
        bucket_dir=bucket_dir,
    )


def _has_required_identity_fields(identity: dict[str, Any]) -> bool:
    symbol = str(identity.get("symbol", "")).strip()
    timeframe = str(identity.get("timeframe", "")).strip()
    profile = str(identity.get("profile", "")).strip()
    pass_id = identity.get("passId", identity.get("pass_id"))
    return bool(symbol and timeframe and profile and pass_id is not None)


def _derive_set_stem(identity: dict[str, Any]) -> str | None:
    if not _has_required_identity_fields(identity):
        return None
    symbol = str(identity["symbol"]).strip()
    timeframe = str(identity["timeframe"]).strip()
    profile = str(identity["profile"]).strip()
    pass_id = identity.get("passId", identity.get("pass_id"))
    return f"{symbol}_{timeframe}_{profile}_pass{pass_id}"


def _set_file_candidates(param_file: str | None, summary: dict[str, Any] | None) -> list[str]:
    candidates: list[str] = []
    if param_file and param_file.strip():
        candidates.append(param_file.strip())
    if isinstance(summary, dict):
        set_file = summary.get("set_file")
        if isinstance(set_file, str) and set_file.strip():
            candidates.append(set_file.strip())
    return candidates


def _sets_path(bucket_dir: Path, file_name: str) -> Path:
    return bucket_dir / "sets" / _basename(file_name)


def _find_existing_in_best_dir(
    best_dir: Path,
    candidate: str,
    repo_root: Path,
) -> Path | None:
    resolved = normalize_report_path(repo_root, candidate, bucket_dir=best_dir)
    if resolved.is_file():
        return resolved
    sets_candidate = _sets_path(best_dir, candidate)
    return sets_candidate if sets_candidate.is_file() else None


def _find_bare_filename_in_best_dir(best_dir: Path, candidates: list[str]) -> Path | None:
    bare_name = next(
        (
            candidate
            for candidate in candidates
            if "/" not in candidate and "\\" not in candidate
        ),
        None,
    )
    if not bare_name:
        return None
    sets_candidate = _sets_path(best_dir, bare_name)
    return sets_candidate if sets_candidate.is_file() else None


def _find_derived_stem_in_best_dir(best_dir: Path, identity: dict[str, Any]) -> Path | None:
    stem = _derive_set_stem(identity)
    if not stem:
        return None
    sets_candidate = _sets_path(best_dir, f"{stem}.set")
    return sets_candidate if sets_candidate.is_file() else None


def resolve_set_file_in_best_dir(
    *,
    best_dir: Path,
    repo_root: Path,
    param_file: str | None,
    summary: dict[str, Any] | None,
    identity: dict[str, Any] | None = None,
) -> Path | None:
    identity = identity or {}
    candidates = _set_file_candidates(param_file, summary)
    for candidate in candidates:
        found = _find_existing_in_best_dir(best_dir, candidate, repo_root)
        if found:
            return found
    return _find_bare_filename_in_best_dir(best_dir, candidates) or _find_derived_stem_in_best_dir(
        best_dir,
        identity,
    )


def resolve_favorite_source_set_file(
    *,
    best_dir: Path,
    favorites_dir: Path | None,
    repo_root: Path,
    param_file: str | None,
    summary: dict[str, Any] | None,
    identity: dict[str, Any],
) -> Path | None:
    found = resolve_set_file_in_best_dir(
        best_dir=best_dir,
        repo_root=repo_root,
        param_file=param_file,
        summary=summary,
        identity=identity,
    )
    if found or not favorites_dir:
        return found
    return resolve_set_file_in_best_dir(
        best_dir=favorites_dir,
        repo_root=repo_root,
        param_file=param_file,
        summary=summary,
        identity=identity,
    )


def describe_favorite_identity(identity: dict[str, Any]) -> str:
    symbol = identity.get("symbol")
    timeframe = identity.get("timeframe")
    profile = identity.get("profile")
    pass_id = identity.get("passId", identity.get("pass_id"))
    return f"{symbol} {timeframe} {profile} pass {pass_id}"
