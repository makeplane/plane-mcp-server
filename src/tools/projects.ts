import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { makePlaneRequest } from "../request-helper.js";

export const registerProjectTools = (server: McpServer) => {
  server.tool("get_projects", "Get all projects for the current user", {}, async () => {
    const projects = await makePlaneRequest("GET", `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects`);
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(projects, null, 2),
        },
      ],
    };
  });

  server.tool(
    "create_project",
    "Create a new project",
    {
      name: z.string().describe("The name of the project"),
    },
    async ({ name }) => {
      const project = await makePlaneRequest("POST", `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/projects`, {
        name,
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(project, null, 2),
          },
        ],
      };
    }
  );
};
