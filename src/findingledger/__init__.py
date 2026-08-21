"""finding-ledger — the finding-lifecycle layer for LLM evaluation.

Assertion runners (promptfoo, DeepEval) and observability platforms (Langfuse,
Phoenix) answer "did the tests pass?". This library answers everything around
that question: where findings live, how duplicates collapse into one
root-cause item, when a repeated defect escalates, how an expected-fail test
graduates into a permanent regression guard, and how false positives get
retracted without losing history — all in markdown/YAML files owned by git.
"""

from . import adapters
from .adapters import findings as findings_from
from .adapters import results as results_from
from .audits import AuditSummary, load_audits, parse_audit, render_hub, trend
from .cases import alarms, case_for_signature, evaluate, graduate, load_cases
from .ledger import Ledger, MergeReport
from .models import Case, CaseOutcome, Finding, LedgerItem
from .report import build_report, load_project, load_registry, render_html

__version__ = "0.2.0"
__all__ = [
    "AuditSummary", "Case", "CaseOutcome", "Finding", "Ledger", "LedgerItem",
    "MergeReport", "adapters", "alarms", "build_report", "case_for_signature",
    "evaluate", "findings_from", "graduate", "load_project", "load_registry",
    "render_html", "results_from",
    "load_audits", "load_cases", "parse_audit", "render_hub", "trend",
]
