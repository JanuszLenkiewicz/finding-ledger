"""The integration CLI: import, results, check --format/--github, alert, scores.

Exit codes are part of the contract here — an unattended pipeline reads them,
not the prose. The load-bearing one: `check` exits 0 when an `open` case fails
and 1 when a `regression` case does.
"""

import json

import pytest

from findingledger.cli import main

PROMPTFOO = {"evalId": "e1", "results": {"results": [
    {"success": False, "vars": {"case_id": "GC-07"},
     "testCase": {"description": "cites the source",
                  "metadata": {"signature": "B1-detail-beyond-source",
                               "severity": "critical"}},
     "gradingResult": {"pass": False, "reason": "no citation"}},
    {"success": True, "vars": {"case_id": "GC-01"}, "testCase": {"description": "sanity"}},
]}}

LEDGER = """# Backlog

## Open

### [B1-detail-beyond-source] OPEN — Mentor invents a figure
- **Occurrences:** 2× (2026-08-05, 2026-08-01)
"""


@pytest.fixture
def project(tmp_path):
    """A complete consumer project: config, ledger, cases, audits, registry."""
    root = tmp_path / "demo"
    (root / "cases").mkdir(parents=True)
    (root / "audits").mkdir()
    (root / "findingledger.yaml").write_text(
        "name: demo\nledger: backlog.md\ncases: cases\naudits: audits\n"
        "alerts:\n  channel: file\n  path: alerts.log\n  min_severity: critical\n",
        encoding="utf-8")
    (root / "backlog.md").write_text(LEDGER, encoding="utf-8")
    (root / "cases" / "gc-07.yaml").write_text(
        "id: GC-07\nstatus: open\ncheck: GC-07\nbacklog: B1-detail-beyond-source\n",
        encoding="utf-8")
    (root / "cases" / "gc-01.yaml").write_text(
        "id: GC-01\nstatus: regression\ncheck: GC-01\n", encoding="utf-8")
    (root / "audits" / "2026-08-08-audit.md").write_text(
        "---\ndate: 2026-08-08\nn_findings: 3\nby_severity: {critical: 1, major: 2}\n---\n",
        encoding="utf-8")
    (tmp_path / "promptfoo.json").write_text(json.dumps(PROMPTFOO), encoding="utf-8")
    (tmp_path / "registry.yaml").write_text(f"projects:\n  - {root}\n", encoding="utf-8")
    return root


def test_check_passes_a_failing_open_case(project, capsys):
    code = main(["check", "--cases", str(project / "cases"),
                 "--results", str(project.parent / "promptfoo.json")])
    out = capsys.readouterr().out
    assert code == 0
    assert "GC-07" in out and "status-quo" in out


def test_check_alarms_when_a_regression_case_fails(project, capsys):
    results = project.parent / "flip.json"
    results.write_text(json.dumps({"GC-01": "FAIL"}), encoding="utf-8")
    code = main(["check", "--cases", str(project / "cases"), "--results", str(results)])
    assert code == 1
    assert "ALARM" in capsys.readouterr().err


def test_check_resolves_cases_from_the_project(project, capsys):
    code = main(["check", "--project", "demo", "--registry",
                 str(project.parent / "registry.yaml"),
                 "--results", str(project.parent / "promptfoo.json"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["format"] == "promptfoo"
    assert payload["has_alarms"] is False


def test_check_without_cases_or_project_is_a_usage_error(project, capsys):
    assert main(["check", "--results", str(project.parent / "promptfoo.json")]) == 2
    assert "--cases" in capsys.readouterr().err


def test_check_github_mode_writes_summary_and_outputs(project, tmp_path, monkeypatch,
                                                      capsys):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    main(["check", "--cases", str(project / "cases"),
          "--results", str(project.parent / "promptfoo.json"), "--github"])
    capsys.readouterr()
    assert "finding-ledger" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "has_alarms=false" in (tmp_path / "out.txt").read_text(encoding="utf-8")


def test_results_converts_any_runner_output(project, tmp_path, capsys):
    out = tmp_path / "results.json"
    code = main(["results", "-i", str(project.parent / "promptfoo.json"), "-o", str(out)])
    assert code == 0 and "promptfoo" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["GC-07"] == "FAIL"


def test_import_dry_run_writes_nothing(project, capsys):
    before = (project / "backlog.md").read_text(encoding="utf-8")
    code = main(["import", "-i", str(project.parent / "promptfoo.json"),
                 "--ledger", str(project / "backlog.md"), "--dry-run"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["dry_run"] is True
    assert (project / "backlog.md").read_text(encoding="utf-8") == before


def test_import_bumps_the_counter_of_an_existing_signature(project, capsys):
    main(["import", "-i", str(project.parent / "promptfoo.json"),
          "--ledger", str(project / "backlog.md"), "--date", "2026-08-08"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] == ["B1-detail-beyond-source"]
    assert payload["escalation_due"] == ["B1-detail-beyond-source"]
    ledger = (project / "backlog.md").read_text(encoding="utf-8")
    assert "3× (2026-08-08, 2026-08-05, 2026-08-01)" in ledger


def test_import_without_a_target_refuses_silently_to_write(project, capsys):
    code = main(["import", "-i", str(project.parent / "promptfoo.json")])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and "nothing was written" in payload["note"]


def test_import_ragas_with_baseline_and_save(project, tmp_path, capsys):
    scores = tmp_path / "ragas.json"
    scores.write_text(json.dumps({"context_recall": 0.6}), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"date": "2026-08-01",
                                    "scores": {"context_recall": 0.85}}), encoding="utf-8")
    code = main(["import", "-i", str(scores), "--format", "ragas", "--baseline",
                 str(baseline), "--save-baseline", "--date", "2026-08-08",
                 "--ledger", str(project / "backlog.md")])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["created"] == ["ragas-context-recall-regression"]
    assert json.loads(baseline.read_text(encoding="utf-8"))["scores"]["context_recall"] == 0.6


def test_a_dry_run_never_moves_the_ragas_baseline(project, tmp_path, capsys):
    """Moving it silently would hide the very regression the next run looks for."""
    scores = tmp_path / "ragas.json"
    scores.write_text(json.dumps({"context_recall": 0.6}), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"scores": {"context_recall": 0.85}}), encoding="utf-8")
    main(["import", "-i", str(scores), "--format", "ragas", "--baseline", str(baseline),
          "--save-baseline", "--dry-run"])
    capsys.readouterr()
    assert json.loads(baseline.read_text(encoding="utf-8"))["scores"]["context_recall"] == 0.85


def test_alert_fires_on_a_critical_audit_and_writes_the_channel(project, capsys):
    code = main(["alert", "--project", "demo", "--registry",
                 str(project.parent / "registry.yaml"),
                 "--audit", str(project / "audits" / "2026-08-08-audit.md"), "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert code == 0 and receipt["alerted"] is True and receipt["sent"] is True
    assert receipt["channel"] == "file", "channel came from the project config"
    assert "critical" in (project / "alerts.log").read_text(encoding="utf-8").lower()


def test_alert_stays_quiet_below_the_threshold(project, capsys, tmp_path):
    quiet = tmp_path / "quiet-audit.md"
    quiet.write_text("---\ndate: 2026-08-09\nn_findings: 2\nby_severity: {major: 2}\n---\n",
                     encoding="utf-8")
    code = main(["alert", "--audit", str(quiet), "--name", "demo"])
    assert code == 0 and "no alert" in capsys.readouterr().out


def test_alert_can_fail_the_step_for_chained_cron(project, capsys):
    code = main(["alert", "--audit", str(project / "audits" / "2026-08-08-audit.md"),
                 "--name", "demo", "--dry-run", "--fail-on-alert"])
    capsys.readouterr()
    assert code == 1


def test_alert_needs_a_source(capsys):
    assert main(["alert"]) == 2
    assert "--audit" in capsys.readouterr().err


def test_scores_render_as_json_without_any_sdk(project, capsys):
    code = main(["scores", "--project", "demo", "--registry",
                 str(project.parent / "registry.yaml"), "--target", "json"])
    payloads = json.loads(capsys.readouterr().out)
    assert code == 0
    names = {p["name"] for p in payloads}
    assert "findingledger.open_items" in names and "findingledger.verdict" in names


def test_scores_dry_run_never_touches_the_network(project, capsys):
    code = main(["scores", "--project", "demo", "--registry",
                 str(project.parent / "registry.yaml"), "--dry-run"])
    assert code == 0 and json.loads(capsys.readouterr().out)
