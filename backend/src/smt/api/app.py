"""The read-only public API.

It never writes disclosure data and never computes analytics on request; it
reads what the worker has already stored (lodestar's rule: the UI reads
completed runs). The live stream lives at /api/stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from smt import __version__
from smt.config import get_settings
from smt.db.session import dispose_engines
from smt.logs import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    from smt.api.routers.stream import broadcaster

    await broadcaster.stop()
    await dispose_engines()


def create_app() -> FastAPI:
    """Build the FastAPI app with every router mounted."""
    configure_logging()
    settings = get_settings()
    app = FastAPI(
        title="Smart Money Tracker API",
        version=__version__,
        description="Read-only API over official public disclosures. Research tool, not investment advice.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    from smt.api.middleware import ETagMiddleware, RateLimitMiddleware

    # Order matters: the last one added runs first. Rate limiting sees every
    # request; ETags are computed on the uncompressed body; then gzip.
    # (Starlette never gzips text/event-stream, so the live stream stays unbuffered.)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(ETagMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    from smt.api.routers import health, status

    app.include_router(health.router)
    app.include_router(status.router)
    _include_optional_routers(app)
    return app


def _include_optional_routers(app: FastAPI) -> None:
    """Routers added as phases land. Each one is independent."""
    import importlib

    for name in ("feed", "insiders", "tickers", "filings", "search", "stream", "meta", "private"):
        try:
            module = importlib.import_module(f"smt.api.routers.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"smt.api.routers.{name}":
                continue
            raise
        app.include_router(module.router)
