"""Company input model and identity matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .util import fold, host_of, registrable_domain, slugify

LEGAL_SUFFIXES = r"\b(s\.?\s?a\.?\s?s\.?|s\.?\s?a\.?|ltda\.?|limitada|s\.?\s?en\s?c\.?\s?s?\.?|e\.?\s?u\.?|inc\.?|llc|corp\.?|" \
                 r"corporation|company|co\.|gmbh|s\.?\s?l\.?|sociedad anonima|sociedad por acciones simplificada)\b"
STOPWORDS = {"de", "la", "el", "los", "las", "y", "del", "the", "and", "of", "en", "e", "company", "grupo",
             "group", "empresa", "compania", "sociedad"}

COUNTRY_LANG = {"CO": ["es"], "MX": ["es"], "ES": ["es"], "PE": ["es"], "CL": ["es"], "AR": ["es"],
                "EC": ["es"], "PA": ["es"], "US": ["en"], "GB": ["en"], "BR": ["pt"]}
COUNTRY_NAMES_ES = {"CO": "Colombia", "MX": "México", "ES": "España", "PE": "Perú", "CL": "Chile",
                    "AR": "Argentina", "EC": "Ecuador", "PA": "Panamá", "US": "Estados Unidos", "BR": "Brasil"}
CO_CITIES = ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena", "Bucaramanga", "Pereira", "Manizales",
             "Cúcuta", "Ibagué", "Santa Marta", "Villavicencio", "Pasto", "Montería", "Neiva", "Armenia",
             "Popayán", "Valledupar", "Tunja", "Sincelejo", "Palmira", "Buenaventura", "Envigado", "Itagüí",
             "Rionegro", "Chía", "Soacha", "Yumbo", "Jamundí", "Sabaneta", "Bello", "Floridablanca", "Tuluá"]


def strip_legal(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(LEGAL_SUFFIXES, " ", fold(name))).strip(" .,-")


def name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", strip_legal(name)) if t not in STOPWORDS and len(t) > 1]


@dataclass
class Company:
    name: str
    country: str | None = None
    city: str | None = None
    legal_name: str | None = None
    company_id: str | None = None           # NIT or other identifier
    official_urls: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    industry_hints: list = field(default_factory=list)
    social_urls: list = field(default_factory=list)
    contact_names: list = field(default_factory=list)   # professional identities only
    product_keywords: list = field(default_factory=list)
    research_focus: list = field(default_factory=list)
    known_restrictions: list = field(default_factory=list)
    slug: str = ""

    def __post_init__(self):
        self.country = (self.country or "").upper() or None
        self.slug = self.slug or slugify(self.legal_name or self.name)
        self.official_urls = [u if u.startswith("http") else "https://" + u for u in self.official_urls if u]

    @classmethod
    def from_dict(cls, d: dict) -> "Company":
        def lst(key, *alts):
            for k in (key, *alts):
                v = d.get(k)
                if v:
                    return v if isinstance(v, list) else [v]
            return []
        legal_ids = d.get("legal_identifiers") or {}
        company_id = d.get("company_id") or d.get("nit") or (legal_ids.get("nit") if isinstance(legal_ids, dict) else None)
        return cls(name=d["name"], country=d.get("country"), city=d.get("city"), legal_name=d.get("legal_name"),
                   company_id=company_id, official_urls=lst("official_url", "domains", "official_urls"),
                   aliases=lst("aliases"), industry_hints=lst("industry_hints", "industry"),
                   social_urls=lst("social_urls"), contact_names=lst("contact_names"),
                   product_keywords=lst("product_keywords"), research_focus=lst("research_focus"),
                   known_restrictions=lst("known_restrictions"), slug=d.get("slug") or "")

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    # identity -----------------------------------------------------------------------
    @property
    def all_names(self) -> list[str]:
        names = [self.name] + ([self.legal_name] if self.legal_name else []) + list(self.aliases)
        return list(dict.fromkeys(n for n in names if n))

    @property
    def official_domains(self) -> set[str]:
        return {registrable_domain(host_of(u)) for u in self.official_urls if host_of(u)}

    @property
    def languages(self) -> list[str]:
        return COUNTRY_LANG.get(self.country or "", ["es", "en"])

    def name_match(self, text: str) -> dict:
        """How strongly does text refer to this company's name (not proof of identity)?"""
        t = fold(text)
        best = {"exact_phrase": False, "token_ratio": 0.0, "matched_name": None}
        for n in self.all_names:
            core = strip_legal(n)
            if core and re.search(r"(?<![a-z0-9])" + re.escape(core) + r"(?![a-z0-9])", t):
                return {"exact_phrase": True, "token_ratio": 1.0, "matched_name": n}
            toks = name_tokens(n)
            if toks:
                ratio = sum(1 for tok in toks if re.search(r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", t)) / len(toks)
                if ratio > best["token_ratio"]:
                    best = {"exact_phrase": False, "token_ratio": ratio, "matched_name": n}
        return best

    def location_hits(self, text: str) -> list[str]:
        t = fold(text)
        hits = []
        for loc in [self.city, COUNTRY_NAMES_ES.get(self.country or ""), self.country if self.country == "CO" else None]:
            if loc and re.search(r"(?<![a-z])" + re.escape(fold(loc)) + r"(?![a-z])", t):
                hits.append(loc)
        return hits

    def is_official_url(self, url: str) -> bool:
        return registrable_domain(host_of(url)) in self.official_domains
