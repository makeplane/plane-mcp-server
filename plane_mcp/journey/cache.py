import os
import json
import time
import tempfile
import logging

def get_cached_workspace_context(cache_ttl_seconds: int = 300) -> dict:    
    cache_dir = os.path.join(tempfile.gettempdir(), "plane_mcp")
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, "workspace_context_cache.json")
    
    context = {}
    if os.path.exists(cache_file):
        if time.time() - os.path.getmtime(cache_file) < cache_ttl_seconds:
            try:
                with open(cache_file, "r") as f:
                    context = json.load(f)
            except Exception as e:
                logging.getLogger(__name__).debug(f"Cache read failed for {cache_file}, will refresh: {e}")
                
    if not context:
        try:
            from plane_mcp.client import get_plane_client_context
            ctx = get_plane_client_context()
            if ctx.client and ctx.workspace_slug:
                response = ctx.client.projects.list(workspace_slug=ctx.workspace_slug)
                
                projects = []
                states_by_project = {}
                labels_by_project = {}
                
                for p in response.results:
                    projects.append({
                        "identifier": p.identifier, 
                        "name": p.name, 
                        "description": getattr(p, "description", "")
                    })
                    try:
                        s_res = ctx.client.states.list(workspace_slug=ctx.workspace_slug, project_id=p.id)
                        states_by_project[p.identifier] = [s.name for s in s_res.results if s.name]
                    except Exception:
                        states_by_project[p.identifier] = []
                        
                    try:
                        label_res = ctx.client.labels.list(workspace_slug=ctx.workspace_slug, project_id=p.id)
                        labels_by_project[p.identifier] = [label.name for label in label_res.results if label.name]
                    except Exception:
                        labels_by_project[p.identifier] = []
                
                all_states = sorted({s for ss in states_by_project.values() for s in ss})
                all_labels = sorted({label for label_list in labels_by_project.values() for label in label_list})
                
                context = {
                    "projects": projects,
                    "states_by_project": states_by_project,
                    "labels_by_project": labels_by_project,
                    "all_states": all_states,
                    "all_labels": all_labels,
                    "priorities": ["urgent", "high", "medium", "low", "none"]
                }
                
                with open(cache_file, "w") as f:
                    json.dump(context, f)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to fetch workspace context: {e}")
            return {"error": f"Could not connect to Plane API: {e}. Check PLANE_API_KEY and PLANE_BASE_URL."}

    return context

def get_cached_project_slugs_docstring(cache_ttl_seconds: int = 300, full_descriptions: bool = False) -> str:
    context = get_cached_workspace_context(cache_ttl_seconds)
    projects = context.get("projects", [])
    
    if not projects:
        return "(e.g., 'PLANE' or 'TEST'). If you are unsure of a project_slug, make your best logical guess."
        
    if full_descriptions:
        lines = ["valid slugs:"]
        for p in projects:
            desc = f" - {p['description']}" if p.get("description") else f" - {p.get('name', '')}"
            lines.append(f"     * {p['identifier']}: {desc}")
        return "\n".join(lines)
    else:
        slugs = [p["identifier"] for p in projects]
        return f"valid slugs: {', '.join(slugs)}"

def get_cached_states_string() -> str:
    context = get_cached_workspace_context()
    states = context.get("all_states", context.get("states", []))
    if not states:
        return "'In Progress', 'Backlog', 'Done'"
    return ", ".join([f"'{s}'" for s in states])

def get_cached_labels_string() -> str:
    context = get_cached_workspace_context()
    labels = context.get("all_labels", context.get("labels", []))
    if not labels:
        return "'bug', 'feature'"
    return ", ".join([f"'{label}'" for label in labels])
    
def get_cached_priorities_string() -> str:
    context = get_cached_workspace_context()
    priorities = context.get("priorities", ["urgent", "high", "medium", "low", "none"])
    return ", ".join([f"'{p}'" for p in priorities])
