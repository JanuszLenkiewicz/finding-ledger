"""JUnit XML adapter — the universal fallback.

Almost every runner can emit JUnit XML (``pytest --junitxml=``, DeepEval, Jest,
Go, Maven, most CI systems), which makes this the adapter that works when no
dedicated one exists::

    pytest --junitxml=report.xml
    findingledger check --cases eval/cases --results report.xml --format junit

Mapping: ``<failure>`` or ``<error>`` → FAIL, ``<skipped>`` → SKIP, otherwise
PASS. The case id is taken from a ``<properties>`` entry when present —

.. code-block:: xml

    <testcase classname="tests.test_mentor" name="test_cites_source">
      <properties>
        <property name="findingledger_case" value="GC-07"/>
        <property name="findingledger_signature" value="B1-detail-beyond-source"/>
      </properties>
    </testcase>

— and otherwise from the test name, with ``classname.name`` recorded as an
alias so either spelling matches a case file. In pytest, those properties come
from the ``record_property`` fixture (or automatically from the ``finding``
marker, see :mod:`findingledger.pytest_plugin`).

Security note: this uses :mod:`xml.etree.ElementTree`, which does not resolve
external entities but is not hardened against entity-expansion bombs. Parsing
a report produced by your own CI is fine; for untrusted XML, install
``defusedxml`` and pre-parse with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .common import (
    DEFAULT_CLASS_KEYS,
    DEFAULT_ID_KEYS,
    DEFAULT_SEVERITY_KEYS,
    DEFAULT_SIGNATURE_KEYS,
    FAIL,
    PASS,
    SKIP,
    ToolResult,
    first_of,
    oneline,
)

SOURCE = "junit"


def _properties(case: Any) -> dict[str, str]:
    props: dict[str, str] = {}
    for holder in case.iter("property"):
        name, value = holder.get("name"), holder.get("value")
        if name:
            props[name] = value or ""
            props[name.replace("findingledger_", "")] = value or ""
    return props


def _failure_text(case: Any) -> str:
    parts = []
    for tag in ("failure", "error"):
        for node in case.iter(tag):
            parts.append(node.get("message") or (node.text or "").strip())
    return oneline(" / ".join(p for p in parts if p))


def parse(text: str, id_keys=DEFAULT_ID_KEYS) -> list[ToolResult]:
    """Normalize a JUnit XML document into :class:`ToolResult` rows."""
    root = ElementTree.fromstring(text)
    cases = root.iter("testcase")
    out: list[ToolResult] = []
    for case in cases:
        name = case.get("name") or ""
        classname = case.get("classname") or ""
        props = _properties(case)
        case_id = str(first_of(props, id_keys) or name or classname or "junit-case")
        outcome = PASS
        if any(True for _ in case.iter("failure")) or any(True for _ in case.iter("error")):
            outcome = FAIL
        elif any(True for _ in case.iter("skipped")):
            outcome = SKIP
        # Deliberately not aliasing the bare classname: it is shared by every
        # test in the module, so one failure would mark the whole module FAIL.
        aliases = [name, f"{classname}.{name}" if classname else ""]
        out.append(ToolResult(
            id=case_id,
            outcome=outcome,
            aliases=[a for a in aliases if a],
            reason=_failure_text(case),
            title=f"{classname}.{name}".strip("."),
            signature=(str(first_of(props, DEFAULT_SIGNATURE_KEYS))
                       if first_of(props, DEFAULT_SIGNATURE_KEYS) else None),
            severity=str(first_of(props, DEFAULT_SEVERITY_KEYS) or "major"),
            klass=str(first_of(props, DEFAULT_CLASS_KEYS) or ""),
            source=SOURCE,
            meta={"time": case.get("time")},
        ))
    return out


def load(path: str | Path, id_keys=DEFAULT_ID_KEYS) -> list[ToolResult]:
    return parse(Path(path).read_text(encoding="utf-8"), id_keys)
