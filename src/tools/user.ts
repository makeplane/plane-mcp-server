import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import { makePlaneRequest } from "../common/request-helper.js";

export const registerUserTools = (server: McpServer) => {
  server.tool("get_user", "Get the current user's information", {}, async () => {
    const user = await makePlaneRequest("GET", "users/me/");
    return {
      content: [{ type: "text", text: JSON.stringify(user, null, 2) }],
    };
  });
  server.tool("get_workspace_members", "Get all members in the current workspace", {}, async () => {
    const members = await makePlaneRequest("GET", `workspaces/${process.env.PLANE_WORKSPACE_SLUG}/members/`);
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(members, null, 2),
        },
      ],
    };
  });
};
