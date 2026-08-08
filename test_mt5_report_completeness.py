"""Tests for forward-report completeness helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from mt5_report_completeness import (
    back_xml_path,
    base_report_path,
    delete_incomplete_forward_reports,
    incomplete_forward_reports,
)


def test_base_and_back_xml_paths(tmp_path: Path) -> None:
    forward = tmp_path / "job.forward.xml"
    back = tmp_path / "job.xml"
    assert base_report_path(forward) == str(tmp_path / "job")
    assert base_report_path(back) == str(tmp_path / "job")
    assert back_xml_path(forward) == back
    assert back_xml_path(back) == back


def test_incomplete_false_when_forward_mode_off(tmp_path: Path) -> None:
    base = tmp_path / "job"
    (tmp_path / "job.xml").write_text("x", encoding="utf-8")
    assert incomplete_forward_reports(str(base), forward_mode="0") is False


def test_incomplete_when_forward_xml_missing(tmp_path: Path) -> None:
    base = tmp_path / "job"
    (tmp_path / "job.xml").write_text("x", encoding="utf-8")
    assert incomplete_forward_reports(str(base), forward_mode="1") is True


def test_delete_incomplete_removes_partial_artifacts(tmp_path: Path) -> None:
    base = str(tmp_path / "job")
    xml = tmp_path / "job.xml"
    htm = tmp_path / "job.htm"
    xml.write_text("x", encoding="utf-8")
    htm.write_text("y", encoding="utf-8")
    deleted = delete_incomplete_forward_reports(base, forward_mode="1")
    assert {p.name for p in deleted} == {"job.xml", "job.htm"}
    assert not xml.exists()
    assert not htm.exists()


def test_delete_incomplete_ignores_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = str(tmp_path / "job")
    xml = tmp_path / "job.xml"
    htm = tmp_path / "job.htm"
    xml.write_text("x", encoding="utf-8")
    htm.write_text("y", encoding="utf-8")

    original_unlink = Path.unlink

    def flaky_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith(".xml"):
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    deleted = delete_incomplete_forward_reports(base, forward_mode="1")
    assert {p.name for p in deleted} == {"job.htm"}
    assert xml.exists()
    assert not htm.exists()
    # Caller must re-check before re-opt: residual incomplete artifacts remain.
    assert incomplete_forward_reports(base, forward_mode="1") is True


def test_delete_incomplete_clears_when_unlocked(tmp_path: Path) -> None:
    base = str(tmp_path / "job")
    (tmp_path / "job.xml").write_text("x", encoding="utf-8")
    (tmp_path / "job.htm").write_text("y", encoding="utf-8")
    deleted = delete_incomplete_forward_reports(base, forward_mode="1")
    assert {p.name for p in deleted} == {"job.xml", "job.htm"}
    assert incomplete_forward_reports(base, forward_mode="1") is False
