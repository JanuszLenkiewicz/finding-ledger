from pathlib import Path

import pytest

from findingledger import load_audits, render_hub, trend

AUDIT = """---
type: mentor-audit
date: {date}
n_findings: {n}
by_severity: {{critical: {c}, major: {m}, minor: 0}}
by_class: {{A: 1, B: {b}, C: 0, D: 0, E: 0}}
---

# Audit {date}
"""


@pytest.fixture
def audits_dir(tmp_path: Path) -> Path:
    for date, n, c, m, b in (("2026-08-06", 2, 0, 2, 0),
                             ("2026-08-07", 3, 1, 2, 1),
                             ("2026-08-08", 5, 2, 2, 2)):
        (tmp_path / f"{date}-audit.md").write_text(
            AUDIT.format(date=date, n=n, c=c, m=m, b=b), encoding="utf-8")
    (tmp_path / "notes.md").write_text("no frontmatter here", encoding="utf-8")
    return tmp_path


def test_load_sorted_and_pattern_filtered(audits_dir: Path):
    summaries = load_audits(audits_dir)
    assert [s.date for s in summaries] == ["2026-08-06", "2026-08-07", "2026-08-08"]
    assert summaries[-1].by_severity == {"critical": 2, "major": 2, "minor": 0}
    assert summaries[-1].extra["type"] == "mentor-audit"


def test_render_hub(audits_dir: Path):
    hub = render_hub(load_audits(audits_dir))
    assert "| 2026-08-08 | 2/2/0 |" in hub
    assert "2026-08-08-audit.md" in hub


def test_trend_degrading(audits_dir: Path):
    t = trend(load_audits(audits_dir), window=1)
    assert t["recent"]["critical"] == 2 and t["previous"]["critical"] == 1
    assert t["delta"]["critical"] == 1 and t["degrading"] is True


def test_trend_without_baseline_makes_no_claim(audits_dir: Path):
    # regression: a single-window project showed positive deltas labelled
    # "improving" because an empty baseline was read as "not degrading"
    # (spotted on a live report, 2026-08-08)
    summaries = load_audits(audits_dir)[:1]
    t = trend(summaries, window=1)
    assert t["has_baseline"] is False
    assert t["degrading"] is None
    assert t["delta"] == {}
