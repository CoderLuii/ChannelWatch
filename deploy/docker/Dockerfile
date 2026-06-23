#-----------------------------------------------------------------------------
# Stage 1: UI Build
#-----------------------------------------------------------------------------
FROM node:24-alpine AS ui-builder

WORKDIR /src

COPY app/ui/package.json app/ui/pnpm-lock.yaml* app/ui/pnpm-workspace.yaml* ./
RUN corepack enable
RUN pnpm install --frozen-lockfile

COPY app/ui/ .
RUN pnpm build

#-----------------------------------------------------------------------------
# Stage 2: Python Dependencies
#-----------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest-dev AS python-deps

USER root
WORKDIR /deps

COPY deploy/requirements/runtime.txt ./requirements.txt
RUN apk add --no-cache tzdata \
    && python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

#-----------------------------------------------------------------------------
# Stage 3: Application
#-----------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest

USER root

ARG VERSION=0.9.3
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

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

RUN ["/usr/bin/python", "-c", "from pathlib import Path; [Path(path).mkdir(parents=True, exist_ok=True) for path in ('/app/core', '/app/ui/backend/static_ui', '/app/ui/backend/static/images', '/config', '/etc/supervisor/conf.d')]"]

WORKDIR /app

COPY --from=python-deps /venv /venv
COPY --from=python-deps /usr/share/zoneinfo /usr/share/zoneinfo

COPY app/core/ /app/core/
COPY app/ui/backend/ /app/ui/backend/
COPY app/bin/channelwatch /usr/local/bin/channelwatch
COPY deploy/config/supervisor/supervisord.conf.template /etc/supervisor/conf.d/supervisord.conf.template
COPY --from=ui-builder /src/out /app/ui/backend/static_ui/
COPY app/ui/public/images /app/ui/backend/static/images/

RUN ["/usr/bin/python", "-c", "import os; os.chmod('/usr/local/bin/channelwatch', 0o755)"]

VOLUME /config
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/usr/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/healthz/live', timeout=3).read()"]

ENTRYPOINT ["/usr/bin/python", "/app/core/docker-entrypoint.py"]
CMD ["/venv/bin/python", "-m", "supervisor.supervisord", "-c", "/tmp/supervisord.conf"]
