"""Parser errors. A ParseError sends the document to quarantine; it is never swallowed."""

from __future__ import annotations


class ParseError(ValueError):
    """The document could not be parsed into the expected structure."""


class NotMachineReadable(ParseError):
    """The document exists but has no machine-readable content (e.g. a scanned image)."""
