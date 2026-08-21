# How it works — where the data comes from

The single most common question about the report is *"where do these numbers
come from?"*. This page answers it by tracing every figure back to a line in a
file.

## The one sentence that explains everything

**This tool has no database, no server, no telemetry and no integrations that
phone home. It measures nothing by itself — it reads text files that live in
your repositories and turns them into a view.**

It is a reader, not a collector. If something is not in the files, it is not on
the screen; and everything on the screen can be pointed at with a finger in a
specific line of a specific markdown file. That is deliberate: it means the
entire history of your quality lives in git and outlives every tool, including
this one.

## How it finds your files: a chain of three links

```
~/.config/findingledger/projects.yaml     ← link 1: which projects exist (local paths, no repo)
        │
        ▼
<project>/findingledger.yaml              ← link 2: where this project keeps its three inputs
        │
        ▼
ledger.md · cases/*.yaml · audits/*.md    ← link 3: the actual data
```

When you run `findingledger report`, it reads the registry, opens each
project's YAML, learns the paths, parses three sets of files and renders one
HTML file. The whole thing takes a fraction of a second and writes nothing
except that report.

## The three inputs

| Input | Format | Who writes it | What the report derives |
|---|---|---|---|
| **Ledger** | one markdown file | your audit (via `merge`) + humans by hand | open/closed/critical counts, occurrence counters, escalation candidates |
| **Cases** | one YAML file per case | humans, when a defect gets a test | tri-state status chips, coverage summary |
| **Audits** | markdown files with YAML frontmatter | your auditor, once per run | audit count, last audit date, severity history, trend |

Nothing else is read. In particular the report reads only the **frontmatter**
of audit files, never their prose — which is why the history and the chart cost
nothing to compute.

## Tracing a real report, number by number

Using a live example from a newsletter pipeline whose ledger has 8 entries:

**"6 open items."** The parser scans the ledger for level-3 headings shaped
`### [signature] STATUS — title`. It finds 8, then drops those whose status
starts with `FIXED`, `RETRACTED` or `✅`. Six remain. This number is not stored
anywhere — it is recomputed from the file's current content on every run.

**"2 critical (open)."** Of those six, the ones whose status token starts with
`CRITICAL` or `🔴`.

**"3 escalation candidates."** A derived list, not a stored one: entries whose
counter is at or above the threshold (default 3) but which are *not yet* marked
critical and not closed. In other words: things recurring often enough to look
like a settled state while still carrying a low severity. This is a list for a
human to decide on — the library never promotes them itself.

**"26×" on the length-drift entry.** This is not a counter that grew over 26
days. It came from a backtest: a linter ran over all 29 archived issues and
counted how many exceeded the word limit. The result was written into the
ledger by hand as `26×/29 issues (...)` — with only three sample dates, not 26.
Hence the rule that **the declared `N×` outranks the number of listed dates**.
Without it the report would show 3 and the priority would be a lie.

**"3×" on the source-fidelity entry.** Different provenance: three genuine,
separate events. One found by the *end user* three weeks ago, two found by the
automated auditor today. All three share a signature because the mechanism is
identical — the model rounds a paraphrase toward a popular notion and adds a
detail under someone else's citation.

**An entry with counter `0×`.** Looks odd, but is honest: a closed entry about
a false fact that never appeared in the audited medium (the leak was in a
different channel), recorded as `0×/29 issues`. The parser reads that as zero
and shows zero.

**Golden case chips.** Literally one YAML file each, showing that file's
`status` field. Nothing more is hidden behind them.

**The audits row and the bar chart.** From the frontmatter that your auditor
writes at the top of its output file:

```markdown
---
date: 2026-08-08
n_findings: 5
by_severity: {critical: 2, major: 2, minor: 1}
by_class: {A: 2, B: 2, C: 1}
---
```

## Who fills these files? Not this tool

This is the heart of the answer. The report only displays. The files are
produced by **your loop**, which runs independently:

```
11:00 cron  →  generate output  →  send  →  auditor run (LLM)  →  writes audits/<date>-audit.md
                                                              →  calls `merge` → ledger.md
                                                              →  git commit
                    (any time)  →  you write a case → cases/*.yaml
                    (any time)  →  findingledger report  ← read-only, optional, last link
```

The report is the last, entirely optional link. You can never generate it and
the loop still works. That is a good test of the architecture: the presentation
layer can disappear without anything breaking.

Two consumers of this library have completely different rhythms feeding the
same view: one writes its ledger from an unattended daily cron, the other from
a skill a human invokes by hand. One keeps audits next to its code, the other
in a personal knowledge vault in a different repository. The tool does not
care — it comes to the data where the data already is.

## Where *your* data will come from

A newcomer installing the library has no data, and the library will not invent
any. You need your own source of findings. Three levels, each usable alone:

1. **Manual.** An empty `## Open` file; after each review of your system's
   output you add one finding with `merge`. Works from minute one, needs no
   automation.
2. **Semi-automatic.** A small script checking hard rules (format, required
   fields, forbidden phrases, length) whose failures become findings.
3. **Full.** An LLM auditor that reads production against a contract once a day
   and produces findings itself.

One production consumer runs all three levels at once, but they are
independent — start at level 1.

## A worked example of why "no data" beats "pretty data"

While tracing these numbers for the first time, a real bug surfaced: a project
with only one audit displayed *"critical +2, major +2, minor +1 — improving ↓"*.
Positive deltas cannot mean improvement. The cause: with a single audit the
comparison window is empty, and the code read "not degrading" as "improving".

The fix was not to compute a cleverer number but to **refuse to make a claim**:
with no baseline the report now says *"no trend yet — 1 audit(s), nothing to
compare against"*. A regression test guards it.

That is the principle behind the whole library: a report that lies elegantly is
worse than a report that admits it lacks data.

## Summary

This is not a dashboard wired into your system. It is a view over files you
keep in git anyway. Everything visible is pointable-at in text, and if you
delete the tool, the data stays.
