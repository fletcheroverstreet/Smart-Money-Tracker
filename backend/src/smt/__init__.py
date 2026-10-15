"""Smart Money Tracker backend.

Tracks what insiders, members of Congress and institutions buy and sell, using
official public disclosures only. Every number traces back to the filing it
came from.

Package map:
    config      settings from the environment / .env
    db          SQLAlchemy models, sessions, Alembic migrations
    domain      pure finance/legal logic (business days, deadlines, codes)
    net         the one outbound HTTP client, rate limiters, raw document store
    sources     fetchers and parsers per public source
    ingest      loaders that turn parsed documents into database rows
    worker      scheduler and job definitions (the only process that fetches)
    api         read-only FastAPI app and the live SSE stream
"""

__version__ = "0.1.0"
