"""Generated tools for Plane MCP Server."""

import uuid
from typing import Any

import requests
from fastmcp import FastMCP

from plane_mcp.client import get_plane_client_context


def register_core_generated_tools(mcp: FastMCP) -> None:
    """Register generated core tools."""

    @mcp.tool()
    def get_workspace() -> dict[str, Any]:
        """
        Get workspace details returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/".format(workspace_slug=workspace_slug)
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("GET", url, headers=headers, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def update_workspace(data: dict[str, Any]) -> dict[str, Any]:
        """
        Update workspace details returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/".format(workspace_slug=workspace_slug)
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("PATCH", url, headers=headers, json=data, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def list_projects() -> dict[str, Any]:
        """
        List all projects in a workspace returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/".format(workspace_slug=workspace_slug)
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("GET", url, headers=headers, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def create_project(data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a project returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/".format(workspace_slug=workspace_slug)
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("POST", url, headers=headers, json=data, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def get_project(project_id: uuid.UUID) -> dict[str, Any]:
        """
        Get a specific project returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("GET", url, headers=headers, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def list_issues(project_id: uuid.UUID) -> dict[str, Any]:
        """
        List all issues in a project returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/issues/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("GET", url, headers=headers, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def create_issue_raw(project_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create an issue returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/issues/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("POST", url, headers=headers, json=data, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def update_issue_raw(project_id: uuid.UUID, issue_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Update an issue returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            issue_id: UUID of the issue
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/issues/{issue_id}/".format(workspace_slug=workspace_slug, project_id=str(project_id), issue_id=str(issue_id))
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("PATCH", url, headers=headers, json=data, timeout=client.config.timeout)
        response.raise_for_status()
        return response.json()

    @mcp.tool()
    def delete_issue(project_id: uuid.UUID, issue_id: uuid.UUID) -> dict[str, Any]:
        """
        Delete an issue returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            issue_id: UUID of the issue

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/issues/{issue_id}/".format(workspace_slug=workspace_slug, project_id=str(project_id), issue_id=str(issue_id))
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if client.config.access_token:
            headers["Authorization"] = f"Bearer {client.config.access_token}"
        elif client.config.api_key:
            headers["x-api-key"] = client.config.api_key
        
        response = requests.request("DELETE", url, headers=headers, timeout=client.config.timeout)
        response.raise_for_status()
        return {"status": "deleted"} if response.status_code == 204 else response.json()
