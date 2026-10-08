"""Regression tests for MT5 HTML report metric extraction."""

from __future__ import annotations

from pathlib import Path

from mt5_db_report import extract_full_report_metrics


def test_extract_company_when_inputs_row_has_empty_bold(tmp_path: Path) -> None:
    """MT5 Settings: Inputs has empty <b></b>, Company is the next row."""
    report = tmp_path / "report.htm"
    report.write_text(
        """
<html><body><table>
<tr align="right">
  <td nowrap colspan="3">Inputs:</td>
  <td nowrap colspan="10" align="left"><b></b></td>
</tr>
<tr align="right">
  <td nowrap colspan="3" >Company:</td>
  <td nowrap colspan="10" align="left"><b>Pepperstone EU Limited</b></td>
</tr>
<tr align="right">
  <td nowrap colspan="3" >Currency:</td>
  <td nowrap colspan="10" align="left"><b>EUR</b></td>
</tr>
</table></body></html>
""".strip(),
        encoding="utf-8",
    )
    metrics = extract_full_report_metrics(report)["metrics"]
    assert metrics.get("Company") == "Pepperstone EU Limited"
    assert metrics.get("Currency") == "EUR"
    assert metrics.get("Inputs") is None
