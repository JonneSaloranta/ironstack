#!/bin/sh
# Builds the production image with full version/build metadata baked
# in (docs/ARCHITECTURE.md "Versioning") — the plain `docker compose
# -f docker-compose.yml up -d --build` from README's "Production"
# section still works fine without this; its image's GIT_SHA file and
# OCI labels just default to "unknown" instead of the real values this
# script fills in.
set -eu

cd "$(dirname "$0")/.."

export APP_VERSION="$(cat VERSION)"
export GIT_SHA="$(git rev-parse --short HEAD)"
export BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

docker compose -f docker-compose.yml build

echo "Built IronStack $APP_VERSION ($GIT_SHA, $BUILD_DATE)"
