# 0004 — Company intelligence research engine (`cumbre research`)

- **Status:** Accepted (pending founder review of provider spend policy)
- **Date:** 2026-11-16
- **Spec:** Cumbre Company Intelligence Master Build Prompt v3 (supplied by the founders; full requirements summarized in [`docs/research/README.md`](../research/README.md))

## Context

Cumbre needs deep, source-backed public research on prospects before discovery meetings, and a clean handoff into the Discovery Engine (ADR 0003). At build time no paid search/crawl API key (Tavily, Firecrawl, Brave) was configured. Free public sources were verified live on 2026-11-16: Internet Archive CDX, RDAP (rdap.org), Wikidata search, datos.gov.co SECOP II (`jbjy-vk9h`), GDELT DOC 2.0 (rate limit: 1 request / 5 s).

## Decision

- **Python 3 stdlib, no required dependencies**, matching `scripts/discovery/`. Optional: PyYAML (YAML company files), pypdf (PDF text). Lives in `scripts/research/` (a tool, not an app).
- **Deterministic retrieval, agent reasoning.** The engine searches, maps, fetches, extracts, scores and stores. It never asks an LLM to invent search results. Rule-based extractors produce claims/signals; the operating agent (Claude Code) reviews the evidence pack and adds findings through `ingest-findings`, which validates every cited source ID. No LLM API is called by the engine, so there is no hidden token spend.
- **Provider adapters with declared capabilities.** Paid providers (Tavily, Firecrawl, Brave) run only with an API key **and** an explicit `--allow-paid` plus per-provider call caps; unit costs are left `null` until the founders enter real plan prices, so the engine never fabricates spend. Free adapters: direct HTTP (robots-aware), sitemaps, Wayback, RDAP, Wikidata, GDELT, SECOP.
- **Agent web search as a provider.** When no search API is configured, the run writes `pending_queries.json`; the skill has Claude Code execute them with its WebSearch tool and import results via `ingest-search`. Depth is never silently reduced: unexecuted queries are reported as `NOT_EXECUTED_NO_PROVIDER`.
- **Storage:** per-run JSONL registries + SQLite (FTS5) in `clients/<slug>/research/<date>-<run>/`, which is git-ignored (same privacy guard as the discovery kit; `CUMBRE_CLIENTS_DIR` supported).
- **Safety:** SSRF guard on every hop, robots.txt, per-host rate limits, size caps, prompt-injection neutralization, PII/secret redaction, passive-only risk research.

## Consequences

- Works today without spending money; quality of web discovery depends on the agent-search loop until a search API is approved.
- Paid adapters are unit-tested against documented response shapes (docs checked 2026-11-16) but not live-tested.
- Heuristic extraction has limited recall/precision on unusual sites; the agent review step and founder review remain necessary.
