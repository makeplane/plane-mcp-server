import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { authenticateWithPassword, isSessionAuthenticated, resetAuthentication } from "../common/auth.js";

export const registerAuthTools = (server: McpServer) => {
  server.tool(
    "plane_login",
    "Authenticate with Plane using email and password to enable full API access including Pages",
    {
      email: z.string().email().describe("Your Plane account email"),
      password: z.string().describe("Your Plane account password"),
    },
    async ({ email, password }) => {
      const hostUrl = process.env.PLANE_API_HOST_URL || "https://api.plane.so/";
      const success = await authenticateWithPassword(email, password, hostUrl);

      if (success) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  message: "Successfully authenticated with Plane",
                  authenticated: true,
                  note: "Session authentication enabled. Pages and other endpoints now fully accessible.",
                },
                null,
                2
              ),
            },
          ],
        };
      } else {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  message: "Authentication failed",
                  authenticated: false,
                  error: "Invalid credentials or connection error",
                },
                null,
                2
              ),
            },
          ],
        };
      }
    }
  );

  server.tool(
    "plane_auth_status",
    "Check current Plane authentication status",
    {},
    async () => {
      const authenticated = isSessionAuthenticated();
      const hasApiKey = !!process.env.PLANE_API_KEY;

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                session_authenticated: authenticated,
                api_key_configured: hasApiKey,
                current_mode: authenticated ? "session (full access)" : hasApiKey ? "api_key (limited)" : "unauthenticated",
                note: authenticated
                  ? "Using session authentication - all endpoints available"
                  : "Using API key - some endpoints may be restricted",
              },
              null,
              2
            ),
          },
        ],
      };
    }
  );

  server.tool("plane_logout", "Logout and clear Plane session", {}, async () => {
    resetAuthentication();

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              message: "Session cleared",
              authenticated: false,
            },
            null,
            2
          ),
        },
      ],
    };
  });
};
