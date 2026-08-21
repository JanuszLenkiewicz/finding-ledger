# Getting started

Adopting finding-ledger in your own project, step by step. Every step is small
and independently useful — you can stop after step 5 and still get value.

## Before you start: do you need this?

There is exactly one prerequisite: **a source of findings**. It can be

- an LLM auditor that reviews production output against a contract,
- a person reading responses once a week,
- a deterministic linter,
- a bug report from a user.

The library is agnostic about the source. What it cares about is that a defect
gets a name for its *mechanism* and stops being a note that dies in a chat log.

If you don't yet have any of the above, start with the crudest version: read
ten production outputs by hand and write down what is wrong. That is a source
of findings.

## 1. Install

```bash
pip install -e /path/to/finding-ledger      # from a checkout, pre-PyPI
pip install "finding-ledger[mcp]"           # plus the agent-facing MCP server
```

One runtime dependency (`pyyaml`). Two commands land on your PATH:
`findingledger` (CLI) and `findingledger-mcp` (MCP server, with the extra).

## 2. Describe your project

Create `findingledger.yaml` at the root of your repository:

```yaml
name: my-project
description: One sentence about what this pipeline does
ledger: bugfix/backlog.md
cases: eval/cases
audits: audits/
```

Paths may be relative to this file or absolute — one real consumer keeps its
ledger in the code repo and its audits in a completely different repository,
and that works. You do not need all three keys on day one; the ledger alone is
enough to start.

## 3. Create the ledger

The ledger is a plain markdown file. Create it by hand with a single line:

```markdown
## Open
```

That's it — the rest is appended for you. Throughout the life of the project
this file stays human-readable and hand-editable: add your own paragraphs,
links and thinking, and the library will not destroy them. It performs
surgical edits only (bump one counter, flip one status token, add one note)
and never regenerates the file from a data model. That is what makes the
backlog reviewable in a pull request and readable a year later.

## 4. Write your first finding — the signature matters most

A signature is the identifier the library uses to recognise that today's defect
is yesterday's defect. **Name the mechanism, not the symptom.**

| Bad (symptom) | Good (mechanism) |
|---|---|
| `newsletter-too-long-aug-8` | `LEN-01-length-drift` |
| `mentor-said-there-were-heels` | `B1-detail-beyond-source` |
| `wrong-answer-ticket-412` | `retrieval-recall-regression` |

The difference is practical, not aesthetic. Name symptoms and after a month you
have a hundred entries with a counter of 1 each and no signal about priority.
Name mechanisms and you have one entry with a counter of 7 — and in this method
**the counter is the priority weight**. It is what tells you whether something
is an episode or a settled state.

Findings are JSON. The minimum is `signature` and `date`; everything else earns
its place:

```json
[{
  "signature": "B1-detail-beyond-source",
  "date": "2026-08-08",
  "title": "Detail added under a real citation",
  "severity": "critical",
  "class": "B1",
  "symptom": "Paraphrase rounds a source list up to a category.",
  "evidence": "\"...heels...\" (issues/2026-08-08.md:41); the cited article lists none.",
  "root_cause": "Generative rounding to the popular notion.",
  "fix_direction": "Prompt rule: only facts pointable-at in the cited source.",
  "verification": "eval/cases/b1-source-fidelity.yaml"
}]
```

The most important optional field is **`evidence`**: a literal quote, a path,
and a counterexample. A finding without evidence is expensive for a human to
review and will eventually be deleted as noise.

## 5. Merge findings into the ledger

```bash
findingledger merge --ledger bugfix/backlog.md --findings findings.json
```

The result is four lists:

| Field | Meaning |
|---|---|
| `created` | new entries |
| `updated` | counters bumped on existing entries |
| `unchanged` | that date was already recorded — **merging twice is safe** |
| `escalation_due` | entries that just crossed the recurrence threshold |

Note what the library does *not* do with `escalation_due`: it does not raise
severity by itself. It reports, you decide. That is the **write-little**
policy — the tool is a bookkeeper, not a judge.

## 6. Write the test *before* the fix

For each open entry, add a golden case — a YAML file in your cases directory:

```yaml
id: b1-source-fidelity
status: open                       # regression | open | sanity
backlog: B1-detail-beyond-source
since: 2026-08-08
rubric: |                          # or `check: <assertion-id>` for a deterministic rule
  Every detail must be pointable-at in the cited source.
```

`status: open` means: **this test is allowed to fail**, because it describes a
defect nobody has fixed yet. That is what lets you keep tests for every known
problem without breaking CI or a nightly cron. Success gets a definition before
anyone touches the prompt.

The third status, `sanity`, is for structural invariants that must always pass.

## 7. Close the loop after the fix

```bash
findingledger fixed --ledger bugfix/backlog.md B1-detail-beyond-source \
  --note "prompt rule added, commit abc123"
findingledger graduate --cases eval/cases b1-source-fidelity
```

History stays — counters, dates and evidence remain in the file; only the
status token changes. The case flips from `open` to `regression`, and from now
on a failure means one thing: **the defect came back**. This is the moment a
fixed bug becomes its own permanent guard.

## 8. When you were wrong

Automated audits sometimes report things that turn out to be correct on closer
inspection. Retraction is a first-class operation, not an embarrassed delete:

```bash
findingledger retract --ledger bugfix/backlog.md PWT-01-repeat \
  --note "deliberate follow-up, not a repetition"
```

The entry stays in place with its full history and an explicit `RETRACTED`
status. This matters: if you want to trust an automated auditor, you must be
able to measure how often it is wrong.

## 9. Run it in CI or cron

```bash
findingledger check --cases eval/cases --results results.json
```

Exit codes are designed for automation: a failing `open` case exits **0**
(expected state), while a failing `regression` or `sanity` case exits **1**
(alarm). You can wire this into CI without drowning in false alarms.

```bash
findingledger trend --audits audits/ --window 7
```

reads the YAML frontmatter of your audit files and compares the last N audits
against the previous N, per severity. Negative deltas mean things are getting
better. With no previous window it says so instead of inventing a verdict.

## 10. Multiple projects and the report

Add a machine-level registry at `~/.config/findingledger/projects.yaml` —
just a list of paths, deliberately outside every repository because it holds
local paths:

```yaml
projects:
  - ~/projects/newsletter
  - ~/projects/trading-mentor
```

```bash
findingledger report -o report.html
```

One self-contained HTML file, a tab per project: item counts, escalation
candidates, tri-state case status, audit history with a trend. No server, no
database — open it in a browser, commit it, or serve it from GitHub Pages.

## 11. Hand it to an agent

Two surfaces: [AGENTS.md](../AGENTS.md) with recipes and safety rules, and the
MCP server exposing the same operations as structured tools. See
[integrations.md](integrations.md#mcp-hosts).

## The rhythm in practice

- **Daily** — your auditor (human or model) reviews production and produces
  findings; `merge` files them; critical ones raise an alert.
- **When you sit down to fix** — sort the ledger by counter, take the top
  entry, write its case as `open`, fix, `fixed` + `graduate`.
- **Weekly or monthly** — generate the report and look at the trend. If the
  trend is rising, things are breaking faster than you are fixing them.

Each operation is one command, so a full cycle takes minutes.

## Three beginner mistakes

1. **Signatures that name symptoms.** After a month: a hundred entries, all
   with counter 1, no priority signal.
2. **Findings without evidence.** They read like opinions, so nobody fixes them.
3. **Treating a failing `open` case as a broken build** and disabling tests to
   get CI green. That inverts the method. A red `open` case is not a failure —
   it is recorded debt that knows when it will be paid off.
