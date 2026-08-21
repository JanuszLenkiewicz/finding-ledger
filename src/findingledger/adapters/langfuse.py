"""Langfuse adapter — the bridge that runs in both directions.

Langfuse (and LangSmith, Braintrust, Phoenix — the model is the same) records
what production *did*: a trace per request, a generation per model call, scores
attached to traces. This library records what is *wrong* with it and whether it
comes back. Connecting them is worth more than either side alone:

**Platform → ledger.** Traces are the best finding source you will ever have,
because they are real user traffic rather than your imagination of it. Review
the most expensive, the slowest, the lowest-scored — spot the pattern, name the
mechanism, file it: :func:`findings_from_traces`.

**Ledger → platform.** Push the lifecycle state back as scores:
:func:`payloads_from_status`. Then cost, latency and *quality* sit in one view,
and the sentence "my system's quality lives in the same dashboard as its cost"
stops being a slide and becomes a screenshot.

The ``langfuse`` SDK is optional and imported lazily — everything except the
actual network call works without it, which is what makes ``--dry-run`` honest::

    pip install "finding-ledger[langfuse]"
    export LANGFUSE_PUBLIC_KEY=pk-... LANGFUSE_SECRET_KEY=sk-...
    findingledger scores --project newsletter --trace-id <id>

Credentials are read from the environment only. This module never writes them
anywhere, and no adapter in this package persists a key.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from datetime import date as _date
from typing import Any

from ..models import Finding
from .common import oneline, slugify

SOURCE = "langfuse"

#: Prefix for every score this library emits, so they group in the UI and never
#: collide with the application's own scores.
SCORE_PREFIX = "findingledger."


def payloads_from_status(status: Mapping[str, Any], prefix: str = SCORE_PREFIX,
                         ) -> list[dict]:
    """Lifecycle state → Langfuse score payloads.

    ``status`` is what :func:`findingledger.service.project_status` returns.
    Numeric scores carry the counts; one categorical score carries the verdict,
    because "is it getting better?" is the question a dashboard should answer
    without arithmetic.
    """
    project = str(status.get("name", ""))
    base = {"project": project}
    out: list[dict] = [
        {"name": f"{prefix}open_items", "value": int(status.get("open_items") or 0),
         "dataType": "NUMERIC", "comment": f"open ledger items in {project}", **base},
        {"name": f"{prefix}critical_open", "value": int(status.get("critical_open") or 0),
         "dataType": "NUMERIC", "comment": f"open CRITICAL items in {project}", **base},
        {"name": f"{prefix}escalation_due",
         "value": len(status.get("escalation_candidates") or []),
         "dataType": "NUMERIC",
         "comment": "items at/over the occurrence threshold awaiting a human decision",
         **base},
    ]

    cases = status.get("cases") or []
    for wanted in ("regression", "open", "sanity"):
        out.append({"name": f"{prefix}cases_{wanted}",
                    "value": sum(1 for c in cases if str(c.get("status")) == wanted),
                    "dataType": "NUMERIC", "comment": f"golden cases with status {wanted}",
                    **base})

    trend = status.get("trend") or {}
    delta = trend.get("delta") or {}
    if trend.get("has_baseline") and delta:
        out.append({"name": f"{prefix}delta_critical",
                    "value": int(delta.get("critical") or 0), "dataType": "NUMERIC",
                    "comment": "critical findings vs the previous audit window "
                               "(negative = improving)", **base})
        verdict = "degrading" if trend.get("degrading") else "improving"
    else:
        verdict = "no-baseline"
    out.append({"name": f"{prefix}verdict", "value": verdict, "dataType": "CATEGORICAL",
                "comment": "quality trend over the audit window", **base})

    if status.get("last_audit"):
        out.append({"name": f"{prefix}last_audit", "value": str(status["last_audit"]),
                    "dataType": "CATEGORICAL", "comment": "date of the most recent audit",
                    **base})
    return out


def payloads_from_audit(summary: Any, prefix: str = SCORE_PREFIX) -> list[dict]:
    """One audit's frontmatter → score payloads (findings by severity)."""
    by_sev = getattr(summary, "by_severity", None) or {}
    date = getattr(summary, "date", "")
    out = [{"name": f"{prefix}audit_findings",
            "value": int(getattr(summary, "n_findings", 0) or 0), "dataType": "NUMERIC",
            "comment": f"findings in the audit of {date}"}]
    for sev in ("critical", "major", "minor"):
        out.append({"name": f"{prefix}audit_{sev}", "value": int(by_sev.get(sev, 0) or 0),
                    "dataType": "NUMERIC", "comment": f"{sev} findings in the audit of {date}"})
    return out


def _client(host: str | None = None):
    """Build a Langfuse client from the environment (lazy import)."""
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "pushing scores needs the optional dependency:\n"
            '    pip install "finding-ledger[langfuse]"'
        ) from exc
    public, secret = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get(
        "LANGFUSE_SECRET_KEY")
    if not (public and secret):
        raise RuntimeError(
            "set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (this library never "
            "stores credentials)")
    return Langfuse(public_key=public, secret_key=secret,
                    host=host or os.environ.get("LANGFUSE_HOST") or None)


def push_scores(payloads: Iterable[Mapping[str, Any]], trace_id: str | None = None,
                session_id: str | None = None, client: Any = None, host: str | None = None,
                dry_run: bool = False) -> dict:
    """Send score payloads to Langfuse.

    ``client`` may be injected (any object with ``create_score``/``score``),
    which is how this is tested without the SDK. ``dry_run`` returns exactly
    what *would* be sent — use it in CI before wiring a real key.

    A score needs something to attach to: pass ``trace_id`` (typically the
    day's audit run) or ``session_id``. Without either, Langfuse would drop the
    score silently, so this raises instead.
    """
    payloads = list(payloads)
    if dry_run:
        return {"pushed": 0, "dry_run": True, "payloads": payloads,
                "trace_id": trace_id, "session_id": session_id}
    if not (trace_id or session_id):
        raise ValueError("pass trace_id or session_id: a Langfuse score must attach to "
                         "a trace or a session")
    api = client or _client(host)
    create = getattr(api, "create_score", None) or getattr(api, "score", None)
    if create is None:  # pragma: no cover - defensive across SDK generations
        raise RuntimeError("the langfuse client exposes neither create_score nor score")

    errors: list[str] = []
    pushed = 0
    for p in payloads:
        kwargs = {"name": p["name"], "value": p["value"]}
        if p.get("comment"):
            kwargs["comment"] = p["comment"]
        if p.get("dataType"):
            kwargs["data_type"] = p["dataType"]
        if trace_id:
            kwargs["trace_id"] = trace_id
        if session_id:
            kwargs["session_id"] = session_id
        try:
            create(**kwargs)
            pushed += 1
        except TypeError:  # older SDKs know fewer keyword arguments
            try:
                create(name=p["name"], value=p["value"],
                       **({"trace_id": trace_id} if trace_id else {}))
                pushed += 1
            except Exception as exc:  # pragma: no cover - SDK-specific
                errors.append(f"{p['name']}: {exc}")
        except Exception as exc:  # pragma: no cover - network/SDK-specific
            errors.append(f"{p['name']}: {exc}")
    flush = getattr(api, "flush", None)
    if callable(flush):
        flush()
    return {"pushed": pushed, "dry_run": False, "errors": errors,
            "trace_id": trace_id, "session_id": session_id}


# ── platform → ledger ──────────────────────────────────────────────────────

def _trace_value(trace: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        if trace.get(k) is not None:
            return trace[k]
    return None


def _score_of(trace: Mapping[str, Any], name: str) -> float | None:
    scores = trace.get("scores")
    if isinstance(scores, Mapping):
        value = scores.get(name)
        return float(value) if isinstance(value, (int, float)) else None
    if isinstance(scores, list):
        for s in scores:
            if isinstance(s, Mapping) and s.get("name") == name and isinstance(
                    s.get("value"), (int, float)):
                return float(s["value"])
    return None


def findings_from_traces(traces: Iterable[Mapping[str, Any]], date: str | None = None,
                         cost_over: float | None = None, latency_over: float | None = None,
                         score_below: tuple[str, float] | None = None,
                         min_hits: int = 1, group_by: str = "name",
                         host_url: str = "") -> list[Finding]:
    """Turn a batch of production traces into finding candidates.

    This is *triage*, not judgement: it groups traces by operation name and
    files one candidate per rule that fires often enough (``min_hits``), with
    example trace ids as evidence. A human or an agent then names the real
    mechanism and rewrites the signature — the derived one deliberately reads
    like a symptom so nobody mistakes it for a diagnosis.

    Expected trace fields (all optional, several spellings accepted):
    ``id``, ``name``, ``totalCost``/``cost``, ``latency``/``latencyMs``,
    ``scores``.
    """
    day = date or _date.today().isoformat()
    buckets: dict[tuple[str, str], dict[str, Any]] = {}

    def hit(rule: str, group: str, trace: Mapping[str, Any], detail: str) -> None:
        b = buckets.setdefault((rule, group), {"n": 0, "examples": [], "details": []})
        b["n"] += 1
        tid = str(_trace_value(trace, "id", "traceId", "trace_id") or "")
        if tid and len(b["examples"]) < 3:
            b["examples"].append(f"{host_url.rstrip('/')}/trace/{tid}" if host_url else tid)
        if len(b["details"]) < 3:
            b["details"].append(detail)

    for trace in traces:
        group = str(trace.get(group_by) or trace.get("name") or "unnamed")
        cost = _trace_value(trace, "totalCost", "cost", "total_cost")
        latency = _trace_value(trace, "latency", "latencyMs", "latency_ms", "duration")
        if cost_over is not None and isinstance(cost, (int, float)) and cost > cost_over:
            hit("cost", group, trace, f"cost {cost}")
        if latency_over is not None and isinstance(latency, (int, float)) \
                and latency > latency_over:
            hit("latency", group, trace, f"latency {latency}")
        if score_below is not None:
            name, bar = score_below
            value = _score_of(trace, name)
            if value is not None and value < bar:
                hit(f"score-{slugify(name)}", group, trace, f"{name}={value}")

    rules = {"cost": ("cost outlier", f"cost over {cost_over}"),
             "latency": ("latency outlier", f"latency over {latency_over}")}
    out: list[Finding] = []
    for (rule, group), b in sorted(buckets.items()):
        if b["n"] < min_hits:
            continue
        # Named bar_text, not bar: `bar` above is the numeric score_below threshold
        # (float) and mypy infers one type per variable name across the function.
        label, bar_text = rules.get(rule, (f"low {rule.replace('score-', '')} score",
                                            f"below {score_below[1] if score_below else '—'}"))
        out.append(Finding(
            signature=f"trace-{rule}-{slugify(group)}",
            date=day,
            title=f"{group}: {label} ({b['n']} trace(s))",
            severity="major",
            klass="production-trace",
            symptom=f"{b['n']} trace(s) of {group} matched the rule: {bar_text}",
            evidence=oneline(f"examples: {', '.join(b['examples'])} — {'; '.join(b['details'])}"),
            root_cause="unknown — review the linked traces before naming the mechanism",
            fix_direction="rename this signature to the real mechanism once identified",
        ))
    return out
