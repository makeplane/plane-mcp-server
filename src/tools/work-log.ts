import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";
import { IssueWorkLog } from "../schemas.js";

export const registerWorkLogTools = (server: McpServer) => {
  server.tool(
    "get_issue_worklogs",
    "Get all worklogs for a specific issue",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue"),
      issue_id: z.string().describe("The uuid identifier of the issue to get worklogs for"),
    },
    async ({ project_id, issue_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/${issue_id}/worklogs/`
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
    "get_total_worklogs",
    "Get total logged time for a project",
    {
      project_id: z.string().describe("The uuid identifier of the project to get total worklogs for"),
    },
    async ({ project_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/total-worklogs/`
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
    "create_worklog",
    "Create a new worklog for an issue",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue"),
      issue_id: z.string().describe("The uuid identifier of the issue to create worklog for"),
      worklog_data: IssueWorkLog.partial()
        .required({
          duration: true,
          description: true,
        })
        .describe("The data for creating the worklog"),
    },
    async ({ project_id, issue_id, worklog_data }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/${issue_id}/worklogs/`,
        worklog_data
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
    "update_worklog",
    "Update an existing worklog",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue"),
      issue_id: z.string().describe("The uuid identifier of the issue containing the worklog"),
      worklog_id: z.string().describe("The uuid identifier of the worklog to update"),
      worklog_data: IssueWorkLog.partial().describe("The fields to update on the worklog"),
    },
    async ({ project_id, issue_id, worklog_id, worklog_data }) => {
      const response = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/${issue_id}/worklogs/${worklog_id}/`,
        worklog_data
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
    "delete_worklog",
    "Delete a worklog",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue"),
      issue_id: z.string().describe("The uuid identifier of the issue containing the worklog"),
      worklog_id: z.string().describe("The uuid identifier of the worklog to delete"),
    },
    async ({ project_id, issue_id, worklog_id }) => {
      await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/${issue_id}/worklogs/${worklog_id}/`
      );
      return {
        content: [
          {
            type: "text",
            text: "Worklog deleted successfully",
          },
        ],
      };
    }
  );
};
