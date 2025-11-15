import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { authenticateWithPassword, isSessionAuthenticated, resetAuthentication } from "../common/auth.js";

/**
 * Registers authentication-related MCP tools
 *
 * Provides tools for:
 * - plane_login: Session authentication via email/password
 * - plane_auth_status: Check current authentication state
 * - plane_logout: Clear session and reset authentication
 *
 * @param server - MCP server instance to register tools with
 */
export const registerAuthTools = (server: McpServer) => {
  server.tool(
    "plane_login",
    "Authenticate with Plane using email and password for session-based access to Pages and /api/ endpoints",
    {
      email: z.string().email().describe("Your Plane account email"),
      password: z.string().describe("Your Plane account password"),
    },
    async ({ email, password }) => {
      const hostUrl = process.env.PLANE_API_HOST_URL || "https://api.plane.so/";
      const result = await authenticateWithPassword(email, password, hostUrl);

      if (result.success) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  message: "Successfully authenticated with Plane",
                  authenticated: true,
                  note: "Session authentication enabled for Pages and /api/ endpoints. Other endpoints (/api/v1/) use API key if configured.",
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
                  error: result.error,
                  details: result.message,
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
                current_mode: authenticated ? "session (Pages + /api/ endpoints)" : hasApiKey ? "api_key (/api/v1/ endpoints)" : "unauthenticated",
                note: authenticated
                  ? "Using session authentication - access to Pages and /api/ endpoints"
                  : hasApiKey
                  ? "Using API key - access to /api/v1/ endpoints only"
                  : "No authentication configured",
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
    await resetAuthentication();

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
