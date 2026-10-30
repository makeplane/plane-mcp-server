"""Declarative surface definitions and automated Starlette mount generation.

Provides a unified registry for FastMCP surfaces (Streamable HTTP, Header Auth,
and SSE fallback) with automated route ordering, lifespan aggregation, and
strict security boundaries.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import BaseRoute, Mount, Route

from plane_mcp.server import SurfaceType, get_header_mcp, get_oauth_mcp

logger = logging.getLogger(__name__)

PREFIX_REGEX = re.compile(r"^(/[-a-zA-Z0-9_]+)*$")


def sanitize_prefix(prefix: str | None) -> str:
    """Validate and normalize MCP_PATH_PREFIX against injection and traversal.

    Enforces:
    - Must start with '/' if non-empty
    - Alphanumeric, hyphen, and underscore characters only
    - No trailing slashes, double slashes, traversal (..), or route parameter injection ({})
    """
    if not prefix:
        return ""
    cleaned = prefix.strip().rstrip("/")
    if not cleaned:
        return ""
    if not PREFIX_REGEX.match(cleaned):
        raise ValueError(
            f"Security Violation: Invalid MCP_PATH_PREFIX '{prefix}'. "
            "Must start with '/' and contain only alphanumeric, hyphen, and underscore characters."
        )
    return cleaned


def normalize_route_path(*segments: str) -> str:
    """Join path segments into a normalized Starlette route path."""
    clean_parts = [p.strip("/ \t\r\n") for p in segments if p and p.strip("/ \t\r\n")]
    if not clean_parts:
        return "/"
    return "/" + "/".join(clean_parts)


@dataclass(frozen=True)
class SurfaceSpec:
    """Declarative specification for an MCP API surface.

    Attributes:
        name: Primary identifier and route segment (e.g. "admin", "agent", "docs").
        surface_type: Internal surface implementation identifier.
        aliases: Alternative route segments (e.g. ("http",) for backwards compatibility).
        description: Human-readable documentation for this surface.
        enable_oauth: Whether to generate OAuth endpoints (/{name}, /mcp/{name}).
        enable_header_auth: Whether to generate Header/API-key endpoints (/{name}/api-key, /mcp/{name}/api-key).
    """

    name: str
    surface_type: SurfaceType
    aliases: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    enable_oauth: bool = True
    enable_header_auth: bool = True


DEFAULT_SURFACES: tuple[SurfaceSpec, ...] = (
    SurfaceSpec(
        name="admin",
        surface_type="admin",
        aliases=("http",),
        description="Upstream Plane administration surface with full CRUD tools and legacy aliases",
    ),
    SurfaceSpec(
        name="agent",
        surface_type="agent",
        description="Intent-based ticket and workflow surface",
    ),
    SurfaceSpec(
        name="docs",
        surface_type="docs",
        description="Intent-based documentation surface for pages, collections, and templates",
    ),
)


class SurfaceRegistry:
    """Compiles declarative SurfaceSpecs into Starlette mounts and lifecycle hooks."""

    def __init__(
        self,
        surfaces: Sequence[SurfaceSpec] = DEFAULT_SURFACES,
        prefix: str | None = None,
    ):
        self.surfaces = list(surfaces)
        self.prefix = sanitize_prefix(prefix)
        self._compiled_routes: list[BaseRoute] | None = None
        self._lifecycle_apps: list[Starlette] | None = None

    def compile(self) -> tuple[list[BaseRoute], list[Starlette]]:
        """Compile surfaces into an ordered, conflict-free Starlette route list.

        Enforces strict precedence:
        1. Exact RFC 8414/9728 .well-known routes (highest precedence)
        2. Specific /api-key mounts (prevents greedy prefix matching by base mounts)
        3. Base surface mounts (OAuth Streamable HTTP)
        4. Fallback SSE mount on root (lowest precedence)
        """
        if self._compiled_routes is not None and self._lifecycle_apps is not None:
            return self._compiled_routes, self._lifecycle_apps

        well_known_routes: list[BaseRoute] = []
        specific_mounts: list[Mount] = []
        base_mounts: list[Mount] = []
        lifecycle_apps: list[Starlette] = []

        seen_mount_paths: set[str] = set()
        seen_route_signatures: set[tuple[str, tuple[str, ...]]] = set()

        def _track_app(app: Starlette) -> None:
            if app not in lifecycle_apps:
                lifecycle_apps.append(app)

        for surface in self.surfaces:
            all_names = [surface.name, *surface.aliases]
            oauth_app: Starlette | None = None
            header_app: Starlette | None = None

            # 1. Instantiate OAuth MCP & App
            if surface.enable_oauth:
                base_path = normalize_route_path(self.prefix, surface.name)
                oauth_mcp = get_oauth_mcp(base_path=base_path, surface=surface.surface_type)
                oauth_app = oauth_mcp.http_app(stateless_http=True)
                _track_app(oauth_app)

                # Collect well-known routes (mcp_path="/mcp" relative to base_path)
                if hasattr(oauth_mcp, "auth") and oauth_mcp.auth:
                    try:
                        routes = oauth_mcp.auth.get_well_known_routes(mcp_path="/mcp")
                        for r in routes:
                            if isinstance(r, Route):
                                sig = (r.path, tuple(sorted(r.methods or ())))
                                if sig in seen_route_signatures:
                                    continue
                                seen_route_signatures.add(sig)
                            well_known_routes.append(r)
                    except Exception as exc:
                        logger.warning("Failed to get well-known routes for %s: %s", surface.name, exc)

            # 2. Instantiate Header/API-Key MCP & App
            if surface.enable_header_auth:
                header_mcp = get_header_mcp(surface=surface.surface_type)
                header_app = header_mcp.http_app(stateless_http=True)
                _track_app(header_app)

            # 3. Mounts generation for primary name and all aliases
            for name_segment in all_names:
                # Specific /api-key mounts (highest mount precedence)
                if header_app:
                    api_key_paths = [
                        normalize_route_path(self.prefix, name_segment, "api-key"),
                        normalize_route_path(self.prefix, "mcp", name_segment, "api-key"),
                    ]
                    for p in api_key_paths:
                        if p not in seen_mount_paths:
                            seen_mount_paths.add(p)
                            specific_mounts.append(Mount(p, app=header_app))

                # Base OAuth mounts
                if oauth_app:
                    base_paths = [
                        normalize_route_path(self.prefix, name_segment),
                        normalize_route_path(self.prefix, "mcp", name_segment),
                    ]
                    for p in base_paths:
                        if p not in seen_mount_paths:
                            seen_mount_paths.add(p)
                            base_mounts.append(Mount(p, app=oauth_app))

        # 4. SSE Fallback (Mounted at root "/" or prefix)
        sse_base_path = self.prefix or "/"
        sse_mcp = get_oauth_mcp(base_path=sse_base_path, surface="admin")
        sse_app = sse_mcp.http_app(transport="sse")
        _track_app(sse_app)

        if hasattr(sse_mcp, "auth") and sse_mcp.auth:
            try:
                sse_routes = sse_mcp.auth.get_well_known_routes(mcp_path="/sse")
                for r in sse_routes:
                    if isinstance(r, Route):
                        sig = (r.path, tuple(sorted(r.methods or ())))
                        if sig in seen_route_signatures:
                            continue
                        seen_route_signatures.add(sig)
                    well_known_routes.append(r)
            except Exception as exc:
                logger.warning("Failed to get well-known routes for SSE: %s", exc)

        root_mount_path = self.prefix or "/"
        terminal_mounts = [Mount(root_mount_path, app=sse_app)]

        # Ordered route list guaranteeing precedence
        compiled = [*well_known_routes, *specific_mounts, *base_mounts, *terminal_mounts]
        self._compiled_routes = compiled
        self._lifecycle_apps = lifecycle_apps
        return compiled, lifecycle_apps

    def build_app(self) -> Starlette:
        """Construct the configured Starlette application with CORS and lifespan management."""
        routes, lifecycle_apps = self.compile()

        @asynccontextmanager
        async def combined_lifespan(app: Starlette) -> AsyncIterator[None]:
            async with AsyncExitStack() as stack:
                for sub_app in lifecycle_apps:
                    await stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
                yield

        application = Starlette(
            routes=routes,
            lifespan=combined_lifespan,
        )

        application.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id", "Mcp-Protocol-Version", "WWW-Authenticate"],
        )
        return application


def build_mcp_app(
    prefix: str | None = None,
    surfaces: Sequence[SurfaceSpec] = DEFAULT_SURFACES,
) -> Starlette:
    """Convenience builder returning a configured Starlette application."""
    registry = SurfaceRegistry(surfaces=surfaces, prefix=prefix)
    return registry.build_app()
