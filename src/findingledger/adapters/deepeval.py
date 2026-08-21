"""DeepEval adapter — the pytest-shaped assertion runner.

DeepEval is the Python-native equivalent of promptfoo: metrics such as
faithfulness and answer relevancy, plus a natural-language-criterion judge
(G-Eval), driven from pytest. Two ways in:

1. **Its JSON test-run file** (``deepeval test run ... -j`` / the file
   ``.deepeval/latest_test_run.json``, whose exact name moves between
   versions)::

       findingledger check --cases eval/cases --results test_run.json --format deepeval

2. **Straight from pytest**, with no file at all — see
   :mod:`findingledger.pytest_plugin`. Because this library is a plain package
   with a dict-returning service layer, a session hook can file failures with
   one call. That is the shortest integration available.

Key spellings differ across DeepEval releases (``testCases`` vs ``test_cases``
vs ``test_results``, ``metricsData`` vs ``metrics_data`` vs
``metrics_metadata``); all of them are accepted, because pinning a library to
one release of another library is how integrations rot.

Metric-level ids (``GC-07::Faithfulness``) are registered as aliases, so a
golden case can guard *one metric* of a test rather than the whole test.

Carry the mapping in the test-case metadata to make it exact::

    LLMTestCase(..., additional_metadata={"case_id": "GC-07",
                                          "signature": "B1-detail-beyond-source"})
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    DEFAULT_CLASS_KEYS,
    DEFAULT_ID_KEYS,
    DEFAULT_SEVERITY_KEYS,
    DEFAULT_SIGNATURE_KEYS,
    FAIL,
    PASS,
    ToolResult,
    first_of,
    oneline,
    read_json,
)

SOURCE = "deepeval"

_CASE_KEYS = ("testCases", "test_cases", "test_results", "testResults", "results")
_METRIC_KEYS = ("metricsData", "metrics_data", "metrics_metadata", "metricsMetadata",
                "metrics")
_META_KEYS = ("additionalMetadata", "additional_metadata", "metadata")


def rows(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    found = first_of(data, _CASE_KEYS)
    if isinstance(found, list):
        return [r for r in found if isinstance(r, dict)]
    return []


def _metrics(row: dict) -> list[dict]:
    found = first_of(row, _METRIC_KEYS)
    return [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []


def _success(row: dict, metrics: list[dict]) -> bool:
    if isinstance(row.get("success"), bool):
        return row["success"]
    if metrics:
        return all(m.get("success", True) for m in metrics)
    return True


def _reason(metrics: list[dict]) -> str:
    parts = []
    for m in metrics:
        if m.get("success") is False:
            score, threshold = m.get("score"), m.get("threshold")
            head = f"{m.get('name', 'metric')} {score}"
            if threshold is not None:
                head += f" < {threshold}"
            parts.append(f"{head}: {m.get('reason', '')}".strip())
    return oneline(" / ".join(parts))


def parse(data: Any, id_keys=DEFAULT_ID_KEYS) -> list[ToolResult]:
    """Normalize a DeepEval test-run document into :class:`ToolResult` rows."""
    out: list[ToolResult] = []
    for row in rows(data):
        meta = first_of(row, _META_KEYS)
        meta = meta if isinstance(meta, dict) else {}
        metrics = _metrics(row)
        name = str(row.get("name") or row.get("testName") or "deepeval-case")
        case_id = str(first_of(meta, id_keys) or first_of(row, id_keys) or name)
        signature = first_of(meta, DEFAULT_SIGNATURE_KEYS) or first_of(row,
                                                                      DEFAULT_SIGNATURE_KEYS)
        ok = _success(row, metrics)
        aliases = [name]
        aliases += [f"{case_id}::{m.get('name')}" for m in metrics if m.get("name")]
        out.append(ToolResult(
            id=case_id,
            outcome=PASS if ok else FAIL,
            aliases=[a for a in aliases if a],
            reason=_reason(metrics),
            title=name,
            signature=str(signature) if signature else None,
            severity=str(first_of(meta, DEFAULT_SEVERITY_KEYS) or "major"),
            klass=str(first_of(meta, DEFAULT_CLASS_KEYS) or ""),
            source=SOURCE,
            meta={"metrics": [m.get("name") for m in metrics]},
        ))
        # Metric-level rows: a case file may guard one metric of a test.
        for m in metrics:
            metric_name = m.get("name")
            if not metric_name:
                continue
            passed = m.get("success", True)
            out.append(ToolResult(
                id=f"{case_id}::{metric_name}",
                outcome=PASS if passed else FAIL,
                reason=oneline(m.get("reason", "")),
                title=f"{name} — {metric_name}",
                source=SOURCE,
                kind="metric",
                meta={"score": m.get("score"), "threshold": m.get("threshold")},
            ))
    return out


def load(path: str | Path, id_keys=DEFAULT_ID_KEYS) -> list[ToolResult]:
    return parse(read_json(path), id_keys)
