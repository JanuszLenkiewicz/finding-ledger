"""Inbound adapters: promptfoo, DeepEval, JUnit, Ragas, and the auto-detector.

Every fixture here is deliberately *irregular* — old envelopes, missing keys,
foreign spellings, multiline failure text — because that is what real output
files look like once a tool has shipped a few versions.
"""

import json

import pytest

from findingledger import adapters
from findingledger.adapters import deepeval, junit, promptfoo, ragas
from findingledger.adapters.common import ToolResult, results_map, to_findings

# ── promptfoo ──────────────────────────────────────────────────────────────

MODERN = {
    "evalId": "eval-1",
    "results": {"version": 3, "results": [
        {"provider": {"id": "openai:gpt-4"}, "success": False, "vars": {"case_id": "GC-07"},
         "testCase": {"description": "cites the source",
                      "metadata": {"signature": "B1-detail-beyond-source",
                                   "severity": "critical", "class": "B1"}},
         "gradingResult": {"pass": False, "componentResults": [
             {"pass": False, "reason": "missing 3.2%\nsecond line",
              "assertion": {"type": "contains"}}]}},
        {"provider": {"id": "openai:gpt-4"}, "success": True, "vars": {"case_id": "SAN-01"},
         "testCase": {"description": "sections present"}},
    ], "stats": {"successes": 1, "failures": 1, "tokenUsage": {"total": 99}}},
}

LEGACY = {"results": [
    {"provider": "openai:gpt-3.5", "gradingResult": {"pass": False, "reason": "tone"},
     "vars": {"id": "GC-02"}, "description": "old schema"},
]}


def test_promptfoo_modern_envelope():
    rows = promptfoo.parse(MODERN)
    assert [(r.id, r.outcome) for r in rows] == [("GC-07", "FAIL"), ("SAN-01", "PASS")]
    assert rows[0].signature == "B1-detail-beyond-source"
    assert rows[0].severity == "critical" and rows[0].klass == "B1"
    # multiline failure text is flattened — a newline would split a ledger bullet
    assert "\n" not in rows[0].reason and "missing 3.2%" in rows[0].reason


def test_promptfoo_legacy_envelope_and_bare_list():
    assert [r.id for r in promptfoo.parse(LEGACY)] == ["GC-02"]
    assert [r.id for r in promptfoo.parse(LEGACY["results"])] == ["GC-02"]


def test_promptfoo_stats_fall_back_to_counting_rows():
    assert promptfoo.stats(MODERN)["failures"] == 1
    assert promptfoo.stats(LEGACY)["failures"] == 1  # no stats block in the file


def test_promptfoo_description_is_the_id_when_nothing_else_is():
    rows = promptfoo.parse([{"success": False, "description": "no ids anywhere"}])
    assert rows[0].id == "no ids anywhere"


def test_multi_provider_case_fails_if_any_provider_fails():
    rows = [ToolResult(id="GC-1", outcome="PASS"), ToolResult(id="GC-1", outcome="FAIL")]
    assert results_map(rows)["GC-1"] == "FAIL"
    assert results_map(rows, strict=False)["GC-1"] == "PASS"


def test_alias_never_overwrites_a_primary_id():
    rows = [ToolResult(id="GC-1", outcome="PASS", aliases=["shared"]),
            ToolResult(id="shared", outcome="FAIL")]
    assert results_map(rows)["shared"] == "FAIL"


# ── JUnit ──────────────────────────────────────────────────────────────────

JUNIT_XML = """<testsuites><testsuite name="pytest">
<testcase classname="tests.test_mentor" name="test_cites">
  <properties>
    <property name="findingledger_case" value="GC-07"/>
    <property name="findingledger_signature" value="B1-detail-beyond-source"/>
    <property name="findingledger_severity" value="critical"/>
  </properties>
  <failure message="assert '3.2%' in answer">traceback</failure>
</testcase>
<testcase classname="tests.test_mentor" name="test_tone"/>
<testcase classname="tests.test_other" name="test_skipped"><skipped/></testcase>
<testcase classname="tests.test_other" name="test_errored"><error message="boom"/></testcase>
</testsuite></testsuites>"""


def test_junit_maps_failures_errors_and_skips():
    rows = {r.id: r.outcome for r in junit.parse(JUNIT_XML)}
    assert rows == {"GC-07": "FAIL", "test_tone": "PASS", "test_skipped": "SKIP",
                    "test_errored": "FAIL"}


def test_junit_properties_carry_signature_and_severity():
    row = junit.parse(JUNIT_XML)[0]
    assert row.signature == "B1-detail-beyond-source" and row.severity == "critical"


def test_junit_does_not_alias_the_bare_classname():
    """Otherwise one red test would mark every test in the module FAIL."""
    assert "tests.test_mentor" not in results_map(junit.parse(JUNIT_XML))
    assert results_map(junit.parse(JUNIT_XML))["tests.test_mentor.test_cites"] == "FAIL"


# ── DeepEval ───────────────────────────────────────────────────────────────

DEEPEVAL_CAMEL = {"testCases": [
    {"name": "test_faithful", "success": False,
     "additionalMetadata": {"case_id": "GC-09", "signature": "HALL-01"},
     "metricsData": [{"name": "Faithfulness", "score": 0.4, "threshold": 0.7,
                      "success": False, "reason": "invented a figure"},
                     {"name": "Relevancy", "score": 0.9, "success": True}]},
]}
DEEPEVAL_SNAKE = {"test_results": [
    {"name": "test_relevant", "metrics_data": [{"name": "Relevancy", "success": True}]}]}


@pytest.mark.parametrize("doc,expected", [(DEEPEVAL_CAMEL, "FAIL"), (DEEPEVAL_SNAKE, "PASS")])
def test_deepeval_accepts_both_key_spellings(doc, expected):
    rows = deepeval.parse(doc)
    assert rows[0].outcome == expected


def test_deepeval_metric_rows_are_addressable_but_not_filed():
    rows = deepeval.parse(DEEPEVAL_CAMEL)
    results = results_map(rows)
    assert results["GC-09"] == "FAIL"
    assert results["GC-09::Faithfulness"] == "FAIL"
    assert results["GC-09::Relevancy"] == "PASS"
    # one finding for the case, not one per failing metric
    found = to_findings(rows, date="2026-08-08")
    assert [f.signature for f in found] == ["HALL-01"]


def test_deepeval_success_is_inferred_from_metrics_when_absent():
    doc = {"testCases": [{"name": "t", "metricsData": [{"name": "M", "success": False,
                                                        "reason": "bad"}]}]}
    assert deepeval.parse(doc)[0].outcome == "FAIL"


# ── Ragas ──────────────────────────────────────────────────────────────────

def test_ragas_accepts_aggregate_records_and_wrapper():
    assert ragas.scores({"faithfulness": 0.9})["faithfulness"] == 0.9
    assert ragas.scores({"scores": {"faithfulness": 0.9}})["faithfulness"] == 0.9
    averaged = ragas.scores([{"faithfulness": 1.0, "question": "text"},
                             {"faithfulness": 0.0}])
    assert averaged == {"faithfulness": 0.5}   # non-numeric columns ignored


def test_ragas_below_threshold_and_regression_are_different_findings():
    values = {"context_recall": 0.60, "faithfulness": 0.50}
    found = {f.signature: f for f in ragas.findings(
        values, baseline={"context_recall": 0.85}, date="2026-08-08")}
    assert "ragas-context-recall-regression" in found       # fell against the baseline
    assert "ragas-faithfulness-below-threshold" in found    # no baseline, under the bar
    assert found["ragas-context-recall-regression"].severity == "critical"
    assert found["ragas-faithfulness-below-threshold"].verification == "ragas:faithfulness"


def test_ragas_within_threshold_is_silent():
    assert ragas.findings({"faithfulness": 0.99}, baseline={"faithfulness": 0.99}) == []


def test_ragas_parse_feeds_sanity_cases():
    rows = {r.id: r.outcome for r in ragas.parse({"context_recall": 0.6, "unknown": 1.0})}
    assert rows == {"ragas:context_recall": "FAIL"}   # unknown metric has no bar


def test_ragas_baseline_round_trip(tmp_path):
    path = tmp_path / "nested" / "baseline.json"
    ragas.save_baseline(path, {"faithfulness": 0.912345678}, date="2026-08-08")
    assert ragas.load_baseline(path)["faithfulness"] == pytest.approx(0.912346)
    assert ragas.load_baseline(tmp_path / "missing.json") == {}


# ── the dispatcher ─────────────────────────────────────────────────────────

def _write(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                 encoding="utf-8")
    return p


def test_detect_recognizes_every_format(tmp_path):
    assert adapters.detect(_write(tmp_path, "a.json", MODERN)) == "promptfoo"
    assert adapters.detect(_write(tmp_path, "b.json", LEGACY)) == "promptfoo"
    assert adapters.detect(_write(tmp_path, "c.json", DEEPEVAL_CAMEL)) == "deepeval"
    assert adapters.detect(_write(tmp_path, "d.xml", JUNIT_XML)) == "junit"
    assert adapters.detect(_write(tmp_path, "e.json", {"GC-1": "PASS"})) == "json"
    assert adapters.detect(_write(tmp_path, "f.json", {"faithfulness": 0.9})) == "ragas"
    assert adapters.detect(_write(tmp_path, "g.json", {"resourceSpans": []})) == "spans"


def test_results_and_findings_go_through_auto_detection(tmp_path):
    path = _write(tmp_path, "promptfoo.json", MODERN)
    assert adapters.results(path)["GC-07"] == "FAIL"
    found = adapters.findings(path, date="2026-08-08")
    assert [f.signature for f in found] == ["B1-detail-beyond-source"]
    assert found[0].verification == "GC-07"   # points back at the golden case


def test_jsonl_output_is_read_line_by_line(tmp_path):
    path = tmp_path / "out.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in LEGACY["results"]) + "\n",
                    encoding="utf-8")
    results = adapters.results(path)
    assert results["GC-02"] == "FAIL"
    assert results["old schema"] == "FAIL"                  # description alias
    assert results["GC-02::openai:gpt-3.5"] == "FAIL"       # per-provider alias


def test_native_results_map_round_trips(tmp_path):
    path = _write(tmp_path, "r.json", {"GC-1": "PASS", "GC-2": "fail"})
    assert adapters.results(path) == {"GC-1": "PASS", "GC-2": "FAIL"}
    with pytest.raises(ValueError, match="PASS/FAIL/SKIP"):
        adapters.results(_write(tmp_path, "bad.json", {"GC-1": "maybe"}), "json")


def test_mining_formats_refuse_to_pretend_they_have_verdicts(tmp_path):
    path = _write(tmp_path, "spans.json", {"resourceSpans": []})
    with pytest.raises(ValueError, match="no pass/fail verdict"):
        adapters.results(path)


def test_unknown_format_names_the_alternatives(tmp_path):
    with pytest.raises(ValueError, match="unknown format"):
        adapters.rows(_write(tmp_path, "x.json", {}), "nope")


def test_derived_signature_is_a_slug_and_evidence_is_single_line():
    rows = [ToolResult(id="GC 07: mentor's answer", outcome="FAIL",
                       reason="line one\nline two | pipe")]
    finding = to_findings(rows, date="2026-08-08", prefix="promptfoo-")[0]
    assert finding.signature == "promptfoo-gc-07-mentor-s-answer"   # no spaces: valid
    assert "\n" not in finding.evidence and "|" not in finding.evidence


def test_duplicate_signatures_in_one_run_collapse_with_merged_evidence():
    rows = [ToolResult(id="a", outcome="FAIL", signature="SIG-1", reason="first"),
            ToolResult(id="b", outcome="FAIL", signature="SIG-1", reason="second")]
    found = to_findings(rows, date="2026-08-08")
    assert len(found) == 1
    assert "first" in found[0].evidence and "second" in found[0].evidence
