import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../request-helper.js";

export const registerModuleIssueTools = (server: McpServer) => {
  server.tool(
    "list_module_issues",
    "Get all issues for a specific module",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the module"),
      module_id: z.string().describe("The uuid identifier of the module to get issues for"),
    },
    async ({ project_id, module_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/${module_id}/module-issues/`
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
    "add_module_issues",
    "Add issues to a module. Assign module to issues.",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the module"),
      module_id: z.string().describe("The uuid identifier of the module to add issues to"),
      issues: z.array(z.string()).describe("Array of issue UUIDs to add to the module"),
    },
    async ({ project_id, module_id, issues }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/${module_id}/module-issues/`,
        { issues }
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
    "delete_module_issue",
    "Remove an issue from a module. Unassign module from issue.",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the module"),
      module_id: z.string().describe("The uuid identifier of the module containing the issue"),
      issue_id: z.string().describe("The uuid identifier of the issue to remove from the module"),
    },
    async ({ project_id, module_id, issue_id }) => {
      const response = await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/modules/${module_id}/module-issues/${issue_id}/`
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
