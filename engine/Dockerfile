# Percentile engine — the private FastAPI service that scores sessions, matches
# occupations and, from M9, renders the PDF reports.  This image is the
# "disposable compute" of plan.md §2: build it once and run it on any free
# container host (Render, Railway, ...) with no code change.
#
# The apt-get line is load-bearing.  WeasyPrint links against these system
# libraries at import time.  CI (.github/workflows/ci.yml) installs exactly the
# same list, and missing them is the number-one cause of "works locally, blank
# PDF in production" (CLAUDE.md · WeasyPrint).  fonts-dejavu-core is the bundled
# fallback family so a report never renders with zero fonts.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/engine

# The package manifest, then the source.  pyproject.tom changes rarely and
# app/ often; keeping the two COPYs separate lets Docker reuse the metadata
# layer when only the code has changed.
COPY pyproject.toml ./
COPY app/ ./app/

# Installed as a real package (not `-e`) so the image does not need the source
# tree at runtime.  setuptools' package-data (pyproject.toml) ships
# app/content/*.yaml and app/report/templates/* with the wheel — required once
# M9 fills those directories.  [dev] extras (pytest, ruff, psycopg) are
# deliberately left out of the image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libfribidi0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && pip install .

# M9 reports: the real report fonts must live in this image too — never be
# fetched from Google Fonts at render time (CLAUDE.md · WeasyPrint), or the PDF
# silently falls back and looks wrong only in production.  When those fonts
# exist (engine/report/fonts/) add, in this same RUN layer:
#   COPY app/report/fonts/ /usr/share/fonts/truetype/percentile
#   RUN fc-cache -f
# keeping the apt-get/ fc-cache / pip install work in one layer.

EXPOSE 8000

# Render and Railway inject $PORT; default to 8000 anywhere else.  Single
# worker on purpose: /tick runs once every five minutes, driven by GitHub
# Actions, and the free instances have one CPU — a second worker would not make
# the batch faster, only slower to cold-start.  `exec` so sh replaces itself
# and SIGTERM from a deploy reaches uvicorn.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]