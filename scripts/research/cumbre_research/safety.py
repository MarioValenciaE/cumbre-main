"""Safety controls: SSRF guard, untrusted-content handling, PII/secret redaction.

Everything fetched from the web is untrusted data. Nothing here ever executes or
follows instructions found in page content.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Callable, Iterable
from urllib.parse import urlsplit

Resolver = Callable[[str], Iterable[str]]

BLOCKED_HOSTNAMES = {
    "localhost", "localhost.localdomain", "metadata", "metadata.google.internal",
    "metadata.goog", "instance-data", "instance-data.ec2.internal",
}
METADATA_IPS = {"169.254.169.254", "169.254.170.2", "fd00:ec2::254", "100.100.100.200"}


class UnsafeURL(Exception):
    """Raised when a URL must not be fetched."""


class DNSFailure(UnsafeURL):
    """Host did not resolve: a reachability problem, not a safety block."""


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def check_ip(ip_text: str) -> None:
    ip = ipaddress.ip_address(ip_text.split("%")[0])
    if ip_text in METADATA_IPS:
        raise UnsafeURL(f"cloud metadata address {ip_text}")
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or not ip.is_global):
        raise UnsafeURL(f"non-public address {ip_text}")


def validate_url(url: str, allowed_ports: Iterable[int] = (80, 443, 8080, 8443),
                 resolver: Resolver | None = None) -> str:
    """Return the URL if safe to fetch, else raise UnsafeURL.

    Checks scheme, credentials-in-URL, port, hostname blocklist, literal IPs and
    every resolved address (private, loopback, link-local, metadata, reserved).
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURL(f"unparseable URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL(f"disallowed scheme {parts.scheme!r}")
    if parts.username or parts.password:
        raise UnsafeURL("credentials embedded in URL")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURL("missing host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("invalid port") from exc
    if port not in set(allowed_ports):
        raise UnsafeURL(f"disallowed port {port}")
    if host in BLOCKED_HOSTNAMES or host.endswith((".localhost", ".local", ".internal", ".intranet", ".lan")):
        raise UnsafeURL(f"blocked hostname {host}")
    try:
        ipaddress.ip_address(host.strip("[]"))
        check_ip(host.strip("[]"))
        return url
    except ValueError:
        pass
    if "." not in host:
        raise UnsafeURL(f"single-label hostname {host}")
    resolve = resolver or default_resolver
    try:
        addresses = list(resolve(host))
    except OSError as exc:
        raise DNSFailure(f"DNS resolution failed for {host}: {exc}") from exc
    if not addresses:
        raise DNSFailure(f"no addresses for {host}")
    for address in addresses:
        check_ip(address)
    return url


# --------------------------------------------------------------------------
# Untrusted content: prompt-injection neutralization
# --------------------------------------------------------------------------

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?|rules)",
    r"disregard (all |the )?(previous|prior|above|system)",
    r"you are (now )?(an? )?(ai|assistant|language model|chatgpt|claude)",
    r"(system|developer) prompt",
    r"new instructions?:",
    r"act as (an? )?(ai|assistant|admin)",
    r"(send|email|post|upload) (the |all )?(data|files|secrets|credentials|api keys?)",
    r"olvida (todas )?las instrucciones",
    r"ignora (todas )?las instrucciones",
    r"eres (ahora )?un (asistente|modelo)",
    r"</?(system|assistant|instructions?)>",
    r"\b(run|execute) (this|the following) (command|code)",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> list[str]:
    return sorted({m.group(0)[:80] for m in _INJECTION_RE.finditer(text or "")})


def neutralize(text: str) -> str:
    """Strip instruction-like sentences and markup that could confuse a reader or agent."""
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = ["[instruction-like text removed]" if _INJECTION_RE.search(s) else s for s in sentences]
    out = " ".join(kept)
    out = re.sub(r"<[^>]{0,200}>", " ", out)          # stray tags
    out = out.replace("`", "'").replace("|", "/")     # keep Markdown tables intact
    out = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1", out)  # no embedded links
    return re.sub(r"\s+", " ", out).strip()


# --------------------------------------------------------------------------
# Personal data and secrets
# --------------------------------------------------------------------------

SECRET_PATTERNS = {
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "private_key_block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "generic_api_key": r"\b(?:sk|pk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b",
    "openai_style_key": r"\bsk-[A-Za-z0-9_-]{20,}\b",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b",
    "google_api_key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "password_assignment": r"(?i)\b(pass(word)?|pwd|contrase(n|ñ)a|clave)\s*[:=]\s*\S{4,}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    "connection_string": r"(?i)\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis)://[^\s:/]+:[^\s@/]+@",
}
_SECRET_RES = {k: re.compile(v) for k, v in SECRET_PATTERNS.items()}

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
PHONE_RE = re.compile(
    r"(?<![\w/.])(?:"
    r"\+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){1,4}\d{3,4}"   # international with +
    r"|\(\d{1,4}\)[\s.-]?\d{3}[\s.-]?\d{3,4}"                    # (602) 555 1234
    r"|3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}"                            # CO mobile 300 123 4567
    r"|\d{3}[\s.-]\d{3}[\s.-]\d{4}"                               # 555-123-4567
    r")(?![\w/])")
NATIONAL_ID_RE = re.compile(r"(?i)\b(c\.?\s?c\.?|c[ée]dula( de ciudadan[ií]a)?|ssn|dni|pasaporte|passport)\s*(n[o°º.]*\s*)?[:#]?\s*[\d.\- ]{6,15}")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
ROLE_MAILBOXES = {"info", "contacto", "contact", "ventas", "sales", "servicioalcliente", "soporte",
                  "support", "hola", "hello", "comercial", "pqrs", "pqr", "notificaciones", "admin",
                  "atencion", "atencionalcliente", "prensa", "press", "rrhh", "talentohumano", "empleo"}


def find_secrets(text: str) -> list[str]:
    """Return the *types* of secret-like strings present (never the values)."""
    return sorted(k for k, rx in _SECRET_RES.items() if rx.search(text or ""))


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def redact(text: str, keep_role_emails: bool = True) -> str:
    """Remove secrets, personal emails, phone numbers, national IDs and card numbers."""
    if not text:
        return ""
    out = text
    for rx in _SECRET_RES.values():
        out = rx.sub("[REDACTED_SECRET]", out)
    out = NATIONAL_ID_RE.sub("[REDACTED_ID]", out)

    def _card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[REDACTED_CARD]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)

    out = CARD_RE.sub(_card, out)

    def _email(m: re.Match) -> str:
        local = m.group(0).split("@")[0].lower()
        if keep_role_emails and re.sub(r"[^a-z]", "", local) in ROLE_MAILBOXES:
            return m.group(0)
        return f"[EMAIL@{m.group(1)}]"

    out = EMAIL_RE.sub(_email, out)

    def _phone(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return m.group(0) if len(digits) < 7 else "[PHONE]"

    return PHONE_RE.sub(_phone, out)


def safe_excerpt(text: str, limit: int = 240) -> str:
    """Untrusted text -> short, redacted, neutralized excerpt for reports."""
    clean = neutralize(redact(text or ""))
    return clean if len(clean) <= limit else clean[: limit - 1].rsplit(" ", 1)[0] + "…"
