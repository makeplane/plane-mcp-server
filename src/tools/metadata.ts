import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../request-helper.js";
import { IssueTypeAPI as IssueTypeSchema, Label as LabelSchema, State as StateSchema } from "../schemas.js";

export const registerMetadataTools = (server: McpServer) => {
  server.tool(
    "list_issue_types",
    "Get all issue types for a specific project",
    {
      project_id: z.string().describe("The uuid identifier of the project to get issue types for"),
    },
    async ({ project_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issue-types/`
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
    "get_issue_type",
    "Get details of a specific issue type",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue type"),
      type_id: z.string().describe("The uuid identifier of the issue type to get"),
    },
    async ({ project_id, type_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issue-types/${type_id}/`
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
    "create_issue_type",
    "Create a new issue type in a project",
    {
      project_id: z.string().describe("The uuid identifier of the project to create the issue type in"),
      issue_type_data: IssueTypeSchema.partial().required({
        name: true,
        description: true,
      }),
    },
    async ({ project_id, issue_type_data }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issue-types/`,
        issue_type_data
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
    "update_issue_type",
    "Update an existing issue type",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue type"),
      type_id: z.string().describe("The uuid identifier of the issue type to update"),
      issue_type_data: IssueTypeSchema.partial().describe("The fields to update on the issue type"),
    },
    async ({ project_id, type_id, issue_type_data }) => {
      const response = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issue-types/${type_id}/`,
        issue_type_data
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
    "delete_issue_type",
    "Delete an issue type",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the issue type"),
      type_id: z.string().describe("The uuid identifier of the issue type to delete"),
    },
    async ({ project_id, type_id }) => {
      const response = await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/issue-types/${type_id}/`
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
    "list_states",
    "Get all states for a specific project",
    {
      project_id: z.string().describe("The uuid identifier of the project to get states for"),
    },
    async ({ project_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/states/`
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
    "get_state",
    "Get details of a specific state",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the state"),
      state_id: z.string().describe("The uuid identifier of the state to get"),
    },
    async ({ project_id, state_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/states/${state_id}/`
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
    "create_state",
    "Create a new state in a project",
    {
      project_id: z.string().describe("The uuid identifier of the project to create the state in"),
      state_data: StateSchema.partial().required({
        name: true,
        color: true,
        group: true,
      }),
    },
    async ({ project_id, state_data }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/states/`,
        state_data
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
    "update_state",
    "Update an existing state",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the state"),
      state_id: z.string().describe("The uuid identifier of the state to update"),
      state_data: StateSchema.partial().describe("The fields to update on the state"),
    },
    async ({ project_id, state_id, state_data }) => {
      const response = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/states/${state_id}/`,
        state_data
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
    "delete_state",
    "Delete a state",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the state"),
      state_id: z.string().describe("The uuid identifier of the state to delete"),
    },
    async ({ project_id, state_id }) => {
      const response = await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/states/${state_id}/`
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
    "list_labels",
    "Get all labels for a specific project",
    {
      project_id: z.string().describe("The uuid identifier of the project to get labels for"),
    },
    async ({ project_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/labels/`
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
    "get_label",
    "Get details of a specific label",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the label"),
      label_id: z.string().describe("The uuid identifier of the label to get"),
    },
    async ({ project_id, label_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/labels/${label_id}/`
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
    "create_label",
    "Create a new label in a project",
    {
      project_id: z.string().describe("The uuid identifier of the project to create the label in"),
      label_data: LabelSchema.partial().required({
        name: true,
        color: true,
      }),
    },
    async ({ project_id, label_data }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/labels/`,
        label_data
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
    "update_label",
    "Update an existing label",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the label"),
      label_id: z.string().describe("The uuid identifier of the label to update"),
      label_data: LabelSchema.partial().describe("The fields to update on the label"),
    },
    async ({ project_id, label_id, label_data }) => {
      const response = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/labels/${label_id}/`,
        label_data
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
    "delete_label",
    "Delete a label",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the label"),
      label_id: z.string().describe("The uuid identifier of the label to delete"),
    },
    async ({ project_id, label_id }) => {
      const response = await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/labels/${label_id}/`
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
