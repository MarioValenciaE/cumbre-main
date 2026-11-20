# Company intelligence research (`cumbre research`)

Passive, source-driven public research on prospects, built to the founders' **Master Build Prompt v3** (sections 0–21). It produces a five-minute meeting brief **and** an unrestricted, cited, searchable knowledge base, then hands clean `[EXTERNO]` context to the Discovery Engine ([`docs/discovery/README.md`](../discovery/README.md)). Decision record: [ADR 0004](../decisions/0004-company-intelligence-engine.md). Claude Code skill: [`/cumbre-company-research`](../../.claude/skills/cumbre-company-research/SKILL.md).

## Quick start

```bash
python scripts/research/cumbre.py providers                     # what is available / which keys are missing
python scripts/research/cumbre.py research "Empresa ABC" --country CO --city Cali --depth deep
python scripts/research/cumbre.py research "Empresa ABC" --official-url https://example.com --depth exhaustive --dry-run
python scripts/research/cumbre.py research --companies-file targets.yaml --depth deep --mode pre-meeting
python scripts/research/cumbre.py research --company "Empresa ABC" --company "Empresa XYZ" --depth deep
python scripts/research/cumbre.py research "Empresa ABC" --mode post-meeting --session clients/empresa-abc/sessions/2026-11-16
python scripts/research/cumbre.py research "Empresa ABC" --mode refresh --stale-days 30
python scripts/research/cumbre.py research "Empresa ABC" --mode targeted --focus inventario
python scripts/research/cumbre.py demo --out <scratch folder>   # synthetic end-to-end run, no network
```

Other commands: `resume`, `pending`, `confirm-identity` (record a founder-confirmed official URL / NIT / legal name / candidate, then `resume`), `ingest-search`, `ingest-findings`, `render`, `lint`, `query`, `diff`, `migrate`. Add `--json` anywhere for machine output. Budget flags: `--max-spend --max-search-queries --max-pages --max-depth --max-runtime --max-llm-tokens --max-concurrency --provider-cap name=N --allow-paid`.

## How a run works

| Phase | What happens | Code |
| --- | --- | --- |
| A Identity | Candidate table from supplied URL, Wikidata and search hits; `confirmed_by_hint` / `dominant` / `provisional` (labelled assumption) / `ambiguous` (alternatives researched for identity only) / `tentative` (only a company-directory profile matches; no website) / `unresolved`; `confirmed_by_founder` after `confirm-identity`. Short names (one word or ≤8 letters) get the city appended to every query. Never merges candidates. | `planner.py` |
| B Query universe | Facet × language × name-variant matrix (14 facets, es/en, aliases, product keywords, focus). | `planner.py` |
| C Mapping | robots.txt, sitemaps (incl. indexes), internal links to `max_crawl_depth`, Wayback URL history, optional Tavily/Firecrawl map. URL categories; huge catalogs sampled per URL pattern with counts recorded. | `pipeline.py`, `extract.py` |
| D External | RDAP, Wayback captures by year + yearly samples, GDELT news, SECOP II (CO, NIT suppliers only, no personal fields), Wikidata. RUES (CO) runs in phase A: by NIT if known, else exact legal name; a single exact match resolves website-less companies, otherwise it corroborates the site candidate and adds NIT / matrícula / CIIU / status claims. | `providers.py`, `pipeline.py` |
| E Signals | 27 rule families (quoting, chat ordering, PDF catalogs, wholesale, payments, PQRS, hiring, spreadsheets, compliance…), one record per observation, many per page. | `analyze.py` |
| F Verification | Claims with `fact_status`, `evidence_strength`, `confidence_band`, supporting/contradicting sources; contradiction detection; distinct event / publication / modification / archive / retrieval dates. | `analyze.py`, `pipeline.finalize` |
| G Gap waves | Thin facets, new aliases/domains, documents → next wave; novelty yield per wave. | `planner.gap_queries` |
| H Audit | `coverage_report.md` with exact stop reason. | `reports.py` |

Stop reasons are reported verbatim: page/runtime budget, maximum waves, marginal novelty, or "no further executable work" (queries awaiting the agent-search loop).

### Depth

Configured in [`scripts/research/research_defaults.json`](../../scripts/research/research_defaults.json) (versioned). Targets, not quotas:

| Depth | Target relevant pages | max_pages | max queries | waves (min–max) |
| --- | --- | --- | --- | --- |
| standard | 30–80 | 120 | 40 | 1–2 |
| deep (default) | 100–300 | 400 | 150 | 2–4 |
| exhaustive | 300–1,000+ | 1,500 | 500 | 3–8 |

## Providers and credentials

| Provider | Capabilities | Cost | Status without setup |
| --- | --- | --- | --- |
| Direct HTTP | map, crawl, extract, tech fingerprint | free | works (robots-aware, 1 req/s/host) |
| Wayback CDX, RDAP, Wikidata, SECOP II, GDELT | archive, registry, news | free (GDELT: 1 req / 5 s) | works; verified live 2026-11-16 |
| RUES open data (Confecámaras, datos.gov.co `c82u-588k`, CC BY-SA 4.0) | Colombian business registry: legal name, NIT + DV, chamber, matrícula, CIIU, status, last renewal | free, no key | works; verified live 2026-11-20. Legal entities only; legal-representative and natural-person fields are never requested. The DIAN RUT lookup (MUISCA) has a CAPTCHA and is not automated. |
| Agent web search (Claude Code WebSearch) | search | covered by the agent session | via `pending` → `ingest-search` → `resume` |
| Tavily (search/map/extract) | search, map, extract | paid credits | `TAVILY_API_KEY` + `--allow-paid` + `--provider-cap tavily.search=N` |
| Brave Search | search | paid | `BRAVE_SEARCH_API_KEY` + same authorization |
| Firecrawl (map/scrape) | map, extract | paid credits | `FIRECRAWL_API_KEY` + same authorization |

Unit costs in `provider_unit_costs_usd` are `null` until the founders enter real plan prices; until then spend is reported as **unknown**, never estimated. Calls are capped per endpoint and cached in the run database, so a resumed run never repeats a paid request. Not automated (no verified open API): RUES, Supersociedades, chambers of commerce, Google Business Profile, LinkedIn, BuiltWith/Wappalyzer, HIBP (needs owner-verified domain control).

## Outputs

`clients/<slug>/research/<YYYY-MM-DD>-<run>/` (git-ignored; or `$CUMBRE_CLIENTS_DIR`). The engine refuses to write anywhere Git could pick it up.

- Entry points: `00_INDEX.md`, `EXECUTIVE_BRIEF.md` (exact 5-minute template, ≤5 snapshot bullets), `MEETING_TALKING_POINTS.md`, `DISCOVERY_FOLLOWUPS.md`.
- Full view: `FULL_COMPANY_DOSSIER.md` + the 12 facet files, `OPPORTUNITY_SIGNAL_CATALOG.md` (every signal), `opportunity_hypotheses.md`, `discovery_questions.md`, `research_gaps.md`, `ownership_funding_and_recent_events.md`, `public_risk_observations.md`, `public_incidents_and_advisories.md`.
- Audit: `coverage_report.md`, `research_journal.md`, `run_manifest.json` (providers, calls, spend, waves, errors, versions), `research_config.yaml`, `pending_queries.json`.
- Evidence: `sources/*.jsonl` (all discovered URLs with fate and scores, claims, signals, edges, incidents, observations, queries), `data/company_research.sqlite` (FTS5 passages; `query` command), `data/structured_profile.json` (schema v3).
- Refresh: `history/change_log.md` (new/changed/disappeared pages, new/contradicted claims, new opportunities); stable SRC/CLM/SIG/OPP IDs reused across runs; past runs untouched.
- Batches: `clients/_batches/<date>-<id>/batch_manifest.json` + `BATCH_OVERVIEW.md`.

## Scoring (scoring_version 1.0.0)

Five independent 0–100 heuristics with written rationale per source: **relevance** (identity, location, business density; homonyms ≤ 8), **reliability** (publisher type for the claim at hand, minus contradictions), **freshness** (null when undated — retrieval is never publication), **novelty** (new text + new claims/signals vs corpus), **opportunity** (signal strength). A configurable composite only orders the crawl queue; nothing is deleted on score. The brief's overall confidence is coverage-and-evidence quality, not a probability of legitimacy.

## Safety and privacy

- SSRF guard on every redirect hop (private, loopback, link-local, metadata, reserved IPs; non-HTTP schemes; odd ports; credentials in URL); size caps; robots.txt; per-host rate limits; no CAPTCHA/login bypass.
- Page text is untrusted: instruction-like text is flagged and stripped; reports paraphrase and cite.
- Redaction of personal emails (role mailboxes kept), phones, national IDs, card numbers and secrets. Incidental secrets are recorded as type-only metadata, `UNVERIFIED`, owner validation recommended.
- Incidents come from published reports only and must name the company in the same passage (collision guard). No-hit wording: *"No relevant public incident reports were identified in the specific sources checked as of YYYY-MM-DD; this does not establish that the company is secure or incident-free."* CVE applicability stays `UNDETERMINED` without evidenced versions.
- Post-meeting correlation reads the session locally; session content is never put into queries or sent anywhere.

## Agent loop

The engine never calls an LLM. Claude Code supplies (1) web search for queries no API covers and (2) a review layer: findings imported with `ingest-findings` must cite existing `SRC-` IDs or the import is rejected; provenance records the model label.

## Tests

```bash
python -m unittest discover -s scripts/research/tests -p "test_*.py"
```

52 offline tests on a fictional web (`scripts/research/tests/synthetic_web.py`): homonyms, legal-name changes, 280+ URLs with sampling and dedupe, five signals on one page, event vs publication dates, contradictions, restricted social platforms, auth visible vs internals unknown, job-ad vs engineering-article evidence classes, rate limits, provider failure, checkpoint/resume, budget and monetary caps, paid-call caching, PII/secret redaction, prompt injection, SSRF/unsafe redirects, citation lint, refresh change detection with stable IDs, batch isolation and partial failure, migration, Discovery Engine handoff and post-meeting separation, plus regressions for the defects found in the live validation below.

## Live validation (2026-11-16)

Authorized public-company test: `research "Alpina" --country CO --industry lácteos --depth standard`, no paid APIs, 7 queries executed through the agent-search loop (≈45 result URLs), 110-page budget.

- **Worked:** Wikidata full-text + country match found the right company among homonyms (insect genus, German car maker, watches); the company's foundation was correctly kept as a separate, tied candidate (`ambiguous`, labelled). Wikidata listed a retired domain (alpina.com.co, DNS failure); the engine marked it unreachable and moved provisionally to alpina.com from search evidence. 2,769 URLs inventoried, 73 processed, 0 lint errors.
- **Manually verified against sources:** founding 1945, NIT 860.025.900-2 (company's own T&C PDF), current leadership (2 independent outlets), Clover Sonoma acquisition (2021), Fitch AAA (2021), Feb-2026 MinTrabajo plant-area closure (flagged as an attributed regulatory report, not a finding).
- **Defects found and fixed during validation:** DNS failures labelled as unsafe URLs; 1,200 archived asset/query URLs flooding the queue (one carried a person's name — query-string archive URLs are now dropped); redirects to the homepage counted as separate sources (content-hash duplicate control added); footer text duplicated one signal per page; publishers' own NITs, a head of state and sidebar headlines attributed to the company; "desde 2009" in a rating PDF read as a founding date; "4.000 tiendas" parsed as "000"; news sites' share buttons recorded as company social profiles; the external-source queue starved by a SQL limit bug; `resume` ignoring budget flags.
- **Still weak:** marketing headings counted as "offerings"; signal themes skew to whatever text the official site repeats (e.g. PQRS/privacy pages); only 7 of 54 planned queries were run in this test, so hiring, reviews and social coverage are thin; legal-form variants (S.A. vs S.A.S. BIC) are listed, not resolved — RUES is not automated.

## Known limitations

- Rule-based extraction is Spanish/English keyword driven; recall on unusual sites is limited and some headings are navigation noise. The agent review step and founder review remain necessary.
- Without a search API, breadth depends on the agent-search loop; unexecuted queries are always listed.
- JavaScript-rendered sites yield little through direct HTTP; Firecrawl (if approved) is the fallback.
- Social metrics, LinkedIn, Google Business Profile and Colombian company registries are not automated.
