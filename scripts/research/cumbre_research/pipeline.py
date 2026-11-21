"""Research orchestration: phases A-H, waves, budgets, checkpoints, refresh."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .analyze import Findings, analyze_source, detect_contradictions
from .company import Company, strip_legal
from .extract import (HIGH_VALUE_CATEGORIES, PageData, classify_url, extract_dates, parse_html, parse_sitemap,
                      pdf_text, social_platform, url_pattern)
from .fetch import Fetcher, Response
from .hypotheses import build_hypotheses
from .planner import CONFIRMED_IDENTITY, candidate_entities, decide_identity, gap_queries, initial_queries
from .providers import BudgetExceeded, Ledger, RuesProvider, SearchHit, build_providers, provider_report
from .safety import redact, safe_excerpt
from .scoring import (classify_source_type, confidence_band, freshness, novelty, opportunity, priority,
                      relevance, reliability, shingles)
from .store import Store, export_jsonl, load_prior_registry, new_run_dir, prior_runs, write_manifest
from .util import canonical_url, fold, host_of, iso, registrable_domain, utc_now, validate_nit, write_json

SEARCH_PROVIDER_ORDER = ["tavily", "brave"]
ASSET_RE = re.compile(r"\.(css|js|mjs|map|png|jpe?g|gif|svg|ico|webp|bmp|woff2?|ttf|eot|otf|mp4|mp3|webm|zip|rar|exe)$", re.I)
HOST_FAILURE_LIMIT = 6
PLATFORM_RESTRICTED = {"facebook", "instagram", "linkedin", "x", "tiktok", "threads", "whatsapp", "telegram",
                       "discord", "slack", "google_business_profile"}


@dataclass
class Options:
    depth: str = "deep"
    mode: str = "pre-meeting"
    allow_paid: bool = False
    provider_caps: dict = field(default_factory=dict)
    max_spend: float | None = None
    max_search_queries: int | None = None
    max_pages: int | None = None
    max_depth: int | None = None
    max_runtime: int | None = None
    max_llm_tokens: int | None = None
    max_concurrency: int | None = None
    stale_days: int | None = None
    session: str | None = None
    focus: list = field(default_factory=list)
    dry_run: bool = False
    skip_external: bool = False


@dataclass
class PageResult:
    item: dict
    response: Response | None = None
    page: PageData | None = None
    blocks: list = field(default_factory=list)
    text: str = ""
    status: str = ""
    reason: str = ""
    content_type: str = ""


class ResearchRun:
    def __init__(self, company: Company, config: dict, options: Options, root: Path,
                 fetcher: Fetcher | None = None, run_dir: Path | None = None, clock=utc_now):
        self.company = company
        self.config = config
        self.opt = options
        self.root = root
        self.clock = clock
        self.started = clock()
        self.t0 = time.monotonic()
        self.depth_cfg = dict(config["depths"][options.depth])
        for key, attr in (("max_search_queries", "max_search_queries"), ("max_pages", "max_pages"),
                          ("max_crawl_depth", "max_depth"), ("max_runtime_s", "max_runtime")):
            if getattr(options, attr) is not None:
                self.depth_cfg[key] = getattr(options, attr)
        if options.max_concurrency:
            config["fetch"]["max_concurrency"] = options.max_concurrency
        self.fetcher = fetcher or Fetcher(config)
        if run_dir is None:
            self.run_dir, self.run_id = new_run_dir(root, company.slug, self.started)
        else:
            self.run_dir, self.run_id = run_dir, run_dir.name.split("-")[-1]
        self.store = Store(self.run_dir / "data" / "company_research.sqlite")
        caps = {k: v for k, v in config["paid_provider_call_caps"].items() if not k.startswith("_")}
        caps.update(options.provider_caps or {})
        self.ledger = Ledger(caps=caps, unit_costs={k: v for k, v in config["provider_unit_costs_usd"].items()
                                                    if not k.startswith("_")},
                             max_spend=float(options.max_spend if options.max_spend is not None
                                             else config["budgets"]["max_spend_usd"]),
                             allow_paid=options.allow_paid)
        saved = self.store.kv_get("ledger_calls")
        if saved:
            self.ledger.calls.update(saved.get("calls", {}))
            self.ledger.spend_known = saved.get("spend_known", 0.0)
        self.providers = build_providers(self.fetcher, self.ledger, self.store, config)
        self.errors: list[dict] = self.store.kv_get("errors") or []
        self.corpus: set[int] = set()
        self.leads: list = []
        self.pattern_counts: dict[str, int] = self.store.kv_get("pattern_counts") or {}
        self.host_failures: dict[str, int] = {}
        self.hash_index: dict[str, str] | None = None
        self.run_date = self.started.date()
        self.prior: dict | None = None
        self.identity: dict = self.store.kv_get("identity") or {}
        self.candidates: list = self.store.kv_get("candidates") or []
        self.registry_extra: dict = self.store.kv_get("registry_extra") or {}
        self.stop_reason = ""
        self.limitations: list[str] = []
        self._gdelt_paused = False

    # ------------------------------------------------------------------ utils
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def _err(self, where: str, exc: BaseException | str) -> None:
        msg = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        self.errors.append({"at": iso(self.clock()), "where": where, "error": redact(str(msg))[:400]})

    def pages_fetched(self) -> int:
        c = self.store.queue_counts()
        return sum(v for k, v in c.items() if k not in ("queued",) and not k.startswith("not_fetched"))

    def search_providers(self) -> list:
        return [self.providers[n] for n in SEARCH_PROVIDER_ORDER if self.providers[n].available()]

    # ------------------------------------------------------------------ sources
    def register(self, url: str, origin: str, title: str = "", snippet: str = "", published: str | None = None,
                 extra: dict | None = None) -> str | None:
        if not url.startswith(("http://", "https://")):
            return None
        canon = canonical_url(url)
        existing = self.store.source_by_url(url)
        stype = classify_source_type(url, self.company, self.config)
        if existing:
            discovered_via = list(dict.fromkeys((existing.get("discovered_via") or []) + [origin]))[:12]
            self.store.update_source(existing["source_id"], discovered_via=discovered_via)
            return existing["source_id"]
        preset = (self.prior or {}).get("src_ids", {}).get(canon)
        data = {
            "url": url, "title": safe_excerpt(title, 200), "publisher": registrable_domain(host_of(url)),
            "source_type": stype, "url_category": classify_url(url) if self.company.is_official_url(url) else "external",
            "discovered_via": [origin], "discovered_at_utc": iso(self.clock()), "retrieved_at_utc": None,
            "published_at": published, "published_at_basis": "search_provider_metadata" if published else "not_available",
            "modified_at": None, "modified_at_basis": "unknown", "event_date": None, "archive_capture_at": None,
            "identity_match_status": "confirmed" if self.company.is_official_url(url) else "unverified",
            "relevance_score": None, "reliability_score": reliability(stype)[0], "freshness_score": None,
            "novelty_score": None, "opportunity_signal_score": None, "score_rationale": "",
            "score_components": {}, "processing_status": "discovered", "exclusion_reason": None,
            "content_hash": None, "snippet": safe_excerpt(snippet, 300), "linked_claim_ids": [],
            "linked_signal_ids": [], "scoring_version": self.config["scoring_version"], **(extra or {}),
        }
        if snippet or title:
            r, why = relevance(self.company, url, title, snippet, "unverified")
            data["relevance_score"], data["score_rationale"] = r, "snippet-only: " + why
        sid, _ = self.store.upsert_source(url, data, preset_id=preset)
        return sid

    def enqueue(self, url: str, depth: int, wave: int, origin: str, external: bool, prio: float | None = None) -> None:
        src = self.store.source_by_url(url)
        if prio is None:
            cat = classify_url(url) if not external else "external"
            base = 60 if cat in HIGH_VALUE_CATEGORIES else (80 if cat == "home" else 40)
            rel = (src or {}).get("relevance_score") or 30
            prio = base * 0.5 + rel * 0.5 - depth * 5
        self.store.enqueue(url, prio, depth, wave, {"origin": origin, "external": external,
                                                    "source_id": (src or {}).get("source_id")})

    # ------------------------------------------------------------------ refresh
    def load_prior(self) -> None:
        runs = prior_runs(self.root, self.company.slug, exclude=self.run_dir)
        if not runs:
            self.limitations.append("refresh requested but no prior run exists; ran as a fresh baseline")
            return
        prev = runs[-1]
        reg = load_prior_registry(prev)
        src_ids = {canonical_url(s["url"]): s["source_id"] for s in reg["sources"]}
        claim_ids = {c.get("claim_key"): c["claim_id"] for c in reg["claims"] if c.get("claim_key")}
        signal_ids = {s.get("signal_key"): s["signal_id"] for s in reg["signals"] if s.get("signal_key")}
        self.prior = {"dir": str(prev), "src_ids": src_ids, "claim_ids": claim_ids, "signal_ids": signal_ids,
                      "registry": reg, "opp_by_function": {}}
        hyp_file = prev / "data" / "opportunity_hypotheses.json"
        if hyp_file.is_file():
            for h in json.loads(hyp_file.read_text(encoding="utf-8")):
                self.prior["opp_by_function"][h["business_function"]] = h["opp_id"]
        for prefix, ids in (("SRC", src_ids.values()), ("CLM", claim_ids.values()), ("SIG", signal_ids.values())):
            self.store.reserve_ids(prefix, [int(i.split("-")[1]) for i in ids if i and "-" in i])
        prior_company = reg["manifest"].get("company") or {}
        if not self.company.official_urls and prior_company.get("official_urls"):
            self.company.official_urls = prior_company["official_urls"]
        self.company.aliases = list(dict.fromkeys(self.company.aliases + (prior_company.get("aliases") or [])))
        stale_cut = None
        if self.opt.stale_days is not None:
            stale_cut = self.run_date - dt.timedelta(days=self.opt.stale_days)
        for s in reg["sources"]:
            retrieved = (s.get("retrieved_at_utc") or "")[:10]
            fresh_enough = stale_cut and retrieved and dt.date.fromisoformat(retrieved) >= stale_cut
            if fresh_enough:
                carried = {**s, "processing_status": "carried_forward_not_stale", "carried_from_run": prev.name}
                self.store.upsert_source(s["url"], carried, preset_id=s["source_id"])
            else:
                self.register(s["url"], "refresh:prior_run", s.get("title", ""), s.get("snippet", ""))
                if s.get("processing_status", "").startswith("extracted"):
                    self.enqueue(s["url"], 0 if self.company.is_official_url(s["url"]) else 1, 1, "refresh",
                                 external=not self.company.is_official_url(s["url"]), prio=70)

    # ------------------------------------------------------------------ search
    def execute_queries(self, wave: int) -> dict:
        stats = {"executed": 0, "pending_no_provider": 0, "hits": 0, "new_sources": 0, "failed": 0}
        providers = self.search_providers()
        executed_count = len([q for q in self.store.queries() if q.get("status") == "executed"])
        for q in self.store.queries("planned"):
            if executed_count >= self.depth_cfg["max_search_queries"]:
                self.store.update_query(q["query_id"], status="skipped_budget")
                continue
            hits: list[SearchHit] = []
            used = []
            news_prov = self.providers["gdelt"]
            try:
                if q.get("news") and not self.opt.skip_external and not self._gdelt_paused:
                    hits += news_prov.search(q["query"].replace('"', ""), max_results=25)
                    used.append("gdelt")
            except (BudgetExceeded, RuntimeError, ValueError) as exc:
                self._err(f"gdelt:{q['query_id']}", exc)
                if "HTTP 429" in str(exc):
                    # GDELT throttles beyond its documented interval: stop calling it for this invocation
                    # instead of collecting more 429s; news queries still go to the other search paths.
                    self._gdelt_paused = True
                    note = ("GDELT rate-limited this run (HTTP 429 after backoff); remaining news queries skipped "
                            "GDELT. Re-run `resume` later to retry it.")
                    if note not in self.limitations:
                        self.limitations.append(note)
            for p in providers:
                try:
                    hits += p.search(q["query"], max_results=self.depth_cfg["results_per_query"],
                                     country=self.company.country, language=q.get("language"), news=False)
                    used.append(p.name)
                except BudgetExceeded as exc:
                    self.ledger.skipped.append({"query_id": q["query_id"], "provider": p.name, "reason": str(exc)})
                except (RuntimeError, ValueError) as exc:
                    stats["failed"] += 1
                    self._err(f"{p.name}:{q['query_id']}", exc)
            if not providers:
                # Not silently dropped: kept for the agent-search loop (ingest-search).
                self.store.update_query(q["query_id"], status="NOT_EXECUTED_NO_PROVIDER",
                                        partial_providers=used, hit_count=len(hits))
                stats["pending_no_provider"] += 1
            else:
                self.store.update_query(q["query_id"], status="executed", providers=used, hit_count=len(hits),
                                        executed_at_utc=iso(self.clock()))
                stats["executed"] += 1
                executed_count += 1
            stats["hits"] += len(hits)
            stats["new_sources"] += self.ingest_hits(hits, wave, q)
        return stats

    def ingest_hits(self, hits: list[SearchHit], wave: int, q: dict) -> int:
        new = 0
        for h in hits:
            existed = self.store.source_by_url(h.url) is not None
            sid = self.register(h.url, f"search:{h.provider}:{q['query_id']}", h.title, h.snippet, h.published,
                                extra={"search_facet": q.get("facet")})
            if not sid:
                continue
            new += 0 if existed else 1
            ext = not self.company.is_official_url(h.url)
            plat = social_platform(h.url)
            if plat in PLATFORM_RESTRICTED:
                self.store.update_source(sid, processing_status="not_fetched_platform_restricted",
                                         exclusion_reason="NOT_CHECKED_PROVIDER_RESTRICTED: platform requires login/blocks "
                                                          "automated access; metadata from search only")
                continue
            self.enqueue(h.url, 1 if ext else 0, wave, "search", external=ext)
        return new

    # ------------------------------------------------------------------ identity (Phase A)
    def phase_identity(self) -> None:
        if self.store.kv_get("phase_done:identity"):
            return
        supplied = self.store.kv_get("supplied_official_urls")
        if supplied is None:
            supplied = list(self.company.official_urls)
            self.store.kv_set("supplied_official_urls", supplied)
        unreachable = set(self.store.kv_get("unreachable_domains") or [])
        wikidata = []
        if not self.opt.skip_external:
            try:
                wikidata = self.providers["wikidata"].find(self.company.legal_name or self.company.name,
                                                           language=self.company.languages[0],
                                                           country=self.company.country)
            except (BudgetExceeded, RuntimeError, ValueError, KeyError) as exc:
                self._err("wikidata", exc)
        for w in wikidata:
            self.register(w["url"], "identity:wikidata", w.get("label") or "", w.get("description") or "")
        seed_hits = []
        for s in self.store.sources():
            if any(v.startswith("search:") for v in s.get("discovered_via", [])):
                seed_hits.append(SearchHit(url=s["url"], title=s.get("title", ""), snippet=s.get("snippet", ""),
                                           query=s.get("search_facet") or "", rank=0))
        self.candidates = candidate_entities(self.company, seed_hits, wikidata, supplied_urls=supplied)
        rues_cands = self._rues_candidates()
        self._rues_match = None
        has_site = any("." in c["candidate"] and not c["candidate"].startswith(("wikidata:", "profile:", "nit:"))
                       and c["identity_match_confidence"] >= 0.5 for c in self.candidates)
        if rues_cands and has_site:
            # The registry corroborates the entity but has no website: keep the site candidate as the identity
            # and attach the record only when it is unambiguous (one exact match, or the supplied NIT).
            strong = [c for c in rues_cands if "nit" in c["hint_matches"]] or                      ([rues_cands[0]] if len(rues_cands) == 1 else
                      [c for c in rues_cands if self.company.city in c["hint_matches"]][:1]
                      if sum(1 for c in rues_cands if self.company.city in c["hint_matches"]) == 1 else [])
            self._rues_match = strong[0] if strong else None
        elif rues_cands:
            self.candidates = sorted(self.candidates + rues_cands, key=lambda c: -c["identity_match_confidence"])
        for c in self.candidates:
            if c["candidate"] in unreachable:
                c["basis"].append("official root unreachable during this run (possibly a retired domain)")
        self.identity = decide_identity(self.candidates)
        override = self.store.kv_get("identity_override")
        if override:
            # Founder-confirmed identity (confirm-identity) outranks any automatic decision.
            chosen_o = (registrable_domain(host_of(override["official_url"])) if override.get("official_url")
                        else override.get("candidate")
                        or (f"nit:{str(override['nit']).split('-')[0].replace('.', '')}" if override.get("nit") else None)
                        or self.identity.get("chosen"))
            self.identity = {"status": "confirmed_by_founder", "chosen": chosen_o,
                             "alternatives": [c["candidate"] for c in self.candidates if c["candidate"] != chosen_o][:6],
                             "rationale": "Identity confirmed by a Cumbre founder"
                                          + (f": {override['note']}" if override.get("note") else "."),
                             "confirmed_fields": {k: v for k, v in override.items() if k not in ("note", "at") and v},
                             "confirmed_at": override.get("at")}
        self.identity["supplied_official_urls"] = supplied
        chosen = self.identity.get("chosen")
        if chosen and chosen in unreachable:
            # follow the evidence to a live domain for the same entity (old/new domain change)
            alt = next((c for c in self.candidates if c["candidate"] not in unreachable
                        and "." in c["candidate"] and not c["candidate"].startswith("wikidata:")
                        and c["identity_match_confidence"] >= 0.6 and any(b.startswith("search hit") for b in c["basis"])), None)
            if alt:
                self.identity["historical_domains"] = sorted(unreachable)
                self.identity["assumption"] = (f"{chosen} (from knowledge-base/supplied evidence) did not respond; "
                                               f"{alt['candidate']} appears in search results for the same name and is "
                                               "treated provisionally as the current site. Confirm with the company.")
                chosen = alt["candidate"]
                self.identity["chosen"] = chosen
                self.store.upsert_edge(f"{self.company.slug}|possible_domain_change|{chosen}", {
                    "from": sorted(unreachable)[0], "to": chosen, "edge_type": "possible_domain_change",
                    "direction": "old->new", "validity_period": "unknown", "confidence": "low",
                    "source_ids": [s for s in [self.store.source_by_url(u)["source_id"] for u in alt["evidence_urls"][:2]
                                               if self.store.source_by_url(u)]],
                    "basis": "old domain unreachable; same-name domain found in search results"})
        if (not supplied and chosen and "." in chosen and not chosen.startswith("wikidata:")
                and self.identity["status"] in ("provisional", "dominant", "ambiguous")):
            self.company.official_urls = [f"https://{chosen}"]
            self.identity.setdefault("assumption", f"No official URL supplied; provisionally treating {chosen} as the "
                                                   "official domain.")
            for s in self.store.sources():
                if self.company.is_official_url(s["url"]):
                    self.store.update_source(s["source_id"], source_type=classify_source_type(s["url"], self.company, self.config),
                                             identity_match_status="provisional", url_category=classify_url(s["url"]))
        if self.identity["status"] == "ambiguous":
            note = ("Identity ambiguous: alternatives researched for identity only; clarification recommended "
                    "before relying on merged evidence.")
            if note not in self.limitations:
                self.limitations.append(note)
            for alt in self.identity.get("alternatives", [])[:3]:
                if alt != chosen and "." in alt and not alt.startswith("wikidata:"):
                    sid = self.register(f"https://{alt}/", "identity:alternative_candidate")
                    if sid:
                        self.store.update_source(sid, identity_match_status="candidate_alternative")
                        self.enqueue(f"https://{alt}/", 0, 1, "identity_alternative", external=True, prio=90)
        self._apply_rues_identity()
        self._register_supplied_social()
        self.store.kv_set("identity", self.identity)
        self.store.kv_set("candidates", self.candidates)
        self.store.kv_set("company", self.company.to_dict())
        self.store.kv_set("phase_done:identity", True)
        self.store.commit()

    # ------------------------------------------------------------------ RUES registry (Colombia)
    def _rues_candidates(self) -> list[dict]:
        """Registry candidates from the RUES open dataset: by NIT when known, else by exact legal name."""
        if self.company.country != "CO" or self.opt.skip_external or "rues" not in self.providers:
            return []
        prov = self.providers["rues"]
        nit = self.company.company_id
        try:
            rows = prov.lookup(nit=nit) if nit else prov.lookup(name=strip_legal(self.company.legal_name or self.company.name))
        except (BudgetExceeded, RuntimeError, ValueError) as exc:
            self._err("rues", exc)
            return []
        core = strip_legal(self.company.legal_name or self.company.name)
        city = fold(self.company.city or "")
        cands = []
        for r in rows:
            if not r.get("nit"):
                continue
            rs, sigla = r.get("razon_social") or "", r.get("sigla") or ""
            exact = strip_legal(rs) == core or bool(sigla and strip_legal(sigla) == core)
            if not nit and not exact:
                continue  # BRISAS / BRISOL are other entities: never candidates for BRISA
            conf = 0.97 if nit else 0.6
            hints = ["nit"] if nit else []
            if not nit and city and fold(r.get("camara_comercio") or "") == city:
                conf += 0.25
                hints.append(self.company.city)
            if not nit and (r.get("estado_matricula") or "").upper() == "ACTIVA":
                conf += 0.05
            url = RuesProvider.record_url(r["nit"])
            sid = self.register(url, "identity:rues", f"RUES: {rs} (NIT {r['nit']}-{r.get('digito_verificacion', '')})",
                                f"Camara {r.get('camara_comercio')}; matricula {r.get('matricula')}; "
                                f"{r.get('estado_matricula')}; CIIU {r.get('cod_ciiu_act_econ_pri')}")
            if sid:
                self.store.update_source(sid, source_type="official_registry",
                                         reliability_score=reliability("official_registry")[0],
                                         processing_status="extracted_structured_registry",
                                         identity_match_status="confirmed" if nit else "probable")
            cands.append({"candidate": f"nit:{r['nit']}", "label": rs[:80],
                          "basis": [f"RUES registry record (Camara {r.get('camara_comercio')}, "
                                    f"{r.get('estado_matricula')})"],
                          "evidence_urls": [url], "identity_match_confidence": round(min(conf, 0.97), 2),
                          "hint_matches": hints, "rues": r, "source_id": sid})
        if not nit and len(cands) > 1:
            for c in cands:  # several same-name registrations (e.g. in different chambers): none dominates alone
                c["identity_match_confidence"] = min(c["identity_match_confidence"], 0.8)
        return cands

    def _register_supplied_social(self) -> None:
        """Founder-supplied social profiles: recorded as official sources and profiles, never fetched
        (Instagram/TikTok/Facebook require login, and search engines rarely index small accounts)."""
        for url in self.company.social_urls or []:
            url = url if url.startswith("http") else "https://" + url
            plat = social_platform(url)
            if not plat:
                continue
            sid = self.register(url, "founder:social_url", f"{plat} profile supplied by the founder")
            if not sid:
                continue
            self.store.update_source(sid, source_type="first_party_social", identity_match_status="confirmed",
                                     processing_status="restricted_login_wall",
                                     exclusion_reason="Supplied by the founder; the platform requires login, "
                                                      "so content and counts were not collected.")
            self.store.upsert_observation("social_profile", f"{plat}|{canonical_url(url)}", {
                "platform": plat, "url": url, "classification": "official_supplied_by_founder",
                "affiliation_evidence": "founder", "source_ids": [sid], "followers": "UNKNOWN",
                "last_verified_activity": "UNKNOWN",
                "access_note": "Supplied by the founder; counts/activity not collected (login wall)."})

    def _apply_rues_identity(self) -> None:
        """If the chosen identity is a RUES record, adopt its NIT/legal name and record structured claims."""
        chosen = self.identity.get("chosen") or ""
        cand = self._rues_match or next((c for c in self.candidates if c["candidate"] == chosen and c.get("rues")), None)
        if not cand:
            return
        r, sid = dict(cand["rues"]), cand.get("source_id")
        fm = str(r.get("fecha_matricula") or "")
        if len(fm) == 8 and fm.isdigit():  # RUES gives YYYYMMDD; store ISO so timelines sort and read correctly
            r["fecha_matricula"] = f"{fm[:4]}-{fm[4:6]}-{fm[6:]}"
        nit_full = f"{r['nit']}-{r.get('digito_verificacion', '')}".rstrip("-")
        if not self.company.company_id:
            self.company.company_id = nit_full
        if not self.company.legal_name:
            self.company.legal_name = r.get("razon_social")
        if not sid:
            return
        status = "confirmed" if ("nit" in cand["hint_matches"] or (
            self.identity.get("status") in CONFIRMED_IDENTITY and cand["candidate"] == chosen)) else "inferred"
        self.identity["registry_record"] = {"candidate": cand["candidate"], "source_id": sid, "fact_status": status}
        self._structured_claim("legal_name_variant", r.get("razon_social", ""),
                               f"Registered legal name in RUES: {r.get('razon_social')}.", sid, status=status)
        self._structured_claim("tax_id_nit", nit_full, f"NIT {nit_full} per the RUES registry record.", sid,
                               status=status)
        reg = (f"Matricula {r.get('matricula')} at the Camara de Comercio de {str(r.get('camara_comercio', '')).title()}, "
               f"registered {r.get('fecha_matricula')}; status {r.get('estado_matricula')}; last renewed "
               f"{r.get('ultimo_ano_renovado')}; legal form {r.get('organizacion_juridica')}.")
        self._structured_claim("certification_or_registration", f"RUES {r.get('matricula')}", reg, sid,
                               event_date=r.get("fecha_matricula"), status=status)
        self.store.update_source(sid, retrieved_at_utc=iso(self.clock()))
        ciiu = ", ".join(x for x in [r.get("cod_ciiu_act_econ_pri"), r.get("cod_ciiu_act_econ_sec")] if x)
        if ciiu:
            self._structured_claim("economic_activity_ciiu", ciiu,
                                   f"Registered economic activity (CIIU): primary {r.get('cod_ciiu_act_econ_pri')}"
                                   + (f", secondary {r.get('cod_ciiu_act_econ_sec')}" if r.get("cod_ciiu_act_econ_sec") else "")
                                   + ".", sid, status=status)
        self.registry_extra["rues"] = {k: r.get(k) for k in (
            "camara_comercio", "matricula", "razon_social", "nit", "digito_verificacion", "fecha_matricula",
            "estado_matricula", "ultimo_ano_renovado", "cod_ciiu_act_econ_pri", "cod_ciiu_act_econ_sec",
            "organizacion_juridica")}

    def check_official_reachability(self) -> bool:
        """After a wave: if an official root failed, mark it and request an identity re-check."""
        changed = False
        unreachable = set(self.store.kv_get("unreachable_domains") or [])
        for official in list(self.company.official_urls):
            dom = registrable_domain(host_of(official))
            if dom in unreachable:
                continue
            roots = [s for s in self.store.sources() if registrable_domain(host_of(s["url"])) == dom
                     and urlsplit(s["url"]).path in ("", "/")]
            if not roots:
                continue
            statuses = {s.get("processing_status") for s in roots}
            if statuses and all(st in ("fetch_error", "dns_error", "http_error") for st in statuses):
                unreachable.add(dom)
                self.limitations.append(f"Official root of {dom} was unreachable ({', '.join(sorted(statuses))}); "
                                        "content from it comes only from archives/search metadata.")
                changed = True
        if changed:
            self.store.kv_set("unreachable_domains", sorted(unreachable))
            self.store.kv_set("phase_done:identity", False)
            if not self.store.kv_get("supplied_official_urls"):
                self.company.official_urls = []
        return changed

    # ------------------------------------------------------------------ mapping (Phase C)
    def phase_map(self, wave: int) -> None:
        for official in self.company.official_urls:
            domain = registrable_domain(host_of(official))
            if self.store.kv_get(f"mapped:{domain}"):
                continue
            base = f"{urlsplit(official).scheme or 'https'}://{urlsplit(official).netloc or domain}"
            self.register(base + "/", "map:official_root")
            self.enqueue(base + "/", 0, wave, "map", external=False, prio=100)
            sitemaps = []
            try:
                sitemaps = self.fetcher.robots_sitemaps(base + "/")
            except Exception as exc:  # noqa: BLE001
                self._err("robots", exc)
            sitemaps = list(dict.fromkeys(sitemaps + [base + "/sitemap.xml", base + "/sitemap_index.xml"]))
            seen_maps, count, cap = set(), 0, self.depth_cfg["sitemap_url_cap"]
            while sitemaps and len(seen_maps) < 60 and count < cap:
                sm = sitemaps.pop(0)
                if sm in seen_maps:
                    continue
                seen_maps.add(sm)
                resp = self.fetcher.get(sm)
                if resp.status != 200 or resp.error:
                    continue
                body = resp.body
                if sm.endswith(".gz"):
                    import gzip
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        continue
                urls, children = parse_sitemap(body.decode("utf-8", "replace"))
                sitemaps.extend(children)
                for loc, lastmod in urls:
                    if count >= cap:
                        break
                    if not self.company.is_official_url(loc):
                        continue
                    sid = self.register(loc, "map:sitemap")
                    if sid and lastmod:
                        self.store.update_source(sid, sitemap_lastmod=lastmod)
                    self.enqueue(loc, 1, wave, "sitemap", external=False)
                    count += 1
            self.registry_extra.setdefault("sitemaps", {})[domain] = {"sitemaps_read": sorted(seen_maps),
                                                                     "urls_from_sitemaps": count, "cap": cap}
            for name in ("tavily", "firecrawl"):
                p = self.providers[name]
                if p.available():
                    try:
                        for u in p.map_site(base, limit=min(cap, 1000)):
                            if self.company.is_official_url(u):
                                self.register(u, f"map:{name}")
                                self.enqueue(u, 1, wave, f"map:{name}", external=False)
                    except (BudgetExceeded, RuntimeError, ValueError) as exc:
                        self._err(f"{name}.map", exc)
            if not self.opt.skip_external:
                archive_enqueued, archive_cap = 0, max(10, int(self.depth_cfg["max_pages"] * 0.2))
                try:
                    for row in self.providers["wayback"].site_urls(domain, limit=min(cap, 3000)):
                        orig = row.get("original", "")
                        if not orig.startswith("http") or not self.company.is_official_url(orig):
                            continue
                        if (row.get("mimetype") or "").startswith(("image/", "text/css", "application/javascript",
                                                                     "font/", "application/font", "audio/", "video/")):
                            continue
                        if ASSET_RE.search(urlsplit(orig).path) or urlsplit(orig).query:
                            continue  # assets, and query-string pages (search results may carry personal data)
                        if archive_enqueued >= archive_cap:
                            self.register(orig, "map:wayback_cdx")
                            continue
                        archive_enqueued += 1
                        existed = self.store.source_by_url(orig)
                        sid = self.register(orig, "map:wayback_cdx")
                        if sid and not existed:
                            self.store.update_source(sid, archive_capture_at=row.get("timestamp"),
                                                     archive_url=f"https://web.archive.org/web/{row.get('timestamp')}/{orig}")
                            self.enqueue(orig, 2, wave, "archive_discovered", external=False, prio=12)
                except (BudgetExceeded, RuntimeError, ValueError) as exc:
                    self._err("wayback.site_urls", exc)
            self.store.kv_set(f"mapped:{domain}", True)
        self.store.kv_set("registry_extra", self.registry_extra)
        self.store.commit()

    # ------------------------------------------------------------------ external (Phase D)
    def phase_external(self) -> None:
        if self.opt.skip_external:
            return
        extra = self.registry_extra
        for domain in sorted(self.company.official_domains):
            if self.store.kv_get(f"external_done:{domain}"):
                continue
            self.store.kv_set(f"external_done:{domain}", True)
            try:
                info = self.providers["rdap"].domain(domain)
                sid = self.register(info["rdap_url"], "external:rdap", f"RDAP record for {domain}")
                self.store.update_source(sid, processing_status="extracted_structured", source_type="domain_registry",
                                         retrieved_at_utc=iso(self.clock()), identity_match_status="confirmed")
                extra.setdefault("rdap", {})[domain] = {**info, "source_id": sid}
                if info.get("registered_at"):
                    self._structured_claim("domain_registered", f"{domain} registered {info['registered_at'][:10]}",
                                           f"RDAP shows {domain} was registered on {info['registered_at'][:10]} "
                                           "(domain event, not company founding).", sid,
                                           event_date=info["registered_at"][:10], status="confirmed")
            except (BudgetExceeded, RuntimeError, ValueError, KeyError, IndexError, TypeError) as exc:
                self._err(f"rdap:{domain}", exc)
            try:
                caps = self.providers["wayback"].captures(domain)
                if caps:
                    years: dict[str, int] = {}
                    for c in caps:
                        years[c["timestamp"][:4]] = years.get(c["timestamp"][:4], 0) + 1
                    first, last = caps[0], caps[-1]
                    url = f"https://web.archive.org/web/{first['timestamp']}/{first['original']}"
                    sid = self.register(url, "external:wayback", f"Earliest archive capture of {domain}")
                    self.store.update_source(sid, processing_status="extracted_structured", source_type="web_archive",
                                             archive_capture_at=first["timestamp"], retrieved_at_utc=iso(self.clock()),
                                             identity_match_status="confirmed")
                    extra.setdefault("wayback", {})[domain] = {"first_capture": first["timestamp"],
                                                               "last_capture": last["timestamp"],
                                                               "captures_by_year": years, "source_id": sid,
                                                               "sampled_monthly": True}
                    self._structured_claim("earliest_archive_capture", f"{domain} {first['timestamp'][:8]}",
                                           f"Earliest Internet Archive capture of {domain} is {first['timestamp'][:8]} "
                                           "(proves the site existed by then, not when the company started).", sid,
                                           event_date=first["timestamp"][:8], status="confirmed")
                    for year in sorted(years)[:: max(1, len(years) // 6)]:
                        ts = next(c for c in caps if c["timestamp"].startswith(year))
                        aurl = f"https://web.archive.org/web/{ts['timestamp']}/{ts['original']}"
                        asid = self.register(aurl, "external:wayback_yearly_sample", f"{domain} archived {year}")
                        self.store.update_source(asid, archive_capture_at=ts["timestamp"], source_type="web_archive",
                                                 identity_match_status="confirmed")
                        self.enqueue(aurl, 1, 1, "archive_sample", external=True, prio=45)
            except (BudgetExceeded, RuntimeError, ValueError, KeyError) as exc:
                self._err(f"wayback:{domain}", exc)
        if self.company.country == "CO" and not self.store.kv_get("external_done:secop"):
            self.store.kv_set("external_done:secop", True)
            try:
                rows = self.providers["secop"].contracts(name=None if self.company.company_id else
                                                         (self.company.legal_name or self.company.name),
                                                         nit=self.company.company_id, limit=60)
                kept, related = [], {}
                for r in rows:
                    supplier = r.get("proveedor_adjudicado", "")
                    if not self.company.company_id:
                        own = {strip_legal(n) for n in self.company.all_names}
                        if strip_legal(supplier) not in own:
                            if self.company.name_match(supplier)["exact_phrase"]:
                                related.setdefault(supplier.strip()[:80], 0)
                                related[supplier.strip()[:80]] += 1
                            continue  # different legal entity sharing words (e.g. a foundation): not merged
                    kept.append(r)
                    link = r.get("urlproceso")
                    link = link.get("url") if isinstance(link, dict) else link
                    if link:
                        sid = self.register(link, "external:secop", f"SECOP II contract {r.get('id_contrato')}")
                        self.store.update_source(sid, processing_status="extracted_structured",
                                                 source_type="government_record", retrieved_at_utc=iso(self.clock()),
                                                 published_at=(r.get("fecha_de_firma") or "")[:10] or None,
                                                 published_at_basis="secop_fecha_de_firma",
                                                 identity_match_status="probable" if not self.company.company_id else "confirmed")
                        self._structured_claim(
                            "public_contract", f"{r.get('id_contrato')}",
                            f"SECOP II lists a contract awarded to '{safe_excerpt(supplier, 80)}' by "
                            f"{safe_excerpt(r.get('nombre_entidad', ''), 80)}, signed {(r.get('fecha_de_firma') or 'undated')[:10]}, "
                            f"value COP {r.get('valor_del_contrato', 'n/a')}, status {r.get('estado_contrato', 'n/a')}.",
                            sid, event_date=(r.get("fecha_de_firma") or "")[:10] or None,
                            status="confirmed" if self.company.company_id else "inferred")
                extra["secop"] = {"queried_by": "nit" if self.company.company_id else "name",
                                  "rows_returned": len(rows), "rows_kept_identity_matched": len(kept),
                                  "similar_name_suppliers_not_merged": related,
                                  "dataset": "datos.gov.co jbjy-vk9h (SECOP II contratos)"}
            except (BudgetExceeded, RuntimeError, ValueError) as exc:
                self._err("secop", exc)
                extra["secop"] = {"status": "error", "detail": str(exc)[:200]}
        self.store.kv_set("registry_extra", extra)
        self.store.commit()

    def _structured_claim(self, ctype: str, value: str, statement: str, sid: str, event_date=None, status="confirmed"):
        key = f"{self.company.slug}|{ctype}|{fold(value)[:120]}"
        preset = (self.prior or {}).get("claim_ids", {}).get(key)
        cid, _ = self.store.upsert_claim(key, {
            "claim_key": key, "claim_type": ctype, "subject": self.company.slug, "value": value, "statement": statement,
            "fact_status": status, "supporting_source_ids": [sid], "contradicting_source_ids": [],
            "locations": [sid], "extracted_at_utc": iso(self.clock()), "extraction_method": "structured:" + ctype,
            "event_date": event_date}, preset_id=preset)
        src = self.store.source(sid) or {}
        self.store.update_source(sid, linked_claim_ids=list(dict.fromkeys(src.get("linked_claim_ids", []) + [cid])))

    # ------------------------------------------------------------------ fetching
    def _fetch_and_parse(self, item: dict) -> PageResult:
        res = PageResult(item=item)
        url = item["url"]
        try:
            resp = self.fetcher.get(url)
        except Exception as exc:  # noqa: BLE001 - isolate per-URL failures
            res.status, res.reason = "error", f"{type(exc).__name__}: {exc}"
            return res
        res.response = resp
        if resp.error:
            if resp.error.startswith("blocked_robots"):
                res.status, res.reason = "blocked_robots", "Disallowed by robots.txt"
            elif resp.error.startswith("dns_error"):
                res.status, res.reason = "dns_error", resp.error
            elif resp.error.startswith("blocked_unsafe_url"):
                res.status, res.reason = "blocked_unsafe_url", resp.error
            else:
                res.status, res.reason = "fetch_error", resp.error
            return res
        if resp.status == 429:
            res.status, res.reason = "rate_limited", "HTTP 429 after retries"
            return res
        if resp.status in (401, 403):
            res.status, res.reason = "access_restricted", f"HTTP {resp.status} (authentication/blocked; not bypassed)"
            return res
        if resp.status == 404 or resp.status == 410:
            res.status, res.reason = "gone", f"HTTP {resp.status}"
            return res
        if resp.status >= 400:
            res.status, res.reason = "http_error", f"HTTP {resp.status}"
            return res
        if resp.truncated:
            res.status, res.reason = "excluded_oversized", f"> {self.fetcher.max_bytes} bytes"
            return res
        ctype = resp.content_type
        res.content_type = ctype
        if ctype in ("text/html", "application/xhtml+xml", "") or ctype.startswith("text/html"):
            html = resp.text()
            page = parse_html(html, resp.final_url or url)
            res.page = page
            res.blocks = [(b.section, b.text) for b in page.blocks]
            res.text = page.text
            res.status = "extracted"
        elif ctype == "application/pdf" or url.lower().endswith(".pdf"):
            text, status = pdf_text(resp.body)
            res.text = text
            res.blocks = [("", para) for para in text.split("\n") if len(para.strip()) > 2][:3000]
            res.status = "extracted_pdf" if text else status
        elif ctype in ("text/plain", "text/csv", "application/json"):
            text = resp.text()[:300000]
            res.text = text
            res.blocks = [("", line) for line in text.splitlines() if line.strip()][:3000]
            res.status = "extracted_text"
        else:
            res.status, res.reason = "excluded_non_text", f"content-type {ctype}"
        return res

    def _identity_status(self, url: str, text: str, title: str) -> str:
        if self.company.is_official_url(url):
            return "confirmed" if self.identity.get("status") in CONFIRMED_IDENTITY else "provisional"
        alt_domains = {a for a in self.identity.get("alternatives", []) if a != self.identity.get("chosen")}
        if registrable_domain(host_of(url)) in alt_domains and self.identity.get("status") == "ambiguous":
            return "candidate_alternative"
        nm = self.company.name_match(title + "\n" + text[:40000])
        locs = self.company.location_hits(text[:40000])
        foreign = self._foreign_country(text[:40000])
        if nm["exact_phrase"] and foreign and not locs:
            return "different_entity"
        if nm["exact_phrase"] and (locs or any(fold(h) in fold(text[:40000]) for h in self.company.industry_hints)):
            return "probable"
        if any(d in fold(text[:60000]) for d in self.company.official_domains):
            return "probable"
        if nm["exact_phrase"] or nm["token_ratio"] >= 0.5:
            return "uncertain"
        return "not_matched"

    def _foreign_country(self, text: str) -> bool:
        from .company import COUNTRY_NAMES_ES
        if not self.company.country:
            return False
        t = fold(text)
        own = fold(COUNTRY_NAMES_ES.get(self.company.country, ""))
        others = [fold(v) for k, v in COUNTRY_NAMES_ES.items() if k != self.company.country]
        other_hits = sum(t.count(o) for o in others if o)
        return other_hits >= 2 and (not own or t.count(own) == 0)

    def integrate(self, res: PageResult, wave: int) -> dict:
        """Main-thread only: write results to the store."""
        item = res.item
        url = item["url"]
        sid = item.get("source_id") or self.register(url, item.get("origin", "queue"))
        now = iso(self.clock())
        stats = {"fetched": 1, "new_claims": 0, "new_signals": 0, "novel": 0}
        if not res.status.startswith("extracted") or (res.status == "extracted" and not res.text.strip()):
            status = res.status or "fetch_error"
            self.store.update_source(sid, processing_status=status, exclusion_reason=res.reason or status,
                                     retrieved_at_utc=now, http_status=getattr(res.response, "status", None))
            self.store.mark_url(url, status)
            if status in ("gone",) and (self.prior or {}).get("src_ids", {}).get(canonical_url(url)):
                self.store.update_source(sid, change_status="disappeared_since_prior_run")
            return stats
        resp, page = res.response, res.page
        final = resp.final_url or url
        title = page.title if page else ""
        dates = extract_dates(page, resp.headers) if page else {
            "published_at": None, "published_at_basis": "not_available", "modified_at": resp.headers.get("last-modified"),
            "modified_at_basis": "http_last_modified_header" if resp.headers.get("last-modified") else "unknown"}
        src = self.store.source(sid) or {}
        if src.get("published_at") and dates["published_at"] is None:
            dates["published_at"], dates["published_at_basis"] = src["published_at"], src.get("published_at_basis")
        if "web.archive.org" in host_of(url):
            dates["archive_capture_at"] = src.get("archive_capture_at")
        identity = src.get("identity_match_status") if src.get("identity_match_status") == "candidate_alternative" \
            else self._identity_status(final, res.text, title)
        stype = classify_source_type(final, self.company, self.config, res.content_type)
        if stype == "unknown" and src.get("source_type") not in (None, "unknown"):
            stype = src["source_type"]
        content_hash = "sha256:" + __import__("hashlib").sha256(fold(res.text).encode()).hexdigest()
        dup_of = None
        if canonical_url(final) != canonical_url(url):
            target = self.store.source_by_url(final)
            if target and target["source_id"] != sid and (target.get("processing_status") or "").startswith("extracted"):
                dup_of = target["source_id"]
        if dup_of is None:
            if self.hash_index is None:
                self.hash_index = {x["content_hash"]: x["source_id"] for x in self.store.sources()
                                   if x.get("content_hash") and (x.get("processing_status") or "").startswith("extracted")}
            other = self.hash_index.get(content_hash)
            if other and other != sid:
                dup_of = other
        if dup_of:
            self.store.update_source(sid, processing_status="duplicate_content", duplicate_of=dup_of,
                                     exclusion_reason=f"same content as {dup_of} (redirect or identical page)",
                                     retrieved_at_utc=now, content_hash=content_hash, final_url=final if final != url else None,
                                     http_status=resp.status, **dates)
            self.store.mark_url(url, "duplicate_content")
            return stats
        self.hash_index[content_hash] = sid
        prior_src = None
        if self.prior:
            prior_src = next((s for s in self.prior["registry"]["sources"]
                              if s["canonical_url"] == canonical_url(url)), None)
        change = None
        if prior_src:
            change = "unchanged" if prior_src.get("content_hash") == content_hash else "changed_since_prior_run"
        elif self.prior:
            change = "new_since_prior_run"
        self.store.update_source(sid, title=safe_excerpt(title, 200) or src.get("title"), source_type=stype,
                                 final_url=final if final != url else None, retrieved_at_utc=now,
                                 identity_match_status=identity, content_hash=content_hash, lang=page.lang if page else None,
                                 http_status=resp.status, content_type=res.content_type, change_status=change,
                                 url_category=classify_url(final) if self.company.is_official_url(final) else "external",
                                 redirect_chain=resp.redirects or None, **dates)
        src = self.store.source(sid)
        # identity guard: homonyms and alternatives are recorded but not mined for this company
        findings = Findings()
        if identity not in ("different_entity", "candidate_alternative", "not_matched"):
            findings = analyze_source(self.company, src, res.blocks, page, resp.headers, self.run_date, self.config)
        new_claim_ids, new_signal_ids = [], []
        for key, data in findings.claims:
            preset = (self.prior or {}).get("claim_ids", {}).get(key)
            cid, new = self.store.upsert_claim(key, {**data, "claim_key": key}, preset_id=preset)
            new_claim_ids.append(cid)
            stats["new_claims"] += int(new)
        for key, data in findings.signals:
            preset = (self.prior or {}).get("signal_ids", {}).get(key)
            gid, new = self.store.upsert_signal(key, {**data, "signal_key": key}, preset_id=preset)
            new_signal_ids.append(gid)
            stats["new_signals"] += int(new)
        for kind, key, data in findings.observations:
            self.store.upsert_observation(kind, key, data)
        for key, data in findings.incidents:
            self.store.upsert_incident(key, data)
        for key, data in findings.edges:
            self.store.upsert_edge(key, data)
        for kind, value in findings.new_leads:
            if kind == "alias" and fold(value) not in {fold(a) for a in self.company.all_names}:
                self.leads.append((kind, value))
        # scores
        sh = shingles(res.text)
        rel, rel_why = relevance(self.company, final, title, res.text, identity)
        rlb, rlb_why = reliability(stype)
        fr, fr_why = freshness(dates.get("published_at"), dates.get("modified_at"), self.run_date,
                               dates.get("archive_capture_at"))
        nov, nov_why = novelty(sh, self.corpus, stats["new_claims"], stats["new_signals"])
        opp, opp_why = opportunity([d for _, d in findings.signals])
        self.corpus |= sh
        if nov >= 30:
            stats["novel"] = 1
        scores = {"relevance": rel, "reliability": rlb, "freshness": fr, "novelty": nov, "opportunity": opp}
        self.store.update_source(
            sid, relevance_score=rel, reliability_score=rlb, freshness_score=fr, novelty_score=nov,
            opportunity_signal_score=opp, processing_status=res.status,
            score_components={"relevance": rel_why, "reliability": rlb_why, "freshness": fr_why,
                              "novelty": nov_why, "opportunity": opp_why},
            score_rationale=f"relevance: {rel_why} | reliability: {rlb_why} | freshness: {fr_why}",
            research_priority=priority(scores, self.config["score_weights"]),
            linked_claim_ids=sorted(set(src.get("linked_claim_ids", []) + new_claim_ids)),
            linked_signal_ids=sorted(set(src.get("linked_signal_ids", []) + new_signal_ids)),
            low_confidence_caveat=("Retained with caveat: low reliability or uncertain identity."
                                   if (rlb < 45 or identity == "uncertain") and rel >= 30 else None))
        # passages (private DB only)
        ret = self.config["retention"]
        if ret.get("store_passages"):
            passages = [(sec[:80], src.get("url_category") or "", safe_excerpt(txt, ret["max_passage_chars"]))
                        for sec, txt in res.blocks if len(txt) > 30][: ret["max_passages_per_page"]]
            self.store.add_passages(sid, passages)
        self.store.mark_url(url, res.status)
        # follow links (official domain only; external links recorded, not crawled)
        if page and identity not in ("different_entity", "not_matched"):
            depth = item.get("depth", 0)
            for link, anchor in page.links:
                if not link.startswith("http"):
                    continue
                if self.company.is_official_url(link):
                    if depth + 1 <= self.depth_cfg["max_crawl_depth"] and self.company.is_official_url(final):
                        self.register(link, f"link:{sid}", anchor)
                        self.enqueue(link, depth + 1, wave, "link", external=False)
                elif self.company.is_official_url(final):
                    plat = social_platform(link)
                    lsid = self.register(link, f"outlink:{sid}", anchor)
                    if lsid and plat in PLATFORM_RESTRICTED:
                        self.store.update_source(lsid, processing_status="not_fetched_platform_restricted",
                                                 exclusion_reason="NOT_CHECKED_PROVIDER_RESTRICTED: social platform "
                                                                  "requires login or blocks automated access")
                    elif lsid and plat in ("github", "youtube", "reddit", "app_store", "google_play", "blog"):
                        self.enqueue(link, depth + 1, wave, "official_outlink", external=True, prio=50)
        return stats

    def process_queue(self, wave: int, quota: int) -> dict:
        totals = {"fetched": 0, "new_claims": 0, "new_signals": 0, "novel": 0}
        ext_share = self.depth_cfg["external_fetch_share"]
        workers = max(1, int(self.config["fetch"]["max_concurrency"]))
        while totals["fetched"] < quota:
            if self.pages_fetched() >= self.depth_cfg["max_pages"]:
                break
            if self.elapsed() > self.depth_cfg["max_runtime_s"]:
                break
            batch_n = min(workers * 2, quota - totals["fetched"])
            n_ext = max(1, int(batch_n * ext_share))
            batch = self.store.dequeue_batch(batch_n - n_ext, external=False) + \
                self.store.dequeue_batch(n_ext, external=True)
            if not batch:
                batch = self.store.dequeue_batch(batch_n)
            if not batch:
                break
            ready = []
            for item in batch:
                pat = url_pattern(item["url"])
                segs = [x for x in urlsplit(item["url"]).path.split("/") if x]
                if (not item.get("external") and len(segs) >= 2
                        and classify_url(item["url"]) in ("product_service", "commerce", "unknown")):
                    self.pattern_counts[pat] = self.pattern_counts.get(pat, 0) + 1
                    if self.pattern_counts[pat] > self.depth_cfg["catalog_sample_per_pattern"] and item.get("depth", 0) > 0:
                        sid = self.register(item["url"], item.get("origin", "queue"))
                        self.store.update_source(sid, processing_status="not_fetched_catalog_sampled",
                                                 exclusion_reason=f"pattern {pat} sampled (cap "
                                                                  f"{self.depth_cfg['catalog_sample_per_pattern']})")
                        self.store.mark_url(item["url"], "not_fetched_catalog_sampled")
                        continue
                self.store.mark_url(item["url"], "in_progress")
                ready.append(item)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(self._fetch_and_parse, ready))
            for res in results:
                host = host_of(res.item["url"])
                if res.status in ("fetch_error", "dns_error") or (res.status == "http_error" and res.reason.startswith("HTTP 5")):
                    self.host_failures[host] = self.host_failures.get(host, 0) + 1
                elif res.status.startswith("extracted"):
                    self.host_failures[host] = 0
                try:
                    s = self.integrate(res, wave)
                except Exception as exc:  # noqa: BLE001 - one bad page must not kill the run
                    self._err(f"integrate:{res.item.get('url')}", f"{exc}\n{traceback.format_exc()[-300:]}")
                    self.store.mark_url(res.item["url"], "error")
                    continue
                for k in totals:
                    totals[k] += s.get(k, 0)
            for host, n in list(self.host_failures.items()):
                if n >= HOST_FAILURE_LIMIT:
                    skipped = self.store.skip_host(host, "not_fetched_host_unavailable")
                    if skipped:
                        self.limitations.append(f"{host}: {n} consecutive failures; {skipped} queued URL(s) not "
                                                "fetched (host unavailable — circuit breaker).")
                    self.host_failures[host] = -10 ** 6
            self.store.kv_set("pattern_counts", self.pattern_counts)
            self.checkpoint()
        return totals

    # ------------------------------------------------------------------ checkpoint
    def checkpoint(self) -> None:
        self.store.kv_set("ledger_calls", {"calls": self.ledger.calls, "spend_known": self.ledger.spend_known})
        self.store.kv_set("errors", self.errors[-500:])
        self.store.commit()

    def facet_counts(self) -> dict:
        counts: dict[str, int] = {}
        for s in self.store.sources():
            if s.get("processing_status", "").startswith("extracted") and (s.get("relevance_score") or 0) >= 30:
                f = s.get("search_facet") or s.get("url_category") or "unknown"
                counts[f] = counts.get(f, 0) + 1
        return counts

    # ------------------------------------------------------------------ main loop
    def run(self) -> dict:
        self.store.kv_set("company", self.company.to_dict())
        if self.opt.mode == "refresh" and not self.store.kv_get("phase_done:refresh_load"):
            self.load_prior()
            self.store.kv_set("phase_done:refresh_load", True)
        elif self.opt.mode == "refresh":
            self.load_prior()
        if not self.store.kv_get("phase_done:plan"):
            planned = initial_queries(self.company, self.opt.depth, self.depth_cfg["max_search_queries"])
            if self.opt.mode == "targeted":
                planned = [q for q in planned if q["facet"].startswith("targeted:") or q["origin"] == "seed"
                           or q["facet"] == "identity_legal"]
            for q in planned:
                self.store.add_query(q)
            self.store.kv_set("phase_done:plan", True)
        if self.opt.dry_run:
            return self.estimate()
        wave = int(self.store.kv_get("wave") or 1)
        wave_log = self.store.waves()
        if any(w["wave"] == wave for w in wave_log):
            wave += 1  # resuming after a completed wave
        min_w, base_max_w = self.depth_cfg["min_waves"], self.depth_cfg["max_waves"]
        extra_w = int(self.store.kv_get("agent_extra_waves") or 0)
        max_w = base_max_w + extra_w
        while True:
            w_start = time.monotonic()
            qstats = self.execute_queries(wave)
            stuck_on_dead_domain = self.identity.get("chosen") in set(self.store.kv_get("unreachable_domains") or [])
            no_website_yet = str(self.identity.get("chosen") or "").startswith(("nit:", "profile:"))
            if (self.identity.get("status") in (None, "unresolved", "tentative") or stuck_on_dead_domain
                    or (no_website_yet and self.identity.get("status") != "confirmed_by_founder"))                     and self.store.kv_get("phase_done:identity"):
                # new search evidence (e.g. agent-imported results) may now resolve identity
                self.store.kv_set("phase_done:identity", False)
            self.phase_identity()
            self.phase_map(wave)
            self.phase_external()
            remaining = self.depth_cfg["max_pages"] - self.pages_fetched()
            quota = max(0, math.ceil(remaining / max(1, max_w - wave + 1) * 1.5))
            pstats = self.process_queue(wave, min(quota, remaining))
            if self.check_official_reachability():
                self.phase_identity()
                self.phase_map(wave)
            yield_ratio = round(pstats["novel"] / max(1, pstats["fetched"]), 3)
            rec = {"wave": wave, **{f"query_{k}": v for k, v in qstats.items()}, **pstats,
                   "novelty_yield": yield_ratio, "seconds": round(time.monotonic() - w_start, 1),
                   "queue": self.store.queue_counts()}
            self.store.save_wave(wave, rec)
            wave_log.append(rec)
            self.checkpoint()
            # --- stop conditions (reported precisely) -----------------------------
            queued = self.store.queue_counts().get("queued", 0)
            pending_q = len(self.store.queries("NOT_EXECUTED_NO_PROVIDER"))
            if self.pages_fetched() >= self.depth_cfg["max_pages"]:
                self.stop_reason = f"page budget reached (max_pages={self.depth_cfg['max_pages']})"
                break
            if self.elapsed() > self.depth_cfg["max_runtime_s"]:
                self.stop_reason = f"runtime budget reached (max_runtime_s={self.depth_cfg['max_runtime_s']})"
                break
            if wave >= max_w:
                if extra_w:
                    self.stop_reason = (f"maximum waves reached (max_waves={base_max_w} for depth {self.opt.depth}, "
                                        f"plus {extra_w} wave(s) to process agent-search imports)")
                else:
                    self.stop_reason = f"maximum waves reached (max_waves={base_max_w})"
                break
            # Phase G: gap-driven queries for the next wave
            executed = {q["query"].lower() for q in self.store.queries()}
            budget_left = self.depth_cfg["max_search_queries"] - len([q for q in self.store.queries()
                                                                      if q.get("status") == "executed"])
            new_q = gap_queries(self.company, wave + 1, self.facet_counts(), self.leads, executed, budget_left)
            for q in new_q:
                self.store.add_query(q)
            self.leads = []
            if queued == 0 and not self.search_providers() and wave >= min_w:
                self.stop_reason = ("no further executable work: no search API configured; "
                                    f"{pending_q + len(new_q)} queries await the agent-search loop "
                                    "(pending_queries.json → ingest-search → resume)")
                break
            if wave >= min_w and yield_ratio < self.depth_cfg["novelty_stop_threshold"] and queued == 0:
                self.stop_reason = (f"marginal novelty: wave {wave} novelty yield {yield_ratio} < "
                                    f"{self.depth_cfg['novelty_stop_threshold']} and no queued URLs")
                break
            wave += 1
            self.store.kv_set("wave", wave)
        self.store.kv_set("wave", wave)
        self.finalize()
        return self.summary()

    # ------------------------------------------------------------------ finalize
    def finalize(self) -> None:
        claims = self.store.claims()
        sources = {s["source_id"]: s for s in self.store.sources()}
        contradictions = detect_contradictions(claims)
        contradicted_ids = {cid for _, ids in contradictions for cid in ids}
        self.registry_extra["contradictions"] = [{"claim_type": t, "claim_ids": ids} for t, ids in contradictions]
        for c in claims:
            sup = [sources[s] for s in c.get("supporting_source_ids", []) if s in sources]
            strength = max([s.get("reliability_score") or 0 for s in sup] or [0])
            domains = {registrable_domain(host_of(s["url"])) for s in sup}
            status = c.get("fact_status", "inferred")
            if c["claim_id"] in contradicted_ids:
                status = "contradicted"
                others = [i for _, ids in contradictions for i in ids if c["claim_id"] in ids and i != c["claim_id"]]
                contra_src = sorted({sid for o in others for sid in (next((x for x in claims if x["claim_id"] == o), {})
                                                                    .get("supporting_source_ids", []))})
                c["contradicting_source_ids"] = contra_src
            elif any(s.get("source_type") in ("official_registry", "government_record", "domain_registry") for s in sup):
                status = "confirmed"
            elif len(domains) >= 2 and status != "hypothesis":
                status = "confirmed"
            band = confidence_band(strength, len(domains), status == "contradicted")
            self.store.update_claim(c["claim_id"], fact_status=status, evidence_strength=strength,
                                    confidence_band=band, independent_domains=len(domains),
                                    contradicting_source_ids=c.get("contradicting_source_ids", []))
        for _, ids in contradictions:
            for cid in ids:
                c = next(x for x in claims if x["claim_id"] == cid)
                for sid in c.get("supporting_source_ids", []):
                    s = sources.get(sid)
                    if s:
                        r, why = reliability(s.get("source_type", "unknown"), contradictions=1)
                        self.store.update_source(sid, reliability_score=r, contradiction_note=why)
        signals = self.store.signals()
        hyps = build_hypotheses(signals, {s["source_id"]: s for s in self.store.sources()})
        if self.prior:
            used = {h["opp_id"] for h in hyps}
            prior_map = self.prior.get("opp_by_function", {})
            next_n = max([int(i.split("-")[1]) for i in prior_map.values()] + [0])
            for h in hyps:
                if h["business_function"] in prior_map:
                    h["opp_id"] = prior_map[h["business_function"]]
                    h["status_vs_prior"] = "carried"
                else:
                    next_n += 1
                    h["opp_id"] = f"OPP-{next_n:03d}"
                    h["status_vs_prior"] = "new_since_prior_run"
            del used
        write_json(self.run_dir / "data" / "opportunity_hypotheses.json", hyps)
        self.store.kv_set("registry_extra", self.registry_extra)
        self.store.commit()
        export_jsonl(self.store, self.run_dir)
        if not self.search_providers():
            for q in self.store.queries("planned"):
                self.store.update_query(q["query_id"], status="NOT_EXECUTED_NO_PROVIDER")
        pending = self.store.queries("NOT_EXECUTED_NO_PROVIDER")
        write_json(self.run_dir / "pending_queries.json", {
            "instructions": "Execute each query with the agent's web-search tool, save results as "
                            "[{query_id, query, results:[{url,title,snippet,published?}]}] and run "
                            "`cumbre.py ingest-search <run_dir> <file>` then `cumbre.py resume <run_dir>`.",
            "queries": [{"query_id": q["query_id"], "query": q["query"], "facet": q.get("facet"),
                         "language": q.get("language"), "wave": q.get("wave")} for q in pending]})
        if pending:
            self.limitations.append(f"{len(pending)} planned search queries were not executed by an API "
                                    "(NOT_EXECUTED_NO_PROVIDER); run the agent-search loop to cover them.")
        self.write_manifest()
        from .reports import render_all
        render_all(self.run_dir, self)
        self.write_manifest()
        self.store.commit()

    def provider_rows(self) -> list[dict]:
        return provider_report(self.providers)

    def write_manifest(self) -> None:
        write_manifest(self.run_dir, {
            "run_id": self.run_id, "company_run_id": f"{self.company.slug}:{self.run_id}",
            "engine_version": __version__, "config_version": self.config["config_version"],
            "scoring_version": self.config["scoring_version"], "schema_version": self.config["schema_version"],
            "company": self.company.to_dict(), "depth": self.opt.depth, "mode": self.opt.mode,
            "depth_config": self.depth_cfg, "started_at_utc": iso(self.started), "finished_at_utc": iso(self.clock()),
            "runtime_s": round(self.elapsed(), 1), "stop_reason": self.stop_reason or "in progress",
            "identity_decision": self.identity, "providers": self.provider_rows(),
            "provider_calls": self.ledger.calls, "provider_events_tail": self.ledger.events[-300:],
            "paid_calls_skipped_by_budget": self.ledger.skipped,
            "spend": {"known_usd": round(self.ledger.spend_known, 4),
                      "paid_calls_with_unknown_unit_cost": self.ledger.spend_unknown_calls,
                      "note": "Spend is only reported where unit costs were configured; never estimated."},
            "llm": {"engine_llm_calls": 0, "max_llm_tokens": self.opt.max_llm_tokens,
                    "note": "The engine calls no LLM. Agent-reviewed findings are imported via ingest-findings "
                            "and listed in agent_imports."},
            "agent_imports": self.store.kv_get("agent_imports") or [],
            "waves": self.store.waves(), "queue": self.store.queue_counts(), "errors": self.errors[-200:],
            "limitations": self.limitations, "prior_run": (self.prior or {}).get("dir"),
            "session_correlated": self.opt.session,
        })

    def estimate(self) -> dict:
        planned = self.store.queries("planned")
        search = self.search_providers()
        est = {"dry_run": True, "company": self.company.name, "depth": self.opt.depth,
               "planned_queries_wave1": len(planned), "max_search_queries": self.depth_cfg["max_search_queries"],
               "max_pages": self.depth_cfg["max_pages"], "max_waves": self.depth_cfg["max_waves"],
               "search_providers_available": [p.name for p in search], "providers": self.provider_rows(),
               "paid_call_estimate": {}, "estimated_spend_usd": None}
        total, unknown = 0.0, False
        for p in search:
            key = f"{p.name}.search"
            n = min(self.depth_cfg["max_search_queries"], int(self.ledger.caps.get(key) or 0))
            est["paid_call_estimate"][key] = n
            unit = self.ledger.unit_costs.get(key)
            if unit is None:
                unknown = True
            else:
                total += n * unit
        est["estimated_spend_usd"] = None if unknown or not search else round(total, 2)
        est["note"] = ("No search API available: wave queries will be written to pending_queries.json for the "
                       "agent-search loop." if not search else
                       "Upper bound from call caps; unit costs null => spend unknown.")
        return est

    def summary(self) -> dict:
        sources = self.store.sources()
        return {"run_dir": str(self.run_dir), "company": self.company.name, "slug": self.company.slug,
                "depth": self.opt.depth, "mode": self.opt.mode, "stop_reason": self.stop_reason,
                "identity": self.identity.get("status"),
                "sources_discovered": len(sources),
                "sources_processed": sum(1 for s in sources if s.get("processing_status", "").startswith("extracted")),
                "claims": len(self.store.claims()), "signals": len(self.store.signals()),
                "incidents": len(self.store.incidents()),
                "pending_agent_queries": len(self.store.queries("NOT_EXECUTED_NO_PROVIDER")),
                "founder_asks": self._founder_asks(),
                "errors": len(self.errors)}

    def _founder_asks(self) -> list[str]:
        try:
            return [a["question"] for a in json.loads((self.run_dir / "data" / "founder_asks.json").read_text(encoding="utf-8"))]
        except (OSError, ValueError, KeyError, TypeError):
            return []


def confirm_identity(run_dir: Path, official_url: str | None = None, nit: str | None = None,
                     legal_name: str | None = None, candidate: str | None = None, note: str | None = None,
                     social_urls: list[str] | None = None) -> dict:
    """Record a founder-confirmed identity in an existing run; the next `resume` uses it."""
    if not any([official_url, nit, legal_name, candidate, social_urls]):
        raise ValueError("give at least one of --official-url, --nit, --legal-name, --candidate, --social-url")
    for u in social_urls or []:
        if not social_platform(u if u.startswith("http") else "https://" + u):
            raise ValueError(f"not a recognized social profile URL: {u!r}")
    if nit:
        ok, why = validate_nit(nit)
        if not ok:
            raise ValueError(f"invalid NIT {nit!r}: {why}")
    store = Store(run_dir / "data" / "company_research.sqlite")
    try:
        if candidate:
            known = {c["candidate"] for c in (store.kv_get("candidates") or [])}
            if candidate not in known:
                raise ValueError(f"unknown candidate {candidate!r}; known: {sorted(known) or 'none'}")
        company = store.kv_get("company") or {}
        if official_url:
            official_url = official_url if official_url.startswith("http") else "https://" + official_url
            supplied = list(dict.fromkeys((store.kv_get("supplied_official_urls") or []) + [official_url]))
            store.kv_set("supplied_official_urls", supplied)
            company["official_urls"] = list(dict.fromkeys((company.get("official_urls") or []) + [official_url]))
        if nit:
            company["company_id"] = nit
        if legal_name:
            company["legal_name"] = legal_name
        if social_urls:
            company["social_urls"] = list(dict.fromkeys((company.get("social_urls") or []) + list(social_urls)))
        store.kv_set("company", company)
        override = {"official_url": official_url, "nit": nit, "legal_name": legal_name, "candidate": candidate,
                    "social_urls": social_urls or [], "note": note, "at": iso(utc_now())}
        prev = store.kv_get("identity_override") or {}
        for k, v in prev.items():  # a later confirmation adds to an earlier one; it never erases it
            if k == "social_urls":
                override[k] = list(dict.fromkeys((v or []) + override[k]))
            elif override.get(k) in (None, "") and k != "at":
                override[k] = v
        store.kv_set("identity_override", override)
        store.kv_set("phase_done:identity", False)
        for k in [r[0] for r in store.db.execute("SELECT k FROM kv WHERE k LIKE 'external_done:%'")]:
            store.kv_set(k, False)  # re-run registry lookups (e.g. SECOP by NIT) with the confirmed data
        store.commit()
    finally:
        store.close()
    return {"run_dir": str(run_dir), "identity_override": override,
            "next": "run `resume` to research with the confirmed identity"}


def resume_run(run_dir: Path, config: dict, fetcher: Fetcher | None = None, overrides: dict | None = None) -> dict:
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8")) \
        if (run_dir / "run_manifest.json").is_file() else {}
    store = Store(run_dir / "data" / "company_research.sqlite")
    company = Company.from_dict(store.kv_get("company") or manifest["company"])
    store.db.close()
    opts = Options(depth=manifest.get("depth", "deep"), mode=manifest.get("mode", "pre-meeting"), **(overrides or {}))
    root = run_dir.parent.parent.parent
    run = ResearchRun(company, config, opts, root, fetcher=fetcher, run_dir=run_dir)
    # Agent imports (ingest-search) since the last resume earn one extra wave to process them. Extra waves are
    # counted separately so the configured max_waves of the depth is reported unchanged.
    run.store.kv_set("wave", int(run.store.kv_get("wave") or 1))
    search_imports = [i for i in (run.store.kv_get("agent_imports") or []) if i.get("kind") == "search"]
    consumed = int(run.store.kv_get("agent_imports_consumed") or 0)
    if len(search_imports) > consumed:
        run.store.kv_set("agent_imports_consumed", len(search_imports))
        run.store.kv_set("agent_extra_waves", int(run.store.kv_get("agent_extra_waves") or 0) + 1)
    return run.run()
