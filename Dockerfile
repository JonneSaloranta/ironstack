# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 gettext \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM base AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements/ requirements/
ARG REQUIREMENTS_FILE=requirements/production.txt
RUN python -m venv /venv && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install -r ${REQUIREMENTS_FILE}

FROM base AS runtime
COPY --from=build /venv /venv
ENV PATH="/venv/bin:${PATH}"
RUN useradd --create-home --uid 1000 django
COPY . .

# Build-time metadata (docs/ARCHITECTURE.md "Versioning") — none of
# these are required for `docker compose up -d --build` to work; they
# default to "unknown" so that simple path is unaffected.
# scripts/build.sh fills them in for anyone who wants full version/
# build metadata baked into the image and its OCI labels.
ARG GIT_SHA=unknown
ARG APP_VERSION=unknown
ARG BUILD_DATE=unknown
RUN echo "$GIT_SHA" > GIT_SHA
LABEL org.opencontainers.image.title="IronStack" \
      org.opencontainers.image.description="Self-hosted, mobile-first fitness and activity tracker" \
      org.opencontainers.image.version="$APP_VERSION" \
      org.opencontainers.image.revision="$GIT_SHA" \
      org.opencontainers.image.created="$BUILD_DATE"

RUN mkdir -p /app/staticfiles /app/media && chown -R django:django /app
USER django
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
