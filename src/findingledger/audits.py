"""Observability for free: audit files carry machine-readable YAML frontmatter
(``n_findings``, ``by_severity``, ``by_class``); aggregating them over time
yields a quality trend and a chronological hub table without any extra
infrastructure — the git repo *is* the metrics store.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .models import AuditSummary

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_audit(path: Path) -> AuditSummary | None:
    text = Path(path).read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    data = yaml.safe_load(m.group(1)) or {}
    name_date = DATE_IN_NAME.search(Path(path).name)
    date = str(data.get("date") or (name_date.group(1) if name_date else ""))
    known = {"date", "n_findings", "by_severity", "by_class"}
    return AuditSummary(
        date=date,
        path=str(path),
        n_findings=int(data.get("n_findings") or 0),
        by_severity={str(k): int(v or 0) for k, v in (data.get("by_severity") or {}).items()},
        by_class={str(k): int(v or 0) for k, v in (data.get("by_class") or {}).items()},
        extra={k: v for k, v in data.items() if k not in known},
    )


def load_audits(audits_dir: Path, pattern: str = "*-audit.md") -> list[AuditSummary]:
    out = []
    for p in sorted(Path(audits_dir).glob(pattern)):
        s = parse_audit(p)
        if s:
            out.append(s)
    return sorted(out, key=lambda s: s.date)


def render_hub(summaries: list[AuditSummary]) -> str:
    """Chronological hub table (markdown) — drop-in for a hub page."""
    lines = ["| Date | Findings (crit/maj/min) | Dominant class | File |",
             "|---|---|---|---|"]
    for s in summaries:
        sev = s.by_severity
        trio = f"{sev.get('critical', 0)}/{sev.get('major', 0)}/{sev.get('minor', 0)}"
        dom = max(s.by_class, key=lambda k: s.by_class[k]) if any(s.by_class.values()) else "—"
        lines.append(f"| {s.date} | {trio} | {dom} | {Path(s.path).name} |")
    return "\n".join(lines)


def trend(summaries: list[AuditSummary], window: int = 7) -> dict:
    """Compare the last ``window`` audits against the preceding ``window``.

    Returns per-severity deltas; positive delta = quality degrading. Naive on
    purpose: with audits in git, fancier analytics belong in a notebook, not
    in the loop.
    """
    recent, previous = summaries[-window:], summaries[-2 * window:-window]

    def totals(rows: list[AuditSummary]) -> dict:
        agg = {"critical": 0, "major": 0, "minor": 0, "n": len(rows)}
        for r in rows:
            for k in ("critical", "major", "minor"):
                agg[k] += r.by_severity.get(k, 0)
        return agg

    r, p = totals(recent), totals(previous)
    has_baseline = p["n"] > 0
    return {
        "recent": r,
        "previous": p,
        # Deltas are meaningless without a previous window to compare against;
        # `has_baseline` tells callers whether `degrading` carries any signal.
        "delta": ({k: r[k] - p[k] for k in ("critical", "major", "minor")}
                  if has_baseline else {}),
        "has_baseline": has_baseline,
        "degrading": any(r[k] > p[k] for k in ("critical", "major")) if has_baseline else None,
    }
