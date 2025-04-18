import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";

export const registerCycleIssueTools = (server: McpServer) => {
  server.tool(
    "list_cycle_issues",
    "Get all issues for a specific cycle",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the cycle"),
      cycle_id: z.string().describe("The uuid identifier of the cycle to get issues for"),
    },
    async ({ project_id, cycle_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/${cycle_id}/cycle-issues/`
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
    "add_cycle_issues",
    "Add issues to a cycle",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the cycle"),
      cycle_id: z.string().describe("The uuid identifier of the cycle to add issues to"),
      issues: z.array(z.string()).describe("Array of issue UUIDs to add to the cycle"),
    },
    async ({ project_id, cycle_id, issues }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/${cycle_id}/cycle-issues/`,
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
    "delete_cycle_issue",
    "Remove an issue from a cycle",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the cycle"),
      cycle_id: z.string().describe("The uuid identifier of the cycle containing the issue"),
      issue_id: z.string().describe("The uuid identifier of the issue to remove from the cycle"),
    },
    async ({ project_id, cycle_id, issue_id }) => {
      await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/${cycle_id}/cycle-issues/${issue_id}/`
      );
      return {
        content: [
          {
            type: "text",
            text: "Issue removed from cycle successfully",
          },
        ],
      };
    }
  );
};
