#-----------------------------------------------------------------------------
# Stage 1: UI Build
#-----------------------------------------------------------------------------
FROM node:24-alpine AS ui-builder

WORKDIR /src
COPY ui/ .

RUN corepack enable
RUN pnpm install --frozen-lockfile
RUN pnpm build 

#-----------------------------------------------------------------------------
# Stage 2: Python Dependencies
#-----------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest-dev AS python-deps

USER root
WORKDIR /deps

COPY requirements.txt .
RUN apk add --no-cache tzdata \
    && python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

#-----------------------------------------------------------------------------
# Stage 3: Application
#-----------------------------------------------------------------------------
FROM cgr.dev/chainguard/python:latest

USER root

# Metadata
LABEL maintainer="CoderLuii"
LABEL version="0.8.3"
LABEL description="ChannelWatch - Channels DVR Monitoring Tool"

ENV PATH="/venv/bin:${PATH}"
ENV PYTHONPATH="/app"

# Directory structure
RUN ["/usr/bin/python", "-c", "from pathlib import Path; [Path(path).mkdir(parents=True, exist_ok=True) for path in ('/app/core', '/app/ui/static_ui', '/config', '/etc/supervisor/conf.d')]"]

WORKDIR /app

# Dependencies
COPY --from=python-deps /venv /venv
COPY --from=python-deps /usr/share/zoneinfo /usr/share/zoneinfo

# Application files
COPY core/ /app/core/
COPY ui/backend/config.py ui/backend/main.py ui/backend/schemas.py /app/ui/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY --from=ui-builder /src/out /app/ui/static_ui/
COPY ui/public/images /app/ui/static/images/

# Runtime
EXPOSE 8501
ENTRYPOINT ["/usr/bin/python", "/app/core/docker-entrypoint.py"]
CMD ["/venv/bin/python", "-m", "supervisor.supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
