"""Polite, SSRF-safe HTTP fetching with robots.txt, rate limits and size caps.

`transport` is injectable so tests run against a synthetic web without network.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlsplit

from .safety import DNSFailure, Resolver, UnsafeURL, validate_url


@dataclass
class Response:
    url: str
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    final_url: str = ""
    error: str | None = None
    redirects: list = field(default_factory=list)
    truncated: bool = False

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type") or ""
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text())


Transport = Callable[[str, str, dict, bytes | None, float, int], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - we follow manually
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def urllib_transport(method: str, url: str, headers: dict, data: bytes | None,
                     timeout: float, max_bytes: int) -> Response:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        resp = _OPENER.open(req, timeout=timeout)
        status = resp.status
    except urllib.error.HTTPError as exc:  # 3xx/4xx/5xx land here
        resp = exc
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return Response(url=url, status=0, error=f"{type(exc).__name__}: {exc}")
    hdrs = {k.lower(): v for k, v in resp.headers.items()}
    body = b""
    truncated = False
    try:
        declared = int(hdrs.get("content-length") or 0)
        if declared and declared > max_bytes:
            truncated = True
        else:
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                body, truncated = body[:max_bytes], True
    except Exception as exc:  # noqa: BLE001 - network read errors vary
        return Response(url=url, status=status, headers=hdrs, error=f"read error: {exc}")
    enc = hdrs.get("content-encoding", "")
    try:
        if enc == "gzip":
            body = gzip.decompress(body)
        elif enc == "deflate":
            body = zlib.decompress(body)
    except Exception:  # noqa: BLE001
        pass
    return Response(url=url, status=status, headers=hdrs, body=body, final_url=url, truncated=truncated)


class Fetcher:
    """All network access goes through here (also used by API provider adapters)."""

    def __init__(self, config: dict, transport: Transport | None = None,
                 resolver: Resolver | None = None, sleep: Callable[[float], None] = time.sleep):
        fc = config["fetch"]
        self.user_agent = fc["user_agent"]
        self.timeout = fc["timeout_s"]
        self.max_bytes = fc["max_bytes"]
        self.max_redirects = fc["max_redirects"]
        self.min_interval = fc["per_host_min_interval_s"]
        self.retries = fc["retries"]
        self.respect_robots = fc["respect_robots"]
        self.allowed_ports = fc["allowed_ports"]
        self.transport = transport or urllib_transport
        self.resolver = resolver
        self.sleep = sleep
        self._last_hit: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.request_count = 0
        self.log: list[dict] = []

    # -- politeness -----------------------------------------------------------
    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _wait_turn(self, host: str, min_interval: float) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = min_interval - (time.monotonic() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def robots_allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            resp = self._raw("GET", base + "/robots.txt", {}, None, check_robots=False)
            if resp.status == 200 and not resp.error:
                parser.parse(resp.text().splitlines())
                self._robots[base] = parser
            elif resp.status in (401, 403):
                parser.disallow_all = True
                self._robots[base] = parser
            else:
                self._robots[base] = None  # no robots.txt: allowed
        parser = self._robots[base]
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    def robots_sitemaps(self, base_url: str) -> list[str]:
        parts = urlsplit(base_url)
        base = f"{parts.scheme}://{parts.netloc}"
        self.robots_allowed(base + "/")
        parser = self._robots.get(base)
        return list(parser.site_maps() or []) if parser else []

    # -- requests ---------------------------------------------------------------
    def _raw(self, method: str, url: str, headers: dict, data: bytes | None,
             check_robots: bool = True, min_interval: float | None = None) -> Response:
        hops: list[str] = []
        current = url
        for _ in range(self.max_redirects + 1):
            try:
                validate_url(current, self.allowed_ports, self.resolver)
            except DNSFailure as exc:
                return Response(url=url, status=0, error=f"dns_error: {exc}", final_url=current, redirects=hops)
            except UnsafeURL as exc:
                return Response(url=url, status=0, error=f"blocked_unsafe_url: {exc}",
                                final_url=current, redirects=hops)
            if check_robots and method == "GET" and not self.robots_allowed(current):
                return Response(url=url, status=0, error="blocked_robots", final_url=current, redirects=hops)
            host = (urlsplit(current).hostname or "").lower()
            hdrs = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate",
                    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8", **headers}
            with self._host_lock(host):
                self._wait_turn(host, self.min_interval if min_interval is None else min_interval)
                resp = self.transport(method, current, hdrs, data, self.timeout, self.max_bytes)
            self.request_count += 1
            if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                hops.append(current)
                current = urljoin(current, resp.headers["location"])
                if resp.status == 303:
                    method, data = "GET", None
                continue
            resp.url = url
            resp.final_url = current
            resp.redirects = hops
            return resp
        return Response(url=url, status=0, error="too_many_redirects", final_url=current, redirects=hops)

    def request(self, method: str, url: str, headers: dict | None = None, data: bytes | None = None,
                check_robots: bool = True, min_interval: float | None = None) -> Response:
        """Request with retry/backoff on 429/5xx/network errors."""
        attempt = 0
        while True:
            resp = self._raw(method, url, headers or {}, data, check_robots, min_interval)
            retryable = resp.status in (429, 500, 502, 503, 504) or (resp.status == 0 and resp.error
                                                                  and not resp.error.startswith(("blocked", "dns_error")))
            self.log.append({"url": url, "status": resp.status, "error": resp.error, "attempt": attempt})
            if not retryable or attempt >= self.retries:
                return resp
            attempt += 1
            retry_after = resp.headers.get("retry-after", "")
            delay = float(retry_after) if retry_after.isdigit() else 2.0 * attempt
            if resp.status == 429:  # back off at least the provider's own interval
                delay = max(delay, (min_interval or 0) * 1.5, 6.0 * attempt)
            self.sleep(min(delay, 30.0))

    def get(self, url: str, **kwargs) -> Response:
        return self.request("GET", url, **kwargs)

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> Response:
        body = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        return self.request("POST", url, headers=hdrs, data=body, check_robots=False)
