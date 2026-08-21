"""promptfoo adapter — the closest neighbour in the stack.

promptfoo answers *"what failed"*; this library answers *"and what that means
for the project"*. promptfoo has no notion of a case that is **allowed** to
fail — to it a red test is a red test — so the tri-state meaning is added from
the outside, here.

Produce the input with either::

    promptfoo eval -o results.json
    promptfoo eval -o results.jsonl        # also accepted

Then::

    findingledger check --cases eval/cases --results results.json --format promptfoo
    findingledger import --input results.json --ledger bugfix/backlog.md

Every schema promptfoo has shipped is accepted: the modern
``{"evalId": ..., "results": {"results": [...]}}`` envelope, the older
``{"results": [...]}`` one, and a bare list of rows (JSON Lines).

**How to wire the ids.** Give each test case a stable identifier in
``promptfooconfig.yaml`` and the mapping to golden cases becomes exact::

    tests:
      - description: mentor cites the source figure
        vars: { case_id: GC-07 }
        metadata: { signature: B1-detail-beyond-source, severity: critical }
        assert:
          - type: contains
            value: "3.2%"

``metadata.signature`` is the valuable part: it names the *mechanism*, so a
failure lands on the right ledger item instead of creating a new one.
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
    SKIP,
    ToolResult,
    first_of,
    oneline,
    read_json,
)

SOURCE = "promptfoo"


def rows(data: Any) -> list[dict]:
    """Unwrap whichever envelope this promptfoo version used."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    inner = data.get("results")
    if isinstance(inner, dict):
        return [r for r in (inner.get("results") or []) if isinstance(r, dict)]
    if isinstance(inner, list):
        return [r for r in inner if isinstance(r, dict)]
    return []


def stats(data: Any) -> dict:
    """Headline numbers of the run (successes/failures/errors, tokens, cost).

    Useful as observability payload — see :mod:`findingledger.adapters.langfuse`.
    """
    envelope = data.get("results") if isinstance(data, dict) else None
    raw = envelope.get("stats") if isinstance(envelope, dict) else None
    if not isinstance(raw, dict) and isinstance(data, dict):
        raw = data.get("stats")
    # float, not int: "cost" below is a fractional dollar amount sharing this dict.
    out: dict[str, float] = {"successes": 0, "failures": 0, "errors": 0}
    if isinstance(raw, dict):
        for key in ("successes", "failures", "errors"):
            out[key] = int(raw.get(key) or 0)
        tokens = raw.get("tokenUsage")
        if isinstance(tokens, dict):
            out["tokens"] = int(tokens.get("total") or 0)
        if raw.get("cost") is not None:
            out["cost"] = float(raw["cost"])
    if not any(out.values()):  # older files: count the rows instead
        parsed = parse(data)
        out["successes"] = sum(1 for r in parsed if r.outcome == PASS)
        out["failures"] = sum(1 for r in parsed if r.outcome == FAIL)
    out["cases"] = out["successes"] + out["failures"] + out["errors"]
    return out


def _provider(row: dict) -> str:
    p = row.get("provider")
    if isinstance(p, dict):
        return str(p.get("id") or p.get("label") or "")
    return str(p or "")


def _outcome(row: dict) -> str:
    grading = row.get("gradingResult")
    if row.get("error"):
        return FAIL
    if isinstance(row.get("success"), bool):
        return PASS if row["success"] else FAIL
    if isinstance(grading, dict) and isinstance(grading.get("pass"), bool):
        return PASS if grading["pass"] else FAIL
    return SKIP


def _reason(row: dict) -> str:
    grading_raw = row.get("gradingResult")
    # Narrowed via a local instead of re-testing `row.get(...)` inline: mypy
    # can't trust a second call to return the same value, so the ternary would
    # otherwise keep `None` in the inferred type of `grading`.
    grading: dict = grading_raw if isinstance(grading_raw, dict) else {}
    parts: list[str] = []
    for comp in grading.get("componentResults") or []:
        if isinstance(comp, dict) and comp.get("pass") is False:
            assertion = comp.get("assertion") or {}
            kind = assertion.get("type", "assert") if isinstance(assertion, dict) else "assert"
            parts.append(f"[{kind}] {comp.get('reason', '')}".strip())
    if not parts and grading.get("reason"):
        parts.append(str(grading["reason"]))
    if not parts and row.get("error"):
        parts.append(f"error: {row['error']}")
    return oneline(" / ".join(parts))


def _lookup(row: dict, keys) -> Any:
    """Search a row's metadata/vars/testCase for one of ``keys``."""
    test_case_raw = row.get("testCase")
    # See _reason() above for why this is a local instead of an inline ternary.
    test_case: dict = test_case_raw if isinstance(test_case_raw, dict) else {}
    for holder in (row.get("metadata"), test_case.get("metadata"),
                   row.get("vars"), test_case.get("vars"), test_case, row):
        found = first_of(holder, keys)
        if found is not None:
            return found
    return None


def parse(data: Any, id_keys=DEFAULT_ID_KEYS) -> list[ToolResult]:
    """Normalize a promptfoo output document into :class:`ToolResult` rows."""
    out: list[ToolResult] = []
    for row in rows(data):
        test_case_raw = row.get("testCase")
        # See _reason() above for why this is a local instead of an inline ternary.
        test_case: dict = test_case_raw if isinstance(test_case_raw, dict) else {}
        description = str(test_case.get("description") or row.get("description") or "")
        case_id = _lookup(row, id_keys)
        case_id = str(case_id) if case_id is not None else (description or "promptfoo-case")
        provider = _provider(row)
        severity = _lookup(row, DEFAULT_SEVERITY_KEYS)
        klass = _lookup(row, DEFAULT_CLASS_KEYS)
        signature = _lookup(row, DEFAULT_SIGNATURE_KEYS)
        aliases = [description, f"{case_id}::{provider}" if provider else ""]
        out.append(ToolResult(
            id=case_id,
            outcome=_outcome(row),
            aliases=[a for a in aliases if a],
            reason=_reason(row),
            title=description or case_id,
            signature=str(signature) if signature else None,
            severity=str(severity or "major"),
            klass=str(klass or ""),
            source=SOURCE,
            meta={"provider": provider, "score": row.get("score")},
        ))
    return out


def load(path: str | Path, id_keys=DEFAULT_ID_KEYS) -> list[ToolResult]:
    """Read a promptfoo JSON/JSONL output file and normalize it."""
    return parse(read_json(path), id_keys)
