from __future__ import annotations

from mealie_parser.client import _authorization_value


def test_authorization_value_adds_bearer_prefix() -> None:
    assert _authorization_value("abc123") == "Bearer abc123"


def test_authorization_value_preserves_existing_bearer_prefix() -> None:
    assert _authorization_value("Bearer abc123") == "Bearer abc123"
