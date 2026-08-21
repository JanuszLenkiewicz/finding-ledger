# Project: finding-ledger — the finding-lifecycle layer for LLM evaluation

The maintainer keeps a working journal in `MEMORY.md` — current state, design
decisions, open threads. It is **local and untracked** (it records operational
detail from the consuming projects), so it exists only in a working copy that
has one. Read and update it when it is there; when it is not, `CHANGELOG.md`
and this file carry everything a contributor needs.

## What this is

A Python library (package `findingledger`) extracted from two production
implementations of the same method: a trading-mentor platform (promptfoo +
Langfuse + a cumulative bugfix ledger) and an unattended daily-newsletter
pipeline (deterministic lint + LLM auditor + ledger). Origin story and market
analysis live in the consumer projects' design docs.

**Positioning: complements promptfoo/DeepEval/Langfuse — does NOT compete.**
Those tools answer "did the tests pass?" and "what did production do?". This
library owns only the finding lifecycle:

```
audit finds a defect → deduplicated into a ledger by ROOT-CAUSE SIGNATURE (mechanism, not symptom)
→ occurrence counter = priority weight; threshold → escalation recommendation
→ golden case written BEFORE the fix (status: open — allowed to fail)
→ fix → case graduates to regression (a FAIL now means the bug is back: alarm)
→ false positives: RETRACTED with history, never deleted
```

## Design principles (non-negotiable)

1. **The ledger is a markdown document owned by humans, kept in git.** The
   library performs surgical edits only (bump a counter, flip a status token,
   add a note) — it NEVER regenerates the file. Hand-written prose must survive.
2. **Write little**: escalation past the threshold is REPORTED, never applied.
3. **Tri-state cases** `regression`/`open`/`sanity`; a failing `open` case
   exits 0 (a test written before the fix must not break CI/cron).
4. **Git is the metrics store**: trends come from audit-file frontmatter,
   zero external infrastructure.
5. **Tolerant parser, declared count authoritative** — hand-written ledgers
   contain formats like `26×/29 issues (partial dates)`; the `N×` in the file
   wins over the number of listed dates.
6. Single runtime dependency: `pyyaml`. Do not add dependencies without a
   strong reason. **Adapters read a foreign tool's output file, never import
   its SDK** — integrating promptfoo/DeepEval/Ragas/Phoenix costs no install.
   The one unavoidable SDK (`langfuse`, for *pushing* scores) is an optional
   extra imported lazily, and the offline path must keep working without it.
7. **A derived signature names a symptom and must say so.** Adapters prefer an
   explicit signature from the tool's metadata; what they derive is a starting
   point for a human rename, and trace/span mining files candidates with
   `root_cause: unknown`. One signature = one mechanism is what makes the
   counters mean anything.

## Structure

- `src/findingledger/` — code (PyPA src-layout): `models.py`, `ledger.py`,
  `cases.py`, `audits.py`, `report.py`, `service.py`, `cli.py`,
  `mcp_server.py` (optional extra), `pytest_plugin.py` (`pytest11` entry
  point), `adapters/` (foreign tools in, lifecycle state out), `py.typed`
- `tests/` — pytest; `examples/` — runnable examples; `examples/ci/` —
  copy-ready workflows; `conftest.py` at the root enables `pytester`
- `docs/` — documentation; `spec/` — specs for future features (written
  BEFORE implementation)
- `action.yml` — composite GitHub Action wrapping `findingledger check --github`
- `.github/workflows/ci.yml` — CI: pytest + ruff on 3.9/3.11/3.13, plus a job
  for the `[mcp]` extra and a step that runs every example
- Community files: CHANGELOG (Keep a Changelog), CONTRIBUTING, AGENTS.md,
  CODE_OF_CONDUCT, SECURITY, LICENSE (MIT), .editorconfig
- venv: `.venv` (`python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`)

## Library consumers

The library code is curated ONLY here. Consuming projects install it
(`pip install -e <path-to-this-repo>`, or run `python -m findingledger.cli`),
never by copying code into themselves.

Each consumer keeps its own ledger, cases and audits and self-describes with a
`findingledger.yaml` at its root; the machine-level registry in
`~/.config/findingledger/projects.yaml` lists where they live. Which projects
those are is local configuration, deliberately not recorded in this repo.

## Working rules

- **Language: everything in this repository is English** — code, docs,
  CLAUDE.md, MEMORY.md, commit messages (decision 2026-08-08; the repo is
  headed for public release).
- **Git:** commit + push to main (no PRs) while private — the owner's solo
  standard. Tests and ruff must pass before every push.
- **Any change to ledger parsing/editing** ships with a test on a *dirty*,
  hand-written format variant — real ledgers are irregular.
- **Any change to an adapter** ships with a test on a *real-world irregular*
  sample: an old envelope, a foreign key spelling, a multiline failure message,
  a missing id. Tool output files are as irregular as ledgers.
- **Publishing** — releasing to PyPI or changing repository visibility — is the
  owner's decision, never automatic.
