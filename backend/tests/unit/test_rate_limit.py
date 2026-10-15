"""Rate limiting: which visitor a request counts against (smt.api.middleware).

Addresses from the documentation ranges (198.51.100.0/24, 203.0.113.0/24,
2001:db8::/32) stand in for public visitors; 172.18.0.5 is a Docker-network
address like the one Caddy or the Next.js server connects from.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from smt.api.middleware import TRUSTED_PROXY_NETWORKS, RateLimitMiddleware, parse_ip, rate_limit_key

WEB_SERVER = "172.18.0.5"
VISITOR = "198.51.100.7"
OTHER_VISITOR = "198.51.100.8"
PUBLIC_PEER = "203.0.113.9"


def _app(*, behind_uvicorn: bool) -> ASGIApp:
    """A one-route app with a 2-request burst and almost no refill.

    behind_uvicorn wraps it the way `smt api` serves it: uvicorn applies
    X-Forwarded-For from trusted peers before the middleware sees the request.
    """
    async def ok(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/x", ok)])
    app.add_middleware(RateLimitMiddleware, rate=0.001, burst=2)
    if behind_uvicorn:
        # uvicorn and Starlette spell the ASGI types differently; both are plain ASGI apps.
        wrapped = ProxyHeadersMiddleware(cast("Any", app), trusted_hosts=",".join(TRUSTED_PROXY_NETWORKS))
        return cast("ASGIApp", wrapped)
    return app


async def _codes(app: ASGIApp, peer: str, forwarded: list[str | None]) -> list[int]:
    """Send one request per entry from `peer`, with that X-Forwarded-For (None: no header)."""
    transport = httpx.ASGITransport(app=app, client=(peer, 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return [(await client.get("/api/x", headers={"X-Forwarded-For": f} if f else {})).status_code
                for f in forwarded]


both_layers = pytest.mark.parametrize("behind_uvicorn", [False, True], ids=["middleware", "under-uvicorn"])


@both_layers
async def test_the_visitor_forwarded_by_the_web_server_is_limited(behind_uvicorn: bool) -> None:
    app = _app(behind_uvicorn=behind_uvicorn)
    assert await _codes(app, WEB_SERVER, [VISITOR] * 3) == [200, 200, 429]
    # Another visitor rendering pages through the same web server has a bucket of their own.
    assert await _codes(app, WEB_SERVER, [OTHER_VISITOR]) == [200]


@both_layers
async def test_a_forwarded_header_from_a_public_address_is_ignored(behind_uvicorn: bool) -> None:
    # A scraper connecting directly and inventing a new X-Forwarded-For each time is still limited.
    app = _app(behind_uvicorn=behind_uvicorn)
    assert await _codes(app, PUBLIC_PEER, ["198.51.100.1", "198.51.100.2", "198.51.100.3"]) == [200, 200, 429]


@both_layers
async def test_internal_callers_without_a_forwarded_header_are_exempt(behind_uvicorn: bool) -> None:
    # Health checks and the worker: a private or loopback connection with no visitor behind it.
    app = _app(behind_uvicorn=behind_uvicorn)
    assert await _codes(app, WEB_SERVER, [None] * 10) == [200] * 10
    assert await _codes(app, "127.0.0.1", [None] * 10) == [200] * 10


@both_layers
async def test_a_render_whose_visitor_cant_be_read_is_still_limited(behind_uvicorn: bool) -> None:
    # web/src/lib/api/server.ts sends "unknown" when the page request's headers hold no address.
    app = _app(behind_uvicorn=behind_uvicorn)
    assert await _codes(app, WEB_SERVER, ["unknown"] * 3) == [200, 200, 429]


@pytest.mark.parametrize(("peer", "forwarded", "key"), [
    ("127.0.0.1", None, None),
    ("::1", None, None),
    (WEB_SERVER, VISITOR, VISITOR),
    (WEB_SERVER, f"::ffff:{VISITOR}", VISITOR),        # IPv4 written in IPv6 form
    (PUBLIC_PEER, VISITOR, PUBLIC_PEER),               # not believed from a public address
    # Read from the right: our proxies are skipped, and a made-up first entry is ignored.
    (WEB_SERVER, f"6.6.6.6, {VISITOR}, 172.18.0.3", VISITOR),
    (WEB_SERVER, "192.168.1.20", "192.168.1.20"),      # a visitor on the local network
    (WEB_SERVER, "not-an-address", WEB_SERVER),        # unusable header: limit the proxy, don't exempt it
    (WEB_SERVER, "", WEB_SERVER),
    (WEB_SERVER, "2001:db8:1:2::5", "2001:db8:1:2::/64"),  # one bucket per IPv6 /64
    (WEB_SERVER, "2001:db8:1:2::6", "2001:db8:1:2::/64"),
    (None, None, "unknown"),
])
def test_rate_limit_key(peer: str | None, forwarded: str | None, key: str | None) -> None:
    assert rate_limit_key(peer, forwarded) == key


def test_parse_ip_drops_ports() -> None:
    assert str(parse_ip("203.0.113.7:8080")) == "203.0.113.7"
    assert str(parse_ip("[2001:db8::1]:443")) == "2001:db8::1"
    assert parse_ip("unknown") is None
