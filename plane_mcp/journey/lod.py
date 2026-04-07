"""Level of Detail (LOD) filtering system to strip verbose REST metadata."""
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from plane_mcp.client import get_plane_client_context
from plane_mcp.resolver import EntityResolver
from markdownify import markdownify

class LODProfile(Enum):
    SUMMARY = "summary"
    STANDARD = "standard"
    FULL = "full"

# Summary: Minimum viable fields for AI context
SUMMARY_FIELDS = {
    "ticket_id", "name", "state", "priority", "assignees"
}

# Standard: Default ticket read fields (Key, Name, Details/description, priority, labels, state)
STANDARD_FIELDS = {
    "ticket_id", "name", "description_html", "priority", "labels", "state"
}

def inject_sequence_id(data: Dict[str, Any], project_identifier: Optional[str] = None) -> None:
    """
    Consistently inject sequence IDs (e.g., 'ENG-123') into the data dictionary
    to enable zero-lookup chaining for subsequent AI actions.
    """
    if "ticket_id" in data:
        return
        
    proj_id = project_identifier or data.get("project_identifier")
    if not proj_id and "project_detail" in data and isinstance(data["project_detail"], dict):
        proj_id = data["project_detail"].get("identifier")
        
    if proj_id and "sequence_id" in data:
        data["ticket_id"] = f"{proj_id}-{data['sequence_id']}"

def _hydrate_state(data: Dict[str, Any], project_identifier: Optional[str] = None) -> None:
    """If state is a raw UUID, attempt to hydrate its name."""
    state_val = data.get("state")
    if state_val:
        state_str = str(state_val)
        if len(state_str) == 36 and "-" in state_str:
            try:
                client, workspace_slug = get_plane_client_context()
                resolver = EntityResolver(client, workspace_slug)
                
                # Figure out project UUID
                proj_id = None
                if data.get("project"):
                    proj_id = str(data.get("project"))
                elif data.get("project_id"):
                    proj_id = str(data.get("project_id"))
                elif project_identifier:
                    proj_id = str(resolver.resolve_project(project_identifier))
                    
                if proj_id:
                    state_obj = client.states.retrieve(workspace_slug=workspace_slug, project_id=proj_id, state_id=state_str)
                    data["state"] = {"name": state_obj.name, "id": state_str}
            except Exception:
                pass

def _clean_html(html_str: str) -> str:
    if not html_str:
        return ""
    return markdownify(html_str, heading_style="ATX", bullet_list_marker="-").strip()

def _apply_lod_to_dict(data: Dict[str, Any], profile: LODProfile, project_identifier: Optional[str] = None) -> Dict[str, Any]:
    inject_sequence_id(data, project_identifier)
    _hydrate_state(data, project_identifier)
    
    result = {}
    if profile == LODProfile.SUMMARY:
        for key, value in data.items():
            if key in SUMMARY_FIELDS:
                out_key = "key" if key == "ticket_id" else key
                if key == "state" and isinstance(value, dict) and "name" in value:
                    result[out_key] = value["name"]
                else:
                    result[out_key] = value
                    
        # Always prefer human-readable state name from state_detail over raw UUID
        if "state_detail" in data and isinstance(data["state_detail"], dict) and "name" in data["state_detail"]:
            result["state"] = data["state_detail"]["name"]

        if "ticket_id" in data:
            result["key"] = data["ticket_id"]
            
    elif profile == LODProfile.STANDARD:
        # Standard: Default ticket read fields (Key, Name, Details, priority, labels, state)
        
        # Priority mapping
        if "ticket_id" in data:
            result["ticket_id"] = data["ticket_id"]
        if "name" in data:
            result["name"] = data["name"]
        
        # Description (convert HTML to markdown)
        if "description_html" in data and isinstance(data["description_html"], str):
            result["description"] = _clean_html(data["description_html"])
        elif "description" in data:
            result["description"] = data["description"]
            
        if "priority" in data:
            result["priority"] = data["priority"]
            
        if "labels" in data and isinstance(data["labels"], list):
            result["labels"] = [l.get("name") if isinstance(l, dict) and "name" in l else l for l in data["labels"]]
            
        # State mapping
        if "state" in data:
            if isinstance(data["state"], dict) and "name" in data["state"]:
                result["state"] = data["state"]["name"]
            else:
                result["state"] = data["state"]
        
        # Backup state check
        if "state" not in result and "state_detail" in data and isinstance(data["state_detail"], dict):
            result["state"] = data["state_detail"].get("name")
            
    elif profile == LODProfile.FULL:
        result = data.copy()
        if "description_html" in result and isinstance(result["description_html"], str):
            result["description"] = _clean_html(result["description_html"])
            del result["description_html"]
            
    return result

def apply_lod(
    data: Union[Dict, List, Any], 
    profile: LODProfile = LODProfile.SUMMARY,
    project_identifier: Optional[str] = None
) -> Union[Dict, List]:
    """
    Applies the LOD filter to a dictionary, list of dictionaries, or Pydantic model
    and returns a clean JSON-serializable structure.
    """
    # Convert Pydantic models or objects to dict
    if hasattr(data, "model_dump"):
        try:
            data = data.model_dump(mode='json')
        except TypeError:
            data = data.model_dump()
    elif hasattr(data, "dict"):
        data = data.dict()
    elif hasattr(data, "__dict__"):
        data = data.__dict__
        
    filtered_data = None
    if isinstance(data, list):
        filtered_data = [
            _apply_lod_to_dict(
                item.model_dump(mode='json') if hasattr(item, "model_dump") else (item.dict() if hasattr(item, "dict") else item),
                profile,
                project_identifier
            ) if hasattr(item, "model_dump") or hasattr(item, "dict") or isinstance(item, dict) else item 
            for item in data
        ]
    elif isinstance(data, dict):
        filtered_data = _apply_lod_to_dict(data, profile, project_identifier)
    else:
        filtered_data = data
        
    return filtered_data
