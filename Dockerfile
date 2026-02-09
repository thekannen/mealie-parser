FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md VERSION ./
COPY src ./src
COPY scripts ./scripts

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

RUN mkdir -p /app/reports

ENTRYPOINT ["/app/scripts/docker/entrypoint.sh"]
CMD []
