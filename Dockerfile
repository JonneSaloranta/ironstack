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

# postgresql-client-16 (not Debian's own default `postgresql-client`,
# whatever major version that happens to be — currently 17, a mismatch
# from db's `postgres:16-alpine` in docker-compose.yml causes pg_dump/
# pg_restore to embed/expect session settings the *server* doesn't
# recognize, e.g. "unrecognized configuration parameter
# transaction_timeout") comes from the official PostgreSQL apt
# repository, not Debian's, since Debian trixie only ships one
# postgresql-client version at a time. Keep this "16" and
# docker-compose.yml's `postgres:16-alpine` in sync if the server's
# major version is ever upgraded — apps.core.backups (the web-UI
# backup feature) is what actually needs a matching client. Only in
# the runtime stage, not `base`: the build stage needs its own,
# untouched ca-certificates for pip's own HTTPS fetches, and purging
# curl/gnupg again afterward here doesn't risk that.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/pgdg.list

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

RUN mkdir -p /app/staticfiles /app/media /app/backups && chown -R django:django /app
USER django
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
