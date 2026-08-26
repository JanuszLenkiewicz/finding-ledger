from pathlib import Path

import pytest

from findingledger import Finding, Ledger

FIXTURE = """# Mentor backlog — cumulative

> Header prose stays untouched.

## Open

### [B1-detail-beyond-source] CRITICAL — detail added under a real citation
- **Class:** B1 | **Severity:** critical
- **Occurrences:** 2× (2026-08-08, 2026-07-27)
- **Symptom:** paraphrase rounds a list up to a category.
- Hand-written extra bullet that must survive edits.

### [LEN-01-length-drift] OPEN — issues over the word limit
- **Class:** A | **Severity:** major
- **Occurrences:** 26× (2026-08-08, 2026-08-07)
- **Symptom:** no enforcement gate.

## Fixed

### [FAK-01-university] FIXED — false fact about studies
- **Occurrences:** 1× (2026-07-15)
"""


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    p = tmp_path / "backlog.md"
    p.write_text(FIXTURE, encoding="utf-8")
    return Ledger(p)


def test_parse(ledger: Ledger):
    assert set(ledger.items) == {"B1-detail-beyond-source", "LEN-01-length-drift",
                                 "FAK-01-university"}
    assert ledger.get("B1-detail-beyond-source").count == 2
    assert ledger.get("FAK-01-university").section == "Fixed"
    assert len(ledger.open_items()) == 2


def test_merge_dedup_same_date_is_noop(ledger: Ledger):
    rep = ledger.merge([Finding(signature="LEN-01-length-drift", date="2026-08-08")])
    assert rep.unchanged == ["LEN-01-length-drift"]
    assert not rep.changed


def test_merge_bumps_counter_and_preserves_hand_written_context(ledger: Ledger):
    rep = ledger.merge([Finding(signature="B1-detail-beyond-source", date="2026-08-09")])
    assert rep.updated == ["B1-detail-beyond-source"]
    item = ledger.get("B1-detail-beyond-source")
    assert item.count == 3 and item.occurrences[0] == "2026-08-09"
    ledger.save()
    text = ledger.path.read_text(encoding="utf-8")
    assert "3× (2026-08-09, 2026-08-08, 2026-07-27)" in text
    assert "Hand-written extra bullet that must survive edits." in text
    assert "> Header prose stays untouched." in text


def test_merge_new_finding_creates_item_in_open_section(ledger: Ledger):
    rep = ledger.merge([Finding(
        signature="TON-01-guilt-tone", date="2026-08-09", title="Guilt-laden tone",
        severity="minor", klass="D2", symptom="counts days of delay")])
    assert rep.created == ["TON-01-guilt-tone"]
    ledger.save()
    reparsed = Ledger(ledger.path)
    item = reparsed.get("TON-01-guilt-tone")
    assert item is not None and item.count == 1 and item.section == "Open"
    # new item must land before the Fixed section
    text = ledger.path.read_text(encoding="utf-8")
    assert text.index("TON-01-guilt-tone") < text.index("## Fixed")


def test_escalation_reported_not_applied(ledger: Ledger):
    rep = ledger.merge([Finding(signature="LEN-01-length-drift", date="2026-08-09")])
    assert "LEN-01-length-drift" in rep.escalation_due
    # write-little: status token untouched, escalation is a recommendation
    assert ledger.get("LEN-01-length-drift").status == "OPEN"


def test_escalation_fires_exactly_at_threshold(tmp_path: Path):
    # regression: merge() used a stale pre-bump item for the threshold check,
    # so an item crossing escalate_at DURING the merge was never reported
    # (caught by examples/quickstart.py, 2026-08-08)
    p = tmp_path / "backlog.md"
    p.write_text("## Open\n\n### [X-2-sig] OPEN — t\n"
                 "- **Occurrences:** 2× (2026-08-01, 2026-08-02)\n", encoding="utf-8")
    ledger = Ledger(p, escalate_at=3)
    rep = ledger.merge([Finding(signature="X-2-sig", date="2026-08-03")])
    assert rep.escalation_due == ["X-2-sig"]


def test_escalation_skips_already_critical(ledger: Ledger):
    rep = ledger.merge([Finding(signature="B1-detail-beyond-source", date="2026-08-09")])
    assert rep.escalation_due == []


def test_retract_is_first_class(ledger: Ledger):
    ledger.retract("LEN-01-length-drift", note="limit was raised on purpose")
    ledger.save()
    text = ledger.path.read_text(encoding="utf-8")
    assert "### [LEN-01-length-drift] RETRACTED —" in text
    assert "false positive of the audit: limit was raised on purpose" in text
    assert Ledger(ledger.path).get("LEN-01-length-drift").count == 26  # history kept


def test_mark_fixed(ledger: Ledger):
    ledger.mark_fixed("LEN-01-length-drift", note="commit abc123")
    assert ledger.get("LEN-01-length-drift").status == "FIXED"


def test_polish_field_alias(tmp_path: Path):
    p = tmp_path / "backlog.md"
    p.write_text("## Otwarte\n\n### [X-1-sig] OPEN — t\n- **Wystąpienia:** 1× (2026-08-01)\n",
                 encoding="utf-8")
    ledger = Ledger(p)
    ledger.merge([Finding(signature="X-1-sig", date="2026-08-02")])
    assert ledger.get("X-1-sig").count == 2
    ledger.save()
    assert "**Wystąpienia:** 2× (2026-08-02, 2026-08-01)" in p.read_text(encoding="utf-8")


def test_new_ledger_from_scratch(tmp_path: Path):
    ledger = Ledger(tmp_path / "fresh.md")
    ledger.merge([Finding(signature="A-1-new", date="2026-08-08", title="New defect")])
    ledger.save()
    text = (tmp_path / "fresh.md").read_text(encoding="utf-8")
    assert "## Open" in text and "### [A-1-new] OPEN — New defect" in text


def test_new_item_gets_a_blank_line_before_its_heading(tmp_path: Path):
    """Dirty real-world shape: an open section that ends flush against EOF.

    Markdown needs a blank line before a heading; hand-written ledgers do not
    reliably end an item with one, so appending must add it rather than assume
    it — otherwise the new item renders as part of the previous item's prose.
    """
    p = tmp_path / "backlog.md"
    p.write_text("## Open\n\n### [A-1] OPEN — first\n- **Occurrences:** 1× (2026-08-01)",
                 encoding="utf-8")
    led = Ledger(p)
    led.merge([Finding(signature="B-2", date="2026-08-02", title="second")])
    led.save()
    lines = p.read_text(encoding="utf-8").splitlines()
    heading = lines.index("### [B-2] OPEN — second")
    assert lines[heading - 1] == "", "a heading glued to the previous bullet"
    assert Ledger(p).get("A-1").count == 1, "the previous item still parses"


def test_ledger_created_from_nothing_has_no_leading_blank_line(tmp_path: Path):
    p = tmp_path / "fresh.md"
    led = Ledger(p)
    led.merge([Finding(signature="A-1", date="2026-08-01", title="first")])
    led.save()
    assert p.read_text(encoding="utf-8").startswith("## Open")


# ── dirty, hand-written occurrence lines (real-world irregular formats) ─────
#
# Shapes taken from a production ledger: the occurrence bullet is prose written
# by a human, not a clean list. A date is routinely restated while describing a
# multi-day pattern, or repeated inside a trailing evidence path — `DATE_RE
# .findall` without dedup counted the same day several times and inflated
# `len(occurrences)` past the declared `N×` (one item parsed as 39 against a
# declared 16×). A separate class of item records a continuous period with no
# `N×` token at all, which the old `OCC_RE` (requiring `\d+×`) could not read
# at all, so it silently produced `count == 0`.
#
# The Polish prose is deliberate: ledgers are written in the maintainer's
# language, and the parser must read `Wystąpienia` and free-form sentences
# around the dates just as well as their English equivalents.


def test_prose_line_with_repeated_dates_dedupes_and_declared_count_wins(tmp_path: Path):
    p = tmp_path / "backlog.md"
    p.write_text(
        "## Open\n\n"
        "### [C2-context-wiring] OPEN — context wiring dead\n"
        "- **Occurrences:** **16×** (2026-06-30, **2026-07-16**, 2026-07-17, "
        "**2026-08-04**, **2026-08-06**, **2026-08-20**) — stan ustalony; "
        "**2026-08-20** siódmy kolejny dzień (dowód: "
        "2026-08-20-audit.md#F7). **2026-08-04 i 2026-08-06 — dwa dni z rzędu** "
        "z ręcznie dopisanym czynnikiem.\n",
        encoding="utf-8",
    )
    item = Ledger(p).get("C2-context-wiring")
    # 6 distinct dates named in the list, plus 2026-08-20/04/06 restated later
    # — dedup must not let the restatements inflate the parsed date count.
    assert item.occurrences == [
        "2026-06-30", "2026-07-16", "2026-07-17", "2026-08-04", "2026-08-06", "2026-08-20",
    ]
    assert len(item.occurrences) == 6  # unique, not the 10 raw regex matches
    assert item.declared_count == 16
    assert item.count == 16  # hand-written N× authoritative over the (partial) date list


def test_occurrence_line_with_dash_separated_first_date(tmp_path: Path):
    p = tmp_path / "backlog.md"
    p.write_text(
        "## Open\n\n"
        "### [X-3-sig] OPEN — t\n"
        "- **Occurrences:** 3× — 2026-08-01 (opis wariantu), 2026-08-02, 2026-08-03\n",
        encoding="utf-8",
    )
    item = Ledger(p).get("X-3-sig")
    assert item.occurrences == ["2026-08-01", "2026-08-02", "2026-08-03"]
    assert item.declared_count == 3
    assert item.count == 3


def test_partial_date_list_declared_count_wins(tmp_path: Path):
    p = tmp_path / "backlog.md"
    p.write_text(
        "## Open\n\n"
        "### [LEN-02-sig] OPEN — t\n"
        "- **Occurrences:** 26×/29 issues (partial list: 2026-08-01, 2026-08-02)\n",
        encoding="utf-8",
    )
    item = Ledger(p).get("LEN-02-sig")
    assert len(item.occurrences) == 2  # only 2 dates actually written out
    assert item.declared_count == 26
    assert item.count == 26  # declared wins even though it's far above the date list


def test_continuous_period_note_without_n_times_token(tmp_path: Path):
    """An outage item recorded as a period rather than as a count.

    No "N×" anywhere in the line — the old OCC_RE (which required `\\d+×`)
    never matched, so occurrences stayed empty and count was silently 0.
    """
    p = tmp_path / "backlog.md"
    p.write_text(
        "## Open\n\n"
        "### [OPS-pipeline-outage] CRITICAL — nightly run never started\n"
        "- **Wystąpienia:** ciągłe od 2026-08-04 (wykryte 2026-08-18 przy "
        "diagnozie porannego raportu); **2026-08-20** — kolejny dzień "
        "bez żadnego runa\n",
        encoding="utf-8",
    )
    item = Ledger(p).get("OPS-pipeline-outage")
    assert item.occurrences == ["2026-08-04", "2026-08-18", "2026-08-20"]
    assert item.declared_count == 0  # nothing declared
    assert item.count == 3  # falls back to the number of unique dates


def test_more_dates_listed_than_declared_count_declared_still_wins(tmp_path: Path):
    p = tmp_path / "backlog.md"
    p.write_text(
        "## Open\n\n"
        "### [A1-sig] OPEN — t\n"
        "- **Occurrences:** **8×** (2026-07-14, 2026-07-30, 2026-07-31, "
        "2026-08-04, 2026-08-06, 2026-08-10, 2026-08-11, 2026-08-20) — later "
        "the same paragraph also names 2026-08-05 and 2026-08-07, which were "
        "never part of the declared list.\n",
        encoding="utf-8",
    )
    item = Ledger(p).get("A1-sig")
    assert len(item.occurrences) == 10  # 8 declared + 2 extra dates mentioned in prose
    assert item.declared_count == 8
    assert item.count == 8  # declared wins even though the date list is longer


def test_bump_occurrence_still_increments_n_times_and_prepends_date(tmp_path: Path):
    """Regression: merge()/_bump_occurrence must keep working identically on
    the normal (N×-declared) write path after splitting the read regex out."""
    p = tmp_path / "backlog.md"
    p.write_text(
        "## Open\n\n"
        "### [Y-1-sig] OPEN — t\n"
        "- **Occurrences:** 2× (2026-08-01, 2026-07-30)\n",
        encoding="utf-8",
    )
    led = Ledger(p)
    rep = led.merge([Finding(signature="Y-1-sig", date="2026-08-02")])
    assert rep.updated == ["Y-1-sig"]
    item = led.get("Y-1-sig")
    assert item.declared_count == 3
    assert item.occurrences == ["2026-08-02", "2026-08-01", "2026-07-30"]
    assert item.count == 3
    led.save()
    text = p.read_text(encoding="utf-8")
    assert "3× (2026-08-02, 2026-08-01, 2026-07-30)" in text

def test_status_note_with_em_dash_does_not_eat_the_title(tmp_path):
    """Regression 2026-08-26: an em dash INSIDE the status note stole the title.

    Real headers in the kierunki ledger carry a parenthesised note after the status
    ("FIXED 2026-08-26 (same day as the audit, commit abc; escalated — reported by the
    user) — the mentor claims ..."). The non-greedy split stopped at the dash inside the
    parentheses, so the quality portal rendered titles starting mid-note, with an orphan
    ")" — for 10 of 137 items.
    """
    md = tmp_path / "ledger.md"
    md.write_text(
        "### [B3-false-missing-dol] FIXED 2026-08-26 (same day as the audit, commit `45560a5`; "
        "escalated to CRITICAL and closed in one pass — reported by the user as a daily nuisance) "
        "— the mentor claims an element is missing that the trader did describe\n"
        "- **Class:** B3 | **Severity:** CRITICAL\n",
        encoding="utf-8")
    ledger = Ledger(md)
    item = ledger.items["B3-false-missing-dol"]
    assert item.title.startswith("the mentor claims an element is missing")
    assert ")" not in item.title[:20]
    assert item.status.startswith("FIXED 2026-08-26")


def test_plain_header_still_splits_at_the_first_dash(tmp_path):
    md = tmp_path / "ledger.md"
    md.write_text(
        "### [X1-simple] OPEN — a plain title without any note\n"
        "- **Class:** X1\n",
        encoding="utf-8")
    item = Ledger(md).items["X1-simple"]
    assert item.status == "OPEN"
    assert item.title == "a plain title without any note"
