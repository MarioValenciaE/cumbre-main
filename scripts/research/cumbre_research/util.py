"""Small shared helpers: time, hashing, URL canonicalization, config, JSONL."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PKG_DIR = Path(__file__).resolve().parent
RESEARCH_DIR = PKG_DIR.parent
REPO_ROOT = RESEARCH_DIR.parent.parent
DEFAULTS_FILE = RESEARCH_DIR / "research_defaults.json"

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "igshid", "ref", "ref_src",
}
# Country mirrors that serve the same page under another domain (duplicate control only).
MIRROR_HOSTS = {"emis.cn": "emis.com", "emis.co": "emis.com"}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: dt.datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str | bytes) -> str:
    data = text.encode("utf-8", "ignore") if isinstance(text, str) else text
    return "sha256:" + hashlib.sha256(data).hexdigest()


def short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:n]


def fold(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace (for matching only)."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.lower()).strip()


def slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot build a folder name from {value!r}")
    return slug


def canonical_url(url: str) -> str:
    """Normalize for duplicate control: lowercase scheme/host, drop default port,
    fragments and tracking params, sort query, trim trailing slash (except root)."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = (parts.scheme or "http").lower()
    if scheme == "http":
        scheme = "https"  # http/https variants of one page are one source
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    host = MIRROR_HOSTS.get(host, host)
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if k.lower() not in TRACKING_PARAMS))
    return urlunsplit((scheme, netloc, path, query, ""))


def nit_check_digit(base: str) -> int:
    """DIAN check digit (dígito de verificación) for a Colombian NIT base number."""
    weights = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
    digits = [int(c) for c in reversed(base)]
    total = sum(d * w for d, w in zip(digits, weights))
    r = total % 11
    return r if r < 2 else 11 - r


def validate_nit(nit: str) -> tuple[bool, str]:
    """Check a 'base-DV' NIT. Returns (ok, reason). A NIT without DV is accepted if the base looks valid."""
    raw = str(nit).strip().replace(".", "").replace(" ", "")
    base, _, dv = raw.partition("-")
    if not base.isdigit() or not 6 <= len(base) <= 10:
        return False, f"NIT base must be 6-10 digits (got {base!r})"
    if dv:
        if not dv.isdigit() or int(dv) != nit_check_digit(base):
            return False, f"check digit {dv!r} does not match {nit_check_digit(base)} for base {base}"
    if len(base) == 9 and base[0] in "89":
        return True, "ok"
    return True, "ok (legal-entity NITs usually have 9 digits starting with 8 or 9; verify)"


def host_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host: str) -> str:
    """Approximate eTLD+1 without a PSL dependency (handles .com.co style suffixes)."""
    labels = [p for p in host.lower().split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    second_level = {"com", "net", "org", "gov", "edu", "co", "mil", "ac", "gob"}
    if labels[-2] in second_level and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_structured_file(path: Path) -> object:
    """JSON always; YAML when PyYAML is installed (JSON is valid YAML too)."""
    text = Path(path).read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise SystemExit(f"{path}: YAML needs PyYAML (pip install pyyaml) or use JSON") from exc
    return yaml.safe_load(text)


def load_config(override_path: str | None = None, overrides: dict | None = None) -> dict:
    config = json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))
    if override_path:
        config = deep_merge(config, load_structured_file(Path(override_path)) or {})
    if overrides:
        config = deep_merge(config, overrides)
    return config


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def parse_date(value: str | None) -> dt.date | None:
    """Parse common date forms. Returns None rather than guessing."""
    if not value:
        return None
    value = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", value)  # wayback / gdelt 20230115T...
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None
