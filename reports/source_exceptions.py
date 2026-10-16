"""Shared provider failures that must cross the isolated fetch boundary."""
from __future__ import annotations


class SourceProviderUnavailable(RuntimeError):
    """A provider is reachable enough to answer, but cannot be consumed now."""

    def __init__(self, reason: str, *, availability: str = "error", status_code: int | None = None):
        super().__init__(reason)
        self.availability = availability
        self.status_code = status_code
