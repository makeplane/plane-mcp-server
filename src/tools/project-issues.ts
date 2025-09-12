import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";

export const registerProjectIssueTools = (server: McpServer): void => {
  server.tool(
    "list_project_issues",
    "Get all issues for a specific project",
    {
      project_id: z.string().describe("The uuid identifier of the project to get issues for"),
    },
    async ({ project_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/`
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
