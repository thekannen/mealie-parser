from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PARSER_STRATEGIES = ("nlp", "openai")


@dataclass(frozen=True)
class ParserConfig:
    base_url: str
    api_token: str
    confidence_threshold: float = 0.80
    parser_strategies: tuple[str, ...] = DEFAULT_PARSER_STRATEGIES
    force_parser: str | None = None
    page_size: int = 200
    delay_seconds: float = 0.10
    timeout_seconds: int = 30
    request_retries: int = 3
    request_backoff_seconds: float = 0.4
    max_recipes: int | None = None
    after_slug: str | None = None
    dry_run: bool = False
    output_dir: Path = Path("reports")
    low_confidence_filename: str = "review_low_confidence.json"
    success_log_filename: str = "parsed_success.log"

    @classmethod
    def from_env(cls) -> ParserConfig:
        parser_strategies = tuple(
            item.strip()
            for item in os.getenv(
                "PARSER_STRATEGIES", ",".join(DEFAULT_PARSER_STRATEGIES)
            ).split(",")
            if item.strip()
        )

        force_parser = _str_or_none(os.getenv("FORCE_PARSER"))

        if force_parser:
            parser_strategies = (force_parser,)
        else:
            parser_strategies = _ensure_fallback_strategies(parser_strategies)

        api_token = (
            os.getenv("MEALIE_API_TOKEN", "").strip()
            or os.getenv("MEALIE_API_KEY", "").strip()
        )

        return cls(
            base_url=_clean_base_url(os.getenv("MEALIE_BASE_URL", "")),
            api_token=api_token,
            confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.80")),
            parser_strategies=parser_strategies or DEFAULT_PARSER_STRATEGIES,
            force_parser=force_parser,
            page_size=int(os.getenv("PAGE_SIZE", "200")),
            delay_seconds=float(os.getenv("DELAY_SECONDS", "0.10")),
            timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
            request_retries=int(os.getenv("REQUEST_RETRIES", "3")),
            request_backoff_seconds=float(os.getenv("REQUEST_BACKOFF_SECONDS", "0.4")),
            max_recipes=_int_or_none(os.getenv("MAX_RECIPES")),
            after_slug=_str_or_none(os.getenv("AFTER_SLUG")),
            dry_run=_parse_bool(os.getenv("DRY_RUN", "false")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "reports")),
            low_confidence_filename=os.getenv(
                "LOW_CONFIDENCE_FILE", "review_low_confidence.json"
            ).strip(),
            success_log_filename=os.getenv(
                "SUCCESS_FILE", "parsed_success.log"
            ).strip(),
        )


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists() or not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _str_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    return int(value) if value else None


def _clean_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    return value


def _ensure_fallback_strategies(strategies: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    for strategy in (*strategies, *DEFAULT_PARSER_STRATEGIES):
        if strategy not in ordered:
            ordered.append(strategy)
    return tuple(ordered)
