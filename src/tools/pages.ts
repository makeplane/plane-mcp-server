import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";
import { type Page } from "../schemas.js";

export const registerPageTools = (server: McpServer) => {
  server.tool(
    "list_pages",
    "Get all pages for a specific project",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project to get pages for"),
    },
    async ({ project_id }) => {
      const pages: Page[] = await makePlaneRequest<Page[]>(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/`
      );

      const simplifiedPages = pages.map((page) => ({
        id: page.id,
        name: page.name,
        owned_by: page.owned_by,
        access: page.access,
        is_locked: page.is_locked,
        is_favorite: page.is_favorite,
        parent: page.parent,
        archived_at: page.archived_at,
        created_at: page.created_at,
        updated_at: page.updated_at,
      }));

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(simplifiedPages, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get_page",
    "Get details of a specific page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to get"),
    },
    async ({ project_id, page_id }) => {
      const page = await makePlaneRequest<Page>(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(page, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "create_page",
    "Create a new page in a project",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project to create the page in"),
      name: z.string().describe("The name of the page"),
      description_html: z.string().optional().describe("The HTML content of the page"),
      access: z.number().int().gte(0).lte(1).optional().describe("0 = Public, 1 = Private. Defaults to 0 (Public)"),
      color: z.string().optional().describe("Color for the page"),
      parent: z.string().uuid().optional().describe("Parent page ID if this is a sub-page"),
    },
    async ({ project_id, name, description_html, access, color, parent }) => {
      const pageData: any = {
        name,
      };

      if (description_html !== undefined) {
        pageData.description_html = description_html;
      }

      if (access !== undefined) {
        pageData.access = access;
      }

      if (color !== undefined) {
        pageData.color = color;
      }

      if (parent !== undefined) {
        pageData.parent = parent;
      }

      const page = await makePlaneRequest<Page>(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/`,
        pageData
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(page, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "update_page",
    "Update an existing page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to update"),
      name: z.string().optional().describe("The name of the page"),
      description_html: z.string().optional().describe("The HTML content of the page"),
      access: z.number().int().gte(0).lte(1).optional().describe("0 = Public, 1 = Private"),
      color: z.string().optional().describe("Color for the page"),
      parent: z.string().uuid().optional().describe("Parent page ID if this is a sub-page"),
    },
    async ({ project_id, page_id, name, description_html, access, color, parent }) => {
      const updateData: any = {};

      if (name !== undefined) {
        updateData.name = name;
      }

      if (description_html !== undefined) {
        updateData.description_html = description_html;
      }

      if (access !== undefined) {
        updateData.access = access;
      }

      if (color !== undefined) {
        updateData.color = color;
      }

      if (parent !== undefined) {
        updateData.parent = parent;
      }

      const page = await makePlaneRequest<Page>(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/`,
        updateData
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(page, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "delete_page",
    "Delete a page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to delete"),
    },
    async ({ project_id, page_id }) => {
      await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ message: "Page deleted successfully", page_id }, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "archive_page",
    "Archive a page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to archive"),
    },
    async ({ project_id, page_id }) => {
      await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/archive/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ message: "Page archived successfully", page_id }, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "unarchive_page",
    "Unarchive a page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to unarchive"),
    },
    async ({ project_id, page_id }) => {
      await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/archive/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ message: "Page unarchived successfully", page_id }, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "lock_page",
    "Lock a page to prevent editing",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to lock"),
    },
    async ({ project_id, page_id }) => {
      await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/lock/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ message: "Page locked successfully", page_id }, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "unlock_page",
    "Unlock a page to allow editing",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to unlock"),
    },
    async ({ project_id, page_id }) => {
      await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/lock/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ message: "Page unlocked successfully", page_id }, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "favorite_page",
    "Mark a page as favorite for quick access",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to favorite"),
    },
    async ({ project_id, page_id }) => {
      await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/favorite-pages/${page_id}/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ message: "Page marked as favorite", page_id }, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "unfavorite_page",
    "Remove a page from favorites",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to unfavorite"),
    },
    async ({ project_id, page_id }) => {
      await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/favorite-pages/${page_id}/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ message: "Page removed from favorites", page_id }, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "duplicate_page",
    "Duplicate a page to create a template or copy",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to duplicate"),
    },
    async ({ project_id, page_id }) => {
      const duplicatedPage = await makePlaneRequest<Page>(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/duplicate/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(duplicatedPage, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "set_page_access",
    "Set page access level (public or private)",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page to update"),
      access: z.number().int().gte(0).lte(1).describe("0 = Public, 1 = Private"),
    },
    async ({ project_id, page_id, access }) => {
      const page = await makePlaneRequest<Page>(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/access/`,
        { access }
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(page, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get_pages_summary",
    "Get a summary view of pages (filtered list of root-level pages)",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project to get pages summary for"),
    },
    async ({ project_id }) => {
      const pages: Page[] = await makePlaneRequest<Page[]>(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages-summary/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(pages, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get_page_description",
    "Get the description content of a specific page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page"),
    },
    async ({ project_id, page_id }) => {
      const description = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/description/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(description, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "update_page_description",
    "Update the description content of a specific page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page"),
      description_html: z.string().describe("The HTML content for the page description"),
    },
    async ({ project_id, page_id, description_html }) => {
      const description = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/description/`,
        { description_html }
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(description, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get_page_versions",
    "Get version history for a specific page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page"),
    },
    async ({ project_id, page_id }) => {
      const versions = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/versions/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(versions, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get_page_version",
    "Get a specific version of a page",
    {
      project_id: z.string().uuid().describe("The uuid identifier of the project containing the page"),
      page_id: z.string().uuid().describe("The uuid identifier of the page"),
      version_id: z.string().uuid().describe("The uuid identifier of the specific version"),
    },
    async ({ project_id, page_id, version_id }) => {
      const version = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/pages/${page_id}/versions/${version_id}/`
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(version, null, 2),
          },
        ],
      };
    }
  );
};
