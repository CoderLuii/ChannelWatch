#-----------------------------------------------------------------------------
# Stage 1: UI Build
#-----------------------------------------------------------------------------
FROM node:18-alpine AS ui-builder

WORKDIR /src
COPY ui/ .

RUN npm install -g pnpm
RUN pnpm install
RUN pnpm build 

#-----------------------------------------------------------------------------
# Stage 2: Application
#-----------------------------------------------------------------------------
FROM python:alpine

# System setup
ARG UID=1000
ARG GID=1000
RUN addgroup -g ${GID} -S appgroup \
    && adduser -u ${UID} -S appuser -G appgroup

# Metadata
LABEL maintainer="CoderLuii"
LABEL version="0.7"
LABEL description="ChannelWatch - Channels DVR Monitoring Tool"

# Directory structure
RUN mkdir -p /app/core \
             /app/ui/static_ui \
             /config \
             /etc/supervisor/conf.d/

WORKDIR /app

# Dependencies
COPY requirements.txt .
RUN apk add --no-cache build-base libffi-dev tzdata grep sed su-exec \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del build-base libffi-dev

# Application files
COPY core/ /app/core/
COPY ui/backend/config.py ui/backend/main.py ui/backend/schemas.py /app/ui/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY --from=ui-builder /src/out /app/ui/static_ui/
COPY ui/public/images /app/ui/static/images/

# Permissions
RUN chmod +x /app/core/docker-entrypoint.sh
RUN chown -R appuser:appgroup /app /config

# Runtime
EXPOSE 8501
ENTRYPOINT ["/bin/sh", "/app/core/docker-entrypoint.sh"]
CMD ["/usr/local/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
