"""Markdown-backed backlog ledger.

The ledger is a plain markdown file kept in git — human-readable, PR-reviewable,
merge-conflict-friendly. Item headers follow the convention::

    ### [signature] STATUS — Title

Field bullets inside an item are free-form; the library recognizes (in English
and Polish) only the bullets it must edit surgically:

* occurrences  — ``- **Occurrences:** 3× (2026-08-08, 2026-08-07, ...)``
  (aliases: ``Wystąpienia``)

Everything else is preserved verbatim. Design rule: *the library performs
surgical edits on a document owned by humans* — it never regenerates the file
from a model, so hand-written context can never be lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import Finding, LedgerItem

ITEM_RE = re.compile(
    r"^###\s+\[(?P<sig>[^\]\s]+)\]\s+(?P<status>\S+(?:\s+\S+)*?)\s+—\s+(?P<title>.*)$")


def _split_status_title(rest: str) -> tuple[str, str]:
    """Split "STATUS ... — Title" at the em dash that separates status from title.

    The status half often carries a parenthesised note that itself contains an em
    dash ("FIXED 2026-08-26 (same day as the audit, commit abc — reported by X) — Title").
    A non-greedy regex stops at the FIRST dash, which lands inside the parentheses and
    leaves the title starting with the tail of that note plus an orphan ")".

    So: take the first separator at which the status half has balanced parentheses.
    Falls back to the first separator when nothing balances (malformed header).
    """
    sep = " — "
    first: tuple[str, str] | None = None
    idx = rest.find(sep)
    while idx != -1:
        left, right = rest[:idx], rest[idx + len(sep):]
        if first is None:
            first = (left.strip(), right.strip())
        if left.count("(") == left.count(")"):
            return left.strip(), right.strip()
        idx = rest.find(sep, idx + len(sep))
    return first if first is not None else (rest.strip(), "")
OCC_KEYS = ("Occurrences", "Wystąpienia", "Wystapienia")
# Read path: matches the bullet key (with optional suffix words, e.g.
# "Wystąpienia w biuletynach") and leaves the rest of the line free-form.
# Deliberately does NOT require a "N×" token — a hand-written line can be a
# continuous-period note with no count at all ("ciągłe od 2026-08-04; ...").
OCC_LINE_RE = re.compile(
    r"^(?P<prefix>\s*-\s+\*\*(?:{})[^:*]*:\*\*\s*)(?P<rest>.*)$".format("|".join(OCC_KEYS)))
# Write path (surgical edits only, see `_bump_occurrence`): unchanged from the
# original parser — bumping always writes a "N×" token, so requiring one here
# keeps the edit path's line-matching behavior identical.
OCC_RE = re.compile(
    r"^(?P<prefix>\s*-\s+\*\*(?:{})[^:*]*:\*\*\s*)(?P<rest>.*\d+×.*)$".format("|".join(OCC_KEYS)))
COUNT_RE = re.compile(r"(\d+)×")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SECTION_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$")


def _dedupe_dates(dates: list[str]) -> list[str]:
    """Deduplicate dates while preserving order of first appearance.

    Hand-written occurrence lines are prose, not a clean list: a date is
    routinely restated while describing a multi-day pattern (e.g. "2026-08-04
    i 2026-08-06 — dwa dni z rzędu ...") or repeated inside a trailing
    evidence path ("Dowód: .../2026-08-20-audit.md#F7"). Counting raw
    ``DATE_RE.findall`` results without dedup inflates `len(occurrences)` well
    past the number of distinct days actually on record.
    """
    seen: set[str] = set()
    out: list[str] = []
    for d in dates:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out

DEFAULT_OPEN_SECTIONS = ("Open", "Otwarte")
DEFAULT_ESCALATE_AT = 3


@dataclass
class MergeReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)   # same date already recorded
    escalation_due: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)


class Ledger:
    """Parse, surgically edit and write back a markdown backlog."""

    def __init__(self, path: Path, escalate_at: int = DEFAULT_ESCALATE_AT):
        self.path = Path(path)
        self.escalate_at = escalate_at
        self.lines: list[str] = []
        self.items: dict[str, LedgerItem] = {}
        self._spans: dict[str, tuple] = {}      # signature -> (start, end) line span
        if self.path.exists():
            self._parse(self.path.read_text(encoding="utf-8"))

    # ── parsing ────────────────────────────────────────────────────────────
    def _parse(self, text: str) -> None:
        self.lines = text.splitlines()
        self.items, self._spans = {}, {}
        section = ""
        current: LedgerItem | None = None
        start = 0
        for i, ln in enumerate(self.lines):
            sec = SECTION_RE.match(ln)
            item = ITEM_RE.match(ln)
            if (sec or item) and current is not None:
                self._close_item(current, start, i)
                current = None
            if sec:
                section = sec.group("name")
                continue
            if item:
                status, title = _split_status_title(
                    f'{item.group("status")} — {item.group("title")}')
                current = LedgerItem(signature=item.group("sig"),
                                     status=status,
                                     title=title,
                                     section=section)
                start = i
        if current is not None:
            self._close_item(current, start, len(self.lines))

    def _close_item(self, item: LedgerItem, start: int, end: int) -> None:
        item.body = self.lines[start:end]
        for ln in item.body:
            m = OCC_LINE_RE.match(ln)
            if m:
                item.occurrences = _dedupe_dates(DATE_RE.findall(m.group("rest")))
                count_match = COUNT_RE.search(m.group("rest"))
                # No "N×" at all (e.g. a continuous-period note) means no
                # declared count — LedgerItem.count then falls back to the
                # number of unique dates instead of treating 0 as authoritative.
                item.declared_count = int(count_match.group(1)) if count_match else 0
                break
        if item.signature in self.items:
            raise ValueError(f"duplicate signature in ledger: {item.signature}")
        self.items[item.signature] = item
        self._spans[item.signature] = (start, end)

    # ── queries ────────────────────────────────────────────────────────────
    def get(self, signature: str) -> LedgerItem | None:
        return self.items.get(signature)

    def open_items(self) -> list[LedgerItem]:
        return [i for i in self.items.values()
                if not i.status.upper().startswith(("FIXED", "RETRACTED", "✅"))]

    # ── surgical edits ─────────────────────────────────────────────────────
    def merge(self, findings: list[Finding]) -> MergeReport:
        """Deduplicate findings into the ledger by signature.

        Existing item: prepend the finding date to the occurrences bullet and
        bump the counter (same date twice = no-op). New item: render a fresh
        ``###`` block into the first open section. Items at/over the
        escalation threshold are reported, **not** auto-escalated — severity
        changes are a human (or reviewing-agent) decision. Write-little policy.
        """
        report = MergeReport()
        for f in findings:
            item = self.items.get(f.signature)
            if item is None:
                self._append_new(f)
                report.created.append(f.signature)
                item = self.items[f.signature]
            elif f.date in item.occurrences:
                report.unchanged.append(f.signature)
            else:
                self._bump_occurrence(item, f.date)
                report.updated.append(f.signature)
                item = self.items[f.signature]  # _bump reparses; refetch before threshold check
            if item.count >= self.escalate_at and not item.status.upper().startswith(
                    ("CRITICAL", "FIXED", "RETRACTED", "🔴", "✅")):
                report.escalation_due.append(f.signature)
        return report

    def _bump_occurrence(self, item: LedgerItem, date: str) -> None:
        start, end = self._spans[item.signature]
        for i in range(start, end):
            m = OCC_RE.match(self.lines[i])
            if m:
                rest = m.group("rest")
                dates = DATE_RE.findall(rest)
                if date in dates:
                    return
                count_match = COUNT_RE.search(rest)
                # OCC_RE only matches lines whose `rest` contains a "N×" token
                # (see its pattern above), so this search always succeeds here.
                assert count_match is not None
                new_count = max(int(count_match.group(1)), len(dates)) + 1
                rest = COUNT_RE.sub(f"{new_count}×", rest, count=1)
                if dates:
                    rest = rest.replace(dates[0], f"{date}, {dates[0]}", 1)
                else:
                    rest += f" ({date})"
                self.lines[i] = f"{m.group('prefix')}{rest}"
                self._parse("\n".join(self.lines))
                return
        # item had no occurrences bullet — add one right under the header
        self.lines.insert(start + 1, f"- **Occurrences:** 1× ({date})")
        self._parse("\n".join(self.lines))

    def _append_new(self, f: Finding) -> None:
        sev_token = {"critical": "CRITICAL", "major": "OPEN", "minor": "NICE"}[f.severity]
        block = [f"### [{f.signature}] {sev_token} — {f.title or f.signature}"]
        if f.klass or f.severity:
            block.append(f"- **Class:** {f.klass or '—'} | **Severity:** {f.severity}")
        block.append(f"- **Occurrences:** 1× ({f.date})")
        for label, value in (("Symptom", f.symptom), ("Evidence", f.evidence),
                             ("Root-cause", f.root_cause),
                             ("Fix direction", f.fix_direction),
                             ("Verification", f.verification)):
            if value:
                block.append(f"- **{label}:** {value}")
        block.append("")
        idx = self._open_section_end()
        # Markdown needs a blank line before a heading; hand-written ledgers do
        # not always end an item with one, so add it rather than assume it.
        if idx > 0 and self.lines[idx - 1].strip():
            block.insert(0, "")
        self.lines[idx:idx] = block
        self._parse("\n".join(self.lines))

    def _open_section_end(self) -> int:
        """Index right after the last line of the first open section (before the
        next ``##`` heading), creating the section if the file lacks one."""
        open_start = None
        for i, ln in enumerate(self.lines):
            m = SECTION_RE.match(ln)
            if m and open_start is None and m.group("name") in DEFAULT_OPEN_SECTIONS:
                open_start = i
            elif m and open_start is not None:
                return i
        if open_start is not None:
            return len(self.lines)
        self.lines += (["## Open", ""] if not self.lines else ["", "## Open", ""])
        return len(self.lines)

    def retract(self, signature: str, note: str = "") -> None:
        """Mark an item as a false positive of the audit — first-class citizen.

        The item stays in place with full history; only the status token flips
        to RETRACTED and an explanatory bullet is added.
        """
        item = self.items.get(signature)
        if item is None:
            raise KeyError(f"no ledger item with signature {signature!r}")
        start, _ = self._spans[signature]
        self.lines[start] = f"### [{signature}] RETRACTED — {item.title}"
        suffix = f": {note}" if note else ""
        self.lines.insert(start + 1, f"- **Retracted:** false positive of the audit{suffix}")
        self._parse("\n".join(self.lines))

    def mark_fixed(self, signature: str, note: str = "") -> None:
        item = self.items.get(signature)
        if item is None:
            raise KeyError(f"no ledger item with signature {signature!r}")
        start, _ = self._spans[signature]
        self.lines[start] = f"### [{signature}] FIXED — {item.title}"
        if note:
            self.lines.insert(start + 1, f"- **Fixed:** {note}")
        self._parse("\n".join(self.lines))

    # ── io ─────────────────────────────────────────────────────────────────
    def save(self) -> None:
        self.path.write_text("\n".join(self.lines).rstrip("\n") + "\n", encoding="utf-8")
