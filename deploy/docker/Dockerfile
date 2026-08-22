#-----------------------------------------------------------------------------
# Stage 1: UI Build
#-----------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43 AS ui-builder

WORKDIR /src

COPY app/ui/package.json app/ui/pnpm-lock.yaml* app/ui/pnpm-workspace.yaml* ./
RUN corepack enable \
    && pnpm install --frozen-lockfile

COPY app/ui/ .
RUN pnpm build

#-----------------------------------------------------------------------------
# Stage 2: Python Dependencies
#-----------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest-dev@sha256:4bf7e945777010672b8ccd5d2ae2c41c91ad6d3478878347c731ae536d506bef AS python-deps

# This build-only stage installs dependencies and is not the shipped runtime.
# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root
# hadolint ignore=DL3002,DL3066
USER root
WORKDIR /deps

COPY deploy/requirements/runtime.txt ./requirements.txt
COPY deploy/requirements/runtime.constraints.txt ./constraints.txt
RUN apk add --no-cache tzdata=2026c-r0 \
    && python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -c constraints.txt -r requirements.txt \
    && /venv/bin/pip uninstall --yes pip setuptools

COPY scripts/release/copyleft_licenses.py /tmp/copyleft_licenses.py
COPY docs/legal/CORRESPONDING_SOURCE.md /tmp/CORRESPONDING_SOURCE.md
RUN ["/usr/bin/python", "/tmp/copyleft_licenses.py", "--output-dir", "/release-licenses/copyleft", "--source-map", "/tmp/CORRESPONDING_SOURCE.md"]

#-----------------------------------------------------------------------------
# Stage 3: Application
#-----------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest@sha256:1f6779775c9f466890da563e411cb677045a6c20b6a65160eefad1deffb5012c

# The entrypoint needs root only to repair /config ownership for configurable
# PUID/PGID, then verifies a fail-closed UID/GID and group drop before exec.
# nosemgrep
# trivy:ignore:DS-0002
# hadolint ignore=DL3002,DL3066
USER root

ARG VERSION=0.9.16
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG RUNTIME_ABI=channelwatch-runtime-v1

LABEL org.opencontainers.image.title="ChannelWatch"
LABEL org.opencontainers.image.description="Self-hosted Channels DVR monitoring with multi-DVR dashboards, alerts, health checks, and notifications"
LABEL org.opencontainers.image.source="https://github.com/CoderLuii/ChannelWatch"
LABEL org.opencontainers.image.url="https://hub.docker.com/r/coderluii/channelwatch"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL org.opencontainers.image.licenses="MIT"
LABEL maintainer="CoderLuii"

ENV PATH="/venv/bin:${PATH}"
ENV PYTHONPATH="/app"
ENV CONFIG_PATH="/config"
ENV CHANNELWATCH_IMAGE_APP_DIR="/app"
ENV CHANNELWATCH_RUNTIME_ABI="${RUNTIME_ABI}"
ENV CHANNELWATCH_IMAGE_VERSION="${VERSION}"
ENV CHANNELWATCH_REPORT_MODE="live"
ENV CHANNELWATCH_REPORT_ENDPOINT="https://channelwatch.coderluii.dev/api/reports"
ENV CHANNELWATCH_REPORT_PORTAL_URL="https://channelwatch.coderluii.dev/report"

RUN ["/usr/bin/python", "-c", "from pathlib import Path; [Path(path).mkdir(parents=True, exist_ok=True) for path in ('/app/core', '/app/ui/backend/static_ui', '/app/ui/backend/static/images', '/config', '/etc/supervisor/conf.d')]"]

WORKDIR /app

COPY --from=python-deps /venv /venv
COPY --from=python-deps /usr/share/zoneinfo /usr/share/zoneinfo

COPY app/core/ /app/core/
COPY app/ui/backend/ /app/ui/backend/
COPY app/bin/channelwatch /usr/local/bin/channelwatch
COPY deploy/config/supervisor/supervisord.conf.template /etc/supervisor/conf.d/supervisord.conf.template
COPY LICENSE /licenses/channelwatch/LICENSE
COPY docs/legal/NOTICE /licenses/channelwatch/NOTICE
COPY docs/legal/THIRD_PARTY_LICENSES.md /licenses/channelwatch/THIRD_PARTY_LICENSES.md
COPY --from=python-deps /release-licenses/copyleft /licenses/channelwatch/copyleft
COPY --from=ui-builder /src/out /app/ui/backend/static_ui/
COPY app/ui/public/images /app/ui/backend/static/images/

RUN ["/usr/bin/python", "-c", "import json, os; from pathlib import Path; Path('/app/channelwatch-image.json').write_text(json.dumps({'version': os.environ.get('CHANNELWATCH_IMAGE_VERSION', 'unknown'), 'runtime_abi': os.environ.get('CHANNELWATCH_RUNTIME_ABI', 'channelwatch-runtime-v1'), 'settings_schema_version': 7}, indent=2) + '\\n'); os.chmod('/usr/local/bin/channelwatch', 0o755)"]

VOLUME /config
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/usr/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/healthz/live', timeout=3).read()"]

ENTRYPOINT ["/venv/bin/python", "/app/core/docker-entrypoint.py"]
CMD ["/venv/bin/python", "-m", "supervisor.supervisord", "-c", "/tmp/supervisord.conf"]
