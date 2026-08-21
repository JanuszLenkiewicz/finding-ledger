from pathlib import Path

from findingledger.report import build_report, load_project, load_registry

LEDGER = """## Open

### [B1-detail-beyond-source] 🔴 CRITICAL — detail beyond source
- **Occurrences:** 3× (2026-08-08, 2026-08-07, 2026-08-06)

### [LEN-01-length-drift] OPEN — length drift
- **Occurrences:** 26× (2026-08-08)

## Fixed

### [FAK-01-university] ✅ FIXED — false fact
- **Occurrences:** 1× (2026-07-15)
"""

AUDIT = """---
date: 2026-08-08
n_findings: 5
by_severity: {critical: 2, major: 2, minor: 1}
by_class: {A: 2, B: 2, C: 1}
---
# audit
"""


def make_project(root: Path, name: str) -> Path:
    root.mkdir(parents=True)
    (root / "findingledger.yaml").write_text(
        f"name: {name}\nledger: backlog.md\ncases: cases\naudits: audits\n", encoding="utf-8")
    (root / "backlog.md").write_text(LEDGER, encoding="utf-8")
    (root / "cases").mkdir()
    (root / "cases" / "ok.yaml").write_text(
        "id: len-01\nstatus: open\ncheck: LEN-01\n", encoding="utf-8")
    # foreign schema (promptfoo-style) — only the tri-state status is shared
    (root / "cases" / "foreign.yaml").write_text(
        "id: d2-outcome\nstatus: regression\nvars: {x: 1}\nasserts: [{type: contains}]\n",
        encoding="utf-8")
    (root / "audits").mkdir()
    (root / "audits" / "2026-08-08-audit.md").write_text(AUDIT, encoding="utf-8")
    return root


def test_load_project_counts(tmp_path: Path):
    p = load_project(make_project(tmp_path / "alpha", "alpha"))
    assert p.name == "alpha" and not p.error
    assert p.n_open == 2 and p.n_closed == 1 and p.n_critical == 1
    # LEN-01 has 26 occurrences and is neither critical nor closed -> candidate
    assert [i.signature for i in p.escalation_candidates] == ["LEN-01-length-drift"]
    # tolerant loader survives the foreign promptfoo-style case
    assert dict(p.cases) == {"len-01": "open", "d2-outcome": "regression"}
    assert len(p.audits) == 1 and p.audits[0].by_severity["critical"] == 2


def test_multi_project_report(tmp_path: Path):
    a = make_project(tmp_path / "alpha", "alpha")
    b = make_project(tmp_path / "beta", "beta")
    out = tmp_path / "report.html"
    projects = build_report([a, b], out, generated="2026-08-08 12:00")
    assert [p.name for p in projects] == ["alpha", "beta"]
    html_text = out.read_text(encoding="utf-8")
    assert html_text.startswith("<!doctype html>")
    for token in ("alpha", "beta", "LEN-01-length-drift", "26×",
                  "Escalation candidates", "2026-08-08 12:00"):
        assert token in html_text
    # no external assets — self-contained page
    assert "http://" not in html_text and "https://" not in html_text


def test_single_audit_project_shows_no_trend_claim(tmp_path: Path):
    a = make_project(tmp_path / "alpha", "alpha")
    out = tmp_path / "r.html"
    build_report([a], out)
    text = out.read_text(encoding="utf-8")
    assert "no trend yet" in text
    assert "improving" not in text and "worsening" not in text


def test_missing_project_reports_error_not_crash(tmp_path: Path):
    p = load_project(tmp_path / "ghost")
    assert p.error and "cannot read" in p.error


def test_registry(tmp_path: Path):
    reg = tmp_path / "projects.yaml"
    reg.write_text("projects:\n  - /tmp/a\n  - ~/b\n", encoding="utf-8")
    paths = load_registry(reg)
    assert paths[0] == Path("/tmp/a") and paths[1].is_absolute()
