"""Every adapter, end to end, on synthetic data — no API keys, no network.

Run: python examples/integrations_tour.py

The tour follows one defect through the whole stack: promptfoo finds it, the
ledger remembers it, a golden case guards it, Ragas catches a retrieval
regression, production spans suggest a new candidate, an alert fires (only for
the critical one), and the lifecycle state leaves as Langfuse scores.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from findingledger import Ledger, adapters, evaluate, load_cases
from findingledger.adapters import alerts, github, langfuse, ragas

PROMPTFOO_OUTPUT = {
    "evalId": "eval-2026-08-08",
    "results": {"version": 3, "results": [
        {"provider": {"id": "openai:gpt-4o-mini"}, "success": False,
         "vars": {"case_id": "GC-07"},
         "testCase": {"description": "mentor cites the source figure",
                      "metadata": {"signature": "B1-detail-beyond-source",
                                   "severity": "critical"}},
         "gradingResult": {"pass": False, "componentResults": [
             {"pass": False, "reason": "expected '3.2%', got 'about 3%'",
              "assertion": {"type": "contains"}}]}},
        {"provider": {"id": "openai:gpt-4o-mini"}, "success": True,
         "vars": {"case_id": "SAN-01"},
         "testCase": {"description": "four required sections"}},
    ], "stats": {"successes": 1, "failures": 1, "tokenUsage": {"total": 4210}}},
}

SPANS = {"resourceSpans": [{"scopeSpans": [{"spans": [
    {"name": "retrieve", "spanId": "a1", "status": {"code": 2, "message": "index timeout"}},
    {"name": "retrieve", "spanId": "a2", "status": {"code": 2, "message": "index timeout"}},
]}]}]}

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "cases").mkdir()
    (root / "cases" / "gc-07.yaml").write_text(
        "id: GC-07\nstatus: open\ncheck: GC-07\nbacklog: B1-detail-beyond-source\n",
        encoding="utf-8")
    (root / "cases" / "san-01.yaml").write_text(
        "id: SAN-01\nstatus: sanity\ncheck: SAN-01\n", encoding="utf-8")
    (root / "promptfoo.json").write_text(json.dumps(PROMPTFOO_OUTPUT), encoding="utf-8")
    (root / "spans.json").write_text(json.dumps(SPANS), encoding="utf-8")
    (root / "ragas.json").write_text(
        json.dumps({"faithfulness": 0.90, "context_recall": 0.61}), encoding="utf-8")
    (root / "baseline.json").write_text(
        json.dumps({"date": "2026-08-01",
                    "scores": {"faithfulness": 0.91, "context_recall": 0.86}}),
        encoding="utf-8")

    # 1. promptfoo → the tri-state gate. The `open` case is red, and that is fine.
    print("== 1. promptfoo → gate ==")
    results = adapters.results(root / "promptfoo.json")
    outcomes = evaluate(load_cases(root / "cases"), results)
    for o in outcomes:
        print(f"  {o.case.id:8s} [{o.case.status:10s}] {o.result:4s} -> {o.action}")
    print("  exit code would be:", 1 if any(o.action == "alarm" for o in outcomes) else 0)

    # 2. promptfoo → the ledger. The signature comes from the test's metadata,
    #    so it names the mechanism instead of the symptom.
    print("\n== 2. promptfoo → ledger ==")
    ledger = Ledger(root / "backlog.md")
    found = adapters.findings(root / "promptfoo.json", date="2026-08-08")
    report = ledger.merge(found)
    ledger.save()
    print("  created:", report.created)

    # 3. Ragas → a retrieval regression, measured against a stored baseline.
    print("\n== 3. ragas → ledger ==")
    rag = ragas.findings(ragas.load(root / "ragas.json"),
                         baseline=ragas.load_baseline(root / "baseline.json"),
                         date="2026-08-08", baseline_date="2026-08-01")
    report = ledger.merge(rag)
    ledger.save()
    for f in rag:
        print(f"  {f.signature} [{f.severity}] — {f.title}")

    # 4. Production spans → triage candidates a human renames later.
    print("\n== 4. otel spans → candidates ==")
    for f in adapters.findings(root / "spans.json", date="2026-08-08"):
        print(f"  {f.signature} — {f.symptom}")

    # 5. Alerts: only the critical one, and by deterministic code.
    print("\n== 5. alert (critical only) ==")
    alert = alerts.from_findings(found + rag, project="demo", min_severity="critical")
    print(alerts.render(alert) if alert else "  nothing critical — stay quiet")
    print("  receipt:", alerts.dispatch(alert, channel="none") if alert else "—")

    # 6. Lifecycle state → observability scores (built offline, no SDK needed).
    print("\n== 6. ledger → langfuse scores ==")
    status = {"name": "demo", "open_items": len(ledger.open_items()), "critical_open": 1,
              "escalation_candidates": [], "cases": [{"id": c.id, "status": c.status}
                                                     for c in load_cases(root / "cases")],
              "trend": {}}
    for payload in langfuse.payloads_from_status(status)[:4]:
        print(f"  {payload['name']} = {payload['value']}")

    # 7. The same verdict, rendered for a GitHub job summary.
    print("\n== 7. github job summary ==")
    checked = {"outcomes": [{"id": o.case.id, "status": o.case.status, "result": o.result,
                             "action": o.action, "backlog": o.case.backlog}
                            for o in outcomes],
               "alarms": [], "graduation_candidates": [], "has_alarms": False}
    print(github.summary_markdown(checked, status, "demo"))

    print("--- ledger file ---")
    print(ledger.path.read_text(encoding="utf-8"))
