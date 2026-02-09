"""Mealie parser package."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _resolve_version() -> str:
    try:
        return version("mealie-parser")
    except PackageNotFoundError:
        version_file = Path(__file__).resolve().parents[2] / "VERSION"
        if version_file.exists():
            return version_file.read_text(encoding="utf-8").strip()
        return "0.0.0"


__version__ = _resolve_version()

__all__ = ["__version__"]
