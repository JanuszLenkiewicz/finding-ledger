# AGENTS.md

## Claude Code instructions

Before starting any work, read the complete repository-root `CLAUDE.md` and follow its applicable instructions unless they conflict with higher-priority instructions.

Instructions for AI agents. Two audiences, two sections: agents **contributing
to this repository**, and agents **using the library** inside their own
pipelines (the more common case — this library was built by an agent, for
agent-run loops).

---

## Part 1 — Agents working on this repository

### Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### Checks (both must pass before any commit)

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests examples conftest.py
```

### Conventions

- **English only** — code, comments, docs, commit messages.
- **Line length 100**, ruff rules `E,F,W,I,UP,B,SIM` (configured in `pyproject.toml`).
- **`src/` layout**: code in `src/findingledger/`, never at the repo root.
- **One runtime dependency** (`pyyaml`). New dependencies need a strong
  justification; anything optional goes into `[project.optional-dependencies]`.
  **Adapters are stdlib-only**: a foreign tool's *output file* is the contract,
  never its SDK, so integrating it must not add an install. Where an SDK is
  unavoidable (pushing Langfuse scores), import it lazily inside the one
  function that needs it and keep the offline path (`--dry-run`, payload
  building) working without it.
- **Any change to ledger parsing or editing ships with a test on a *dirty*,
  hand-written format variant.** Real ledgers are irregular: `3× — dates`,
  `26×/29 issues (partial list)`, emoji status tokens, localized field names.
- Update `CHANGELOG.md` (Keep a Changelog) when behavior or decisions change.

### Architectural invariants (do not "fix" these)

1. **The ledger is a human-owned markdown document.** Only surgical edits —
   never regenerate the file from the data model. Hand-written prose must
   survive every operation.
2. **Write little.** Threshold escalation is *reported*
   (`MergeReport.escalation_due`), never applied. Severity is a human call.
3. **A failing `open` case exits 0.** That is deliberate: a test written
   before the fix must not break CI or a cron job. Only `regression` and
   `sanity` failures are alarms (exit 1).
4. **Git is the metrics store.** Trends come from audit-file frontmatter.
   No database, no server, no background process.
5. **Declared count wins when present.** `LedgerItem.count = declared_count or
   len(occurrences)` — a hand-written `N×` can be *lower* than the parsed date
   list (prose restates a date while describing a pattern, or an evidence path
   repeats one) or *higher* (representative dates only, e.g. `26× (all except
   ...)`); either way the human-written number is authoritative. Dates are
   deduplicated by first appearance before counting. No `N×` at all (a
   continuous-period note) falls back to the number of unique dates.
6. **A derived signature is a symptom, and the code says so.** Adapters that
   cannot read an explicit signature (promptfoo `metadata.signature`, a JUnit
   property, a `finding` marker) derive one from the case id and mark mined
   candidates `root_cause: unknown`. Never make a derived signature look like a
   diagnosis — the whole priority signal depends on one signature meaning one
   mechanism.
7. **A side effect never happens by inference.** `import_tool_output` and
   `alert` default to `dry_run=true` over MCP; a dry run must leave nothing
   behind (it does not move the Ragas baseline either); a failed alert channel
   returns an error in its receipt instead of raising, because the finding is
   already safe in the ledger and a crashed notifier must not take the run
   down with it.

---

## Part 2 — Agents using the library

You are likely an auditor, reviewer, or pipeline agent that just found defects
and needs them to survive past your context window. That is what this library
is for: **findings become a deduplicated, prioritized, git-tracked backlog,
and fixed defects become permanent regression guards.**

### Install and point it at a project

```bash
pip install -e /path/to/finding-ledger
```

Each project self-describes with `findingledger.yaml` at its root:

```yaml
name: my-project
description: What this pipeline does
ledger: bugfix/backlog.md
cases: eval/cases
audits: audits/
alerts:                     # optional: defaults for `findingledger alert`
  channel: command          # stdout | file | webhook | command | none
  command: telegram-send --stdin
  min_severity: critical
```

An optional machine-level registry (`~/.config/findingledger/projects.yaml`)
maps names to paths so you can say `newsletter` instead of a full path.

### Recipe A — after an audit: record what you found

Write findings to JSON, then merge. The signature is the important part:
**name the mechanism, not the symptom**, so the same defect found tomorrow
lands on the same item instead of creating a duplicate.

```json
[{
  "signature": "B1-detail-beyond-source",
  "date": "2026-08-08",
  "title": "Detail added under a real citation",
  "severity": "critical",
  "class": "B1",
  "symptom": "Paraphrase rounds a source list up to a category.",
  "evidence": "Quote + path + counterexample from the cited source.",
  "root_cause": "Generative rounding to the popular notion.",
  "fix_direction": "Prompt rule: only facts pointable-at in the source.",
  "verification": "eval/cases/b1-source-fidelity.yaml"
}]
```

```bash
findingledger merge --ledger bugfix/backlog.md --findings findings.json
```

The result tells you what was `created`, `updated` (counter bumped),
`unchanged` (same date already recorded — merging twice is safe) and
`escalation_due` (threshold reached: **report it to the human, do not
re-prioritize on your own**).

### Recipe B — write the test before the fix

For any open item, add a golden case with status `open`. It is allowed to
fail; it exists so success has a definition before anyone touches the prompt.

```yaml
id: b1-source-fidelity
status: open              # regression | open | sanity
backlog: B1-detail-beyond-source
since: 2026-08-08
rubric: |                 # or `check: <assertion-id>` for deterministic checks
  Every detail must be pointable-at in the cited source.
```

### Recipe C — after a fix lands: close the loop

```bash
findingledger fixed --ledger bugfix/backlog.md B1-detail-beyond-source \
  --note "prompt rule added, commit abc123"
findingledger graduate --cases eval/cases b1-source-fidelity
```

The case is now `regression`: if it ever fails again, that is an alarm.

### Recipe D — you were wrong

False positives are first-class. Retract instead of deleting — the history of
what the audit claimed stays visible.

```bash
findingledger retract --ledger bugfix/backlog.md PWT-01-repeat \
  --note "deliberate follow-up, not a repetition"
```

### Recipe E — check and report

```bash
findingledger check --cases eval/cases --results results.json   # exit 1 = alarm
findingledger trend --audits audits/ --window 7
findingledger report -o report.html                             # all projects
```

### Recipe F — someone else's tool already found the defects

You rarely have to transcribe results by hand. If the project runs promptfoo,
DeepEval, plain pytest (JUnit XML) or Ragas, read its output file directly —
the format is sniffed, so `--format` is only an override:

```bash
findingledger check  --project newsletter --results promptfoo-output.json
findingledger import --project newsletter --input promptfoo-output.json --dry-run
```

**Always `--dry-run` first, and read what comes back.** A finding whose
signature was *derived* (`promptfoo-gc-07`) names a symptom; before writing it,
try to name the mechanism instead — then either merge your corrected list with
`merge_findings`, or better, put the good signature into the test's metadata
(`metadata.signature` in promptfoo, `additional_metadata` in DeepEval, a
`findingledger_signature` property in JUnit) so every future run lands on the
right item by itself.

Traces and spans (`--format traces` / `spans`) are *mining*: they produce
candidates with `root_cause: unknown`, grouped by operation. Treat them as a
review queue, not as findings — rename before recording.

### Recipe G — notify, but only when it is worth it

```bash
findingledger alert --project newsletter --audit "audits/$(date +%F)-audit.md"
```

Silence is the normal outcome (`alerted: false`). Do **not** implement alerting
by deciding, in your own reasoning, that something is worth sending: run this
step unconditionally and let the frontmatter decide. A model that "knows" it
should notify the human is exactly what failed in production and caused this
command to exist.

### Working through MCP

If the host supports MCP, run `findingledger-mcp` (requires
`pip install "finding-ledger[mcp]"`) and use the tools instead of the CLI:
`list_projects`, `project_status`, `project_paths`, `ledger_items`,
`merge_findings`, `mark_fixed`, `retract_finding`, `graduate_case`,
`check_cases`, `check_results_file`, `import_tool_output`, `audit_trend`,
`alert`, `quality_scores`, `build_report`. Same semantics, structured
arguments. `import_tool_output` and `alert` default to `dry_run=true` — flip
that only when the human has seen what you are about to write or send.

### Rules for agents

- **Read before you write.** `project_status` / `ledger_items` first — an
  existing signature must be reused, not duplicated with a new name.
- **Every finding carries evidence**: a literal quote, a path, and a
  counterexample from the contract or an external source. Findings without
  evidence are expensive for humans to review and get deleted.
- **Do not invent severity inflation.** Escalation past the threshold is a
  recommendation you surface, not a change you make.
- **Never edit the ledger with free-form text tools** (sed, full-file
  rewrites, "regenerate the backlog"). Use the CLI/MCP operations; they are
  surgical by construction and preserve human prose.
- **Do not delete items.** Use `retract` or `fixed`.
- Write operations change files in someone's repository: run them when the
  task calls for it, and report exactly which signatures you touched.
