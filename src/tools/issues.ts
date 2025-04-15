import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../request-helper.js";
import { Issue as IssueSchema } from "../schemas.js";

export const registerIssueTools = (server: McpServer) => {
  server.tool(
    "get_issue_using_readable_identifier",
    "Get all issues for a specific project. When issue identifier is provided something like FIRST-123, ABC-123, etc. For FIRST-123, project_identifier is FIRST and issue_identifier is 123",
    {
      project_identifier: z.string().describe("The readable identifier of the project to get issues for"),
      issue_identifier: z.string().describe("The identifier of the issue to get"),
    },
    async ({ project_identifier, issue_identifier }) => {
      const issue = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/issues/${project_identifier}-${issue_identifier}/`
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(issue, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "get_issue_comments",
    "Get all comments for a specific issue. This requests project_id and issue_id as uuid parameters. If you have a readable identifier, you can use the get_issue_using_readable_identifier tool to get the issue_id and project_id",
    {
      project_id: z.string().describe("The uuid identifier of the project to get issues for"),
      issue_id: z.string().describe("The uuid identifier of the issue to get"),
    },
    async ({ project_id, issue_id }) => {
      const comments = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/${issue_id}/comments`
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(comments, null, 2),
          },
        ],
      };
    }
  );

  server.tool(
    "add_issue_comment",
    "Add a comment to a specific issue. This requests project_id and issue_id as uuid parameters. If you have a readable identifier, you can use the get_issue_using_readable_identifier tool to get the issue_id and project_id",
    {
      project_id: z.string().describe("The uuid identifier of the project to get issues for"),
      issue_id: z.string().describe("The uuid identifier of the issue to get"),
      comment_html: z.string().describe("The html content of the comment to add"),
    },
    async ({ project_id, issue_id, comment_html }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/${issue_id}/comments/`,
        {
          comment_html,
        }
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
    "create_issue",
    "Create an issue. This requests project_id as uuid parameter. If you have a readable identifier for project, you can use the get_projects tool to get the project_id from it",
    {
      project_id: z.string().describe("The uuid identifier of the project to create the issue for"),
      issue_data: IssueSchema.partial().required({
        name: true,
        description_html: true,
      }),
    },
    async ({ project_id, issue_data }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/`,
        issue_data
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
    "update_issue",
    "Update an issue. This requests project_id and issue_id as uuid parameters. If you have a readable identifier, you can use the get_issue_using_readable_identifier tool to get the issue_id and project_id",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue"),
      issue_id: z.string().describe("The uuid identifier of the issue to update"),
      issue_data: IssueSchema.partial().describe("The fields to update on the issue"),
    },
    async ({ project_id, issue_id, issue_data }) => {
      const response = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/${issue_id}/`,
        issue_data
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
