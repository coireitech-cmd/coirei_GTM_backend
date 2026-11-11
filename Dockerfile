# One image for the API, the worker and one-off commands (migrations, prune).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY gtm_crawler ./gtm_crawler
RUN pip install . \
    && playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 gtm \
    && mkdir -p /data/raw && chown gtm /data/raw

# Never run the crawler (or Chromium) as root.
USER gtm
ENV GTM__RAWSTORE__ROOT=/data/raw
VOLUME ["/data/raw"]

CMD ["gtm-worker"]
