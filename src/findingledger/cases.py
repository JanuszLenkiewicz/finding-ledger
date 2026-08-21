"""Tri-state golden cases and their lifecycle.

The tri-state status is the crux of the method — it lets you write the test
*before* the fix without breaking CI:

* ``open``       — expected-fail bound to an unfixed ledger item; FAIL = status quo.
* ``regression`` — the defect was fixed; FAIL = it came back: alarm.
* ``sanity``     — structural invariant; FAIL = alarm.

Graduation (``open`` → ``regression``) happens when the fix lands and the case
first passes. Cases are YAML files (one per case) so each has its own git
history and merge granularity.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Case, CaseOutcome


def load_cases(cases_dir: Path) -> list[Case]:
    cases: list[Case] = []
    for p in sorted(Path(cases_dir).glob("*.yaml")) + sorted(Path(cases_dir).glob("*.yml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        backlog = data.get("backlog")
        cases.append(Case(
            id=data.get("id", p.stem),
            status=data.get("status", "sanity"),
            path=str(p),
            check=data.get("check"),
            rubric=data.get("rubric"),
            backlog=None if backlog in (None, "", "—", "-") else str(backlog),
            since=str(data["since"]) if data.get("since") else None,
            notes=str(data.get("notes", "")).strip(),
        ))
    ids = [c.id for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate case ids: {sorted(dupes)}")
    return cases


def evaluate(cases: list[Case], results: dict[str, str]) -> list[CaseOutcome]:
    """Confront cases with assertion results (``{check_id: "PASS"|"FAIL"}``).

    Rubric cases carry no deterministic result — they are routed to the LLM
    judge (``needs-judge``); pass the judge's verdicts in ``results`` keyed by
    case id to resolve them the same way.
    """
    outcomes: list[CaseOutcome] = []
    for c in cases:
        key = c.check if c.check else c.id
        res = results.get(key, "SKIP")
        if res == "SKIP":
            action = "needs-judge" if c.rubric else "ok"
        elif res == "FAIL":
            action = "status-quo" if c.status == "open" else "alarm"
        else:  # PASS
            action = "graduation-candidate" if c.status == "open" else "ok"
        outcomes.append(CaseOutcome(case=c, result=res, action=action))
    return outcomes


def alarms(outcomes: list[CaseOutcome]) -> list[CaseOutcome]:
    return [o for o in outcomes if o.action == "alarm"]


def graduate(cases_dir: Path, case_id: str) -> Case:
    """Flip an ``open`` case to ``regression`` (surgical edit of its YAML)."""
    for c in load_cases(cases_dir):
        if c.id != case_id:
            continue
        if c.status != "open":
            raise ValueError(f"case {case_id} has status {c.status!r}; only 'open' graduates")
        p = Path(c.path)
        lines = p.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            if ln.strip().startswith("status:"):
                lines[i] = ln.replace("open", "regression", 1)
                break
        else:
            raise ValueError(f"case file {p} has no status line")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        c.status = "regression"
        return c
    raise KeyError(f"no case with id {case_id!r}")


def case_for_signature(cases: list[Case], signature: str) -> Case | None:
    return next((c for c in cases if c.backlog == signature), None)
