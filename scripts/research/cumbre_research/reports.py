"""Markdown/JSON renderers. Two views of one evidence graph: a five-minute brief
and an unrestricted dossier + catalogs. Every factual line cites registry IDs
with a clickable URL. Reports paraphrase; they never dump page content."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .planner import CONFIRMED_IDENTITY, UNCERTAIN_IDENTITY
from .safety import safe_excerpt
from .util import host_of, iso, parse_date, write_json

DISCOVERY_CONSUMERS = {
    "sales / quoting": "02_process_maps.md, 04_problem_register.md",
    "sales channels / ordering": "02_process_maps.md, 03_systems_and_data.md",
    "catalog / product information": "03_systems_and_data.md",
    "B2B / distribution": "01_business_profile.md, 02_process_maps.md",
    "operations / multi-site": "01_business_profile.md, 03_systems_and_data.md",
    "inventory": "03_systems_and_data.md, 04_problem_register.md",
    "payments / collections": "02_process_maps.md, 04_problem_register.md",
    "customer support / returns": "02_process_maps.md, 04_problem_register.md",
    "reporting / data": "03_systems_and_data.md, 05_opportunity_backlog.md",
    "growth / change": "01_business_profile.md",
    "hiring / capability": "01_business_profile.md, 09_internal_decision.md",
    "compliance / regulatory": "03_systems_and_data.md, 08_validation_requests.md",
}
NO_HIT = ("No relevant public incident reports were identified in the specific sources checked as of {d}; "
          "this does not establish that the company is secure or incident-free.")


class Ctx:
    def __init__(self, run_dir: Path, run):
        self.run_dir = run_dir
        self.run = run
        self.company = run.company
        st = run.store
        self.sources = {s["source_id"]: s for s in st.sources()}
        self.claims = st.claims()
        self.claims_by_id = {c["claim_id"]: c for c in self.claims}
        self.signals = st.signals()
        self.obs = st.observations()
        self.incidents = st.incidents()
        self.edges = st.edges()
        self.hyps = json.loads((run_dir / "data" / "opportunity_hypotheses.json").read_text(encoding="utf-8"))
        self.extra = run.registry_extra
        self.identity = run.identity or {}
        self.run_date = run.run_date
        self.generated = iso(run.clock())
        self.cited: set[str] = set()

    # helpers ---------------------------------------------------------------
    def cite(self, *sids: str, limit: int = 4) -> str:
        out = []
        for sid in list(dict.fromkeys(s for s in sids if s))[:limit]:
            s = self.sources.get(sid)
            if s:
                self.cited.add(sid)
                out.append(f"[{sid}]({s.get('final_url') or s['url']})")
        return " ".join(out) if out else "UNKNOWN (no source)"

    def ccite(self, claim: dict, limit: int = 3) -> str:
        return f"`{claim['claim_id']}` " + self.cite(*claim.get("supporting_source_ids", []), limit=limit)

    def by_type(self, *types: str) -> list[dict]:
        return [c for c in self.claims if c["claim_type"] in types]

    def obs_kind(self, kind: str) -> list[dict]:
        return [o for o in self.obs if o.get("kind") == kind]

    def processed(self) -> list[dict]:
        return [s for s in self.sources.values() if s.get("processing_status", "").startswith("extracted")
                or s.get("processing_status") == "carried_forward_not_stale"]

    def date_of(self, sid: str) -> str:
        s = self.sources.get(sid, {})
        if s.get("published_at"):
            return f"published {str(s['published_at'])[:10]}"
        if s.get("archive_capture_at"):
            return f"archived {str(s['archive_capture_at'])[:8]}"
        return "undated"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_None found in the sources checked._\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("\n", " ").replace("|", "/") for c in r) + " |")
    return "\n".join(out) + "\n"


IDENTITY_TOPICS = ("Legal name", "NIT / tax ID")


def coverage_checks(ctx: Ctx) -> list[tuple[str, bool]]:
    """Topic checklist shared by the gaps report and the overall confidence level."""
    return [
        ("Legal name", bool(ctx.by_type("legal_name_variant"))),
        ("NIT / tax ID", bool(ctx.by_type("tax_id_nit"))),
        ("Founding date", bool(ctx.by_type("founding_year")) or bool((ctx.extra.get("rues") or {}).get("fecha_matricula"))),
        ("Leadership roles", bool(ctx.by_type("leadership_role"))),
        ("Ownership classification", bool(ctx.by_type("ownership_indicator"))),
        ("Company size", bool(ctx.by_type("employee_count_statement"))),
        ("Products/services", bool(ctx.by_type("offering_or_topic"))),
        ("News coverage", bool(ctx.by_type("news_mention"))),
        ("Hiring signals", any(s["signal_type"] in ("tech_hiring", "manual_admin_hiring") for s in ctx.signals)),
        ("Reviews / complaints", any(s["signal_type"] == "customer_complaints" for s in ctx.signals)),
        ("Public contracts", bool(ctx.by_type("public_contract"))),
        ("Social profiles", bool(ctx.obs_kind("social_profile"))),
        ("Competitors", False),
    ]


def overall_confidence(ctx: Ctx) -> tuple[str, str]:
    """Evidence level = identity certainty AND source volume AND profile coverage.
    A confirmed identity with an empty profile must not read as HIGH."""
    processed = ctx.processed()
    domains = {host_of(s["url"]) for s in processed}
    confirmed = sum(1 for c in ctx.claims if c.get("fact_status") == "confirmed")
    ident = ctx.identity.get("status")
    profile = [ok for t, ok in coverage_checks(ctx) if t not in IDENTITY_TOPICS]
    found, total = sum(profile), len(profile)
    cov = found / total if total else 0.0
    if ident in CONFIRMED_IDENTITY and len(processed) >= 30 and len(domains) >= 5 and confirmed >= 5 and cov >= 0.5:
        level = "HIGH"
    elif ident in CONFIRMED_IDENTITY + ("provisional",) and len(processed) >= 10 and cov >= 0.25:
        level = "MEDIUM"
    elif len(processed) >= 3:
        level = "LOW"
    else:
        level = "INSUFFICIENT"
    why = (f"Coverage-and-evidence quality, not a probability of legitimacy: identity {ident}; "
           f"profile coverage {found}/{total} topics (see research_gaps.md); "
           f"{len(processed)} processed sources across {len(domains)} hosts; {confirmed} confirmed claims.")
    return level, why


# ---------------------------------------------------------------------------
# Section builders (shared by facet files and the full dossier)
# ---------------------------------------------------------------------------

def sec_identity(ctx: Ctx) -> str:
    c = ctx.company
    lines = ["## Identity and legal footprint\n",
             f"- Query: **{c.name}**; country: {c.country or 'not given'}; city: {c.city or 'not given'}; "
             f"supplied identifiers: {c.company_id or 'none'}; supplied official URL(s): "
             f"{', '.join(ctx.identity.get('supplied_official_urls') or []) or 'none'}; official domain(s) used: "
             f"{', '.join(sorted(c.official_domains)) or 'none'}"
             + (f"; historical/unreachable: {', '.join(ctx.identity['historical_domains'])}" if ctx.identity.get('historical_domains') else "") + ".",
             f"- Identity decision: **{ctx.identity.get('status', 'unknown')}** — {ctx.identity.get('rationale', '')}"]
    if ctx.identity.get("assumption"):
        lines.append(f"- **Assumption:** {ctx.identity['assumption']}")
    lines.append("\n### Candidate entities (never merged)\n")
    rows = [[k["candidate"], safe_excerpt(k.get("label") or "", 60), k["identity_match_confidence"],
             "; ".join(k["basis"])[:160], " ".join(f"<{u}>" for u in k["evidence_urls"][:2])]
            for k in ctx.run.candidates[:12]]
    lines.append(md_table(["Candidate", "Label", "identity_match_confidence", "Basis", "Evidence"], rows))
    lines.append("### Legal names, identifiers and registrations\n")
    rows = [[c2["claim_type"], safe_excerpt(str(c2["value"]), 80), c2.get("fact_status"), c2.get("confidence_band"),
             ctx.ccite(c2)] for c2 in ctx.by_type("legal_name_variant", "tax_id_nit", "certification_or_registration",
                                                 "domain_registered")]
    lines.append(md_table(["Type", "Value", "fact_status", "Confidence", "Evidence"], rows))
    contra = ctx.extra.get("contradictions") or []
    if contra:
        lines.append("### Contradictions\n")
        for x in contra:
            lines.append(f"- {x['claim_type']}: " + "; ".join(
                f"{ctx.claims_by_id[i]['value']} ({ctx.ccite(ctx.claims_by_id[i], 2)})" for i in x["claim_ids"]
                if i in ctx.claims_by_id) + " — may reflect different entities or periods; unresolved.")
    alts = [s for s in ctx.sources.values() if s.get("identity_match_status") in ("candidate_alternative", "different_entity")]
    if alts:
        lines.append("\n### Similar-name entities kept separate\n")
        lines.append(md_table(["Source", "Status", "Title"], [[ctx.cite(s["source_id"]), s["identity_match_status"],
                                                                safe_excerpt(s.get("title") or "", 80)] for s in alts[:30]]))
    return "\n".join(lines) + "\n"


def ranked_offers(ctx: Ctx) -> list[dict]:
    """Home/category headings first; individual product pages last (catalog items, not offerings)."""
    order = {"home": 0, "product_service": 1, "commerce": 2, "company_about": 3}
    return sorted(ctx.by_type("offering_or_topic"),
                  key=lambda c: (order.get(c.get("page_category"), 4), len(c.get("supporting_source_ids", [])) * -1,
                                 c["claim_id"]))


def timeline_rows(ctx: Ctx) -> list[tuple[str, list[str]]]:
    rows = []
    for cl in ctx.by_type("founding_year", "domain_registered", "earliest_archive_capture", "public_contract",
                          "mna_report", "funding_report", "news_mention", "years_in_business_claim"):
        ev = cl.get("event_date")
        if cl["claim_type"] == "news_mention":
            sid = cl["supporting_source_ids"][0]
            when = ctx.date_of(sid)
            key = str(ctx.sources.get(sid, {}).get("published_at") or "9999")[:10]
            desc = f"News coverage: {safe_excerpt(str(cl['value']), 90)} (event date: read article)"
        elif cl["claim_type"] == "years_in_business_claim":
            when, key = "undated self-claim", "9998"
            desc = cl["statement"]
        else:
            when = str(ev)[:10] if ev else "undated"
            key = str(ev or "9997")[:10]
            desc = cl["statement"]
        rows.append((key, [when, safe_excerpt(desc, 180), cl.get("confidence_band") or cl.get("fact_status"),
                           ctx.ccite(cl, 2)]))
    rows.sort(key=lambda r: r[0])
    return rows


def sec_history(ctx: Ctx) -> str:
    out = ["## Business history and timeline\n",
           "Dates are labelled by type. Domain registration and archive captures are not founding dates; "
           "retrieval dates are never used as publication dates.\n"]
    out.append(md_table(["Date (type)", "Event or change", "Confidence", "Source"], [r for _, r in timeline_rows(ctx)]))
    wb = ctx.extra.get("wayback") or {}
    for dom, w in wb.items():
        years = ", ".join(f"{y}: {n}" for y, n in sorted(w["captures_by_year"].items()))
        out.append(f"- Internet Archive monthly-collapsed captures for {dom}: first {w['first_capture'][:8]}, "
                   f"last {w['last_capture'][:8]}; by year — {years}. {ctx.cite(w['source_id'])}")
    return "\n".join(out) + "\n"


def sec_products(ctx: Ctx) -> str:
    offers = ctx.by_type("offering_or_topic")
    out = ["## Products, services and channels\n",
           "Official page headings (self-reported; categories, not a verified catalog):\n"]
    out.append(md_table(["Offering/topic", "Claim", "Evidence"],
                        [[safe_excerpt(str(o["value"]), 80), o["claim_id"], ctx.cite(*o["supporting_source_ids"], limit=2)]
                         for o in offers[:120]]))
    chan = [s for s in ctx.signals if s["signal_type"] in ("ecommerce_present", "whatsapp_ordering", "email_ordering",
                                                           "quote_request", "b2b_portal", "wholesale_distribution", "pdf_catalog")]
    out.append("### Channel indicators\n")
    out.append(md_table(["Signal", "Observed", "Evidence"],
                        [[s["signal_id"] + " " + s["signal_type"], s["observed_statement"][:140], ctx.cite(*s["source_ids"])]
                         for s in chan[:60]]))
    cats: dict[str, int] = {}
    for s in ctx.sources.values():
        if ctx.company.is_official_url(s["url"]):
            cats[s.get("url_category") or "unknown"] = cats.get(s.get("url_category") or "unknown", 0) + 1
    out.append("### Official site structure by category (discovered URLs)\n")
    out.append(md_table(["Category", "URLs discovered"], [[k, v] for k, v in sorted(cats.items(), key=lambda x: -x[1])]))
    return "\n".join(out) + "\n"


def sec_business_model(ctx: Ctx) -> str:
    out = ["## Customers, business model and operations\n",
           "Inferences are labelled; no revenue or volumes are invented.\n"]
    rows = []
    for c in ctx.by_type("site_count_statement", "employee_count_statement", "location_mention", "years_in_business_claim"):
        note = " (public statement, not payroll)" if c["claim_type"] == "employee_count_statement" else ""
        rows.append([c["claim_type"], safe_excerpt(str(c["value"]), 70) + note, c.get("fact_status"), ctx.ccite(c, 2)])
    out.append(md_table(["Indicator", "Value", "fact_status", "Evidence"], rows))
    funcs: dict[str, list] = {}
    for s in ctx.signals:
        funcs.setdefault(s["business_function"], []).append(s)
    out.append("### Operational clues by business function (signals → questions, not conclusions)\n")
    out.append(md_table(["Business function", "Signals", "Example evidence"],
                        [[f, len(v), ctx.cite(*{x for s in v for x in s["source_ids"]}, limit=3)]
                         for f, v in sorted(funcs.items(), key=lambda x: -len(x[1]))]))
    return "\n".join(out) + "\n"


def sec_people(ctx: Ctx) -> str:
    out = ["## Leadership, hiring and partners\n",
           "Only public professional roles are recorded; no personal contact details.\n"]
    out.append(md_table(["Person — role", "Status", "Evidence"],
                        [[safe_excerpt(str(c["value"]), 80), c.get("fact_status"), ctx.ccite(c, 2)]
                         for c in ctx.by_type("leadership_role")]))
    hiring = [s for s in ctx.signals if s["signal_type"] in ("tech_hiring", "manual_admin_hiring", "excel_tools")]
    out.append("### Hiring signals\n")
    out.append(md_table(["Signal", "Observed", "Evidence"],
                        [[s["signal_id"], s["observed_statement"][:150], ctx.cite(*s["source_ids"])] for s in hiring[:50]]))
    out.append("### Relationship graph (evidence-based edges; never inferred from name similarity)\n")
    out.append(md_table(["Edge", "From", "Type", "To", "Confidence", "Evidence"],
                        [[e["edge_id"], e["from"], e["edge_type"], e["to"], e["confidence"], ctx.cite(*e["source_ids"])]
                         for e in ctx.edges[:80]]))
    return "\n".join(out) + "\n"


def sec_ownership(ctx: Ctx) -> str:
    out = ["## Ownership, funding and recent events\n"]
    own = ctx.by_type("ownership_indicator")
    if own:
        out.append("Ownership indicators (classification only when corroborated):\n")
        out.append(md_table(["Indicator", "Observed", "Source type", "Evidence"],
                            [[c["value"], safe_excerpt(c.get("observed_statement") or "", 140),
                              ctx.sources.get(c["supporting_source_ids"][0], {}).get("source_type"), ctx.ccite(c, 2)]
                             for c in own]))
        out.append("**ownership_type:** UNKNOWN unless two independent credible sources agree "
                   "(see structured_profile.json → ownership_evidence).\n")
    else:
        out.append("- ownership_type: **UNKNOWN** — no ownership statement found in the sources checked.\n")
    fund = ctx.by_type("funding_report", "mna_report")
    out.append("### Funding, investment and M&A reports\n")
    out.append(md_table(["Report", "Source type", "Note", "Evidence"],
                        [[safe_excerpt(str(c["value"]), 120), ctx.sources.get(c["supporting_source_ids"][0], {}).get("source_type"),
                          "news estimate ≠ audited data" if "news" in str(ctx.sources.get(c["supporting_source_ids"][0], {}).get("source_type"))
                          else ("aggregator assertion — unverified" if ctx.sources.get(c["supporting_source_ids"][0], {}).get("source_type") == "business_aggregator" else ""),
                          ctx.ccite(c, 2)] for c in fund]))
    out.append("### Company size\n")
    out.append(md_table(["Statement", "Caveat", "Evidence"],
                        [[safe_excerpt(str(c["value"]), 80), "Public statement or estimate; LinkedIn/aggregator counts are not payroll.",
                          ctx.ccite(c, 2)] for c in ctx.by_type("employee_count_statement")]))
    out.append(f"### Last 12 months (relative to run date {ctx.run_date})\n")
    rows, undated = last_12_months(ctx)
    out.append(md_table(["Date", "Event / coverage", "Evidence"], rows))
    if undated:
        out.append(f"- {len(undated)} relevant item(s) are undated and were not placed in the 12-month window: "
                   + ", ".join(ctx.cite(s) for s in undated[:8]))
    out.append("### Sensitive corporate red flags\n")
    out.append(md_table(["Type", "Legal posture", "Observed (third-party)", "Evidence"],
                        [[c.get("red_flag_type"), c.get("legal_posture"), safe_excerpt(c.get("observed_statement") or "", 140),
                          ctx.ccite(c, 2)] for c in ctx.by_type("corporate_red_flag")]))
    out.append("Red flags are attributed reports, not findings of wrongdoing; one article is not a trend.\n")
    return "\n".join(out) + "\n"


def last_12_months(ctx: Ctx) -> tuple[list[list[str]], list[str]]:
    window = ctx.run.config.get("recent_window_days", 365)
    start = ctx.run_date - dt.timedelta(days=window)
    rows, undated = [], []
    seen = set()
    for c in ctx.claims:
        if c["claim_type"] not in ("news_mention", "public_contract", "mna_report", "funding_report", "corporate_red_flag"):
            continue
        sid = c["supporting_source_ids"][0]
        s = ctx.sources.get(sid, {})
        d = parse_date(c.get("event_date")) or parse_date(s.get("published_at"))
        if d is None:
            undated.append(sid)
            continue
        if start <= d <= ctx.run_date and c["claim_id"] not in seen:
            seen.add(c["claim_id"])
            basis = "event" if parse_date(c.get("event_date")) else "published"
            rows.append([f"{d} ({basis})", safe_excerpt(c["statement"] if c["claim_type"] != "news_mention" else str(c["value"]), 150),
                         ctx.ccite(c, 2)])
    for s in ctx.signals:
        if s["signal_type"] == "growth_expansion":
            src = ctx.sources.get(s["source_ids"][0], {})
            d = parse_date(src.get("published_at"))
            if d and start <= d <= ctx.run_date:
                rows.append([f"{d} (published)", s["observed_statement"][:150], ctx.cite(*s["source_ids"])])
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows, list(dict.fromkeys(undated))


def sec_digital(ctx: Ctx) -> str:
    out = ["## Websites, domains and authentication surfaces\n"]
    rows = []
    for dom in sorted(ctx.company.official_domains):
        rd = (ctx.extra.get("rdap") or {}).get(dom, {})
        sm = (ctx.extra.get("sitemaps") or {}).get(dom, {})
        n = sum(1 for s in ctx.sources.values() if host_of(s["url"]).endswith(dom))
        rows.append([dom, (rd.get("registered_at") or "UNKNOWN")[:10], rd.get("registrar") or "UNKNOWN",
                     sm.get("urls_from_sitemaps", 0), n, ctx.cite(rd.get("source_id")) if rd.get("source_id") else "NOT_CHECKED"])
    out.append(md_table(["Domain", "Registered (RDAP)", "Registrar", "Sitemap URLs", "URLs discovered", "Evidence"], rows))
    out.append("### Visible authentication and portals (observable only)\n")
    out.append(md_table(["Surface", "Label", "Basis", "Evidence", "Caveat"],
                        [[f"<{o['surface_url']}>", safe_excerpt(o["label"], 50), o["basis"], ctx.cite(*o["source_ids"], limit=2),
                          "internals unknown"] for o in ctx.obs_kind("auth_surface")[:60]]))
    out.append(md_table(["Auth method observation", "Evidence class", "Domain", "Evidence", "Caveat"],
                        [[o["method"], o["evidence_class"], o["domain"], ctx.cite(*o["source_ids"], limit=2), o["caveat"][:120]]
                         for o in ctx.obs_kind("auth_method")]))
    out.append("### Public API / developer documentation\n")
    out.append(md_table(["URL", "Label", "Evidence"], [[f"<{o['url']}>", o.get("label", ""), ctx.cite(*o["source_ids"])]
                                                       for o in ctx.obs_kind("public_api_docs")]))
    out.append("### Externally visible data posture\n")
    out.append(md_table(["Privacy notice", "Habeas data / Ley 1581", "Cookies", "Evidence"],
                        [["yes", o["mentions_habeas_data_or_1581"], o["mentions_cookies"], ctx.cite(*o["source_ids"])]
                         for o in ctx.obs_kind("data_posture")]))
    out.append("This describes what the company publishes, not its internal databases or security.\n")
    return "\n".join(out) + "\n"


def sec_tech(ctx: Ctx) -> str:
    out = ["## Visible technology observations\n",
           "Fingerprints are indicators, not architecture maps; they reveal nothing about versions or security state.\n"]
    out.append(md_table(["Technology", "Category", "Evidence type", "Status", "Domain", "Source/date"],
                        [[o["technology"], o["category"], o["evidence_type"], o["status"], o["domain"],
                          ctx.cite(*o["source_ids"], limit=1) + " " + (o.get("observed_at_utc") or "")[:10]]
                         for o in ctx.obs_kind("tech_fingerprint")]))
    out.append("### Named data platforms / disclosures\n")
    out.append(md_table(["Technology", "Evidence class", "Statement", "Evidence", "Caveat"],
                        [[o["technology"], o["evidence_class"], o["statement"][:120], ctx.cite(*o["source_ids"]), o["caveat"]]
                         for o in ctx.obs_kind("tech_disclosure")]))
    sh = ctx.obs_kind("security_headers")
    if sh:
        out.append("### Ordinary response headers (not an assessment)\n")
        out.append(md_table(["Host", "Present", "Absent", "Evidence"],
                            [[o.get("host", "?"), ", ".join(o["present"]) or "none", ", ".join(o["absent"]) or "none",
                              ctx.cite(*o["source_ids"])] for o in sh]))
    return "\n".join(out) + "\n"


def sec_social(ctx: Ctx) -> str:
    out = ["## Social, community and reputation\n"]
    socials = {}
    for o in ctx.obs_kind("social_profile"):
        key = o["url"].split("?")[0].rstrip("/").lower()
        if key not in socials or o["classification"].startswith("official"):
            socials[key] = o
    out.append(md_table(["Platform", "URL", "Classification", "Followers", "Last verified activity", "Evidence", "Access note"],
                        [[o["platform"], f"<{o['url']}>", o["classification"], o["followers"], o["last_verified_activity"],
                          ctx.cite(*o["source_ids"], limit=1), o["access_note"]] for o in socials.values()]))
    restricted = [s for s in ctx.sources.values() if s.get("processing_status") == "not_fetched_platform_restricted"]
    if restricted:
        out.append(f"- {len(restricted)} social/platform URL(s) recorded but not fetched: "
                   "NOT_CHECKED_PROVIDER_RESTRICTED (login walls/robots).")
    comp = [s for s in ctx.signals if s["signal_type"] == "customer_complaints"]
    out.append("### Public customer friction (anecdotal unless a pattern is shown)\n")
    out.append(md_table(["Signal", "Observed", "Source type", "Evidence"],
                        [[s["signal_id"], s["observed_statement"][:150], s.get("source_type"), ctx.cite(*s["source_ids"])]
                         for s in comp]))
    out.append(f"Sampling: complaints are counted only from sources fetched in this run ({len(comp)} signals); "
               "no universal reputation conclusion is drawn.\n")
    return "\n".join(out) + "\n"


def sec_records_news(ctx: Ctx) -> str:
    out = ["## Public records and news\n"]
    rues = ctx.extra.get("rues")
    if rues:
        out.append(f"- RUES registry (Confecámaras open data): {rues.get('razon_social')}, NIT {rues.get('nit')}-"
                   f"{rues.get('digito_verificacion')}, Cámara {str(rues.get('camara_comercio', '')).title()}, matrícula "
                   f"{rues.get('matricula')} registered {rues.get('fecha_matricula')}, {rues.get('estado_matricula')}, "
                   f"last renewed {rues.get('ultimo_ano_renovado')}; CIIU {rues.get('cod_ciiu_act_econ_pri')}"
                   + (f"/{rues['cod_ciiu_act_econ_sec']}" if rues.get("cod_ciiu_act_econ_sec") else "") + ".")
    elif ctx.company.country == "CO":
        out.append("- RUES registry: no record matched the exact name/NIT (not found ≠ not registered; check rues.org.co).")
    secop = ctx.extra.get("secop")
    if secop:
        out.append(f"- SECOP II query ({secop.get('dataset', '')}) by {secop.get('queried_by', '?')}: "
                   f"{secop.get('rows_returned', 0)} rows returned, {secop.get('rows_kept_identity_matched', 0)} kept "
                   "after identity matching." if "status" not in secop else f"- SECOP II: error — {secop.get('detail')}")
    else:
        out.append("- SECOP II: NOT_CHECKED (country is not CO or external sources skipped).")
    out.append(md_table(["Contract", "Statement", "Evidence"],
                        [[c["value"], safe_excerpt(c["statement"], 180), ctx.ccite(c, 1)] for c in ctx.by_type("public_contract")]))
    out.append("### News coverage\n")
    out.append(md_table(["Headline", "Date", "Source type", "Evidence"],
                        [[safe_excerpt(str(c["value"]), 100), ctx.date_of(c["supporting_source_ids"][0]),
                          ctx.sources.get(c["supporting_source_ids"][0], {}).get("source_type"), ctx.ccite(c, 1)]
                         for c in ctx.by_type("news_mention")]))
    return "\n".join(out) + "\n"


def sec_competitors(ctx: Ctx) -> str:
    out = ["## Competitors and market\n"]
    comp_src = [s for s in ctx.sources.values() if s.get("search_facet") == "customers_market"
                and s.get("processing_status", "").startswith("extracted")]
    if not comp_src:
        out.append("- Not established from evidence in this run. Competitor identification needs explicit "
                   "comparable-company research (targeted mode: `--focus competitors`).\n")
    else:
        out.append(md_table(["Source", "Title", "Relevance"], [[ctx.cite(s["source_id"]), safe_excerpt(s.get("title") or "", 90),
                                                                 s.get("relevance_score")] for s in comp_src[:30]]))
        out.append("Comparable companies listed in these sources still need verification; no ranking is implied.\n")
    return "\n".join(out) + "\n"


def sec_risk(ctx: Ctx) -> str:
    out = ["## Public digital-risk observations and reported incidents\n",
           "Passive research of published reports only. No scanning, probing, login attempts or leaked-data access. "
           "Risk/severity is not the same as claim confidence.\n"]
    if ctx.incidents:
        out.append(md_table(["ID", "Finding type", "Published/event date", "Status", "Severity basis", "Summary (redacted)",
                             "Identity match", "Evidence", "Owner validation"],
                            [[i["incident_id"], i["finding_type"], str(i.get("published_event_date") or "undated")[:10],
                              i["status"], i["severity_basis"], i["redacted_summary"][:160], i["company_identity_match"],
                              ctx.cite(*i["source_ids"]), "needed"] for i in ctx.incidents]))
    else:
        out.append("- " + NO_HIT.format(d=ctx.run_date.isoformat()))
    exp = ctx.obs_kind("incidental_exposure")
    if exp:
        out.append("### Incidental possible exposures (metadata only, unverified)\n")
        for o in exp:
            out.append(f"- {o['redacted_summary']} Status: {o['status']}. {o['recommended_action']} {ctx.cite(*o['source_ids'])}")
    out.append("\n### Coverage of this check\n")
    checked = sum(1 for s in ctx.processed() if s.get("search_facet") == "risk_incidents"
                  or s.get("source_type") in ("reputable_news", "news_other"))
    pending = [q for q in ctx.run.store.queries("NOT_EXECUTED_NO_PROVIDER") if q.get("facet") == "risk_incidents"]
    out.append(f"- News/incident sources processed: {checked}. Incident-facet queries not executed by an API: {len(pending)}.")
    out.append("- Breach-notification registries / HIBP domain search: NOT_CHECKED_PROVIDER_RESTRICTED "
               "(requires owner-verified domain control and authorization).")
    out.append("- CVE applicability: UNDETERMINED — no deployed versions are evidenced; fingerprints are not vulnerability findings.")
    out.append("- If relevant, propose an owner-authorized security review as a follow-up; this tool has not performed one.\n")
    return "\n".join(out) + "\n"


SECTIONS = [
    ("company_identity.md", sec_identity), ("company_history_timeline.md", sec_history),
    ("business_model_and_operations.md", sec_business_model), ("products_services_channels.md", sec_products),
    ("digital_footprint.md", sec_digital), ("technology_observations.md", sec_tech),
    ("social_media_and_reputation.md", sec_social), ("personnel_hiring_and_partners.md", sec_people),
    ("ownership_funding_and_recent_events.md", sec_ownership), ("public_records_and_news.md", sec_records_news),
    ("competitors_and_market.md", sec_competitors), ("public_risk_observations.md", sec_risk),
]


# ---------------------------------------------------------------------------
# Brief, catalogs, questions, handoff
# ---------------------------------------------------------------------------

def _ymd(value) -> str:
    v = str(value or "")
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v.isdigit() else v[:10]


def _ciiu(rues: dict) -> str:
    return "/".join(x for x in (rues.get("cod_ciiu_act_econ_pri"), rues.get("cod_ciiu_act_econ_sec")) if x)


def render_brief(ctx: Ctx) -> str:
    c = ctx.company
    level, why = overall_confidence(ctx)
    processed, discovered = len(ctx.processed()), len(ctx.sources)
    L = [f"# {c.name} — Public Information Brief\n",
         f"_Generated: {ctx.generated} · unique sources checked: {processed} · sources discovered: {discovered} · "
         f"research depth: {ctx.run.opt.depth} · overall evidence confidence: {level}_\n",
         f"> {why} As-of date: {ctx.run_date}. Full evidence: [FULL_COMPANY_DOSSIER.md](FULL_COMPANY_DOSSIER.md) · "
         f"[source registry](sources/source_registry.jsonl).\n",
         "## 1. Snapshot (5 bullets maximum)\n"]
    bullets = []
    legal = ctx.by_type("legal_name_variant", "tax_id_nit")
    ident = ctx.identity.get("status", "unknown")
    if legal:
        bullets.append(f"- Identity: {ident}; public legal-form name/ID: {safe_excerpt(str(legal[0]['value']), 60)} ({ctx.ccite(legal[0], 2)}).")
    else:
        off = [s for s in ctx.processed() if c.is_official_url(s["url"])]
        bullets.append(f"- Identity: {ident}; no legal name/NIT found publicly in sources checked "
                       f"({ctx.cite(off[0]['source_id']) if off else 'no official page processed'}).")
    rues = ctx.extra.get("rues")
    rues_claim = next(iter(ctx.by_type("certification_or_registration")), None)
    if rues:
        bullets.append(f"- Registry (RUES): matrícula {rues.get('matricula')}, Cámara "
                       f"{str(rues.get('camara_comercio', '')).title()}, registered {_ymd(rues.get('fecha_matricula'))}, "
                       f"{rues.get('estado_matricula')}, last renewed {rues.get('ultimo_ano_renovado')}; "
                       f"CIIU {_ciiu(rues)}" + (f" ({ctx.ccite(rues_claim, 1)})" if rues_claim else "") + ".")
    offers = ranked_offers(ctx)[:4]
    if offers:
        bullets.append("- Advertises: " + "; ".join(safe_excerpt(str(o["value"]), 40) for o in offers) +
                       f" ({ctx.cite(*{s for o in offers for s in o['supporting_source_ids']}, limit=2)}).")
    founding = sorted(ctx.by_type("founding_year"), key=lambda f: f.get("fact_status") != "confirmed")
    if len({f["value"] for f in founding}) > 1:
        bullets.append("- Founding year conflicts across sources: " + "; ".join(
            f"{f['value']} ({ctx.ccite(f, 1)})" for f in founding[:3]) + " — confirm.")
    elif founding and not (rues and str(founding[0]["value"]) == str(rues.get("fecha_matricula") or "")[:4]):
        bullets.append(f"- Founded (stated): {founding[0]['value']} — {founding[0].get('fact_status')} ({ctx.ccite(founding[0], 2)}).")
    sites = ctx.by_type("site_count_statement", "employee_count_statement")
    if sites:
        bullets.append(f"- Footprint statement: {safe_excerpt(str(sites[0]['value']), 50)} ({ctx.ccite(sites[0], 2)}).")
    if ctx.hyps:
        h = ctx.hyps[0]
        bullets.append(f"- Top discovery theme: {h['business_function']} — {h['opp_id']}, confidence {h['hypothesis_confidence']} "
                       f"({ctx.cite(*h['source_ids'], limit=2)}).")
    L += bullets[:5]
    L.append("\n## 2. What they do\n")
    if offers:
        L += [f"- {safe_excerpt(str(o['value']), 90)} — {ctx.ccite(o, 1)}" for o in ranked_offers(ctx)[:8]]
    else:
        L.append("- Not established from processed official pages (UNKNOWN).")
    if rues and _ciiu(rues):
        L.append(f"- Registered economic activity (CIIU, RUES): {_ciiu(rues)}"
                 + (f" ({ctx.ccite(rues_claim, 1)})" if rues_claim else "")
                 + ". Registered codes describe the legal object, not necessarily current operations.")
    chans = sorted({s["signal_type"] for s in ctx.signals if s["signal_type"] in
                    ("ecommerce_present", "whatsapp_ordering", "quote_request", "b2b_portal", "wholesale_distribution")})
    if chans:
        ex = [s for s in ctx.signals if s["signal_type"] in chans]
        L.append(f"- Channel indicators: {', '.join(chans)} ({ctx.cite(*{x for s in ex for x in s['source_ids']}, limit=3)}).")
    L.append("\n## 3. History and milestones\n")
    L.append(md_table(["Event date", "Event or change", "Claim confidence", "Source"], [r for _, r in timeline_rows(ctx)][:12]))
    L.append("## 4. Web presence and authentication\n")
    rows = []
    for dom in sorted(c.official_domains):
        surf = [o for o in ctx.obs_kind("auth_surface") if o["domain"].endswith(dom)]
        meth = [o for o in ctx.obs_kind("auth_method") if o["domain"].endswith(dom)]
        caps = sorted({s["signal_type"] for s in ctx.signals if any(c.is_official_url(ctx.sources.get(x, {}).get("url", ""))
                                                                    for x in s["source_ids"])})[:4]
        home = next((s for s in ctx.processed() if host_of(s["url"]).endswith(dom)), None)
        ev = ctx.cite(*([o["source_ids"][0] for o in surf[:2]] or ([home["source_id"]] if home else [])))
        rows.append([dom, ", ".join(caps) or "site content", (", ".join(sorted({m["method"] for m in meth})) or
                     ("login/portal surface visible" if surf else "none observed")), ev,
                     "observed" if surf or meth else ("UNKNOWN" if not home else "observed: none")])
    for o in ctx.obs_kind("auth_surface"):
        if not any(o["domain"].endswith(d) for d in c.official_domains):
            rows.append([o["domain"], "linked portal", o["label"], ctx.cite(*o["source_ids"], limit=1), "observed"])
    L.append(md_table(["Domain/surface", "Observed capability", "Visible authentication option", "Evidence", "Confidence"], rows[:12]))
    L.append("_A login form does not establish OAuth, SAML, password storage or any internal design._\n")
    L.append("## 5. Data and technology signals\n")
    trow = [[o["technology"], o["evidence_type"], "INFERRED", ctx.cite(*o["source_ids"], limit=1) + " " +
             (o.get("observed_at_utc") or "")[:10]] for o in ctx.obs_kind("tech_fingerprint")[:12]]
    trow += [[o["technology"], o["evidence_class"], "CONFIRMED" if o["evidence_class"] == "SELF_DISCLOSED" else
              ("INFERRED" if o["evidence_class"] != "UNKNOWN" else "UNKNOWN"), ctx.cite(*o["source_ids"], limit=1)]
             for o in ctx.obs_kind("tech_disclosure")[:8]]
    L.append(md_table(["Technology or platform", "Evidence type", "CONFIRMED / INFERRED / UNKNOWN", "Source/date"], trow))
    L.append("## 6. Social and community presence\n")
    srows, seen = [], set()
    for o in ctx.obs_kind("social_profile"):
        k = o["platform"] + o["url"].split("?")[0].rstrip("/").lower()
        if k in seen:
            continue
        seen.add(k)
        srows.append([o["platform"], f"<{o['url']}>" if o["classification"].startswith("official") else f"unverified: <{o['url']}>",
                      "UNKNOWN (not collected)", "UNKNOWN", ({"official_supplied_by_founder": "medium — supplied by the founder "}.get(
                          o["classification"], "medium — linked from official site ") if o["classification"].startswith("official")
                                                          else "low ") + ctx.cite(*o["source_ids"], limit=1)])
    L.append(md_table(["Platform", "Official profile", "Public activity indicators", "Last verified activity", "Confidence"], srows[:12]))
    L.append("## 7. Publicly reported incidents and exposure observations\n")
    if ctx.incidents:
        L.append(md_table(["Finding", "Date", "Source", "Relevance / stated severity", "Verification status"],
                          [[f"{i['incident_id']} {i['finding_type']}: {i['redacted_summary'][:100]}",
                            str(i.get("published_event_date") or "undated")[:10], ctx.cite(*i["source_ids"]),
                            i["severity_basis"], i["status"]] for i in ctx.incidents[:8]]))
    else:
        L.append(md_table(["Finding", "Date", "Source", "Relevance / stated severity", "Verification status"],
                          [[NO_HIT.format(d=ctx.run_date.isoformat()), ctx.run_date.isoformat(),
                            "see public_risk_observations.md (coverage)", "n/a", "NO_HIT_IN_CHECKED_SOURCES"]]))
    L.append("## 8. Business red flags and useful meeting talking points\n")
    flags = ctx.by_type("corporate_red_flag")
    for f in flags[:5]:
        L.append(f"- Caution ({f.get('red_flag_type')}, {f.get('legal_posture')}): {safe_excerpt(f.get('observed_statement') or '', 140)} — {ctx.ccite(f, 1)}")
    for x in (ctx.extra.get("contradictions") or [])[:3]:
        L.append(f"- Caution: conflicting {x['claim_type']} values across sources ("
                 + ", ".join(f"`{i}`" for i in x["claim_ids"]) + "); confirm in the meeting.")
    if ident in UNCERTAIN_IDENTITY:
        L.append(f"- Caution: identity is {ident}; confirm legal entity and official domain at the start.")
    for h in ctx.hyps[:3]:
        L.append(f"- Talking point ({h['opp_id']}): {h['meeting_questions'][0]} — {ctx.cite(*h['source_ids'], limit=2)}")
    if not flags and not ctx.hyps:
        L.append("- No red flags or signals found in the sources checked (coverage limits apply).")
    L.append("\n## 9. Priority questions for the meeting\n")
    qs = discovery_question_list(ctx)[:7]
    L += [f"{i}. {q['question']} — {q['cite']}" for i, q in enumerate(qs, 1)] or ["1. Confirm legal entity, offering and main channels (no signals found)."]
    L.append("\n## 10. Sources and retrieval dates\n")
    cited = sorted(ctx.cited)
    L.append(md_table(["ID", "Title / publisher", "Type", "Published (basis)", "Archive capture", "Retrieved (UTC)"],
                      [[f"[{sid}]({ctx.sources[sid].get('final_url') or ctx.sources[sid]['url']})",
                        safe_excerpt(ctx.sources[sid].get("title") or ctx.sources[sid].get("publisher") or "", 60),
                        ctx.sources[sid].get("source_type"),
                        f"{str(ctx.sources[sid].get('published_at') or 'undated')[:10]} ({ctx.sources[sid].get('published_at_basis')})",
                        str(ctx.sources[sid].get("archive_capture_at") or "—")[:8],
                        str(ctx.sources[sid].get("retrieved_at_utc") or "not fetched")[:19]] for sid in cited]))
    L.append(f"Complete inventory ({discovered} discovered URLs, including blocked and excluded): "
             "[sources/source_registry.jsonl](sources/source_registry.jsonl).\n")
    top = ctx.hyps[0] if ctx.hyps else None
    tldr = (f"{c.name} ({ident} identity) — {processed} public sources processed, evidence confidence {level}. "
            + (f"Lead with {top['business_function']} ({top['opp_id']}): {top['meeting_questions'][0]} " if top else "")
            + ("No public incident reports found in the sources checked. " if not ctx.incidents else
               f"{len(ctx.incidents)} published incident report(s) to handle carefully. ")
            + "Everything here is public information to validate, not a diagnosis.")
    L.append(f"**TL;DR for the meeting:** {tldr}\n")
    return "\n".join(L)


def discovery_question_list(ctx: Ctx) -> list[dict]:
    qs = []
    if ctx.identity.get("status") in UNCERTAIN_IDENTITY:
        qs.append({"question": "Can you confirm the legal entity, NIT and official website we should associate with your company?",
                   "cite": "identity " + ctx.identity.get("status", ""), "reason": "Identity not fully established",
                   "function": "identity", "clarifies": "prevents mixing similar-name entities", "rank_score": 999})
    for h in ctx.hyps:
        for q in h["meeting_questions"][:2]:
            qs.append({"question": q, "cite": f"{h['opp_id']} " + ctx.cite(*h["source_ids"], limit=2),
                       "reason": h["hypothesis"], "function": h["business_function"],
                       "clarifies": h["what_must_be_true"], "rank_score": h["discovery_priority_score"],
                       "signals": h["signal_ids"][:5]})
    for x in (ctx.extra.get("contradictions") or []):
        qs.append({"question": f"Public sources disagree on {x['claim_type'].replace('_', ' ')}; which is correct?",
                   "cite": ", ".join(f"`{i}`" for i in x["claim_ids"]), "reason": "contradiction",
                   "function": "identity", "clarifies": "resolves conflicting public records", "rank_score": 50})
    qs.sort(key=lambda q: -q["rank_score"])
    seen, out = set(), []
    for q in qs:
        if q["question"] not in seen:
            seen.add(q["question"])
            out.append(q)
    return out


def render_signal_catalog(ctx: Ctx) -> str:
    L = ["# Opportunity signal catalog\n",
         f"Every useful observation ({len(ctx.signals)} signals) — not only the strongest. Signals are public "
         "observations that justify questions; they are not client problems. Weak/third-party signals are labelled.\n"]
    by_f: dict[str, list] = {}
    for s in ctx.signals:
        by_f.setdefault(s["business_function"], []).append(s)
    for f, sigs in sorted(by_f.items(), key=lambda x: -len(x[1])):
        L.append(f"## {f} ({len(sigs)})\n")
        L.append(md_table(["ID", "Type", "Strength", "Observed statement", "Why it matters", "Alternative", "Question", "Location", "Source type"],
                          [[s["signal_id"], s["signal_type"], s["strength"], s["observed_statement"][:180], s["why_it_matters"],
                            s["alternative_interpretations"], s["meeting_question"], ctx.cite(*s["source_ids"]) + " §" +
                            safe_excerpt(s.get("section") or "", 40), s.get("source_type")] for s in sigs]))
    return "\n".join(L)


def render_hypotheses(ctx: Ctx) -> str:
    L = ["# Opportunity hypothesis register\n",
         "Ranked for discovery priority; the full backlog is retained. Hypotheses come from public signals and "
         "must be validated in discovery. No budgets, revenue, effort or prices are inferred.\n"]
    for h in ctx.hyps:
        L += [f"## {h['opp_id']} — {h['business_function']}\n",
              f"- **Hypothesis:** {h['hypothesis']}",
              f"- **Signals:** {', '.join(h['signal_ids'][:12])}{' …' if len(h['signal_ids']) > 12 else ''} · sources: {ctx.cite(*h['source_ids'], limit=6)}",
              f"- **Signal quality:** {h['signal_quality_note']}",
              f"- **What must be true:** {h['what_must_be_true']}",
              f"- **Meeting questions:** " + " / ".join(h["meeting_questions"]),
              f"- **Validation data needed:** {', '.join(h['validation_data_needed'])}",
              f"- **Benefits/metrics if validated:** {', '.join(h['potential_benefits_metrics'])}",
              f"- **Possible interventions:** {', '.join(h['possible_interventions'])}. Trade-offs: {h['intervention_tradeoffs']}",
              f"- **Possible prototype:** {h['possible_prototype']}",
              f"- **Productization:** {h['productization']}",
              f"- **hypothesis_confidence:** {h['hypothesis_confidence']} · **potential_value:** {h['potential_value']} · "
              f"**discovery_priority:** #{h['discovery_priority_rank']} (score {h['discovery_priority_score']})",
              f"- **Feasibility unknowns:** {', '.join(h['feasibility_unknowns'])}",
              f"- **Disconfirming evidence / alternative / reasons not to pursue:** {h['disconfirming_evidence']} "
              f"{h['alternative_interpretation']} {h['reasons_not_to_pursue']}", ""]
    if not ctx.hyps:
        L.append("_No opportunity signals were found in the processed sources._")
    return "\n".join(L)


def render_questions(ctx: Ctx) -> str:
    qs = discovery_question_list(ctx)
    L = ["# Discovery questions\n", "Prioritized, evidence-linked, for Cumbre's discovery interview.\n"]
    L += [f"{i}. **{q['question']}** — {q['cite']}  \n   _Why:_ {q['reason']}" for i, q in enumerate(qs, 1)]
    return "\n".join(L) + "\n"


def render_followups(ctx: Ctx, correlation: list[dict] | None = None) -> str:
    qs = discovery_question_list(ctx)
    L = ["# Discovery follow-ups (ranked)\n",
         "Evidence classes kept separate: PUBLIC_WEB · CLIENT_STATEMENT · CUMBRE_OBSERVATION · HYPOTHESIS.\n"]
    L.append(md_table(["#", "Exact question", "Supporting source/signal", "Reason for asking", "What the answer clarifies",
                       "Discovery Engine output that consumes it"],
                      [[i, q["question"], q["cite"], f"HYPOTHESIS: {q['reason']}", q["clarifies"],
                        DISCOVERY_CONSUMERS.get(q["function"], "01_business_profile.md, 08_validation_requests.md")]
                       for i, q in enumerate(qs, 1)]))
    if correlation is not None:
        L.append("## Post-meeting correlation (local only; nothing sent to external services)\n")
        L.append(md_table(["Public claim (PUBLIC_WEB)", "Client statement (CLIENT_STATEMENT)", "Assessment", "To resolve"],
                          [[r["public"], r["client"], r["assessment"], r["resolve"]] for r in correlation]))
        L.append("Assessments are keyword-based candidates for human review, not conclusions.\n")
    return "\n".join(L)


def render_talking_points(ctx: Ctx) -> str:
    L = [f"# {ctx.company.name} — Meeting talking points\n",
         "Three distinct labels. Never assert an internal weakness from an outward-facing page.\n",
         "## Confirmed context (safe to mention; dated and cited)\n"]
    safe = [c for c in ctx.claims if c.get("fact_status") == "confirmed" and c["claim_type"] in
            ("offering_or_topic", "founding_year", "public_contract", "domain_registered", "news_mention", "location_mention",
             "certification_or_registration")]
    L += [f"- {safe_excerpt(c['statement'], 160)} ({ctx.date_of(c['supporting_source_ids'][0])}) — {ctx.ccite(c, 1)}" for c in safe[:15]] \
        or ["- Nothing confirmed beyond the basics; keep openers general."]
    L.append("\n## Questions / possible opportunities (hypotheses to validate)\n")
    for h in ctx.hyps[:10]:
        sig = next((s for s in ctx.signals if s["signal_id"] in h["signal_ids"]), None)
        opener = (f"Your public site mentions “{safe_excerpt(sig['observed_statement'], 90)}”; "
                  f"{h['meeting_questions'][0][0].lower() + h['meeting_questions'][0][1:]}") if sig else h["meeting_questions"][0]
        L.append(f"- **{h['opp_id']}** ({h['business_function']}): {opener} — {ctx.cite(*h['source_ids'], limit=2)}")
    L.append("\n## Caution / red flags\n")
    cautions = []
    if ctx.identity.get("status") not in CONFIRMED_IDENTITY:
        cautions.append(f"- Identity is {ctx.identity.get('status')}; confirm entity before referencing public facts.")
    for f in ctx.by_type("corporate_red_flag"):
        cautions.append(f"- {f.get('red_flag_type')} ({f.get('legal_posture')}): reported by a third party — do not raise "
                        f"accusatorily. {ctx.ccite(f, 1)}")
    for i in ctx.incidents:
        cautions.append(f"- Published report {i['incident_id']} ({i['finding_type']}, {i['status']}): handle only if relevant; "
                        f"never as a pressure tactic. {ctx.cite(*i['source_ids'])}")
    for x in (ctx.extra.get("contradictions") or []):
        cautions.append(f"- Conflicting public {x['claim_type']}: {', '.join(x['claim_ids'])}.")
    L += cautions or ["- None identified in the sources checked."]
    return "\n".join(L) + "\n"


def render_handoff(ctx: Ctx) -> str:
    rows, undated = last_12_months(ctx)
    L = [f"# Discovery context handoff — {ctx.company.name}\n",
         "Machine/agent input for `/cumbre-discovery`. **All content is `[EXTERNO]`** (public web research, with source "
         "and date). It must never be merged with client statements (`[CONFIRMADO]`) or promoted to confirmed internal "
         "process or a guaranteed service recommendation.\n",
         f"- Research run: `{ctx.run.run_id}` · as of {ctx.run_date} · depth {ctx.run.opt.depth} · identity {ctx.identity.get('status')}",
         f"- Evidence confidence: {overall_confidence(ctx)[0]} — {overall_confidence(ctx)[1]}\n",
         "## Identity [EXTERNO]\n"]
    for c in ctx.by_type("legal_name_variant", "tax_id_nit")[:5]:
        L.append(f"- [EXTERNO] {c['statement']} {ctx.ccite(c, 1)}")
    L.append(f"\n## 12-month news digest (relative to {ctx.run_date}) [EXTERNO]\n")
    L += [f"- [EXTERNO] {r[0]}: {r[1]} {r[2]}" for r in rows[:15]] or ["- [EXTERNO] No dated items found in the window."]
    L.append("\n## Publicly referenced technical platforms [EXTERNO]\n")
    techs = {}
    for o in ctx.obs_kind("tech_fingerprint") + ctx.obs_kind("tech_disclosure"):
        techs.setdefault(o["technology"], o)
    L += [f"- [EXTERNO] {t} — {o.get('evidence_class') or o.get('evidence_type')} (indicator only) {ctx.cite(*o['source_ids'], limit=1)}"
          for t, o in list(techs.items())[:25]] or ["- [EXTERNO] none observed"]
    L.append("\n## Customer-friction themes [EXTERNO]\n")
    fr = [s for s in ctx.signals if s["signal_type"] in ("customer_complaints", "order_tracking", "returns_warranty_pqrs")]
    L += [f"- [EXTERNO] {s['business_function']}: {s['observed_statement'][:140]} {ctx.cite(*s['source_ids'])}" for s in fr[:10]] \
        or ["- [EXTERNO] none found in sources checked"]
    L.append("\n## Source-linked question shortlist [INFERENCIA from EXTERNO]\n")
    L += [f"- {q['question']} — {q['cite']}" for q in discovery_question_list(ctx)[:12]]
    L.append("\n## Opportunity hypotheses (to validate) [INFERENCIA]\n")
    L += [f"- {h['opp_id']} {h['business_function']}: {h['hypothesis']} (confidence {h['hypothesis_confidence']})" for h in ctx.hyps[:12]]
    L.append("\nOPP IDs here are research-run IDs; the Discovery Engine assigns its own OPP-XXX in `05_opportunity_backlog.md`.\n")
    return "\n".join(L)


def render_coverage(ctx: Ctx) -> str:
    st = ctx.run.store
    srcs = list(ctx.sources.values())
    processed = ctx.processed()
    by_status: dict[str, int] = {}
    for s in srcs:
        by_status[s.get("processing_status") or "?"] = by_status.get(s.get("processing_status") or "?", 0) + 1
    by_type: dict[str, int] = {}
    for s in processed:
        by_type[s.get("source_type")] = by_type.get(s.get("source_type"), 0) + 1
    queries = st.queries()
    q_status: dict[str, int] = {}
    for q in queries:
        q_status[q["status"]] = q_status.get(q["status"], 0) + 1
    facets: dict[str, list] = {}
    for q in queries:
        facets.setdefault(q.get("facet") or "?", []).append(q)
    dates = sorted(d for d in (parse_date(s.get("published_at")) for s in processed) if d)
    rel_bands = {"≥70": 0, "40–69": 0, "<40": 0, "unscored": 0}
    for s in srcs:
        r = s.get("relevance_score")
        rel_bands["unscored" if r is None else ("≥70" if r >= 70 else "40–69" if r >= 40 else "<40")] += 1
    target = ctx.run.depth_cfg["target_pages"]
    L = ["# Coverage report\n",
         f"Run `{ctx.run.run_id}` · {ctx.company.name} · depth **{ctx.run.opt.depth}** (target {target[0]}–{target[1]} relevant pages; "
         f"a target, not a quota) · mode {ctx.run.opt.mode}\n",
         f"**Stop reason:** {ctx.run.stop_reason or 'n/a'}\n",
         "Coverage is an estimate over researched categories, not a claim that all public information was found.\n",
         "## Counts (separate measures)\n",
         md_table(["Measure", "Count"], [
             ["Discovered URLs (registry)", len(srcs)], ["Processed/extracted sources", len(processed)],
             ["Distinct hosts processed", len({host_of(s['url']) for s in processed})],
             ["Distinct useful sources (relevance ≥40, processed)", sum(1 for s in processed if (s.get('relevance_score') or 0) >= 40)],
             ["Documents (PDF/text) extracted", sum(1 for s in processed if s.get('processing_status') in ('extracted_pdf', 'extracted_text'))],
             ["Search queries planned", len(queries)], ["Claims", len(ctx.claims)], ["Signals", len(ctx.signals)],
             ["Opportunity hypotheses", len(ctx.hyps)], ["Incident observations", len(ctx.incidents)],
             ["Relationship edges", len(ctx.edges)]]),
         "## Processing status of all discovered URLs\n",
         md_table(["Status", "URLs"], sorted(([k, v] for k, v in by_status.items()), key=lambda x: -x[1])),
         "## Source-quality distribution (processed)\n",
         md_table(["Source type", "Count"], sorted(([k, v] for k, v in by_type.items()), key=lambda x: -x[1])),
         "## Relevance distribution (all discovered)\n", md_table(["Band", "Count"], [[k, v] for k, v in rel_bands.items()]),
         "## Queries by status\n", md_table(["Status", "Count"], [[k, v] for k, v in q_status.items()]),
         "## Findings and queries by research facet\n",
         md_table(["Facet", "Queries", "Executed", "Not executed (no provider)", "Processed sources"],
                  [[f, len(qs), sum(1 for q in qs if q['status'] == 'executed'),
                    sum(1 for q in qs if q['status'] == 'NOT_EXECUTED_NO_PROVIDER'),
                    sum(1 for s in processed if s.get('search_facet') == f)] for f, qs in sorted(facets.items())]),
         "## Novelty by wave\n",
         md_table(["Wave", "Queries executed", "Pending (no provider)", "Pages fetched", "New claims", "New signals", "Novelty yield", "Seconds"],
                  [[w["wave"], w.get("query_executed"), w.get("query_pending_no_provider"), w.get("fetched"), w.get("new_claims"),
                    w.get("new_signals"), w.get("novelty_yield"), w.get("seconds")] for w in st.waves()]),
         f"## Dates covered\n\n- Publication dates span: {dates[0] if dates else 'n/a'} → {dates[-1] if dates else 'n/a'} "
         f"({len(dates)} dated of {len(processed)} processed; the rest are undated).\n",
         "## Providers\n",
         md_table(["Provider", "Capabilities", "Paid", "Status", "Detail"],
                  [[p["provider"], ", ".join(p["capabilities"]), p["paid"], p["status"], p["detail"]] for p in ctx.run.provider_rows()]),
         md_table(["Provider call", "Count"], [[k, v] for k, v in sorted(ctx.run.ledger.calls.items())]),
         f"- Spend: known ${ctx.run.ledger.spend_known:.4f}; paid calls with unknown unit cost: {ctx.run.ledger.spend_unknown_calls}. "
         "LLM tokens used by the engine: 0 (engine calls no LLM).",
         f"- Paid work skipped due to limits/authorization: {len(ctx.run.ledger.skipped)}.",
         f"- Runtime: {round(ctx.run.elapsed(), 1)} s. Errors logged: {len(ctx.run.errors)}.\n",
         "## Blocked, restricted and excluded\n",
         md_table(["Status", "Example URL", "Reason"],
                  [[s.get("processing_status"), f"<{s['url']}>", safe_excerpt(s.get("exclusion_reason") or "", 120)]
                   for s in srcs if s.get("processing_status") not in ("discovered", None)
                   and not s.get("processing_status", "").startswith("extracted")][:60]),
         "## Notable omissions and limitations\n"]
    L += [f"- {x}" for x in ctx.run.limitations] or ["- None recorded."]
    L.append("- Social metrics, Google Business Profile and LinkedIn data: NOT_CHECKED_PROVIDER_RESTRICTED unless fetched above.")
    L.append("- Colombian registries: RUES is queried through the Confecámaras open dataset (datos.gov.co); "
             "Supersociedades financial filings and DIAN RUT status are not automated; check manually.")
    return "\n".join(L) + "\n"


def render_journal(ctx: Ctx) -> str:
    st = ctx.run.store
    L = ["# Research journal\n", "Waves, pivots and unresolved branches.\n"]
    for w in st.waves():
        L.append(f"## Wave {w['wave']}\n")
        L.append(f"- Queries executed: {w.get('query_executed')}, pending for agent search: {w.get('query_pending_no_provider')}, "
                 f"hits: {w.get('query_hits')}, new sources: {w.get('query_new_sources')}")
        L.append(f"- Pages fetched: {w.get('fetched')}, new claims: {w.get('new_claims')}, new signals: {w.get('new_signals')}, "
                 f"novelty yield: {w.get('novelty_yield')}")
        qs = [q for q in st.queries() if q.get("wave") == w["wave"]]
        origins: dict[str, int] = {}
        for q in qs:
            origins[q.get("origin")] = origins.get(q.get("origin"), 0) + 1
        L.append(f"- Query origins: {origins}\n")
    L.append("## All queries\n")
    L.append(md_table(["ID", "Wave", "Facet", "Origin", "Status", "Providers", "Hits", "Query"],
                      [[q["query_id"], q.get("wave"), q.get("facet"), q.get("origin"), q["status"],
                        ",".join(q.get("providers") or q.get("partial_providers") or []), q.get("hit_count", ""),
                        q["query"].replace("|", "/")] for q in st.queries()]))
    L.append("## Errors\n")
    L += [f"- {e['at']} {e['where']}: {e['error'][:200]}" for e in ctx.run.errors[-80:]] or ["- None."]
    return "\n".join(L) + "\n"


def render_gaps(ctx: Ctx) -> str:
    L = ["# Research gaps\n", "Not found ≠ does not exist.\n"]
    checks = coverage_checks(ctx)
    L.append(md_table(["Topic", "Status"], [[t, "found (see dossier)" if ok else "NOT FOUND in sources checked"] for t, ok in checks]))
    pend = ctx.run.store.queries("NOT_EXECUTED_NO_PROVIDER")
    if pend:
        L.append(f"## Queries not executed by any API ({len(pend)})\n")
        L.append("Listed in `pending_queries.json`. Run them via the agent-search loop (see the skill) before treating "
                 "these gaps as real.\n")
        L += [f"- {q['query_id']} [{q.get('facet')}] {q['query']}" for q in pend[:80]]
    queued = [s for s in ctx.sources.values() if s.get("processing_status") == "discovered"]
    L.append(f"\n## Discovered but not inspected: {len(queued)} URLs\n")
    L += [f"- <{s['url']}> (relevance {s.get('relevance_score')})" for s in sorted(queued, key=lambda s: -(s.get('relevance_score') or 0))[:40]]
    weak = [c for c in ctx.claims if c.get("independent_domains", 0) <= 1 and c["claim_type"] in
            ("founding_year", "employee_count_statement", "ownership_indicator", "funding_report", "site_count_statement")]
    L.append("\n## Strong claims resting on a single source\n")
    L += [f"- `{c['claim_id']}` {c['claim_type']}: {safe_excerpt(str(c['value']), 80)}" for c in weak] or ["- None."]
    return "\n".join(L) + "\n"


def structured_profile(ctx: Ctx) -> dict:
    own = ctx.by_type("ownership_indicator")
    own_domains = {host_of(ctx.sources[s]["url"]) for c in own for s in c["supporting_source_ids"] if s in ctx.sources}
    rows, _ = last_12_months(ctx)
    return {
        "schema_version": ctx.run.config["schema_version"], "company_run_id": f"{ctx.company.slug}:{ctx.run.run_id}",
        "generated_at_utc": ctx.generated, "as_of_date": ctx.run_date.isoformat(),
        "company": ctx.company.to_dict(), "identity_decision": ctx.identity,
        "candidate_identity_matches": ctx.run.candidates,
        "ownership_type": "UNKNOWN" if len(own_domains) < 2 else "SEE_EVIDENCE_REQUIRES_REVIEW",
        "ownership_evidence": [{"claim_id": c["claim_id"], "value": c["value"], "source_ids": c["supporting_source_ids"]} for c in own],
        "funding_rounds": [{"claim_id": c["claim_id"], "report": c["value"], "source_ids": c["supporting_source_ids"],
                            "note": "reported, not audited"} for c in ctx.by_type("funding_report")],
        "investors": [],
        "employee_count_estimates": [{"claim_id": c["claim_id"], "statement": c["value"], "source_ids": c["supporting_source_ids"],
                                      "note": "public statement/estimate, not payroll"} for c in ctx.by_type("employee_count_statement")],
        "last_12_months_events": [{"date": r[0], "event": r[1], "evidence": r[2]} for r in rows],
        "auth_surfaces": ctx.obs_kind("auth_surface"), "auth_method_observations": ctx.obs_kind("auth_method"),
        "public_api_docs": ctx.obs_kind("public_api_docs"),
        "tech_disclosures": ctx.obs_kind("tech_disclosure") + ctx.obs_kind("tech_fingerprint"),
        "communities": ctx.obs_kind("social_profile"), "incident_reports": ctx.incidents,
        "claims_count": len(ctx.claims), "signals_count": len(ctx.signals),
        "opportunity_ids": [h["opp_id"] for h in ctx.hyps],
    }


def render_index(ctx: Ctx, files: list[str]) -> str:
    L = [f"# {ctx.company.name} — research run `{ctx.run.run_id}`\n",
         f"As of {ctx.run_date}. Start with [EXECUTIVE_BRIEF.md](EXECUTIVE_BRIEF.md) (5 minutes), then "
         "[MEETING_TALKING_POINTS.md](MEETING_TALKING_POINTS.md) and [DISCOVERY_FOLLOWUPS.md](DISCOVERY_FOLLOWUPS.md).\n"]
    L += [f"- [{f}]({f})" for f in files]
    L.append("\nQuery the evidence: `python scripts/research/cumbre.py query <this folder> \"inventario\"`.")
    return "\n".join(L) + "\n"


def render_all(run_dir: Path, run, correlation: list[dict] | None = None) -> list[str]:
    ctx = Ctx(run_dir, run)
    files = []

    def write(name: str, text: str):
        (run_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / name).write_text(text, encoding="utf-8")
        files.append(name)

    dossier = [f"# {ctx.company.name} — Full company dossier\n",
               f"As of {ctx.run_date} · run `{run.run_id}` · depth {run.opt.depth}. No page limit; every factual line cites "
               "registry IDs. Sections are also available as separate files.\n",
               "## Contents\n"] + [f"- [{n}]({n})" for n, _ in SECTIONS] + [""]
    for name, fn in SECTIONS:
        body = fn(ctx)
        write(name, f"# {ctx.company.name}\n\n" + body)
        dossier.append(body)
    dossier.append("## Opportunity signals and hypotheses\n\nSee [OPPORTUNITY_SIGNAL_CATALOG.md](OPPORTUNITY_SIGNAL_CATALOG.md) "
                   f"({len(ctx.signals)} signals) and [opportunity_hypotheses.md](opportunity_hypotheses.md) ({len(ctx.hyps)} hypotheses).\n")
    write("FULL_COMPANY_DOSSIER.md", "\n".join(dossier))
    write("public_incidents_and_advisories.md", f"# {ctx.company.name} — public incidents and advisories\n\n"
          "Cross-links [public_risk_observations.md](public_risk_observations.md).\n\n" + sec_risk(ctx))
    write("OPPORTUNITY_SIGNAL_CATALOG.md", render_signal_catalog(ctx))
    write("opportunity_hypotheses.md", render_hypotheses(ctx))
    write("discovery_questions.md", render_questions(ctx))
    write("DISCOVERY_FOLLOWUPS.md", render_followups(ctx, correlation))
    write("MEETING_TALKING_POINTS.md", render_talking_points(ctx))
    write("discovery_context_handoff.md", render_handoff(ctx))
    write("research_gaps.md", render_gaps(ctx))
    write("research_journal.md", render_journal(ctx))
    ctx.cited = set()
    write("EXECUTIVE_BRIEF.md", render_brief(ctx))
    write("coverage_report.md", render_coverage(ctx))
    cfg = {"company": ctx.company.to_dict(), "depth": run.opt.depth, "mode": run.opt.mode, "depth_config": run.depth_cfg,
           "config_version": run.config["config_version"], "scoring_version": run.config["scoring_version"],
           "score_weights": run.config["score_weights"], "allow_paid": run.opt.allow_paid,
           "provider_caps": run.ledger.caps, "_format": "JSON (valid YAML 1.2)"}
    write("research_config.yaml", json.dumps(cfg, ensure_ascii=False, indent=2))
    write_json(run_dir / "data" / "structured_profile.json", structured_profile(ctx))
    if run.prior:
        from .diff import change_log
        write("history/change_log.md", change_log(run_dir, Path(run.prior["dir"])))
    write("00_INDEX.md", render_index(ctx, sorted(files)))
    return files
