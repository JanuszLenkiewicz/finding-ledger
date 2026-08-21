# Documentation

| Guide | Read it when |
|---|---|
| [Getting started](getting-started.md) | You want to adopt the library — 11 steps from install to a running loop, plus the rhythm in practice and the three beginner mistakes. |
| [How it works](how-it-works.md) | You want to know where the data comes from — every number in the report traced back to a line in a file, and who writes those files. |
| [Integrations and best practices](integrations.md) | You already use promptfoo / DeepEval / Ragas / Langfuse / MLflow and want to know how they fit together — plus three recipes by scale, eight best practices, three anti-patterns. |
| [Adapter reference](adapters.md) | You are wiring one of those tools up right now and need the flags, file shapes, id conventions — and how to write an adapter for a tool that has none. |
| [AGENTS.md](../AGENTS.md) | An AI agent will run the loop (or contribute to this repo). |
| [demo-report.html](demo-report.html) | You want to see what the report looks like — synthetic data, regenerate with `python examples/demo_report.py`. |
| [examples/ci/](../examples/ci/) | You want copy-ready CI: a PR quality gate, a pytest gate, a nightly audit with alerting and a GitHub Pages dashboard. |

Specifications for features that do not exist yet live in [`../spec/`](../spec/).

API reference: the module docstrings in `src/findingledger/` are the reference —
`ledger.py`, `cases.py`, `audits.py`, `report.py`, `service.py`,
`pytest_plugin.py` and the `adapters/` package.
