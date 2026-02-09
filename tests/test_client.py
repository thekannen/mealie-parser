from __future__ import annotations

from mealie_parser.client import _authorization_value, _summarize_error_payload


def test_authorization_value_adds_bearer_prefix() -> None:
    assert _authorization_value("abc123") == "Bearer abc123"


def test_authorization_value_preserves_existing_bearer_prefix() -> None:
    assert _authorization_value("Bearer abc123") == "Bearer abc123"


def test_summarize_error_payload_handles_duplicate_key_violation() -> None:
    payload = {
        "detail": {
            "message": "An unexpected error occurred",
            "exception": "duplicate key value violates unique constraint "
            '"ingredient_foods_name_group_id_key"',
        }
    }
    assert _summarize_error_payload(payload) == "duplicate key violation"


def test_summarize_error_payload_truncates_long_detail() -> None:
    payload = {"detail": "x" * 400}
    summary = _summarize_error_payload(payload)
    assert len(summary) <= 220
    assert summary.endswith("...")
