from __future__ import annotations

import requests

from mealie_parser.parser import (
    AlreadyParsed,
    extract_raw_lines,
    looks_suspicious,
    parse_with_fallback,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def parse_ingredients(self, ingredients, strategy):
        response = self.responses[strategy]
        if isinstance(response, Exception):
            raise response
        return response


def test_extract_raw_lines_from_strings() -> None:
    recipe = {"recipeIngredient": ["1 cup flour", " 2 eggs ", ""]}
    assert extract_raw_lines(recipe) == ["1 cup flour", "2 eggs"]


def test_extract_raw_lines_from_legacy_dict_items() -> None:
    recipe = {
        "recipeIngredient": [
            {"food": None, "originalText": "1 cup sugar"},
            {"food": None, "note": "pinch salt"},
        ]
    }
    assert extract_raw_lines(recipe) == ["1 cup sugar", "pinch salt"]


def test_extract_raw_lines_raises_when_already_parsed() -> None:
    recipe = {
        "recipeIngredient": [
            {"food": {"id": "food-1", "name": "Sugar"}, "originalText": "1 cup sugar"}
        ]
    }
    try:
        extract_raw_lines(recipe)
    except AlreadyParsed:
        pass
    else:
        raise AssertionError("Expected AlreadyParsed")


def test_looks_suspicious_detects_zero_qty_with_unit() -> None:
    assert looks_suspicious(
        {"quantity": 0, "unit": {"id": "unit-1"}, "food": {"id": "f"}}
    )


def test_looks_suspicious_ignores_serving_notes() -> None:
    assert not looks_suspicious(
        {"quantity": 0, "unit": {"id": "unit-1"}, "note": "For garnish"}
    )


def test_parse_with_fallback_uses_second_parser() -> None:
    low_conf = [
        {
            "confidence": {"average": 0.4},
            "ingredient": {"quantity": 1, "food": {"id": "f"}},
        }
    ]
    good_conf = [
        {
            "confidence": {"average": 0.95},
            "ingredient": {"quantity": 1, "food": {"id": "f"}, "unit": None},
        }
    ]
    client = FakeClient({"nlp": low_conf, "openai": good_conf})

    parsed, strategy, attempts = parse_with_fallback(
        client,
        ["1 cup sugar"],
        ("nlp", "openai"),
        confidence_threshold=0.8,
    )

    assert strategy == "openai"
    assert parsed == good_conf
    assert attempts == [{"strategy": "nlp", "error": "below confidence threshold"}]


def test_parse_with_fallback_records_http_error() -> None:
    error = requests.HTTPError("boom")
    client = FakeClient({"nlp": error})

    parsed, strategy, attempts = parse_with_fallback(
        client,
        ["1 cup sugar"],
        ("nlp",),
        confidence_threshold=0.8,
    )

    assert parsed == []
    assert strategy is None
    assert attempts == [{"strategy": "nlp", "error": "boom"}]
