"""Shared vocabulary for inbound adapters.

Every assertion runner says the same three things in a different dialect: *what
was tested*, *did it pass*, and *why not*. ``ToolResult`` is that common
denominator; each adapter only translates its own file format into a list of
these, and the two functions below turn the list into the two things this
library consumes:

* :func:`results_map` — ``{check_id: "PASS"|"FAIL"|"SKIP"}`` for
  :func:`findingledger.cases.evaluate` (the tri-state gate);
* :func:`to_findings` — :class:`~findingledger.models.Finding` objects for
  :meth:`findingledger.ledger.Ledger.merge` (the backlog).

Keeping the translation here means a new adapter is ~40 lines and inherits
id-aliasing, multi-provider aggregation, evidence flattening and signature
derivation for free.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any

from ..models import SEVERITIES, Finding

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

#: Keys an adapter looks at (in order) to find the golden-case id of a row.
DEFAULT_ID_KEYS = ("findingledger_case", "case_id", "case", "golden_case", "id")
#: Keys that carry an explicit ledger signature, overriding the derived one.
DEFAULT_SIGNATURE_KEYS = ("findingledger_signature", "signature", "backlog")
#: Keys that carry an explicit severity for a derived finding.
DEFAULT_SEVERITY_KEYS = ("findingledger_severity", "severity")
#: Keys that carry a taxonomy class.
DEFAULT_CLASS_KEYS = ("findingledger_class", "class", "klass")

EVIDENCE_LIMIT = 400
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class ToolResult:
    """One test outcome, normalized across runners.

    ``id`` is the primary key used to look the row up in a ledger's case files;
    ``aliases`` are the other names the same row is plausibly known by (a
    pytest nodeid, a promptfoo description, a JUnit ``classname.name``), so a
    case file matches whichever spelling its author used.
    """

    id: str
    outcome: str                                  # PASS | FAIL | SKIP
    aliases: list[str] = field(default_factory=list)
    reason: str = ""                              # why it failed, verbatim-ish
    title: str = ""
    signature: str | None = None                  # explicit ledger signature
    severity: str = "major"
    klass: str = ""
    source: str = ""                              # adapter name, for provenance
    kind: str = "case"                            # "case" | "metric" (sub-result of a case)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in (PASS, FAIL, SKIP):
            raise ValueError(f"outcome must be PASS/FAIL/SKIP, got {self.outcome!r}")
        if self.severity not in SEVERITIES:
            self.severity = "major"


def slugify(text: str, fallback: str = "case") -> str:
    """Lowercase slug safe to use as a ledger signature (no spaces)."""
    slug = _SLUG_RE.sub("-", str(text).strip().lower()).strip("-")
    return slug or fallback


def oneline(text: Any, limit: int = EVIDENCE_LIMIT) -> str:
    """Flatten to a single line and truncate.

    Ledger fields are markdown bullets: a newline inside one would split the
    item and a pipe would break a table, so evidence copied from a tool's
    failure message is normalized before it ever reaches the file.
    """
    flat = " / ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    flat = flat.replace("|", "¦").replace("**", "*")
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def first_of(mapping: Any, keys: Sequence[str]) -> Any:
    """First present, non-empty value among ``keys`` in a (possibly nested) dict."""
    if not isinstance(mapping, dict):
        return None
    for k in keys:
        v = mapping.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def read_json(path: str | Path) -> Any:
    """Read JSON or JSON Lines (a ``.jsonl`` file becomes a list of objects)."""
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith((".jsonl", ".ndjson")):
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        if not rows:
            raise
        return rows


def results_map(rows: Iterable[ToolResult], strict: bool = True) -> dict[str, str]:
    """``{check_id: outcome}``, aggregating repeated ids and adding aliases.

    A promptfoo case run against three providers yields three rows for one id.
    ``strict`` (the default) means one failure fails the case — the same
    reading a release gate wants. With ``strict=False`` a single pass is enough
    (useful when providers are alternatives rather than requirements).

    Aliases never overwrite a primary id, so an explicit ``case_id`` always
    wins over an incidentally equal test name.
    """
    primary: dict[str, str] = {}
    aliases: dict[str, str] = {}

    def combine(old: str | None, new: str) -> str:
        if old is None:
            return new
        if old == new:
            return old
        if SKIP in (old, new):
            return old if new == SKIP else new
        return FAIL if strict else PASS

    for row in rows:
        primary[row.id] = combine(primary.get(row.id), row.outcome)
        for alias in row.aliases:
            if alias and alias != row.id:
                aliases[alias] = combine(aliases.get(alias), row.outcome)

    merged = {k: v for k, v in aliases.items() if k not in primary}
    merged.update(primary)
    return merged


def to_findings(rows: Iterable[ToolResult], date: str | None = None, prefix: str = "",
                default_severity: str = "major", include: Sequence[str] = (FAIL,),
                kinds: Sequence[str] = ("case",)) -> list[Finding]:
    """Turn failing rows into ledger findings, one per distinct signature.

    The signature is the row's explicit one when the tool carries it (a
    ``signature`` var in promptfoo, a ``findingledger_signature`` property in
    JUnit) — that is the good case, because the human already named the
    *mechanism*. Otherwise it is derived from the case id, which names a
    *symptom*: usable as a starting point, but rename it by hand once you know
    the real cause. Duplicate signatures inside one run collapse into a single
    finding with merged evidence, so a run never double-counts a defect.

    Sub-results (``kind="metric"``, e.g. one DeepEval metric of a test) are
    excluded by default: they are useful as *check ids*, but filing one finding
    per failing metric would inflate the counters that carry the priority
    signal.
    """
    day = date or _date.today().isoformat()
    order: list[str] = []
    picked: dict[str, Finding] = {}
    for row in rows:
        if row.outcome not in include or (kinds and row.kind not in kinds):
            continue
        signature = row.signature or f"{prefix}{slugify(row.id)}"
        severity = row.severity if row.severity in SEVERITIES else default_severity
        if signature in picked:
            existing = picked[signature]
            if row.reason and row.reason not in existing.evidence:
                existing.evidence = oneline(f"{existing.evidence} / {row.reason}")
            continue
        order.append(signature)
        picked[signature] = Finding(
            signature=signature,
            date=day,
            title=oneline(row.title or row.id, 120),
            severity=severity,
            klass=row.klass,
            symptom=oneline(f"{row.source or 'assertion'} case {row.id} failed", 200),
            evidence=oneline(row.reason),
            verification=row.id,
        )
    return [picked[s] for s in order]
