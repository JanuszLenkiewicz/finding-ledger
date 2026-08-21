"""OpenTelemetry / Arize Phoenix adapter — spans as a finding source.

Phoenix builds the same observability model as Langfuse, but on OpenTelemetry,
which is the right fit when a team already runs Otel collectors and Grafana.
The relationship to this library is identical: spans in, findings out.

Two input shapes, both handled:

* **raw OTLP JSON** — what a collector's file exporter writes
  (``resourceSpans → scopeSpans → spans``, attributes as ``{"key", "value":
  {"stringValue": ...}}``): :func:`flatten_otlp`;
* **flat records** — what Phoenix hands you in Python::

      import phoenix as px
      px.Client().get_spans_dataframe().to_json("spans.json", orient="records")

::

    findingledger import --input spans.json --format spans --ledger bugfix/backlog.md

Rules that fire (each grouped by span name, so one finding covers a whole
endpoint rather than one unlucky request): span status ERROR, latency over a
bound, token count over a bound. As with traces, the derived signature reads
like a symptom on purpose — it is a triage candidate a human renames once the
mechanism is known.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Mapping
from datetime import date as _date
from pathlib import Path
from typing import Any

from ..models import Finding
from .common import oneline, read_json, slugify

SOURCE = "otel"

_LATENCY_KEYS = ("latency_ms", "latencyMs", "latency", "duration_ms", "duration")
_TOKEN_KEYS = ("llm.token_count.total", "cumulative_llm_token_count_total",
               "total_tokens", "token_count")
_STATUS_KEYS = ("status_code", "statusCode", "status")
_NAME_KEYS = ("name", "span_name", "operation")


def _attr_value(value: Any) -> Any:
    """Unwrap one OTLP ``AnyValue``."""
    if not isinstance(value, Mapping):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            raw = value[key]
            if key == "intValue":
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return raw
            return raw
    if "arrayValue" in value:
        values = (value["arrayValue"] or {}).get("values") or []
        return [_attr_value(v) for v in values]
    return value


def flatten_otlp(doc: Any) -> list[dict]:
    """OTLP JSON → flat span records with attributes hoisted to top level."""
    out: list[dict] = []
    resource_spans = doc.get("resourceSpans") if isinstance(doc, Mapping) else None
    for res in resource_spans or []:
        for scope in (res or {}).get("scopeSpans") or []:
            for span in (scope or {}).get("spans") or []:
                flat: dict[str, Any] = {
                    "name": span.get("name", ""),
                    "span_id": span.get("spanId", ""),
                    "trace_id": span.get("traceId", ""),
                }
                status = span.get("status") or {}
                code = status.get("code")
                flat["status_code"] = {2: "ERROR", "STATUS_CODE_ERROR": "ERROR",
                                       1: "OK", "STATUS_CODE_OK": "OK"}.get(code, code or "")
                if status.get("message"):
                    flat["status_message"] = status["message"]
                start, end = span.get("startTimeUnixNano"), span.get("endTimeUnixNano")
                if start and end:
                    with contextlib.suppress(TypeError, ValueError):
                        flat["latency_ms"] = (int(end) - int(start)) / 1e6
                for attr in span.get("attributes") or []:
                    if isinstance(attr, Mapping) and attr.get("key"):
                        flat[str(attr["key"])] = _attr_value(attr.get("value"))
                out.append(flat)
    return out


def normalize(data: Any) -> list[dict]:
    """Accept OTLP JSON, a list of flat records, or ``{"spans": [...]}``."""
    if isinstance(data, Mapping):
        if "resourceSpans" in data:
            return flatten_otlp(data)
        for key in ("spans", "data", "records"):
            if isinstance(data.get(key), list):
                # dict, not the wider Mapping: records always come from json.loads
                # here, and the declared return type is list[dict].
                return [r for r in data[key] if isinstance(r, dict)]
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def load(path: str | Path) -> list[dict]:
    return normalize(read_json(path))


def _first(record: Mapping[str, Any], keys) -> Any:
    for k in keys:
        if record.get(k) is not None:
            return record[k]
    return None


def findings_from_spans(records: Iterable[Mapping[str, Any]], date: str | None = None,
                        latency_over: float | None = None,
                        tokens_over: float | None = None, errors: bool = True,
                        min_hits: int = 1) -> list[Finding]:
    """Group spans by name and file one candidate per (rule, span name)."""
    day = date or _date.today().isoformat()
    buckets: dict[tuple[str, str], dict[str, Any]] = {}

    def hit(rule: str, name: str, detail: str, span_id: str) -> None:
        b = buckets.setdefault((rule, name), {"n": 0, "details": [], "ids": []})
        b["n"] += 1
        if len(b["details"]) < 3 and detail:
            b["details"].append(detail)
        if span_id and len(b["ids"]) < 3:
            b["ids"].append(span_id)

    for span in records:
        name = str(_first(span, _NAME_KEYS) or "unnamed")
        span_id = str(span.get("span_id") or span.get("spanId") or "")
        status = str(_first(span, _STATUS_KEYS) or "").upper()
        if errors and status in ("ERROR", "STATUS_CODE_ERROR"):
            hit("error", name, str(span.get("status_message") or "span status ERROR"), span_id)
        latency = _first(span, _LATENCY_KEYS)
        if latency_over is not None and isinstance(latency, (int, float)) \
                and latency > latency_over:
            hit("latency", name, f"{latency:.0f} ms", span_id)
        tokens = _first(span, _TOKEN_KEYS)
        if tokens_over is not None and isinstance(tokens, (int, float)) and tokens > tokens_over:
            hit("tokens", name, f"{tokens:.0f} tokens", span_id)

    bars = {"error": "span status ERROR", "latency": f"latency over {latency_over} ms",
            "tokens": f"token count over {tokens_over}"}
    out: list[Finding] = []
    for (rule, name), b in sorted(buckets.items()):
        if b["n"] < min_hits:
            continue
        out.append(Finding(
            signature=f"span-{rule}-{slugify(name)}",
            date=day,
            title=f"{name}: {b['n']} span(s) hit the {rule} rule",
            severity="critical" if rule == "error" else "major",
            klass="observability",
            symptom=f"{b['n']} span(s) of {name} matched: {bars[rule]}",
            evidence=oneline(f"{'; '.join(b['details'])} — spans: {', '.join(b['ids'])}"),
            root_cause="unknown — open the spans before naming the mechanism",
            fix_direction="rename this signature to the real mechanism once identified",
        ))
    return out
