"""pytest plugin — the tri-state gate inside your existing test run.

This is the shortest integration in the package: no export file, no second
command. pytest already knows which tests failed; the plugin adds the one thing
pytest cannot know — *whether a red test is bad news*.

A test bound to an ``open`` golden case is **expected** to fail: it was written
before the fix, deliberately, so that success has a definition up front. With
``--fl-gate`` such a failure no longer breaks the build, while a failing
``regression`` or ``sanity`` case still does. That is the alternative to the
industry's favourite anti-pattern: deleting or skipping red tests to get CI
green.

Bind a test to a case with the ``finding`` marker::

    @pytest.mark.finding("GC-07", signature="B1-detail-beyond-source", severity="critical")
    def test_mentor_cites_the_source_figure():
        assert "3.2%" in answer

Then run::

    pytest --fl-project newsletter --fl-gate
    pytest --fl-cases eval/cases --fl-ledger bugfix/backlog.md --fl-gate --fl-record

Flags (long forms ``--findingledger-*`` also work):

``--fl-cases DIR``       golden cases to confront the run with
``--fl-ledger FILE``     ledger for ``--fl-record``
``--fl-project NAME``    take both from the project's ``findingledger.yaml``
``--fl-results FILE``    write the ``{check_id: PASS|FAIL}`` map (feeds other tools)
``--fl-gate``            exit 0 when the only failures are ``open`` cases
``--fl-record``          merge failures into the ledger as findings
``--fl-date YYYY-MM-DD`` occurrence date to record (default: today)

The plugin is inert unless one of these flags is passed, so having it installed
never changes an unrelated test run.
"""

from __future__ import annotations

import json
from pathlib import Path

MARKER = "finding"
FLAGS = ("fl_cases", "fl_ledger", "fl_project", "fl_results", "fl_gate", "fl_record")


def pytest_addoption(parser) -> None:
    group = parser.getgroup("findingledger", "finding-lifecycle layer")
    group.addoption("--fl-cases", "--findingledger-cases", dest="fl_cases", default=None,
                    metavar="DIR", help="golden-case directory to evaluate against")
    group.addoption("--fl-ledger", "--findingledger-ledger", dest="fl_ledger", default=None,
                    metavar="FILE", help="markdown ledger for --fl-record")
    group.addoption("--fl-project", "--findingledger-project", dest="fl_project", default=None,
                    metavar="NAME",
                    help="project name/path; takes cases+ledger from findingledger.yaml")
    group.addoption("--fl-registry", "--findingledger-registry", dest="fl_registry",
                    default=None, metavar="FILE", help="project registry path")
    group.addoption("--fl-results", "--findingledger-results", dest="fl_results", default=None,
                    metavar="FILE", help="write the results map as JSON")
    group.addoption("--fl-gate", "--findingledger-gate", dest="fl_gate", action="store_true",
                    default=False, help="failing `open` cases do not fail the run")
    group.addoption("--fl-record", "--findingledger-record", dest="fl_record",
                    action="store_true", default=False,
                    help="merge failures into the ledger as findings")
    group.addoption("--fl-date", "--findingledger-date", dest="fl_date", default=None,
                    metavar="YYYY-MM-DD", help="occurrence date to record (default: today)")


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "finding(case_id, signature=None, severity='major', klass=''): bind this test to a "
        "golden case in the finding ledger")
    if any(getattr(config.option, flag, None) for flag in FLAGS):
        config.pluginmanager.register(FindingLedgerPlugin(config), "findingledger-runtime")


class FindingLedgerPlugin:
    """Collects outcomes, then applies the finding lifecycle at session end."""

    def __init__(self, config) -> None:
        self.config = config
        self.rows: dict[str, dict] = {}

    # ── collection ─────────────────────────────────────────────────────────
    def pytest_itemcollected(self, item) -> None:
        marker = item.get_closest_marker(MARKER)
        info = dict(marker.kwargs) if marker else {}
        if marker and marker.args:
            info.setdefault("case_id", str(marker.args[0]))
        self.rows[item.nodeid] = {"outcome": "PASS", "reason": "", "info": info,
                                  "name": item.name}

    def pytest_runtest_logreport(self, report) -> None:
        row = self.rows.setdefault(report.nodeid,
                                   {"outcome": "PASS", "reason": "", "info": {}, "name": ""})
        if report.failed:
            row["outcome"] = "FAIL"
            row["reason"] = str(getattr(report, "longreprtext", "")
                                or report.longrepr or "")[:2000]
        elif report.skipped and row["outcome"] != "FAIL":
            row["outcome"] = "SKIP"

    # ── keys ───────────────────────────────────────────────────────────────
    @staticmethod
    def keys_of(nodeid: str, row: dict) -> list[str]:
        """Every name this test is plausibly known by in a case file."""
        info = row.get("info") or {}
        candidates = [str(info.get("case_id") or ""), str(info.get("case") or ""),
                      nodeid, str(row.get("name") or ""), nodeid.split("::")[-1]]
        seen, out = set(), []
        for k in candidates:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def results_map(self) -> dict[str, str]:
        results: dict[str, str] = {}
        for nodeid, row in self.rows.items():
            for key in self.keys_of(nodeid, row):
                if results.get(key) != "FAIL":
                    results[key] = row["outcome"]
        return results

    def findings(self):
        from .adapters.common import ToolResult, to_findings

        rows = []
        for nodeid, row in self.rows.items():
            if row["outcome"] != "FAIL":
                continue
            info = row.get("info") or {}
            case_id = str(info.get("case_id") or info.get("case") or row.get("name") or nodeid)
            rows.append(ToolResult(
                id=case_id, outcome="FAIL", aliases=[nodeid], reason=row.get("reason", ""),
                title=str(row.get("name") or case_id),
                signature=str(info["signature"]) if info.get("signature") else None,
                severity=str(info.get("severity", "major")),
                klass=str(info.get("klass", info.get("class", ""))), source="pytest"))
        return to_findings(rows, date=self.config.option.fl_date, prefix="pytest-")

    def _paths(self) -> tuple[Path | None, Path | None]:
        opt = self.config.option
        cases, ledger = opt.fl_cases, opt.fl_ledger
        if opt.fl_project and not (cases and ledger):
            from .service import project_paths
            paths = project_paths(opt.fl_project, opt.fl_registry)
            cases, ledger = cases or paths.get("cases"), ledger or paths.get("ledger")
        return (Path(cases) if cases else None, Path(ledger) if ledger else None)

    # ── the whole lifecycle, once the run is over ──────────────────────────
    def pytest_sessionfinish(self, session, exitstatus) -> None:
        opt = self.config.option
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        say = reporter.write_line if reporter is not None else print
        results = self.results_map()

        if opt.fl_results:
            out = Path(opt.fl_results)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n",
                           encoding="utf-8")
            say(f"finding-ledger: results map -> {out}")

        cases_dir, ledger_path = self._paths()
        alarming: list = []
        allowed: set[str] = set()
        if cases_dir and cases_dir.is_dir():
            from .cases import alarms, evaluate, load_cases
            cases = load_cases(cases_dir)
            allowed = {c.check or c.id for c in cases if c.status == "open"}
            outcomes = evaluate(cases, results)
            alarming = alarms(outcomes)
            graduating = [o for o in outcomes if o.action == "graduation-candidate"]
            say(f"finding-ledger: {len(outcomes)} case(s), {len(alarming)} alarm(s), "
                f"{len(graduating)} ready to graduate")
            for o in alarming:
                say(f"  ALARM  {o.case.id} [{o.case.status}] — the defect is back "
                    f"(ledger: {o.case.backlog or '—'})")
            for o in graduating:
                say(f"  ready  {o.case.id} — now passes; run `findingledger graduate "
                    f"--cases {cases_dir} {o.case.id}`")

        if opt.fl_record and ledger_path:
            from .ledger import Ledger
            findings = self.findings()
            if findings:
                ledger = Ledger(ledger_path)
                report = ledger.merge(findings)
                if report.changed:
                    ledger.save()
                say(f"finding-ledger: recorded {len(report.created)} new, "
                    f"{len(report.updated)} updated item(s) in {ledger_path}")
                for signature in report.escalation_due:
                    say(f"  escalation due: {signature} reached the threshold — "
                        "a human decides whether it becomes CRITICAL")
            else:
                say("finding-ledger: nothing to record (no failures)")

        if opt.fl_gate and exitstatus == 1:
            unexpected = [nodeid for nodeid, row in self.rows.items()
                          if row["outcome"] == "FAIL"
                          and not (set(self.keys_of(nodeid, row)) & allowed)]
            if not unexpected and not alarming:
                say("finding-ledger: gate — every failure belongs to an `open` case "
                    "(expected-fail, written before the fix); exiting 0")
                session.exitstatus = 0
            elif unexpected:
                say(f"finding-ledger: gate — {len(unexpected)} failure(s) not covered by an "
                    "`open` case; the run stays red")
