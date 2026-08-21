# Contributing to finding-ledger

Thanks for your interest! This project is young and small on purpose — issues
and discussions are as valuable as code.

## Development setup

```bash
git clone https://github.com/JanuszLenkiewicz/finding-ledger
cd finding-ledger
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Before you submit

```bash
.venv/bin/python -m pytest -q       # all tests must pass
.venv/bin/python -m ruff check src tests examples conftest.py
```

## Ground rules (they mirror the library's own philosophy)

1. **The ledger is a human-owned document.** Any change to parsing or editing
   must keep hand-written prose intact and must ship with a test on a
   *dirty*, hand-written format variant — real ledgers are irregular.
2. **Write little.** The library recommends (e.g. escalation), humans decide.
   Don't add behavior that silently rewrites judgment calls.
3. **No new runtime dependencies** without a strong reason (`pyyaml` is the
   only one today). Adapters must be stdlib-only: they read a tool's *output
   file*, they do not import its SDK, so integrating a tool never costs an
   install. Where an SDK is genuinely required (pushing Langfuse scores), it
   goes in an optional extra, is imported lazily, and the offline path keeps
   working without it.
4. **Scope discipline:** finding-ledger is the finding-lifecycle layer. Assertion
   running belongs to promptfoo/DeepEval, tracing to Langfuse/Phoenix —
   integrations are welcome as thin adapters, not reimplementations.

**New adapters are very welcome** and cost about forty lines — translate the
tool's output into `ToolResult` rows and the shared layer handles the rest.
See [writing a new adapter](docs/adapters.md#writing-a-new-adapter), and ship
it with a test built on a *dirty* real-world sample (an old schema version, a
foreign key spelling, a multiline failure message).

## Commit style

Short imperative subject, body explains *why*. Reference ledger signatures or
issues where relevant.
