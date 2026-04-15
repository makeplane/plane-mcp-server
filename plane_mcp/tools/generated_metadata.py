"""Generated tools for Plane MCP Server."""

import uuid
from typing import Any

import requests
from fastmcp import FastMCP

from plane_mcp.client import get_plane_client_context


def register_metadata_generated_tools(mcp: FastMCP) -> None:
    """Register generated metadata tools."""

    @mcp.tool()
    def list_states(project_id: uuid.UUID) -> dict[str, Any]:
        """
        List all states in a project returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/states/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
    def create_state(project_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a state returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/states/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
    def update_state(project_id: uuid.UUID, state_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Update a state returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            state_id: UUID of the state
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/states/{state_id}/".format(workspace_slug=workspace_slug, project_id=str(project_id), state_id=str(state_id))
        
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
    def list_labels(project_id: uuid.UUID) -> dict[str, Any]:
        """
        List all labels in a project returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/labels/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
    def create_label(project_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a label returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/labels/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
    def delete_label(project_id: uuid.UUID, label_id: uuid.UUID) -> dict[str, Any]:
        """
        Delete a label returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            label_id: UUID of the label

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/labels/{label_id}/".format(workspace_slug=workspace_slug, project_id=str(project_id), label_id=str(label_id))
        
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

    @mcp.tool()
    def list_cycles(project_id: uuid.UUID) -> dict[str, Any]:
        """
        List all cycles in a project returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/cycles/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
    def create_cycle(project_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a cycle returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/cycles/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
    def update_cycle(project_id: uuid.UUID, cycle_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Update a cycle returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/cycles/{cycle_id}/".format(workspace_slug=workspace_slug, project_id=str(project_id), cycle_id=str(cycle_id))
        
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
    def list_modules(project_id: uuid.UUID) -> dict[str, Any]:
        """
        List all modules in a project returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/modules/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
    def create_module(project_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a module returning raw JSON.

        Args:
            workspace_slug: Workspace slug (injected by context)
            project_id: UUID of the project
            data: JSON payload

        Returns:
            Raw JSON response from Plane API
        """
        client, workspace_slug = get_plane_client_context()
        
        url = f"{client.config.base_path}{client.projects.base_path}".replace("/workspaces", "") + f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/modules/".format(workspace_slug=workspace_slug, project_id=str(project_id))
        
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
