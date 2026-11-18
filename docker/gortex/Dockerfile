# syntax=docker/dockerfile:1
#
# claudio-gortex — Gortex code-intelligence daemon exposing the MCP Streamable
# HTTP transport (issue #54, Option B).
#
# Why a separate container: a `transport: stdio` MCP server is spawned as a child
# of the backend process, so it would have to be baked into the agent image and
# its daemon would live/die with the agent. Running Gortex as its own service
# isolates the daemon lifecycle, indexing and index persistence from the agent,
# and lets the agent connect over HTTP by service name on the internal bridge.
#
# The daemon serves the HTTP/JSON API under /v1/* PLUS the MCP transport at /mcp
# when started with --http-addr (see gortex docs/cli.md). The agent's MCP entry
# therefore points at http://gortex:<port>/mcp.

FROM debian:bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl + ca-certificates: fetch the signed release. git: Gortex reads git
# metadata (HEAD, worktrees) when indexing a repository.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install the Gortex binary system-wide so it is on the default PATH
# (/usr/local/bin). GORTEX_NO_PATH=1 skips the shell-rc edit (there is no
# interactive shell here). Pin a version with --build-arg GORTEX_VERSION=vX.Y.Z
# for reproducible builds; empty installs the latest signed release.
ARG GORTEX_VERSION=
RUN set -eux; \
    if [ -n "${GORTEX_VERSION}" ]; then export GORTEX_VERSION; fi; \
    curl -fsSL https://get.gortex.dev \
      | GORTEX_INSTALL_DIR=/usr/local/bin GORTEX_NO_PATH=1 sh; \
    gortex version

# Unprivileged runtime user. Gortex keeps its config and index under $HOME/.gortex
# (and $XDG_* under the home), which is backed by a named volume at runtime so the
# index survives container restarts. UID/GID default to 1000 to match the host
# user and the agent container, so the shared /workspace bind mount stays readable.
ARG GORTEX_UID=1000
ARG GORTEX_GID=1000
RUN groupadd --gid "${GORTEX_GID}" gortex \
    && useradd --uid "${GORTEX_UID}" --gid "${GORTEX_GID}" --create-home \
         --shell /bin/bash gortex \
    && mkdir -p /workspace /home/gortex/.gortex \
    && chown -R gortex:gortex /workspace /home/gortex/.gortex
# NOTE: /home/gortex/.gortex MUST exist here with gortex:gortex ownership. The
# compose service mounts the named volume `gortex-data` at this path. Docker
# initialises an empty named volume from the image's content+ownership at the
# mount point ONLY if that directory exists in the image; if it does not, the
# volume is created root:root 0755 and the unprivileged daemon gets
# "permission denied" writing its config/store and crash-loops.

COPY docker/gortex/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER gortex:gortex
ENV HOME=/home/gortex
WORKDIR /workspace

# Internal bridge only. Do NOT publish this to the host: the /mcp endpoint has no
# auth token (Claudio's MCPServerEntry carries no auth-header field), so it must
# stay reachable only from the claudio-net network.
EXPOSE 7411

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
