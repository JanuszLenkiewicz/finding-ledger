# Adapter reference

Every adapter is a translation between a tool that answers *"what failed"* and
this library, which answers *"and what that means for the project"*. They live
in `findingledger.adapters`, are stdlib-only, and never require the tool they
adapt to be installed — they read its output files.

| Adapter | Direction | Reads / writes | `--format` |
|---|---|---|---|
| [promptfoo](#promptfoo) | in | `promptfoo eval -o out.json` (any schema version, JSONL too) | `promptfoo` |
| [DeepEval](#deepeval) | in | its test-run JSON (camelCase or snake_case) | `deepeval` |
| [JUnit XML](#junit-xml) | in | `pytest --junitxml`, and most other runners | `junit` |
| [Ragas](#ragas) | in | metric scores, aggregate or per-sample | `ragas` |
| [pytest plugin](#pytest-plugin) | in | the running test session itself | — |
| [OTLP / Phoenix](#otlp--phoenix-spans) | in (mining) | OTLP JSON or flat span records | `spans` |
| [Langfuse traces](#langfuse-traces-in) | in (mining) | exported trace records | `traces` |
| [Langfuse scores](#langfuse-scores-out) | out | score payloads pushed to a trace | — |
| [Alerts](#alerts) | out | stdout / file / webhook / command | — |
| [GitHub Actions](#github-actions) | out | annotations, job summary, step outputs | — |

Two verbs run everything:

```bash
findingledger check  --cases eval/cases --results <file>   # what does this mean?
findingledger import --input <file> --ledger bugfix/backlog.md   # remember it
```

Both sniff the format by looking inside the file, so `--format` is only needed
to override a wrong guess.

```python
from findingledger import adapters

adapters.results("promptfoo-output.json")   # {"GC-07": "FAIL", "SAN-01": "PASS"}
adapters.findings("promptfoo-output.json")  # [Finding(signature=...), ...]
adapters.detect("promptfoo-output.json")    # "promptfoo"
```

---

## Ids and signatures — the part worth getting right

An adapter has to answer two questions about every failing row:

1. **Which golden case is this?** → the *check id*. Resolution order:
   `findingledger_case`, `case_id`, `case`, `golden_case`, `id`, then the test
   description/name. Alternative spellings (a pytest nodeid, a promptfoo
   description, `classname.name`) are registered as **aliases**, so a case file
   matches whichever name its author used. An explicit id always wins over an
   alias.
2. **Which ledger item is this?** → the *signature*. Taken from
   `findingledger_signature`, `signature` or `backlog` when the tool carries
   one; otherwise **derived** from the check id (`promptfoo-gc-07`).

A derived signature names a *symptom*. That is fine as a starting point and
wrong as a permanent entry — counters only carry priority signal when one
signature means one **mechanism**. So: run `import --dry-run` first, rename
what you can, and put the good name back into the test's metadata, where it
will be reused forever after.

Multi-provider runs collapse: one case run against three providers is one
result. A single FAIL fails the case (`--lenient` / `strict=False` flips that).

---

## promptfoo

```bash
promptfoo eval -c eval/promptfooconfig.yaml -o promptfoo-output.json
findingledger check --cases eval/cases --results promptfoo-output.json
```

Wire the ids in `promptfooconfig.yaml` (full example:
[`examples/ci/promptfooconfig.yaml`](../examples/ci/promptfooconfig.yaml)):

```yaml
tests:
  - description: mentor cites the source figure
    vars: { case_id: GC-07 }
    metadata: { signature: B1-detail-beyond-source, severity: critical, class: B1 }
    assert:
      - type: contains
        value: "3.2%"
```

Accepted envelopes: `{"evalId": ..., "results": {"results": [...]}}`, the older
`{"results": [...]}`, a bare list, and JSON Lines. Failure evidence is built
from the failing `componentResults` (assertion type + reason), falling back to
`gradingResult.reason`, then `error`.

`promptfoo.stats(doc)` gives successes/failures/tokens/cost — useful as an
observability payload next to the lifecycle scores.

## DeepEval

```bash
findingledger check --cases eval/cases --results test_run.json --format deepeval
```

Key spellings differ across DeepEval releases (`testCases` / `test_cases` /
`test_results`, `metricsData` / `metrics_data` / `metrics_metadata`); all are
accepted. Carry the mapping in the test case:

```python
LLMTestCase(..., additional_metadata={"case_id": "GC-09", "signature": "HALL-01"})
```

Each metric also becomes an addressable check id — `GC-09::Faithfulness` — so a
golden case can guard one metric rather than a whole test. Metric rows are
*not* filed as separate findings: one failing test is one defect, otherwise the
counters inflate.

Running DeepEval through pytest? Skip the file entirely and use the
[plugin](#pytest-plugin).

## JUnit XML

The universal fallback — if a runner can emit JUnit, it is integrated:

```bash
pytest --junitxml=report.xml
findingledger check --cases eval/cases --results report.xml
```

`<failure>`/`<error>` → FAIL, `<skipped>` → SKIP, otherwise PASS. Ids come from
`<property>` entries when present:

```xml
<properties>
  <property name="findingledger_case" value="GC-07"/>
  <property name="findingledger_signature" value="B1-detail-beyond-source"/>
  <property name="findingledger_severity" value="critical"/>
</properties>
```

In pytest those come from the `record_property` fixture. The bare `classname`
is deliberately *not* an alias — it is shared by every test in a module, so one
red test would mark the whole module FAIL.

> Parsing uses `xml.etree`, which does not resolve external entities but is not
> hardened against entity-expansion bombs. Reports from your own CI are fine;
> for untrusted XML, pre-parse with `defusedxml`.

## Ragas

```bash
findingledger import --input ragas.json --format ragas \
  --ledger bugfix/backlog.md \
  --threshold context_recall=0.8 --threshold faithfulness=0.9 \
  --baseline .findingledger/ragas-baseline.json --save-baseline
```

Input: an aggregate mapping (`dict(result)`), per-sample records
(`result.to_pandas().to_dict(orient="records")`, averaged per metric), or
either wrapped in `{"scores": ...}`.

Two rules, two different meanings:

| Rule | Signature | Means |
|---|---|---|
| score under its bar | `ragas-{metric}-below-threshold` | the bar has been breached — possibly for months |
| score fell ≥ `--drop` vs baseline | `ragas-{metric}-regression` | **something you just did** caused it |

Defaults: faithfulness 0.85, answer_relevancy 0.80, context_precision 0.75,
context_recall 0.80, answer_correctness 0.70, `--drop` 0.10. Commit the
baseline file next to the ledger so a regression is attributable to a diff.
A `--dry-run` never moves the baseline — that would hide the very regression
the next run is looking for.

`ragas.parse()` also yields check ids (`ragas:context_recall`), so a `sanity`
case can guard retrieval quality directly:

```yaml
id: SAN-recall
status: sanity
check: ragas:context_recall
```

## pytest plugin

Installed with the package; inert until a `--fl-*` flag appears.

```bash
pytest --fl-cases eval/cases --fl-gate                     # tri-state gate
pytest --fl-project newsletter --fl-gate --fl-record          # + file the failures
pytest --fl-results results.json                           # + export for other tools
```

```python
@pytest.mark.finding("GC-07", signature="B1-detail-beyond-source", severity="critical")
def test_mentor_cites_the_source_figure():
    assert "3.2%" in answer
```

The first argument is the **case id** — it must match the `id` (or the `check`)
of a file in `--fl-cases`, here a case whose `id: GC-07`. That is the whole
binding; `signature` names the ledger item the case verifies, which is a
different identifier and is not used for matching. Absent a marker, the plugin
also tries the test's nodeid and its function name, so a case can be keyed by
either instead.

`--fl-gate` is the point: a failing test bound to an `open` case exits 0, a
failing `regression`/`sanity` case exits 1, and anything failing *outside* the
case map keeps the run red. This is the alternative to deleting red tests to
get CI green.

| Flag | Does |
|---|---|
| `--fl-cases DIR` | golden cases to evaluate against |
| `--fl-ledger FILE` | ledger for `--fl-record` |
| `--fl-project NAME` | take both from `findingledger.yaml` |
| `--fl-registry FILE` | registry `--fl-project` resolves against (rarely needed) |
| `--fl-results FILE` | write the `{check_id: PASS\|FAIL}` map |
| `--fl-gate` | failing `open` cases do not fail the run |
| `--fl-record` | merge the run's failures into the ledger |
| `--fl-date` | occurrence date (default: today) |

Long forms (`--findingledger-cases`, …) work too.

## OTLP / Phoenix spans

*Mining*, not gating: spans carry no pass/fail verdict, so this format only
produces **candidates** for review.

```bash
findingledger import --input spans.json --format spans \
  --latency-over 2000 --tokens-over 8000 --min-hits 3 --dry-run
```

Input: raw OTLP JSON (`resourceSpans → scopeSpans → spans`, attributes
unwrapped and hoisted, latency computed from the nano timestamps) or flat
records:

```python
import phoenix as px
px.Client().get_spans_dataframe().to_json("spans.json", orient="records")
```

Rules: `status_code == ERROR` (severity critical), latency over a bound, token
count over a bound — grouped by span name, so one finding covers an endpoint
rather than one unlucky request. `--min-hits` keeps one-off outliers out.

## Langfuse traces (in)

```bash
findingledger import --input traces.json --format traces \
  --cost-over 0.5 --latency-over 10 --score-below quality=0.6 \
  --host-url https://cloud.langfuse.com --dry-run
```

Production traces are the best finding source there is, because they are real
traffic rather than your imagination of it. Each candidate carries example
trace links as evidence and an explicit `root_cause: unknown` — it is triage,
not a diagnosis, and the signature is meant to be renamed once a human names
the mechanism.

## Langfuse scores (out)

The direction that makes quality visible where cost already is:

```bash
findingledger scores --project newsletter --target json          # inspect payloads
findingledger scores --project newsletter --trace-id <id>        # push
```

```python
from findingledger import service
service.score_payloads("newsletter")     # build (no SDK, no network)
service.push_scores("newsletter", trace_id="...")
```

Emitted scores: `findingledger.open_items`, `critical_open`, `escalation_due`,
`cases_regression` / `cases_open` / `cases_sanity`, `delta_critical`,
`verdict` (`improving` / `degrading` / `no-baseline`), `last_audit`.
`payloads_from_audit()` adds per-severity counts for a single audit.

Credentials come from `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
`LANGFUSE_HOST` — the library never stores them. Pushing needs the optional
extra (`pip install "finding-ledger[langfuse]"`); building and `--dry-run` do
not. A score must attach to something, so pass `--trace-id` or `--session-id`
(a run that cannot attach raises instead of silently dropping the score).

The client is injectable (`push_scores(..., client=...)`), which is how it is
tested — and how you point it at LangSmith/Braintrust wrappers with the same
`create_score(name, value, trace_id)` shape.

## Alerts

Deterministic and critical-only:

```bash
findingledger alert --project newsletter --audit audits/2026-08-08-audit.md
findingledger alert --audit audits/today-audit.md --name newsletter \
  --channel webhook --webhook "$SLACK_URL" --format markdown
findingledger alert --project newsletter --results promptfoo-output.json   # regression cases
```

Sources, in order of preference: an audit file's frontmatter, a results file (a
failing `regression`/`sanity` case is critical by definition), or the ledger's
current critical items. Channels: `stdout`, `file`, `webhook` (Slack-compatible
JSON `{"text": ...}`), `command` (argv reading the message on stdin — Telegram
bots, `osascript`, `notify-send`), `none`.

Defaults live in the project config, so a cron line stays short:

```yaml
alerts:
  channel: command
  command: telegram-send --stdin
  min_severity: critical
```

A relative `path:` in that block resolves against the project root, not
whatever directory cron started in. A channel failure never raises: the finding
is already safe in the ledger, and a crashed notification must not take the
rest of the run with it — errors come back in the receipt. Use
`--fail-on-alert` when a chained step should stop.

**Why this is a separate step:** an LLM auditor in production was instructed to
alert on critical findings. It found two, wrote them down correctly, and never
sent the alert. The send moved into code that reads the audit's frontmatter and
fires unconditionally. If a step must happen, it cannot depend on a model's
attention.

## GitHub Actions

```yaml
- uses: JanuszLenkiewicz/finding-ledger@main
  with:
    results: promptfoo-output.json
    cases: eval/cases
    record: 'true'        # also file the failures in the ledger
    report: report.html   # optional dashboard artefact
```

Outputs: `has_alarms`, `alarms`, `graduation_candidates`, `open_items`,
`critical_open`. Or call the CLI directly with `--github`:

```bash
findingledger check --project newsletter --results out.json --github
```

That emits `::error` annotations on the diff for alarms, `::notice` for `open`
cases ready to graduate, a job-summary table, and the step outputs above.
Escaping follows the workflow-command protocol, so a multi-line failure message
cannot break the log. Outside Actions the same command just prints — no
special-casing in your scripts.

Complete workflows: [`examples/ci/`](../examples/ci/) — PR quality gate,
pytest gate, nightly audit with alerting and a GitHub Pages dashboard.

---

## Writing a new adapter

About forty lines. Translate the tool's output into `ToolResult` rows and
everything else — aliasing, multi-provider aggregation, evidence flattening,
signature derivation, deduplication — comes from
`findingledger.adapters.common`:

```python
from findingledger.adapters.common import FAIL, PASS, ToolResult, oneline, read_json

def parse(data):
    return [ToolResult(id=row["name"], outcome=PASS if row["ok"] else FAIL,
                       aliases=[row.get("path", "")], reason=oneline(row.get("why")),
                       signature=row.get("signature"), source="mytool")
            for row in data["cases"]]

def load(path, id_keys=None):
    return parse(read_json(path))
```

Register it in `adapters.INBOUND`, add a fingerprint to `adapters.detect`, and
ship a test built on a *dirty* sample — real output files are irregular, and
that is the whole reason this layer exists.
