#!/usr/bin/env python3
"""Cumbre company intelligence CLI (spec: Master Build Prompt v3).

  research   Research one or more companies (pre-meeting | post-meeting | refresh | targeted)
  resume     Continue a checkpointed run (e.g. after ingest-search)
  pending    Show queries waiting for the agent-search loop
  confirm-identity Record a founder-confirmed entity (official URL / NIT / legal name / candidate)
  ingest-search    Import web-search results gathered by the operating agent
  ingest-findings  Import agent-reviewed claims/signals (validated against the registry)
  render     Rebuild reports from the stored evidence (no new fetching)
  lint       Citation/provenance and output checks
  query      Search stored evidence ("where did we see inventory?")
  providers  Show provider availability and what credentials are needed
  diff       Source/claim deltas between two runs
  migrate    Upgrade an older run's structured profile (original kept)
  demo       Synthetic end-to-end run (no network)

Run from the repo root: python scripts/research/cumbre.py research "Empresa ABC" --country CO --city Cali
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cumbre_research.company import Company  # noqa: E402
from cumbre_research.fetch import Fetcher  # noqa: E402
from cumbre_research.pipeline import Options, ResearchRun, confirm_identity, resume_run  # noqa: E402
from cumbre_research.store import KitError, Store, clients_root  # noqa: E402
from cumbre_research.util import load_config, load_structured_file  # noqa: E402


def _caps(values: list[str] | None) -> dict:
    out = {}
    for v in values or []:
        k, _, n = v.partition("=")
        if not n.isdigit():
            raise SystemExit(f"--provider-cap expects name=N, got {v!r}")
        out[k.strip()] = int(n)
    return out


def _options(a) -> Options:
    return Options(depth=a.depth, mode=a.mode, allow_paid=a.allow_paid, provider_caps=_caps(a.provider_cap),
                   max_spend=a.max_spend, max_search_queries=a.max_search_queries, max_pages=a.max_pages,
                   max_depth=a.max_depth, max_runtime=a.max_runtime, max_llm_tokens=a.max_llm_tokens,
                   max_concurrency=a.max_concurrency, stale_days=a.stale_days, session=a.session,
                   focus=a.focus or [], dry_run=a.dry_run, skip_external=a.skip_external)


def _companies(a) -> list[Company]:
    comps: list[Company] = []
    if a.companies_file:
        data = load_structured_file(Path(a.companies_file))
        items = data.get("companies", data) if isinstance(data, dict) else data
        comps += [Company.from_dict(d if isinstance(d, dict) else {"name": d}) for d in items]
    names = ([a.query] if a.query else []) + (a.company or [])
    for i, n in enumerate(names):
        d = {"name": n, "country": a.country, "city": a.city}
        if len(names) == 1 and not a.companies_file:  # identifying flags apply to a single company only
            d.update(legal_name=a.legal_name, company_id=a.company_id, official_url=a.official_url or [],
                     aliases=a.alias or [], industry_hints=a.industry or [], social_urls=a.social_url or [],
                     product_keywords=a.product_keyword or [], research_focus=a.focus or [])
        comps.append(Company.from_dict(d))
    if not comps:
        raise SystemExit("give a company name, --company (repeatable) or --companies-file")
    return comps


def cmd_research(a) -> int:
    config = load_config(a.config)
    root = clients_root(a.clients_dir)
    if a.depth is None:
        a.depth = config["default_depth"]
    comps = _companies(a)
    if a.mode == "post-meeting":
        if not a.session:
            raise SystemExit("post-meeting mode needs --session <clients/<slug>/sessions/<id>>")
        from cumbre_research.batch import _session_dir, session_allows_research
        sdir = _session_dir(a.session)
        if not session_allows_research(sdir) and not a.allow_session_research:
            raise SystemExit(f"{sdir}/session.yaml has external_research_allowed: false. Re-run with "
                             "--allow-session-research only if a founder approved public research for this client.")
    if a.mode == "targeted" and not a.focus:
        raise SystemExit("targeted mode needs --focus <theme> (repeatable)")

    def make(c: Company) -> ResearchRun:
        return ResearchRun(c, load_config(a.config), _options(a), root)

    if a.dry_run:
        out = []
        for c in comps:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                r = ResearchRun(c, load_config(a.config), _options(a), Path(tmp))
                out.append(r.run())
                r.store.db.close()
        print(json.dumps(out if len(out) > 1 else out[0], ensure_ascii=False, indent=2))
        return 0
    if len(comps) > 1:
        from cumbre_research.batch import run_batch
        manifest = run_batch(comps, make, root, a.depth, a.mode)
        result = {"batch_dir": manifest["batch_dir"], "companies": [
            {k: e.get(k) for k in ("name", "status", "run_dir", "error")} for e in manifest["companies"]]}
    else:
        run = make(comps[0])
        result = run.run()
        if a.mode == "post-meeting":
            from cumbre_research.batch import correlate_session
            from cumbre_research.reports import render_all
            corr = correlate_session(run.run_dir, a.session)
            render_all(run.run_dir, run, correlation=corr)
            result["post_meeting_correlations"] = len(corr)
        if a.handoff_to_session:
            from cumbre_research.batch import export_handoff
            result["handoff"] = export_handoff(run.run_dir, a.handoff_to_session)
    _out(result, a.json)
    return 0


def _out(result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    if isinstance(result, dict):
        for k, v in result.items():
            print(f"{k}: {v}")


def _open_run(run_dir: str, config_path: str | None = None, **overrides) -> ResearchRun:
    rd = Path(run_dir).resolve()
    store = Store(rd / "data" / "company_research.sqlite")
    comp = Company.from_dict(store.kv_get("company"))
    store.db.close()
    manifest = json.loads((rd / "run_manifest.json").read_text(encoding="utf-8"))
    opts = Options(depth=manifest.get("depth", "deep"), mode=manifest.get("mode", "pre-meeting"), **overrides)
    run = ResearchRun(comp, load_config(config_path), opts, rd.parent.parent.parent, run_dir=rd)
    run.stop_reason = manifest.get("stop_reason", "")
    return run


def cmd_resume(a) -> int:
    overrides = {k: getattr(a, k) for k in ("max_search_queries", "max_pages", "max_depth", "max_runtime",
                                            "max_llm_tokens", "max_concurrency") if getattr(a, k, None) is not None}
    if a.allow_paid:
        overrides.update(allow_paid=True, provider_caps=_caps(a.provider_cap), max_spend=a.max_spend)
    _out(resume_run(Path(a.run_dir).resolve(), load_config(a.config), overrides=overrides), a.json)
    return 0


def cmd_confirm_identity(a) -> int:
    try:
        res = confirm_identity(Path(a.run_dir).resolve(), official_url=a.official_url, nit=a.nit,
                               legal_name=a.legal_name, candidate=a.candidate, note=a.note)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _out(res, a.json)
    print("next: python scripts/research/cumbre.py resume", a.run_dir)
    return 0


def cmd_pending(a) -> int:
    data = json.loads((Path(a.run_dir) / "pending_queries.json").read_text(encoding="utf-8"))
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for q in data["queries"]:
            print(f"{q['query_id']}\t{q.get('facet')}\t{q['query']}")
        print(f"\n{len(data['queries'])} pending. {data['instructions']}")
    return 0


def cmd_ingest_search(a) -> int:
    from cumbre_research.agent_io import ingest_search
    run = _open_run(a.run_dir, a.config)
    _out(ingest_search(run, Path(a.file)), a.json)
    print("next: python scripts/research/cumbre.py resume", a.run_dir)
    return 0


def cmd_ingest_findings(a) -> int:
    from cumbre_research.agent_io import ingest_findings
    run = _open_run(a.run_dir, a.config)
    res = ingest_findings(run, Path(a.file), a.model)
    if res["errors"]:
        _out(res, True)
        return 2
    run.finalize()
    _out(res, a.json)
    return 0


def cmd_render(a) -> int:
    run = _open_run(a.run_dir, a.config)
    run.finalize()
    print("rendered", run.run_dir)
    return 0


def cmd_lint(a) -> int:
    from cumbre_research.lint import lint_run
    errors, warns = lint_run(Path(a.run_dir))
    for e in errors:
        print("ERROR", e)
    for w in warns:
        print("WARN", w)
    print(f"{len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


def cmd_query(a) -> int:
    rd = Path(a.run_dir)
    store = Store(rd / "data" / "company_research.sqlite")
    out = {"passages": [], "sources": [], "claims": []}
    if a.text:
        for hit in store.search_passages(a.text, limit=a.limit):
            s = store.source(hit["source_id"]) or {}
            out["passages"].append({**hit, "url": s.get("url"), "published_at": s.get("published_at"),
                                    "retrieved_at_utc": s.get("retrieved_at_utc")})
    for s in store.sources():
        if a.source_type and s.get("source_type") != a.source_type:
            continue
        if a.min_relevance is not None and (s.get("relevance_score") or 0) < a.min_relevance:
            continue
        if a.facet and a.facet not in (s.get("search_facet"), s.get("url_category")):
            continue
        if a.source_type or a.min_relevance is not None or a.facet:
            out["sources"].append({k: s.get(k) for k in ("source_id", "url", "source_type", "relevance_score",
                                                         "reliability_score", "published_at", "processing_status")})
    if a.claim_status or a.claim_type:
        out["claims"] = [c for c in store.claims() if (not a.claim_status or c.get("fact_status") == a.claim_status)
                         and (not a.claim_type or c["claim_type"] == a.claim_type)]
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for p in out["passages"]:
            print(f"[{p['source_id']}] {p['url']} ({p.get('published_at') or 'undated'}) §{p['section']}\n   {p['snippet']}")
        for s in out["sources"]:
            print(f"{s['source_id']} {s['source_type']} rel={s['relevance_score']} {s['url']}")
        for c in out["claims"]:
            print(f"{c['claim_id']} {c['claim_type']} [{c.get('fact_status')}] {c['value']} <- {c['supporting_source_ids']}")
    return 0


def cmd_providers(a) -> int:
    from cumbre_research.providers import Ledger, build_providers, provider_report
    config = load_config(a.config)
    ledger = Ledger(caps={}, unit_costs={}, max_spend=0, allow_paid=a.allow_paid)
    rows = provider_report(build_providers(Fetcher(config), ledger, None, config))
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['provider']:16} {r['status']:36} paid={str(r['paid']):5} {','.join(r['capabilities'])}\n{'':16} {r['detail']}")
    return 0


def cmd_diff(a) -> int:
    from cumbre_research.diff import change_log
    print(change_log(Path(a.new_run), Path(a.old_run)))
    return 0


def cmd_migrate(a) -> int:
    from cumbre_research.diff import migrate_run
    print(json.dumps(migrate_run(Path(a.run_dir)), indent=2))
    return 0


def cmd_demo(a) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
    from synthetic_web import run_demo  # type: ignore
    out = Path(a.out).resolve()
    result = run_demo(out)
    _out(result, a.json)
    return 0


def add_research_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("query", nargs="?", help='company name or keywords, e.g. "Reblujos de Pati"')
    p.add_argument("--company", action="append", help="additional company (repeatable; creates a batch)")
    p.add_argument("--companies-file", help="YAML/JSON with a companies: [...] list")
    p.add_argument("--country", help="ISO country code, e.g. CO")
    p.add_argument("--city")
    p.add_argument("--legal-name")
    p.add_argument("--company-id", "--nit", dest="company_id", help="NIT or other identifier")
    p.add_argument("--official-url", action="append")
    p.add_argument("--alias", action="append")
    p.add_argument("--industry", action="append")
    p.add_argument("--social-url", action="append")
    p.add_argument("--product-keyword", action="append")
    p.add_argument("--focus", action="append", help="research theme (targeted mode)")
    p.add_argument("--depth", choices=["standard", "deep", "exhaustive"], default=None, help="default: deep")
    p.add_argument("--mode", choices=["pre-meeting", "post-meeting", "refresh", "targeted"], default="pre-meeting")
    p.add_argument("--session", help="discovery session for post-meeting correlation (read locally only)")
    p.add_argument("--allow-session-research", action="store_true",
                   help="founder-approved override when session.yaml says external_research_allowed: false")
    p.add_argument("--handoff-to-session", help="register discovery_context_handoff.md as [EXTERNO] input of a session")
    p.add_argument("--stale-days", type=int, help="refresh: only refetch sources older than N days")
    add_budget_flags(p)
    p.add_argument("--dry-run", action="store_true", help="plan + cost estimate only; no fetching")
    p.add_argument("--skip-external", action="store_true", help="skip free external APIs (Wayback, RDAP, GDELT, SECOP, Wikidata)")


def add_budget_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-spend", type=float, help="USD ceiling for paid providers (requires known unit costs)")
    p.add_argument("--max-search-queries", type=int)
    p.add_argument("--max-pages", type=int)
    p.add_argument("--max-depth", type=int, help="max crawl depth from the homepage")
    p.add_argument("--max-runtime", type=int, help="seconds")
    p.add_argument("--max-llm-tokens", type=int, help="recorded; the engine itself calls no LLM")
    p.add_argument("--max-concurrency", type=int)
    p.add_argument("--allow-paid", action="store_true", help="authorize paid providers (founder approval required)")
    p.add_argument("--provider-cap", action="append", help="e.g. tavily.search=150 (paid call cap)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="override config (JSON or YAML) merged over research_defaults.json")
    parser.add_argument("--clients-dir", help="default: $CUMBRE_CLIENTS_DIR or <repo>/clients")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("research", help="research one or more companies")
    add_research_flags(p)
    p.set_defaults(func=cmd_research)
    p = sub.add_parser("resume", help="continue a checkpointed run")
    p.add_argument("run_dir")
    add_budget_flags(p)
    p.set_defaults(func=cmd_resume)
    p = sub.add_parser("confirm-identity", help="record a founder-confirmed identity, then resume")
    p.add_argument("run_dir")
    p.add_argument("--official-url")
    p.add_argument("--nit")
    p.add_argument("--legal-name")
    p.add_argument("--candidate", help="a candidate key from company_identity.md, e.g. profile:brisa-s-a-s")
    p.add_argument("--note", help="how it was confirmed (e.g. 'RUES lookup', 'client said so')")
    p.set_defaults(func=cmd_confirm_identity)
    p = sub.add_parser("pending", help="queries awaiting agent search")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_pending)
    p = sub.add_parser("ingest-search", help="import agent web-search results")
    p.add_argument("run_dir")
    p.add_argument("file")
    p.set_defaults(func=cmd_ingest_search)
    p = sub.add_parser("ingest-findings", help="import agent-reviewed findings")
    p.add_argument("run_dir")
    p.add_argument("file")
    p.add_argument("--model", required=True, help="model/agent label recorded in provenance, e.g. claude-opus-5-5")
    p.set_defaults(func=cmd_ingest_findings)
    p = sub.add_parser("render", help="rebuild reports from stored evidence")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_render)
    p = sub.add_parser("lint", help="citation and output checks")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_lint)
    p = sub.add_parser("query", help="search stored evidence")
    p.add_argument("run_dir")
    p.add_argument("text", nargs="?")
    p.add_argument("--facet")
    p.add_argument("--source-type")
    p.add_argument("--min-relevance", type=int)
    p.add_argument("--claim-status", choices=["confirmed", "inferred", "hypothesis", "unknown", "contradicted"])
    p.add_argument("--claim-type")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_query)
    p = sub.add_parser("providers", help="provider availability")
    p.add_argument("--allow-paid", action="store_true")
    p.set_defaults(func=cmd_providers)
    p = sub.add_parser("diff", help="deltas between two runs")
    p.add_argument("new_run")
    p.add_argument("old_run")
    p.set_defaults(func=cmd_diff)
    p = sub.add_parser("migrate", help="schema migration for an older run")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_migrate)
    p = sub.add_parser("demo", help="synthetic end-to-end run (no network)")
    p.add_argument("--out", required=True, help="output root (e.g. a scratch folder or docs/research/example)")
    p.set_defaults(func=cmd_demo)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [x for x in argv if x != "--json"]
    a = parser.parse_args(argv)
    a.json = as_json
    for flag in ("depth", "mode", "provider_cap", "allow_paid", "max_spend"):
        if not hasattr(a, flag):
            setattr(a, flag, None)
    try:
        return a.func(a)
    except KitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
