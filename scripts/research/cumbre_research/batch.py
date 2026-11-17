"""Multi-company batches and the Discovery Engine bridge."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

from .company import Company
from .store import assert_private
from .util import REPO_ROOT, fold, host_of, iso, read_jsonl, registrable_domain, short_hash, utc_now, write_json, write_jsonl


def run_batch(companies: list[Company], make_run, root: Path, depth: str, mode: str) -> dict:
    """make_run(company) -> ResearchRun. Each company is isolated; one failure never stops the batch."""
    now = utc_now()
    batch_id = "b" + short_hash("|".join(c.slug for c in companies) + now.isoformat(), 6)
    batch_dir = root / "_batches" / f"{now.date().isoformat()}-{batch_id}"
    assert_private(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)
    slugs = [c.slug for c in companies]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    for c in companies:
        if c.slug in dupes:
            c.slug = f"{c.slug}-{short_hash(c.name + str(c.city) + str(c.official_urls), 4)}"
    entries = []
    manifest = {"batch_id": batch_id, "created_at_utc": iso(now), "depth": depth, "mode": mode, "companies": entries}
    for c in companies:
        entry = {"name": c.name, "slug": c.slug, "status": "running", "run_dir": None, "error": None}
        entries.append(entry)
        write_json(batch_dir / "batch_manifest.json", manifest)
        try:
            run = make_run(c)
            entry["run_dir"] = str(run.run_dir)
            summary = run.run()
            entry.update(status="completed", summary=summary)
        except Exception as exc:  # noqa: BLE001 - error isolation per company
            entry.update(status="failed", error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-800:])
        write_json(batch_dir / "batch_manifest.json", manifest)
    edges = cross_company_edges([e for e in entries if e["status"] == "completed"], companies)
    manifest["cross_company_edges"] = edges
    write_json(batch_dir / "batch_manifest.json", manifest)
    (batch_dir / "BATCH_OVERVIEW.md").write_text(batch_overview(manifest), encoding="utf-8")
    manifest["batch_dir"] = str(batch_dir)
    return manifest


def cross_company_edges(entries: list[dict], companies: list[Company]) -> list[dict]:
    """Record verified-looking links between batch companies; never merge them."""
    by_slug = {c.slug: c for c in companies}
    edges = []
    for e in entries:
        run_dir = Path(e["run_dir"])
        sources = read_jsonl(run_dir / "sources" / "source_registry.jsonl")
        me = by_slug[e["slug"]]
        for other in companies:
            if other.slug == me.slug or not other.official_domains:
                continue
            hits = [s for s in sources if registrable_domain(host_of(s["url"])) in other.official_domains
                    and any(v.startswith("outlink:") for v in s.get("discovered_via", []))]
            if hits:
                edges.append({"from": me.slug, "to": other.slug, "edge_type": "links_to_official_site",
                              "source_ids": [h["source_id"] for h in hits[:5]], "run": run_dir.name,
                              "confidence": "low", "note": "Outbound link from official site; relationship type unverified. "
                                                           "Companies remain separate entities."})
    return edges


def batch_overview(m: dict) -> str:
    L = [f"# Batch overview `{m['batch_id']}`\n",
         f"Created {m['created_at_utc']} · depth {m['depth']} · mode {m['mode']}. Each company has separate dossiers, "
         "sources and claims. No comparative ranking is made: public visibility differs between companies.\n",
         "| Company | Status | Identity | Sources processed / discovered | Claims | Signals | Stop reason | Folder |",
         "|---|---|---|---|---|---|---|---|"]
    for e in m["companies"]:
        s = e.get("summary") or {}
        folder = f"[{Path(e['run_dir']).name}]({Path(e['run_dir']).as_uri()})" if e.get("run_dir") else "—"
        L.append(f"| {e['name']} | {e['status']}{(': ' + e['error'][:80]) if e.get('error') else ''} | {s.get('identity', '—')} | "
                 f"{s.get('sources_processed', '—')} / {s.get('sources_discovered', '—')} | {s.get('claims', '—')} | "
                 f"{s.get('signals', '—')} | {str(s.get('stop_reason', '—'))[:80]} | {folder} |")
    L.append("\n## Key discovery questions per company\n")
    for e in m["companies"]:
        if e["status"] != "completed":
            continue
        q = Path(e["run_dir"]) / "discovery_questions.md"
        lines = [l for l in q.read_text(encoding="utf-8").splitlines() if re.match(r"^\d+\. ", l)][:3] if q.is_file() else []
        L.append(f"### {e['name']}\n")
        L += lines or ["- (none)"]
        L.append("")
    if m.get("cross_company_edges"):
        L.append("## Cross-company relationships (evidence only, entities not merged)\n")
        L += [f"- {x['from']} → {x['to']}: {x['edge_type']} ({x['confidence']}) sources {', '.join(x['source_ids'])} in run {x['run']}"
              for x in m["cross_company_edges"]]
    L.append("\n## Limitations\n- See each company's coverage_report.md; failed companies did not affect the others.")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Discovery Engine bridge
# ---------------------------------------------------------------------------

def _session_dir(session: str | Path) -> Path:
    p = Path(session).expanduser()
    if p.name == "session.yaml":
        p = p.parent
    if not (p / "session.yaml").is_file():
        raise SystemExit(f"not a discovery session (no session.yaml): {p}")
    return p.resolve()


def session_allows_research(session: Path) -> bool:
    text = (session / "session.yaml").read_text(encoding="utf-8")
    m = re.search(r"^external_research_allowed:\s*(\w+)", text, re.M)
    return bool(m and m.group(1).lower() == "true")


def correlate_session(run_dir: Path, session: str | Path) -> list[dict]:
    """Local-only comparison of PUBLIC_WEB claims with CLIENT_STATEMENT lines
    ([CONFIRMADO]) in the session outputs. Nothing leaves this machine."""
    sdir = _session_dir(session)
    statements = []
    for md in sorted((sdir / "outputs").rglob("*.md")):
        for line in md.read_text(encoding="utf-8").splitlines():
            if "[CONFIRMADO]" in line:
                statements.append((md.name, line.strip()[:300]))
    claims = read_jsonl(run_dir / "sources" / "claims.jsonl")
    topics = {"founding_year": ["fund", "desde", "años", "anos"], "site_count_statement": ["sede", "tienda", "sucursal"],
              "employee_count_statement": ["emplead", "colaborador", "personas"],
              "offering_or_topic": [], "location_mention": [], "tech_disclosure": []}
    rows = []
    for c in claims:
        if c["claim_type"] not in topics:
            continue
        words = topics[c["claim_type"]] or [w for w in re.findall(r"[a-z]{5,}", fold(str(c["value"])))][:3]
        for fname, line in statements:
            fl = fold(line)
            if words and any(w in fl for w in words):
                val = fold(str(c["value"]))
                nums_pub = set(re.findall(r"\d{2,4}", val))
                nums_cli = set(re.findall(r"\d{2,4}", fl))
                if nums_pub and nums_cli:
                    assessment = "POSSIBLE ALIGNMENT" if nums_pub & nums_cli else "POSSIBLE CONFLICT"
                else:
                    assessment = "RELATED — compare manually"
                rows.append({"public": f"`{c['claim_id']}` {str(c['value'])[:80]} (PUBLIC_WEB)",
                             "client": f"{fname}: {line[:120]} (CLIENT_STATEMENT)", "assessment": assessment,
                             "resolve": "Ask the client for a document (registry certificate, report) that settles it"
                             if assessment == "POSSIBLE CONFLICT" else "Confirm during review"})
                break
    return rows[:80]


def export_handoff(run_dir: Path, session: str | Path) -> str:
    """Register the handoff file as [EXTERNO] input via the discovery script's `add` command."""
    sdir = _session_dir(session)
    handoff = run_dir / "discovery_context_handoff.md"
    tmp = run_dir / "exports" / f"public_research_{run_dir.name}.md"
    tmp.write_text(handoff.read_text(encoding="utf-8"), encoding="utf-8")
    script = REPO_ROOT / "scripts" / "discovery" / "cumbre_session.py"
    res = subprocess.run([sys.executable, str(script), "add", str(sdir), "--external", str(tmp)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"discovery `add` failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()
