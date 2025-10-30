import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";
import { type Issue, Issue as IssueSchema } from "../schemas.js";

type IssueStateSummary = {
  id: string;
  name?: string;
  color?: string;
  group?: string;
};

type IssuePrioritySummary = {
  id?: string;
  label?: string;
  name?: string;
  key?: string;
};

type IssueWithDetails = Issue & {
  state_detail?: IssueStateSummary | null;
  priority_detail?: IssuePrioritySummary | null;
};

type IssuesResponse = {
  grouped_by: null;
  sub_grouped_by: null;
  total_count: number;
  next_cursor: string;
  prev_cursor: string;
  next_page_results: boolean;
  prev_page_results: boolean;
  count: number;
  total_pages: number;
  total_results: number;
  extra_stats: null;
  results: IssueWithDetails[];
};

export const registerIssueTools = (server: McpServer): void => {
  server.tool(
    "list_project_issues",
    "Get all issues for a specific project. This requests project_id as uuid parameter. If you have a readable identifier for project, you can use the get_projects tool to get the project_id from it",
    {
      project_id: z.string().describe("The uuid identifier of the project to get issues for"),
    },
    async ({ project_id }) => {
      const issuesResponse: IssuesResponse = await makePlaneRequest<IssuesResponse>(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issues/`
      );

      // Return only essential fields to reduce token usage and improve LLM processing
      const simplifiedIssues = issuesResponse.results.map((issue) => {
        const stateDetail = issue.state_detail ?? null;
        const priorityDetail =
          issue.priority_detail ??
          (typeof issue.priority === "object" && issue.priority !== null
            ? (issue.priority as IssuePrioritySummary)
            : null);

        return {
          id: issue.id,
          name: issue.name,
          sequence_id: issue.sequence_id,
          state: {
            id: issue.state ?? stateDetail?.id ?? null,
            name: stateDetail?.name ?? null,
            color: stateDetail?.color ?? null,
            group: stateDetail?.group ?? null,
          },
          priority: {
            id: typeof issue.priority === "string" ? issue.priority : (priorityDetail?.id ?? null),
            label: priorityDetail?.label ?? priorityDetail?.name ?? null,
            key: priorityDetail?.key ?? null,
          },
          created_at: issue.created_at,
          updated_at: issue.updated_at,
        };
      });

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                total_count: issuesResponse.total_count,
                count: issuesResponse.count,
                results: simplifiedIssues,
              },
              null,
              2
            ),
          },
        ],
      };
    }
  );

  server.tool(
    "get_issue_using_readable_identifier",
    "Get a specific issue using its readable identifier. When issue identifier is provided something like FIRST-123, ABC-123, etc. For FIRST-123, project_identifier is FIRST and issue_identifier is 123",
    {
      project_identifier: z.string().describe("The readable identifier of the project (e.g., 'FIRST' for FIRST-123)"),
      issue_identifier: z.string().describe("The issue number (e.g., '123' for FIRST-123)"),
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
