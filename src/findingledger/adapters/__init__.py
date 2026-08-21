"""Adapters — everything that talks to a tool this library deliberately is not.

The stack has four layers, and this package is the wiring between three of them
and the fourth:

===========================  ===========================================
Layer                        Adapter
===========================  ===========================================
1. assertions (in)           :mod:`.promptfoo`, :mod:`.deepeval`,
                             :mod:`.junit`, :mod:`.ragas`,
                             :mod:`findingledger.pytest_plugin`
2. observability (in + out)  :mod:`.langfuse`, :mod:`.otel` (Phoenix)
3. judges                    *(none — judges are invoked by the runners in
                             layer 1, and reach us through their output)*
4. lifecycle (out)           :mod:`.alerts`, :mod:`.github`
===========================  ===========================================

**Inbound** adapters answer *"what failed"* in a foreign dialect and translate
it into the two things this library consumes: a results map for the tri-state
gate, and findings for the ledger. **Outbound** adapters take the lifecycle
state — how many items are open, how many are critical, whether the trend is
degrading — and put it where people already look.

Every adapter is stdlib-only. The optional SDKs (``langfuse``) are imported
lazily inside the function that needs them, so importing this package never
costs a dependency you did not ask for.

Auto-detection means the usual call needs no ``--format``::

    from findingledger import adapters
    results = adapters.results("promptfoo-output.json")      # -> {"GC-07": "FAIL", ...}
    found   = adapters.findings("promptfoo-output.json")     # -> [Finding(...), ...]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Finding
from . import alerts, deepeval, github, junit, langfuse, otel, promptfoo, ragas
from .common import (
    FAIL,
    PASS,
    SKIP,
    ToolResult,
    oneline,
    read_json,
    results_map,
    slugify,
    to_findings,
)

#: Inbound adapters by ``--format`` name. ``json`` is this library's own
#: ``{check_id: "PASS"|"FAIL"}`` map; ``auto`` sniffs the file.
INBOUND = {
    "promptfoo": promptfoo,
    "deepeval": deepeval,
    "junit": junit,
    "ragas": ragas,
}
#: Formats that carry no pass/fail verdict — they only produce finding
#: candidates for review (``spans`` = OTLP/Phoenix, ``traces`` = Langfuse).
MINING = ("spans", "traces")
FORMATS = ("auto", "json", *sorted(INBOUND), *MINING)

__all__ = [
    "FAIL", "FORMATS", "INBOUND", "PASS", "SKIP", "ToolResult", "alerts", "deepeval",
    "detect", "findings", "github", "junit", "langfuse", "oneline", "otel", "promptfoo",
    "ragas", "read_json", "results", "results_map", "rows", "slugify", "to_findings",
]


def detect(path: str | Path, data: Any = None) -> str:
    """Guess the format of a results file from its shape, not its name.

    Names lie (everyone calls it ``results.json``), so only the ``.xml``
    suffix is trusted; the rest is decided by looking for each tool's
    fingerprint inside the document.
    """
    p = Path(path)
    if p.suffix.lower() in (".xml", ".junit"):
        return "junit"
    if data is None:
        try:
            data = read_json(p)
        except (OSError, ValueError):
            return "junit" if p.suffix.lower() == ".xml" else "json"

    if isinstance(data, dict) and "resourceSpans" in data:
        return "spans"
    if isinstance(data, dict) and ("evalId" in data or "evalId" in (data.get("results") or {})):
        return "promptfoo"
    sample = promptfoo.rows(data)
    if sample and any(k in sample[0] for k in ("gradingResult", "testCase", "provider",
                                               "promptId")):
        return "promptfoo"
    de = deepeval.rows(data)
    if de and any(k in de[0] for k in ("metricsData", "metrics_data", "metrics_metadata",
                                       "metricsMetadata")):
        return "deepeval"
    if isinstance(data, dict) and data and all(
            isinstance(v, str) and v.upper() in (PASS, FAIL, SKIP) for v in data.values()):
        return "json"
    if ragas.scores(data):
        return "ragas"
    return "json"


def _native_rows(data: Any) -> list[ToolResult]:
    """This library's own results map, lifted into :class:`ToolResult` rows."""
    if not isinstance(data, dict):
        raise ValueError("a native results file must be an object of {check_id: PASS|FAIL}")
    out = []
    for key, value in data.items():
        outcome = str(value).upper()
        if outcome not in (PASS, FAIL, SKIP):
            raise ValueError(f"results[{key!r}] must be PASS/FAIL/SKIP, got {value!r}")
        out.append(ToolResult(id=str(key), outcome=outcome, source="json"))
    return out


def rows(path: str | Path, fmt: str = "auto", **kwargs) -> list[ToolResult]:
    """Load any supported results file into normalized rows."""
    fmt = fmt or "auto"
    if fmt == "auto":
        fmt = detect(path)
    if fmt in MINING:
        raise ValueError(
            f"format {fmt!r} carries no pass/fail verdict — it produces finding "
            "candidates only; use findings() / `findingledger import`")
    if fmt == "json":
        return _native_rows(read_json(path))
    if fmt == "ragas":
        return ragas.parse(ragas.load(path), kwargs.get("thresholds"))
    if fmt not in INBOUND:
        raise ValueError(f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")
    return INBOUND[fmt].load(path)


def results(path: str | Path, fmt: str = "auto", strict: bool = True,
            **kwargs) -> dict[str, str]:
    """``{check_id: "PASS"|"FAIL"|"SKIP"}`` from any supported results file."""
    return results_map(rows(path, fmt, **kwargs), strict=strict)


def findings(path: str | Path, fmt: str = "auto", date: str | None = None,
             prefix: str = "", **kwargs) -> list[Finding]:
    """Findings for the failures in any supported results file.

    Ragas is special-cased: its findings compare against thresholds and an
    optional stored baseline rather than a pass/fail flag — pass
    ``thresholds=``, ``baseline=`` and ``drop=``.
    """
    fmt = fmt or "auto"
    if fmt == "auto":
        fmt = detect(path)
    if fmt == "ragas":
        return ragas.findings(ragas.load(path), kwargs.get("thresholds"),
                              kwargs.get("baseline"), date,
                              kwargs.get("drop", ragas.DEFAULT_DROP),
                              kwargs.get("baseline_date", ""))
    if fmt == "spans":
        return otel.findings_from_spans(
            otel.load(path), date=date, latency_over=kwargs.get("latency_over"),
            tokens_over=kwargs.get("tokens_over"), errors=kwargs.get("errors", True),
            min_hits=kwargs.get("min_hits", 1))
    if fmt == "traces":
        data = read_json(path)
        traces = data if isinstance(data, list) else (data.get("data") or data.get("traces") or [])
        return langfuse.findings_from_traces(
            traces, date=date, cost_over=kwargs.get("cost_over"),
            latency_over=kwargs.get("latency_over"), score_below=kwargs.get("score_below"),
            min_hits=kwargs.get("min_hits", 1), host_url=kwargs.get("host_url", ""))
    return to_findings(rows(path, fmt), date=date, prefix=prefix)
