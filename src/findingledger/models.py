"""Core data model of the finding lifecycle.

A *finding* is a single defect observation produced by an audit (human, LLM
auditor, or deterministic linter). Findings are merged into a *ledger* — a
human-readable markdown backlog kept in git — deduplicated by *root-cause
signature*. Occurrence count is the priority weight. A fixed defect graduates
into a *golden case* with status ``regression`` that guards it forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("critical", "major", "minor")
CASE_STATUSES = ("regression", "open", "sanity")
ITEM_STATUSES = ("OPEN", "CRITICAL", "NICE", "FIXED", "RETRACTED")


@dataclass
class Finding:
    """One defect observation, addressed by root-cause signature.

    The signature names the *mechanism*, not the symptom (e.g.
    ``LEN-01-length-drift``, not ``issue-too-long-aug-8``) — that is what makes
    the same bug on two days merge into one ledger item.
    """

    signature: str
    date: str                       # ISO date of the audited artifact
    title: str = ""
    severity: str = "major"         # critical | major | minor
    klass: str = ""                 # taxonomy class, e.g. "A2", "D1"
    symptom: str = ""
    evidence: str = ""              # literal quote + path + counterexample
    root_cause: str = ""
    fix_direction: str = ""
    verification: str = ""          # golden-case id that will guard the fix

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")
        if not self.signature or " " in self.signature:
            raise ValueError(f"signature must be a non-empty slug, got {self.signature!r}")

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        """Build a finding from the JSON/dict shape used by the CLI and MCP.

        Accepts either ``class`` (the wire name, matching the ledger bullet —
        ``class`` is a Python keyword, hence the ``klass`` attribute) or
        ``klass`` itself. Unknown keys are ignored, so a caller may pass a
        richer record through without it being rejected here.
        """
        return cls(
            signature=data["signature"], date=data["date"], title=data.get("title", ""),
            severity=data.get("severity", "major"),
            klass=data.get("class", data.get("klass", "")),
            symptom=data.get("symptom", ""), evidence=data.get("evidence", ""),
            root_cause=data.get("root_cause", ""),
            fix_direction=data.get("fix_direction", ""),
            verification=data.get("verification", ""),
        )

    def to_dict(self) -> dict:
        """The JSON/dict shape :meth:`from_dict` reads — the round trip is exact."""
        return {"signature": self.signature, "date": self.date, "title": self.title,
                "severity": self.severity, "class": self.klass, "symptom": self.symptom,
                "evidence": self.evidence, "root_cause": self.root_cause,
                "fix_direction": self.fix_direction, "verification": self.verification}


@dataclass
class LedgerItem:
    """One deduplicated backlog entry backed by a markdown ``###`` block.

    ``body`` holds the item's raw markdown lines verbatim; the library only
    performs surgical edits (occurrences line, status token, escalation note)
    so that hand-written context is never lost — the ledger stays a document
    people edit, not a database people export.
    """

    signature: str
    status: str                     # OPEN/CRITICAL/NICE/FIXED/RETRACTED (free-form tolerated)
    title: str
    body: list[str] = field(default_factory=list)
    occurrences: list[str] = field(default_factory=list)
    declared_count: int = 0         # the "N×" written in the file; date list may be partial
    section: str = "Open"

    @property
    def count(self) -> int:
        """Occurrence count. The hand-written ``N×`` wins whenever it is
        present — not because the date list undercounts, but because it can
        go either way: a list of *representative* dates only ("26× (all
        except ...)") undercounts, while free-form prose describing a pattern
        restates dates ("2026-08-04 i 2026-08-06 — dwa dni z rzędu ...";
        evidence paths like ".../2026-08-20-audit.md#F7" repeat a date already
        named in the sentence) and overcounts. A number written by a human
        beats a number mined from prose either way. Falls back to the number
        of unique dates only when the line has no ``N×`` at all — e.g. a
        "continuous since DATE" note with no declared count."""
        return self.declared_count or len(self.occurrences)


@dataclass
class Case:
    """A golden case with tri-state lifecycle.

    * ``regression`` — defect fixed; a FAIL means it came back: alarm.
    * ``open``       — expected-fail written *before* the fix; FAIL is status quo.
    * ``sanity``     — structural invariant; must always pass.

    Either ``check`` (id of a deterministic assertion) or ``rubric`` (prompt
    text for an LLM judge) is set — never both.
    """

    id: str
    status: str
    path: str = ""
    check: str | None = None
    rubric: str | None = None
    backlog: str | None = None   # ledger signature this case verifies
    since: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.status not in CASE_STATUSES:
            raise ValueError(
                f"case {self.id}: status must be one of {CASE_STATUSES}, got {self.status!r}")
        if bool(self.check) == bool(self.rubric):
            raise ValueError(f"case {self.id}: exactly one of check/rubric must be set")


@dataclass
class CaseOutcome:
    """Result of confronting one case with assertion results."""

    case: Case
    result: str                     # PASS | FAIL | SKIP (no result available)
    action: str                     # alarm | status-quo | graduation-candidate | ok | needs-judge


@dataclass
class AuditSummary:
    """Machine-readable head of one audit file (parsed from YAML frontmatter)."""

    date: str
    path: str
    n_findings: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    by_class: dict[str, int] = field(default_factory=dict)
    extra: dict[str, object] = field(default_factory=dict)
