import axios, { AxiosInstance } from "axios";
import { wrapper } from "axios-cookiejar-support";
import { CookieJar } from "tough-cookie";
import fs from "fs";
import path from "path";

const logFile = path.join("/tmp", "plane-mcp-debug.log");
function debugLog(message: string) {
  const timestamp = new Date().toISOString();
  const logMessage = `[${timestamp}] ${message}\n`;
  fs.appendFileSync(logFile, logMessage);
  console.error(message);
}

let axiosInstance: AxiosInstance | null = null;
let isAuthenticated = false;

debugLog(`[AUTH] Module loaded - PID: ${process.pid}`);

export function getAxiosInstance(): AxiosInstance {
  if (!axiosInstance) {
    debugLog("[AUTH] Creating new axios instance with cookie jar");
    const jar = new CookieJar();
    axiosInstance = wrapper(axios.create({ jar, withCredentials: true }));
  } else {
    debugLog("[AUTH] Reusing existing axios instance");
  }
  return axiosInstance;
}

export async function authenticateWithPassword(
  email: string,
  password: string,
  hostUrl: string
): Promise<boolean> {
  try {
    const instance = getAxiosInstance();
    const host = hostUrl.endsWith("/") ? hostUrl : `${hostUrl}/`;

    debugLog("[AUTH] Starting authentication flow...");
    debugLog(`[AUTH] Host URL: ${host}`);

    // Step 1: Get CSRF token (stored in cookie jar automatically)
    await instance.get(`${host}auth/get-csrf-token/`);
    debugLog("[AUTH] CSRF token requested");

    // Step 2: Extract CSRF token from cookie jar for the request header
    const jar = (instance.defaults as any).jar as CookieJar;
    const cookies = await jar.getCookies(host);
    debugLog(`[AUTH] Cookies after CSRF request: ${cookies.map(c => `${c.key}=${c.value.substring(0, 10)}...`).join(", ")}`);

    const csrfCookie = cookies.find((c) => c.key === "csrftoken");

    if (!csrfCookie) {
      debugLog("[AUTH] CSRF token not found in cookies");
      return false;
    }

    // Step 3: Login with email, password, and CSRF token
    // Send as form data (application/x-www-form-urlencoded) not JSON
    const formData = new URLSearchParams();
    formData.append('email', email);
    formData.append('password', password);

    const loginResponse = await instance.post(
      `${host}auth/sign-in/`,
      formData.toString(),
      {
        headers: {
          "X-CSRFToken": csrfCookie.value,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        maxRedirects: 0, // Don't follow redirects, we just need the cookies
        validateStatus: (status) => status >= 200 && status < 400, // Accept redirects as success
      }
    );

    // Log response details
    debugLog(`[AUTH] Login response status: ${loginResponse.status}`);
    debugLog(`[AUTH] Login response headers: ${JSON.stringify(loginResponse.headers)}`);

    // Check if Set-Cookie headers are present
    const setCookieHeader = loginResponse.headers['set-cookie'];
    if (setCookieHeader) {
      debugLog(`[AUTH] Set-Cookie headers received: ${JSON.stringify(setCookieHeader)}`);
    } else {
      debugLog(`[AUTH] WARNING: No Set-Cookie headers in login response!`);
    }

    // Check cookies after login
    const loginCookies = await jar.getCookies(host);
    debugLog(`[AUTH] Cookies after login: ${loginCookies.map(c => `${c.key}=${c.value.substring(0, 10)}...`).join(", ")}`);
    debugLog(`[AUTH] Total cookies stored: ${loginCookies.length}`);

    // Log full cookie details for debugging
    loginCookies.forEach(c => {
      debugLog(`[AUTH] Cookie detail - ${c.key}: domain=${c.domain}, path=${c.path}, httpOnly=${c.httpOnly}, secure=${c.secure}`);
    });

    isAuthenticated = true;
    debugLog("[AUTH] Authentication successful");
    return true;
  } catch (error) {
    debugLog(`[AUTH] Authentication failed: ${error}`);
    return false;
  }
}

export function isSessionAuthenticated(): boolean {
  debugLog(`[AUTH] isSessionAuthenticated() called - returning: ${isAuthenticated}`);
  return isAuthenticated;
}

export function resetAuthentication(): void {
  axiosInstance = null;
  isAuthenticated = false;
}
