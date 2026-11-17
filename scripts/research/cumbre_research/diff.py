"""Refresh deltas (source- and claim-level) and schema migrations."""

from __future__ import annotations

import json
from pathlib import Path

from .util import read_jsonl, write_json

CURRENT_SCHEMA = 3
V3_PROFILE_KEYS = {
    "ownership_type": "UNKNOWN", "ownership_evidence": [], "funding_rounds": [], "investors": [],
    "employee_count_estimates": [], "last_12_months_events": [], "auth_surfaces": [], "auth_method_observations": [],
    "public_api_docs": [], "tech_disclosures": [], "communities": [], "incident_reports": [],
    "candidate_identity_matches": [], "company_run_id": None,
}


def _load(run: Path) -> dict:
    return {"sources": {s["canonical_url"]: s for s in read_jsonl(run / "sources" / "source_registry.jsonl")},
            "claims": {c.get("claim_key") or c["claim_id"]: c for c in read_jsonl(run / "sources" / "claims.jsonl")},
            "signals": {s.get("signal_key") or s["signal_id"]: s for s in read_jsonl(run / "sources" / "signals.jsonl")},
            "hyps": json.loads((run / "data" / "opportunity_hypotheses.json").read_text(encoding="utf-8"))
            if (run / "data" / "opportunity_hypotheses.json").is_file() else []}


def compute_diff(new_run: Path, old_run: Path) -> dict:
    new, old = _load(new_run), _load(old_run)
    fetched = lambda s: (s.get("processing_status") or "").startswith("extracted")  # noqa: E731
    d = {
        "new_sources": [u for u in new["sources"] if u not in old["sources"]],
        "changed_pages": [u for u, s in new["sources"].items() if u in old["sources"] and fetched(s)
                          and fetched(old["sources"][u]) and s.get("content_hash") != old["sources"][u].get("content_hash")],
        "disappeared_pages": [u for u, s in new["sources"].items() if s.get("change_status") == "disappeared_since_prior_run"]
                             + [u for u in old["sources"] if u not in new["sources"]],
        "new_claims": [k for k in new["claims"] if k not in old["claims"]],
        "removed_claims": [k for k in old["claims"] if k not in new["claims"]],
        "newly_contradicted": [k for k, c in new["claims"].items() if c.get("fact_status") == "contradicted"
                               and old["claims"].get(k, {}).get("fact_status") != "contradicted"],
        "new_signals": [k for k in new["signals"] if k not in old["signals"]],
        "new_opportunities": [h["opp_id"] for h in new["hyps"]
                              if h["business_function"] not in {o["business_function"] for o in old["hyps"]}],
    }
    d["_claims"] = new["claims"]
    d["_sources"] = new["sources"]
    return d


def change_log(new_run: Path, old_run: Path) -> str:
    d = compute_diff(new_run, old_run)
    claims, sources = d.pop("_claims"), d.pop("_sources")
    L = [f"# Change log: {old_run.name} → {new_run.name}\n",
         "Past snapshots are kept unchanged; this file records deltas only.\n"]
    for key, label in (("new_sources", "New sources"), ("changed_pages", "Changed pages (content hash differs)"),
                       ("disappeared_pages", "Disappeared pages"), ("new_claims", "New claims"),
                       ("newly_contradicted", "Newly contradicted claims"), ("removed_claims", "Claims not re-observed"),
                       ("new_signals", "New signals"), ("new_opportunities", "New opportunity themes")):
        items = d[key]
        L.append(f"## {label} ({len(items)})\n")
        for it in items[:200]:
            if key in ("new_claims", "newly_contradicted") and it in claims:
                c = claims[it]
                L.append(f"- `{c['claim_id']}` {c['claim_type']}: {str(c['value'])[:100]}")
            elif key in ("new_sources", "changed_pages", "disappeared_pages"):
                sid = sources.get(it, {}).get("source_id", "")
                L.append(f"- {sid} <{it}>")
            else:
                L.append(f"- {it}")
        L.append("")
    return "\n".join(L)


def migrate_run(run_dir: Path) -> dict:
    """Upgrade a run's structured_profile.json to the current schema without
    rewriting past source records: the original is kept, a migrated copy is added."""
    prof_path = run_dir / "data" / "structured_profile.json"
    if not prof_path.is_file():
        return {"migrated": False, "reason": "no structured_profile.json"}
    prof = json.loads(prof_path.read_text(encoding="utf-8"))
    version = int(prof.get("schema_version") or 1)
    if version >= CURRENT_SCHEMA:
        return {"migrated": False, "reason": f"already schema {version}"}
    out = dict(prof)
    added = []
    for k, v in V3_PROFILE_KEYS.items():
        if k not in out:
            out[k] = v
            added.append(k)
    out["schema_version"] = CURRENT_SCHEMA
    out["migrated_from_schema"] = version
    target = run_dir / "data" / f"structured_profile.migrated-v{CURRENT_SCHEMA}.json"
    write_json(target, out)
    return {"migrated": True, "from": version, "to": CURRENT_SCHEMA, "added_keys": added, "written": str(target),
            "original_kept": str(prof_path)}
