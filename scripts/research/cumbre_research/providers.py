"""Provider adapters with declared capabilities and budget/cost accounting.

Capabilities: search, map, crawl, extract, registries, historical, social,
tech_fingerprint, archive, news.

Paid adapters (Tavily, Firecrawl, Brave) need an API key in the environment AND
explicit authorization (--allow-paid + a per-endpoint call cap). Unit costs are
`null` until the founders enter real plan prices, so spend is reported as unknown
instead of guessed. Free adapters still count calls and respect rate limits.

API shapes verified against provider docs on 2026-11-16 (see ADR 0004).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field

from .fetch import Fetcher, Response
from .util import iso, short_hash, utc_now

STATUS_OK = "ok"
NOT_CONFIGURED = "NOT_CONFIGURED_MISSING_CREDENTIAL"
NOT_AUTHORIZED = "NOT_CHECKED_PROVIDER_RESTRICTED"   # key present but paid use not authorized / cap 0
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
PROVIDER_ERROR = "PROVIDER_ERROR"


class BudgetExceeded(Exception):
    pass


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    published: str | None = None
    provider: str = ""
    query: str = ""
    rank: int = 0


@dataclass
class Ledger:
    """Counts every provider call; enforces caps and the monetary ceiling."""
    caps: dict
    unit_costs: dict
    max_spend: float
    allow_paid: bool
    calls: dict = field(default_factory=dict)
    spend_known: float = 0.0
    spend_unknown_calls: int = 0
    skipped: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def authorize(self, key: str, paid: bool) -> None:
        if not paid:
            return
        if not self.allow_paid:
            raise BudgetExceeded(f"{key}: paid provider use not authorized (--allow-paid)")
        cap = int(self.caps.get(key) or 0)
        if self.calls.get(key, 0) >= cap:
            raise BudgetExceeded(f"{key}: call cap {cap} reached")
        unit = self.unit_costs.get(key)
        if unit is not None and self.max_spend >= 0 and self.spend_known + float(unit) > self.max_spend:
            raise BudgetExceeded(f"{key}: monetary ceiling ${self.max_spend:.2f} would be exceeded")

    def record(self, key: str, paid: bool, status: int | str, note: str = "", units: float = 1.0) -> None:
        self.calls[key] = self.calls.get(key, 0) + 1
        unit = self.unit_costs.get(key)
        cost = None
        if paid:
            if unit is None:
                self.spend_unknown_calls += 1
            else:
                cost = float(unit) * units
                self.spend_known += cost
        self.events.append({"at": iso(utc_now()), "provider_call": key, "paid": paid, "status": status,
                            "cost_usd": cost, "note": note[:200]})


class Provider:
    name = "base"
    capabilities: set[str] = set()
    paid = False
    env_key: str | None = None

    def __init__(self, fetcher: Fetcher, ledger: Ledger, cache=None, min_interval: float | None = None):
        self.fetcher = fetcher
        self.ledger = ledger
        self.cache = cache            # store.ProviderCache-like: get(key)/put(key, value)
        self.min_interval = min_interval

    # availability ---------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.env_key) if self.env_key else None

    def status(self) -> tuple[str, str]:
        if self.env_key and not self.api_key:
            return NOT_CONFIGURED, f"set {self.env_key} in the environment (.env.example lists it)"
        if self.paid and not self.ledger.allow_paid:
            return NOT_AUTHORIZED, "paid provider: run with --allow-paid and --provider-cap after founder approval"
        if self.paid and not any(int(self.ledger.caps.get(f"{self.name}.{c}") or 0) > 0 for c in self._endpoints()):
            return NOT_AUTHORIZED, "paid provider call caps are 0; set --provider-cap"
        return STATUS_OK, "available"

    def _endpoints(self) -> list[str]:
        return []

    def available(self) -> bool:
        return self.status()[0] == STATUS_OK

    # cached call helper ------------------------------------------------------
    def _call(self, endpoint: str, params: dict, do_request) -> object:
        key = f"{self.name}.{endpoint}"
        cache_key = key + ":" + short_hash(json.dumps(params, sort_keys=True), 24)
        if self.cache is not None:
            hit = self.cache.get(cache_key)
            if hit is not None:
                self.ledger.events.append({"at": iso(utc_now()), "provider_call": key, "paid": self.paid,
                                           "status": "cache_hit", "cost_usd": 0.0, "note": ""})
                return hit
        self.ledger.authorize(key, self.paid)
        resp: Response = do_request()
        ok = 200 <= resp.status < 300 and not resp.error
        self.ledger.record(key, self.paid, resp.status if ok else (resp.error or resp.status),
                           note="" if ok else resp.text()[:200])
        if not ok:
            raise RuntimeError(f"{key} failed: HTTP {resp.status} {resp.error or resp.text()[:200]}")
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            data = {"_text": resp.text()[:200000]}
        if self.cache is not None:
            self.cache.put(cache_key, data)
        return data

    # capability methods (override) -------------------------------------------
    def search(self, query: str, max_results: int = 10, country: str | None = None,
               language: str | None = None, news: bool = False) -> list[SearchHit]:
        raise NotImplementedError

    def map_site(self, url: str, limit: int = 500) -> list[str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Paid / keyed providers
# ---------------------------------------------------------------------------

COUNTRY_NAMES = {"CO": "colombia", "MX": "mexico", "US": "united states", "ES": "spain", "PE": "peru",
                 "CL": "chile", "AR": "argentina", "EC": "ecuador", "PA": "panama", "BR": "brazil"}


class TavilyProvider(Provider):
    name = "tavily"
    capabilities = {"search", "news", "map", "extract", "crawl"}
    paid = True
    env_key = "TAVILY_API_KEY"
    base = "https://api.tavily.com"

    def _endpoints(self):
        return ["search", "map", "extract"]

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def search(self, query, max_results=10, country=None, language=None, news=False):
        payload = {"query": query, "search_depth": "basic", "max_results": min(int(max_results), 20),
                   "topic": "news" if news else "general", "include_published_date": True}
        if country and not news and country.upper() in COUNTRY_NAMES:
            payload["country"] = COUNTRY_NAMES[country.upper()]
        data = self._call("search", payload,
                          lambda: self.fetcher.post_json(f"{self.base}/search", payload, self._headers()))
        return [SearchHit(url=r.get("url", ""), title=r.get("title", ""), snippet=r.get("content", "")[:500],
                          published=r.get("published_date"), provider=self.name, query=query, rank=i + 1)
                for i, r in enumerate(data.get("results", [])) if r.get("url")]

    def map_site(self, url, limit=500):
        payload = {"url": url, "max_depth": 2, "max_breadth": 50, "limit": int(limit), "allow_external": False}
        data = self._call("map", payload, lambda: self.fetcher.post_json(f"{self.base}/map", payload, self._headers()))
        return [u for u in data.get("results", []) if isinstance(u, str)]

    def extract(self, urls: list[str]) -> list[dict]:
        payload = {"urls": urls[:20], "extract_depth": "basic", "format": "text", "include_published_date": True}
        data = self._call("extract", payload,
                          lambda: self.fetcher.post_json(f"{self.base}/extract", payload, self._headers()))
        return list(data.get("results", [])) + [{"url": f.get("url"), "error": f.get("error")}
                                                 for f in data.get("failed_results", [])]


class FirecrawlProvider(Provider):
    name = "firecrawl"
    capabilities = {"map", "extract"}
    paid = True
    env_key = "FIRECRAWL_API_KEY"
    base = "https://api.firecrawl.dev/v2"

    def _endpoints(self):
        return ["map", "scrape"]

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def map_site(self, url, limit=500):
        payload = {"url": url, "sitemap": "include", "includeSubdomains": True, "limit": int(limit)}
        data = self._call("map", payload, lambda: self.fetcher.post_json(f"{self.base}/map", payload, self._headers()))
        links = data.get("links", [])
        return [(l.get("url") if isinstance(l, dict) else l) for l in links if l]

    def scrape(self, url: str) -> dict:
        payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
        data = self._call("scrape", payload,
                          lambda: self.fetcher.post_json(f"{self.base}/scrape", payload, self._headers()))
        return data.get("data", {})


class BraveProvider(Provider):
    name = "brave"
    capabilities = {"search", "news"}
    paid = True
    env_key = "BRAVE_SEARCH_API_KEY"

    def _endpoints(self):
        return ["search"]

    def search(self, query, max_results=10, country=None, language=None, news=False):
        params = {"q": query, "count": min(int(max_results), 20)}
        if country:
            params["country"] = country.upper()
        if language:
            params["search_lang"] = language
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
        data = self._call("search", params, lambda: self.fetcher.request(
            "GET", url, headers={"X-Subscription-Token": self.api_key or "", "Accept": "application/json"},
            check_robots=False))
        return [SearchHit(url=r.get("url", ""), title=r.get("title", ""), snippet=r.get("description", "")[:500],
                          published=r.get("page_age") or r.get("age"), provider=self.name, query=query, rank=i + 1)
                for i, r in enumerate((data.get("web") or {}).get("results", [])) if r.get("url")]


# ---------------------------------------------------------------------------
# Free providers
# ---------------------------------------------------------------------------

class _FreeGet(Provider):
    def _get(self, endpoint: str, url: str, params: dict) -> object:
        return self._call(endpoint, params, lambda: self.fetcher.request(
            "GET", url, headers={"Accept": "application/json"}, check_robots=False,
            min_interval=self.min_interval))


class GdeltProvider(_FreeGet):
    """GDELT DOC 2.0 news search (free; documented limit 1 request / 5 s)."""
    name = "gdelt"
    capabilities = {"news", "search"}

    def search(self, query, max_results=25, country=None, language=None, news=True):
        q = f'"{query}"' if " " in query and '"' not in query else query
        params = {"query": q, "mode": "artlist", "format": "json", "maxrecords": min(int(max_results), 75),
                  "sort": "datedesc", "timespan": "5y"}
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
        data = self._get("doc", url, params)
        if not isinstance(data, dict) or "articles" not in data:
            return []
        return [SearchHit(url=a.get("url", ""), title=a.get("title", ""), snippet=a.get("domain", ""),
                          published=a.get("seendate"), provider=self.name, query=query, rank=i + 1)
                for i, a in enumerate(data["articles"]) if a.get("url")]


class WaybackProvider(_FreeGet):
    name = "wayback"
    capabilities = {"historical", "archive"}

    def captures(self, domain: str, limit: int = 2000) -> list[dict]:
        params = {"url": domain, "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest",
                  "collapse": "timestamp:6", "limit": int(limit)}
        url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
        data = self._get("cdx", url, params)
        if not isinstance(data, list) or len(data) < 2:
            return []
        header = data[0]
        return [dict(zip(header, row)) for row in data[1:]]

    def site_urls(self, domain: str, limit: int = 3000) -> list[dict]:
        """Distinct historical URLs under a domain (archive-based deep-page discovery)."""
        params = {"url": f"{domain}/*", "output": "json", "fl": "timestamp,original,statuscode,mimetype",
                  "collapse": "urlkey", "limit": int(limit), "filter": "statuscode:200"}
        url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
        data = self._get("cdx_urls", url, params)
        if not isinstance(data, list) or len(data) < 2:
            return []
        return [dict(zip(data[0], row)) for row in data[1:]]


class RdapProvider(_FreeGet):
    name = "rdap"
    capabilities = {"registries", "historical"}

    def domain(self, domain: str) -> dict:
        url = f"https://rdap.org/domain/{urllib.parse.quote(domain)}"
        data = self._get("domain", url, {"domain": domain})
        events = {e.get("eventAction"): e.get("eventDate") for e in (data.get("events") or [])}
        return {"domain": domain, "registered_at": events.get("registration"),
                "expires_at": events.get("expiration"), "last_changed_at": events.get("last changed"),
                "registrar": next((ent.get("vcardArray", [None, []])[1][1][3]
                                   for ent in data.get("entities", []) if "registrar" in (ent.get("roles") or [])
                                   and ent.get("vcardArray") and len(ent["vcardArray"][1]) > 1), None),
                "status": data.get("status"), "nameservers": [n.get("ldhName") for n in data.get("nameservers", [])],
                "rdap_url": url}


class WikidataProvider(_FreeGet):
    name = "wikidata"
    capabilities = {"registries", "search"}

    def find(self, name: str, language: str = "es", limit: int = 15, country: str | None = None) -> list[dict]:
        """Label search plus full-text search scoped by country name (e.g. 'Alpina Colombia')."""
        params = {"action": "wbsearchentities", "search": name, "language": language, "format": "json",
                  "limit": limit, "type": "item"}
        url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(params)
        data = self._get("search", url, params)
        hits = data.get("search", []) if isinstance(data, dict) else []
        if country and country.upper() in COUNTRY_NAMES:
            p3 = {"action": "query", "list": "search", "srsearch": f"{name} {COUNTRY_NAMES[country.upper()]}",
                  "srlimit": 10, "format": "json"}
            ft = self._get("fulltext", "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(p3), p3)
            known = {h["id"] for h in hits}
            extra = [{"id": r["title"]} for r in ((ft or {}).get("query") or {}).get("search", [])
                     if r.get("title", "").startswith("Q") and r["title"] not in known]
            hits = extra + hits
        if not hits:
            return []
        ids = "|".join(h["id"] for h in hits[:50])
        p2 = {"action": "wbgetentities", "ids": ids, "props": "claims|labels|descriptions|sitelinks/urls",
              "languages": "es|en", "format": "json"}
        ents = self._get("entities", "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(p2), p2)
        for h in hits:
            ent = (ents.get("entities") or {}).get(h["id"], {})
            labels, descs = ent.get("labels", {}), ent.get("descriptions", {})
            h.setdefault("label", (labels.get(language) or labels.get("en") or {}).get("value"))
            h.setdefault("description", (descs.get("en") or descs.get(language) or {}).get("value"))
        out = []
        for h in hits:
            ent = (ents.get("entities") or {}).get(h["id"], {})
            claims = ent.get("claims", {})

            def val(prop):
                for c in claims.get(prop, []):
                    v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                    if v is not None:
                        return v
                return None

            inception = val("P571")
            country = val("P17")
            out.append({
                "qid": h["id"], "label": h.get("label"), "description": h.get("description"),
                "official_website": val("P856"),
                "inception": inception.get("time") if isinstance(inception, dict) else None,
                "country_qid": country.get("id") if isinstance(country, dict) else None,
                "instance_of": [((c.get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id")
                                for c in claims.get("P31", [])][:5],
                "url": f"https://www.wikidata.org/wiki/{h['id']}",
            })
        return out


class SecopProvider(_FreeGet):
    """Colombia SECOP II contracts (datos.gov.co Socrata, dataset jbjy-vk9h).

    Queries only suppliers registered with NIT (legal entities) and selects only
    non-personal fields. Bank accounts, legal-representative and supervisor
    identities are never requested.
    """
    name = "secop"
    capabilities = {"registries", "public_contracts"}
    dataset = "https://www.datos.gov.co/resource/jbjy-vk9h.json"
    FIELDS = ("nombre_entidad,proveedor_adjudicado,documento_proveedor,valor_del_contrato,fecha_de_firma,"
              "objeto_del_contrato,estado_contrato,tipo_de_contrato,modalidad_de_contratacion,urlproceso,"
              "departamento,ciudad,id_contrato")

    def contracts(self, name: str | None = None, nit: str | None = None, limit: int = 50) -> list[dict]:
        if nit:
            digits = "".join(ch for ch in nit.split("-")[0] if ch.isdigit())
            where = f"tipodocproveedor='NIT' AND documento_proveedor like '{digits}%'"
        elif name:
            safe = name.upper().replace("'", "")
            where = f"tipodocproveedor='NIT' AND upper(proveedor_adjudicado) like '%{safe}%'"
        else:
            return []
        params = {"$select": self.FIELDS, "$where": where, "$order": "fecha_de_firma DESC", "$limit": int(limit)}
        url = self.dataset + "?" + urllib.parse.urlencode(params)
        data = self._get("contracts", url, params)
        return data if isinstance(data, list) else []


class AgentSearchImport(Provider):
    """Search results produced by the operating agent's own web-search tool and
    imported with `cumbre.py ingest-search`. Not executed by the engine."""
    name = "agent_websearch"
    capabilities = {"search", "news"}


def build_providers(fetcher: Fetcher, ledger: Ledger, cache, config: dict) -> dict[str, Provider]:
    intervals = config.get("free_provider_min_interval_s", {})
    provs = [TavilyProvider(fetcher, ledger, cache), BraveProvider(fetcher, ledger, cache),
             FirecrawlProvider(fetcher, ledger, cache),
             GdeltProvider(fetcher, ledger, cache, intervals.get("gdelt", 6.0)),
             WaybackProvider(fetcher, ledger, cache, intervals.get("wayback", 1.5)),
             RdapProvider(fetcher, ledger, cache, intervals.get("rdap", 1.0)),
             WikidataProvider(fetcher, ledger, cache, intervals.get("wikidata", 1.0)),
             SecopProvider(fetcher, ledger, cache, intervals.get("secop", 1.0))]
    return {p.name: p for p in provs}


def provider_report(providers: dict[str, Provider]) -> list[dict]:
    rows = [{"provider": name, "capabilities": sorted(p.capabilities), "paid": p.paid,
             "status": p.status()[0], "detail": p.status()[1]} for name, p in providers.items()]
    rows.append({"provider": "direct_http", "capabilities": ["map", "crawl", "extract", "tech_fingerprint"],
                 "paid": False, "status": STATUS_OK, "detail": "robots-aware fetching, sitemaps, link following"})
    rows.append({"provider": AgentSearchImport.name, "capabilities": ["search", "news"], "paid": False,
                 "status": "AGENT_DRIVEN", "detail": "queries in pending_queries.json; import with ingest-search"})
    return rows


def sleep_noop(_: float) -> None:  # for tests
    time.sleep(0)
