# Use Python 3.11 as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files and application code
COPY pyproject.toml ./
COPY uv.lock* ./
COPY plane_mcp/ ./plane_mcp/

# Install the package and dependencies using uv. pip>=26.2.2 replaces the base
# image's pip, whose vendored certifi still trusts the emSign ECC Root CA - G3.
RUN uv pip install --system --no-cache . "pip>=26.2.2"

# Expose port for HTTP transports (SSE, streamable-http, http)
EXPOSE 8211

# Set environment variables with defaults
ENV FASTMCP_PORT=8211

# Default to streamable-http transport, but allow override via command
# Users can override by passing different transport as CMD
ENTRYPOINT ["python", "-m", "plane_mcp"]
CMD ["http"]

