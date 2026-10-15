"""API entry point: `smt api`.

Uvicorn is started inside our own event loop (see smt.runtime) so psycopg's
async driver works on Windows too. One process serves many connections;
scale out later by running more containers, not more workers per container.
"""

from __future__ import annotations

import uvicorn

from smt.api.middleware import TRUSTED_PROXY_NETWORKS
from smt.runtime import run


def main(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    # proxy_headers: take the visitor's address and scheme from X-Forwarded-For/-Proto, but only
    # when the connection comes from our own proxy or web server (a private or loopback address).
    # From anywhere else those headers could be made up (see smt.api.middleware).
    config = uvicorn.Config(
        "smt.api.app:create_app", factory=True, host=host, port=port,
        proxy_headers=True, forwarded_allow_ips=",".join(TRUSTED_PROXY_NETWORKS), log_level="info",
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    run(server.serve())
