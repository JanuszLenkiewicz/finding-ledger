"""Smoke tests for the MCP wrapper.

Skipped unless the optional extra is installed (``pip install
"finding-ledger[mcp]"``). The operations themselves are covered by
``test_service.py``, which needs no optional dependency — this file only
guards the wiring: every service operation is exposed, tools carry schemas,
and a real call round-trips.
"""

import asyncio

import pytest

pytest.importorskip("mcp", reason="optional extra: pip install 'finding-ledger[mcp]'")

from findingledger.mcp_server import mcp  # noqa: E402

EXPECTED = {"list_projects", "project_status", "ledger_items", "merge_findings",
            "mark_fixed", "retract_finding", "graduate_case", "check_cases",
            "audit_trend", "build_report",
            # integrations (v0.2)
            "project_paths", "check_results_file", "import_tool_output", "alert",
            "quality_scores"}


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "alpha"
    (root / "cases").mkdir(parents=True)
    (root / "findingledger.yaml").write_text(
        "name: alpha\nledger: backlog.md\ncases: cases\n", encoding="utf-8")
    (root / "backlog.md").write_text(
        "## Open\n\n### [A-1-sig] OPEN — t\n- **Occurrences:** 1× (2026-08-01)\n",
        encoding="utf-8")
    reg = tmp_path / "projects.yaml"
    reg.write_text(f"projects:\n  - {root}\n", encoding="utf-8")
    return str(reg)


def _tools():
    return asyncio.run(mcp.list_tools())


def test_every_operation_is_exposed():
    assert {t.name for t in _tools()} == EXPECTED


def test_tools_have_descriptions_and_schemas():
    for t in _tools():
        # mcp 2.x exposes input_schema, 1.x used inputSchema
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
        assert schema.get("type") == "object"
        assert t.description and len(t.description) > 40, f"{t.name} lacks agent-facing docs"


def test_write_tool_round_trip(registry):
    async def run():
        await mcp.call_tool("merge_findings", {
            "project": "alpha", "registry": registry,
            "findings": [{"signature": "B-2-sig", "date": "2026-08-09", "title": "New"}]})
        return await mcp.call_tool("ledger_items",
                                   {"project": "alpha", "registry": registry})

    result = asyncio.run(run())
    payload = str(result)
    assert "B-2-sig" in payload and "A-1-sig" in payload
