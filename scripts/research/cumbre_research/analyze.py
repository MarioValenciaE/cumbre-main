"""Rule-based extraction of claims, opportunity signals, observations, incidents
and relationship edges from one processed source.

Deterministic and explainable. The operating agent can add richer findings via
`ingest-findings`; those are validated against the same source registry.
Signals are observations that justify questions, never established problems.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .company import CO_CITIES, Company, name_tokens, strip_legal
from .extract import PageData, auth_surfaces, data_platform_mentions, social_platform, tech_fingerprints
from .safety import detect_injection, find_secrets, safe_excerpt
from .util import fold, host_of, parse_date, registrable_domain, short_hash

# ---------------------------------------------------------------------------
# Opportunity signal rules
# ---------------------------------------------------------------------------

SIGNAL_RULES = [
    {"id": "quote_request", "function": "sales / quoting", "strength": 2,
     "rx": r"cotiza(ci[oó]n|r|nos)|solicita (tu|una) cotizaci|request (a )?quote|get a quote|pide tu cotiz",
     "why": "Quotes appear to be requested manually; quoting workflows are often slow or spreadsheet-based.",
     "alt": "Quoting may already be automated behind the form.",
     "q": "How are quote requests received, priced and tracked today, and how long does a typical quote take?"},
    {"id": "whatsapp_ordering", "function": "sales channels / ordering", "strength": 2,
     "rx": r"pedidos? (por|v[ií]a|al) whatsapp|escr[ií]benos (al|por) whatsapp|order (via|on) whatsapp|wa\.me/|comprar por whatsapp",
     "why": "Orders or inquiries via WhatsApp can fragment order capture across chats.",
     "alt": "WhatsApp may be integrated with a CRM or used only for pre-sales.",
     "q": "What share of orders arrive by WhatsApp, and how do they reach inventory/billing?"},
    {"id": "email_ordering", "function": "sales channels / ordering", "strength": 1,
     "rx": r"(env[ií]a|envíanos|manda) (tu|su) (pedido|orden|solicitud) (al|por) (correo|email)|pedidos? (al|por) correo|send (your )?orders? (to|by) e-?mail",
     "why": "Email-based ordering suggests manual re-entry of orders.",
     "alt": "Email may feed an automated inbox parser.",
     "q": "How are emailed orders entered into your systems, and who does it?"},
    {"id": "pdf_catalog", "function": "catalog / product information", "strength": 2,
     "rx": r"descarga (nuestro|el) cat[aá]logo|cat[aá]logo (en )?pdf|lista de precios|price list|download (our )?catalog",
     "why": "PDF catalogs/price lists often mean product data is maintained by hand in several places.",
     "alt": "The PDF may be generated from a product database.",
     "q": "Where does the master product/price data live, and how are the PDF catalog and website kept in sync?"},
    {"id": "wholesale_distribution", "function": "B2B / distribution", "strength": 2,
     "rx": r"al por mayor|mayoristas?|distribuidor(es)? autorizado|s[eé] distribuidor|wholesale|reseller|red de distribuidores",
     "why": "Wholesale/distributor channels add customer-specific pricing, credit and order complexity.",
     "alt": "Distribution may be handled by a third party.",
     "q": "How are distributor price lists, credit and orders managed across customer groups?"},
    {"id": "multi_location", "function": "operations / multi-site", "strength": 2,
     "rx": r"\b\d{1,4}\s+(sedes|sucursales|tiendas|puntos de venta|almacenes|branches|stores|locations)\b|nuestras (sedes|tiendas|sucursales)",
     "why": "Several sites usually multiply inventory, reporting and coordination effort.",
     "alt": "Sites may already share one system.",
     "q": "How is inventory and sales information consolidated across locations?"},
    {"id": "order_tracking", "function": "fulfilment / customer service", "strength": 1,
     "rx": r"rastre(a|o) (tu|de) (pedido|env[ií]o)|seguimiento (de|a) (tu )?(pedido|env[ií]o)|track (your )?order|estado de (tu|su) pedido",
     "why": "Order status is customer-facing; manual tracking generates repetitive inquiries.",
     "alt": "Tracking may be fully handled by a carrier integration.",
     "q": "How many 'where is my order' inquiries do you get, and how are they answered?"},
    {"id": "scheduling", "function": "scheduling / bookings", "strength": 1,
     "rx": r"agenda (tu|una) (cita|visita|demo)|reserva (tu|una)|book (an? )?(appointment|demo|visit)|agendamiento|separa tu cita",
     "why": "Appointment handling may rely on calls/chat and manual calendars.",
     "alt": "An online scheduler may already be in use.",
     "q": "How are appointments booked, rescheduled and confirmed?"},
    {"id": "offline_payments", "function": "payments / collections", "strength": 2,
     "rx": r"consignaci[oó]n|transferencia bancaria|env[ií]a (el|tu) comprobante|pago contra entrega|cash on delivery|bank transfer|soporte de pago",
     "why": "Manual payment proofs imply reconciliation work and collection follow-up.",
     "alt": "Volume may be too small to matter.",
     "q": "How are incoming payments reconciled against orders/invoices, and how long does it take?"},
    {"id": "returns_warranty_pqrs", "function": "customer support / returns", "strength": 1,
     "rx": r"devoluci[oó]n(es)?|garant[ií]a|pqrs?|peticiones,? quejas|returns? polic|warranty claim|cambios y devoluciones",
     "why": "Returns, warranty and PQRS handling are process-heavy and regulated in Colombia.",
     "alt": "Policies may be published only for compliance, with low volume.",
     "q": "How are PQRS/warranty cases logged, assigned and closed, and are response times measured?"},
    {"id": "manual_forms", "function": "data capture", "strength": 1,
     "rx": r"(diligencia|descarga|llena) (el|este) formulario|formato (de )?(solicitud|inscripci[oó]n|vinculaci[oó]n)|download (the )?form|fill (out|in) the form|formulario en (pdf|excel|word)",
     "why": "Downloadable/manual forms suggest re-keying data into internal systems.",
     "alt": "Forms may be legally required in that format.",
     "q": "What happens to submitted forms — who re-types them and into which system?"},
    {"id": "supplier_onboarding", "function": "procurement / supplier management", "strength": 1,
     "rx": r"(registro|inscripci[oó]n|vinculaci[oó]n) de proveedores|s[eé] nuestro proveedor|supplier (registration|portal)|portal de proveedores",
     "why": "Supplier onboarding often involves document collection and manual validation.",
     "alt": "May be handled by an e-procurement platform.",
     "q": "How are new suppliers registered, validated and maintained?"},
    {"id": "customer_onboarding", "function": "customer onboarding / credit", "strength": 2,
     "rx": r"(solicitud|estudio) de cr[eé]dito|vinculaci[oó]n de clientes|abre tu cuenta|crea tu cuenta de cliente|credit application|customer onboarding",
     "why": "Customer onboarding/credit studies involve documents, approvals and follow-ups.",
     "alt": "Volume and process may already be optimized.",
     "q": "What steps and documents does onboarding a new business customer take, and how long?"},
    {"id": "inventory_mentions", "function": "inventory", "strength": 2,
     "rx": r"inventario|disponibilidad (de|en) (stock|inventario)|existencias|stock disponible|in stock|out of stock|agotado",
     "why": "Inventory visibility is a common source of errors and lost sales.",
     "alt": "Mention may be generic marketing copy.",
     "q": "How is stock availability determined and how often is it reconciled?"},
    {"id": "large_catalog", "function": "catalog / product information", "strength": 2,
     "rx": r"m[aá]s de \d[\d.,]* (referencias|productos|skus?|items)|over \d[\d,]* (products|skus|items)|\d[\d.,]{3,} referencias",
     "why": "Large catalogs make data quality, pricing updates and search harder.",
     "alt": "Catalog may be managed in a capable PIM/ERP.",
     "q": "How are product data and price updates maintained across the catalog and channels?"},
    {"id": "manufacturing_production", "function": "production / operations", "strength": 1,
     "rx": r"planta (de producci[oó]n|propia)|fabricamos|manufactur|producci[oó]n propia|our factory|nuestra f[aá]brica|maquila",
     "why": "Own production adds planning, materials and traceability processes.",
     "alt": "Production may be outsourced.",
     "q": "How is production planned against orders and inventory?"},
    {"id": "logistics_delivery", "function": "logistics / distribution", "strength": 1,
     "rx": r"env[ií]os? a (todo el pa[ií]s|nivel nacional|toda colombia)|flota propia|despachos|domicilios|nationwide shipping|own fleet|cobertura nacional",
     "why": "Delivery operations involve routing, proof of delivery and customer notifications.",
     "alt": "Logistics may be fully outsourced.",
     "q": "How are deliveries planned and confirmed, and how are customers notified?"},
    {"id": "growth_expansion", "function": "growth / change", "strength": 2,
     "rx": r"nueva sede|nueva tienda|apertura|inaugur|expansi[oó]n|llegamos a|new (store|branch|plant|location)|expanding|lanzamiento|launch(ed)?",
     "why": "Growth initiatives increase operational complexity and system strain.",
     "alt": "Announcement may be old or aspirational.",
     "q": "Which processes are under the most strain from recent growth?"},
    {"id": "tech_hiring", "function": "hiring / capability", "strength": 2,
     "rx": r"(analista|ingenier[oa]|desarrollador(a)?|developer|data|datos|bi|power bi|programador|sistemas|automatizaci[oó]n|transformaci[oó]n digital)\b.{0,40}(vacante|oferta|cargo|se requiere|buscamos|hiring|we.re looking)|"
           r"(vacante|cargo|buscamos|se requiere|hiring).{0,60}(analista de datos|desarrollador|power bi|excel avanzado|sql|ingeniero de sistemas|auxiliar de sistemas)",
     "why": "Hiring for data/systems roles hints at internal technology needs or initiatives.",
     "alt": "Routine replacement hiring.",
     "q": "What problem is the new data/systems role expected to solve, and what tools will they use?"},
    {"id": "manual_admin_hiring", "function": "hiring / back-office", "strength": 2,
     "rx": r"(auxiliar|asistente) (administrativ|contable|de facturaci[oó]n|de inventario|de cartera|de digitaci[oó]n)|digitador|data entry|facturador",
     "why": "Hiring for data entry/billing/inventory assistants can indicate manual back-office volume.",
     "alt": "Normal staffing for company size.",
     "q": "How much of the back-office team's time goes to re-typing or reconciling data?"},
    {"id": "excel_tools", "function": "reporting / data", "strength": 2,
     "rx": r"excel (avanzado|intermedio)|tablas din[aá]micas|macros|google sheets|hojas de c[aá]lculo|spreadsheet",
     "why": "Spreadsheet skills in job ads often mean spreadsheets are core operating tools.",
     "alt": "Standard office-skill requirement.",
     "q": "Which key reports or processes run on spreadsheets today?"},
    {"id": "reporting_kpis", "function": "reporting / decision-making", "strength": 1,
     "rx": r"informes? (de gesti[oó]n|mensual|semanal)|reportes? (de ventas|gerencial)|indicadores de gesti[oó]n|kpis?|dashboard|tablero",
     "why": "Reporting needs are explicit; consolidation is often manual.",
     "alt": "May refer to services they sell.",
     "q": "How long does it take to produce the main management report, and from which sources?"},
    {"id": "compliance_workflows", "function": "compliance / regulatory", "strength": 1,
     "rx": r"habeas data|ley 1581|sagrilaft|ptee|facturaci[oó]n electr[oó]nica|n[oó]mina electr[oó]nica|invima|registro sanitario|rut\b|siplaft|iso 9001|basc",
     "why": "Regulatory workflows create documentation and reporting obligations.",
     "alt": "Standard boilerplate.",
     "q": "Which compliance tasks (e.g. e-invoicing, data-protection requests, sanitary registrations) take the most manual effort?"},
    {"id": "customer_complaints", "function": "customer experience", "strength": 2,
     "rx": r"no (me )?(responden|contestan)|p[eé]sima atenci[oó]n|mala atenci[oó]n|nunca lleg[oó]|demora(ron|do)? .{0,20}(pedido|entrega)|never arrived|terrible service|no answer|poor customer service",
     "why": "Public complaints about responsiveness or delivery may reflect process gaps (anecdotal until patterns are shown).",
     "alt": "Isolated incident or review bias.",
     "q": "How do you measure response times and delivery issues, and what do the numbers show?"},
    {"id": "many_channels", "function": "omnichannel / CRM", "strength": 1,
     "rx": r"(whatsapp|facebook|instagram|correo|tel[eé]fono|chat).{0,40}(whatsapp|facebook|instagram|correo|tel[eé]fono|chat).{0,40}(whatsapp|facebook|instagram|correo|tel[eé]fono|chat)",
     "why": "Many contact channels can fragment customer history across tools.",
     "alt": "Channels may be unified in a CRM/inbox.",
     "q": "Where is the full history of a customer's conversations and orders kept?"},
    {"id": "ecommerce_present", "function": "ecommerce", "strength": 1,
     "rx": r"agregar al carrito|add to cart|carrito de compras|checkout|finalizar compra|tienda (en l[ií]nea|virtual|online)",
     "why": "An online store raises integration needs with inventory, billing and fulfilment.",
     "alt": "Store may be a small side channel.",
     "q": "Is the online store connected to inventory and invoicing, or are orders re-entered?"},
    {"id": "b2b_portal", "function": "B2B self-service", "strength": 1,
     "rx": r"portal (de )?(clientes|distribuidores|proveedores)|zona (de )?clientes|b2b|extranet",
     "why": "A B2B portal shows digital self-service; its depth and adoption are unknown.",
     "alt": "Portal may be fully adopted and adequate.",
     "q": "What can customers do in the portal today, and what still requires calling/emailing?"},
]
_SIGNAL_RES = [(r, re.compile(r["rx"], re.I)) for r in SIGNAL_RULES]

# ---------------------------------------------------------------------------
# Claim extractors
# ---------------------------------------------------------------------------

RX_FOUNDED = re.compile(r"(fundad[ao]s?|founded|desde|since|establecid[ao]|cread[ao]|naci[oó]|nace|inici[oó] (operaciones|labores)|constituid[ao])"
                        r"\s+(?:en\s+|in\s+)?(?:el\s+)?(?:a[ñn]o\s+)?(?P<year>19[0-9]{2}|20[0-2][0-9])\b", re.I)
RX_YEARS = re.compile(r"(m[aá]s de|over|more than|con)\s+(\d{1,3})\s+(a[ñn]os|years)\s+(de\s+)?(experiencia|trayectoria|en el mercado|of experience|in business)?", re.I)
RX_NIT = re.compile(r"\bNIT\.?\s*(no\.?|n[°º])?\s*[:#]?\s*(\d{3}[.\s]?\d{3}[.\s]?\d{3})\s*[-–]?\s*(\d)?", re.I)
RX_EMPLOYEES = re.compile(r"(m[aá]s de|over|more than|cerca de|about|around)?\s*(?<![\d.,])(\d[\d.,]{0,6})\+?\s+(empleados|colaboradores|trabajadores|employees|staff members|personas en nuestro equipo)", re.I)
RX_SITES = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{1,4})\s+(sedes|sucursales|tiendas|puntos de venta|almacenes|plantas|branches|stores|locations|warehouses)\b", re.I)
RX_OWNERSHIP = re.compile(r"(filial de|subsidiaria de|subsidiary of|parte del grupo|part of the .{2,40} group|propiedad de|owned by|"
                          r"empresa familiar|family[- ]owned|family business|cotiza en (la )?bolsa|listed on|bolsa de valores de colombia|"
                          r"respaldad[ao] por (fondos|inversionistas)|backed by|private equity|venture capital|cooperativa)", re.I)
RX_FUNDING = re.compile(r"(ronda (de )?(inversi[oó]n|financiaci[oó]n|serie [a-d]|semilla)|series [a-d] (round|funding)|seed round|"
                        r"levant[oó]|recaud[oó]|raised|cerr[oó] una ronda|inversi[oó]n de)\s.{0,80}?(us\$|usd|\$|cop|millones|million|mil millones|billion)", re.I)
RX_MNA = re.compile(r"(adquiri[oó]|adquisici[oó]n de|compr[oó] (la|el) (empresa|compa[ñn][ií]a)|acquired|acquisition of|fusi[oó]n con|merger with|se fusion[oó])", re.I)
ROLE_WORDS = (r"CEO|gerente general|fundador(?:a)?|cofundador(?:a)?|co-?founder|founder|presidente(?: ejecutivo)?|presidenta|"
              r"director(?:a)? (?:general|ejecutiv[oa])|managing director")
RX_LEADER = re.compile(r"(CEO|gerente general|fundador(?:a)?|cofundador(?:a)?|co-?founder|founder|presidente(?: ejecutivo)?|director(?:a)? (?:general|ejecutiv[oa])|managing director)"
                       r"(?:\s+de\s+[\w ]{2,30}?)?[,:]?\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+(?:de\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})")
RX_CERT = re.compile(r"\b(ISO\s?\d{4,5}(?::\d{4})?|BASC|HACCP|BPM|INVIMA|OEA|B Corp|FSSC\s?22000|GlobalG\.A\.P\.)\b", re.I)
RX_LEGAL_NAME = re.compile(r"([A-ZÁÉÍÓÚÑ0-9][\wÁÉÍÓÚÑáéíóúñ&.\-]*(?:\s+[\wÁÉÍÓÚÑáéíóúñ&.\-]+){0,6}?)\s+(S\.?\s?A\.?\s?S\.?|S\.A\.|LTDA\.?|Ltda\.?|S\. en C\.|E\.U\.)(?![\w])")
RX_RED_FLAGS = {
    "layoffs": re.compile(r"despidos? masivos?|recorte de personal|layoffs?|cut (\d+ )?jobs", re.I),
    "insolvency_or_reorganization": re.compile(r"reorganizaci[oó]n empresarial|ley 1116|insolvencia|liquidaci[oó]n (judicial|obligatoria|forzosa)|concordato|bankruptcy|chapter 11", re.I),
    "litigation": re.compile(r"demanda (contra|interpuesta)|proceso judicial|lawsuit|sued|fallo (contra|en contra)|tutela contra", re.I),
    "regulatory_action": re.compile(r"pliegos? de cargos|orden(ó)? (el )?cierre|cierre (temporal |parcial )?de (\w+ )?[aá]reas|"
                                    r"ministerio de trabajo .{0,80}(cierre|sanci|cargos|multa)|invima .{0,60}(alerta|retiro|sanci)|"
                                    r"sanci[oó]n (de|impuesta por) la (sic|superintendencia)|multa (de|impuesta)|superintendencia de industria y comercio .{0,60}(sancion|multa)|fined|regulatory penalty", re.I),
    "leadership_change": re.compile(r"nuev[oa] (gerente|ceo|president[ea])|renunci[oó]|stepped down|appointed (as )?(ceo|president)|nombrad[oa] (como )?(gerente|ceo|president[ea])", re.I),
}
LEGAL_POSTURE = [("judgement", r"fallo|sentencia|condenad|judgment|ruled"), ("resolution", r"resuelt|acuerdo|settled|archivad"),
                 ("filing", r"demanda|interpuso|filed|radic"), ("allegation", r"denuncia|acusa|alleg|presunt"),
                 ("rumor", r"rumor|se dice|supuestamente")]

INCIDENT_RX = re.compile(r"(brecha de (seguridad|datos)|filtraci[oó]n de (datos|informaci[oó]n)|fuga de (datos|informaci[oó]n)|ciberataque|ataque inform[aá]tico|"
                         r"ransomware|hacke(o|ad[oa]s?|aron)|hacked|incidente de (ciber)?seguridad|data breach|security incident|cyber ?attack|"
                         r"datos expuestos|exposed (data|database|bucket)|ca[ií]da (de la|del) (plataforma|servicio|sistema|p[aá]gina)|"
                         r"interrupci[oó]n del servicio|outage|service disruption|leaked credentials|credenciales filtradas)", re.I)
INCIDENT_TYPES = [("ransomware", r"ransomware"), ("data_breach", r"brecha|filtraci|fuga|breach|expuest|exposed"),
                  ("cyber_attack", r"ciberataque|ataque inform|cyber ?attack|hacke"),
                  ("credential_leak_report", r"credenciales|credentials"),
                  ("outage", r"ca[ií]da|interrupci|outage|disruption"), ("security_incident", r"incidente|incident")]
REGULATORY_PRIVACY_RX = re.compile(r"(sic|superintendencia de industria y comercio|data protection authority|autoridad de protecci[oó]n de datos)"
                                   r".{0,120}(sanci|multa|orden|fine|penalt)", re.I)

API_DOC_RX = re.compile(r"(developers?\.|/developers?|/api(-docs?)?(/|$)|swagger|openapi|redoc|postman\.com|/docs/api|api reference|documentaci[oó]n (de la )?api)", re.I)
RELATIONSHIP_WORDS = [("partner", r"aliad[oa]s?|partners?|socios estrat"), ("customer", r"nuestros clientes|clientes que conf|our clients|customers include"),
                      ("distributor", r"distribuidor|distributor|reseller"), ("supplier", r"proveedor|supplier"),
                      ("brand_of", r"nuestras marcas|our brands|marcas propias"), ("group_member", r"grupo empresarial|empresas del grupo|our group")]


@dataclass
class Findings:
    claims: list = field(default_factory=list)        # (key, data)
    signals: list = field(default_factory=list)       # (key, data)
    observations: list = field(default_factory=list)  # (kind, key, data)
    incidents: list = field(default_factory=list)     # (key, data)
    edges: list = field(default_factory=list)         # (key, data)
    new_leads: list = field(default_factory=list)     # (kind, value)


def _sentence(text: str, start: int, end: int, limit: int = 260) -> str:
    s = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    e_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    e = min(e_candidates) if e_candidates else len(text)
    return safe_excerpt(text[s + 1: e + 1], limit)


def _claim(f: Findings, company: Company, ctype: str, value: str, statement: str, src: dict,
           section: str, base_status: str = "inferred", extra: dict | None = None):
    norm = fold(str(value))[:120]
    key = f"{company.slug}|{ctype}|{norm}"
    f.claims.append((key, {
        "claim_type": ctype, "subject": company.slug, "value": value, "statement": statement,
        "fact_status": base_status, "supporting_source_ids": [src["source_id"]], "contradicting_source_ids": [],
        "locations": [f"{src['source_id']}#{section[:60]}" if section else src["source_id"]],
        "extracted_at_utc": src.get("retrieved_at_utc"), "extraction_method": "rule:" + ctype,
        "event_date": None, **(extra or {}),
    }))


def analyze_source(company: Company, src: dict, blocks: list[tuple[str, str]], page: PageData | None,
                   headers: dict, run_date: dt.date, config: dict) -> Findings:
    f = Findings()
    sid = src["source_id"]
    stype = src.get("source_type", "unknown")
    official = stype.startswith("first_party")
    full_text = "\n".join(t for _, t in blocks)
    url = src["url"]

    # --- untrusted content checks -------------------------------------------------
    inj = detect_injection(full_text + " " + (page.raw_html_sample[:50000] if page else ""))
    if inj:
        f.observations.append(("untrusted_content_flag", sid, {
            "source_ids": [sid], "flag": "instruction_like_text_detected", "count": len(inj),
            "handling": "text neutralized; never treated as instructions"}))
    secret_types = find_secrets(full_text)
    if secret_types:
        f.observations.append(("incidental_exposure", sid, {
            "source_ids": [sid], "finding_type": "possible_incidental_exposure",
            "redacted_summary": f"Secret-like string pattern(s) of type {', '.join(secret_types)} appeared in public content. "
                                "Values were not stored.",
            "status": "UNVERIFIED", "owner_validation_needed": True,
            "recommended_action": "Owner-authorized verification and responsible private notification."}))

    # --- signals (one record per rule per block; one page can yield many) ----------
    for section, text in blocks:
        for rule, rx in _SIGNAL_RES:
            m = rx.search(text)
            if not m:
                continue
            if rule["id"] == "customer_complaints" and official:
                continue
            if not official and not company.name_match(text)["exact_phrase"]:
                continue  # sidebars/related headlines on third-party pages are not about this company
            stmt = _sentence(text, m.start(), m.end())
            key = f"{company.slug}|{rule['id']}|{short_hash(fold(stmt), 10)}"
            f.signals.append((key, {
                "signal_type": rule["id"], "business_function": rule["function"], "strength": rule["strength"],
                "source_ids": [sid], "location": f"{sid}#{section[:60]}" if section else sid, "section": section[:120],
                "observed_statement": stmt,
                "why_it_matters": rule["why"], "alternative_interpretations": rule["alt"],
                "meeting_question": rule["q"], "evidence_basis": "public_web",
                "caveat": "Signal from public content; not an established client problem.",
                "source_type": stype,
            }))
    # cap per (rule, source) to keep catalogs readable but retain variety
    seen, kept = {}, []
    for key, s in f.signals:
        k = (s["signal_type"], sid)
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= 3:
            kept.append((key, s))
    f.signals = kept

    # --- claims ----------------------------------------------------------------------
    name_present = company.name_match(full_text)["exact_phrase"] or official
    for section, text in blocks:
        if not (official or company.name_match(text)["token_ratio"] >= 0.5 or name_present):
            continue
        for m in RX_FOUNDED.finditer(text):
            year = m.group("year")
            verb = fold(m.group(1))
            sentence = _sentence(text, m.start(), m.end(), 400)
            loose = verb.startswith(("desde", "since"))
            if loose and (stype != "first_party_website" or not company.name_match(sentence)["exact_phrase"]):
                continue  # "desde 2009" is usually not a founding date (and never in reports/PDFs)
            if not official and not company.name_match(sentence)["exact_phrase"]:
                continue  # third-party pages: the company must be named in the same sentence
            if int(year) <= run_date.year:
                _claim(f, company, "founding_year", year, f"Source states the company was founded/started in {year}.",
                       src, section, extra={"event_date": year, "observed_statement": _sentence(text, m.start(), m.end())})
        for m in RX_YEARS.finditer(text):
            if m.group(5) or "trayectoria" in m.group(0).lower():
                n = int(m.group(2))
                if 0 < n < 150:
                    _claim(f, company, "years_in_business_claim", str(n),
                           f"Source claims more than {n} years of experience/operation (as of its unknown publication date).",
                           src, section, extra={"observed_statement": _sentence(text, m.start(), m.end())})
        for m in RX_NIT.finditer(text):
            if not (official or stype in ("official_registry", "government_record")
                    or company.name_match(_sentence(text, m.start(), m.end(), 300))["exact_phrase"]):
                continue  # publishers print their own NIT in footers
            digits = re.sub(r"\D", "", m.group(2))
            value = digits + (f"-{m.group(3)}" if m.group(3) else "")
            _claim(f, company, "tax_id_nit", value, f"Source lists NIT {value}.", src, section)
        for m in RX_EMPLOYEES.finditer(text):
            _claim(f, company, "employee_count_statement", m.group(0).strip(),
                   "Source states a staff size; this is a public statement, not a verified payroll count.",
                   src, section, extra={"qualifier": (m.group(1) or "").strip() or None,
                                        "observed_statement": _sentence(text, m.start(), m.end())})
        for m in RX_SITES.finditer(text):
            _claim(f, company, "site_count_statement", f"{m.group(1)} {m.group(2).lower()}",
                   f"Source mentions {m.group(1)} {m.group(2).lower()}.", src, section,
                   extra={"observed_statement": _sentence(text, m.start(), m.end())})
        for m in RX_OWNERSHIP.finditer(text):
            _claim(f, company, "ownership_indicator", m.group(0).lower()[:80],
                   "Source contains an ownership/structure indicator (classification requires corroboration).",
                   src, section, extra={"observed_statement": _sentence(text, m.start(), m.end())})
        for m in RX_FUNDING.finditer(text):
            _claim(f, company, "funding_report", _sentence(text, m.start(), m.end(), 160),
                   "Source reports funding/investment; news figures are not audited financial data.", src, section,
                   extra={"observed_statement": _sentence(text, m.start(), m.end())})
        for m in RX_MNA.finditer(text):
            _claim(f, company, "mna_report", _sentence(text, m.start(), m.end(), 160),
                   "Source mentions an acquisition/merger involving or near the company (direction to verify).",
                   src, section, extra={"observed_statement": _sentence(text, m.start(), m.end())})
        for m in RX_LEADER.finditer(text):
            role, person = m.group(1), m.group(2)
            if company.name_match(person)["token_ratio"] > 0 or re.search(r"(?i)fundaci|grupo|s\.a|ltda|ministerio", person):
                continue
            if not official:
                after = fold(text[m.start(): m.end() + 60])
                cut = max(text.rfind(". ", 0, m.start()), text.rfind("\n", 0, m.start()), m.start() - 60)
                before = text[max(0, cut): m.start()]  # same sentence, close to the role
                of_company = re.search(r"\b(de|of|en|at)\s+(la\s+)?(compania\s+)?" + re.escape(strip_legal(company.name)), after)
                if not of_company and not company.name_match(before)["exact_phrase"]:
                    continue  # e.g. "el presidente Gustavo Petro" in an article about the company
            _claim(f, company, "leadership_role", f"{person} — {role}",
                   f"Source names {person} in the professional role '{role}'.", src, section,
                   extra={"person_name": person, "role": role})
        # "Ernesto Fajardo, presidente de Alpina" (person first, company named)
        rx_after = re.compile(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3}),\s+((?i:" + ROLE_WORDS +
                              r"))\s+(?:de|of)\s+(?:la\s+)?(?i:" + re.escape(company.name) + ")")
        for m in rx_after.finditer(text):
            person, role = m.group(1), m.group(2)
            if company.name_match(person)["token_ratio"] == 0:
                _claim(f, company, "leadership_role", f"{person} — {role}",
                       f"Source names {person} in the professional role '{role}'.", src, section,
                       extra={"person_name": person, "role": role})
        for m in RX_CERT.finditer(text):
            _claim(f, company, "certification_or_registration", m.group(1).upper(),
                   f"Source mentions {m.group(1)} (scope and validity unverified).", src, section)
        for m in RX_LEGAL_NAME.finditer(text):
            candidate = (m.group(1) + " " + m.group(2)).strip()
            first_tok = (name_tokens_of(company) or [""])[0]
            pos = fold(candidate).find(first_tok) if first_tok else -1
            if pos > 0:  # drop leading words such as "Información sobre", "La empresa"
                candidate = candidate[pos:]
            if company.name_match(candidate)["token_ratio"] >= 0.5 and len(candidate) < 90:
                _claim(f, company, "legal_name_variant", candidate, f"Source uses the legal-form name '{candidate}'.",
                       src, section)
                f.new_leads.append(("alias", candidate))
        if company.country == "CO":
            for city in CO_CITIES:
                if re.search(r"(?<![a-záéíóú])" + re.escape(city) + r"(?![a-záéíóú])", text) and official:
                    _claim(f, company, "location_mention", city, f"Official content mentions {city}.", src, section)
        for flag, rx in RX_RED_FLAGS.items():
            m = rx.search(text)
            if m and not official and company.name_match(text)["token_ratio"] >= 0.5:
                posture = next((p for p, prx in LEGAL_POSTURE if re.search(prx, text, re.I)), "unclassified")
                _claim(f, company, "corporate_red_flag", f"{flag}: {_sentence(text, m.start(), m.end(), 140)}",
                       "Sensitive corporate event reported by a third party; attribute precisely, do not generalize.",
                       src, section, base_status="hypothesis",
                       extra={"red_flag_type": flag, "legal_posture": posture,
                              "observed_statement": _sentence(text, m.start(), m.end())})

    # offerings from product/service headings on official pages
    if official and page is not None and src.get("url_category") in ("product_service", "commerce", "home", "company_about"):
        for tag, heading in page.headings[:25]:
            h = safe_excerpt(heading, 90)
            if (3 <= len(h) <= 90 and tag in ("h1", "h2", "h3")
                    and not company.name_match(h)["exact_phrase"]
                    and not re.search(r"(contact|nosotros|quienes somos|about|siguenos|follow|newsletter|menu|search|buscar|cookies|bienvenid|welcome|^descubre|^conoce|^explora|^encuentra|^mira|^ver |^discover|^explore|^learn|^shop|^suscri)", fold(h))):
                _claim(f, company, "offering_or_topic", h, f"Official page heading: '{h}'.", src, h,
                       base_status="confirmed", extra={"self_reported": True,
                                                       "page_category": src.get("url_category")})

    # news/event record (publication date != event date)
    if stype in ("reputable_news", "news_other") and company.name_match(full_text)["exact_phrase"]:
        title = safe_excerpt(src.get("title") or "", 160)
        _claim(f, company, "news_mention", title or url, "Independent coverage mentions the company.", src, "",
               extra={"published_at": src.get("published_at"), "event_date": None,
                      "date_note": "publication date shown; actual event date must be read from the article"})

    # --- incidents (published reports only) -------------------------------------------
    for section, text in blocks:
        m = INCIDENT_RX.search(text)
        if not m:
            continue
        nm = company.name_match(text)
        if not (nm["exact_phrase"] or (official and nm["token_ratio"] >= 0.5)):
            continue  # cross-company collision guard: the incident text must name this company
        if src.get("identity_match_status") == "different_entity":
            continue
        itype = next((t for t, rx in INCIDENT_TYPES if re.search(rx, m.group(0), re.I)), "security_incident")
        if official:
            status = "CORPORATE_NOTICE"
        elif stype in ("reputable_news", "government_record", "official_registry"):
            status = "REPORTED_BY_CREDIBLE_SOURCE"
        elif stype == "forum_community":
            status = "UNVERIFIED_RUMOR"
        else:
            status = "UNVERIFIED_REPORT"
        key = f"{company.slug}|{itype}|{sid}"
        f.incidents.append((key, {
            "finding_type": itype, "company_identity_match": src.get("identity_match_status", "unknown"),
            "discovery_method": "passive_public_source_review", "published_event_date": src.get("published_at"),
            "published_date_basis": src.get("published_at_basis"), "event_date": None,
            "retrieved_at_utc": src.get("retrieved_at_utc"), "source_ids": [sid],
            "evidence_strength": src.get("reliability_score"), "status": status,
            "severity_basis": "not_stated_by_source", "legal_context": None,
            "redacted_summary": _sentence(text, m.start(), m.end(), 220),
            "owner_validation_needed": True,
            "scope_caveat": "A reported incident does not necessarily affect every domain, product or current user.",
        }))
        if REGULATORY_PRIVACY_RX.search(text):
            f.incidents[-1][1]["legal_context"] = "possible regulatory/privacy decision mentioned — verify issuing authority and status"

    # --- observations from official pages -------------------------------------------------
    if page is not None:
        for t in (tech_fingerprints(page, headers, full_text) if official else []):
            status = "INFERRED"
            f.observations.append(("tech_fingerprint", f"{t['technology']}|{registrable_domain(host_of(url))}", {
                **t, "source_ids": [sid], "observed_at_utc": src.get("retrieved_at_utc"), "status": status,
                "domain": host_of(url),
                "caveat": "Passive fingerprint: an indicator, not proof of architecture, version or security state."}))
        auth = auth_surfaces(page)
        if official:
            for s in auth["surfaces"]:
                f.observations.append(("auth_surface", f"{canonical_host(s['surface_url'])}|{fold(s['label'])[:40]}", {
                    **s, "source_ids": [sid], "domain": host_of(s["surface_url"]),
                    "caveat": "Visible surface only; internal authentication design is unknown."}))
            for meth in auth["method_observations"]:
                proof = "VISIBLE_OPTION" if meth.endswith("_visible") else "DOCUMENTED_MENTION"
                f.observations.append(("auth_method", f"{meth}|{host_of(url)}", {
                    "method": meth, "evidence_class": proof, "source_ids": [sid], "domain": host_of(url),
                    "caveat": ("A visible 'Sign in with X' button shows the option exists; it does not reveal "
                               "OAuth/OIDC configuration or password storage.") if proof == "VISIBLE_OPTION" else
                              ("Documentation mentions this method; deployment scope is unverified.")}))
        for link, anchor in page.links:
            plat = social_platform(link)
            if plat and not official and company.name_match(link.replace("/", " ").replace("@", " ")
                                                              .replace("-", " ").replace("_", " "))["token_ratio"] < 1.0:
                plat = None  # third-party page linking its own profiles/share buttons: not the company's
            if plat:
                cls = "official_linked_from_company_site" if official else "third_party_or_unverified"
                f.observations.append(("social_profile", f"{plat}|{canonical_host(link)}{_path(link)}", {
                    "platform": plat, "url": link, "classification": cls, "affiliation_evidence": sid if official else None,
                    "source_ids": [sid], "followers": "UNKNOWN", "last_verified_activity": "UNKNOWN",
                    "access_note": "Counts/activity not collected (no logged-in or restricted access)."}))
            if official and API_DOC_RX.search(link) and company.is_official_url(link):
                f.observations.append(("public_api_docs", canonical_host(link) + _path(link), {
                    "url": link, "label": safe_excerpt(anchor, 80), "source_ids": [sid],
                    "caveat": "Published API docs do not prove internal use or reveal private APIs."}))
            if official and not company.is_official_url(link) and not plat and link.startswith("http"):
                ctx = fold(anchor)
                for rel, rx in RELATIONSHIP_WORDS:
                    if re.search(rx, ctx) or re.search(rx, fold(src.get("title") or "")):
                        target = registrable_domain(host_of(link))
                        f.edges.append((f"{company.slug}|{rel}|{target}", {
                            "from": company.slug, "to": target, "edge_type": rel, "direction": "from->to",
                            "validity_period": "as observed on retrieval date", "source_ids": [sid],
                            "confidence": "low", "basis": "outbound link in relationship context (not name similarity)"}))
                        break
            if official and company.is_official_url(link) is False and link.startswith("http"):
                pass
        if official and src.get("url_category") == "legal":
            low = fold(full_text)
            f.observations.append(("data_posture", f"privacy|{sid}", {
                "source_ids": [sid], "privacy_notice": True,
                "mentions_habeas_data_or_1581": bool(re.search(r"habeas data|1581", low)),
                "mentions_cookies": "cookie" in low,
                "caveat": "Describes what the company publishes about data handling, not its internal practices."}))
        if official and config["fetch"].get("observe_security_headers") and src.get("url_category") == "home":
            hdr = {k: headers.get(k) for k in ("strict-transport-security", "content-security-policy",
                                               "x-frame-options", "x-content-type-options", "referrer-policy")}
            f.observations.append(("security_headers", host_of(url), {
                "source_ids": [sid], "host": host_of(url), "present": sorted(k for k, v in hdr.items() if v),
                "absent": sorted(k for k, v in hdr.items() if not v),
                "caveat": "Ordinary response headers from a normal page view; not a security assessment."}))

    # data platforms mentioned in text (evidence class depends on the source)
    for name, sentence in data_platform_mentions(full_text):
        if not company.name_match(full_text)["token_ratio"] >= 0.5 and not official:
            continue
        if stype == "job_board" or re.search(r"(vacante|requisitos|experiencia en|conocimientos en|requirements|experience with)", fold(sentence)):
            ev = "INDIRECT_SIGNAL"
        elif official and re.search(r"(usamos|utilizamos|nuestra plataforma|migramos|we use|we migrated|our stack|built on|corre sobre)", fold(sentence)):
            ev = "SELF_DISCLOSED"
        elif stype == "web_archive":
            ev = "HISTORICAL_ONLY"
        elif stype in ("reputable_news", "news_other") or (not official and stype != "unknown"):
            ev = "CREDIBLE_THIRD_PARTY_REPORT" if stype == "reputable_news" else "INDIRECT_SIGNAL"
        else:
            ev = "INDIRECT_SIGNAL"
        f.observations.append(("tech_disclosure", f"{name}|{ev}|{sid}", {
            "technology": name, "evidence_class": ev, "source_ids": [sid], "statement": safe_excerpt(sentence, 220),
            "caveat": {"INDIRECT_SIGNAL": "A job ad or passing mention is not proof of production use.",
                       "SELF_DISCLOSED": "Company states usage; scope/version unknown.",
                       "CREDIBLE_THIRD_PARTY_REPORT": "Reported by a third party; confirm with the company.",
                       "HISTORICAL_ONLY": "Seen only in archived content; may no longer apply."}.get(ev, "")}))
    return f


def name_tokens_of(company: Company) -> list[str]:
    return name_tokens(company.name)


def canonical_host(url: str) -> str:
    return host_of(url)


def _path(url: str) -> str:
    from urllib.parse import urlsplit
    return (urlsplit(url).path or "/").rstrip("/")[:80]


def detect_contradictions(claims: list[dict]) -> list[tuple[str, list[str]]]:
    """Single-valued claim types with differing values across sources."""
    single = {"founding_year", "tax_id_nit"}
    by_type: dict[str, list[dict]] = {}
    for c in claims:
        if c["claim_type"] in single:
            by_type.setdefault(c["claim_type"], []).append(c)
    out = []
    for ctype, items in by_type.items():
        values = {c["value"] for c in items}
        if ctype == "tax_id_nit":
            values = {v.split("-")[0] for v in values}
        if len(values) > 1:
            out.append((ctype, [c["claim_id"] for c in items]))
    return out
