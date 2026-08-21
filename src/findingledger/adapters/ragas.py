"""Ragas adapter — RAG metrics as a family of findings.

Ragas measures retrieval and generation separately, which is exactly what you
need to answer *"do the hallucinations come from the retriever or from the
model?"*. Its metrics are a first-rate source of findings: when context recall
drops from 0.85 to 0.60 after a chunking change, that is not a number on a
dashboard, it is a defect with a mechanism (``ragas-context-recall-regression``),
an occurrence counter and a golden case guarding it afterwards.

Two rules produce findings, and they mean different things:

* **below threshold** — an absolute quality bar was breached
  (``ragas-{metric}-below-threshold``); it may have been broken for months;
* **regression** — the score fell by more than ``drop`` against a stored
  baseline (``ragas-{metric}-regression``); *something you did* caused it, and
  that is the one worth waking up for.

Usage — dump the scores from your Ragas run and hand them over::

    result = evaluate(dataset, metrics=[faithfulness, context_recall])
    Path("ragas.json").write_text(json.dumps(dict(result)))

::

    findingledger import --format ragas --input ragas.json --ledger bugfix/backlog.md \\
        --threshold context_recall=0.8 --threshold faithfulness=0.9 \\
        --baseline .findingledger/ragas-baseline.json --save-baseline

Accepted input: an aggregate mapping (``{"context_recall": 0.83}``), a list of
per-sample records (``result.to_pandas().to_dict(orient="records")`` — averaged
per metric, non-numeric columns ignored), or either of those wrapped in
``{"scores": ...}``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date as _date
from pathlib import Path
from typing import Any

from ..models import Finding
from .common import FAIL, PASS, ToolResult, read_json

SOURCE = "ragas"

#: Sensible starting bars. Override per project — a legal-summary RAG and a
#: support chatbot do not deserve the same threshold.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
    "context_precision": 0.75,
    "context_recall": 0.80,
    "answer_correctness": 0.70,
}

#: Relative drop against the baseline that counts as a regression (10%).
DEFAULT_DROP = 0.10


def scores(data: Any) -> dict[str, float]:
    """Coerce whatever Ragas produced into ``{metric: mean_score}``."""
    if isinstance(data, Mapping) and isinstance(data.get("scores"), (Mapping, list)):
        data = data["scores"]
    if isinstance(data, Mapping):
        return {str(k): float(v) for k, v in data.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if isinstance(data, list):
        totals: dict[str, list[float]] = {}
        for row in data:
            if not isinstance(row, Mapping):
                continue
            for k, v in row.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    totals.setdefault(str(k), []).append(float(v))
        return {k: sum(v) / len(v) for k, v in totals.items() if v}
    return {}


def load(path: str | Path) -> dict[str, float]:
    return scores(read_json(path))


def load_baseline(path: str | Path) -> dict[str, float]:
    """Read a stored baseline; a missing file means "no baseline yet"."""
    p = Path(path).expanduser()
    if not p.exists():
        return {}
    return scores(read_json(p))


def save_baseline(path: str | Path, values: Mapping[str, float],
                  date: str | None = None) -> Path:
    """Store the current scores as the baseline for the next run.

    Commit this file: the baseline belongs in git next to the ledger, so a
    regression is attributable to a diff.
    """
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": date or _date.today().isoformat(),
               "scores": {k: round(float(v), 6) for k, v in values.items()}}
    p.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return p


def _thresholds(overrides: Mapping[str, float] | None) -> dict[str, float]:
    merged = dict(DEFAULT_THRESHOLDS)
    merged.update({str(k): float(v) for k, v in (overrides or {}).items()})
    return merged


def parse(values: Mapping[str, float], thresholds: Mapping[str, float] | None = None,
          ) -> list[ToolResult]:
    """Metric rows for the tri-state gate — check ids are ``ragas:{metric}``.

    A ``sanity`` case with ``check: ragas:context_recall`` then fails CI the
    moment retrieval quality falls under the bar.
    """
    bars = _thresholds(thresholds)
    out = []
    for metric, score in sorted(values.items()):
        bar = bars.get(metric)
        if bar is None:
            continue
        out.append(ToolResult(
            id=f"ragas:{metric}",
            outcome=PASS if score >= bar else FAIL,
            reason=f"{metric} {score:.3f} < threshold {bar:.3f}" if score < bar else "",
            title=f"ragas {metric}",
            source=SOURCE,
            kind="metric",
            meta={"score": score, "threshold": bar},
        ))
    return out


def findings(values: Mapping[str, float], thresholds: Mapping[str, float] | None = None,
             baseline: Mapping[str, float] | None = None, date: str | None = None,
             drop: float = DEFAULT_DROP, baseline_date: str = "") -> list[Finding]:
    """Findings for metrics under their bar, and for regressions vs baseline."""
    bars = _thresholds(thresholds)
    day = date or _date.today().isoformat()
    since = f" since {baseline_date}" if baseline_date else ""
    out: list[Finding] = []
    for metric, score in sorted(values.items()):
        bar = bars.get(metric)
        was = (baseline or {}).get(metric)
        if was is not None and was > 0 and (was - score) / was >= drop:
            delta = score - was
            out.append(Finding(
                signature=f"ragas-{metric.replace('_', '-')}-regression",
                date=day,
                title=f"{metric} regression ({was:.3f} → {score:.3f})",
                severity="critical" if (was - score) / was >= 2 * drop else "major",
                klass="retrieval" if "context" in metric else "generation",
                symptom=f"Ragas {metric} fell by {abs(delta):.3f}{since}",
                evidence=f"{metric}: baseline {was:.3f} → current {score:.3f} "
                         f"({delta:+.3f}, threshold {bar if bar is not None else '—'})",
                root_cause="unknown — diff the retrieval/prompt changes since the baseline",
                fix_direction="bisect the chunking/embedding/prompt change that moved it",
                verification=f"ragas:{metric}",
            ))
        elif bar is not None and score < bar:
            out.append(Finding(
                signature=f"ragas-{metric.replace('_', '-')}-below-threshold",
                date=day,
                title=f"{metric} below threshold ({score:.3f} < {bar:.3f})",
                severity="critical" if score < bar * 0.75 else "major",
                klass="retrieval" if "context" in metric else "generation",
                symptom=f"Ragas {metric} under the agreed bar",
                evidence=f"{metric}: {score:.3f}, threshold {bar:.3f}",
                fix_direction="raise retrieval quality or lower the bar deliberately, "
                              "not silently",
                verification=f"ragas:{metric}",
            ))
    return out
