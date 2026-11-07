"""Intent-based tool surface for documentation, pages, collections, and templates."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP

from plane_mcp.client import get_plane_client_context
from plane_mcp.docs.lod import DocsLOD, apply_doc_lod
from plane_mcp.journey.base import mcp_error_boundary
from plane_mcp.resolver import EntityResolver, is_valid_uuid
from plane_mcp.sanitize import apply_snippet_edits, markdown_or_html_to_safe_html
from plane_mcp.yaml_formatter import with_yaml


def _get_str_attr(obj: Any, key: str) -> str | None:
    """Safely extract a non-empty string attribute or dict key, ignoring MagicMock objects."""
    val = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _extract_page_name(page_or_item: Any) -> str:
    """Extract the title/name string from a page model, dict, or collection wrapper."""
    name = _get_str_attr(page_or_item, "name")
    if name is not None:
        return name
    inner = page_or_item.get("page") if isinstance(page_or_item, dict) else getattr(page_or_item, "page", None)
    if inner is not None:
        inner_name = _get_str_attr(inner, "name")
        if inner_name is not None:
            return inner_name
    return ""


def _resolve_ticket_and_project(
    resolver: EntityResolver,
    client: Any,
    workspace_slug: str,
    ticket_id: str,
    project_slug: str | None = None,
) -> tuple[str, str, str | None]:
    """Resolve a ticket identifier or UUID into (work_item_uuid, project_uuid, project_slug_or_id)."""
    stripped = ticket_id.strip()
    effective_proj_slug = project_slug if is_valid_uuid(stripped) else None

    if hasattr(resolver, "resolve_ticket_context") and callable(resolver.resolve_ticket_context):
        ctx = resolver.resolve_ticket_context(stripped, project_slug=effective_proj_slug)
        if isinstance(ctx, tuple):
            if len(ctx) >= 3:
                return str(ctx[0]), str(ctx[1]), ctx[2] or effective_proj_slug or str(ctx[1])
            if len(ctx) == 2:
                return str(ctx[0]), str(ctx[1]), effective_proj_slug or str(ctx[1])

    if is_valid_uuid(stripped):
        wid = resolver.resolve_ticket(stripped)
        if project_slug:
            proj_id = resolver.resolve_project(project_slug)
            return wid, proj_id, project_slug

        try:
            resp = client.work_items._get(f"{workspace_slug}/work-items/{wid}")
            p_detail = resp.get("project_detail") if isinstance(resp, dict) else getattr(resp, "project_detail", None)
            if p_detail is not None:
                pd_id = _get_str_attr(p_detail, "id")
                pd_ident = _get_str_attr(p_detail, "identifier")
                if pd_id:
                    return wid, pd_id, pd_ident or pd_id
            for key in ("project_id", "project"):
                val = _get_str_attr(resp, key)
                if val:
                    return wid, val, val
        except Exception:
            pass

        projects_resp = client.projects.list(workspace_slug=workspace_slug)
        projects = getattr(projects_resp, "results", projects_resp if isinstance(projects_resp, list) else [])
        for p in projects:
            p_id = _get_str_attr(p, "id") or (str(p.id) if getattr(p, "id", None) is not None else None)
            p_ident = _get_str_attr(p, "identifier")
            if not p_id:
                continue
            try:
                wi = client.work_items.retrieve(workspace_slug=workspace_slug, project_id=p_id, work_item_id=wid)
                wi_id = _get_str_attr(wi, "id")
                if wi is not None and wi_id is not None:
                    return wid, p_id, p_ident or p_id
            except Exception:
                continue

        raise ValueError(f"Could not determine project for ticket UUID '{ticket_id}'. Pass project_slug explicitly.")

    wid = resolver.resolve_ticket(stripped)
    parts = stripped.upper().split("-")
    slug_to_use = parts[0] if len(parts) >= 2 else (project_slug or parts[0])
    proj_id = resolver.resolve_project(slug_to_use)
    return wid, proj_id, slug_to_use


def _resolve_template_body(
    resolver: EntityResolver,
    client: Any,
    workspace_slug: str,
    template: str,
    project_slug: str | None = None,
) -> tuple[str, str | None]:
    """Resolve a page template by name or UUID and extract its HTML/Markdown body if available."""
    if hasattr(resolver, "resolve_template_with_body") and callable(resolver.resolve_template_with_body):
        res = resolver.resolve_template_with_body(template, kind="page", project_slug=project_slug)
        if isinstance(res, tuple) and len(res) >= 2:
            t_id, t_body = str(res[0]), res[1]
            if isinstance(t_body, str) and t_body.strip():
                return t_id, t_body.strip()
            return t_id, None

    t_id = resolver.resolve_template(template, kind="page", project_slug=project_slug)
    proj_id = resolver.resolve_project(project_slug) if project_slug else None
    templates = (
        client.project_templates.page_templates.list(workspace_slug=workspace_slug, project_id=proj_id)
        if proj_id
        else client.workspace_templates.pages.list(workspace_slug=workspace_slug)
    )
    items = getattr(templates, "results", templates if isinstance(templates, list) else [])
    for t in items:
        item_id = _get_str_attr(t, "id") or str(getattr(t, "id", ""))
        item_name = _get_str_attr(t, "name") or ""
        if item_id.lower() == t_id.lower() or item_name.lower() == template.strip().lower():
            t_data = t.get("template_data") if isinstance(t, dict) else getattr(t, "template_data", None)
            if isinstance(t_data, dict):
                for k in ("description_html", "body", "content", "description"):
                    val = _get_str_attr(t_data, k)
                    if val:
                        return t_id, val
            for k in ("description_html", "body", "content", "description"):
                val = _get_str_attr(t, k)
                if val:
                    return t_id, val
    return t_id, None


def _resolve_collection_membership_id(
    client: Any,
    workspace_slug: str,
    coll_id: str,
    page_id: str,
) -> str | None:
    """Resolve the page_collection_id membership row UUID for a page inside a collection."""
    from plane.models.query_params import CollectionPageQueryParams

    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        coll_pages = (
            client.collections.pages.list(workspace_slug=workspace_slug, collection_id=coll_id)
            if cursor is None
            else client.collections.pages.list(
                workspace_slug=workspace_slug,
                collection_id=coll_id,
                params=CollectionPageQueryParams(cursor=cursor),
            )
        )
        items = getattr(coll_pages, "results", coll_pages if isinstance(coll_pages, list) else [])
        for item in items:
            item_page = item.get("page") if isinstance(item, dict) else getattr(item, "page", None)
            item_page_id: str | None = None
            if isinstance(item_page, str) and item_page.strip():
                item_page_id = item_page.strip()
            elif item_page is not None:
                item_page_id = _get_str_attr(item_page, "id")
            if not item_page_id:
                item_page_id = _get_str_attr(item, "page_id")
            item_id = _get_str_attr(item, "id")
            pc_id = _get_str_attr(item, "page_collection_id")

            if item_page_id == str(page_id) or item_id == str(page_id):
                membership = pc_id or item_id
                if membership:
                    return membership

        next_cursor = _get_str_attr(coll_pages, "next_cursor")
        next_page_results = getattr(
            coll_pages,
            "next_page_results",
            coll_pages.get("next_page_results", True) if isinstance(coll_pages, dict) else True,
        )
        if next_page_results is not False and next_cursor and next_cursor not in seen_cursors:
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            break

    try:
        filed = client.pages.retrieve_workspace_page(workspace_slug=workspace_slug, page_id=page_id)
        pc_id = _get_str_attr(filed, "page_collection_id")
        if pc_id:
            return pc_id
    except Exception:
        pass

    return None


def register_docs_tools(mcp: FastMCP) -> None:
    """Register the 6 intent-based documentation tools on the FastMCP instance."""

    @mcp.tool(
        name="search_docs",
        description=(
            "Discover and search documentation pages, collections, or page templates across workspace, "
            "project, collection, or ticket scope. Pass project_slug='help' for workspace discovery."
        ),
    )
    @with_yaml
    @mcp_error_boundary
    def search_docs(
        query: str | None = None,
        project_slug: str | None = None,
        collection: str | None = None,
        ticket_id: str | None = None,
        include_templates: bool = False,
        limit: int = 50,
        lod: Literal["summary", "standard", "full"] = "summary",
    ) -> dict[str, Any]:
        """Discover and search documentation pages across workspace, project, collection, or ticket scope."""
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)

        if project_slug and project_slug.lower() == "help":
            projects_resp = client.projects.list(workspace_slug=workspace_slug)
            collections_resp = client.collections.list(workspace_slug=workspace_slug)
            p_list = [p.identifier for p in getattr(projects_resp, "results", [])]
            c_list = [c.name for c in getattr(collections_resp, "results", [])]
            return {
                "workspace": workspace_slug,
                "available_projects": p_list,
                "available_collections": c_list,
                "message": "Use project_slug to target project pages, or omit for workspace pages.",
            }

        matched_pages: list[dict[str, Any]] = []

        # 1. Ticket linked pages
        if ticket_id:
            resolved_ticket_id, proj_id, resolved_proj_slug = _resolve_ticket_and_project(
                resolver, client, workspace_slug, ticket_id, project_slug=project_slug
            )
            links = client.work_items.pages.list(
                workspace_slug=workspace_slug, project_id=proj_id, work_item_id=resolved_ticket_id
            )
            items = getattr(links, "results", links if isinstance(links, list) else [])
            for item in items:
                p_id = (
                    item.get("page_id") or item.get("page")
                    if isinstance(item, dict)
                    else getattr(item, "page_id", getattr(item, "page", None))
                )
                if p_id:
                    page_obj = client.pages.retrieve_project_page(
                        workspace_slug=workspace_slug, project_id=proj_id, page_id=str(p_id)
                    )
                    name = _extract_page_name(page_obj)
                    if query and query.lower() not in name.lower():
                        continue
                    matched_pages.append(
                        apply_doc_lod(page_obj, lod=lod, project_slug=resolved_proj_slug or project_slug or proj_id)
                    )
                    if len(matched_pages) >= limit:
                        break
            return {"results": matched_pages[:limit], "count": len(matched_pages[:limit])}

        # 2. Collection pages
        if collection:
            coll_id = resolver.resolve_collection(collection)
            from plane.models.query_params import CollectionPageQueryParams

            cursor: str | None = None
            seen_cursors: set[str] = set()
            while len(matched_pages) < limit:
                params = (
                    CollectionPageQueryParams(per_page=limit)
                    if cursor is None
                    else CollectionPageQueryParams(per_page=limit, cursor=cursor)
                )
                coll_pages = client.collections.pages.list(
                    workspace_slug=workspace_slug, collection_id=coll_id, params=params
                )
                items = getattr(coll_pages, "results", coll_pages if isinstance(coll_pages, list) else [])
                if not items:
                    break
                for item in items:
                    name = _extract_page_name(item)
                    if query and query.lower() not in name.lower():
                        continue
                    matched_pages.append(apply_doc_lod(item, lod=lod))
                    if len(matched_pages) >= limit:
                        break
                next_cursor = getattr(
                    coll_pages, "next_cursor", coll_pages.get("next_cursor") if isinstance(coll_pages, dict) else None
                )
                next_page_results = getattr(
                    coll_pages,
                    "next_page_results",
                    coll_pages.get("next_page_results", True) if isinstance(coll_pages, dict) else True,
                )
                if (
                    len(matched_pages) < limit
                    and next_page_results is not False
                    and isinstance(next_cursor, str)
                    and bool(next_cursor.strip())
                    and next_cursor not in seen_cursors
                ):
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
                else:
                    break
            return {"results": matched_pages[:limit], "count": len(matched_pages[:limit]), "collection": collection}

        # 3. Project or Workspace pages
        from plane.models.query_params import PaginatedQueryParams

        proj_id = resolver.resolve_project(project_slug) if project_slug else None
        cursor = None
        seen_cursors = set()
        while len(matched_pages) < limit:
            params = (
                PaginatedQueryParams(per_page=limit)
                if cursor is None
                else PaginatedQueryParams(per_page=limit, cursor=cursor)
            )
            if proj_id:
                pages_resp = client.pages.list_project_pages(
                    workspace_slug=workspace_slug, project_id=proj_id, params=params
                )
            else:
                pages_resp = client.pages.list_workspace_pages(workspace_slug=workspace_slug, params=params)
            items = getattr(pages_resp, "results", pages_resp if isinstance(pages_resp, list) else [])
            if not items:
                break
            for item in items:
                name = _extract_page_name(item)
                if query and query.lower() not in name.lower():
                    continue
                matched_pages.append(
                    apply_doc_lod(item, lod=lod, project_slug=project_slug)
                    if project_slug
                    else apply_doc_lod(item, lod=lod)
                )
                if len(matched_pages) >= limit:
                    break
            next_cursor = getattr(
                pages_resp, "next_cursor", pages_resp.get("next_cursor") if isinstance(pages_resp, dict) else None
            )
            next_page_results = getattr(
                pages_resp,
                "next_page_results",
                pages_resp.get("next_page_results", True) if isinstance(pages_resp, dict) else True,
            )
            if (
                len(matched_pages) < limit
                and next_page_results is not False
                and isinstance(next_cursor, str)
                and bool(next_cursor.strip())
                and next_cursor not in seen_cursors
            ):
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                break

        result = {"results": matched_pages[:limit], "count": len(matched_pages[:limit])}

        if include_templates:
            templates = client.workspace_templates.pages.list(workspace_slug=workspace_slug)
            t_items = getattr(templates, "results", templates if isinstance(templates, list) else [])
            t_matched = []
            for t in t_items:
                t_name = str(getattr(t, "name", ""))
                if not query or query.lower() in t_name.lower():
                    t_matched.append({"id": str(t.id), "name": t_name, "kind": "page_template"})
            result["templates"] = t_matched

        return result

    @mcp.tool(
        name="read_doc",
        description="Read a documentation page by title or UUID with clean Markdown body and hierarchy context.",
    )
    @with_yaml
    @mcp_error_boundary
    def read_doc(
        page: str,
        project_slug: str | None = None,
        lod: Literal["summary", "standard", "full"] = "standard",
    ) -> dict[str, Any]:
        """Read a documentation page by title or UUID at the requested Level of Detail."""
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)

        page_id, resolved_proj_id = resolver.resolve_page(page, project_slug=project_slug)
        if resolved_proj_id:
            page_obj = client.pages.retrieve_project_page(
                workspace_slug=workspace_slug, project_id=resolved_proj_id, page_id=page_id
            )
        else:
            page_obj = client.pages.retrieve_workspace_page(workspace_slug=workspace_slug, page_id=page_id)

        return apply_doc_lod(page_obj, lod=lod, project_slug=project_slug)

    @mcp.tool(
        name="create_doc",
        description=(
            "Create a workspace or project documentation page with optional template, collection, or ticket attachment."
        ),
    )
    @with_yaml
    @mcp_error_boundary
    def create_doc(
        title: str,
        content: str | None = None,
        project_slug: str | None = None,
        parent_page: str | None = None,
        collection: str | None = None,
        template: str | None = None,
        ticket_ids: list[str] | None = None,
        access: Literal["public", "private"] = "public",
    ) -> dict[str, Any]:
        """Create a workspace or project documentation page with optional template seeding and ticket links."""
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)

        from plane.models.pages import CreatePage

        from plane_mcp.toolkit import opt

        # Resolve parent or collection
        parent_id = None
        if parent_page:
            parent_id, _ = resolver.resolve_page(parent_page, project_slug=project_slug)

        collection_id = None
        if collection:
            collection_id = resolver.resolve_collection(collection)

        safe_html = markdown_or_html_to_safe_html(content) or ""

        # Template seeding if specified
        if template:
            _, template_body = _resolve_template_body(
                resolver, client, workspace_slug, template, project_slug=project_slug
            )
            if not safe_html:
                if template_body:
                    safe_html = (
                        markdown_or_html_to_safe_html(template_body) or f"<p>Created from template {template}</p>"
                    )
                else:
                    safe_html = f"<p>Created from template {template}</p>"

        data = CreatePage(
            name=title,
            description_html=safe_html,
            access=0 if access == "public" else 1,
            parent_id=opt(parent_id),
            collection_id=opt(collection_id),
        )

        project_id = resolver.resolve_project(project_slug) if project_slug else None
        if project_id:
            created = client.pages.create_project_page(workspace_slug=workspace_slug, project_id=project_id, data=data)
        else:
            created = client.pages.create_workspace_page(workspace_slug=workspace_slug, data=data)

        created_id = str(created.get("id") if isinstance(created, dict) else getattr(created, "id", ""))

        # Optional ticket attachments across project or workspace pages
        attached_tickets: list[str] = []
        failed_tickets: list[str] = []
        if ticket_ids:
            from plane.models.work_item_pages import CreateWorkItemPage

            for tid in ticket_ids:
                try:
                    wid, target_proj_id, _ = _resolve_ticket_and_project(
                        resolver, client, workspace_slug, tid, project_slug=project_slug
                    )
                    client.work_items.pages.create(
                        workspace_slug=workspace_slug,
                        project_id=target_proj_id,
                        work_item_id=wid,
                        data=CreateWorkItemPage(page_id=created_id),
                    )
                    attached_tickets.append(tid)
                except Exception:
                    failed_tickets.append(tid)

        res = apply_doc_lod(created, lod=DocsLOD.STANDARD, project_slug=project_slug)
        if attached_tickets:
            res["attached_tickets"] = attached_tickets
        if failed_tickets:
            res["status"] = "partial"
            res["failed_tickets"] = failed_tickets
        return res

    @mcp.tool(
        name="update_doc",
        description=(
            "Update a page's title, body (via snippet editing or overwrite), collection, or archive/delete state."
        ),
    )
    @with_yaml
    @mcp_error_boundary
    def update_doc(
        page: str,
        project_slug: str | None = None,
        new_title: str | None = None,
        append_text: str | None = None,
        append_after_snippet: str | None = None,
        replace_text: str | None = None,
        replace_target_snippet: str | None = None,
        overwrite_content: str | None = None,
        collection: str | None = None,
        archive: bool | None = None,
        delete_permanently: bool = False,
    ) -> dict[str, Any]:
        """Update a documentation page's title, body content, collection membership, or lifecycle state."""
        if archive is not None and any(
            x is not None
            for x in (
                new_title,
                append_text,
                append_after_snippet,
                replace_text,
                replace_target_snippet,
                overwrite_content,
                collection,
            )
        ):
            return {
                "status": "error",
                "message": (
                    "Cannot combine archive/unarchive with page edits "
                    "(new_title, body, or collection) in a single request."
                ),
            }

        if replace_text is not None and replace_target_snippet is None:
            return {"status": "error", "message": "replace_target_snippet is required when replace_text is provided"}

        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        from plane.models.pages import UpdatePage

        from plane_mcp.toolkit import opt

        page_id, project_id = resolver.resolve_page(page, project_slug=project_slug)

        if delete_permanently:
            if project_id:
                client.pages.archive_project_page(workspace_slug=workspace_slug, project_id=project_id, page_id=page_id)
                client.pages.delete_project_page(workspace_slug=workspace_slug, project_id=project_id, page_id=page_id)
            else:
                client.pages.archive_workspace_page(workspace_slug=workspace_slug, page_id=page_id)
                client.pages.delete_workspace_page(workspace_slug=workspace_slug, page_id=page_id)
            return {"page_id": page_id, "status": "deleted"}

        if archive is not None:
            if project_id:
                mover = client.pages.archive_project_page if archive else client.pages.unarchive_project_page
                mover(workspace_slug=workspace_slug, project_id=project_id, page_id=page_id)
            else:
                mover = client.pages.archive_workspace_page if archive else client.pages.unarchive_workspace_page
                mover(workspace_slug=workspace_slug, page_id=page_id)
            return {"page_id": page_id, "status": "archived" if archive else "unarchived"}

        # Fetch current body if doing snippet edits
        current_obj = (
            client.pages.retrieve_project_page(workspace_slug=workspace_slug, project_id=project_id, page_id=page_id)
            if project_id
            else client.pages.retrieve_workspace_page(workspace_slug=workspace_slug, page_id=page_id)
        )
        if isinstance(current_obj, dict):
            raw_desc = current_obj.get("description_html")
        else:
            raw_desc = getattr(current_obj, "description_html", "")
        current_html = str(raw_desc or "")

        new_html = None
        if overwrite_content is not None:
            new_html = markdown_or_html_to_safe_html(overwrite_content)
        elif any(x is not None for x in (append_text, replace_text, replace_target_snippet, append_after_snippet)):
            updated_html, err = apply_snippet_edits(
                current_html,
                replace_target_snippet=replace_target_snippet,
                replace_text=replace_text,
                append_after_snippet=append_after_snippet,
                append_text=append_text,
            )
            if err:
                return {"status": "error", "message": err}
            new_html = updated_html

        update_payload = UpdatePage(name=opt(new_title), description_html=opt(new_html))
        if project_id:
            updated = client.pages.update_project_page(
                workspace_slug=workspace_slug, project_id=project_id, page_id=page_id, data=update_payload
            )
        else:
            updated = client.pages.update_workspace_page(
                workspace_slug=workspace_slug, page_id=page_id, data=update_payload
            )

        if collection:
            coll_id = resolver.resolve_collection(collection)
            from plane.models.collections import AddCollectionPages

            client.collections.pages.add(
                workspace_slug=workspace_slug, collection_id=coll_id, data=AddCollectionPages(page_ids=[page_id])
            )

        return apply_doc_lod(updated, lod=DocsLOD.STANDARD, project_slug=project_slug)

    @mcp.tool(
        name="link_doc",
        description="Attach, detach, or list documentation pages linked to a ticket (e.g. 'ENG-123').",
    )
    @with_yaml
    @mcp_error_boundary
    def link_doc(
        ticket_id: str,
        page: str | None = None,
        action: Literal["attach", "detach", "list"] = "attach",
    ) -> dict[str, Any]:
        """Attach, detach, or list documentation pages linked to a work item."""
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)

        wid, proj_id, _ = _resolve_ticket_and_project(resolver, client, workspace_slug, ticket_id)

        if action == "list":
            links = client.work_items.pages.list(workspace_slug=workspace_slug, project_id=proj_id, work_item_id=wid)
            items = getattr(links, "results", links if isinstance(links, list) else [])
            return {"ticket_id": ticket_id, "linked_pages": [str(getattr(x, "page_id", x)) for x in items]}

        if not page:
            return {"status": "error", "message": f"Action '{action}' requires page parameter"}

        page_id, _ = resolver.resolve_page(page, project_slug=proj_id)

        if action == "attach":
            from plane.models.work_item_pages import CreateWorkItemPage

            client.work_items.pages.create(
                workspace_slug=workspace_slug,
                project_id=proj_id,
                work_item_id=wid,
                data=CreateWorkItemPage(page_id=page_id),
            )
            return {"ticket_id": ticket_id, "page_id": page_id, "status": "attached"}

        if action == "detach":
            # Find work_item_page_id link
            links = client.work_items.pages.list(workspace_slug=workspace_slug, project_id=proj_id, work_item_id=wid)
            items = getattr(links, "results", links if isinstance(links, list) else [])
            link_id = None
            for item in items:
                if str(getattr(item, "page_id", getattr(item, "page", None))) == page_id:
                    link_id = str(item.id)
                    break
            if not link_id:
                return {"status": "error", "message": f"Page '{page}' is not linked to ticket '{ticket_id}'"}

            client.work_items.pages.delete(
                workspace_slug=workspace_slug,
                project_id=proj_id,
                work_item_id=wid,
                work_item_page_id=link_id,
            )
            return {"ticket_id": ticket_id, "page_id": page_id, "status": "detached"}

        return {"status": "error", "message": f"Unknown action: {action}"}

    @mcp.tool(
        name="organize_docs",
        description="Create, update, or organize page collections and reusable templates by name.",
    )
    @with_yaml
    @mcp_error_boundary
    def organize_docs(
        target: Literal["collection", "template"],
        action: Literal["list", "create", "update", "delete", "add_pages", "remove_page"],
        name_or_id: str | None = None,
        new_name: str | None = None,
        description: str | None = None,
        pages: list[str] | None = None,
        access: Literal["public", "private"] = "public",
    ) -> dict[str, Any]:
        """Create, update, delete, or organize page collections and reusable page templates."""
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)

        if target == "collection":
            if action == "list":
                coll_resp = client.collections.list(workspace_slug=workspace_slug)
                items = getattr(coll_resp, "results", coll_resp if isinstance(coll_resp, list) else [])
                return {"collections": [{"id": str(c.id), "name": getattr(c, "name", "")} for c in items]}

            if action == "create":
                from plane.models.collections import CreateCollection

                created = client.collections.create(
                    workspace_slug=workspace_slug,
                    data=CreateCollection(
                        name=name_or_id or new_name or "New Collection", access=0 if access == "public" else 1
                    ),
                )
                return {"id": str(created.id), "name": getattr(created, "name", ""), "status": "created"}

            if not name_or_id:
                return {"status": "error", "message": f"Action '{action}' requires name_or_id"}

            coll_id = resolver.resolve_collection(name_or_id)

            if action == "add_pages":
                if not pages:
                    return {"status": "error", "message": "add_pages requires pages list"}
                page_uuids = [resolver.resolve_page(p)[0] for p in pages]
                from plane.models.collections import AddCollectionPages

                client.collections.pages.add(
                    workspace_slug=workspace_slug,
                    collection_id=coll_id,
                    data=AddCollectionPages(page_ids=page_uuids),
                )
                return {"collection_id": coll_id, "pages_added": pages, "status": "pages_added"}

            if action == "remove_page":
                if not pages:
                    return {"status": "error", "message": "remove_page requires pages list"}
                for p in pages:
                    page_id, _ = resolver.resolve_page(p)
                    membership_id = _resolve_collection_membership_id(client, workspace_slug, coll_id, page_id)
                    if not membership_id:
                        return {
                            "status": "error",
                            "message": f"Page '{p}' is not a member of collection '{name_or_id}'",
                        }
                    client.collections.pages.remove(
                        workspace_slug=workspace_slug,
                        collection_id=coll_id,
                        page_collection_id=str(membership_id),
                    )
                return {"collection_id": coll_id, "pages_removed": pages, "status": "pages_removed"}

            if action == "delete":
                client.collections.delete(workspace_slug=workspace_slug, collection_id=coll_id)
                return {"collection_id": coll_id, "status": "deleted"}

            if action == "update":
                from plane.models.collections import UpdateCollection

                client.collections.update(
                    workspace_slug=workspace_slug,
                    collection_id=coll_id,
                    data=UpdateCollection(name=new_name),
                )
                return {"collection_id": coll_id, "status": "updated"}

        if target == "template":
            if action == "list":
                templates = client.workspace_templates.pages.list(workspace_slug=workspace_slug)
                items = getattr(templates, "results", templates if isinstance(templates, list) else [])
                return {"templates": [{"id": str(t.id), "name": getattr(t, "name", "")} for t in items]}

            if action == "create":
                t_name = name_or_id or new_name
                if not t_name:
                    return {"status": "error", "message": "create requires name_or_id or new_name"}
                safe_desc = (markdown_or_html_to_safe_html(description) or "") if description else ""
                from plane.models.workspace_templates import CreatePageTemplate

                created = client.workspace_templates.pages.create(
                    workspace_slug=workspace_slug,
                    data=CreatePageTemplate(
                        name=t_name,
                        description_html=safe_desc or None,
                        template_data={"name": t_name, "description_html": safe_desc},
                    ),
                )
                created_id = str(created.get("id") if isinstance(created, dict) else getattr(created, "id", ""))
                created_name = _get_str_attr(created, "name") or t_name
                return {"id": created_id, "template_id": created_id, "name": created_name, "status": "created"}

            if action == "delete":
                if not name_or_id:
                    return {"status": "error", "message": "delete requires name_or_id"}
                t_id = resolver.resolve_template(name_or_id, kind="page")
                client.workspace_templates.pages.delete(workspace_slug=workspace_slug, template_id=t_id)
                return {"template_id": t_id, "status": "deleted"}

        return {"status": "error", "message": f"Unsupported combination: target={target}, action={action}"}
