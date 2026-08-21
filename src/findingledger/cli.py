"""Command-line interface.

Designed for unattended pipelines: single-purpose subcommands, JSON in/out,
and exit codes a cron entry or CI step can branch on without parsing output:

======  ====================================================================
Code    Meaning
======  ====================================================================
``0``   fine — including a FAIL on an ``open`` case, which is expected
``1``   **alarm**: a ``regression``/``sanity`` case failed, i.e. the defect
        is back. This code means a *quality* verdict and nothing else.
``2``   the command could not run: missing/unreadable file, malformed
        JSON/YAML, unknown project or signature, bad arguments.
``3``   the work was done but delivery failed (e.g. a dead alert webhook).
======  ====================================================================

Keeping ``1`` exclusively for alarms is what lets a caller trust it: a typo in
a path must never be reported as "the regression came back". Operational
failures are printed as ``error: ...`` on stderr, not as a traceback — a
traceback that survives to the user is a bug in this module, not a user error.

Commands group into three families:

* **lifecycle** — ``merge``, ``retract``, ``fixed``, ``graduate``
* **verdicts** — ``check`` (tri-state gate), ``trend``, ``report``
* **integrations** — ``import`` (promptfoo/DeepEval/JUnit/Ragas/spans/traces →
  ledger), ``results`` (any runner's output → the native results map),
  ``alert`` (deterministic, critical-only), ``scores`` (lifecycle state →
  Langfuse)

Everything that names a ``--project`` reads its paths from that project's
``findingledger.yaml``; everything else takes explicit ``--ledger``/``--cases``
paths, so the library works with or without a registry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import __version__
from .audits import load_audits, parse_audit, render_hub, trend
from .cases import alarms, evaluate, graduate, load_cases
from .ledger import Ledger
from .models import Finding

#: Exit code for "the command could not run" (see the module docstring).
EXIT_USAGE = 2

#: Failures that are the caller's situation, not a defect in this program:
#: an unreadable file, a malformed document, an unknown project or signature.
#: They are reported as one clean line; anything outside this tuple keeps its
#: traceback, because an unexpected exception is a bug worth seeing in full.
OPERATIONAL_ERRORS = (OSError, ValueError, KeyError, yaml.YAMLError)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))


def _cmd_merge(args: argparse.Namespace) -> int:
    raw = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    findings = [Finding.from_dict(f) for f in raw]
    ledger = Ledger(Path(args.ledger), escalate_at=args.escalate_at)
    report = ledger.merge(findings)
    if report.changed:
        ledger.save()
    _print_json({"created": report.created, "updated": report.updated,
                 "unchanged": report.unchanged, "escalation_due": report.escalation_due})
    return 0


def _cmd_retract(args: argparse.Namespace) -> int:
    ledger = Ledger(Path(args.ledger))
    ledger.retract(args.signature, args.note or "")
    ledger.save()
    print(f"retracted: {args.signature}")
    return 0


def _cmd_fixed(args: argparse.Namespace) -> int:
    ledger = Ledger(Path(args.ledger))
    ledger.mark_fixed(args.signature, args.note or "")
    ledger.save()
    print(f"fixed: {args.signature}")
    return 0


def _cmd_graduate(args: argparse.Namespace) -> int:
    case = graduate(Path(args.cases), args.case_id)
    print(f"graduated to regression: {case.id} ({case.path})")
    return 0


# ── verdicts ───────────────────────────────────────────────────────────────

def _cmd_check(args: argparse.Namespace) -> int:
    from . import adapters
    from .adapters import github as gh

    fmt = adapters.detect(args.results) if args.format == "auto" else args.format
    cases_dir = args.cases
    if not cases_dir and args.project:
        from .service import project_paths
        cases_dir = project_paths(args.project, args.registry).get("cases")
    if not cases_dir:
        print("pass --cases DIR or --project NAME", file=sys.stderr)
        return 2

    results = adapters.results(args.results, fmt)
    outcomes = evaluate(load_cases(Path(cases_dir)), results)
    rows = [{"id": o.case.id, "status": o.case.status, "result": o.result,
             "action": o.action, "backlog": o.case.backlog, "path": o.case.path}
            for o in outcomes]
    checked = {"format": fmt, "outcomes": rows,
               "alarms": [r for r in rows if r["action"] == "alarm"],
               "graduation_candidates": [r for r in rows
                                         if r["action"] == "graduation-candidate"],
               "has_alarms": any(r["action"] == "alarm" for r in rows)}

    if args.json:
        _print_json(checked)
    else:
        for o in outcomes:
            print(f"{o.case.id:30s} [{o.case.status}] {o.result:4s} -> {o.action}")

    if args.github:
        status = None
        if args.project:
            from .service import project_status
            status = project_status(args.project, args.registry)
        gh.emit(checked, status, args.project or "")

    bad = alarms(outcomes)
    if bad:
        print(f"\nALARM: {len(bad)} regression/sanity case(s) failing:", file=sys.stderr)
        for o in bad:
            print(f"  - {o.case.id} (backlog: {o.case.backlog or '—'})", file=sys.stderr)
        return 1
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from datetime import datetime

    from .report import build_report, load_registry
    if args.projects:
        paths = [Path(p) for p in args.projects]
    else:
        paths = load_registry(Path(args.registry) if args.registry else None)
    if not paths:
        print("no projects: pass --projects or fill the registry", file=sys.stderr)
        return 2
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    projects = build_report(paths, Path(args.out), escalate_at=args.escalate_at,
                            generated=generated)
    for p in projects:
        note = f"ERROR: {p.error}" if p.error else \
            f"{p.n_open} open ({p.n_critical} critical), {len(p.audits)} audits"
        print(f"{p.name:20s} {note}")
    print(f"report: {args.out}")
    return 0


def _cmd_trend(args: argparse.Namespace) -> int:
    summaries = load_audits(Path(args.audits))
    if args.hub:
        print(render_hub(summaries))
    else:
        _print_json(trend(summaries, window=args.window))
    return 0


# ── integrations ───────────────────────────────────────────────────────────

def _kv_floats(pairs) -> dict:
    out = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise SystemExit(f"expected key=value, got {raw!r}")
        key, value = raw.split("=", 1)
        out[key.strip()] = float(value)
    return out


def _import_kwargs(args: argparse.Namespace, fmt: str) -> dict:
    """Format-specific tuning flags, collected into adapter keyword arguments."""
    from .adapters import ragas as ragas_mod

    kwargs: dict = {}
    if fmt == "ragas":
        kwargs["thresholds"] = _kv_floats(args.threshold)
        kwargs["drop"] = args.drop
        if args.baseline:
            raw = ragas_mod.read_json(args.baseline) if Path(args.baseline).exists() else {}
            kwargs["baseline"] = ragas_mod.scores(raw)
            kwargs["baseline_date"] = str(raw.get("date", "")) if isinstance(raw, dict) else ""
    if fmt in ("spans", "traces"):
        kwargs["min_hits"] = args.min_hits
        if args.latency_over is not None:
            kwargs["latency_over"] = args.latency_over
    if fmt == "spans" and args.tokens_over is not None:
        kwargs["tokens_over"] = args.tokens_over
    if fmt == "traces":
        if args.cost_over is not None:
            kwargs["cost_over"] = args.cost_over
        if args.score_below:
            name, _, value = args.score_below.partition("=")
            kwargs["score_below"] = (name.strip(), float(value))
        if args.host_url:
            kwargs["host_url"] = args.host_url
    return kwargs


def _cmd_import(args: argparse.Namespace) -> int:
    from . import adapters
    from .adapters import ragas as ragas_mod

    fmt = adapters.detect(args.input) if args.format == "auto" else args.format
    kwargs = _import_kwargs(args, fmt)
    findings = adapters.findings(args.input, fmt, date=args.date, prefix=args.prefix, **kwargs)

    # A dry run must not leave anything behind — moving the baseline would make
    # the next real run compare against today and see no regression at all.
    if fmt == "ragas" and args.save_baseline and not args.dry_run:
        target = args.save_baseline if isinstance(args.save_baseline, str) else args.baseline
        if target:
            ragas_mod.save_baseline(target, ragas_mod.load(args.input), args.date)
            print(f"baseline saved: {target}", file=sys.stderr)

    payload = [f.to_dict() for f in findings]

    ledger_path = args.ledger
    if not ledger_path and args.project:
        from .service import project_paths
        ledger_path = project_paths(args.project, args.registry).get("ledger")

    if args.dry_run or not ledger_path:
        result = {"format": fmt, "dry_run": True, "findings": payload}
        if not ledger_path and not args.dry_run:
            result["note"] = "no --ledger/--project given: nothing was written"
        _print_json(result)
        return 0

    ledger = Ledger(Path(ledger_path), escalate_at=args.escalate_at)
    report = ledger.merge(findings)
    if report.changed:
        ledger.save()
    _print_json({"format": fmt, "ledger": str(ledger_path), "findings": len(payload),
                 "created": report.created, "updated": report.updated,
                 "unchanged": report.unchanged, "escalation_due": report.escalation_due})
    return 0


def _cmd_results(args: argparse.Namespace) -> int:
    from . import adapters
    fmt = adapters.detect(args.input) if args.format == "auto" else args.format
    results = adapters.results(args.input, fmt, strict=not args.lenient)
    text = json.dumps(results, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{len(results)} result(s) [{fmt}] -> {args.out}")
    else:
        print(text, end="")
    return 0


def _cmd_alert(args: argparse.Namespace) -> int:
    from .adapters import alerts as alerts_mod

    if args.project:
        from .service import alert as alert_service
        receipt = alert_service(
            args.project, audit=args.audit, results=args.results,
            min_severity=args.min_severity, channel=args.channel, url=args.webhook,
            command=args.command, path=args.file, fmt=args.format, dry_run=args.dry_run,
            registry=args.registry)
    else:
        if not args.audit:
            print("pass --audit FILE (or --project NAME)", file=sys.stderr)
            return 2
        summary = parse_audit(Path(args.audit))
        if summary is None:
            print(f"{args.audit}: no YAML frontmatter to read", file=sys.stderr)
            return 2
        payload = alerts_mod.from_audit(summary, args.name or "", args.min_severity)
        receipt = ({"alerted": False, "source": "audit"} if payload is None else
                   {**alerts_mod.dispatch(payload, channel=args.channel or "stdout",
                                          url=args.webhook, command=args.command,
                                          path=args.file, fmt=args.format,
                                          dry_run=args.dry_run),
                    "alerted": True, "source": "audit"})

    if args.json:
        _print_json(receipt)
    elif not receipt.get("alerted"):
        print(f"no alert: nothing reached severity {args.min_severity}")
    elif receipt.get("error"):
        print(f"alert FAILED via {receipt.get('channel')}: {receipt['error']}", file=sys.stderr)
    elif receipt.get("channel") != "stdout":
        print(f"alert sent via {receipt.get('channel')}: {receipt.get('title')}")

    if receipt.get("error"):
        return 3
    return 1 if (args.fail_on_alert and receipt.get("alerted")) else 0


def _cmd_scores(args: argparse.Namespace) -> int:
    from .service import push_scores, score_payloads

    if args.target == "json" or args.dry_run:
        payloads = score_payloads(args.project, args.registry)
        text = json.dumps(payloads, ensure_ascii=False, indent=1) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"{len(payloads)} score(s) -> {args.out}")
        else:
            print(text, end="")
        return 0
    try:
        result = push_scores(args.project, trace_id=args.trace_id,
                             session_id=args.session_id, host=args.host,
                             dry_run=False, registry=args.registry)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"pushed {result['pushed']} score(s) to langfuse")
    for err in result.get("errors") or []:
        print(f"  error: {err}", file=sys.stderr)
    return 0 if not result.get("errors") else 3


# ── parser ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    from .adapters import FORMATS

    p = argparse.ArgumentParser(prog="findingledger",
                                description="Finding-lifecycle layer for LLM evaluation.")
    p.add_argument("--version", action="version", version=f"findingledger {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("merge", help="merge findings (JSON) into a markdown ledger")
    m.add_argument("--ledger", required=True)
    m.add_argument("--findings", required=True)
    m.add_argument("--escalate-at", type=int, default=3)
    m.set_defaults(fn=_cmd_merge)

    r = sub.add_parser("retract", help="mark a ledger item as a false positive")
    r.add_argument("--ledger", required=True)
    r.add_argument("signature")
    r.add_argument("--note")
    r.set_defaults(fn=_cmd_retract)

    f = sub.add_parser("fixed", help="mark a ledger item as fixed")
    f.add_argument("--ledger", required=True)
    f.add_argument("signature")
    f.add_argument("--note")
    f.set_defaults(fn=_cmd_fixed)

    g = sub.add_parser("graduate", help="flip an open case to regression")
    g.add_argument("--cases", required=True)
    g.add_argument("case_id")
    g.set_defaults(fn=_cmd_graduate)

    c = sub.add_parser("check", help="confront cases with a runner's results "
                                     "(promptfoo/DeepEval/JUnit/Ragas/JSON)")
    c.add_argument("--cases", help="case directory (or use --project)")
    c.add_argument("--results", required=True)
    c.add_argument("--format", default="auto", choices=list(FORMATS),
                   help="results format (default: sniff the file)")
    c.add_argument("--project", help="project name/path (takes --cases from its config)")
    c.add_argument("--registry")
    c.add_argument("--github", action="store_true",
                   help="emit GitHub Actions annotations, job summary and step outputs")
    c.add_argument("--json", action="store_true", help="machine-readable output")
    c.set_defaults(fn=_cmd_check)

    rp = sub.add_parser("report", help="self-contained multi-project HTML report")
    rp.add_argument("--projects", nargs="*",
                    help="project roots (with findingledger.yaml); default: registry")
    rp.add_argument("--registry",
                    help="registry path (default ~/.config/findingledger/projects.yaml)")
    rp.add_argument("--out", "-o", default="findingledger-report.html")
    rp.add_argument("--escalate-at", type=int, default=3)
    rp.set_defaults(fn=_cmd_report)

    t = sub.add_parser("trend", help="quality trend / hub table from audit frontmatter")
    t.add_argument("--audits", required=True)
    t.add_argument("--hub", action="store_true")
    t.add_argument("--window", type=int, default=7)
    t.set_defaults(fn=_cmd_trend)

    i = sub.add_parser("import", help="foreign tool output -> findings in the ledger")
    i.add_argument("--input", "-i", required=True, help="results/spans/traces file")
    i.add_argument("--format", default="auto", choices=list(FORMATS))
    i.add_argument("--ledger", help="ledger file (or use --project)")
    i.add_argument("--project")
    i.add_argument("--registry")
    i.add_argument("--date", help="occurrence date (default: today)")
    i.add_argument("--prefix", default="", help="prefix for derived signatures")
    i.add_argument("--escalate-at", type=int, default=3)
    i.add_argument("--dry-run", action="store_true",
                   help="print the findings instead of writing them (recommended first)")
    i.add_argument("--threshold", action="append", metavar="METRIC=VALUE",
                   help="ragas: quality bar per metric (repeatable)")
    i.add_argument("--baseline", help="ragas: baseline file to compare against")
    i.add_argument("--save-baseline", nargs="?", const=True, default=None,
                   help="ragas: store current scores as the new baseline")
    i.add_argument("--drop", type=float, default=0.10,
                   help="ragas: relative drop counting as a regression (default 0.10)")
    i.add_argument("--latency-over", type=float, help="spans/traces: latency bound")
    i.add_argument("--tokens-over", type=float, help="spans: token-count bound")
    i.add_argument("--cost-over", type=float, help="traces: cost bound")
    i.add_argument("--score-below", metavar="NAME=VALUE", help="traces: score bound")
    i.add_argument("--host-url", default="", help="traces: base url for evidence links")
    i.add_argument("--min-hits", type=int, default=1,
                   help="spans/traces: how many hits before filing a candidate")
    i.set_defaults(fn=_cmd_import)

    res = sub.add_parser("results", help="any runner's output -> the native results map")
    res.add_argument("--input", "-i", required=True)
    res.add_argument("--format", default="auto", choices=list(FORMATS))
    res.add_argument("--out", "-o")
    res.add_argument("--lenient", action="store_true",
                     help="one PASS is enough when a case ran on several providers")
    res.set_defaults(fn=_cmd_results)

    a = sub.add_parser("alert", help="deterministic, critical-only notification")
    a.add_argument("--project")
    a.add_argument("--registry")
    a.add_argument("--audit", help="audit file whose frontmatter decides")
    a.add_argument("--results", help="results file (a failing regression case alerts)")
    a.add_argument("--name", default="", help="label when running without --project")
    a.add_argument("--min-severity", default="critical",
                   choices=["critical", "major", "minor"])
    a.add_argument("--channel", choices=["stdout", "file", "webhook", "command", "none"])
    a.add_argument("--webhook", default="", help="http(s) endpoint (Slack-compatible)")
    a.add_argument("--command", default="", help="argv reading the message on stdin")
    a.add_argument("--file", default="", help="path to append to (channel: file)")
    a.add_argument("--format", default="text", choices=["text", "markdown"])
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--fail-on-alert", action="store_true",
                   help="exit 1 when an alert was raised (for chained cron steps)")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=_cmd_alert)

    s = sub.add_parser("scores", help="lifecycle state -> observability scores (Langfuse)")
    s.add_argument("--project", required=True)
    s.add_argument("--registry")
    s.add_argument("--target", default="langfuse", choices=["langfuse", "json"])
    s.add_argument("--trace-id")
    s.add_argument("--session-id")
    s.add_argument("--host", help="Langfuse host (default: $LANGFUSE_HOST)")
    s.add_argument("--dry-run", action="store_true", help="print payloads, send nothing")
    s.add_argument("--out", "-o")
    s.set_defaults(fn=_cmd_scores)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except OPERATIONAL_ERRORS as exc:
        # KeyError stringifies to "'msg'"; strip the quotes argparse-style so
        # every operational failure reads the same way.
        detail = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"error: {detail}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
