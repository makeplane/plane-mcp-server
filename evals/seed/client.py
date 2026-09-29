"""Plane client construction for evaluation fixture runs."""

from __future__ import annotations

import os

from plane import PlaneClient


def make_plane_client() -> tuple[PlaneClient, str]:
    """Build a PlaneClient from EVAL_* env vars (mirrors stdio client construction)."""
    api_key = os.environ.get("EVAL_PLANE_API_KEY", "")
    workspace_slug = os.environ.get("EVAL_PLANE_WORKSPACE_SLUG", "")
    base_url = os.environ.get("EVAL_PLANE_BASE_URL", "https://api.plane.so")
    if not api_key or not workspace_slug:
        raise RuntimeError("EVAL_PLANE_API_KEY and EVAL_PLANE_WORKSPACE_SLUG are required for live runs")
    client = PlaneClient(base_url=base_url, api_key=api_key)
    return client, workspace_slug
