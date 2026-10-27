# syntax=docker/dockerfile:1
#
# claudio-agent — the Python backend (FastAPI/uvicorn + Google ADK runtime).
#
# Base is python:3.11-slim deliberately: the host venv everything was developed
# and tested against is 3.11.16, and `requires-python >= 3.11` forbids anything
# older. Matching the interpreter exactly means a surprise is attributable to one
# layer, not two.
#
# The repo is copied to /srv/claudio and installed in EDITABLE mode. This is not
# laziness — it is the fix for a real constraint. Both `app.py` and `paths.py`
# derive the runtime base directory from `Path(__file__).resolve().parents[2]`,
# which for a src-layout package is the `claudio-backend` directory. A regular
# (non-editable) install relocates the module into site-packages, which moves
# that base to /usr/local/lib/python3.11 and silently breaks the config volume,
# the skills directory and the data directory. Verified on the host:
#   BASE_DIR -> <repo>/claudio-backend   (editable)
#   BASE_DIR -> /usr/local/lib/python3.11 (non-editable)
# So the source tree must stay where it is mounted, and the volumes are mounted
# at <repo>/claudio-backend/{config,data,skills}.

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git/curl: the agent's run_command and web_fetch tools assume a normal shell
# environment. ca-certificates is required for the https MCP server and any
# https LLM endpoint. tzdata for TZ handling.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        tzdata \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Unprivileged runtime user. Defaults match the host user (uid/gid 1000) so
# bind-mounted repos stay writable without group gymnastics; override at build
# time with --build-arg if the host user differs.
ARG CLAUDIO_UID=1000
ARG CLAUDIO_GID=1000
RUN groupadd --gid "${CLAUDIO_GID}" claudio \
    && useradd --uid "${CLAUDIO_UID}" --gid "${CLAUDIO_GID}" --create-home \
         --shell /bin/bash claudio

WORKDIR /srv/claudio

# Full repo. The agent edits real repositories and resolves paths relative to
# this tree, so it needs the checkout, not just the installed package.
COPY . /srv/claudio/

# Install both packages editable (see header note). Core first: the backend
# depends on it.
RUN pip install --no-cache-dir -e ./claudio-core \
    && pip install --no-cache-dir -e ./claudio-backend

# Writable state the container owns. Each of these is overridden by a named
# volume at runtime; creating them here sets the correct ownership for the
# volume's first-use initialisation.
RUN mkdir -p \
        /srv/claudio/claudio-backend/config \
        /srv/claudio/claudio-backend/data \
        /srv/claudio/claudio-backend/data/logs \
        /srv/claudio/claudio-backend/skills \
        /workspace \
    && chown -R claudio:claudio /srv/claudio /workspace

# Pristine seed copy kept OUTSIDE the mounted config directory. Docker copies
# image content into an empty *named* volume, but never into a *bind* mount, so
# the entrypoint cannot rely on the example surviving the mount. This copy always
# exists regardless of how the config volume is provided.
RUN mkdir -p /opt/claudio/seed \
    && cp claudio-backend/config/config.example.yaml /opt/claudio/seed/config.example.yaml \
    && chown -R claudio:claudio /opt/claudio

COPY docker/backend/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER claudio:claudio

EXPOSE 8100

# Liveness only. /healthz is unauthenticated on purpose: a readiness probe would
# need the bearer token, and a secret in a healthcheck buys nothing while adding
# a leak path. Deep health (auth'd endpoints) is checked by claudio-ctl.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8100/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
