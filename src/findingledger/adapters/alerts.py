"""Alert dispatch — deterministic, critical-only.

Two rules, both learned the hard way.

**1. Alert only on critical findings.** Everything else waits in the ledger.
Notify on every trifle and within a week nobody reads the notifications; the
system then costs attention instead of saving it.

**2. If a step must happen, it cannot depend on a model's attention.** A
production LLM auditor was instructed to send an alert when it found a critical
defect. It found two, wrote them down correctly — and never sent the alert. The
fix was not a better prompt: the send moved into a script that reads the
audit's frontmatter and fires unconditionally. That script is this module, and
that is why it is deterministic code with no model in the path.

Wire it as the step *after* the audit, not inside it::

    findingledger alert --audit audits/2026-08-08-audit.md --project newsletter \\
        --channel command --command "telegram-send --stdin"

Channels: ``stdout`` (default), ``file``, ``webhook`` (Slack/Discord/any JSON
endpoint), ``command`` (any CLI that reads stdin — Telegram bots, ``osascript``,
``notify-send``), ``none``. All stdlib; no client library for any of them.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import SEVERITIES

CHANNELS = ("stdout", "file", "webhook", "command", "none")
#: Severity ordering used by the ``min_severity`` gate (lower index = louder).
RANK = {sev: i for i, sev in enumerate(SEVERITIES)}


@dataclass
class Alert:
    """One alert, already decided: if you hold an Alert, it is worth sending."""

    project: str
    level: str                                    # critical | major | minor
    title: str
    lines: list[str] = field(default_factory=list)
    source: str = ""                              # audit path, "check", "merge"
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"project": self.project, "level": self.level, "title": self.title,
                "lines": list(self.lines), "source": self.source, "counts": dict(self.counts)}


def _passes(level: str, min_severity: str) -> bool:
    return RANK.get(level, 99) <= RANK.get(min_severity, 0)


def from_audit(summary: Any, project: str = "", min_severity: str = "critical",
               ) -> Alert | None:
    """Alert from one audit file's frontmatter — the unconditional path.

    ``summary`` is a :class:`~findingledger.models.AuditSummary` (or anything
    with ``date``/``n_findings``/``by_severity``). Returns ``None`` when nothing
    reaches ``min_severity`` — silence is the correct output most days.
    """
    by_sev = dict(getattr(summary, "by_severity", None) or {})
    level = next((s for s in SEVERITIES if by_sev.get(s)), "")
    if not level or not _passes(level, min_severity):
        return None
    date = getattr(summary, "date", "")
    n_crit = int(by_sev.get("critical", 0) or 0)
    lines = [f"audit {date}: {getattr(summary, 'n_findings', 0)} finding(s)",
             " · ".join(f"{k}: {v}" for k, v in by_sev.items() if v)]
    path = getattr(summary, "path", "")
    if path:
        lines.append(f"file: {path}")
    head = f"{n_crit} critical finding(s)" if n_crit else f"{level} finding(s)"
    return Alert(project=project, level=level, title=f"{project or 'audit'}: {head}",
                 lines=[ln for ln in lines if ln], source=str(path or "audit"),
                 counts={k: int(v or 0) for k, v in by_sev.items()})


def from_findings(findings: Iterable[Any], project: str = "",
                  min_severity: str = "critical") -> Alert | None:
    """Alert from a batch of findings (objects or dicts) about to be merged."""
    rows = []
    for f in findings:
        if isinstance(f, Mapping):
            rows.append((str(f.get("severity", "major")), str(f.get("signature", "")),
                         str(f.get("title", ""))))
        else:
            rows.append((getattr(f, "severity", "major"), getattr(f, "signature", ""),
                         getattr(f, "title", "")))
    counts: dict[str, int] = {}
    for sev, _, _ in rows:
        counts[sev] = counts.get(sev, 0) + 1
    level = next((s for s in SEVERITIES if counts.get(s)), "")
    if not level or not _passes(level, min_severity):
        return None
    loud = [f"[{sev}] {sig} — {title or sig}" for sev, sig, title in rows
            if _passes(sev, min_severity)]
    return Alert(project=project, level=level,
                 title=f"{project or 'audit'}: {len(loud)} {level} finding(s)",
                 lines=loud[:10], source="findings", counts=counts)


def from_check(result: Mapping[str, Any], project: str = "") -> Alert | None:
    """Alert from a ``check`` result — a failing regression/sanity case.

    This one ignores ``min_severity``: a regression case failing *means* the
    defect came back, which is critical by definition.
    """
    alarms = list(result.get("alarms") or [])
    if not alarms:
        return None
    lines = [f"{a.get('id')} [{a.get('status')}] FAIL — backlog: {a.get('backlog') or '—'}"
             for a in alarms]
    return Alert(project=project, level="critical",
                 title=f"{project or 'cases'}: {len(alarms)} regression/sanity case(s) failing",
                 lines=lines[:10], source="check", counts={"critical": len(alarms)})


def render(alert: Alert, fmt: str = "text") -> str:
    """Render for a human. ``text`` for terminals/Telegram, ``markdown`` for chat."""
    if fmt == "markdown":
        body = "\n".join(f"- {ln}" for ln in alert.lines)
        return f"**🔴 {alert.title}**\n{body}".rstrip()
    body = "\n".join(f"  {ln}" for ln in alert.lines)
    return f"[{alert.level.upper()}] {alert.title}\n{body}".rstrip()


def _post_webhook(url: str, payload: Mapping[str, Any], timeout: float = 10.0) -> int:
    if not str(url).lower().startswith(("http://", "https://")):
        raise ValueError(f"webhook url must be http(s), got {url!r}")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - scheme validated above
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "finding-ledger"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return int(getattr(resp, "status", 0) or 0)


def dispatch(alert: Alert, channel: str = "stdout", url: str = "",
             command: str | Sequence[str] = "", path: str = "", fmt: str = "text",
             dry_run: bool = False, timeout: float = 10.0) -> dict:
    """Send the alert. Returns a receipt; never raises for a channel failure.

    A failed notification must not fail the audit that produced it — the
    finding is already safe in the ledger, and a crashed cron job would lose
    the rest of the run. Errors come back in the receipt (and the caller
    decides), they do not propagate.
    """
    message = render(alert, fmt)
    receipt: dict[str, Any] = {"channel": channel, "level": alert.level,
                               "title": alert.title, "message": message, "sent": False}
    if channel not in CHANNELS:
        receipt["error"] = f"unknown channel {channel!r}; expected one of {', '.join(CHANNELS)}"
        return receipt
    if dry_run or channel == "none":
        receipt["dry_run"] = True
        return receipt

    try:
        if channel == "stdout":
            print(message)
        elif channel == "file":
            target = Path(path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(message + "\n\n")
            receipt["path"] = str(target)
        elif channel == "webhook":
            payload = {"text": render(alert, "markdown" if fmt == "markdown" else "text"),
                       **alert.to_dict()}
            receipt["status"] = _post_webhook(url, payload, timeout)
        elif channel == "command":
            argv = shlex.split(command) if isinstance(command, str) else list(command)
            if not argv:
                raise ValueError("channel 'command' needs --command")
            argv = [a.replace("{message}", message).replace("{title}", alert.title)
                    for a in argv]
            proc = subprocess.run(argv, input=message, text=True, capture_output=True,
                                  timeout=timeout, check=False)
            receipt["returncode"] = proc.returncode
            receipt["stdout"] = (proc.stdout or "").strip()[:500]
            if proc.returncode != 0:
                receipt["error"] = (proc.stderr or "").strip()[:500] or "command failed"
                return receipt
        receipt["sent"] = True
    except (OSError, ValueError, urllib.error.URLError, subprocess.SubprocessError) as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    return receipt
