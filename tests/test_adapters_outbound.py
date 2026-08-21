"""Outbound adapters: alerts, GitHub Actions, Langfuse scores, OTLP spans.

The rules under test are the ones that were learned the hard way in
production: alert only on critical, never let a notification failure kill the
audit that produced it, and never send a step's success on a model's word.
"""

import json

import pytest

from findingledger.adapters import alerts, github, langfuse, otel
from findingledger.models import AuditSummary, Finding

# ── alerts ─────────────────────────────────────────────────────────────────


def audit(**kw):
    base = {"date": "2026-08-08", "path": "audits/2026-08-08-audit.md", "n_findings": 3,
            "by_severity": {"critical": 1, "major": 2}}
    base.update(kw)
    return AuditSummary(**base)


def test_alert_only_fires_for_critical_by_default():
    assert alerts.from_audit(audit(), "demo") is not None
    quiet = audit(by_severity={"major": 5, "minor": 2}, n_findings=7)
    assert alerts.from_audit(quiet, "demo") is None, "majors must wait in the ledger"
    assert alerts.from_audit(quiet, "demo", min_severity="major") is not None


def test_alert_from_audit_carries_the_evidence_path():
    alert = alerts.from_audit(audit(), "demo")
    assert alert.level == "critical"
    assert "audits/2026-08-08-audit.md" in alerts.render(alert)
    assert alert.counts == {"critical": 1, "major": 2}


def test_alert_from_findings_accepts_objects_and_dicts():
    findings = [Finding(signature="A-1", date="2026-08-08", severity="critical"),
                {"signature": "B-2", "date": "2026-08-08", "severity": "minor"}]
    alert = alerts.from_findings(findings, "demo")
    assert alert.level == "critical"
    assert "A-1" in alerts.render(alert) and "B-2" not in alerts.render(alert)


def test_failing_regression_case_always_alerts():
    """A regression case failing means the defect came back — critical by definition."""
    alert = alerts.from_check({"alarms": [{"id": "GC-07", "status": "regression",
                                           "backlog": "B1-sig"}]}, "demo")
    assert alert.level == "critical" and "GC-07" in alerts.render(alert)
    assert alerts.from_check({"alarms": []}, "demo") is None


def test_dispatch_to_file_appends(tmp_path):
    target = tmp_path / "logs" / "alerts.log"
    alert = alerts.from_audit(audit(), "demo")
    for _ in range(2):
        receipt = alerts.dispatch(alert, channel="file", path=str(target))
    assert receipt["sent"] is True
    assert target.read_text(encoding="utf-8").count("[CRITICAL]") == 2


def test_dispatch_by_command_passes_the_message_on_stdin(tmp_path):
    out = tmp_path / "received.txt"
    receipt = alerts.dispatch(alerts.from_audit(audit(), "demo"), channel="command",
                              command=f"sh -c 'cat > {out}'")
    assert receipt["sent"] is True
    assert "critical" in out.read_text(encoding="utf-8").lower()


def test_a_failing_channel_never_raises():
    """The finding is already safe in the ledger; a dead webhook must not kill the run."""
    receipt = alerts.dispatch(alerts.from_audit(audit(), "demo"), channel="webhook",
                              url="ftp://nope")
    assert receipt["sent"] is False and "webhook url must be http" in receipt["error"]
    unknown = alerts.dispatch(alerts.from_audit(audit(), "demo"), channel="carrier-pigeon")
    assert unknown["sent"] is False and "unknown channel" in unknown["error"]


def test_dry_run_renders_but_sends_nothing(capsys):
    receipt = alerts.dispatch(alerts.from_audit(audit(), "demo"), dry_run=True)
    assert receipt["dry_run"] is True and receipt["sent"] is False
    assert capsys.readouterr().out == ""


# ── GitHub ─────────────────────────────────────────────────────────────────

CHECK = {"outcomes": [{"id": "GC-07", "status": "regression", "result": "FAIL",
                       "action": "alarm", "backlog": "B1-sig", "path": "eval/gc-07.yaml"},
                      {"id": "GC-11", "status": "open", "result": "PASS",
                       "action": "graduation-candidate", "backlog": None},
                      {"id": "SAN-01", "status": "sanity", "result": "PASS",
                       "action": "ok", "backlog": None}],
         "alarms": [{"id": "GC-07", "status": "regression", "backlog": "B1-sig",
                     "path": "eval/gc-07.yaml"}],
         "graduation_candidates": [{"id": "GC-11"}], "has_alarms": True}


def test_annotations_are_errors_for_alarms_and_notices_for_graduations():
    lines = github.annotation_lines(CHECK)
    assert lines[0].startswith("::error file=eval/gc-07.yaml,title=")
    assert "GC-07" in lines[0] and "%0A" not in lines[0]
    assert lines[1].startswith("::notice ") and "graduate" in lines[1]


def test_annotation_escaping_protects_the_workflow_protocol():
    lines = github.annotation_lines({"alarms": [{"id": "GC-1", "status": "regression",
                                            "backlog": "a\nb: c,d"}]})
    assert "\n" not in lines[0] and "%0A" in lines[0]


def test_summary_explains_why_a_red_open_case_is_fine():
    text = github.summary_markdown({"outcomes": CHECK["outcomes"], "alarms": [],
                                    "graduation_candidates": [], "has_alarms": False},
                                   project="demo")
    assert "🟢 **PASS**" in text and "written before the fix" in text


def test_emit_writes_summary_and_outputs(tmp_path, capsys):
    env = {"GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
           "GITHUB_OUTPUT": str(tmp_path / "out.txt")}
    status = {"open_items": 4, "critical_open": 1, "escalation_candidates":
              [{"signature": "B1-sig", "count": 3, "title": "recurring"}],
              "audits": 2, "last_audit": "2026-08-08"}
    result = github.emit(CHECK, status, "demo", env=env)
    assert "::error" in capsys.readouterr().out
    assert "🔴 **ALARM**" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    outputs = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "has_alarms=true" in outputs and "open_items=4" in outputs
    assert result["outputs"]["alarms"] == "1"


def test_emit_outside_actions_only_prints(capsys):
    result = github.emit(CHECK, None, "demo", env={})
    assert result["summary_file"] is None
    assert "::error" in capsys.readouterr().out


def test_multiline_output_uses_the_heredoc_form(tmp_path):
    env = {"GITHUB_OUTPUT": str(tmp_path / "out.txt")}
    github.set_output("body", "line1\nline2", env=env)
    written = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert written.startswith("body<<FINDINGLEDGER_EOF") and "line2" in written


# ── Langfuse ───────────────────────────────────────────────────────────────

STATUS = {"name": "demo", "open_items": 6, "critical_open": 2,
          "escalation_candidates": [{"signature": "B1-sig"}],
          "cases": [{"id": "GC-1", "status": "regression"}, {"id": "GC-2", "status": "open"}],
          "audits": 9, "last_audit": "2026-08-08",
          "trend": {"has_baseline": True, "degrading": False,
                    "delta": {"critical": -3, "major": 1, "minor": 0}}}


def test_score_payloads_cover_counts_and_the_verdict():
    payloads = {p["name"]: p["value"] for p in langfuse.payloads_from_status(STATUS)}
    assert payloads["findingledger.open_items"] == 6
    assert payloads["findingledger.critical_open"] == 2
    assert payloads["findingledger.escalation_due"] == 1
    assert payloads["findingledger.cases_open"] == 1
    assert payloads["findingledger.delta_critical"] == -3
    assert payloads["findingledger.verdict"] == "improving"


def test_verdict_says_no_baseline_rather_than_guessing():
    status = dict(STATUS, trend={"has_baseline": False, "delta": {}})
    payloads = {p["name"]: p["value"] for p in langfuse.payloads_from_status(status)}
    assert payloads["findingledger.verdict"] == "no-baseline"
    assert "findingledger.delta_critical" not in payloads


def test_audit_payloads_carry_severity_breakdown():
    summary = AuditSummary(date="2026-08-08", path="a.md", n_findings=3,
                           by_severity={"critical": 1, "major": 2})
    payloads = {p["name"]: p["value"] for p in langfuse.payloads_from_audit(summary)}
    assert payloads["findingledger.audit_findings"] == 3
    assert payloads["findingledger.audit_critical"] == 1
    assert payloads["findingledger.audit_minor"] == 0


class FakeLangfuse:
    def __init__(self):
        self.sent, self.flushed = [], False

    def create_score(self, **kwargs):
        self.sent.append(kwargs)

    def flush(self):
        self.flushed = True


def test_push_scores_uses_the_injected_client():
    client = FakeLangfuse()
    result = langfuse.push_scores(langfuse.payloads_from_status(STATUS),
                                  trace_id="tr-1", client=client)
    assert result["pushed"] == len(client.sent) and client.flushed
    assert client.sent[0]["trace_id"] == "tr-1"
    assert client.sent[0]["data_type"] == "NUMERIC"


def test_push_scores_refuses_to_send_into_the_void():
    with pytest.raises(ValueError, match="trace_id or session_id"):
        langfuse.push_scores([{"name": "n", "value": 1}], client=FakeLangfuse())


def test_dry_run_needs_neither_target_nor_sdk():
    result = langfuse.push_scores([{"name": "n", "value": 1}], dry_run=True)
    assert result["dry_run"] and result["pushed"] == 0 and result["payloads"]


TRACES = [
    {"id": "t1", "name": "newsletter", "totalCost": 0.9, "latency": 30,
     "scores": [{"name": "quality", "value": 0.2}]},
    {"id": "t2", "name": "newsletter", "totalCost": 0.8, "scores": {"quality": 0.95}},
    {"id": "t3", "name": "search", "totalCost": 0.01},
]


def test_traces_group_into_candidates_per_operation():
    found = langfuse.findings_from_traces(TRACES, date="2026-08-08", cost_over=0.5,
                                          score_below=("quality", 0.5),
                                          host_url="https://cloud.langfuse.com")
    signatures = [f.signature for f in found]
    assert "trace-cost-newsletter" in signatures
    assert "trace-score-quality-newsletter" in signatures
    assert "trace-cost-search" not in signatures          # under the bound
    cost = next(f for f in found if f.signature == "trace-cost-newsletter")
    assert "https://cloud.langfuse.com/trace/t1" in cost.evidence
    assert "unknown" in cost.root_cause, "a candidate must not pretend to be a diagnosis"


def test_min_hits_keeps_one_off_outliers_out_of_the_ledger():
    assert langfuse.findings_from_traces(TRACES, cost_over=0.5, min_hits=3) == []


# ── OTLP / Phoenix spans ───────────────────────────────────────────────────

OTLP = {"resourceSpans": [{"scopeSpans": [{"spans": [
    {"name": "retrieve", "spanId": "a1", "status": {"code": 2, "message": "index timeout"},
     "startTimeUnixNano": "0", "endTimeUnixNano": "3000000000",
     "attributes": [{"key": "llm.token_count.total", "value": {"intValue": "9000"}}]},
    {"name": "retrieve", "spanId": "a2", "status": {"code": 2, "message": "index timeout"}},
    {"name": "generate", "spanId": "b1", "status": {"code": 1},
     "startTimeUnixNano": "0", "endTimeUnixNano": "100000"},
]}]}]}


def test_otlp_flattening_hoists_attributes_and_computes_latency():
    span = otel.flatten_otlp(OTLP)[0]
    assert span["status_code"] == "ERROR"
    assert span["latency_ms"] == 3000.0
    assert span["llm.token_count.total"] == 9000


def test_span_findings_group_by_name_and_rank_errors_critical():
    found = {f.signature: f for f in otel.findings_from_spans(
        otel.flatten_otlp(OTLP), date="2026-08-08", latency_over=1000, tokens_over=5000)}
    assert found["span-error-retrieve"].severity == "critical"
    assert "2 span(s)" in found["span-error-retrieve"].title
    assert "span-latency-retrieve" in found and "span-tokens-retrieve" in found
    assert "span-latency-generate" not in found


def test_phoenix_flat_records_need_no_conversion():
    records = [{"name": "llm", "status_code": "ERROR", "span_id": "s1"}]
    assert otel.normalize(records) == records
    assert otel.normalize({"spans": records}) == records
    assert [f.signature for f in otel.findings_from_spans(records)] == ["span-error-llm"]


def test_span_loading_from_file(tmp_path):
    path = tmp_path / "spans.json"
    path.write_text(json.dumps(OTLP), encoding="utf-8")
    assert len(otel.load(path)) == 3
