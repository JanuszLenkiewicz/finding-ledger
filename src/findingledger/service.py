"""Project-aware operations returning JSON-serializable dicts.

This is the layer agents talk to — through MCP (``mcp_server.py``) or directly
from Python. It resolves projects by name or path, and returns plain
dicts/lists so nothing needs a custom serializer.

Design note: all logic lives here, not in the MCP wrapper, so it stays
testable without the optional ``mcp`` dependency installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from .audits import load_audits, parse_audit, trend
from .cases import evaluate, graduate, load_cases
from .ledger import DEFAULT_ESCALATE_AT, Ledger
from .models import Finding
from .report import (
    CLOSED_PREFIXES,
    CRITICAL_PREFIXES,
    PROJECT_FILE,
    build_report,
    load_project,
    load_registry,
    resolve_config_path,
)


class ProjectNotFound(ValueError):
    """Raised when a project name/path cannot be resolved to a config file.

    A ``ValueError`` on purpose: to every caller this is a bad argument, so the
    CLI reports it as an operational error (exit 2) without importing this
    module just to name the exception.
    """


def resolve_project(project: str, registry: Optional[str] = None) -> Path:
    """Resolve a project name (from the registry) or a filesystem path to its root."""
    reg_path = Path(registry).expanduser() if registry else None
    try:
        for root in load_registry(reg_path):
            cfg = root / PROJECT_FILE
            if root.name == project or (cfg.exists() and _config_name(cfg) == project):
                return root
    except OSError:
        pass  # no registry is fine when a path is given

    candidate = Path(project).expanduser()
    if (candidate / PROJECT_FILE).exists() or candidate.suffix in (".yaml", ".yml"):
        return candidate
    raise ProjectNotFound(
        f"unknown project {project!r}: not in the registry and not a path containing "
        f"{PROJECT_FILE}")


def _config_name(cfg: Path) -> str:
    try:
        return str((yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}).get("name", ""))
    except (OSError, ValueError):
        return ""


def _config(project: str, registry: Optional[str] = None):
    """``(config_path, parsed_config)`` for a project name or path."""
    root = resolve_project(project, registry)
    cfg_path = root / PROJECT_FILE if root.is_dir() else root
    return cfg_path, (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {})


def _path_of(cfg_path: Path, cfg: dict, key: str) -> Optional[Path]:
    """One declared path from a config, or ``None`` when the key is absent.

    Resolution itself is :func:`~findingledger.report.resolve_config_path` — the
    single rule shared with the report layer.
    """
    value = cfg.get(key)
    return resolve_config_path(cfg_path.parent, str(value)) if value else None


def project_paths(project: str, registry: Optional[str] = None) -> dict:
    """Where a project keeps its ledger, cases and audits — as strings.

    The one place that knows how ``findingledger.yaml`` maps to the filesystem;
    the CLI, the pytest plugin and the MCP tools all read it from here instead
    of re-implementing the resolution.
    """
    cfg_path, cfg = _config(project, registry)
    # Any, not str: "alerts"/"adapters" below hold nested dicts, not strings.
    out: dict[str, Any] = {
        "name": str(cfg.get("name", cfg_path.parent.name)), "root": str(cfg_path.parent),
        "config": str(cfg_path),
        "audits_pattern": str(cfg.get("audits_pattern", "*-audit.md"))}
    for key in ("ledger", "cases", "audits"):
        resolved = _path_of(cfg_path, cfg, key)
        out[key] = str(resolved) if resolved else ""
    out["alerts"] = dict(cfg.get("alerts") or {})
    out["adapters"] = dict(cfg.get("adapters") or {})
    return out


def _ledger_of(project: str, registry: Optional[str] = None) -> Ledger:
    cfg_path, cfg = _config(project, registry)
    ledger_path = _path_of(cfg_path, cfg, "ledger")
    if ledger_path is None:
        raise ProjectNotFound(f"project {project!r} declares no ledger in {PROJECT_FILE}")
    return Ledger(ledger_path)


def _item_dict(item) -> dict:
    return {"signature": item.signature, "status": item.status, "title": item.title,
            "count": item.count, "occurrences": item.occurrences, "section": item.section,
            "open": not item.status.upper().startswith(CLOSED_PREFIXES)}


# ── read operations ────────────────────────────────────────────────────────

def list_projects(registry: Optional[str] = None) -> list[dict]:
    """Every project in the registry with headline numbers."""
    reg_path = Path(registry).expanduser() if registry else None
    out = []
    for root in load_registry(reg_path):
        p = load_project(root)
        out.append({"name": p.name, "root": p.root, "description": p.description,
                    "open_items": p.n_open, "critical": p.n_critical,
                    "audits": len(p.audits), "error": p.error})
    return out


def project_status(project: str, registry: Optional[str] = None,
                   escalate_at: int = DEFAULT_ESCALATE_AT) -> dict:
    """Headline state of one project: counts, escalation candidates, cases, trend."""
    root = resolve_project(project, registry)
    p = load_project(root, escalate_at)
    return {
        "name": p.name, "root": p.root, "description": p.description, "error": p.error,
        "open_items": p.n_open, "closed_items": p.n_closed, "critical_open": p.n_critical,
        "escalation_candidates": [_item_dict(i) for i in p.escalation_candidates],
        "cases": [{"id": cid, "status": st} for cid, st in p.cases],
        "audits": len(p.audits),
        "last_audit": p.audits[-1].date if p.audits else None,
        "trend": p.trend or None,
    }


def ledger_items(project: str, status: str = "open", min_count: int = 0,
                 registry: Optional[str] = None) -> list[dict]:
    """Ledger items, most-recurring first.

    ``status``: ``open`` (default), ``closed``, ``critical`` or ``all``.
    """
    ledger = _ledger_of(project, registry)
    items = sorted(ledger.items.values(), key=lambda i: -i.count)
    keep = []
    for i in items:
        upper = i.status.upper()
        closed = upper.startswith(CLOSED_PREFIXES)
        if status == "open" and closed:
            continue
        if status == "closed" and not closed:
            continue
        if status == "critical" and (closed or not upper.startswith(CRITICAL_PREFIXES)):
            continue
        if i.count < min_count:
            continue
        keep.append(_item_dict(i))
    return keep


def audit_trend(project: str, window: int = 7, registry: Optional[str] = None) -> dict:
    """Quality trend over the audit history (negative deltas = improving)."""
    cfg_path, cfg = _config(project, registry)
    audits_dir = _path_of(cfg_path, cfg, "audits")
    if audits_dir is None:
        return {"error": "project declares no audits directory"}
    summaries = load_audits(audits_dir, str(cfg.get("audits_pattern", "*-audit.md")))
    result = trend(summaries, window=window)
    result["audits"] = [{"date": s.date, "n_findings": s.n_findings,
                         "by_severity": s.by_severity} for s in summaries[-window:]]
    return result


def check_cases(project: str, results: dict, registry: Optional[str] = None) -> dict:
    """Confront golden cases with assertion results.

    A failing ``open`` case is status quo, not an alarm — only ``regression``
    and ``sanity`` failures raise ``has_alarms``.
    """
    cfg_path, cfg = _config(project, registry)
    cases_dir = _path_of(cfg_path, cfg, "cases")
    if cases_dir is None:
        return {"error": "project declares no cases directory"}
    outcomes = evaluate(load_cases(cases_dir), results)
    rows = [{"id": o.case.id, "status": o.case.status, "result": o.result,
             "action": o.action, "backlog": o.case.backlog, "path": o.case.path}
            for o in outcomes]
    return {"outcomes": rows,
            "alarms": [r for r in rows if r["action"] == "alarm"],
            "graduation_candidates": [r for r in rows
                                      if r["action"] == "graduation-candidate"],
            "has_alarms": any(r["action"] == "alarm" for r in rows)}


# ── write operations (surgical, never regenerate a file) ───────────────────

def merge_findings(project: str, findings: list[dict], registry: Optional[str] = None,
                   escalate_at: int = DEFAULT_ESCALATE_AT) -> dict:
    """Deduplicate findings into the ledger by root-cause signature.

    Merging the same finding twice is a no-op. Items reaching ``escalate_at``
    are reported in ``escalation_due`` for a human to decide — severity is
    never changed automatically.
    """
    ledger = _ledger_of(project, registry)
    ledger.escalate_at = escalate_at
    parsed = [Finding.from_dict(f) for f in findings]
    report = ledger.merge(parsed)
    if report.changed:
        ledger.save()
    return {"created": report.created, "updated": report.updated,
            "unchanged": report.unchanged, "escalation_due": report.escalation_due,
            "ledger": str(ledger.path), "written": report.changed}


def mark_fixed(project: str, signature: str, note: str = "",
               registry: Optional[str] = None) -> dict:
    ledger = _ledger_of(project, registry)
    ledger.mark_fixed(signature, note)
    ledger.save()
    return {"signature": signature, "status": "FIXED", "ledger": str(ledger.path)}


def retract_finding(project: str, signature: str, note: str = "",
                    registry: Optional[str] = None) -> dict:
    """Mark an item as a false positive of the audit — history is preserved."""
    ledger = _ledger_of(project, registry)
    ledger.retract(signature, note)
    ledger.save()
    return {"signature": signature, "status": "RETRACTED", "ledger": str(ledger.path)}


def graduate_case(project: str, case_id: str, registry: Optional[str] = None) -> dict:
    """Flip an ``open`` case to ``regression`` once its fix has landed."""
    cfg_path, cfg = _config(project, registry)
    cases_dir = _path_of(cfg_path, cfg, "cases")
    if cases_dir is None:
        raise ProjectNotFound(f"project {project!r} declares no cases directory")
    case = graduate(cases_dir, case_id)
    return {"id": case.id, "status": case.status, "path": case.path}


# ── integrations (foreign tools in, lifecycle state out) ───────────────────

def check_results_file(project: str, path: str, fmt: str = "auto",
                       registry: Optional[str] = None) -> dict:
    """Confront golden cases with a results file from any supported runner.

    ``fmt``: ``auto`` (sniff the file), ``promptfoo``, ``deepeval``, ``junit``,
    ``ragas`` or ``json`` (this library's own map). Read-only.
    """
    from . import adapters
    resolved = adapters.detect(path) if fmt in ("", "auto", None) else fmt
    results = adapters.results(path, resolved)
    out = check_cases(project, results, registry)
    out["format"] = resolved
    out["results"] = results
    return out


def import_tool_output(project: str, path: str, fmt: str = "auto",
                       date: Optional[str] = None, prefix: str = "",
                       dry_run: bool = False, registry: Optional[str] = None,
                       escalate_at: int = DEFAULT_ESCALATE_AT, **kwargs) -> dict:
    """Read a foreign tool's output and merge its failures into the ledger.

    This is the promptfoo/DeepEval/Ragas/Phoenix on-ramp: one call turns "the
    run was red" into deduplicated backlog entries with occurrence counters.
    ``dry_run`` returns the findings without touching the file — always worth
    doing once, because derived signatures name symptoms and usually want a
    human rename before they enter the ledger for good.
    """
    from . import adapters
    resolved = adapters.detect(path) if fmt in ("", "auto", None) else fmt
    found = adapters.findings(path, resolved, date=date, prefix=prefix, **kwargs)
    payload = [f.to_dict() for f in found]
    if dry_run:
        return {"format": resolved, "dry_run": True, "findings": payload,
                "created": [], "updated": [], "unchanged": [], "escalation_due": []}
    result = merge_findings(project, payload, registry, escalate_at) if payload else {
        "created": [], "updated": [], "unchanged": [], "escalation_due": [], "written": False}
    result.update({"format": resolved, "dry_run": False, "findings": payload})
    return result


def alert(project: str, audit: Optional[str] = None, results: Optional[str] = None,
          min_severity: str = "critical", channel: Optional[str] = None,
          url: str = "", command: str = "", path: str = "", fmt: str = "text",
          dry_run: bool = False, registry: Optional[str] = None) -> dict:
    """Decide whether something is worth waking a human for, and send it.

    Sources, in order of preference: an ``--audit`` file's frontmatter, a
    results file (a failing regression/sanity case is always critical), or the
    project's current ledger state. Channel defaults come from the project's
    ``alerts:`` block in ``findingledger.yaml``, so cron entries stay short.

    Returns a receipt with ``alerted: false`` when nothing reached
    ``min_severity`` — the normal, quiet outcome.
    """
    from .adapters import alerts as alerts_mod

    try:
        paths = project_paths(project, registry)
        defaults, name, root = dict(paths.get("alerts") or {}), paths["name"], paths["root"]
    except (ProjectNotFound, OSError):
        defaults, name, root = {}, project, ""
    channel = channel or str(defaults.get("channel", "stdout"))
    url = url or str(defaults.get("webhook", defaults.get("url", "")))
    command = command or str(defaults.get("command", ""))
    from_config = not path and bool(defaults.get("path"))
    path = path or str(defaults.get("path", ""))
    min_severity = min_severity or str(defaults.get("min_severity", "critical"))
    # A relative alert-log path in findingledger.yaml belongs to the project,
    # not to whatever directory cron happened to start in. A path typed on the
    # command line stays relative to the caller, as typed.
    if from_config and root and not Path(path).expanduser().is_absolute():
        path = str(Path(root) / path)

    payload = None
    source = ""
    if audit:
        summary = parse_audit(Path(audit).expanduser())
        if summary is None:
            return {"alerted": False, "error": f"{audit} has no YAML frontmatter"}
        payload, source = alerts_mod.from_audit(summary, name, min_severity), "audit"
    elif results:
        checked = check_results_file(project, results, "auto", registry)
        payload, source = alerts_mod.from_check(checked, name), "check"
    else:
        items = ledger_items(project, "critical", 0, registry)
        payload = alerts_mod.from_findings(
            [{"severity": "critical", "signature": i["signature"], "title": i["title"]}
             for i in items], name, min_severity)
        source = "ledger"

    if payload is None:
        return {"alerted": False, "source": source, "min_severity": min_severity,
                "project": name}
    receipt = alerts_mod.dispatch(payload, channel=channel, url=url, command=command,
                                  path=path, fmt=fmt, dry_run=dry_run)
    receipt.update({"alerted": True, "source": source, "project": name})
    return receipt


def score_payloads(project: str, registry: Optional[str] = None,
                   prefix: Optional[str] = None) -> list[dict]:
    """Lifecycle state as observability scores (Langfuse shape, tool-agnostic)."""
    from .adapters import langfuse as lf
    status = project_status(project, registry)
    return lf.payloads_from_status(status, prefix or lf.SCORE_PREFIX)


def push_scores(project: str, trace_id: Optional[str] = None,
                session_id: Optional[str] = None, host: Optional[str] = None,
                dry_run: bool = False, registry: Optional[str] = None) -> dict:
    """Push the project's quality scores to Langfuse (credentials from env)."""
    from .adapters import langfuse as lf
    payloads = score_payloads(project, registry)
    result = lf.push_scores(payloads, trace_id=trace_id, session_id=session_id,
                            host=host, dry_run=dry_run)
    result["project"] = project
    result.setdefault("payloads", payloads)
    return result


def make_report(out: str, projects: Optional[list[str]] = None,
                registry: Optional[str] = None) -> dict:
    """Render the self-contained multi-project HTML report."""
    if projects:
        paths = [resolve_project(p, registry) for p in projects]
    else:
        paths = load_registry(Path(registry).expanduser() if registry else None)
    rendered = build_report(paths, Path(out).expanduser())
    return {"out": str(Path(out).expanduser()),
            "projects": [{"name": p.name, "open_items": p.n_open,
                          "critical": p.n_critical, "error": p.error} for p in rendered]}
