# syntax=docker/dockerfile:1
#
# claudio-web — built React SPA served by nginx, reverse-proxying /api to the agent.
#
# Two stages: Node builds the static bundle, nginx serves it. The runtime image
# carries no Node toolchain and no secrets, so it is small and rebuilds in seconds.

# ---- build stage ------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# Lockfile first so the dependency layer is cached across source edits.
COPY claudio-frontend/package.json claudio-frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY claudio-frontend/ ./
RUN npm run build          # -> /app/dist

# ---- runtime stage ----------------------------------------------------------
FROM nginx:1.27-alpine

# The config is a full main config; it replaces nginx.conf, not conf.d/default.conf.
COPY docker/web/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html

# nginx's own writable scratch (cache/pid/client-body) lives under /tmp and
# /var/cache/nginx; make sure it exists and is owned by the worker user.
#
# NOTE: `nginx -t` is deliberately NOT run here. nginx resolves upstream hostnames
# at config-parse time, and `claudio-agent` does not exist until the compose
# network does. nginx validates its own config on every start, and the smoke test
# asserts it, so build-time validation would only ever produce a false failure.
RUN mkdir -p /var/cache/nginx /var/log/nginx /tmp \
    && chown -R nginx:nginx /var/cache/nginx /var/log/nginx /tmp

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
