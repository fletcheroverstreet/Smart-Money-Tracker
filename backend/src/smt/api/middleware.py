"""HTTP middleware: per-visitor rate limiting and ETag revalidation for read endpoints.

* **Rate limit.** A token bucket per visitor (default 20 requests/second,
  bursts of 60). The public API is read-only and rate-limited (spec §13).
  The SSE stream is exempt; it is one long request. See "Who is the visitor"
  below.
* **ETag.** GET responses with JSON bodies get a strong ETag (a hash of the
  body). A client that sends `If-None-Match` with the same tag gets 304 Not
  Modified and no body, which saves bandwidth on polling fallbacks. Private
  responses (Cache-Control: private) are skipped.

Who is the visitor. Requests reach the API three ways:

1. A browser's /api call through Caddy. The connection comes from Caddy (a
   private address) and Caddy's X-Forwarded-For header holds the visitor's IP.
2. A page rendered by the Next.js server. The connection comes from the web
   server (a private address), which forwards the visitor's IP as
   X-Forwarded-For (web/src/lib/api/server.ts). Without that, one scraper
   hammering web pages would drive unlimited API and database load.
3. Our own callers with no visitor behind them: health checks, the worker.
   The connection is private and carries no X-Forwarded-For. These are exempt.

X-Forwarded-For is believed only when the connection comes from a private or
loopback address, i.e. from our own proxy or web server. Anyone on the
internet can write that header, so from a public address it is ignored and the
connecting address itself is limited; otherwise a scraper could send a made-up
address with every request and never be limited.
"""

from __future__ import annotations

import hashlib
import ipaddress
import time
from collections import OrderedDict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

EXEMPT_PREFIXES = ("/api/stream", "/healthz")

#: Where our own proxy (Caddy) and web server connect from: loopback, and the
#: private ranges Docker and home networks use. Only connections from these may
#: say who the visitor is. smt.api.serve gives uvicorn the same list, so uvicorn
#: (which applies X-Forwarded-For before this middleware runs) trusts the same peers.
TRUSTED_PROXY_NETWORKS: tuple[str, ...] = (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
)
_TRUSTED = tuple(ipaddress.ip_network(network) for network in TRUSTED_PROXY_NETWORKS)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_ip(value: str | None) -> IPAddress | None:
    """Read an address as a proxy writes it. None when it isn't an IP address.

    Drops a port ("203.0.113.7:443", "[2001:db8::1]:443"), and turns an
    IPv4 address written in IPv6 form ("::ffff:203.0.113.7") into plain IPv4,
    so one visitor always gets one bucket.
    """
    if not value:
        return None
    text = value.strip()
    if text.startswith("["):
        text = text[1:].split("]", 1)[0]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_trusted_proxy(address: IPAddress) -> bool:
    """True for loopback and private addresses: our own proxy or web server."""
    return any(address in network for network in _TRUSTED)


def forwarded_client(header: str) -> IPAddress | None:
    """The visitor's address in an X-Forwarded-For list ("visitor, proxy1, proxy2").

    Each proxy appends the address the request came to it from, so the list is
    read from the right: our own proxies (private addresses) are skipped and
    the first other address is the visitor. When every entry is private (a
    visitor on the local network), the first one is the visitor. None when an
    entry can't be read, rather than guessing past it.
    """
    hops = [parse_ip(part) for part in header.split(",")]
    for hop in reversed(hops):
        if hop is None:
            return None
        if not is_trusted_proxy(hop):
            return hop
    return hops[0]


def _bucket(address: IPAddress) -> str:
    """The rate-limit bucket for an address.

    One IPv6 user usually holds a whole /64 block and can pick a new address
    in it for every request, so public IPv6 addresses share one bucket per /64.
    """
    if isinstance(address, ipaddress.IPv6Address) and not is_trusted_proxy(address):
        return str(ipaddress.ip_network((address, 64), strict=False))
    return str(address)


def rate_limit_key(peer: str | None, forwarded_for: str | None) -> str | None:
    """Which bucket a request counts against, or None when it is exempt.

    `peer` is the address the connection came from; `forwarded_for` is the
    X-Forwarded-For header, if any.

    * A public peer: its own address. Its X-Forwarded-For is ignored, since
      anyone can write that header.
    * A private or loopback peer (Caddy, the web server) with X-Forwarded-For:
      the visitor named there. If the header names no usable address, the
      peer itself, so the request is still limited.
    * A private or loopback peer without it: None, exempt (health checks, the worker).
    """
    address = parse_ip(peer)
    if address is None:
        return peer or "unknown"
    if not is_trusted_proxy(address):
        return _bucket(address)
    if forwarded_for is None:
        return None
    visitor = forwarded_client(forwarded_for)
    return _bucket(visitor if visitor is not None else address)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-visitor token bucket. Memory is bounded (least-recently-seen visitors are forgotten)."""

    def __init__(self, app: object, rate: float = 20.0, burst: float = 60.0, max_clients: int = 50_000) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.rate, self.burst, self.max_clients = rate, burst, max_clients
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)
        # A request may carry the header more than once; together they form one list, in order.
        forwarded = request.headers.getlist("x-forwarded-for")
        key = rate_limit_key(request.client.host if request.client else None,
                             ", ".join(forwarded) if forwarded else None)
        if key is None:
            return await call_next(request)
        now = time.monotonic()
        tokens, updated = self._buckets.pop(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - updated) * self.rate)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429,
                                headers={"Retry-After": "1"})
        self._buckets[key] = (tokens - 1, now)
        if len(self._buckets) > self.max_clients:
            self._buckets.popitem(last=False)
        return await call_next(request)


class ETagMiddleware(BaseHTTPMiddleware):
    """Add ETags to cacheable GET JSON responses and answer If-None-Match with 304."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if (request.method != "GET" or response.status_code != 200
                or not response.headers.get("content-type", "").startswith("application/json")
                or "private" in response.headers.get("cache-control", "")
                or request.url.path.startswith(EXEMPT_PREFIXES)):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        headers = dict(response.headers)
        headers["etag"] = etag
        headers.pop("content-length", None)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={k: v for k, v in headers.items()
                                                     if k in ("etag", "cache-control", "vary")})
        return Response(content=body, status_code=200, headers=headers, media_type="application/json")
