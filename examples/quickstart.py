"""End-to-end tour of the finding lifecycle on a throwaway ledger.

Run: python examples/quickstart.py
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from findingledger import Finding, Ledger

with TemporaryDirectory() as tmp:
    ledger = Ledger(Path(tmp) / "backlog.md")

    # Day 1: an audit reports a defect — it lands as a new ledger item.
    ledger.merge([Finding(
        signature="B1-detail-beyond-source",
        date="2026-08-08",
        title="Detail added under a real citation",
        severity="major", klass="B1",
        symptom="Paraphrase rounds a source list up to a category.",
        evidence='Issue says "heels"; the cited article lists none.',
        root_cause="Generative rounding to the popular notion.",
        fix_direction="Prompt rule: only facts you can point at in the source.",
        verification="cases/b1-source-fidelity.yaml",
    )])

    # Day 2 and 3: same root cause again — counter bumps, no duplicate item.
    for day in ("2026-08-09", "2026-08-10"):
        report = ledger.merge([Finding(signature="B1-detail-beyond-source", date=day)])
    print("escalation due:", report.escalation_due)   # 3rd occurrence hits the threshold

    # The fix lands — the item closes with a note; history stays in the file.
    ledger.mark_fixed("B1-detail-beyond-source", note="prompt rule added, commit abc123")
    ledger.save()

    print("\n--- ledger file ---")
    print(ledger.path.read_text(encoding="utf-8"))
