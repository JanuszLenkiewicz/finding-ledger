"""Generate the showcase demo report from synthetic English data.

Run: python examples/demo_report.py  -> docs/demo-report.html

The demo exists so the public repo can show what a report looks like without
publishing anyone's real ledger — consumer data stays in consumer projects.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from findingledger.report import build_report

LEDGER = """# Mentor backlog — cumulative

## Open

### [B1-detail-beyond-source] 🔴 CRITICAL — detail added under a real citation
- **Class:** B1 | **Severity:** critical
- **Occurrences:** 3× (2026-08-08, 2026-08-04, 2026-07-27)
- **Symptom:** paraphrase rounds a source list up to a category the source never names.
- **Fix direction:** prompt rule — only facts you can point at in the cited source.
- **Verification:** cases/b1-source-fidelity.yaml

### [LEN-01-length-drift] OPEN — issues drift far over the word limit
- **Class:** A | **Severity:** major
- **Occurrences:** 26× (2026-08-08, 2026-08-07, 2026-08-06)
- **Symptom:** limit is prose, not a gate; median 1400 words vs a 700-1000 spec.

### [PWT-01-person-repeated] OPEN — anti-repetition rule broken systematically
- **Class:** C | **Severity:** major
- **Occurrences:** 7× (2026-08-07, 2026-08-04, 2026-08-01)
- **Symptom:** dedup based on model attention instead of a data registry.

## Fixed

### [ALT-01-alert-step-skipped] FIXED — LLM auditor skipped its alert step
- **Fixed:** moved enforcement to the deterministic wrapper layer
- **Occurrences:** 1× (2026-08-08)
"""

CASES = {
    "b1-source-fidelity.yaml":
        "id: b1-source-fidelity\nstatus: open\nbacklog: B1-detail-beyond-source\n"
        "rubric: |\n  Every detail must be pointable-at in the cited source.\n",
    "len-01-limit.yaml":
        "id: len-01-limit\nstatus: open\ncheck: LEN-01\nbacklog: LEN-01-length-drift\n",
    "fak-01-guardrail.yaml":
        "id: fak-01-guardrail\nstatus: regression\ncheck: FAK-01\n",
    "structure.yaml":
        "id: structure\nstatus: sanity\ncheck: STR-01\n",
}

AUDITS = [
    ("2026-08-02", 6, 2, 3, 1), ("2026-08-03", 5, 1, 3, 1), ("2026-08-04", 5, 2, 2, 1),
    ("2026-08-05", 4, 1, 2, 1), ("2026-08-06", 3, 1, 1, 1), ("2026-08-07", 3, 0, 2, 1),
    ("2026-08-08", 2, 0, 1, 1),
]

AUDIT_TPL = """---
date: {d}
n_findings: {n}
by_severity: {{critical: {c}, major: {m}, minor: {mi}}}
by_class: {{A: 1, B: {c}, C: 1, D: 0}}
---
# Audit {d}
"""


def make_demo_project(root: Path, name: str, description: str) -> Path:
    root.mkdir(parents=True)
    (root / "findingledger.yaml").write_text(
        f"name: {name}\ndescription: {description}\n"
        "ledger: backlog.md\ncases: cases\naudits: audits\n", encoding="utf-8")
    (root / "backlog.md").write_text(LEDGER, encoding="utf-8")
    (root / "cases").mkdir()
    for fname, body in CASES.items():
        (root / "cases" / fname).write_text(body, encoding="utf-8")
    (root / "audits").mkdir()
    for d, n, c, m, mi in AUDITS:
        (root / "audits" / f"{d}-audit.md").write_text(
            AUDIT_TPL.format(d=d, n=n, c=c, m=m, mi=mi), encoding="utf-8")
    return root


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "docs" / "demo-report.html"
    with TemporaryDirectory() as tmp:
        a = make_demo_project(Path(tmp) / "newsletter-pipeline", "newsletter-pipeline",
                              "Unattended daily newsletter with an LLM mentor")
        b = make_demo_project(Path(tmp) / "trading-mentor", "trading-mentor",
                              "Trading-mentor platform with a two-layer AI mentor")
        build_report([a, b], out, generated="demo data — synthetic")
    print(f"demo report: {out}")
