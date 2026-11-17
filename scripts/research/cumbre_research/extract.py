"""Content extraction: HTML structure, dates, links, sitemaps, PDFs, URL categories,
passive technology fingerprints and visible authentication surfaces.

All page content is untrusted; this module only parses it.
"""

from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .util import canonical_url, fold, host_of, registrable_domain

SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "dd", "dt", "blockquote",
              "figcaption", "summary", "label", "button", "a", "div", "section", "article", "span", "option"}


@dataclass
class Block:
    text: str
    section: str
    tag: str


@dataclass
class PageData:
    url: str
    title: str = ""
    lang: str = ""
    meta: dict = field(default_factory=dict)
    jsonld: list = field(default_factory=list)
    links: list = field(default_factory=list)            # (absolute_url, anchor_text)
    blocks: list = field(default_factory=list)           # Block
    headings: list = field(default_factory=list)
    script_srcs: list = field(default_factory=list)
    inline_script_sample: str = ""
    forms: list = field(default_factory=list)            # dicts: action, has_password, inputs
    time_datetimes: list = field(default_factory=list)
    raw_html_sample: str = ""

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


class _Parser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.page = PageData(url=base_url)
        self._skip = 0
        self._in_title = False
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self._script_buf: list[str] = []
        self._buf: list[str] = []
        self._section = ""
        self._heading_tag: str | None = None
        self._heading_buf: list[str] = []
        self._link: tuple[str, list[str]] | None = None
        self._form: dict | None = None

    def _flush(self, tag: str = "") -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if len(text) >= 2:
            self.page.blocks.append(Block(text=text, section=self._section, tag=tag))

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.page.lang = a["lang"][:10]
        if tag == "script":
            if "ld+json" in a.get("type", ""):
                self._in_jsonld = True
            elif a.get("src"):
                self.page.script_srcs.append(urljoin(self.base, a["src"]))
            self._skip += 1
            return
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or a.get("http-equiv") or "").lower()
            if key and a.get("content"):
                self.page.meta.setdefault(key, a["content"][:500])
        if tag == "link" and a.get("href"):
            rel = a.get("rel", "").lower()
            if rel in ("canonical", "alternate", "me", "author"):
                self.page.meta.setdefault(f"link:{rel}", urljoin(self.base, a["href"]))
            if "stylesheet" in rel:
                self.page.script_srcs.append(urljoin(self.base, a["href"]))
        if tag == "time" and a.get("datetime"):
            self.page.time_datetimes.append(a["datetime"])
        if tag in ("h1", "h2", "h3", "h4"):
            self._flush()
            self._heading_tag = tag
            self._heading_buf = []
        if tag == "a" and a.get("href"):
            href = a["href"].strip()
            if not href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
                self._link = (urljoin(self.base, href), [])
            elif href.lower().startswith(("mailto:", "tel:")):
                self.page.links.append((href[:120], "contact-link"))
        if tag == "form":
            self._form = {"action": urljoin(self.base, a.get("action", "")), "has_password": False,
                          "inputs": [], "method": a.get("method", "get").lower()}
        if tag == "input" and self._form is not None:
            itype = a.get("type", "text").lower()
            if itype == "password":
                self._form["has_password"] = True
            self._form["inputs"].append((itype, (a.get("name") or a.get("id") or "")[:40]))
        if tag == "img" and a.get("alt"):
            self._buf.append(" " + a["alt"] + " ")
        if tag in ("br", "p", "li", "tr", "div", "section", "article"):
            self._flush()

    def handle_endtag(self, tag):
        if tag == "script":
            if self._in_jsonld:
                raw = "".join(self._jsonld_buf).strip()
                self._jsonld_buf = []
                self._in_jsonld = False
                try:
                    data = json.loads(raw)
                    self.page.jsonld.extend(data if isinstance(data, list) else [data])
                except (json.JSONDecodeError, ValueError):
                    pass
            self._skip = max(0, self._skip - 1)
            return
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag == self._heading_tag:
            heading = re.sub(r"\s+", " ", "".join(self._heading_buf)).strip()
            if heading:
                self._section = heading[:120]
                self.page.headings.append((tag, heading[:200]))
            self._heading_tag = None
            self._buf = [heading]
            self._flush(tag)
            return
        if tag == "a" and self._link:
            url, parts = self._link
            self.page.links.append((url, re.sub(r"\s+", " ", "".join(parts)).strip()[:150]))
            self._link = None
        if tag == "form" and self._form is not None:
            self.page.forms.append(self._form)
            self._form = None
        if tag in BLOCK_TAGS:
            self._flush(tag)

    def handle_data(self, data):
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._skip:
            if len(self.page.inline_script_sample) < 60000:
                self.page.inline_script_sample += data[:4000]
            return
        if self._in_title:
            self.page.title += data
            return
        if self._heading_tag:
            self._heading_buf.append(data)
        else:
            self._buf.append(data)
        if self._link:
            self._link[1].append(data)


def parse_html(html: str, url: str) -> PageData:
    parser = _Parser(url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML must never crash a run
        pass
    parser._flush()
    page = parser.page
    page.title = re.sub(r"\s+", " ", page.title).strip()[:300]
    page.raw_html_sample = html[:200000]
    # de-duplicate repeated blocks (menus, footers)
    seen, uniq = set(), []
    for b in page.blocks:
        key = fold(b.text)
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    page.blocks = uniq
    return page


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

PUBLISHED_META = ["article:published_time", "datepublished", "date", "dc.date", "dc.date.issued",
                  "pubdate", "publishdate", "og:published_time", "sailthru.date", "parsely-pub-date"]
MODIFIED_META = ["article:modified_time", "datemodified", "og:updated_time", "last-modified", "dc.date.modified"]


def _jsonld_items(jsonld: list) -> list[dict]:
    items = []
    for item in jsonld:
        if isinstance(item, dict):
            items.append(item)
            for sub in item.get("@graph", []) or []:
                if isinstance(sub, dict):
                    items.append(sub)
    return items


def extract_dates(page: PageData, headers: dict) -> dict:
    """Return published/modified dates with their basis. Never invents a date."""
    out = {"published_at": None, "published_at_basis": "not_available",
           "modified_at": None, "modified_at_basis": "unknown"}
    for item in _jsonld_items(page.jsonld):
        if item.get("datePublished") and not out["published_at"]:
            out["published_at"], out["published_at_basis"] = str(item["datePublished"])[:25], "jsonld_datePublished"
        if item.get("dateModified") and not out["modified_at"]:
            out["modified_at"], out["modified_at_basis"] = str(item["dateModified"])[:25], "jsonld_dateModified"
    for key in PUBLISHED_META:
        if not out["published_at"] and page.meta.get(key):
            out["published_at"], out["published_at_basis"] = page.meta[key][:25], f"meta:{key}"
    for key in MODIFIED_META:
        if not out["modified_at"] and page.meta.get(key):
            out["modified_at"], out["modified_at_basis"] = page.meta[key][:25], f"meta:{key}"
    if not out["published_at"] and page.time_datetimes:
        out["published_at"], out["published_at_basis"] = page.time_datetimes[0][:25], "html_time_element"
    if not out["published_at"]:
        m = re.search(r"/(20\d{2}|19\d{2})/(0[1-9]|1[0-2])/(?:([0-2]\d|3[01])/)?", page.url)
        if m:
            day = m.group(3) or "01"
            out["published_at"] = f"{m.group(1)}-{m.group(2)}-{day}"
            out["published_at_basis"] = "url_path_pattern" + ("" if m.group(3) else "_month_only")
    if not out["modified_at"] and headers.get("last-modified"):
        out["modified_at"], out["modified_at_basis"] = headers["last-modified"], "http_last_modified_header"
    return out


# --------------------------------------------------------------------------
# Sitemaps, PDFs
# --------------------------------------------------------------------------

def parse_sitemap(xml_text: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Return ([(url, lastmod)], [child sitemap urls]). Tolerates namespaces."""
    urls, children = [], []
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "ignore"))
    except ET.ParseError:
        return urls, children
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("url", "sitemap"):
            loc = lastmod = None
            for child in el:
                ctag = child.tag.split("}")[-1]
                if ctag == "loc" and child.text:
                    loc = child.text.strip()
                elif ctag == "lastmod" and child.text:
                    lastmod = child.text.strip()
            if loc:
                (urls if tag == "url" else children).append((loc, lastmod) if tag == "url" else loc)
    return urls, children


def pdf_text(data: bytes, max_pages: int = 30) -> tuple[str, str]:
    """Return (text, status). pypdf is optional."""
    try:
        import pypdf  # type: ignore
    except ImportError:
        return "", "metadata_only_pypdf_not_installed"
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts = [(p.extract_text() or "") for p in reader.pages[:max_pages]]
        return "\n".join(parts), "extracted_pdf"
    except Exception as exc:  # noqa: BLE001
        return "", f"pdf_parse_error:{type(exc).__name__}"


# --------------------------------------------------------------------------
# URL categories
# --------------------------------------------------------------------------

URL_CATEGORIES = [
    ("legal", r"(terminos|terms|condiciones|legal|aviso-legal|politica(s)?-de-(privacidad|datos|cookies)|privacy|privacidad|cookies|habeas|tratamiento-de-datos)"),
    ("policy_compliance", r"(sagrilaft|etica|ethics|compliance|cumplimiento|sostenibilidad|sustainability|calidad|iso-?\d+|certific)"),
    ("recruitment", r"(trabaja|empleo|careers?|jobs?|vacantes|talento|join-us|unete)"),
    ("support", r"(soporte|support|ayuda|help|faq|preguntas|pqrs?|garantia|devolucion|returns?|servicio-al-cliente)"),
    ("technical_help", r"(docs?|documentation|developers?|api|sdk|manual|tutorial|guia)"),
    ("b2b_b2c_portal", r"(login|signin|sign-in|iniciar-sesion|mi-cuenta|my-account|account|portal|clientes|proveedores|intranet|registro|register|signup)"),
    ("commerce", r"(tienda|shop|store|cart|carrito|checkout|comprar|pedido|order|precio|pricing|cotiza|quote|distribuid|mayorist|wholesale|dealer)"),
    ("product_service", r"(producto|product|servicio|service|solucion|solution|catalogo|catalog|linea|categoria|category|collection|coleccion|item|sku|referencia)"),
    ("blog_news", r"(blog|noticia|news|prensa|press|articulo|article|post|comunicado|eventos?|20\d{2}/)"),
    ("company_about", r"(nosotros|about|quienes-somos|historia|history|empresa|company|mision|vision|equipo|team|who-we-are)"),
    ("logistics", r"(envio|shipping|despacho|entrega|delivery|rastreo|tracking|logistica)"),
    ("resources_downloads", r"(descarga|download|recursos|resources|brochure|ficha|\.pdf$|\.csv$|\.xlsx?$)"),
    ("integrations", r"(integracion|integration|partners?|aliados|marketplace|apps?)"),
    ("location", r"(sedes|ubicacion|locations?|tiendas|sucursales|puntos-de-venta|stores|donde-estamos|mapa)"),
    ("contact", r"(contacto|contact|escribenos|cotizacion)"),
]
_CAT_RES = [(name, re.compile(rx)) for name, rx in URL_CATEGORIES]

HIGH_VALUE_CATEGORIES = {"company_about", "product_service", "b2b_b2c_portal", "support", "recruitment",
                         "technical_help", "location", "contact", "legal", "commerce", "integrations",
                         "logistics", "resources_downloads", "blog_news", "policy_compliance"}


def classify_url(url: str) -> str:
    parts = urlsplit(url)
    path = fold(parts.path + ("?" + parts.query if parts.query else ""))
    if path in ("", "/"):
        return "home"
    for name, rx in _CAT_RES:
        if rx.search(path):
            return name
    return "unknown"


def url_pattern(url: str) -> str:
    """Template for repetitive catalogs: /productos/123-x -> /productos/*"""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        return "/"
    shape = segs[:-1] + ["*"] if len(segs) > 1 else ["*"]
    return host_of(url) + "/" + "/".join(shape)


# --------------------------------------------------------------------------
# Social / community links
# --------------------------------------------------------------------------

SOCIAL_HOSTS = {
    "facebook.com": "facebook", "fb.com": "facebook", "instagram.com": "instagram",
    "linkedin.com": "linkedin", "twitter.com": "x", "x.com": "x", "youtube.com": "youtube",
    "youtu.be": "youtube", "tiktok.com": "tiktok", "github.com": "github", "reddit.com": "reddit",
    "discord.gg": "discord", "discord.com": "discord", "pinterest.com": "pinterest",
    "wa.me": "whatsapp", "api.whatsapp.com": "whatsapp", "t.me": "telegram",
    "g.page": "google_business_profile", "maps.app.goo.gl": "google_business_profile",
    "apps.apple.com": "app_store", "play.google.com": "google_play", "medium.com": "blog",
    "slack.com": "slack", "threads.net": "threads",
}
SOCIAL_SKIP_PATHS = re.compile(r"/(sharer|share|sharing|share-offsite|intent|dialog|plugins|login|signup|hashtag|search)(/|\.|$|\?)", re.I)


def social_platform(url: str) -> str | None:
    host = host_of(url)
    for key, name in SOCIAL_HOSTS.items():
        if host == key or host.endswith("." + key):
            parts = urlsplit(url)
            if SOCIAL_SKIP_PATHS.search(parts.path or "") or "text=" in (parts.query or "") or "url=" in (parts.query or ""):
                return None  # share buttons, not profiles
            return name
    return None


# --------------------------------------------------------------------------
# Passive technology fingerprints (indicators, not architecture maps)
# --------------------------------------------------------------------------

TECH_RULES = [
    # name, category, where (html|header|script), regex
    ("WordPress", "cms", "html", r"wp-content/|wp-includes/|<meta[^>]+generator[^>]+wordpress"),
    ("WooCommerce", "ecommerce", "html", r"woocommerce"),
    ("Shopify", "ecommerce", "html", r"cdn\.shopify\.com|shopify\.theme|myshopify\.com"),
    ("VTEX", "ecommerce", "html", r"vteximg|vtexassets|vtex\.com|\.vtexcommercestable"),
    ("Magento / Adobe Commerce", "ecommerce", "html", r"mage/cookies|magento|/static/version\d+/frontend/"),
    ("PrestaShop", "ecommerce", "html", r"prestashop"),
    ("Wix", "cms", "html", r"static\.wixstatic\.com|wix\.com website builder|x-wix"),
    ("Squarespace", "cms", "html", r"squarespace\.com|static1\.squarespace"),
    ("Joomla", "cms", "html", r"/media/jui/|<meta[^>]+generator[^>]+joomla"),
    ("Drupal", "cms", "html", r"drupal-settings-json|/sites/default/files/|drupal\.js"),
    ("Webflow", "cms", "html", r"webflow\.(js|css|com)"),
    ("Next.js", "framework", "html", r"/_next/static/|__NEXT_DATA__"),
    ("Nuxt", "framework", "html", r"/_nuxt/|__NUXT__"),
    ("React", "framework", "html", r"react(-dom)?(\.production)?\.min\.js|data-reactroot"),
    ("Angular", "framework", "html", r"ng-version=|angular(\.min)?\.js"),
    ("Google Analytics / gtag", "analytics", "html", r"googletagmanager\.com/gtag|google-analytics\.com|gtag\("),
    ("Google Tag Manager", "analytics", "html", r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]{4,}"),
    ("Meta Pixel", "marketing", "html", r"connect\.facebook\.net/[^\"']+/fbevents\.js|fbq\("),
    ("Hotjar", "analytics", "html", r"static\.hotjar\.com|hotjar"),
    ("Microsoft Clarity", "analytics", "html", r"clarity\.ms"),
    ("HubSpot", "crm_marketing", "html", r"js\.hs-scripts\.com|hs-analytics|hubspot"),
    ("Salesforce", "crm_marketing", "html", r"force\.com|salesforce|pardot"),
    ("Zendesk", "support", "html", r"zdassets\.com|zendesk"),
    ("Intercom", "support", "html", r"widget\.intercom\.io|intercomSettings"),
    ("Tawk.to", "support", "html", r"embed\.tawk\.to"),
    ("Freshdesk / Freshchat", "support", "html", r"freshchat|freshdesk|freshworks"),
    ("WhatsApp click-to-chat", "messaging", "html", r"wa\.me/|api\.whatsapp\.com/send"),
    ("Google reCAPTCHA", "security_widget", "html", r"google\.com/recaptcha|grecaptcha"),
    ("hCaptcha", "security_widget", "html", r"hcaptcha\.com"),
    ("Stripe", "payments", "html", r"js\.stripe\.com"),
    ("Mercado Pago", "payments", "html", r"mercadopago"),
    ("PayU", "payments", "html", r"payulatam|payu\.com"),
    ("Wompi", "payments", "html", r"wompi\.co|checkout\.wompi"),
    ("ePayco", "payments", "html", r"epayco"),
    ("PSE (Colombian bank transfer)", "payments", "text", r"\bpse\b"),
    ("Addi", "payments", "html", r"\baddi\b|co\.addi\.com"),
    ("Google Maps embed", "maps", "html", r"maps\.googleapis\.com|google\.com/maps/embed"),
    ("Mailchimp", "crm_marketing", "html", r"mailchimp|list-manage\.com"),
    ("Cloudflare", "cdn_security", "header", r"cloudflare|cf-ray"),
    ("Akamai", "cdn_security", "header", r"akamai"),
    ("Amazon CloudFront", "cdn_security", "header", r"cloudfront"),
    ("Fastly", "cdn_security", "header", r"fastly"),
    ("Vercel", "hosting", "header", r"vercel"),
    ("Netlify", "hosting", "header", r"netlify"),
    ("nginx", "web_server", "header", r"^server:\s*nginx"),
    ("Apache", "web_server", "header", r"^server:\s*apache"),
    ("Microsoft IIS / ASP.NET", "web_server", "header", r"microsoft-iis|x-aspnet-version|x-powered-by:\s*asp\.net"),
    ("PHP", "language_runtime", "header", r"x-powered-by:\s*php"),
    ("Odoo", "erp_crm", "html", r"/web/static/|odoo"),
    ("SAP", "erp_crm", "text", r"\bsap\b"),
    ("Siigo", "erp_crm", "text", r"\bsiigo\b"),
    ("World Office", "erp_crm", "text", r"world office"),
    ("Alegra", "erp_crm", "text", r"\balegra\b"),
]
_TECH_RES = [(n, c, w, re.compile(rx, re.I | re.M)) for n, c, w, rx in TECH_RULES]

DATA_PLATFORMS = ["PostgreSQL", "MySQL", "SQL Server", "Oracle Database", "MongoDB", "Snowflake",
                  "BigQuery", "Redshift", "Databricks", "AWS RDS", "DynamoDB", "Firebase", "Supabase",
                  "Power BI", "Tableau", "Looker", "Azure", "AWS", "Google Cloud", "Kafka", "Elasticsearch",
                  "Excel", "Google Sheets", "SAP", "Salesforce", "HubSpot", "Odoo", "Siigo", "Zoho"]
_DATA_RES = [(name, re.compile(r"(?<![\w-])" + re.escape(name).replace(r"\ ", r"\s+") + r"(?![\w-])", re.I))
             for name in DATA_PLATFORMS]


def tech_fingerprints(page: PageData | None, headers: dict, text: str = "") -> list[dict]:
    header_blob = "\n".join(f"{k}: {v}" for k, v in (headers or {}).items())
    html_blob = ""
    if page is not None:
        html_blob = page.raw_html_sample + "\n" + " ".join(page.script_srcs) + page.inline_script_sample[:40000]
    found = []
    for name, cat, where, rx in _TECH_RES:
        blob = {"html": html_blob, "header": header_blob, "text": fold(text)}[where]
        m = rx.search(blob)
        if m:
            found.append({"technology": name, "category": cat, "evidence_type": f"passive_{where}_fingerprint",
                          "matched": m.group(0)[:60]})
    return found


def data_platform_mentions(text: str) -> list[tuple[str, str]]:
    """(platform, sentence) for named data platforms mentioned in text."""
    out = []
    for name, rx in _DATA_RES:
        m = rx.search(text)
        if m:
            start = max(0, text.rfind(".", 0, m.start()) + 1)
            end = text.find(".", m.end())
            out.append((name, text[start: end if end != -1 else m.end() + 200].strip()[:300]))
    return out


# --------------------------------------------------------------------------
# Authentication surfaces (observable only)
# --------------------------------------------------------------------------

AUTH_LINK_RE = re.compile(
    r"(iniciar sesi[oó]n|inicia sesi[oó]n|ingres(a|ar)|log ?in|sign ?in|mi cuenta|my account|registr(o|ate|arse)|"
    r"sign ?up|crear cuenta|portal (de )?(clientes|proveedores|empleados|colaboradores|distribuidores)|"
    r"zona (de )?clientes|acceso (a )?clientes|intranet|extranet)", re.I)
SSO_RES = {
    "google_sign_in_visible": re.compile(r"(sign in|continue|continuar|iniciar sesi[oó]n|ingresar|log in) (with|con) google|accounts\.google\.com/gsi|g_id_onload", re.I),
    "microsoft_sign_in_visible": re.compile(r"(sign in|continue|continuar|iniciar sesi[oó]n|ingresar) (with|con) microsoft|login\.microsoftonline\.com", re.I),
    "apple_sign_in_visible": re.compile(r"(sign in|continue|continuar|iniciar sesi[oó]n) (with|con) apple|appleid\.cdn-apple\.com", re.I),
    "facebook_login_visible": re.compile(r"(log in|continue|continuar|iniciar sesi[oó]n) (with|con) facebook", re.I),
    "saml_documented": re.compile(r"\bsaml\b", re.I),
    "sso_documented": re.compile(r"single sign-on|\bsso\b|inicio de sesi[oó]n [uú]nico", re.I),
    "magic_link_documented": re.compile(r"magic link|enlace m[aá]gico|link de acceso por correo", re.I),
    "two_factor_documented": re.compile(r"two-factor|2fa|autenticaci[oó]n de dos (pasos|factores)|mfa\b", re.I),
}


def auth_surfaces(page: PageData) -> dict:
    surfaces = []
    for url, anchor in page.links:
        if anchor and AUTH_LINK_RE.search(anchor):
            surfaces.append({"surface_url": url, "label": anchor[:80], "basis": "visible_link_text"})
    for form in page.forms:
        if form.get("has_password"):
            surfaces.append({"surface_url": form["action"] or page.url, "label": "password form",
                             "basis": "visible_password_field"})
    blob = page.raw_html_sample[:200000] + "\n" + page.text
    methods = sorted(name for name, rx in SSO_RES.items() if rx.search(blob))
    return {"surfaces": surfaces[:20], "method_observations": methods}
