# Python 3.11 on Debian 13 (trixie). The suite is named so that the floating
# 3.11-slim tag moving to the next Debian release is a deliberate change here,
# not something a rebuild picks up on its own.
FROM python:3.11-slim-trixie

# Set working directory
WORKDIR /app

# System dependencies, plus every pending Debian security update for packages
# the base image already ships (zlib1g, apt, ...): the base tag lags the
# security archive, so without the upgrade a fresh build re-ships fixed CVEs.
# Bump APT_SECURITY_PATCH to bust buildx's cached layer (keyed on this command
# string); otherwise a rebuild silently re-ships the old packages.
ARG APT_SECURITY_PATCH=2026-11-16
RUN echo "apt-security-patch ${APT_SECURITY_PATCH}" \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files and application code
COPY pyproject.toml ./
COPY uv.lock* ./
COPY plane_mcp/ ./plane_mcp/

# Install the package and its dependencies with uv, then remove the packaging
# toolchain the base image ships. Nothing imports pip, setuptools or wheel at
# runtime, and each carries vendored copies of other packages (pip: urllib3,
# msgpack, certifi, setuptools' pkg_resources; setuptools: wheel,
# jaraco.context) that lag behind the real ones and that image scanners flag on
# their own, whatever version our dependencies resolve to. Removing pip also
# retires its vendored certifi, which still trusted the emSign ECC Root CA - G3.
# `uv pip check` fails the build if an installed package still needs them.
#
# uv is bind-mounted for this step only, so it is not shipped in the image.
# Keep its version in step with mise.toml.
RUN --mount=from=ghcr.io/astral-sh/uv:0.13.5,source=/uv,target=/usr/local/bin/uv \
    uv pip install --system --no-cache . \
    && uv pip uninstall --system pip setuptools wheel \
    && rm -f /usr/local/bin/pip \
    && uv pip check --system

# Expose port for HTTP transports (SSE, streamable-http, http)
EXPOSE 8211

# Set environment variables with defaults
ENV FASTMCP_PORT=8211

# Default to streamable-http transport, but allow override via command
# Users can override by passing different transport as CMD
ENTRYPOINT ["python", "-m", "plane_mcp"]
CMD ["http"]
