"""GitHub adapter — the underrated integration.

Because the ledger is markdown in git rather than rows in a database, GitHub
gives you for free what no tool in this space sells:

* quality history *is* commit history — ``git blame`` on an entry shows who
  filed it and when;
* a status change (``open`` → ``regression``, or a RETRACTED false positive) is
  reviewable in a pull request, with comments;
* the HTML report can be committed or served from GitHub Pages — a public
  quality dashboard with zero servers;
* ``findingledger check`` exits 1 only on a real alarm, so it drops into a
  workflow as an ordinary step and becomes a merge gate.

This module is the polish on top: inline annotations on the PR diff, a job
summary that renders as a table, and step outputs other jobs can branch on. All
of it is the documented Actions text protocol — no API token, no network.

See ``action.yml`` at the repository root for the ready-made composite action,
and ``examples/ci/`` for complete workflows.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

MAX_ANNOTATIONS = 20


def in_actions(env: Mapping[str, str] | None = None) -> bool:
    return (env if env is not None else os.environ).get("GITHUB_ACTIONS") == "true"


def _escape(text: str, in_property: bool = False) -> str:
    """Escape per the workflow-command protocol (``%``, CR, LF, and ``:``/``,``)."""
    out = str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if in_property:
        out = out.replace(":", "%3A").replace(",", "%2C")
    return out


def annotation_lines(check: Mapping[str, Any], project: str = "") -> list[str]:
    """Workflow commands for a ``check`` result: errors for alarms, notices for
    graduation candidates (an ``open`` case that now passes is good news that
    needs a human action — flipping it to ``regression``)."""
    lines: list[str] = []
    for row in list(check.get("alarms") or [])[:MAX_ANNOTATIONS]:
        title = _escape(f"finding-ledger alarm: {row.get('id')}", in_property=True)
        msg = _escape(f"{row.get('status')} case {row.get('id')} FAILED — the defect is back "
                      f"(ledger item: {row.get('backlog') or 'none'})")
        file_prop = f"file={_escape(str(row.get('path')), in_property=True)}," \
            if row.get("path") else ""
        lines.append(f"::error {file_prop}title={title}::{msg}")
    for row in list(check.get("graduation_candidates") or [])[:MAX_ANNOTATIONS]:
        title = _escape(f"finding-ledger: {row.get('id')} is ready to graduate",
                        in_property=True)
        msg = _escape(f"open case {row.get('id')} now passes — run `findingledger graduate "
                      f"--cases <dir> {row.get('id')}` to make it a permanent guard")
        lines.append(f"::notice title={title}::{msg}")
    if project and not lines:
        lines.append(f"::notice title=finding-ledger::{_escape(project)}: no alarms")
    return lines


def summary_markdown(check: Mapping[str, Any] | None = None,
                     status: Mapping[str, Any] | None = None,
                     project: str = "") -> str:
    """Job-summary markdown: the gate verdict, then the lifecycle state."""
    parts: list[str] = [f"## finding-ledger — {project or 'quality gate'}"]

    if check is not None:
        outcomes = list(check.get("outcomes") or [])
        alarms = list(check.get("alarms") or [])
        grads = list(check.get("graduation_candidates") or [])
        verdict = "🔴 **ALARM** — a regression or sanity case failed" if alarms else \
            "🟢 **PASS** — no regression or sanity case failed"
        parts += [verdict, ""]
        counts: dict[str, int] = {}
        for o in outcomes:
            counts[str(o.get("status"))] = counts.get(str(o.get("status")), 0) + 1
        if counts:
            header_results = sorted({str(o.get("result")) for o in outcomes})
            parts.append("| Case status | Cases | " + " | ".join(header_results) + " |")
            parts.append("|---" * (2 + len(header_results)) + "|")
            for st in sorted(counts):
                cells = [str(sum(1 for o in outcomes
                                 if str(o.get("status")) == st and str(o.get("result")) == r))
                         for r in header_results]
                parts.append(f"| {st} | {counts[st]} | " + " | ".join(cells) + " |")
            parts.append("")
        if alarms:
            parts.append("### Alarms")
            parts += [f"- `{a.get('id')}` ({a.get('status')}) — ledger item "
                      f"`{a.get('backlog') or '—'}`" for a in alarms]
            parts.append("")
        if grads:
            parts.append("### Ready to graduate (open → regression)")
            parts += [f"- `{g.get('id')}`" for g in grads]
            parts.append("")
        if not alarms:
            parts.append("> A failing `open` case is expected — the test was written before "
                         "the fix, so it is allowed to be red without breaking the build.")
            parts.append("")

    if status is not None:
        parts += [
            "### Ledger",
            "| Metric | Value |", "|---|---|",
            f"| Open items | {status.get('open_items', 0)} |",
            f"| Critical (open) | {status.get('critical_open', 0)} |",
            f"| At escalation threshold | {len(status.get('escalation_candidates') or [])} |",
            f"| Audits | {status.get('audits', 0)} (last: {status.get('last_audit') or '—'}) |",
            "",
        ]
        for item in (status.get("escalation_candidates") or [])[:10]:
            parts.append(f"- ⚠️ `{item.get('signature')}` reached {item.get('count')}× — "
                         "decide whether to escalate (this library never re-prioritizes "
                         "on its own)")
    return "\n".join(parts).rstrip() + "\n"


def _append_env_file(var: str, text: str, env: Mapping[str, str] | None = None) -> str | None:
    target = (env if env is not None else os.environ).get(var)
    if not target:
        return None
    with Path(target).open("a", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    return target


def write_summary(markdown: str, env: Mapping[str, str] | None = None) -> str | None:
    """Append to ``$GITHUB_STEP_SUMMARY``; returns None outside Actions."""
    return _append_env_file("GITHUB_STEP_SUMMARY", markdown, env)


def set_output(name: str, value: Any, env: Mapping[str, str] | None = None) -> str | None:
    """Set a step output via ``$GITHUB_OUTPUT`` (heredoc form, multiline-safe)."""
    text = str(value)
    if "\n" in text:
        body = f"{name}<<FINDINGLEDGER_EOF\n{text}\nFINDINGLEDGER_EOF"
    else:
        body = f"{name}={text}"
    return _append_env_file("GITHUB_OUTPUT", body, env)


def emit(check: Mapping[str, Any] | None = None, status: Mapping[str, Any] | None = None,
         project: str = "", stream: Any = None,
         env: Mapping[str, str] | None = None) -> dict:
    """Do the whole Actions dance: annotations, job summary, step outputs.

    Safe to call anywhere — outside Actions the annotations simply print and
    the file writes are skipped, so a local run of the same command behaves
    identically minus the decoration.
    """
    import sys
    out = stream or sys.stdout
    lines = annotation_lines(check or {}, project)
    for ln in lines:
        print(ln, file=out)
    markdown = summary_markdown(check, status, project)
    written = write_summary(markdown, env)
    outputs = {
        "has_alarms": "true" if (check or {}).get("has_alarms") else "false",
        "alarms": str(len((check or {}).get("alarms") or [])),
        "graduation_candidates": str(len((check or {}).get("graduation_candidates") or [])),
    }
    if status is not None:
        outputs["open_items"] = str(status.get("open_items", 0))
        outputs["critical_open"] = str(status.get("critical_open", 0))
    for k, v in outputs.items():
        set_output(k, v, env)
    return {"annotations": lines, "summary": markdown, "summary_file": written,
            "outputs": outputs}


def pr_comment_body(check: Mapping[str, Any] | None = None,
                    status: Mapping[str, Any] | None = None, project: str = "",
                    marker: str = "<!-- finding-ledger -->") -> str:
    """Markdown for a sticky PR comment (post it with ``gh pr comment``).

    The marker line lets a workflow find and update its previous comment
    instead of adding one per push.
    """
    return f"{marker}\n{summary_markdown(check, status, project)}"


def annotate_ledger_items(items: Sequence[Mapping[str, Any]], ledger_path: str = "",
                          ) -> list[str]:
    """Notices for items that reached the escalation threshold."""
    out = []
    for item in list(items)[:MAX_ANNOTATIONS]:
        title = _escape(f"finding-ledger: {item.get('signature')} reached "
                        f"{item.get('count')}×", in_property=True)
        msg = _escape(f"{item.get('title')} — recurring defect at the escalation threshold; "
                      "a human decides whether it becomes CRITICAL")
        file_prop = f"file={_escape(ledger_path, in_property=True)}," if ledger_path else ""
        out.append(f"::warning {file_prop}title={title}::{msg}")
    return out
