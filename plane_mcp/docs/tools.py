"""Intent-based tool surface for documentation, pages, collections, and templates."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP

from plane_mcp.client import get_plane_client_context
from plane_mcp.docs.lod import DocsLOD, apply_doc_lod
from plane_mcp.journey.base import mcp_error_boundary
from plane_mcp.resolver import EntityResolver
from plane_mcp.sanitize import apply_snippet_edits, markdown_or_html_to_safe_html
from plane_mcp.yaml_formatter import with_yaml


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

        matched_pages = []

        # 1. Ticket linked pages
        if ticket_id:
            resolved_ticket_id = resolver.resolve_ticket(ticket_id)
            parts = ticket_id.upper().split("-")
            proj_id = resolver.resolve_project(parts[0])
            links = client.work_items.pages.list(
                workspace_slug=workspace_slug, project_id=proj_id, work_item_id=resolved_ticket_id
            )
            items = getattr(links, "results", links if isinstance(links, list) else [])
            for item in items:
                p_id = getattr(item, "page_id", getattr(item, "page", None))
                if p_id:
                    page_obj = client.pages.retrieve_project_page(
                        workspace_slug=workspace_slug, project_id=proj_id, page_id=str(p_id)
                    )
                    matched_pages.append(apply_doc_lod(page_obj, lod=lod, project_slug=parts[0]))
            return {"results": matched_pages, "count": len(matched_pages)}

        # 2. Collection pages
        if collection:
            coll_id = resolver.resolve_collection(collection)
            from plane.models.query_params import CollectionPageQueryParams

            coll_pages = client.collections.pages.list(
                workspace_slug=workspace_slug, collection_id=coll_id, params=CollectionPageQueryParams(per_page=limit)
            )
            items = getattr(coll_pages, "results", coll_pages if isinstance(coll_pages, list) else [])
            for item in items:
                matched_pages.append(apply_doc_lod(item, lod=lod))
            return {"results": matched_pages, "count": len(matched_pages), "collection": collection}

        # 3. Project or Workspace pages
        if project_slug:
            proj_id = resolver.resolve_project(project_slug)
            from plane.models.query_params import PaginatedQueryParams

            pages_resp = client.pages.list_project_pages(
                workspace_slug=workspace_slug, project_id=proj_id, params=PaginatedQueryParams(per_page=limit)
            )
            items = getattr(pages_resp, "results", pages_resp if isinstance(pages_resp, list) else [])
            for item in items:
                name = str(getattr(item, "name", ""))
                if query and query.lower() not in name.lower():
                    continue
                matched_pages.append(apply_doc_lod(item, lod=lod, project_slug=project_slug))
        else:
            from plane.models.query_params import PaginatedQueryParams

            pages_resp = client.pages.list_workspace_pages(
                workspace_slug=workspace_slug, params=PaginatedQueryParams(per_page=limit)
            )
            items = getattr(pages_resp, "results", pages_resp if isinstance(pages_resp, list) else [])
            for item in items:
                name = str(getattr(item, "name", ""))
                if query and query.lower() not in name.lower():
                    continue
                matched_pages.append(apply_doc_lod(item, lod=lod))

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
            _ = resolver.resolve_template(template, kind="page", project_slug=project_slug)
            if not safe_html:
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

        # Optional ticket attachments
        attached_tickets = []
        if ticket_ids and project_id:
            from plane.models.work_item_pages import CreateWorkItemPage

            for tid in ticket_ids:
                try:
                    wid = resolver.resolve_ticket(tid)
                    client.work_items.pages.create(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        work_item_id=wid,
                        data=CreateWorkItemPage(page_id=created_id),
                    )
                    attached_tickets.append(tid)
                except Exception:
                    pass

        res = apply_doc_lod(created, lod=DocsLOD.STANDARD, project_slug=project_slug)
        if attached_tickets:
            res["attached_tickets"] = attached_tickets
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
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)

        wid = resolver.resolve_ticket(ticket_id)
        parts = ticket_id.upper().split("-")
        proj_id = resolver.resolve_project(parts[0])

        if action == "list":
            links = client.work_items.pages.list(workspace_slug=workspace_slug, project_id=proj_id, work_item_id=wid)
            items = getattr(links, "results", links if isinstance(links, list) else [])
            return {"ticket_id": ticket_id, "linked_pages": [str(getattr(x, "page_id", x)) for x in items]}

        if not page:
            return {"status": "error", "message": f"Action '{action}' requires page parameter"}

        page_id, _ = resolver.resolve_page(page, project_slug=parts[0])

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

            if action == "delete":
                if not name_or_id:
                    return {"status": "error", "message": "delete requires name_or_id"}
                t_id = resolver.resolve_template(name_or_id, kind="page")
                client.workspace_templates.pages.delete(workspace_slug=workspace_slug, template_id=t_id)
                return {"template_id": t_id, "status": "deleted"}

        return {"status": "error", "message": f"Unsupported combination: target={target}, action={action}"}
