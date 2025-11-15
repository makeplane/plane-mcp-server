import axios, { AxiosRequestConfig } from "axios";
import fs from "fs";
import path from "path";
import { getAxiosInstance, isSessionAuthenticated } from "./auth.js";

const logFile = path.join("/tmp", "plane-mcp-debug.log");
function debugLog(message: string) {
  const timestamp = new Date().toISOString();
  const logMessage = `[${timestamp}] ${message}\n`;
  fs.appendFileSync(logFile, logMessage);
  console.error(message);
}

export async function makePlaneRequest<T>(method: string, path: string, body: any = null): Promise<T> {
  const hostUrl = process.env.PLANE_API_HOST_URL || "https://api.plane.so/";
  const host = hostUrl.endsWith("/") ? hostUrl : `${hostUrl}/`;

  // Conditional API versioning: Pages use /api/, others use /api/v1/
  // Plane has mixed versioning - pages endpoints don't use version prefix
  const isPagesEndpoint = /\/pages\/|\/pages$|\/pages-summary\/|\/favorite-pages\/|\/description\/|\/versions\//.test(path);
  const usesV1 = !isPagesEndpoint;
  const apiPrefix = usesV1 ? 'api/v1/' : 'api/';
  const url = `${host}${apiPrefix}${path}`;

  // Pages endpoints require session authentication, others use API key
  const requiresSession = isPagesEndpoint;

  debugLog(`[REQUEST] ${method} ${url}`);
  debugLog(`[REQUEST] Auth mode: ${requiresSession ? 'session (cookies)' : 'api_key'} (prefix: ${apiPrefix})`);

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
        debugLog(`[REQUEST] Cookies available for ${url}: ${cookies.map((c: any) => `${c.key}=${c.value.substring(0, 10)}...`).join(", ")}`);
        debugLog(`[REQUEST] Total cookies: ${cookies.length}`);
      } else {
        debugLog(`[REQUEST] WARNING: No cookie jar found!`);
      }

      const config: AxiosRequestConfig = {
        url,
        method,
        headers: {
          "Content-Type": "application/json",
        },
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

    debugLog(`[REQUEST] Response status: ${response.status}`);
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error)) {
      debugLog(`[REQUEST] Error: ${error.message}, status: ${error.response?.status}`);
      debugLog(`[REQUEST] Error response: ${JSON.stringify(error.response?.data)}`);
      throw new Error(`Request failed: ${error.message} (${error.response?.status}). Response: ${JSON.stringify(error.response?.data)}`);
    }
    throw error;
  }
}
