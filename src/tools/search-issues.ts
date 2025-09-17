import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";

export const registerSearchIssueTools = (server: McpServer): void => {
  server.tool(
    "search_issues",
    "Use this to search issues by text query. This requests project_id as uuid parameter. If you have a readable identifier for project, you can use the get_projects tool to get the project_id from it",
    {
      limit: z.number().describe("The number of issues to return").optional(),
      project_id: z.string().describe("The uuid identifier of the project to search issues for").optional(),
      search: z.string().describe("The search query"),
      workspace_search: z.boolean().describe("Whether to search across all projects in the workspace").optional(),
    },
    async ({ limit, project_id, search, workspace_search }) => {
      // send the params if not null as query string
      const queryParams = new URLSearchParams();
      if (limit) queryParams.set("limit", limit.toString());
      if (project_id) queryParams.set("project_id", project_id);
      if (workspace_search) queryParams.set("workspace_search", workspace_search.toString());
      if (search) queryParams.set("search", search);

      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/issues/search/?${queryParams.toString()}`
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
