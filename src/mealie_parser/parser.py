from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import requests
from tqdm import tqdm

from mealie_parser.client import MealieClient
from mealie_parser.config import ParserConfig

LOGGER = logging.getLogger(__name__)

SERVING_PHRASES = {"for serving", "for garnish", "for dipping"}
QUANTITY_PREFIX_RE = re.compile(r"^\s*\d+[¼½¾⅓⅔⅛⅜⅝⅞/\s\-]*")
NON_INGREDIENT_PREFIX_RE = re.compile(r"^(for|to)\s+", re.IGNORECASE)
ZERO_QTY_ALLOWED_UNITS = {"pinch", "dash"}
FRACTION_TEXT_REPLACEMENTS = {
    "¹/₂": "1/2",
    "¹/₄": "1/4",
    "³/₄": "3/4",
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}


class AlreadyParsed(Exception):
    """Raised when a recipe already has parsed ingredients."""


@dataclass
class RunSummary:
    total_candidates: int = 0
    parsed_successfully: int = 0
    requires_review: int = 0
    skipped_empty: int = 0
    skipped_already_parsed: int = 0
    dropped_blank_ingredients: int = 0


@dataclass
class FoodCreateLogState:
    duplicate_logged: set[str]
    failed_logged: set[str]


@dataclass
class ReviewLogState:
    signature_counts: dict[str, int]


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


def sanitize_raw_lines(lines: list[str]) -> tuple[list[str], int]:
    cleaned: list[str] = []
    dropped = 0

    for raw in lines:
        line = _normalize_line_text(raw)
        if not line:
            dropped += 1
            continue
        if _is_non_ingredient_header(line):
            dropped += 1
            continue
        cleaned.append(line)

    return cleaned, dropped


def _normalize_line_text(line: str) -> str:
    normalized = str(line).strip()
    for old, new in FRACTION_TEXT_REPLACEMENTS.items():
        normalized = normalized.replace(old, new)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_non_ingredient_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True

    # e.g. "DRESSING:", "For the tart shell", "To serve"
    if (
        stripped.endswith(":")
        and len(stripped.split()) <= 8
        and not re.search(r"\d", stripped)
    ):
        return True
    if NON_INGREDIENT_PREFIX_RE.match(stripped) and len(stripped.split()) <= 8:
        if not re.search(r"\d", stripped):
            return True

    return False


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
            attempts.append({"strategy": strategy, "error": _short_text(str(exc))})
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

        suspicious_counts = _suspicious_reason_counts(
            [item.get("ingredient") or {} for item in parsed]
        )
        if suspicious_counts:
            attempts.append(
                {
                    "strategy": strategy,
                    "error": f"suspicious:{_format_reason_counts(suspicious_counts)}",
                }
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
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    normalized: list[dict[str, Any]] = []
    suspicious_reasons: dict[str, int] = defaultdict(int)
    dropped_blank = 0

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

        if _is_blank_ingredient(ingredient):
            dropped_blank += 1
            continue

        reason = _suspicion_reason(ingredient)
        if reason is not None:
            suspicious_reasons[reason] += 1

        normalized.append(ingredient)

    return normalized, dict(suspicious_reasons), dropped_blank


def looks_suspicious(ingredient: dict[str, Any]) -> bool:
    return _suspicion_reason(ingredient) is not None


def _suspicion_reason(ingredient: dict[str, Any]) -> str | None:
    if _is_blank_ingredient(ingredient):
        return None

    note = str(ingredient.get("note", "")).strip().lower()
    if any(phrase in note for phrase in SERVING_PHRASES):
        return None

    quantity = _quantity_value(ingredient.get("quantity"))
    unit = ingredient.get("unit")
    unit_name = _entity_name(unit)

    if quantity == 0 and unit is not None:
        if unit_name in ZERO_QTY_ALLOWED_UNITS or "to taste" in note:
            return None
        return "zero_qty_with_unit"

    if ingredient.get("food") is None and not note:
        return "missing_food_no_note"

    return None


def _is_blank_ingredient(ingredient: dict[str, Any]) -> bool:
    note = str(ingredient.get("note", "")).strip()
    quantity = _quantity_value(ingredient.get("quantity"))
    has_food = _has_entity(ingredient.get("food"))
    has_unit = _has_entity(ingredient.get("unit"))
    return not note and quantity == 0 and not has_food and not has_unit


def _has_entity(entity: Any) -> bool:
    if entity is None:
        return False
    if isinstance(entity, dict):
        entity_id = str(entity.get("id") or "").strip()
        entity_name = str(entity.get("name") or "").strip()
        return bool(entity_id or entity_name)
    return True


def _entity_name(entity: Any) -> str:
    if not isinstance(entity, dict):
        return ""
    return str(entity.get("name") or "").strip().lower()


def _suspicious_reason_counts(ingredients: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for ingredient in ingredients:
        reason = _suspicion_reason(ingredient)
        if reason is not None:
            counts[reason] += 1
    return dict(counts)


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


def _short_text(text: str, max_len: int = 220) -> str:
    clean = text.replace("\n", " ").strip()
    if len(clean) <= max_len:
        return clean
    return f"{clean[:max_len-3]}..."


def _short_error(exc: Exception, max_len: int = 220) -> str:
    return _short_text(str(exc), max_len=max_len)


def _format_attempts(attempts: list[dict[str, str]]) -> str:
    if not attempts:
        return "none"
    return "; ".join(
        f"{item.get('strategy', '?')}={item.get('error', 'unknown')}"
        for item in attempts
    )


def _format_reason_counts(reason_counts: dict[str, int]) -> str:
    if not reason_counts:
        return "none"
    pairs = [f"{reason}:{count}" for reason, count in sorted(reason_counts.items())]
    return ",".join(pairs)


def _should_emit_review_log(
    log_state: ReviewLogState,
    signature: str,
    *,
    first_n: int = 3,
    every_n: int = 10,
) -> tuple[bool, int]:
    count = log_state.signature_counts.get(signature, 0) + 1
    log_state.signature_counts[signature] = count
    should_emit = count <= first_n or count % every_n == 0
    return should_emit, count


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
    review_log_state = ReviewLogState(signature_counts={})

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

        raw_lines, dropped_input_lines = sanitize_raw_lines(raw_lines)
        if dropped_input_lines and line_logs:
            LOGGER.info(
                "recipe=%s index=%s/%s status=cleanup dropped_input_lines=%s",
                slug,
                index,
                summary.total_candidates,
                dropped_input_lines,
            )

        if not raw_lines:
            summary.skipped_empty += 1
            if line_logs:
                LOGGER.info(
                    (
                        "recipe=%s index=%s/%s status=skip "
                        "reason=no_ingredient_lines_after_input_cleanup"
                    ),
                    slug,
                    index,
                    summary.total_candidates,
                )
            continue

        parsed_block, parser_used, attempts = parse_with_fallback(
            client,
            raw_lines,
            config.parser_strategies,
            config.confidence_threshold,
        )

        if parser_used is None:
            attempts_text = _format_attempts(attempts)
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
                signature = f"parser_failed_threshold|{attempts_text}"
                should_emit, seen = _should_emit_review_log(review_log_state, signature)
                if should_emit:
                    LOGGER.info(
                        (
                            "recipe=%s index=%s/%s status=review "
                            "reason=parser_failed_threshold attempts=%s seen=%s"
                        ),
                        slug,
                        index,
                        summary.total_candidates,
                        attempts_text,
                        seen,
                    )
            continue

        normalized, suspicious_reasons, dropped_blank = normalize_parsed_block(
            client, parsed_block, food_log_state
        )
        if dropped_blank:
            summary.dropped_blank_ingredients += dropped_blank
            if line_logs:
                LOGGER.info(
                    "recipe=%s index=%s/%s status=cleanup dropped_blank=%s",
                    slug,
                    index,
                    summary.total_candidates,
                    dropped_blank,
                )

        if not normalized:
            reviews.append(
                {
                    "slug": slug,
                    "name": recipe_name,
                    "reason": "no_usable_ingredients_after_cleanup",
                    "parser": parser_used,
                    "raw_lines": raw_lines,
                }
            )
            if line_logs:
                LOGGER.info(
                    (
                        "recipe=%s index=%s/%s status=review "
                        "reason=no_usable_ingredients_after_cleanup parser=%s"
                    ),
                    slug,
                    index,
                    summary.total_candidates,
                    parser_used,
                )
            continue

        if suspicious_reasons:
            suspicious_text = _format_reason_counts(suspicious_reasons)
            reviews.append(
                {
                    "slug": slug,
                    "name": recipe_name,
                    "reason": "suspicious_result",
                    "parser": parser_used,
                    "raw_lines": raw_lines,
                    "parsed": normalized,
                    "suspicious_reasons": suspicious_reasons,
                }
            )
            if line_logs:
                signature = f"suspicious_result|{parser_used}|{suspicious_text}"
                should_emit, seen = _should_emit_review_log(review_log_state, signature)
                if should_emit:
                    LOGGER.info(
                        (
                            "recipe=%s index=%s/%s status=review "
                            "reason=suspicious_result parser=%s suspicious=%s seen=%s"
                        ),
                        slug,
                        index,
                        summary.total_candidates,
                        parser_used,
                        suspicious_text,
                        seen,
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
                    patch_error = _short_error(exc)
                    signature = f"patch_failed|{patch_error}"
                    should_emit, seen = _should_emit_review_log(
                        review_log_state, signature
                    )
                    if should_emit:
                        LOGGER.warning(
                            (
                                "recipe=%s index=%s/%s status=review "
                                "reason=patch_failed error=%s seen=%s"
                            ),
                            slug,
                            index,
                            summary.total_candidates,
                            patch_error,
                            seen,
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

    if review_log_state.signature_counts:
        top_signatures = sorted(
            review_log_state.signature_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
        signature_text = " | ".join(
            f"{count}x {_short_text(signature, max_len=120)}"
            for signature, count in top_signatures
        )
        LOGGER.info("review_signature_summary top=%s", signature_text)

    return summary
