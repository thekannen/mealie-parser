# Mealie Parser

Mealie Parser bulk-parses unparsed recipe ingredients in Mealie and safely patches structured ingredient data back to recipes.

It supports:
- Parser fallback order (for example `nlp` then `openai`)
- Confidence threshold filtering
- Suspicious-line detection and review report output
- Dry-run mode
- Docker one-shot or loop execution
- Manual Linux/macOS execution

## Credit

This project is a refreshed fork of the original script authored by **Nathan Lynch** (`N Lynch`) and keeps that parser workflow as the foundation.

## Project layout

```text
.
├── scripts/
│   ├── docker/
│   │   ├── entrypoint.sh
│   │   └── update.sh
│   └── install/
│       └── ubuntu_setup_mealie_parser.sh
├── src/mealie_parser/
├── tests/
├── .env.example
├── docker-compose.yml
└── README.md
```

## Configuration model

- `.env`: environment-specific settings and secrets
- CLI args: per-run overrides

Priority order:
1. CLI flags
2. Environment variables (`.env`, container env)
3. Built-in defaults

## Environment variables

Required:
- `MEALIE_BASE_URL` (example: `http://192.168.1.50:9000/api`)
- `MEALIE_API_TOKEN` (or `MEALIE_API_KEY`)

Common optional:
- `CONFIDENCE_THRESHOLD` (default `0.80`)
- `PARSER_STRATEGIES` (default `nlp,openai`)
- `FORCE_PARSER` (override fallback order)
- `DRY_RUN` (`true` or `false`)
- `MAX_RECIPES` (limit count for trial runs)
- `AFTER_SLUG` (resume from a known slug)
- `OUTPUT_DIR` (default `reports`)

See `.env.example` for full options.

## Quick start (Docker)

1. Clone and enter the repo.

```bash
git clone https://github.com/thekannen/mealie-parser.git
cd mealie-parser
```

2. Create env file.

```bash
cp .env.example .env
```

3. Edit `.env` with your Mealie URL and API token.

4. Build and run.

```bash
docker compose up --build
```

If you want continuously visible logs in Docker/Portainer, run loop mode and verbose parser logs:

```bash
docker compose up -d --build
docker compose run --rm \
  -e RUN_MODE=loop \
  -e RUN_INTERVAL_SECONDS=21600 \
  -e PARSER_ARGS="--verbose" \
  mealie-parser
```

To inspect logs from CLI:

```bash
docker compose logs -f --tail=200 mealie-parser
```

Update to latest published git + redeploy:

```bash
./scripts/docker/update.sh
```

Useful options:
- `--skip-git-pull`
- `--no-build`
- `--branch <name>`
- `--prune`

Loop mode (every 6 hours):

```bash
docker compose run --rm \
  -e RUN_MODE=loop \
  -e RUN_INTERVAL_SECONDS=21600 \
  mealie-parser
```

One-shot mode (run once and exit cleanly):

```bash
docker compose run --rm \
  -e RUN_MODE=once \
  mealie-parser
```

Notes:
- Portainer's `No log line matching the '' filter` usually means the container produced no log output yet, often because one-shot mode exited quickly.
- Exact weekly scheduling at Sunday 6:00 AM is best done with host cron/systemd timers; interval loop mode cannot align to weekday/time boundaries by itself.
- In non-interactive runs (Docker/Portainer), parser output is emitted as one structured line per recipe instead of a tqdm progress bar.
- `docker-compose.yml` uses `restart: on-failure` so successful `RUN_MODE=once` runs do not auto-restart in a tight loop.

## Local development / manual Linux run

1. Create venv and install.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

2. Create env file.

```bash
cp .env.example .env
```

3. Run parser.

```bash
python -m mealie_parser
```

Useful CLI options:

```bash
# Trial run
python -m mealie_parser --max 20 --dry-run

# Higher confidence and forced parser
python -m mealie_parser --conf 0.9 --force-parser openai

# Resume after a specific slug
python -m mealie_parser --after-slug chicken-tikka-masala
```

Installed CLI after `pip install -e .`:

```bash
mealie-parser --help
```

## Ubuntu helper

You can bootstrap dependencies and venv on Ubuntu:

```bash
./scripts/install/ubuntu_setup_mealie_parser.sh
```

Optional flags:
- `--repo-dir <path>`
- `--setup-cron`
- `--cron-schedule "0 */6 * * *"`

Sunday at 6:00 AM schedule:

```bash
./scripts/install/ubuntu_setup_mealie_parser.sh \
  --repo-dir "/Users/aaron/Library/CloudStorage/GoogleDrive-thekannengieser@gmail.com/My Drive/Repos/mealie-parser" \
  --setup-cron \
  --cron-schedule "0 6 * * 0"
```

## Output artifacts

By default under `reports/`:
- `parsed_success.log`: recipe names successfully parsed/patched
- `review_low_confidence.json`: recipes requiring manual review

## Testing

```bash
pytest
```
