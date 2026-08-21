"""Multi-project HTML report.

Separation model (three layers):

1. **Data lives in each consumer project** — ledger, cases and audits stay in
   the project's own repo, declared by a ``findingledger.yaml`` at its root::

       name: newsletter
       description: Unattended daily newsletter with an LLM mentor
       ledger: bugfix/backlog.md
       cases: eval/cases
       audits: audits

   Relative paths resolve against the file's directory.

2. **A machine-level registry lists projects** — ``~/.config/findingledger/
   projects.yaml`` holds local paths only and belongs to no repo::

       projects:
         - ~/projects/newsletter
         - ~/projects/trading-mentor

3. **The library knows no project** — it renders whatever the registry points
   at, into one self-contained HTML file (inline CSS, no external assets,
   light/dark aware). No server, no database: open the file in a browser.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .audits import load_audits, trend
from .cases import load_cases
from .ledger import DEFAULT_ESCALATE_AT, Ledger

REGISTRY_DEFAULT = Path("~/.config/findingledger/projects.yaml")
PROJECT_FILE = "findingledger.yaml"

CLOSED_PREFIXES = ("FIXED", "RETRACTED", "✅")
CRITICAL_PREFIXES = ("CRITICAL", "🔴")


@dataclass
class ProjectData:
    name: str
    root: str
    description: str = ""
    error: str = ""
    items: list = field(default_factory=list)          # LedgerItem, open first
    n_open: int = 0
    n_closed: int = 0
    n_critical: int = 0
    escalation_candidates: list = field(default_factory=list)
    cases: list = field(default_factory=list)          # (id, status)
    audits: list = field(default_factory=list)         # AuditSummary
    trend: dict = field(default_factory=dict)


def resolve_config_path(base: Path, p: str) -> Path:
    """Resolve a path declared in ``findingledger.yaml``.

    Absolute and ``~`` paths are taken as-is; anything else is relative to the
    config file's own directory, never to the caller's cwd — a cron entry must
    not resolve a project's paths differently than a shell in the repo does.

    This is the single rule for config-declared paths; :mod:`.service` resolves
    through it too, so the report and the MCP/CLI layers can never drift.
    """
    raw = Path(str(p)).expanduser()
    return raw if raw.is_absolute() else base / raw


def load_project(root: Path, escalate_at: int = DEFAULT_ESCALATE_AT) -> ProjectData:
    root = Path(root).expanduser()
    cfg_path = root / PROJECT_FILE if root.is_dir() else root
    base = cfg_path.parent
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except OSError as e:
        return ProjectData(name=str(root), root=str(root), error=f"cannot read {cfg_path}: {e}")

    data = ProjectData(name=str(cfg.get("name", base.name)), root=str(base),
                       description=str(cfg.get("description", "")))

    if cfg.get("ledger"):
        try:
            ledger = Ledger(resolve_config_path(base, cfg["ledger"]))
            is_closed = lambda i: i.status.upper().startswith(CLOSED_PREFIXES)  # noqa: E731
            is_crit = lambda i: i.status.upper().startswith(CRITICAL_PREFIXES)  # noqa: E731
            data.items = sorted(ledger.items.values(),
                                key=lambda i: (is_closed(i), -i.count))
            data.n_open = sum(1 for i in data.items if not is_closed(i))
            data.n_closed = len(data.items) - data.n_open
            data.n_critical = sum(1 for i in data.items if not is_closed(i) and is_crit(i))
            data.escalation_candidates = [
                i for i in data.items
                if i.count >= escalate_at and not is_closed(i) and not is_crit(i)]
        except Exception as e:  # a broken ledger must not kill the whole report
            data.error = f"ledger: {e}"

    if cfg.get("cases"):
        cases_dir = resolve_config_path(base, cfg["cases"])
        if cases_dir.is_dir():
            data.cases = load_cases_tolerant(cases_dir)

    if cfg.get("audits"):
        audits_dir = resolve_config_path(base, cfg["audits"])
        if audits_dir.is_dir():
            data.audits = load_audits(audits_dir, str(cfg.get("audits_pattern", "*-audit.md")))
            if data.audits:
                data.trend = trend(data.audits, window=int(cfg.get("trend_window", 7)))
    return data


def load_cases_tolerant(cases_dir: Path) -> list:
    """(id, status) pairs even for foreign case schemas (e.g. promptfoo files
    that only share the tri-state ``status:`` convention)."""
    out = []
    try:
        for c in load_cases(cases_dir):
            out.append((c.id, c.status))
        return out
    except Exception:
        pass
    for p in sorted(Path(cases_dir).glob("*.y*ml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            out.append((str(d.get("id", p.stem)), str(d.get("status", "?"))))
        except Exception:
            out.append((p.stem, "?"))
    return out


def load_registry(path: Path | None = None) -> list[Path]:
    reg_path = Path(path).expanduser() if path else REGISTRY_DEFAULT.expanduser()
    cfg = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
    return [Path(str(p)).expanduser() for p in cfg.get("projects", [])]


# ── rendering ──────────────────────────────────────────────────────────────

CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --muted:#667; --card:#f5f6f8; --line:#e2e4e9;
        --crit:#c0392b; --major:#b9770e; --minor:#7d8a96; --ok:#1e8449; --accent:#2c5aa0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161a; --fg:#e8e9eb; --muted:#9aa; --card:#1e2127; --line:#2c3038;
          --crit:#e74c3c; --major:#e6a23c; --minor:#8a97a3; --ok:#2ecc71; --accent:#6ea8fe; } }
* { box-sizing:border-box } body { margin:0; font:15px/1.5 system-ui,sans-serif;
  background:var(--bg); color:var(--fg); padding:1.5rem; max-width:1080px; margin:auto }
h1 { font-size:1.4rem } h2 { font-size:1.1rem; margin-top:1.6rem }
.tabs { display:flex; gap:.4rem; flex-wrap:wrap; margin:1rem 0 }
.tabs button { padding:.45rem .9rem; border:1px solid var(--line); background:var(--card);
  color:var(--fg); border-radius:8px; cursor:pointer; font-size:.95rem }
.tabs button.active { background:var(--accent); color:#fff; border-color:var(--accent) }
.cards { display:flex; gap:.8rem; flex-wrap:wrap; margin:.8rem 0 }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:.7rem 1rem; min-width:130px } .card b { font-size:1.5rem; display:block }
.card span { color:var(--muted); font-size:.82rem }
table { border-collapse:collapse; width:100%; font-size:.9rem; margin:.5rem 0 }
th,td { text-align:left; padding:.35rem .6rem; border-bottom:1px solid var(--line);
  vertical-align:top } th { color:var(--muted); font-weight:600 }
.overflow { overflow-x:auto }
.sig { font-family:ui-monospace,monospace; font-size:.82rem }
.badge { display:inline-block; padding:.05rem .5rem; border-radius:99px; font-size:.78rem;
  color:#fff; white-space:nowrap }
.b-crit{background:var(--crit)} .b-major{background:var(--major)} .b-minor{background:var(--minor)}
.b-ok{background:var(--ok)} .b-other{background:var(--muted)}
.bar { display:inline-block; height:.7rem; background:var(--crit); border-radius:2px;
  vertical-align:middle } .muted { color:var(--muted) } .err { color:var(--crit) }
.cases { display:flex; flex-wrap:wrap; gap:.45rem }
.case { display:inline-flex; align-items:center; gap:.45rem; background:var(--card);
  border:1px solid var(--line); border-radius:8px; padding:.22rem .6rem;
  max-width:100%; } .case .sig { overflow-wrap:anywhere }
footer { margin-top:2rem; color:var(--muted); font-size:.8rem }
"""

JS = """
function show(i){document.querySelectorAll('.proj').forEach((p,j)=>p.style.display=i===j?'':'none');
document.querySelectorAll('.tabs button').forEach((b,j)=>b.classList.toggle('active',i===j));}
"""


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _status_badge(status: str) -> str:
    u = status.upper()
    if u.startswith(CRITICAL_PREFIXES):
        cls = "b-crit"
    elif u.startswith(CLOSED_PREFIXES):
        cls = "b-ok"
    elif "NICE" in u or "MINOR" in u:
        cls = "b-minor"
    else:
        cls = "b-major"
    return f'<span class="badge {cls}">{_e(status)}</span>'


def _case_badge(status: str) -> str:
    cls = {"regression": "b-ok", "open": "b-major", "sanity": "b-other"}.get(status, "b-other")
    return f'<span class="badge {cls}">{_e(status)}</span>'


def _project_section(p: ProjectData, idx: int) -> str:
    parts = [f'<section class="proj" id="proj{idx}">',
             f"<h1>{_e(p.name)}</h1>"]
    if p.description:
        parts.append(f'<p class="muted">{_e(p.description)}</p>')
    if p.error:
        parts.append(f'<p class="err">⚠ {_e(p.error)}</p>')

    last_audit = p.audits[-1].date if p.audits else "—"
    parts.append('<div class="cards">')
    for label, val in (("open items", p.n_open), ("critical (open)", p.n_critical),
                       ("escalation candidates", len(p.escalation_candidates)),
                       ("closed", p.n_closed), ("audits", len(p.audits)),
                       ("last audit", last_audit)):
        parts.append(f"<div class='card'><b>{_e(val)}</b><span>{_e(label)}</span></div>")
    parts.append("</div>")

    if p.escalation_candidates:
        parts.append("<h2>Escalation candidates (threshold reached — human decision)</h2>")
        parts.append('<div class="overflow"><table><tr><th>signature</th><th>count</th>'
                     "<th>status</th><th>title</th></tr>")
        for i in p.escalation_candidates:
            parts.append(f"<tr><td class='sig'>{_e(i.signature)}</td><td>{i.count}×</td>"
                         f"<td>{_status_badge(i.status)}</td><td>{_e(i.title)}</td></tr>")
        parts.append("</table></div>")

    if p.items:
        parts.append("<h2>Ledger</h2>")
        parts.append('<div class="overflow"><table><tr><th>signature</th><th>count</th>'
                     "<th>status</th><th>title</th></tr>")
        for i in p.items:
            parts.append(f"<tr><td class='sig'>{_e(i.signature)}</td><td>{i.count}×</td>"
                         f"<td>{_status_badge(i.status)}</td><td>{_e(i.title)}</td></tr>")
        parts.append("</table></div>")

    if p.cases:
        order = {"open": 0, "regression": 1, "sanity": 2}
        cases = sorted(p.cases, key=lambda c: (order.get(c[1], 3), c[0]))
        counts: dict[str, int] = {}
        for _, st in cases:
            counts[st] = counts.get(st, 0) + 1
        summary = " · ".join(f"{n} {st}" for st, n in
                             sorted(counts.items(), key=lambda kv: order.get(kv[0], 3)))
        parts.append(f"<h2>Golden cases</h2><p class='muted'>{_e(summary)}</p>")
        parts.append('<div class="cases">')
        for cid, st in cases:
            parts.append(f"<span class='case'><span class='sig'>{_e(cid)}</span>"
                         f"{_case_badge(st)}</span>")
        parts.append("</div>")

    if p.audits:
        parts.append("<h2>Audits</h2>")
        if p.trend and p.trend.get("has_baseline"):
            d = p.trend["delta"]
            arrow = "worsening ↑" if p.trend.get("degrading") else "improving ↓"
            parts.append(f"<p class='muted'>trend vs previous window: critical "
                         f"{d['critical']:+d}, major {d['major']:+d}, minor {d['minor']:+d} "
                         f"— {arrow}</p>")
        elif p.trend:
            parts.append(f"<p class='muted'>no trend yet — {len(p.audits)} audit(s), "
                         f"nothing to compare against</p>")
        parts.append('<div class="overflow"><table><tr><th>date</th><th>findings</th>'
                     "<th>crit/maj/min</th><th></th></tr>")
        peak = max((a.n_findings for a in p.audits), default=1) or 1
        for a in p.audits[-15:]:
            sev = a.by_severity
            w = int(120 * a.n_findings / peak)
            parts.append(
                f"<tr><td>{_e(a.date)}</td><td>{a.n_findings}</td>"
                f"<td>{sev.get('critical', 0)}/{sev.get('major', 0)}/{sev.get('minor', 0)}</td>"
                f"<td><span class='bar' style='width:{w}px'></span></td></tr>")
        parts.append("</table></div>")
    parts.append("</section>")
    return "\n".join(parts)


def render_html(projects: list[ProjectData], generated: str = "") -> str:
    tabs = "".join(f"<button onclick='show({i})'>{_e(p.name)}</button>"
                   for i, p in enumerate(projects))
    sections = "\n".join(_project_section(p, i) for i, p in enumerate(projects))
    stamp = f"<footer>finding-ledger report{' · ' + _e(generated) if generated else ''}</footer>"
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>finding-ledger report</title><style>{CSS}</style></head><body>"
            f"<div class='tabs'>{tabs}</div>{sections}{stamp}"
            f"<script>{JS}show(0)</script></body></html>")


def build_report(project_paths: list[Path], out: Path,
                 escalate_at: int = DEFAULT_ESCALATE_AT, generated: str = "") -> list[ProjectData]:
    projects = [load_project(p, escalate_at) for p in project_paths]
    Path(out).write_text(render_html(projects, generated), encoding="utf-8")
    return projects
