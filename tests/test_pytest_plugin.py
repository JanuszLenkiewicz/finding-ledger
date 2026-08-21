"""The pytest plugin, exercised through real pytest runs.

The claim under test is the one that matters: a test bound to an ``open``
golden case may fail without failing the build, while a ``regression`` failure
still turns the run red. Exit codes are checked in a subprocess, because that
is what CI actually reads.
"""

import json

import pytest

CASES = {
    "gc-07.yaml": "id: GC-07\nstatus: open\ncheck: GC-07\nbacklog: B1-detail-beyond-source\n",
    "gc-01.yaml": "id: GC-01\nstatus: regression\ncheck: GC-01\nbacklog: OLD-sig\n",
    "san-01.yaml": "id: SAN-01\nstatus: sanity\ncheck: SAN-01\n",
}

LEDGER = """# Backlog

## Open

### [B1-detail-beyond-source] OPEN — Mentor invents a figure
- **Occurrences:** 2× (2026-08-05, 2026-08-01)
"""


@pytest.fixture
def project(pytester):
    cases = pytester.path / "cases"
    cases.mkdir()
    for name, body in CASES.items():
        (cases / name).write_text(body, encoding="utf-8")
    (pytester.path / "backlog.md").write_text(LEDGER, encoding="utf-8")
    return pytester


def write_tests(pytester, *, open_fails=True, regression_fails=False):
    pytester.makepyfile(test_suite=f"""
        import pytest

        @pytest.mark.finding("GC-07", signature="B1-detail-beyond-source",
                             severity="critical")
        def test_open_case():
            assert {not open_fails}

        @pytest.mark.finding("GC-01")
        def test_regression_case():
            assert {not regression_fails}

        @pytest.mark.finding("SAN-01")
        def test_sanity_case():
            assert True
    """)


def test_failing_open_case_does_not_fail_the_build(project):
    write_tests(project)
    result = project.runpytest_subprocess("--fl-cases", "cases", "--fl-gate")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*every failure belongs to an `open` case*"])


def test_failing_regression_case_keeps_the_run_red(project):
    write_tests(project, open_fails=False, regression_fails=True)
    result = project.runpytest_subprocess("--fl-cases", "cases", "--fl-gate")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*ALARM  GC-01*"])


def test_a_failure_outside_the_case_map_stays_red(project):
    write_tests(project)
    project.makepyfile(test_extra="def test_unrelated():\n    assert False\n")
    result = project.runpytest_subprocess("--fl-cases", "cases", "--fl-gate")
    assert result.ret == 1
    result.stdout.fnmatch_lines(["*not covered by an `open` case*"])


def test_gate_off_behaves_like_plain_pytest(project):
    write_tests(project)
    assert project.runpytest_subprocess("--fl-cases", "cases").ret == 1


def test_open_case_that_now_passes_is_reported_as_ready(project):
    write_tests(project, open_fails=False)
    result = project.runpytest_subprocess("--fl-cases", "cases")
    result.stdout.fnmatch_lines(["*ready  GC-07*"])


def test_results_map_is_written_for_other_tools(project):
    write_tests(project)
    project.runpytest_subprocess("--fl-results", "out/results.json")
    written = json.loads((project.path / "out" / "results.json").read_text(encoding="utf-8"))
    assert written["GC-07"] == "FAIL"
    assert written["GC-01"] == "PASS"
    assert written["test_open_case"] == "FAIL"     # addressable by test name too


def test_record_merges_failures_into_the_ledger(project):
    write_tests(project)
    result = project.runpytest_subprocess("--fl-ledger", "backlog.md", "--fl-record",
                                          "--fl-date", "2026-08-09")
    ledger = (project.path / "backlog.md").read_text(encoding="utf-8")
    assert "3× (2026-08-09, 2026-08-05, 2026-08-01)" in ledger, "counter bumped, no duplicate"
    result.stdout.fnmatch_lines(["*escalation due: B1-detail-beyond-source*"])


def test_recording_twice_on_one_day_is_a_no_op(project):
    write_tests(project)
    for _ in range(2):
        project.runpytest_subprocess("--fl-ledger", "backlog.md", "--fl-record",
                                     "--fl-date", "2026-08-09")
    assert (project.path / "backlog.md").read_text(encoding="utf-8").count("3×") == 1


def test_unmarked_failure_is_recorded_under_a_derived_signature(project):
    project.makepyfile(test_plain="def test_no_marker():\n    assert False\n")
    project.runpytest_subprocess("--fl-ledger", "backlog.md", "--fl-record",
                                 "--fl-date", "2026-08-09")
    ledger = (project.path / "backlog.md").read_text(encoding="utf-8")
    assert "[pytest-test-no-marker]" in ledger


def test_plugin_is_inert_without_flags(project):
    write_tests(project, open_fails=False)
    result = project.runpytest_subprocess()
    assert result.ret == 0
    # It is loaded (pytest lists it among plugins) but says and does nothing.
    assert "finding-ledger:" not in result.stdout.str()
