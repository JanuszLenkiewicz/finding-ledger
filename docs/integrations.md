# Integrations and best practices

Which tools finding-ledger works with, how they fit together, and how to
combine them well.

> This page is the **map**: what each tool is for and why the pieces belong
> together. For the exact flags, file shapes and id conventions of each
> adapter, see the **[adapter reference](adapters.md)**.

## The map: four layers of the LLM quality stack

All the tool names in this space blur together until you separate what they
actually do:

| Layer | Question it answers | Tools |
|---|---|---|
| **1. Assertions** | Did the tests pass? | promptfoo, DeepEval, pytest |
| **2. Observability** | What did production do (cost, latency, inputs)? | Langfuse, LangSmith, Braintrust, Phoenix |
| **3. Judges** | Is this answer any good, where regex can't tell? | LLM-as-judge (built into all of the above) |
| **4. Finding lifecycle** | What happened to the defect *after* we found it? | **finding-ledger** |

finding-ledger deliberately stays out of layers 1–3. It runs no tests, collects
no traces, scores no answers. It plugs in *underneath* them and turns what they
emit into long-term memory.

---

## Layer 1 — assertion runners

### promptfoo

YAML-configured, renders real prompts, calls the model, checks answers with
deterministic assertions (`contains`, `not-contains`, `regex`, custom
functions) and model-graded rubrics. Also does model comparison and a local
result viewer.

**How it connects:** promptfoo produces pass/fail per case; `findingledger
check` consumes those results and confronts them with the tri-state status.
promptfoo has no concept of a case that is *allowed* to fail — to it, a red test
is a red test. We add that meaning from the outside.

> promptfoo answers "what failed"; finding-ledger answers "and what that means
> for the project".

```bash
promptfoo eval -c eval/promptfooconfig.yaml -o out.json
findingledger check  --cases eval/cases --results out.json   # exit 1 only on real alarms
findingledger import --input out.json --ledger bugfix/backlog.md --dry-run
```

No conversion step: the adapter reads promptfoo's own output file (any schema
version it has shipped) and sniffs the format itself. Put `case_id` in a test's
`vars` and the mechanism in `metadata.signature`, and the mapping is exact —
see [adapters.md](adapters.md#promptfoo) and
[`examples/ci/promptfooconfig.yaml`](../examples/ci/promptfooconfig.yaml).

### DeepEval

The Python-native equivalent, shaped like pytest, with ready metrics
(faithfulness, answer relevancy) and a natural-language-criterion judge
(G-Eval). Better fit for Python teams with pytest-based CI.

**How it connects:** same in spirit — and because DeepEval runs under pytest,
the shortest integration in the package applies: the bundled **pytest plugin**,
which needs no export file at all.

```bash
pytest --fl-cases eval/cases --fl-gate --fl-record
```

```python
@pytest.mark.finding("GC-07", signature="B1-detail-beyond-source", severity="critical")
def test_mentor_cites_the_source_figure():
    assert_test(test_case, [FaithfulnessMetric(threshold=0.8)])
```

The marker's first argument is the **case id** and must match a case's `id` (or
`check`) in `--fl-cases` — a case with `id: GC-07` here. `signature` is a
different identifier: the ledger item that case verifies.

`--fl-gate` is the load-bearing flag: a failing test bound to an `open` case
exits 0 (it was written before the fix, on purpose), a failing `regression`
case exits 1, and a failure outside the case map keeps the run red. DeepEval's
own JSON test-run file works too (`--format deepeval`), including per-metric
check ids like `GC-09::Faithfulness`.

### Ragas

Specialised in RAG: measures retrieval quality and answer faithfulness against
retrieved context. Tells you whether hallucinations come from retrieval or from
generation.

**How it connects:** Ragas metrics are an excellent source of findings. If
recall drops from 85% to 60% after a chunking change, that is a finding with
the signature `ragas-context-recall-regression`, a counter, and a case that
guards it afterwards.

```bash
findingledger import --input ragas.json --format ragas --ledger bugfix/backlog.md \
  --threshold context_recall=0.8 --baseline .findingledger/ragas-baseline.json \
  --save-baseline
```

The adapter separates two things a single number hides: *below the bar*
(possibly broken for months) and *regression against a committed baseline*
(something you just did). Only the second one deserves attention today.

---

## Layer 2 — observability

### Langfuse (and LangSmith, Braintrust, Phoenix)

Trace per request, generation per model call, span per helper step; plus
*scores* attached to traces, and datasets. Phoenix builds the same idea on
OpenTelemetry, which suits teams that already run Otel and Grafana.

**The integration is bidirectional, and that is what makes it interesting:**

- **Platform → ledger.** Production traces are a first-rate source of findings.
  Review the most expensive, slowest or lowest-scored calls, spot a pattern,
  record it as a finding with a signature.
- **Ledger → platform.** Push audit statistics (findings total, criticals,
  overall verdict) as scores onto that day's traces. Then cost, latency and
  quality sit in one view.

One production consumer does exactly this. It is also the most demonstrable
artefact in a job interview: *"my system's quality is visible in the same
dashboard as its cost."*

```bash
# ledger → platform: quality scores onto the day's trace
findingledger scores --project newsletter --trace-id "$TRACE_ID"

# platform → ledger: mine the worst traces into finding candidates
findingledger import --input traces.json --format traces \
  --cost-over 0.5 --score-below quality=0.6 --min-hits 3 --dry-run
```

Pushing needs `pip install "finding-ledger[langfuse]"` and the standard
`LANGFUSE_*` environment variables; building the payloads (`--target json`) is
stdlib and offline, so you can see exactly what would be sent before wiring a
key. Mined candidates arrive with `root_cause: unknown` and a symptom-shaped
signature on purpose — they are triage, and a human renames them once the
mechanism is known.

### MLflow

A different world: experiment tracking, model registry, versioning — "which
model version with which hyperparameters produced which result". Relevant if
you fine-tune your own models. finding-ledger sits beside it, because "which
behavioural defects exist and do they come back" is independent of whether the
model is fine-tuned or called over an API.

---

## Layer 3 — judges

LLM-as-judge is a pattern, not a product; every tool above supports it. Two
risks worth naming out loud:

- **Self-preference bias** — a model favours text in its own style. Mitigation:
  judge with a *different model family* than the one under test (e.g. test
  Gemini, judge with Claude Haiku).
- **Who validates the validator** — calibrate the judge against a
  human-labelled sample before trusting it.

If a different family is impossible (e.g. a subscription-only, zero-API-key
constraint), separate *roles and contracts* instead: the auditor is not asked
"is this good?" but "is this compliant with this contract, here is the
evidence". Then name the compromise explicitly rather than pretending it does
not exist.

---

## Layer 4 and its neighbours

### git and GitHub

Underrated, and not a joke. Because the ledger is markdown rather than a
database, you get for free what no tool on this page offers:

- quality history *is* commit history; `git blame` on an entry shows who added
  it and when;
- a status change is reviewable in a pull request, with comments;
- the HTML report can be committed or served from GitHub Pages — a public
  dashboard with zero servers;
- `check` exits 1 only on real alarms, so it drops into GitHub Actions as an
  ordinary step and becomes a quality gate on pull requests.

The gate ships as a composite action:

```yaml
- uses: JanuszLenkiewicz/finding-ledger@main
  with:
    results: promptfoo-output.json
    cases: eval/cases
```

It annotates the diff (`::error` for alarms, `::notice` for `open` cases ready
to graduate), writes a job-summary table, and exposes `has_alarms`, `alarms`,
`open_items` and `critical_open` as step outputs. `findingledger check --github`
does the same from any shell, and prints harmlessly outside Actions. Complete
workflows — PR gate, pytest gate, nightly audit with alerting and a Pages
dashboard — live in [`examples/ci/`](../examples/ci/).

### MCP hosts

With the MCP server registered, the library stops being a tool and becomes an
agent's memory. You can say "what's open in this project?", "record the defect
we just found", "close that entry, I fixed it". The agent's context window
disappears between sessions; the ledger stays in the repository.

```bash
pip install "finding-ledger[mcp]"
findingledger-mcp
```

```json
{"mcpServers": {"finding-ledger": {"command": "findingledger-mcp"}}}
```

Fifteen tools, including the integration ones: `check_results_file` (confront
the cases with a runner's output file), `import_tool_output` (file its failures
— `dry_run` on by default, because derived signatures need a human rename),
`alert` and `quality_scores`.

### Schedulers and alert channels

cron, launchd, GitHub Actions on a schedule, a cloud queue — this is the layer
that makes the loop run without a human. And one rule about alerts:

> Alert **only** on critical findings. Everything else waits in the ledger.
> Notify on every trifle and within a week nobody reads the notifications.

That rule is the default rather than a discipline you have to keep:

```bash
findingledger alert --project newsletter --audit "audits/$(date +%F)-audit.md"
```

It reads the audit's frontmatter, stays silent unless something reaches
`critical`, and sends through the channel configured in the project's
`findingledger.yaml` (`stdout`, a file, a Slack-compatible webhook, or any
command reading stdin). It is a *separate step from the auditor* on purpose —
see best practice 6 below.

### Runtime guardrails

Pydantic schema validation, structured outputs, content filters. Worth
understanding the difference in role, because it is a common confusion:

| Component | Tense | Job |
|---|---|---|
| Guardrail | now | **block** a bad answer before the user sees it |
| Eval | later | **measure** how often the system is wrong |
| Ledger | across time | **remember** whether this defect returns |

Three different verbs: block, measure, remember. A healthy system has all
three, not one instead of the others.

---

## Three recipes by scale

### Minimal — no API keys, no external services

A deterministic rule-checking script in pure Python. An LLM auditor invoked
from a subscription CLI for what regex cannot settle. Ledger and cases as files.
HTML report locally. An alert only on critical findings.

Zero infrastructure, zero cost beyond the subscription — and still a complete
loop: detect, record, fix, guard. Recommended as everyone's starting point.

```bash
python tools/lint.py --out results.json          # your own deterministic checks
findingledger merge --ledger bugfix/backlog.md --findings findings.json
findingledger check --cases eval/cases --results results.json
findingledger alert --project mine --audit "audits/$(date +%F)-audit.md"
findingledger report -o report.html
```

### Mid-size — a team with CI

Add promptfoo or DeepEval, run in GitHub Actions on every prompt change. The
gate is simple: `check` lets failing `open` cases through and blocks the merge
when a `regression` or `sanity` case fails. Ledger and cases live in the same
repo as the code, so a prompt change and the test that guards it land in one
pull request. This is where the method starts working for a team rather than
one person.

```yaml
- uses: JanuszLenkiewicz/finding-ledger@main
  with: { results: promptfoo-output.json, cases: eval/cases, record: 'true' }
```

Python team? `pytest --fl-cases eval/cases --fl-gate --fl-record` does the same
thing without an intermediate file. Copy-ready workflows:
[`examples/ci/`](../examples/ci/).

### Full — production system with real traffic

Add Langfuse or Phoenix and instrument every model call. Traces give you cost,
latency and review material at once. Weekly, someone (human or agent) reviews
the worst traces and converts patterns into findings. Audit statistics flow
back as scores. With RAG, add Ragas and track retrieval as its own finding
family. An agent handles the paperwork over MCP.

```bash
# weekly: mine the worst traffic into candidates a human then renames
findingledger import --input traces.json --format traces --cost-over 0.5 \
  --score-below quality=0.6 --min-hits 3 --dry-run
# daily: RAG retrieval as its own finding family
findingledger import --input ragas.json --format ragas --project mine \
  --baseline .findingledger/ragas-baseline.json --save-baseline
# after each audit: quality lands where cost already is
findingledger scores --project mine --trace-id "$TRACE_ID"
```

---

## Eight best practices

1. **Deterministic before judge.** Anything checkable in code — structure,
   required sections, forbidden phrases, length, format — check in code. Leave
   the model only what regex cannot settle. *"Deterministic code counts, the
   model interprets."*
2. **Judge from a different model family** than the system under test; if
   impossible, separate roles and contracts, and name the compromise.
3. **Ground truth from outside the system.** An auditor that judges text using
   only text goes in circles. It needs a reference the pipeline did not
   generate: the real source article, market data, a user's own words.
4. **One signature = one mechanism.** Name the cause, not the symptom, or
   counters never grow and you lose all priority signal.
5. **Test before fix.** Every open entry gets an `open` case before anyone
   touches the prompt, so success is defined up front instead of "it feels
   better now".
6. **Enforce critical steps deterministically, not by prompt.** Learned the
   hard way: an LLM auditor was instructed to alert on critical findings and
   silently skipped it — while itself finding two. The alert moved into a
   script that reads the audit's frontmatter and fires unconditionally. **If a
   step must happen, it cannot depend on a model's attention.** That script is
   now `findingledger alert`, a step *after* the auditor rather than an
   instruction inside it.
7. **Humans in the loop on both sides.** Post-hoc review (the auditor runs
   autonomously, but every finding carries evidence so review is cheap, and
   false positives get an explicit RETRACTED status) *and* the user as ground
   truth (a defect a user finds that your system missed is a finding against
   the evaluation system itself — worth tracking as a metric).
8. **Don't build a dashboard before you have data.** No server and no database
   is a feature: at one audit a day, an HTML file generated on demand is
   plenty, and every extra component is something that breaks. Scale
   infrastructure to traffic, not to ambition.

## Three anti-patterns

- **An uncalibrated judge** — trusting model scores nobody ever compared to
  human labels on a sample.
- **Disabling red tests to get CI green** — that is what `open` status exists
  for: a red test that does not break the pipeline (`findingledger check`, or
  `pytest --fl-gate`).
- **Building your own assertion framework** when promptfoo and DeepEval exist
  and are good. Build only what is genuinely missing — which, as far as we can
  tell, is the finding-lifecycle layer.

---

## Summary

finding-ledger is by design a second-fiddle citizen. It does not want to be
your main tool and does not replace anything you already run. It wants to be
the long-term memory of your quality — the place where a defect found today by
promptfoo, an agent, or a user stops being a note and becomes an entry with a
counter, a test and a history.
