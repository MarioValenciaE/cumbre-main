"""Agent loop I/O: import agent web-search results and agent-reviewed findings.

Both imports are validated. Imported search results are treated exactly like
provider results (untrusted, scored, fetched under the same safety rules).
Agent findings must cite existing source IDs or they are rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

from .providers import SearchHit
from .safety import find_secrets, safe_excerpt
from .store import Store
from .util import fold, iso, load_structured_file, utc_now

ALLOWED_FINDING_TYPES = {"claim", "signal", "incident", "edge"}
CLAIM_STATUSES = {"confirmed", "inferred", "hypothesis", "unknown", "contradicted"}


def ingest_search(run, path: Path) -> dict:
    """run: ResearchRun (resumed). File: [{query_id?, query, results:[{url,title,snippet,published?}]}]."""
    data = load_structured_file(path)
    if isinstance(data, dict):
        data = data.get("queries") or data.get("results") or [data]
    stats = {"queries": 0, "hits": 0, "new_sources": 0, "rejected": 0}
    by_text = {q["query"].lower().strip(): q for q in run.store.queries()}
    wave = int(run.store.kv_get("wave") or 1) + 1
    for entry in data:
        qtext = (entry.get("query") or "").strip()
        if not qtext:
            stats["rejected"] += 1
            continue
        q = None
        if entry.get("query_id"):
            q = next((x for x in run.store.queries() if x["query_id"] == entry["query_id"]), None)
        q = q or by_text.get(qtext.lower())
        if q is None:
            qid, _ = run.store.add_query({"query": qtext, "facet": entry.get("facet") or "agent_added", "wave": wave,
                                          "origin": "agent", "provider_hint": "search", "status": "planned"})
            q = next(x for x in run.store.queries() if x["query_id"] == qid)
        hits = []
        for i, r in enumerate(entry.get("results") or [], 1):
            url = (r.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                stats["rejected"] += 1
                continue
            hits.append(SearchHit(url=url, title=safe_excerpt(r.get("title") or "", 200),
                                  snippet=safe_excerpt(r.get("snippet") or "", 400), published=r.get("published"),
                                  provider="agent_websearch", query=qtext, rank=i))
        stats["new_sources"] += run.ingest_hits(hits, wave, q)
        stats["hits"] += len(hits)
        stats["queries"] += 1
        run.store.update_query(q["query_id"], status="executed", providers=["agent_websearch"], hit_count=len(hits),
                               executed_at_utc=iso(utc_now()), import_file=path.name,
                               executed_as=entry.get("executed_as") or qtext)
    imports = run.store.kv_get("agent_imports") or []
    imports.append({"kind": "search", "file": path.name, "at": iso(utc_now()), **stats})
    run.store.kv_set("agent_imports", imports)
    run.store.commit()
    return stats


def validate_findings(items: list[dict], store: Store) -> tuple[list[dict], list[str]]:
    ok, errors = [], []
    for i, it in enumerate(items):
        t = it.get("type")
        if t not in ALLOWED_FINDING_TYPES:
            errors.append(f"item {i}: type must be one of {sorted(ALLOWED_FINDING_TYPES)}")
            continue
        sids = it.get("source_ids") or it.get("supporting_source_ids") or []
        if not sids:
            errors.append(f"item {i}: no source_ids (every finding must cite the registry)")
            continue
        missing = [s for s in sids if store.source(s) is None]
        if missing:
            errors.append(f"item {i}: unknown source ids {missing}")
            continue
        blob = json.dumps(it, ensure_ascii=False)
        if find_secrets(blob):
            errors.append(f"item {i}: contains secret-like content; rejected")
            continue
        if t == "claim" and it.get("fact_status", "inferred") not in CLAIM_STATUSES:
            errors.append(f"item {i}: invalid fact_status")
            continue
        ok.append(it)
    return ok, errors


def ingest_findings(run, path: Path, model_label: str) -> dict:
    data = load_structured_file(path) if path.suffix != ".jsonl" else \
        [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    items = data if isinstance(data, list) else data.get("findings", [])
    ok, errors = validate_findings(items, run.store)
    if errors:
        return {"accepted": 0, "rejected": len(items) - len(ok), "errors": errors}
    slug = run.company.slug
    for it in ok:
        sids = it.get("source_ids") or it.get("supporting_source_ids")
        if it["type"] == "claim":
            key = f"{slug}|agent:{it.get('claim_type', 'agent_claim')}|{fold(str(it.get('value', it.get('statement', ''))))[:120]}"
            run.store.upsert_claim(key, {
                "claim_key": key, "claim_type": it.get("claim_type", "agent_claim"), "subject": slug,
                "value": safe_excerpt(str(it.get("value", "")), 300), "statement": safe_excerpt(it.get("statement", ""), 400),
                "fact_status": it.get("fact_status", "inferred"), "supporting_source_ids": sids,
                "contradicting_source_ids": it.get("contradicting_source_ids", []), "locations": it.get("locations", sids),
                "event_date": it.get("event_date"), "extracted_at_utc": iso(utc_now()),
                "extraction_method": f"agent_review:{model_label}"})
        elif it["type"] == "signal":
            key = f"{slug}|agent:{it.get('signal_type', 'agent_signal')}|{sids[0]}|{fold(it.get('observed_statement', ''))[:60]}"
            run.store.upsert_signal(key, {
                "signal_key": key, "signal_type": it.get("signal_type", "agent_signal"),
                "business_function": it.get("business_function", "other"), "strength": int(it.get("strength", 1)),
                "source_ids": sids, "location": it.get("location", sids[0]), "section": it.get("section", ""),
                "observed_statement": safe_excerpt(it.get("observed_statement", ""), 300),
                "why_it_matters": it.get("why_it_matters", ""), "alternative_interpretations": it.get("alternative_interpretations", ""),
                "meeting_question": it.get("meeting_question", ""), "evidence_basis": "public_web",
                "caveat": "Agent-reviewed signal; not an established client problem.",
                "extraction_method": f"agent_review:{model_label}"})
        elif it["type"] == "incident":
            key = f"{slug}|agent:{it.get('finding_type', 'incident')}|{sids[0]}"
            run.store.upsert_incident(key, {
                "finding_type": it.get("finding_type", "security_incident"), "company_identity_match": it.get("company_identity_match", "unknown"),
                "discovery_method": "agent_review_of_public_source", "published_event_date": it.get("published_event_date"),
                "event_date": it.get("event_date"), "retrieved_at_utc": iso(utc_now()), "source_ids": sids,
                "evidence_strength": it.get("evidence_strength"), "status": it.get("status", "UNVERIFIED_REPORT"),
                "severity_basis": it.get("severity_basis", "not_stated_by_source"), "legal_context": it.get("legal_context"),
                "redacted_summary": safe_excerpt(it.get("redacted_summary", ""), 300), "owner_validation_needed": True})
        elif it["type"] == "edge":
            key = f"{slug}|{it.get('edge_type')}|{it.get('to')}"
            run.store.upsert_edge(key, {"from": slug, "to": it.get("to"), "edge_type": it.get("edge_type"),
                                        "direction": it.get("direction", "from->to"), "validity_period": it.get("validity_period"),
                                        "source_ids": sids, "confidence": it.get("confidence", "low"),
                                        "basis": f"agent_review:{model_label}"})
    imports = run.store.kv_get("agent_imports") or []
    imports.append({"kind": "findings", "file": path.name, "model": model_label, "at": iso(utc_now()), "accepted": len(ok)})
    run.store.kv_set("agent_imports", imports)
    run.store.commit()
    return {"accepted": len(ok), "rejected": 0, "errors": []}
