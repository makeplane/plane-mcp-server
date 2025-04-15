import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../common/request-helper.js";
import { Cycle as CycleSchema } from "../schemas.js";

export const registerCycleTools = (server: McpServer) => {
  server.tool(
    "list_cycles",
    "Get all cycles for a specific project",
    {
      project_id: z.string().describe("The uuid identifier of the project to get cycles for"),
    },
    async ({ project_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/`
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
    "get_cycle",
    "Get details of a specific cycle",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the cycle"),
      cycle_id: z.string().describe("The uuid identifier of the cycle to get"),
    },
    async ({ project_id, cycle_id }) => {
      const response = await makePlaneRequest(
        "GET",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/${cycle_id}/`
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
    "create_cycle",
    "Create a new cycle in a project",
    {
      project_id: z.string().describe("The uuid identifier of the project to create the cycle in"),
      cycle_data: CycleSchema.partial()
        .required({
          name: true,
          project_id: true,
        })
        .describe("The data for creating the cycle"),
    },
    async ({ project_id, cycle_data }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/`,
        cycle_data
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
    "update_cycle",
    "Update an existing cycle",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the cycle"),
      cycle_id: z.string().describe("The uuid identifier of the cycle to update"),
      cycle_data: CycleSchema.partial().describe("The fields to update on the cycle"),
    },
    async ({ project_id, cycle_id, cycle_data }) => {
      const response = await makePlaneRequest(
        "PATCH",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/${cycle_id}/`,
        cycle_data
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
    "delete_cycle",
    "Delete a cycle",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the cycle"),
      cycle_id: z.string().describe("The uuid identifier of the cycle to delete"),
    },
    async ({ project_id, cycle_id }) => {
      await makePlaneRequest(
        "DELETE",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/${cycle_id}/`
      );
      return {
        content: [
          {
            type: "text",
            text: "Cycle deleted successfully",
          },
        ],
      };
    }
  );

  server.tool(
    "transfer_cycle_issues",
    "Transfer issues from one cycle to another",
    {
      project_id: z.string().describe("The uuid identifier of the project containing the cycle"),
      cycle_id: z.string().describe("The uuid identifier of the source cycle"),
      new_cycle_id: z.string().describe("The uuid identifier of the target cycle"),
    },
    async ({ project_id, cycle_id, new_cycle_id }) => {
      const response = await makePlaneRequest(
        "POST",
        `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects/${project_id}/cycles/${cycle_id}/transfer-issues/`,
        { new_cycle_id }
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
