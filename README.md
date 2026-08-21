# finding-ledger

[![CI](https://github.com/JanuszLenkiewicz/finding-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/JanuszLenkiewicz/finding-ledger/actions/workflows/ci.yml)

**The finding-lifecycle layer for LLM evaluation.** Assertion runners
(promptfoo, DeepEval) answer *"did the tests pass?"*. Observability platforms
(Langfuse, Phoenix) answer *"what did production do?"*. This library answers
everything in between — where findings live, how duplicates collapse, when
defects escalate, and how a fixed bug becomes a permanent regression guard.

> Status: pre-release, in daily use by two production consumers (a
> trading-mentor platform and an unattended daily-newsletter pipeline) from
> which it was extracted. The API will settle when a third project adopts it —
> the rule of three.

**Documentation:** [Getting started](docs/getting-started.md) ·
[How it works](docs/how-it-works.md) ·
[Integrations & best practices](docs/integrations.md) ·
[Adapter reference](docs/adapters.md) ·
[For agents](AGENTS.md)

## The gap this fills

Practitioners agree that **error analysis** — reading failures, building a
taxonomy, tracking recurrence — is the highest-ROI activity in LLM evaluation,
and that it happens in spreadsheets and notes because no tool owns it. Existing
tools stop at "run assertions, show results". The *lifecycle* of a finding is
nobody's feature:

```
audit finds a defect
  → deduplicated into a ledger by ROOT-CAUSE SIGNATURE (mechanism, not symptom)
  → occurrence counter = priority weight; threshold triggers escalation review
  → golden case written BEFORE the fix (status: open — allowed to fail)
  → fix lands → case graduates to regression — a FAIL now means the bug is back
  → false positives get RETRACTED with history, never deleted
```

finding-ledger implements exactly that loop — and nothing else. It complements
promptfoo/DeepEval/Langfuse; it does not replace them.

## Design principles

1. **The ledger is a markdown document owned by humans, kept in git.**
   Readable, hand-editable, PR-reviewable. The library performs *surgical
   edits only* (bump a counter, flip a status token, add a retraction note) —
   it never regenerates the file, so hand-written context can never be lost.
2. **Write little.** Escalation past the threshold is *reported*, not applied —
   severity changes are a human decision.
3. **Tri-state golden cases** (`regression` / `open` / `sanity`) let you write
   the test before the fix without breaking CI: a failing `open` case exits 0,
   a failing `regression` or `sanity` case exits 1.
4. **Git is the metrics store.** Audit files carry YAML frontmatter
   (`n_findings`, `by_severity`, `by_class`); aggregating them yields a quality
   trend and a chronological hub table with zero extra infrastructure.
5. **Deterministic first, LLM second.** Everything countable is counted by
   code; the LLM judge gets only what regex cannot decide (tone polarity,
   rubric quality). Cases carry either a `check:` (assertion id) or a
   `rubric:` (judge prompt) — never both.

## Install & use

**Not on PyPI yet** — publication waits for the third consumer project (see
Roadmap). Install from a clone, or straight from git:

```bash
pip install -e .                    # one runtime dependency: pyyaml
pip install -e ".[mcp]"             # plus the agent-facing MCP server
pip install -e ".[langfuse]"        # plus pushing scores to Langfuse

pip install "git+https://github.com/JanuszLenkiewicz/finding-ledger@main"
```

Every adapter is stdlib-only: reading promptfoo, DeepEval, JUnit, Ragas, OTLP
or trace output — and sending alerts — needs no extra package, and none of
those tools has to be installed for this library to read its files.

Full walkthrough: **[docs/getting-started.md](docs/getting-started.md)**.

```bash
# merge audit findings (JSON) into the markdown ledger
findingledger merge --ledger bugfix/backlog.md --findings findings.json --escalate-at 3

# confront golden cases with assertion results; exit 1 only on real alarms
findingledger check --cases eval/cases --results lint-results.json

# the fix landed — flip the case and close the ledger item
findingledger graduate --cases eval/cases len-01-limit
findingledger fixed --ledger bugfix/backlog.md LEN-01-length-drift --note "commit abc123"

# audit turned out wrong — retract with history
findingledger retract --ledger bugfix/backlog.md PWT-01-repeat --note "deliberate follow-up"

# quality trend from audit frontmatter (or --hub for a markdown hub table)
findingledger trend --audits audyt/ --window 7

# self-contained multi-project HTML dashboard (no server, no database)
findingledger report -o report.html
```

## Integrations

The library is a second-fiddle citizen by design: it runs no tests, collects no
traces and scores no answers. It plugs in *underneath* the tools that do and
turns what they emit into long-term memory.

```bash
# any runner's output → the tri-state verdict (format is sniffed, not declared)
promptfoo eval -o out.json
findingledger check --cases eval/cases --results out.json     # exit 1 only on real alarms

# any runner's failures → deduplicated ledger entries with occurrence counters
findingledger import --input out.json --ledger bugfix/backlog.md --dry-run

# RAG metrics as their own finding family, with a committed baseline
findingledger import --input ragas.json --format ragas --project mine \
  --baseline .findingledger/ragas-baseline.json --save-baseline

# production traffic → finding candidates a human then renames
findingledger import --input traces.json --format traces --cost-over 0.5 --min-hits 3

# deterministic, critical-only notification (a step AFTER the auditor, never inside it)
findingledger alert --project mine --audit "audits/$(date +%F)-audit.md"

# lifecycle state → observability scores, so quality sits next to cost
findingledger scores --project mine --trace-id "$TRACE_ID"
```

| In | Out |
|---|---|
| promptfoo · DeepEval · JUnit XML · Ragas · pytest · OTLP/Phoenix spans · Langfuse traces | Langfuse scores · alerts (stdout/file/webhook/command) · GitHub Actions annotations, job summary, step outputs · HTML report |

**pytest users get the shortest path** — no export file, no second command:

```bash
pytest --fl-cases eval/cases --fl-gate --fl-record
```

```python
@pytest.mark.finding("b1-source-fidelity", signature="B1-detail-beyond-source",
                     severity="critical")
def test_mentor_cites_the_source_figure():
    assert "3.2%" in answer
```

The first argument is **the golden case's `id`** (or its `check`, when the case
declares one) — that pairing is what tells the gate which case this test stands
for. Get it wrong and the failure counts as uncovered, which keeps the run red;
`signature` is a separate thing, the ledger item the case verifies.

`--fl-gate` lets a test bound to an `open` case fail without failing the build
(it was written *before* the fix, deliberately), while a failing `regression`
case still turns CI red. That is the alternative to deleting red tests to get
green pipelines.

**CI in one step:**

```yaml
- uses: JanuszLenkiewicz/finding-ledger@main
  with: { results: promptfoo-output.json, cases: eval/cases }
```

Details and file shapes: [adapter reference](docs/adapters.md). Why the pieces
belong together: [integrations & best practices](docs/integrations.md).
Runnable tour of every adapter on synthetic data:
`python examples/integrations_tour.py`.

## Multi-project reports

Each consumer project self-describes with a `findingledger.yaml` at its root:

```yaml
name: my-project
description: What this pipeline does
ledger: bugfix/backlog.md
cases: eval/cases
audits: audits/          # absolute and ~ paths allowed too
```

A machine-level registry (`~/.config/findingledger/projects.yaml`, outside all
repos — local paths stay private) lists the projects:

```yaml
projects:
  - ~/projects/newsletter
  - ~/projects/trading-mentor
```

`findingledger report` then renders one self-contained HTML file with a tab
per project: open items by occurrence count, escalation candidates, tri-state
case status, audit history with a quality trend. Inline CSS, light/dark aware,
zero external assets — commit it, mail it, or serve it from GitHub Pages.

A demo report built from synthetic data lives at
[`docs/demo-report.html`](docs/demo-report.html)
(regenerate with `python examples/demo_report.py`). Real reports stay local —
consumer data never enters this repository.

Python API: `Ledger`, `Finding`, `load_cases` / `evaluate` / `graduate`,
`load_audits` / `render_hub` / `trend` — see docstrings.

## Ledger format

```markdown
## Open

### [B1-detail-beyond-source] CRITICAL — detail added under a real citation
- **Class:** B1 | **Severity:** critical
- **Occurrences:** 3× (2026-08-08, 2026-07-27, ...)
- **Symptom:** paraphrase rounds a source list up to a category.
- **Evidence:** literal quote + path + counterexample from the source.
- **Root-cause:** generative "rounding to the popular notion".
- **Fix direction:** prompt rule — only facts you can point at in the source.
- **Verification:** eval/cases/b1-source-fidelity.yaml
```

The parser is tolerant of hand-written variants (`3× — dates`, `26×/29 issues
(...)`, localized field names); the declared `N×` is authoritative when the
date list is partial.

**The signature is the load-bearing decision: name the mechanism, not the
symptom.** `B1-detail-beyond-source`, not `mentor-said-heels-on-aug-8`. Name
symptoms and you end up with a hundred entries with a counter of 1 each and no
priority signal; name mechanisms and the counter becomes the priority weight.

## Use from an AI agent

The loop was built to be run by agents, so the library ships two agent
surfaces.

**[AGENTS.md](AGENTS.md)** carries the recipes: how to turn an audit into
ledger entries, write a golden case before the fix, close the loop after it,
and retract a false positive — plus the rules that keep an autonomous agent
from doing damage (read before write, evidence on every finding, never
regenerate the ledger, never delete an item).

**MCP server** — same operations as structured tools:

```bash
pip install -e ".[mcp]"               # not on PyPI yet — see Install & use
findingledger-mcp                     # stdio transport
```

```json
{"mcpServers": {"finding-ledger": {"command": "findingledger-mcp"}}}
```

Tools: `list_projects`, `project_status`, `project_paths`, `ledger_items`,
`merge_findings`, `mark_fixed`, `retract_finding`, `graduate_case`,
`check_cases`, `check_results_file`, `import_tool_output`, `audit_trend`,
`alert`, `quality_scores`, `build_report`. Read operations first — an agent
should reuse an existing root-cause signature rather than invent a parallel
one. The two write-heavy integration tools (`import_tool_output`, `alert`)
default to `dry_run=true`: derived signatures need a human rename, and sending
a notification is a side effect worth confirming.

The logic lives in `findingledger.service` (plain functions returning
JSON-serializable dicts), so the same operations work from Python without MCP.

## Roadmap

- Pattern registry with graduation threshold (recurring cross-item patterns).
- Adapters for the tools nobody has asked for yet — the shape is deliberately
  cheap to add (~40 lines, see [writing a new adapter](docs/adapters.md#writing-a-new-adapter)).
- PyPI publication, after a third consumer project settles the API.

## License

MIT
