"""Optimization report completeness helpers (forward vs back artifacts)."""

from __future__ import annotations

from pathlib import Path

REPORT_ARTIFACT_SUFFIXES = (".xml", ".htm", ".forward.xml", ".forward.htm")


def base_report_path(xml_path: Path) -> str:
    """Stem path for tester artifacts (strip ``.forward.xml`` or ``.xml``)."""
    name = xml_path.name
    if name.endswith(".forward.xml"):
        return str(xml_path.with_name(name[: -len(".forward.xml")]))
    if name.endswith(".xml"):
        return str(xml_path)[: -len(".xml")]
    return str(xml_path)


def back_xml_path(xml_path: Path) -> Path:
    """Normalize a listed xml path to the back (non-forward) ``.xml``."""
    name = xml_path.name
    if name.endswith(".forward.xml"):
        return xml_path.with_name(name[: -len(".forward.xml")] + ".xml")
    return xml_path


def incomplete_forward_reports(
    report_path: str,
    *,
    forward_mode: str,
) -> bool:
    """True when back .xml exists but required .forward.xml is missing."""
    if forward_mode == "0":
        return False
    report_xml = Path(report_path + ".xml")
    report_forward_xml = Path(report_path + ".forward.xml")
    return report_xml.is_file() and not report_forward_xml.is_file()


def delete_incomplete_forward_reports(
    report_path: str,
    *,
    forward_mode: str,
) -> list[Path]:
    """Remove partial opt artifacts so MT5 re-runs cleanly. Returns deleted paths."""
    if not incomplete_forward_reports(report_path, forward_mode=forward_mode):
        return []
    deleted: list[Path] = []
    for suffix in REPORT_ARTIFACT_SUFFIXES:
        path = Path(report_path + suffix)
        if path.is_file():
            try:
                path.unlink()
            except PermissionError:
                continue
            deleted.append(path)
    return deleted
