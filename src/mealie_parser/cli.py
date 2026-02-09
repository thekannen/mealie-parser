from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from pathlib import Path

from mealie_parser import __version__
from mealie_parser.config import ParserConfig, load_dotenv
from mealie_parser.parser import run_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bulk parse unparsed Mealie recipe ingredients with parser fallback"
        ),
    )
    parser.add_argument("--conf", type=float, help="confidence threshold 0-1")
    parser.add_argument(
        "--max", dest="max_recipes", type=int, help="parse at most N recipes"
    )
    parser.add_argument(
        "--after-slug", help="skip recipes through this slug and resume after"
    )
    parser.add_argument(
        "--parsers",
        help="comma-separated parser order, e.g. nlp,openai",
    )
    parser.add_argument("--force-parser", help="force a single parser strategy")
    parser.add_argument(
        "--page-size", type=int, help="recipes per page when listing candidates"
    )
    parser.add_argument(
        "--delay", type=float, help="delay between successful recipe patches"
    )
    parser.add_argument("--timeout", type=int, help="HTTP timeout seconds")
    parser.add_argument("--retries", type=int, help="HTTP retry count")
    parser.add_argument("--backoff", type=float, help="HTTP retry backoff factor")
    parser.add_argument("--dry-run", action="store_true", help="do not PATCH recipes")
    parser.add_argument("--output-dir", help="directory for output artifacts")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    if verbose:
        logging.getLogger("mealie_parser").setLevel(logging.DEBUG)


def main() -> int:
    load_dotenv(Path(".env"))
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(args.verbose)

    config = ParserConfig.from_env()
    if args.conf is not None:
        config = replace(config, confidence_threshold=args.conf)
    if args.max_recipes is not None:
        config = replace(config, max_recipes=args.max_recipes)
    if args.after_slug is not None:
        config = replace(config, after_slug=args.after_slug)
    if args.parsers:
        parser_strategies = tuple(
            item.strip() for item in args.parsers.split(",") if item.strip()
        )
        config = replace(config, parser_strategies=parser_strategies)
    if args.force_parser:
        config = replace(
            config,
            force_parser=args.force_parser,
            parser_strategies=(args.force_parser,),
        )
    if args.page_size is not None:
        config = replace(config, page_size=args.page_size)
    if args.delay is not None:
        config = replace(config, delay_seconds=args.delay)
    if args.timeout is not None:
        config = replace(config, timeout_seconds=args.timeout)
    if args.retries is not None:
        config = replace(config, request_retries=args.retries)
    if args.backoff is not None:
        config = replace(config, request_backoff_seconds=args.backoff)
    if args.dry_run:
        config = replace(config, dry_run=True)
    if args.output_dir:
        config = replace(config, output_dir=Path(args.output_dir))

    summary = run_parser(config)
    logging.info(
        (
            "Finished. candidates=%s parsed=%s review=%s "
            "skipped_empty=%s skipped_already_parsed=%s"
        ),
        summary.total_candidates,
        summary.parsed_successfully,
        summary.requires_review,
        summary.skipped_empty,
        summary.skipped_already_parsed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
