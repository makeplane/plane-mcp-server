import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";
import { Module as ModuleSchema } from "../schemas.js";

export const registerModuleTools = (server: McpServer) => {
  server.tool(
    "list_modules",
    "Get all modules for a specific project",
    {
      project_id: z.string().describe("The uuid identifier of the project to get modules for"),
    },
    async ({ project_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/`
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(response, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get_module",
    "Get details of a specific module",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the module"),
      module_id: z.string().describe("The uuid identifier of the module to get"),
    },
    async ({ project_id, module_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/${module_id}/`
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(response, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "create_module",
    "Create a new module in a project",
    {
      project_id: z.string().describe("The uuid identifier of the project to create the module in"),
      module_data: ModuleSchema.partial().required({
        name: true,
      }),
    },
    async ({ project_id, module_data }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/`,
        module_data
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(response, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "update_module",
    "Update an existing module",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the module"),
      module_id: z.string().describe("The uuid identifier of the module to update"),
      module_data: ModuleSchema.partial().describe("The fields to update on the module"),
    },
    async ({ project_id, module_id, module_data }) => {
      const response = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/${module_id}/`,
        module_data
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(response, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "delete_module",
    "Delete a module",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the module"),
      module_id: z.string().describe("The uuid identifier of the module to delete"),
    },
    async ({ project_id, module_id }) => {
      const response = await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/${module_id}/`
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(response, null, 2),
          },
        ],
      };
    }
  );
};
