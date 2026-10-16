"""Lightweight publication configuration shared by readers and dispatchers."""
import os
from pathlib import Path


def enabled() -> bool:
    return os.getenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', '0') == '1'


def publication_root(artifacts_root: Path) -> Path:
    return artifacts_root / 'publication-generations'
