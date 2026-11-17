"""Independent, explainable 0-100 heuristic scores (scoring_version in config).

These are research-assistance measures, not probabilities or trust ratings.
Relevance, reliability, freshness, novelty and opportunity are never collapsed;
`priority` exists only to order the crawl queue and is never used to drop evidence.
"""

from __future__ import annotations

import datetime as dt
import re
import zlib

from .company import Company
from .util import fold, host_of, parse_date, registrable_domain

SOURCE_TYPE_RELIABILITY = {
    "official_registry": (90, "Official registry: strong for registration facts, silent on operations"),
    "government_record": (88, "Government record: strong for contracts/filings it records"),
    "first_party_website": (78, "Company's own site: strong for what it advertises today, weaker for unverified self-claims"),
    "first_party_document": (76, "Company-published document: authoritative for its own content, may be outdated"),
    "first_party_social": (62, "Company-linked social profile: shows activity/messaging, not verified facts"),
    "reputable_news": (75, "Established news outlet: independent, may simplify or rely on company statements"),
    "news_other": (55, "Less established news/blog outlet: independent but editorial standards unknown"),
    "knowledge_base": (58, "Collaborative knowledge base: useful leads, must be confirmed elsewhere"),
    "web_archive": (72, "Internet Archive capture: reliable as to what a page showed on the capture date"),
    "domain_registry": (85, "RDAP registry data: reliable for domain events, not for business facts"),
    "job_board": (60, "Job posting: reliable for stated role requirements, not for production systems"),
    "review_platform": (45, "Customer review platform: anecdotal; small samples and selection bias"),
    "social_third_party": (38, "Third-party social content: unverified affiliation"),
    "business_aggregator": (40, "Business-data aggregator: often stale or scraped; treat as a lead"),
    "forum_community": (30, "Forum/community post: anecdotal, attribution unclear"),
    "search_snippet_only": (25, "Search-result snippet only (page not fetched)"),
    "unknown": (35, "Unknown publisher; attribution unclear"),
}

JOB_HOSTS = ("computrabajo", "elempleo", "magneto365", "indeed", "linkedin.com/jobs", "glassdoor", "bumeran",
             "trabajando", "getonbrd", "hiring.cafe", "workable", "lever.co", "greenhouse.io", "jobs.")
REVIEW_HOSTS = ("google.com/maps", "trustpilot", "tripadvisor", "reclameaqui", "quejas", "yelp", "opiniones",
                "glassdoor", "ratemyplacement", "comparably")
FORUM_HOSTS = ("reddit.com", "quora.com", "foro", "forum", "taringa", "discord")
GOV_SUFFIXES = (".gov.co", ".gov", ".gob.mx", ".gob.pe", ".gob.es", ".gob.cl", ".gob.ar", ".gob.ec", ".gob.pa")
REGISTRY_HOSTS = ("rues.org.co", "supersociedades.gov.co", "ccb.org.co", "camaramedellin", "ccc.org.co",
                  "datos.gov.co", "colombiacompra.gov.co", "secop", "sec.gov", "opencorporates.com")
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
                "tiktok.com", "github.com", "pinterest.com", "threads.net")


def classify_source_type(url: str, company: Company, config: dict, content_type: str = "") -> str:
    host = host_of(url)
    low = url.lower()
    reg = registrable_domain(host)
    if company.is_official_url(url):
        return "first_party_document" if (content_type == "application/pdf" or low.endswith(".pdf")) else "first_party_website"
    if "web.archive.org" in host:
        return "web_archive"
    if host.startswith("rdap") or "rdap" in host:
        return "domain_registry"
    if any(h in low for h in REGISTRY_HOSTS):
        return "official_registry" if "secop" not in low and "colombiacompra" not in low and "datos.gov.co" not in low \
            else "government_record"
    if host.endswith(GOV_SUFFIXES):
        return "government_record"
    if "wikipedia.org" in host or "wikidata.org" in host:
        return "knowledge_base"
    if any(h in low for h in JOB_HOSTS):
        return "job_board"
    if any(h in low for h in REVIEW_HOSTS):
        return "review_platform"
    if any(h in host for h in FORUM_HOSTS):
        return "forum_community"
    if any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS):
        return "first_party_social" if any(fold(s) in fold(url) for s in company.social_urls) else "social_third_party"
    if reg in set(config.get("aggregator_domains", [])):
        return "business_aggregator"
    if reg in set(config.get("reputable_news_domains", [])):
        return "reputable_news"
    if re.search(r"(noticia|news|blog|articulo|/20\d{2}/)", low):
        return "news_other"
    return "unknown"


FACET_WORDS = ["producto", "servicio", "cliente", "empresa", "pedido", "envio", "tienda", "catalogo", "precio",
               "distribu", "fabrica", "sede", "contacto", "garantia", "empleo", "vacante", "historia", "fundad",
               "product", "service", "customer", "order", "shipping", "store", "price", "career", "about"]


def relevance(company: Company, url: str, title: str, text: str, identity_status: str) -> tuple[int, str]:
    if identity_status == "different_entity":
        return 8, "Refers to a different entity with a similar name (homonym); retained for disambiguation only"
    blob = f"{title}\n{text[:60000]}"
    nm = company.name_match(blob)
    title_nm = company.name_match(title)
    score, why = 0, []
    if company.is_official_url(url):
        score += 55
        why.append("official domain")
    if title_nm["exact_phrase"]:
        score += 22
        why.append("name in title")
    if nm["exact_phrase"]:
        score += 18
        why.append("exact name in content")
    elif nm["token_ratio"] >= 0.5:
        score += int(10 * nm["token_ratio"])
        why.append(f"partial name match ({nm['token_ratio']:.0%} of tokens)")
    else:
        why.append("company name not found in content")
    locs = company.location_hits(blob)
    if locs:
        score += 8
        why.append("location fit: " + ", ".join(locs))
    density = sum(1 for w in FACET_WORDS if w in fold(text[:20000]))
    if density:
        score += min(12, density * 2)
        why.append(f"business-content density {density}")
    if identity_status == "uncertain":
        score = min(score, 55)
        why.append("identity uncertain (capped)")
    if not company.is_official_url(url) and not nm["exact_phrase"] and nm["token_ratio"] < 0.5:
        score = min(score, 20)
    return max(0, min(100, score)), "; ".join(why)


def reliability(source_type: str, contradictions: int = 0, anonymous: bool = False) -> tuple[int, str]:
    base, why = SOURCE_TYPE_RELIABILITY.get(source_type, SOURCE_TYPE_RELIABILITY["unknown"])
    notes = [why]
    if contradictions:
        base -= 8 * contradictions
        notes.append(f"{contradictions} contradicted claim(s)")
    if anonymous:
        base -= 10
        notes.append("no attributable author/publisher")
    return max(0, min(100, base)), "; ".join(notes)


def freshness(published_at: str | None, modified_at: str | None, run_date: dt.date,
              archive_capture_at: str | None = None) -> tuple[int | None, str]:
    """None when no real date is known. Old != useless (historical value kept elsewhere)."""
    d = parse_date(modified_at) or parse_date(published_at) or parse_date(archive_capture_at)
    if d is None:
        return None, "date unknown (retrieval date is not a publication date)"
    age = (run_date - d).days
    basis = "modified" if parse_date(modified_at) else ("published" if parse_date(published_at) else "archive capture")
    if age < 0:
        return None, f"{basis} date {d} is after the run date; treated as unknown"
    for limit, score in ((90, 100), (365, 85), (730, 65), (1825, 45)):
        if age <= limit:
            return score, f"{basis} {d.isoformat()} ({age} days before run)"
    return 25, f"{basis} {d.isoformat()} ({age // 365} years old) — historical; may still matter for history"


def shingles(text: str, n: int = 5) -> set[int]:
    words = re.findall(r"[a-z0-9]+", fold(text))
    return {zlib.crc32(" ".join(words[i:i + n]).encode()) for i in range(0, max(0, len(words) - n + 1), 2)}


def novelty(page_shingles: set[int], corpus: set[int], new_claims: int, new_signals: int) -> tuple[int, str]:
    if not page_shingles:
        base = 0.0
    else:
        base = len(page_shingles - corpus) / len(page_shingles)
    score = int(min(100, base * 70 + min(30, new_claims * 6 + new_signals * 4)))
    return score, f"{base:.0%} new text vs corpus; {new_claims} new claim(s), {new_signals} new signal(s)"


def opportunity(signals: list[dict]) -> tuple[int, str]:
    if not signals:
        return 0, "no opportunity signals detected"
    strength = sum(s.get("strength", 1) for s in signals)
    funcs = sorted({s["business_function"] for s in signals})
    return min(100, 15 + strength * 9), f"{len(signals)} signal(s) across: {', '.join(funcs)[:160]}"


def priority(scores: dict, weights: dict) -> float:
    total = 0.0
    for key, w in weights.items():
        if key.startswith("_"):
            continue
        v = scores.get(key)
        total += float(w) * (50.0 if v is None else float(v))
    return round(total, 2)


def confidence_band(evidence_strength: int, n_sources: int, contradicted: bool) -> str:
    if contradicted:
        return "low"
    if evidence_strength >= 80 and n_sources >= 2:
        return "high"
    if evidence_strength >= 70 or (evidence_strength >= 55 and n_sources >= 2):
        return "medium"
    if evidence_strength > 0:
        return "low"
    return "unknown"
