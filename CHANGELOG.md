# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- `LedgerItem.count` used `max(declared_count, len(occurrences))`, and
  `occurrences` was every date `DATE_RE` found in the raw occurrence line,
  undeduplicated. On real hand-written ledgers the line is prose, not a clean
  list: a date is routinely restated while describing a multi-day pattern, or
  repeated inside a trailing evidence path (`.../2026-08-20-audit.md#F7`).
  Verified against a production ledger, where one item parsed as **39** against
  a declared **16×** and another as **12** against **8×**.
  Dates are now deduplicated by first appearance, and
  `count` is `declared_count or len(occurrences)` — the hand-written `N×` wins
  whenever it is present, in *either* direction (it can be lower than the
  inflated date count, or higher when the list is representative-only, e.g.
  `26× (all except ...)`).
- A hand-written occurrence line with no `N×` token at all (a continuous-period
  note, e.g. "ciągłe od 2026-08-04 (...); 2026-08-20 — kolejny dzień...")
  matched nothing under the old `OCC_RE` (which required `\d+×`), so parsing
  silently produced `occurrences == []` and `count == 0`. A new `OCC_LINE_RE`
  handles the read path without requiring a count token; `OCC_RE` is kept,
  unchanged, for the write path (`_bump_occurrence`), which still always writes
  an `N×` token — surgical edits behave identically to before.

## [0.2.0] - 2026-08-08

### Added — integrations

The library keeps doing exactly one thing (the finding lifecycle); this release
is about everything it now plugs into.

- **`findingledger.adapters`** — stdlib-only translation layer, with format
  auto-detection (`adapters.detect`) so `--format` is only ever an override:
  - **promptfoo** — every output schema it has shipped, plus JSON Lines;
    `vars.case_id` maps to a golden case, `metadata.signature` to a ledger item.
  - **DeepEval** — camelCase and snake_case test-run files; each metric becomes
    an addressable check id (`GC-09::Faithfulness`) without inflating the
    ledger with one finding per metric.
  - **JUnit XML** — the universal fallback (`pytest --junitxml`, most runners);
    ids and signatures via `<property>` entries.
  - **Ragas** — aggregate or per-sample scores; separates *below threshold*
    from *regression against a committed baseline*, with baseline save/load.
  - **OTLP / Arize Phoenix spans** and **Langfuse traces** — mining rules
    (errors, latency, tokens, cost, low scores) that group by operation and
    file review *candidates*, explicitly marked as triage, not diagnosis.
  - **Langfuse scores (outbound)** — lifecycle state as score payloads
    (open items, criticals, escalations due, cases by status, trend verdict);
    building them is offline, pushing needs the new `[langfuse]` extra.
  - **Alerts** — deterministic, critical-only dispatch over stdout, a file, a
    Slack-compatible webhook, or any command reading stdin.
  - **GitHub Actions** — diff annotations, job-summary table, step outputs.
- **pytest plugin** (`pytest11` entry point, inert without flags): the
  tri-state gate inside an existing test run. `@pytest.mark.finding(...)` binds
  a test to a golden case; `--fl-gate` lets a failing `open` case exit 0 while
  a failing `regression`/`sanity` case still fails the build; `--fl-record`
  merges the run's failures into the ledger; `--fl-results` exports the map.
- **CLI**: `import` (foreign output → ledger, with `--dry-run`), `results`
  (any runner's output → the native results map), `alert`, `scores`; `check`
  gained `--format`, `--project`, `--json` and `--github`.
- **Composite GitHub Action** (`action.yml`) plus copy-ready workflows in
  `examples/ci/` (PR gate, pytest gate, nightly audit with alerting and a
  GitHub Pages dashboard) and a wired `promptfooconfig.yaml`.
- **MCP**: five new tools — `project_paths`, `check_results_file`,
  `import_tool_output`, `alert`, `quality_scores` (15 total). The two with side
  effects default to `dry_run=true`.
- **`service.project_paths()`** — single place that maps `findingledger.yaml`
  to the filesystem; `findingledger.yaml` gained an optional `alerts:` block.
- Docs: [adapter reference](docs/adapters.md); `docs/integrations.md` updated
  from roadmap to shipped. New runnable example `examples/integrations_tour.py`
  (every adapter, synthetic data, no keys); CI runs the examples.

### Fixed
- `trend()` claimed "improving" for projects with a single audit window: an
  empty baseline was read as "not degrading". It now reports `has_baseline`,
  and the report says "no trend yet" instead of a false verdict.
- `Ledger.merge`: escalation threshold was checked against a stale pre-bump
  item, so an item crossing `escalate_at` during the merge was never reported.
- `Ledger`: a new item appended after a section that ended flush against a
  bullet produced a heading with no blank line before it — valid text, invalid
  markdown. A ledger created from scratch no longer starts with a blank line.

### Added
- Documentation set in `docs/`: [getting-started](docs/getting-started.md)
  (11-step adoption walkthrough, practice rhythm, beginner mistakes),
  [how-it-works](docs/how-it-works.md) (data provenance — every reported number
  traced to a file; who writes those files), and
  [integrations](docs/integrations.md) (four-layer stack map, promptfoo /
  DeepEval / Ragas / Langfuse / Phoenix / MLflow / git / MCP, three recipes by
  scale, eight best practices, three anti-patterns).
- `AGENTS.md`: contributor rules plus agent-facing recipes for running the
  loop (audit → merge → case → fix → graduate → retract).
- MCP server (`findingledger-mcp`, optional extra `[mcp]`) exposing ten tools;
  compatible with both `mcp` 2.x (`MCPServer`) and 1.x (`FastMCP`).
- `findingledger.service`: project-aware operations returning JSON-serializable
  dicts — the shared layer behind MCP and direct Python use. Resolves projects
  by registry name or path.
- `findingledger report`: self-contained multi-project HTML dashboard.
  Projects self-describe via `findingledger.yaml`; a machine-level registry
  (`~/.config/findingledger/projects.yaml`) lists them; the library knows no
  project. Tolerant case loader accepts foreign schemas (e.g. promptfoo files)
  that share only the tri-state `status:` convention.

### Changed
- Renamed `eval-loop` -> `finding-ledger` (distribution), `evalloop` ->
  `findingledger` (package, CLI): `evalloop` is taken on PyPI by an active
  LLM-eval product — import-name collision and brand confusion.
- Repository language: English only (headed for public release).
- Repository restructured to `src/` layout; `doc/` renamed to `docs/`;
  added CI (pytest + ruff), typing marker (`py.typed`), community files.

## [0.1.0] - 2026-08-08

### Added
- `Ledger`: markdown-backed backlog with surgical edits — signature dedup,
  occurrence counters (declared `N×` authoritative over partial date lists),
  escalation reporting (never auto-applied), `retract` and `mark_fixed`
  with history preserved. Tolerant parser (localized field aliases,
  hand-written count formats).
- Tri-state golden cases (`regression` / `open` / `sanity`) with
  `evaluate` / `alarms` / `graduate` (open → regression).
- Audit frontmatter aggregation: `load_audits`, `render_hub`, `trend`.
- CLI: `findingledger merge / retract / fixed / graduate / check / trend`
  (cron-friendly exit codes: failing `open` cases exit 0).
- 18 unit tests, including dirty real-world ledger formats.
