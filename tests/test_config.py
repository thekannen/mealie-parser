from __future__ import annotations

import os
from pathlib import Path

from mealie_parser.config import ParserConfig, load_dotenv


def test_load_dotenv_sets_missing_vars(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXAMPLE_KEY=example_value\n", encoding="utf-8")

    os.environ.pop("EXAMPLE_KEY", None)
    load_dotenv(env_file)

    assert os.environ["EXAMPLE_KEY"] == "example_value"


def test_from_env_parses_strategy_and_force_parser(monkeypatch) -> None:
    monkeypatch.setenv("MEALIE_BASE_URL", "http://localhost:9000/api")
    monkeypatch.setenv("MEALIE_API_TOKEN", "token")
    monkeypatch.setenv("PARSER_STRATEGIES", "nlp,openai")
    monkeypatch.setenv("FORCE_PARSER", "openai")

    config = ParserConfig.from_env()

    assert config.parser_strategies == ("openai",)
    assert config.force_parser == "openai"


def test_from_env_uses_mealie_api_key_fallback(monkeypatch) -> None:
    monkeypatch.setenv("MEALIE_BASE_URL", "http://localhost:9000/api")
    monkeypatch.delenv("MEALIE_API_TOKEN", raising=False)
    monkeypatch.setenv("MEALIE_API_KEY", "token-from-key")

    config = ParserConfig.from_env()

    assert config.api_token == "token-from-key"
