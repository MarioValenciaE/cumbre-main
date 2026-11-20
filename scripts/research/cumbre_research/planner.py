"""Research plan: entity candidates, expandable query universe, gap-driven waves."""

from __future__ import annotations

import re

from .company import COUNTRY_NAMES_ES, LEGAL_SUFFIXES, Company, name_tokens, strip_legal
from .util import fold, host_of, registrable_domain, slugify

FACETS = {
    "identity_legal": {"es": ["{n} NIT", "{n} razón social", "{n} S.A.S.", "{n} cámara de comercio", "\"{n}\" empresa"],
                       "en": ["{n} company legal name", "\"{n}\" company"]},
    "products_services": {"es": ["{n} productos", "{n} servicios", "{n} catálogo", "{n} precios", "{n} tienda en línea"],
                          "en": ["{n} products", "{n} services", "{n} pricing"]},
    "history_milestones": {"es": ["{n} historia", "{n} fundada en", "{n} fundador", "{n} aniversario"],
                           "en": ["{n} history founded"]},
    "customers_market": {"es": ["{n} clientes", "{n} distribuidores", "{n} competidores", "{n} sector"],
                         "en": ["{n} customers", "{n} competitors"]},
    "locations_expansion": {"es": ["{n} sedes", "{n} nueva sede", "{n} apertura", "{n} expansión"],
                            "en": ["{n} locations", "{n} expansion"]},
    "technology_digital": {"es": ["{n} portal clientes", "{n} plataforma", "{n} app", "{n} transformación digital"],
                           "en": ["{n} platform", "{n} app", "{n} technology"]},
    "hiring": {"es": ["{n} empleo", "{n} vacantes", "{n} trabaja con nosotros", "{n} computrabajo"],
               "en": ["{n} jobs", "{n} careers"]},
    "public_records": {"es": ["{n} SECOP contrato", "{n} licitación", "{n} Supersociedades", "{n} estados financieros"],
                       "en": ["{n} annual report", "{n} filing"]},
    "reputation_reviews": {"es": ["{n} opiniones", "{n} reseñas", "{n} quejas", "{n} servicio al cliente"],
                           "en": ["{n} reviews", "{n} complaints"]},
    "news_media": {"es": ["{n} noticias", "{n} entrevista", "{n} gerente", "{n} 2026", "{n} 2025"],
                   "en": ["{n} news", "{n} interview"]},
    "social_community": {"es": ["{n} instagram", "{n} facebook", "{n} linkedin", "{n} youtube", "{n} tiktok"],
                         "en": ["{n} linkedin", "{n} github", "{n} reddit"]},
    "ownership_funding": {"es": ["{n} inversión", "{n} adquisición", "{n} grupo empresarial", "{n} accionistas"],
                          "en": ["{n} funding round", "{n} acquired", "{n} investors"]},
    "risk_incidents": {"es": ["{n} ciberataque", "{n} filtración de datos", "{n} sanción SIC", "{n} caída plataforma"],
                       "en": ["{n} data breach", "{n} security incident", "{n} outage"]},
    "history_archive": {"es": ["{n} antiguo sitio web", "{n} cambio de nombre"], "en": ["{n} formerly known as"]},
}
CORE_FACETS = ["identity_legal", "products_services", "history_milestones", "customers_market", "technology_digital",
               "hiring", "news_media", "social_community", "reputation_reviews", "risk_incidents"]


def query_variants(company: Company) -> list[str]:
    names = [company.name]
    if company.legal_name and fold(company.legal_name) != fold(company.name):
        names.append(company.legal_name)
    names += [a for a in company.aliases if fold(a) not in {fold(n) for n in names}]
    return names[:4]


def ambiguous_name(company: Company) -> bool:
    """Short or single-word names (e.g. "Brisa") return worldwide noise unless the query adds a locality."""
    core = strip_legal(company.name)
    return len(name_tokens(company.name)) <= 1 or len(core) <= 8


def localize(company: Company, query: str, lang: str | None) -> str:
    """Append the city (else country) to a query when the name alone is ambiguous (any language)."""
    if not ambiguous_name(company):
        return query
    place = company.city or COUNTRY_NAMES_ES.get(company.country or "")
    if not place or fold(place) in fold(query) or "site:" in query:
        return query
    return f"{query} {place}"


def initial_queries(company: Company, depth: str, max_queries: int) -> list[dict]:
    """Wave-1 matrix: facet x language x name variant (+ locality)."""
    out = []
    names = query_variants(company)
    loc = " ".join(x for x in [company.city, COUNTRY_NAMES_ES.get(company.country or "")] if x)
    facets = list(FACETS) if depth != "standard" else CORE_FACETS
    langs = company.languages if depth == "standard" else list(dict.fromkeys(company.languages + ["en"]))
    base_name = names[0]
    out.append(_q(f"\"{base_name}\" {loc}".strip(), "identity_legal", 1, "seed"))
    for d in company.official_domains:
        out.append(_q(f"site:{d}", "website_structure", 1, "seed"))
        out.append(_q(f"\"{d}\" -site:{d}", "external_mentions", 1, "seed"))
    for facet in facets:
        for lang in langs:
            templates = FACETS[facet].get(lang, [])
            limit = {"standard": 2, "deep": 3, "exhaustive": 10}[depth]
            for t in templates[:limit]:
                q = t.format(n=base_name)
                if lang == "es" and loc and facet in ("identity_legal", "locations_expansion", "reputation_reviews"):
                    q = f"{q} {company.city or ''}".strip()
                q = localize(company, q, lang)
                out.append(_q(q, facet, 1, "matrix", lang=lang, news=facet in ("news_media", "risk_incidents")))
    for alt in names[1:]:
        for facet in ("identity_legal", "news_media", "products_services"):
            out.append(_q(f"\"{alt}\"", facet, 1, "alias"))
    for kw in company.product_keywords[:6]:
        out.append(_q(f"{base_name} {kw}", "products_services", 1, "product_keyword"))
    for focus in company.research_focus[:6]:
        out.append(_q(f"{base_name} {focus}", "targeted:" + focus, 1, "research_focus"))
    seen, uniq = set(), []
    for q in out:
        if q["query"].lower() not in seen:
            seen.add(q["query"].lower())
            uniq.append(q)
    return uniq[:max_queries]


def _q(query: str, facet: str, wave: int, origin: str, lang: str | None = None, news: bool = False) -> dict:
    return {"query": query, "facet": facet, "wave": wave, "origin": origin, "language": lang, "news": news,
            "provider_hint": "search", "status": "planned"}


def gap_queries(company: Company, wave: int, facet_counts: dict, leads: list, executed: set[str],
                budget_left: int) -> list[dict]:
    """Phase G: target thin facets, new aliases/domains, and alternate wording."""
    out = []
    name = company.name
    thin = sorted((f for f in FACETS if facet_counts.get(f, 0) < 2), key=lambda f: facet_counts.get(f, 0))
    for facet in thin:
        for lang, templates in FACETS[facet].items():
            for t in templates:
                q = localize(company, t.format(n=name), lang)
                if q.lower() not in executed:
                    out.append(_q(q, facet, wave, "gap:thin_facet", lang=lang, news=facet in ("news_media", "risk_incidents")))
                    break
    for kind, value in leads:
        if kind == "alias":
            out.append(_q(f"\"{value}\"", "identity_legal", wave, "lead:alias"))
            out.append(_q(f"\"{value}\" noticias", "news_media", wave, "lead:alias"))
        elif kind == "domain":
            out.append(_q(f"\"{value}\"", "external_mentions", wave, "lead:domain"))
        elif kind == "brand":
            out.append(_q(f"\"{value}\" {name}", "products_services", wave, "lead:brand"))
    out.append(_q(localize(company, f"\"{name}\" filetype:pdf", "es"), "resources_documents", wave, "gap:documents"))
    uniq, seen = [], set(executed)
    for q in out:
        if q["query"].lower() not in seen:
            seen.add(q["query"].lower())
            uniq.append(q)
    return uniq[:max(0, budget_left)]


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

def candidate_entities(company: Company, hits: list, wikidata: list[dict],
                       supplied_urls: list[str] | None = None) -> list[dict]:
    """Build a candidate table from search hits / Wikidata. Never merges candidates.
    Only URLs the user supplied count as an official_url hint (not ones the engine inferred)."""
    cands: dict[str, dict] = {}
    supplied = company.official_urls if supplied_urls is None else supplied_urls
    supplied_domains = {registrable_domain(host_of(u if u.startswith("http") else "https://" + u)) for u in supplied}
    for d in sorted(supplied_domains):
        cands[d] = {"candidate": d, "label": company.name, "basis": ["supplied official_url"],
                    "evidence_urls": list(supplied), "identity_match_confidence": 0.95,
                    "hint_matches": ["official_url"]}
    for w in wikidata:
        desc_raw = fold(w.get("description") or "")
        if any(x in desc_raw for x in NON_ORG_WORDS):
            continue  # a genus, surname, person or place is not a company candidate
        if not w.get("official_website") and not any(x in desc_raw for x in ORG_WORDS):
            continue  # no sign this item is an organization (e.g. a ship, a car model, a breed)
        site = w.get("official_website")
        key = registrable_domain(host_of(site)) if site else f"wikidata:{w['qid']}"
        desc = fold((w.get("description") or "") + " " + (w.get("label") or ""))
        nm = company.name_match(w.get("label") or "")
        if not nm["exact_phrase"] and nm["token_ratio"] < 0.6:
            continue
        conf = 0.35 + (0.2 if nm["exact_phrase"] else 0) + (0.15 if company.country and fold(COUNTRY_NAMES_ES.get(company.country, "")) in desc else 0)
        if any(fold(h) in desc for h in company.industry_hints):
            conf += 0.1
        want = COUNTRY_QIDS.get(company.country or "")
        if want and w.get("country_qid") == want:
            conf += 0.25
        elif want and w.get("country_qid"):
            conf -= 0.2
        if site and registrable_domain(host_of(site)) in supplied_domains:
            conf = 0.97
        c = cands.setdefault(key, {"candidate": key, "label": w.get("label"), "basis": [], "evidence_urls": [],
                                   "identity_match_confidence": 0.0, "hint_matches": []})
        c["basis"].append(f"wikidata {w['qid']}: {w.get('description') or 'no description'}")
        c["evidence_urls"].append(w["url"])
        c["identity_match_confidence"] = max(c["identity_match_confidence"], round(min(conf, 0.97), 2))
        c["wikidata"] = w
    for h in hits:
        host = registrable_domain(host_of(h.url))
        if host and host.split(".")[0] in PROFILE_SITE_LABELS:
            _profile_candidate(company, h, host, cands)
            continue
        if not host or host in _NON_COMPANY_HOSTS or any(host.endswith(s) for s in (".gov.co", ".gov")):
            continue
        blob = f"{h.title} {h.snippet}"
        nm = company.name_match(blob)
        dom_nm = company.name_match(host.replace("-", " ").replace(".", " "))
        if dom_nm["token_ratio"] < 0.6:
            continue  # a page that merely mentions the name is a mention, not a candidate entity
        conf = 0.2 + (0.2 if nm["exact_phrase"] else 0.05) + (0.2 if dom_nm["token_ratio"] >= 0.6 else 0)
        locs = company.location_hits(blob)
        if locs:
            conf += 0.15
        if any(fold(x) in fold(blob) for x in company.industry_hints + company.product_keywords):
            conf += 0.1
        if host.endswith((".co", ".com.co")) and company.country == "CO":
            conf += 0.05
        c = cands.setdefault(host, {"candidate": host, "label": h.title[:80], "basis": [], "evidence_urls": [],
                                    "identity_match_confidence": 0.0, "hint_matches": []})
        c["basis"].append(f"search hit rank {h.rank} for '{h.query[:50]}'")
        if len(c["evidence_urls"]) < 5:
            c["evidence_urls"].append(h.url)
        c["hint_matches"] = sorted(set(c["hint_matches"]) | set(locs))
        c["identity_match_confidence"] = max(c["identity_match_confidence"], round(min(conf, 0.9), 2))
    return sorted(cands.values(), key=lambda c: -c["identity_match_confidence"])


# Company-directory / registry sites: a profile there is evidence an entity exists, not an official website.
PROFILE_SITE_LABELS = {"emis", "einforma", "informacolombia", "empresite", "colombiaempresas", "guiaempresas",
                       "datacreditoempresas", "rues", "dnb", "cylex", "zoominfo", "rocketreach", "crunchbase",
                       "opencorporates", "infoempresas", "empresascolombia", "directorioempresas"}


def _profile_candidate(company: Company, h, host: str, cands: dict) -> None:
    """A directory profile whose title carries the exact company name becomes a weak, website-less candidate."""
    title = (h.title or "").strip()
    head = re.split(r"\s[-|–·]\s|company profile|perfil de", title, maxsplit=1, flags=re.I)[0].strip()
    nm = company.name_match(head)
    if not nm["exact_phrase"] or strip_legal(head) != strip_legal(company.legal_name or company.name):
        return  # Brisas / Brisol are different entities from Brisa: never merged
    blob = f"{title} {h.snippet}"
    conf = 0.3 + (0.05 if re.search(LEGAL_SUFFIXES, fold(head)) else 0)
    locs = company.location_hits(blob)
    if locs:
        conf += 0.1
    country = COUNTRY_NAMES_ES.get(company.country or "")
    country_en = {"CO": "colombia", "MX": "mexico", "PE": "peru", "EC": "ecuador", "CL": "chile"}.get(company.country or "")
    path = fold(h.url)
    if (country and fold(country) in fold(blob)) or (company.country and f"/{company.country.lower()}/" in path)             or (country_en and country_en in fold(blob)):
        conf += 0.05
    key = "profile:" + slugify(head)
    c = cands.setdefault(key, {"candidate": key, "label": head[:80], "basis": [], "evidence_urls": [],
                               "identity_match_confidence": 0.0, "hint_matches": [], "website": None})
    c["basis"].append(f"directory profile on {host} (no official website found from it)")
    if h.url not in c["evidence_urls"] and len(c["evidence_urls"]) < 5:
        c["evidence_urls"].append(h.url)
    c["hint_matches"] = sorted(set(c["hint_matches"]) | set(locs))
    c["identity_match_confidence"] = max(c["identity_match_confidence"], round(min(conf, 0.5), 2))


ORG_WORDS = ("company", "empresa", "manufacturer", "fabricante", "brand", "marca", "business", "corporation",
             "enterprise", "retailer", "producer", "productor", "organization", "organizacion", "cooperative",
             "cooperativa", "group", "grupo", "firm", "distributor", "distribuidor", "chain", "cadena", "startup")
COUNTRY_QIDS = {"CO": "Q739", "MX": "Q96", "US": "Q30", "ES": "Q29", "PE": "Q419", "CL": "Q298", "AR": "Q414",
                "EC": "Q736", "PA": "Q804", "BR": "Q155", "DE": "Q183", "GB": "Q145"}
NON_ORG_WORDS = ("genus", "species", "family name", "given name", "surname", "researcher", "scientist", "human",
                 "footballer", "singer", "actor", "actress", "politician", "village", "municipality", "river",
                 "mountain", "asteroid", "album", "song", "film", "painting", "scholarly article", "wikimedia",
                 "genero", "especie", "apellido", "nombre de pila", "investigador", "cantante", "pelicula")

_NON_COMPANY_HOSTS = {"facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
                      "tiktok.com", "wikipedia.org", "wikidata.org", "google.com", "computrabajo.com", "elempleo.com",
                      "indeed.com", "glassdoor.com", "web.archive.org", "archive.org", "reddit.com", "github.com",
                      "mercadolibre.com.co", "amazon.com", "pinterest.com", "rues.org.co", "einforma.co",
                      "informacolombia.com", "emis.com", "dnb.com", "crunchbase.com", "datos.gov.co",
                      "eltiempo.com", "semana.com", "larepublica.co", "portafolio.co", "elespectador.com"}


CONFIRMED_IDENTITY = ("confirmed_by_hint", "confirmed_by_founder", "dominant")
UNCERTAIN_IDENTITY = ("ambiguous", "provisional", "tentative", "unresolved")


def decide_identity(candidates: list[dict], margin: float = 0.15) -> dict:
    """Pick a provisional identity only if it clearly dominates; otherwise flag."""
    if not candidates:
        return {"status": "unresolved", "chosen": None, "alternatives": [],
                "rationale": "No candidate with a name match was found; supply --official-url or more hints."}
    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    margin_ok = second is None or top["identity_match_confidence"] - second["identity_match_confidence"] >= margin
    if "official_url" in top["hint_matches"]:
        return {"status": "confirmed_by_hint", "chosen": top["candidate"],
                "alternatives": [c["candidate"] for c in candidates[1:6]],
                "rationale": "Official URL supplied by the user."}
    if "nit" in top["hint_matches"] and top["candidate"].startswith("nit:"):
        return {"status": "confirmed_by_hint", "chosen": top["candidate"],
                "alternatives": [c["candidate"] for c in candidates[1:6]],
                "rationale": "The NIT supplied matches a RUES registry record."}
    if margin_ok and top["candidate"].startswith("profile:"):
        return {"status": "tentative", "chosen": top["candidate"],
                "alternatives": [c["candidate"] for c in candidates[1:6]],
                "rationale": "Only a company-directory profile matches the exact name (no official website or "
                             "registry record found). Treated as a tentative identity; confirm it with the company "
                             "or RUES and record it with `confirm-identity`."}
    if top["identity_match_confidence"] >= 0.9 and margin_ok:
        return {"status": "dominant", "chosen": top["candidate"],
                "alternatives": [c["candidate"] for c in candidates[1:6]],
                "rationale": "Knowledge-base/website evidence clearly dominates the alternatives."}
    if second is None or top["identity_match_confidence"] - second["identity_match_confidence"] >= margin:
        if top["identity_match_confidence"] >= 0.5:
            return {"status": "provisional", "chosen": top["candidate"],
                    "alternatives": [c["candidate"] for c in candidates[1:6]],
                    "rationale": f"Top candidate leads by ≥{margin:.2f}; continuing provisionally (labelled assumption)."}
    return {"status": "ambiguous", "chosen": top["candidate"] if top["identity_match_confidence"] >= 0.7 else None,
            "alternatives": [c["candidate"] for c in candidates[:6]],
            "rationale": "Several plausible entities with similar confidence (possibly related entities, e.g. a company "
                         "and its foundation). The top candidate is researched provisionally; alternatives are kept "
                         "separate for identity only. Confirm the entity before relying on the dossier."}
