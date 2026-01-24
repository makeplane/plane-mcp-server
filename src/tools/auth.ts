import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { 
  authenticateWithPassword, 
  isSessionAuthenticated, 
  resetAuthentication,
  importCookies 
} from "../common/auth.js";

/**
 * Registers authentication-related MCP tools
 *
 * Provides tools for:
 * - plane_login: Session authentication via email/password
 * - plane_import_cookies: Import browser cookies for cloud SSO accounts
 * - plane_auth_status: Check current authentication state
 * - plane_logout: Clear session and reset authentication
 *
 * @param server - MCP server instance to register tools with
 */
export const registerAuthTools = (server: McpServer) => {
  server.tool(
    "plane_login",
    "Authenticate with Plane using email and password for session-based access to Pages and /api/ endpoints. Note: Cloud accounts with SSO may need to use plane_import_cookies instead.",
    {
      email: z.string().email().describe("Your Plane account email"),
      password: z.string().describe("Your Plane account password"),
      host: z.string().url().optional().describe("Plane host URL (defaults to PLANE_API_HOST_URL or https://api.plane.so/)"),
    },
    async ({ email, password, host }) => {
      const hostUrl = host || process.env.PLANE_API_HOST_URL || "https://api.plane.so/";
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
                  note: "Session authentication enables access to Pages and /api/ endpoints. Standard /api/v1/ endpoints (Issues, Projects, etc.) still require an API key if configured.",
                },
                null,
                2
              ),
            },
          ],
        };
      } else {
        // Enhanced error messaging for cloud SSO users
        let troubleshooting = result.message;
        if (result.error === 'credentials' && hostUrl.includes('api.plane.so')) {
          troubleshooting += "\n\nNote for Cloud users: If you signed up with Google/GitHub SSO, password authentication may not work. You have two options:\n" +
            "1. Set a password in your Plane account settings, OR\n" +
            "2. Use 'plane_import_cookies' to import your browser session cookies";
        }
        
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  message: "Authentication failed",
                  authenticated: false,
                  error: result.error,
                  details: troubleshooting,
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
    "plane_import_cookies",
    "Import browser cookies for Plane cloud SSO authentication. This allows you to use your existing browser session. Export cookies from your browser using a cookie export extension, then paste the JSON here.",
    {
      cookies_json: z.string().describe("JSON string containing exported cookies from your browser session"),
      host: z.string().url().optional().describe("Plane host URL (defaults to PLANE_API_HOST_URL or https://api.plane.so/)"),
    },
    async ({ cookies_json, host }) => {
      const hostUrl = host || process.env.PLANE_API_HOST_URL || "https://api.plane.so/";
      
      try {
        const result = await importCookies(cookies_json, hostUrl);
        
        if (result.success) {
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify(
                  {
                    message: "Successfully imported browser cookies",
                    authenticated: true,
                    cookies_imported: result.cookiesImported,
                    note: "You can now access Pages API using your browser session",
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
                    message: "Failed to import cookies",
                    authenticated: false,
                    error: result.message,
                  },
                  null,
                  2
                ),
              },
            ],
          };
        }
      } catch (error) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  message: "Error importing cookies",
                  authenticated: false,
                  error: error instanceof Error ? error.message : String(error),
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

      let currentMode = "";
      if (authenticated && hasApiKey) {
        currentMode = "session + API key (full access)";
      } else if (authenticated) {
        currentMode = "session (Pages + /api/ endpoints)";
      } else if (hasApiKey) {
        currentMode = "API key only (/api/v1/ endpoints)";
      } else {
        currentMode = "not authenticated";
      }

      let note = "";
      if (authenticated) {
        note = "Session active: Access to Pages and /api/ endpoints enabled. (API key required for /api/v1/ endpoints)";
      } else if (hasApiKey) {
        note = "API key configured: Access to /api/v1/ endpoints (Projects, Issues, etc.). Use plane_login or plane_import_cookies for Pages access.";
      } else {
        note = "No authentication configured. Set PLANE_API_KEY for /api/v1/ access, or use plane_login/plane_import_cookies for Pages access.";
      }

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                session_authenticated: authenticated,
                api_key_configured: hasApiKey,
                current_mode: currentMode,
                note: note,
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
    "plane_logout",
    "Log out and clear all authentication state",
    {},
    async () => {
      await resetAuthentication();

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                message: "Successfully logged out",
                authenticated: false,
                note: "All session cookies have been cleared",
              },
              null,
              2
            ),
          },
        ],
      };
    }
  );
};
