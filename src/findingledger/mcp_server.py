"""MCP server exposing the finding lifecycle as agent tools.

Install with the optional extra and run over stdio::

    pip install "finding-ledger[mcp]"
    findingledger-mcp

Register it with an MCP host (Claude Code, Claude Desktop, any MCP client)::

    {"mcpServers": {"finding-ledger": {"command": "findingledger-mcp"}}}

This module is a thin wrapper: every operation lives in ``service.py`` and is
tested without the optional ``mcp`` dependency. Tool docstrings are the agent's
documentation — they are what the model reads before calling.

Note: annotations here are deliberately ``Optional[...]`` rather than PEP 604
``X | None``. FastMCP evaluates type hints at runtime to build tool schemas,
and PEP 604 syntax fails on Python 3.9, which this package still supports.
"""

from typing import Optional

from . import service

try:  # mcp >= 2.0 renamed FastMCP to MCPServer; keep 1.x working too
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - depends on the installed mcp generation
    try:
        # Same name on purpose — one alias, whichever generation is installed.
        # mypy checks both branches and sees a redefinition; at runtime only one
        # of them ever binds.
        from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    except ImportError as exc:
        raise SystemExit(
            "The MCP server needs the optional dependency:\n"
            '    pip install -e ".[mcp]"   # not on PyPI yet'
        ) from exc

mcp = _Server("finding-ledger")


@mcp.tool()
def list_projects(registry: Optional[str] = None) -> list:
    """List every project in the registry with headline numbers.

    Start here when you don't know which projects exist. Returns name, root
    path, open/critical item counts and audit count for each.
    """
    return service.list_projects(registry)


@mcp.tool()
def project_status(project: str, registry: Optional[str] = None,
                   escalate_at: int = 3) -> dict:
    """Headline state of one project: item counts, escalation candidates,
    golden-case statuses, audit count and quality trend.

    Call this before writing anything — it shows which signatures already
    exist, so you reuse them instead of creating duplicates.
    """
    return service.project_status(project, registry, escalate_at)


@mcp.tool()
def ledger_items(project: str, status: str = "open", min_count: int = 0,
                 registry: Optional[str] = None) -> list:
    """Ledger items, most-recurring first.

    status: "open" (default), "closed", "critical" or "all".
    min_count filters by occurrence count (the priority weight).
    """
    return service.ledger_items(project, status, min_count, registry)


@mcp.tool()
def merge_findings(project: str, findings: list, registry: Optional[str] = None,
                   escalate_at: int = 3) -> dict:
    """Record audit findings in the ledger, deduplicated by root-cause signature.

    Each finding is an object with at least `signature` and `date`; optionally
    `title`, `severity` (critical|major|minor), `class`, `symptom`, `evidence`,
    `root_cause`, `fix_direction`, `verification`.

    The signature must name the MECHANISM, not the symptom
    (e.g. "B1-detail-beyond-source"), so the same defect found on another day
    bumps one counter instead of creating a second item. Merging the same
    finding twice is a safe no-op.

    Returns created/updated/unchanged signatures plus `escalation_due` — items
    that reached the threshold. Report those to the human; never re-prioritize
    on your own. Writes to the project's ledger file.
    """
    return service.merge_findings(project, findings, registry, escalate_at)


@mcp.tool()
def mark_fixed(project: str, signature: str, note: str = "",
               registry: Optional[str] = None) -> dict:
    """Close a ledger item after its fix landed. Put the commit or change in
    `note`. History (occurrences, evidence) is preserved. Writes to the ledger.
    """
    return service.mark_fixed(project, signature, note, registry)


@mcp.tool()
def retract_finding(project: str, signature: str, note: str = "",
                    registry: Optional[str] = None) -> dict:
    """Mark an item as a false positive of the audit.

    Use this instead of deleting — the claim stays visible with its history so
    the audit's own error rate remains auditable. Writes to the ledger.
    """
    return service.retract_finding(project, signature, note, registry)


@mcp.tool()
def graduate_case(project: str, case_id: str, registry: Optional[str] = None) -> dict:
    """Flip a golden case from `open` to `regression` once its fix has landed.

    From then on a failure of that case is an alarm: the defect came back.
    Writes to the case's YAML file.
    """
    return service.graduate_case(project, case_id, registry)


@mcp.tool()
def check_cases(project: str, results: dict, registry: Optional[str] = None) -> dict:
    """Confront golden cases with assertion results.

    `results` maps a case's check-id (or case id) to "PASS" or "FAIL".
    A failing `open` case is expected (status quo, not an alarm); failing
    `regression`/`sanity` cases are alarms. Also returns graduation
    candidates: `open` cases that now pass. Read-only.
    """
    return service.check_cases(project, results, registry)


@mcp.tool()
def audit_trend(project: str, window: int = 7, registry: Optional[str] = None) -> dict:
    """Quality trend from audit-file frontmatter: the last `window` audits
    against the preceding ones, per severity. Negative deltas mean improving.
    Read-only.
    """
    return service.audit_trend(project, window, registry)


@mcp.tool()
def build_report(out: str = "findingledger-report.html",
                 projects: Optional[list] = None,
                 registry: Optional[str] = None) -> dict:
    """Render the self-contained multi-project HTML report (one file, no
    server). Omit `projects` to include everything in the registry.
    """
    return service.make_report(out, projects, registry)


@mcp.tool()
def project_paths(project: str, registry: Optional[str] = None) -> dict:
    """Where a project keeps its ledger, cases and audits, plus its alert
    settings — read from its `findingledger.yaml`.

    Call this when you need the actual file paths (to read a ledger yourself,
    or to point a test runner at the case directory). Read-only.
    """
    return service.project_paths(project, registry)


@mcp.tool()
def check_results_file(project: str, path: str, fmt: str = "auto",
                       registry: Optional[str] = None) -> dict:
    """Confront the golden cases with a results file written by a test runner.

    `fmt`: "auto" (sniff the file), "promptfoo", "deepeval", "junit", "ragas"
    or "json" (a plain {check_id: PASS|FAIL} map). Use this after running
    promptfoo/pytest/DeepEval instead of transcribing results by hand.

    A failing `open` case is expected (the test was written before the fix);
    failing `regression`/`sanity` cases are alarms. Read-only.
    """
    return service.check_results_file(project, path, fmt, registry)


@mcp.tool()
def import_tool_output(project: str, path: str, fmt: str = "auto",
                       date: Optional[str] = None, prefix: str = "",
                       dry_run: bool = True, registry: Optional[str] = None) -> dict:
    """Turn a tool's failures into ledger findings (promptfoo, DeepEval, JUnit,
    Ragas metrics, OTLP spans, Langfuse traces).

    Defaults to `dry_run=True` on purpose: derived signatures name the SYMPTOM
    ("promptfoo-gc-07"), and a good ledger names the MECHANISM. Review them,
    rename what you can, then call again with `dry_run=false` — or merge your
    corrected list through `merge_findings`.
    """
    return service.import_tool_output(project, path, fmt, date, prefix, dry_run, registry)


@mcp.tool()
def alert(project: str, audit: Optional[str] = None, results: Optional[str] = None,
          min_severity: str = "critical", dry_run: bool = True,
          registry: Optional[str] = None) -> dict:
    """Decide whether something is worth notifying a human about, and send it
    through the project's configured channel.

    Source: an `audit` file's frontmatter, a `results` file (a failing
    regression case always alerts), or the ledger's current critical items.
    Returns `alerted: false` when nothing reaches `min_severity` — the normal
    outcome. Defaults to `dry_run=true`; sending is a side effect, so ask the
    human before flipping it.
    """
    return service.alert(project, audit=audit, results=results,
                         min_severity=min_severity, dry_run=dry_run, registry=registry)


@mcp.tool()
def quality_scores(project: str, registry: Optional[str] = None) -> list:
    """Lifecycle state as observability score payloads (Langfuse shape).

    Open items, criticals, escalations due, cases by status and the trend
    verdict — ready to attach to a trace so quality sits in the same dashboard
    as cost and latency. Read-only: this builds the payloads, it does not send
    them (that is `findingledger scores --project ... --trace-id ...`).
    """
    return service.score_payloads(project, registry)


def main() -> None:
    """Entry point for `findingledger-mcp` (stdio transport)."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
