import axios, { AxiosRequestConfig } from "axios";
import { getAxiosInstance, isSessionAuthenticated } from "./auth.js";
import { debugLog } from "./debug.js";

/**
 * Makes an authenticated request to the Plane API
 *
 * This function handles routing requests to the correct API endpoint and authentication method:
 * - Pages endpoints (matching `/pages/`, `/pages-summary/`, etc.) use session authentication and /api/ prefix
 * - All other endpoints use API key authentication and /api/v1/ prefix
 *
 * Session authentication requires prior login via plane_login tool.
 * API key authentication requires PLANE_API_KEY environment variable.
 *
 * @template T - Expected response type
 * @param method - HTTP method (GET, POST, PATCH, DELETE, etc.)
 * @param path - API path without prefix (e.g., "workspaces/my-workspace/projects")
 * @param body - Request body for POST/PATCH/PUT requests (optional)
 * @returns Promise resolving to typed response data
 * @throws Error if authentication is required but not configured, or if request fails
 */
export async function makePlaneRequest<T>(method: string, path: string, body: any = null): Promise<T> {
  const hostUrl = process.env.PLANE_API_HOST_URL || "https://api.plane.so/";
  const host = hostUrl.endsWith("/") ? hostUrl : `${hostUrl}/`;

  // Conditional API versioning: Pages use /api/, others use /api/v1/
  // Plane has mixed versioning - pages endpoints don't use version prefix
  // Match pages-specific patterns to avoid false positives with future endpoints
  const isPagesEndpoint = /\/pages\/|\/pages$|\/pages-summary\/|\/favorite-pages\/|\/pages\/[^/]+\/description\/|\/pages\/[^/]+\/versions\//.test(path);
  const usesV1 = !isPagesEndpoint;
  const apiPrefix = usesV1 ? 'api/v1/' : 'api/';
  const url = `${host}${apiPrefix}${path}`;

  // Pages endpoints require session authentication, others use API key
  const requiresSession = isPagesEndpoint;

  await debugLog(`[REQUEST] ${method} ${url}`);
  await debugLog(`[REQUEST] Auth mode: ${requiresSession ? 'session (cookies)' : 'api_key'} (prefix: ${apiPrefix})`);

  try {
    let response;

    if (requiresSession) {
      // Use session authentication for pages endpoints
      if (!isSessionAuthenticated()) {
        throw new Error("Session authentication required. Please call plane_login first.");
      }

      const sessionAxios = getAxiosInstance();

      // Debug: Check what cookies are available
      const jar = (sessionAxios.defaults as any).jar;
      if (jar) {
        const cookies = await jar.getCookies(url);
        await debugLog(`[REQUEST] Cookies available for ${url}: ${cookies.map((c: any) => c.key).join(", ")}`);
        await debugLog(`[REQUEST] Total cookies: ${cookies.length}`);
      } else {
        await debugLog(`[REQUEST] WARNING: No cookie jar found!`);
      }

      const headers: Record<string, string> = {};

      // Get CSRF token from cookies for non-GET requests
      if (method.toUpperCase() !== "GET") {
        headers["Content-Type"] = "application/json";

        const jar = (sessionAxios.defaults as any).jar;
        if (jar) {
          // Use full URL to match path-scoped cookies
          const cookies = await jar.getCookies(url);
          const csrfCookie = cookies.find((c: any) => ["csrftoken", "csrf", "XSRF-TOKEN"].includes(c.key));
          if (csrfCookie) {
            headers["X-CSRFToken"] = csrfCookie.value;
            await debugLog(`[REQUEST] CSRF token found`);
          } else {
            await debugLog(`[REQUEST] WARNING: No CSRF token found in cookies!`);
          }
        }
      }

      const config: AxiosRequestConfig = {
        url,
        method,
        headers,
      };

      // Include body for non-GET requests
      if (method.toUpperCase() !== "GET" && body !== null) {
        config.data = body;
      }

      response = await sessionAxios(config);
    } else {
      // Use API key authentication for /api/v1/ endpoints
      const headers: Record<string, string> = {};

      if (process.env.PLANE_API_KEY) {
        headers["X-API-Key"] = process.env.PLANE_API_KEY;
      }

      // Only add Content-Type for non-GET requests
      if (method.toUpperCase() !== "GET") {
        headers["Content-Type"] = "application/json";
      }

      const config: AxiosRequestConfig = {
        url,
        method,
        headers,
      };

      // Only include body for non-GET requests
      if (method.toUpperCase() !== "GET" && body !== null) {
        config.data = body;
      }

      response = await axios(config);
    }

    await debugLog(`[REQUEST] Response status: ${response.status}`);
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error)) {
      await debugLog(`[REQUEST] Error: ${error.message}, status: ${error.response?.status}`);
      
      // Log full error response ONLY if VERBOSE debug mode is enabled
      if (process.env.PLANE_MCP_DEBUG === 'verbose') {
         await debugLog(`[REQUEST] Full error response: ${JSON.stringify(error.response?.data)}`);
      }
      
      // Throw sanitized error without response data
      throw new Error(`Request failed: ${error.message} (${error.response?.status})`);
    }
    throw error;
  }
}