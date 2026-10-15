"""Private mode: a single-owner session for data that may not be shown publicly (PLAN.md decision 1).

Free price data is licensed for personal use only, so prices and anything
computed from them appear only in private mode. This is deliberately NOT a
user-accounts system (a spec non-goal): there is one passphrase, in the
server's .env, for the owner.

* The session cookie is signed (HMAC-SHA256 with SESSION_SECRET), HttpOnly,
  SameSite=Strict, and Secure in production. It holds only an expiry time.
* Private responses must send `Cache-Control: private, no-store` and
  `Vary: Cookie`, so licensed data never lands in a shared cache.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from smt.api.deps import Session
from smt.api.schemas import PriceBarOut, PriceSeriesOut
from smt.config import get_settings
from smt.domain.business_days import ET
from smt.prices.queries import private_series

router = APIRouter(prefix="/api/private", tags=["private"])

COOKIE = "smt_private"
SESSION_SECONDS = 7 * 24 * 3600
#: Headers on every private answer: never stored by a shared cache (PLAN.md decision 1).
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


class LoginIn(BaseModel):
    passphrase: str


class SessionOut(BaseModel):
    active: bool
    configured: bool


def _sign(expiry: int) -> str:
    secret = get_settings().session_secret.get_secret_value().encode()
    mac = hmac.new(secret, str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{mac}"


def is_private(cookie_value: str | None) -> bool:
    """True if the cookie is a valid, unexpired private-mode session."""
    settings = get_settings()
    if not cookie_value or not settings.session_secret.get_secret_value():
        return False
    expiry_text, _, _mac = cookie_value.partition(".")
    if not expiry_text.isdigit() or int(expiry_text) < time.time():
        return False
    return hmac.compare_digest(cookie_value, _sign(int(expiry_text)))


def require_private(request: Request) -> None:
    """Dependency for endpoints that serve licensed data."""
    if not is_private(request.cookies.get(COOKIE)):
        # No-store even on the refusal: a shared cache must never keep a
        # private URL's answer, whichever viewer it was for.
        raise HTTPException(status_code=403, detail="Private mode only", headers=PRIVATE_HEADERS)


def _configured() -> bool:
    s = get_settings()
    return bool(s.private_mode_passphrase.get_secret_value() and s.session_secret.get_secret_value())


@router.get("/session")
async def session(response: Response, smt_private: Annotated[str | None, Cookie()] = None) -> SessionOut:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    return SessionOut(active=is_private(smt_private), configured=_configured())


@router.post("/login")
async def login(body: LoginIn, response: Response) -> SessionOut:
    if not _configured():
        raise HTTPException(status_code=503, detail="Private mode is not configured on this server")
    expected = get_settings().private_mode_passphrase.get_secret_value()
    if not hmac.compare_digest(body.passphrase.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Wrong passphrase")
    expiry = int(time.time()) + SESSION_SECONDS
    response.set_cookie(COOKIE, _sign(expiry), max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=get_settings().is_production, path="/")
    response.headers["Cache-Control"] = "private, no-store"
    return SessionOut(active=True, configured=True)


@router.post("/logout")
async def logout(response: Response) -> SessionOut:
    response.delete_cookie(COOKIE, path="/")
    response.headers["Cache-Control"] = "private, no-store"
    return SessionOut(active=False, configured=_configured())


@router.get("/prices/{symbol}", dependencies=[Depends(require_private)])
async def prices(
    session: Session,
    symbol: str,
    response: Response,
    days: Annotated[int, Query(ge=1, le=3650, description="calendar days back from today")] = 365,
) -> PriceSeriesOut:
    """Daily closes for one ticker from the stored price history (licensed data: private mode only)."""
    since = datetime.now(ET).date() - timedelta(days=days)
    series = await private_series(session, symbol, since)
    if series is None:
        raise HTTPException(status_code=404, detail=f"No stored prices for {symbol.strip().upper()}",
                            headers=PRIVATE_HEADERS)
    response.headers.update(PRIVATE_HEADERS)
    return PriceSeriesOut(symbol=series.symbol, provider=series.provider, licence=series.licence,
                          bars=[PriceBarOut(day=day, close=close) for day, close in series.bars])
