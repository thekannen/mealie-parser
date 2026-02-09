from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests
from tqdm import tqdm

from mealie_parser.client import MealieClient
from mealie_parser.config import ParserConfig

LOGGER = logging.getLogger(__name__)

SERVING_PHRASES = {"for serving", "for garnish", "for dipping"}
QUANTITY_PREFIX_RE = re.compile(r"^\s*\d+[¼½¾⅓⅔⅛⅜⅝⅞/\s\-]*")


class AlreadyParsed(Exception):
    """Raised when a recipe already has parsed ingredients."""


@dataclass
class RunSummary:
    total_candidates: int = 0
    parsed_successfully: int = 0
    requires_review: int = 0
    skipped_empty: int = 0
    skipped_already_parsed: int = 0


@dataclass
class FoodCreateLogState:
    duplicate_logged: set[str]
    failed_logged: set[str]


def slim(obj: dict[str, Any] | None) -> dict[str, str] | None:
    if isinstance(obj, dict) and obj.get("id"):
        return {"id": str(obj["id"]), "name": str(obj.get("name", ""))}
    return None


def extract_raw_lines(recipe_json: dict[str, Any]) -> list[str]:
    if "recipeIngredient" in recipe_json:
        items = recipe_json["recipeIngredient"]
        if not items:
            return []

        first = items[0]
        if isinstance(first, str):
            return [
                line.strip() for line in items if isinstance(line, str) and line.strip()
            ]

        if isinstance(first, dict):
            all_food_null = all(
                item.get("food") is None for item in items if isinstance(item, dict)
            )
            if not all_food_null:
                raise AlreadyParsed

            lines: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                line = (
                    item.get("originalText")
                    or item.get("rawText")
                    or item.get("note")
                    or QUANTITY_PREFIX_RE.sub("", str(item.get("display", "")))
                )
                line = str(line).strip()
                if line:
                    lines.append(line)
            return lines

    if "ingredients" in recipe_json:
        return [
            str(item.get("rawText", "")).strip()
            for item in recipe_json["ingredients"]
            if isinstance(item, dict) and str(item.get("rawText", "")).strip()
        ]

    return []


def parse_with_fallback(
    client: MealieClient,
    lines: list[str],
    parser_strategies: tuple[str, ...],
    confidence_threshold: float,
) -> tuple[list[dict[str, Any]], str | None, list[dict[str, str]]]:
    attempts: list[dict[str, str]] = []

    for strategy in parser_strategies:
        try:
            parsed = client.parse_ingredients(lines, strategy=strategy)
        except requests.RequestException as exc:
            attempts.append({"strategy": strategy, "error": str(exc)})
            continue

        if not parsed:
            attempts.append({"strategy": strategy, "error": "empty parser response"})
            continue

        if not all(_confidence(item) >= confidence_threshold for item in parsed):
            attempts.append(
                {
                    "strategy": strategy,
                    "error": "below confidence threshold",
                }
            )
            continue

        if any(looks_suspicious(item.get("ingredient") or {}) for item in parsed):
            attempts.append(
                {"strategy": strategy, "error": "suspicious ingredient shape"}
            )
            continue

        return parsed, strategy, attempts

    return [], None, attempts


def ensure_food_object(
    client: MealieClient,
    food: dict[str, Any] | None,
    log_state: FoodCreateLogState,
) -> dict[str, str] | None:
    if not isinstance(food, dict):
        return None

    if food.get("id"):
        return slim(food)

    name = str(food.get("name", "")).strip()
    if not name:
        return None

    try:
        created = client.create_food(name)
    except requests.RequestException as exc:
        if _is_duplicate_food_error(str(exc)):
            if name not in log_state.duplicate_logged:
                LOGGER.info(
                    (
                        "food_create_duplicate name=%r; "
                        "keeping parser result for manual review"
                    ),
                    name,
                )
                log_state.duplicate_logged.add(name)
            return None

        if name not in log_state.failed_logged:
            LOGGER.warning(
                "food_create_failed name=%r error=%s",
                name,
                _short_error(exc),
            )
            log_state.failed_logged.add(name)
        return None
    return slim(created)


def normalize_parsed_block(
    client: MealieClient,
    parsed_block: list[dict[str, Any]],
    log_state: FoodCreateLogState,
) -> tuple[list[dict[str, Any]], bool]:
    normalized: list[dict[str, Any]] = []
    has_suspicious_line = False

    for item in parsed_block:
        ingredient = dict(item.get("ingredient") or {})
        ingredient["food"] = ensure_food_object(
            client,
            ingredient.get("food"),
            log_state,
        )
        ingredient["unit"] = slim(ingredient.get("unit"))
        ingredient.pop("confidence", None)
        ingredient.pop("display", None)

        if looks_suspicious(ingredient):
            has_suspicious_line = True

        normalized.append(ingredient)

    return normalized, has_suspicious_line


def looks_suspicious(ingredient: dict[str, Any]) -> bool:
    note = str(ingredient.get("note", "")).strip().lower()
    if any(phrase in note for phrase in SERVING_PHRASES):
        return False

    quantity = _quantity_value(ingredient.get("quantity"))
    unit = ingredient.get("unit")

    if quantity == 0 and unit is not None:
        return True

    if ingredient.get("food") is None and not note:
        return True

    return False


def _confidence(parsed_line: dict[str, Any]) -> float:
    confidence = parsed_line.get("confidence") or {}
    value = confidence.get("average", 0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _quantity_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_duplicate_food_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "duplicate key value violates unique constraint" in lowered
        and "ingredient_foods_name_group_id_key" in lowered
    )


def _short_error(exc: Exception, max_len: int = 220) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len-3]}..."


def _format_attempts(attempts: list[dict[str, str]]) -> str:
    if not attempts:
        return "none"
    return "; ".join(
        f"{item.get('strategy', '?')}={item.get('error', 'unknown')}"
        for item in attempts
    )


def run_parser(config: ParserConfig) -> RunSummary:
    if not config.base_url:
        raise ValueError("MEALIE_BASE_URL is required")
    if not config.api_token:
        raise ValueError("MEALIE_API_TOKEN is required")
    if not 0 < config.confidence_threshold <= 1:
        raise ValueError("confidence threshold must be between 0 and 1")

    config.output_dir.mkdir(parents=True, exist_ok=True)

    client = MealieClient(
        base_url=config.base_url,
        api_token=config.api_token,
        timeout_seconds=config.timeout_seconds,
        retries=config.request_retries,
        backoff_seconds=config.request_backoff_seconds,
    )

    summary = RunSummary()
    reviews: list[dict[str, Any]] = []
    successes: list[str] = []
    food_log_state = FoodCreateLogState(duplicate_logged=set(), failed_logged=set())

    slugs = client.get_unparsed_recipe_slugs(page_size=config.page_size)

    if config.after_slug:
        try:
            index = slugs.index(config.after_slug)
            slugs = slugs[index + 1 :]
            LOGGER.info(
                "Resuming after '%s' (skipped %s recipes)", config.after_slug, index + 1
            )
        except ValueError:
            LOGGER.warning(
                "AFTER_SLUG '%s' not found; parsing from start", config.after_slug
            )

    if config.max_recipes is not None:
        slugs = slugs[: config.max_recipes]

    summary.total_candidates = len(slugs)

    if not slugs:
        LOGGER.info("No unparsed recipes found")
        return summary

    show_progress_bar = sys.stderr.isatty()
    line_logs = not show_progress_bar
    iterator = tqdm(slugs, desc="Parsing", unit="recipe", disable=not show_progress_bar)

    for index, slug in enumerate(iterator, start=1):
        started = time.monotonic()
        try:
            recipe = client.get_recipe(slug)
        except requests.RequestException as exc:
            reviews.append(
                {
                    "slug": slug,
                    "name": "<unknown>",
                    "reason": "recipe_fetch_failed",
                    "error": str(exc),
                }
            )
            continue

        recipe_name = str(recipe.get("name") or slug)

        try:
            raw_lines = extract_raw_lines(recipe)
        except AlreadyParsed:
            summary.skipped_already_parsed += 1
            continue

        if not raw_lines:
            summary.skipped_empty += 1
            continue

        parsed_block, parser_used, attempts = parse_with_fallback(
            client,
            raw_lines,
            config.parser_strategies,
            config.confidence_threshold,
        )

        if parser_used is None:
            reviews.append(
                {
                    "slug": slug,
                    "name": recipe_name,
                    "reason": "parser_failed_threshold",
                    "raw_lines": raw_lines,
                    "attempts": attempts,
                }
            )
            if line_logs:
                LOGGER.info(
                    (
                        "recipe=%s index=%s/%s status=review "
                        "reason=parser_failed_threshold attempts=%s"
                    ),
                    slug,
                    index,
                    summary.total_candidates,
                    _format_attempts(attempts),
                )
            continue

        normalized, suspicious = normalize_parsed_block(
            client, parsed_block, food_log_state
        )
        if suspicious:
            reviews.append(
                {
                    "slug": slug,
                    "name": recipe_name,
                    "reason": "suspicious_result",
                    "parser": parser_used,
                    "raw_lines": raw_lines,
                    "parsed": normalized,
                }
            )
            if line_logs:
                LOGGER.info(
                    (
                        "recipe=%s index=%s/%s status=review "
                        "reason=suspicious_result parser=%s"
                    ),
                    slug,
                    index,
                    summary.total_candidates,
                    parser_used,
                )
            continue

        if config.dry_run:
            if line_logs:
                LOGGER.info(
                    "recipe=%s index=%s/%s status=dry_run parser=%s",
                    slug,
                    index,
                    summary.total_candidates,
                    parser_used,
                )
        else:
            try:
                client.patch_recipe_ingredients(slug, normalized)
            except requests.RequestException as exc:
                reviews.append(
                    {
                        "slug": slug,
                        "name": recipe_name,
                        "reason": "patch_failed",
                        "parser": parser_used,
                        "error": str(exc),
                        "parsed": normalized,
                    }
                )
                if line_logs:
                    LOGGER.warning(
                        (
                            "recipe=%s index=%s/%s status=review "
                            "reason=patch_failed error=%s"
                        ),
                        slug,
                        index,
                        summary.total_candidates,
                        _short_error(exc),
                    )
                continue

        successes.append(recipe_name)
        summary.parsed_successfully += 1
        if line_logs:
            LOGGER.info(
                "recipe=%s index=%s/%s status=ok parser=%s duration=%.2fs",
                slug,
                index,
                summary.total_candidates,
                parser_used,
                time.monotonic() - started,
            )

        if not show_progress_bar and (
            index == 1 or index == summary.total_candidates or index % 25 == 0
        ):
            LOGGER.info(
                "progress=%s/%s ok=%s review=%s skipped_empty=%s skipped_parsed=%s",
                index,
                summary.total_candidates,
                summary.parsed_successfully,
                len(reviews),
                summary.skipped_empty,
                summary.skipped_already_parsed,
            )

        if config.delay_seconds > 0:
            time.sleep(config.delay_seconds)

    if successes:
        success_path = config.output_dir / config.success_log_filename
        success_path.write_text("\n".join(successes), encoding="utf-8")
        LOGGER.info("Parsed %s recipes; wrote %s", len(successes), success_path)

    if reviews:
        review_path = config.output_dir / config.low_confidence_filename
        review_path.write_text(json.dumps(reviews, indent=2), encoding="utf-8")
        summary.requires_review = len(reviews)
        LOGGER.info("%s recipes need review; wrote %s", len(reviews), review_path)

    return summary
