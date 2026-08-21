"""Service-layer tests — the logic behind the MCP tools, exercised without the
optional ``mcp`` dependency installed."""

from pathlib import Path

import pytest

from findingledger import service
from findingledger.service import ProjectNotFound

LEDGER = """## Open

### [B1-detail] 🔴 CRITICAL — detail beyond source
- **Occurrences:** 3× (2026-08-08, 2026-08-07, 2026-08-06)
- Hand-written note that must survive.

### [LEN-01-drift] OPEN — length drift
- **Occurrences:** 2× (2026-08-08, 2026-08-07)

## Fixed

### [FAK-01-fact] ✅ FIXED — false fact
- **Occurrences:** 1× (2026-07-15)
"""

AUDIT = """---
date: {d}
n_findings: {n}
by_severity: {{critical: {c}, major: 1, minor: 0}}
---
# audit
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "alpha"
    (root / "cases").mkdir(parents=True)
    (root / "audits").mkdir()
    (root / "findingledger.yaml").write_text(
        "name: alpha\ndescription: demo\nledger: backlog.md\ncases: cases\naudits: audits\n",
        encoding="utf-8")
    (root / "backlog.md").write_text(LEDGER, encoding="utf-8")
    (root / "cases" / "len.yaml").write_text(
        "id: len-01\nstatus: open\ncheck: LEN-01\nbacklog: LEN-01-drift\n", encoding="utf-8")
    (root / "cases" / "fak.yaml").write_text(
        "id: fak-01\nstatus: regression\ncheck: FAK-01\n", encoding="utf-8")
    for d, n, c in (("2026-08-06", 6, 3), ("2026-08-07", 4, 1), ("2026-08-08", 2, 0)):
        (root / "audits" / f"{d}-audit.md").write_text(
            AUDIT.format(d=d, n=n, c=c), encoding="utf-8")
    return root


@pytest.fixture
def registry(tmp_path: Path, project: Path) -> str:
    reg = tmp_path / "projects.yaml"
    reg.write_text(f"projects:\n  - {project}\n", encoding="utf-8")
    return str(reg)


def test_resolve_by_name_and_by_path(project: Path, registry: str):
    assert service.resolve_project("alpha", registry) == project
    assert service.resolve_project(str(project)) == project
    with pytest.raises(ProjectNotFound):
        service.resolve_project("ghost", registry)


def test_list_and_status(registry: str):
    listed = service.list_projects(registry)
    assert listed[0]["name"] == "alpha" and listed[0]["open_items"] == 2

    st = service.project_status("alpha", registry)
    assert st["open_items"] == 2 and st["critical_open"] == 1 and st["closed_items"] == 1
    # LEN-01 has 2 occurrences: below the default threshold of 3
    assert st["escalation_candidates"] == []
    assert st["last_audit"] == "2026-08-08"
    assert {c["id"] for c in st["cases"]} == {"len-01", "fak-01"}


def test_ledger_items_filters(registry: str):
    assert [i["signature"] for i in service.ledger_items("alpha", registry=registry)] == [
        "B1-detail", "LEN-01-drift"]                      # open, most-recurring first
    assert [i["signature"] for i in
            service.ledger_items("alpha", status="closed", registry=registry)] == ["FAK-01-fact"]
    assert [i["signature"] for i in
            service.ledger_items("alpha", status="critical", registry=registry)] == ["B1-detail"]
    assert len(service.ledger_items("alpha", status="all", registry=registry)) == 3
    assert [i["signature"] for i in
            service.ledger_items("alpha", min_count=3, registry=registry)] == ["B1-detail"]


def test_merge_dedup_and_escalation(project: Path, registry: str):
    same_day = service.merge_findings(
        "alpha", [{"signature": "LEN-01-drift", "date": "2026-08-08"}], registry)
    assert same_day["unchanged"] == ["LEN-01-drift"] and same_day["written"] is False

    bump = service.merge_findings(
        "alpha", [{"signature": "LEN-01-drift", "date": "2026-08-09"}], registry)
    assert bump["updated"] == ["LEN-01-drift"]
    assert bump["escalation_due"] == ["LEN-01-drift"]     # 3rd occurrence hits threshold

    created = service.merge_findings("alpha", [{
        "signature": "TON-01-tone", "date": "2026-08-09", "title": "Guilt tone",
        "severity": "minor", "class": "D2", "evidence": "quote + path"}], registry)
    assert created["created"] == ["TON-01-tone"]

    text = (project / "backlog.md").read_text(encoding="utf-8")
    assert "Hand-written note that must survive." in text
    assert "TON-01-tone" in text


def test_fixed_and_retract_preserve_history(project: Path, registry: str):
    service.mark_fixed("alpha", "LEN-01-drift", note="commit abc", registry=registry)
    service.retract_finding("alpha", "B1-detail", note="deliberate", registry=registry)
    text = (project / "backlog.md").read_text(encoding="utf-8")
    assert "### [LEN-01-drift] FIXED —" in text and "commit abc" in text
    assert "### [B1-detail] RETRACTED —" in text
    assert "3× (2026-08-08, 2026-08-07, 2026-08-06)" in text     # history kept
    assert service.ledger_items("alpha", registry=registry) == []


def test_check_cases_tristate(registry: str):
    res = service.check_cases("alpha", {"LEN-01": "FAIL", "FAK-01": "FAIL"}, registry)
    by_id = {r["id"]: r for r in res["outcomes"]}
    assert by_id["len-01"]["action"] == "status-quo"      # open + FAIL = expected
    assert by_id["fak-01"]["action"] == "alarm"           # regression + FAIL = defect back
    assert res["has_alarms"] is True
    assert [a["id"] for a in res["alarms"]] == ["fak-01"]

    passing = service.check_cases("alpha", {"LEN-01": "PASS", "FAK-01": "PASS"}, registry)
    assert [c["id"] for c in passing["graduation_candidates"]] == ["len-01"]
    assert passing["has_alarms"] is False


def test_graduate_case(project: Path, registry: str):
    out = service.graduate_case("alpha", "len-01", registry)
    assert out["status"] == "regression"
    assert "status: regression" in (project / "cases" / "len.yaml").read_text(encoding="utf-8")


def test_audit_trend_improving(registry: str):
    t = service.audit_trend("alpha", window=1, registry=registry)
    assert t["delta"]["critical"] == -1 and t["degrading"] is False
    assert [a["date"] for a in t["audits"]] == ["2026-08-08"]


def test_make_report(tmp_path: Path, registry: str):
    out = tmp_path / "r.html"
    res = service.make_report(str(out), registry=registry)
    assert res["projects"][0]["name"] == "alpha"
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


# ── integrations (v0.2) ────────────────────────────────────────────────────

PROMPTFOO = """{"evalId": "e1", "results": {"results": [
 {"success": false, "vars": {"case_id": "LEN-01"},
  "testCase": {"description": "answer stays under 200 words",
               "metadata": {"signature": "LEN-01-drift"}},
  "gradingResult": {"pass": false, "reason": "312 words"}},
 {"success": true, "vars": {"case_id": "FAK-01"}, "testCase": {"description": "no fake facts"}}
]}}"""


def test_project_paths_resolves_everything_relative_to_the_config(project: Path,
                                                                 registry: str):
    paths = service.project_paths("alpha", registry)
    assert Path(paths["ledger"]) == project / "backlog.md"
    assert Path(paths["cases"]) == project / "cases"
    assert Path(paths["audits"]) == project / "audits"
    assert paths["name"] == "alpha"


def test_check_results_file_reads_a_foreign_runner(tmp_path: Path, registry: str):
    results = tmp_path / "promptfoo.json"
    results.write_text(PROMPTFOO, encoding="utf-8")
    out = service.check_results_file("alpha", str(results), registry=registry)
    assert out["format"] == "promptfoo"
    assert out["has_alarms"] is False              # the failing case is `open`
    actions = {row["id"]: row["action"] for row in out["outcomes"]}
    assert actions["len-01"] == "status-quo"
    assert actions["fak-01"] == "ok"


def test_import_tool_output_dry_run_writes_nothing(tmp_path: Path, project: Path,
                                                   registry: str):
    results = tmp_path / "promptfoo.json"
    results.write_text(PROMPTFOO, encoding="utf-8")
    before = (project / "backlog.md").read_text(encoding="utf-8")
    out = service.import_tool_output("alpha", str(results), date="2026-08-09",
                                     dry_run=True, registry=registry)
    assert out["findings"][0]["signature"] == "LEN-01-drift"
    assert (project / "backlog.md").read_text(encoding="utf-8") == before


def test_import_tool_output_merges_by_signature(tmp_path: Path, project: Path,
                                                registry: str):
    results = tmp_path / "promptfoo.json"
    results.write_text(PROMPTFOO, encoding="utf-8")
    out = service.import_tool_output("alpha", str(results), date="2026-08-09",
                                     dry_run=False, registry=registry)
    assert out["updated"] == ["LEN-01-drift"] and out["escalation_due"] == ["LEN-01-drift"]
    assert "3× (2026-08-09," in (project / "backlog.md").read_text(encoding="utf-8")


def test_alert_defaults_come_from_the_project_config(project: Path, registry: str):
    (project / "findingledger.yaml").write_text(
        "name: alpha\nledger: backlog.md\ncases: cases\naudits: audits\n"
        "alerts:\n  channel: file\n  path: alerts.log\n", encoding="utf-8")
    receipt = service.alert("alpha", audit=str(project / "audits" / "2026-08-06-audit.md"),
                            registry=registry)
    assert receipt["alerted"] and receipt["channel"] == "file"
    # relative path resolved against the project, not the current directory
    assert (project / "alerts.log").exists()


def test_alert_from_the_ledger_when_no_source_is_given(project: Path, registry: str):
    receipt = service.alert("alpha", dry_run=True, registry=registry)
    assert receipt["alerted"] and receipt["source"] == "ledger"
    assert "B1-detail" in receipt["message"]


def test_alert_is_silent_when_nothing_is_critical(project: Path, registry: str):
    audit = project / "audits" / "2026-08-08-audit.md"   # 0 critical
    receipt = service.alert("alpha", audit=str(audit), dry_run=True, registry=registry)
    assert receipt["alerted"] is False


def test_score_payloads_describe_the_whole_lifecycle(registry: str):
    payloads = {p["name"]: p["value"] for p in service.score_payloads("alpha", registry)}
    assert payloads["findingledger.open_items"] == 2
    assert payloads["findingledger.critical_open"] == 1
    assert payloads["findingledger.cases_regression"] == 1
    assert payloads["findingledger.verdict"] in ("improving", "degrading", "no-baseline")


def test_push_scores_dry_run_needs_no_credentials(registry: str):
    out = service.push_scores("alpha", dry_run=True, registry=registry)
    assert out["dry_run"] is True and out["project"] == "alpha" and out["payloads"]
