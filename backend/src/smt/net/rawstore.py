"""The raw document store: every fetched document, compressed and content-addressed.

Raw first, parse second (spec §3). A document's bytes are saved under their
SHA-256, zstd-compressed (Python 3.14's standard `compression.zstd`), before
any parser sees them. Identical bytes are stored once. The file layout
(`ab/cd/abcdef...zst`) keeps any one directory small.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from compression import zstd
from pathlib import Path

from smt.config import get_settings


class RawStore:
    """Content-addressed, compressed file storage."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().raw_store_dir

    @staticmethod
    def digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def relative_path(self, sha256: str) -> str:
        return f"{sha256[:2]}/{sha256[2:4]}/{sha256}.zst"

    def put(self, content: bytes) -> tuple[str, str]:
        """Store `content` (idempotent). Returns (sha256, path relative to the store root)."""
        sha = self.digest(content)
        rel = self.relative_path(sha)
        target = self.root / rel
        if target.exists():
            return sha, rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = zstd.compress(content, level=10)
        # Atomic write: a crash never leaves a half-written file under the final name.
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return sha, rel

    def get(self, relative: str) -> bytes:
        """Return the original bytes of a stored document."""
        return zstd.decompress((self.root / relative).read_bytes())

    def exists(self, sha256: str) -> bool:
        return (self.root / self.relative_path(sha256)).exists()
