import axios, { AxiosInstance } from "axios";
import { wrapper } from "axios-cookiejar-support";
import { CookieJar } from "tough-cookie";
import fs from "fs";
import os from "os";
import path from "path";

const logFile = path.join(os.tmpdir(), "plane-mcp-debug.log");
function debugLog(message: string) {
  const timestamp = new Date().toISOString();
  const logMessage = `[${timestamp}] ${message}\n`;
  try {
    fs.appendFileSync(logFile, logMessage);
  } catch (error) {
    console.error(`[AUTH] debugLog write failed: ${error}`);
  }
  console.error(message);
}

/**
 * Result of an authentication attempt
 * @property success - Whether authentication was successful
 * @property error - Type of error if authentication failed
 * @property message - Detailed error message if authentication failed
 */
export interface AuthResult {
  success: boolean;
  error?: 'network' | 'csrf' | 'credentials' | 'cookies' | 'unknown';
  message?: string;
}

let axiosInstance: AxiosInstance | null = null;
let isAuthenticated = false;

debugLog(`[AUTH] Module loaded - PID: ${process.pid}`);

/**
 * Gets or creates an Axios instance with cookie jar support for session authentication
 * @returns Configured Axios instance with cookie persistence
 */
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

/**
 * Authenticates with Plane using email and password, establishing a session with cookies
 *
 * This function performs a two-step authentication flow:
 * 1. Requests a CSRF token from the server
 * 2. Submits credentials with CSRF token to establish session
 *
 * Session cookies are automatically stored in the axios instance's cookie jar
 * and will be included in subsequent requests to /api/ endpoints.
 *
 * @param email - User's Plane account email address
 * @param password - User's Plane account password
 * @param hostUrl - Plane server URL (e.g., "https://api.plane.so/" or self-hosted URL)
 * @returns Authentication result with success status and error details if failed
 * @throws Never throws - all errors are captured in AuthResult
 */
export async function authenticateWithPassword(
  email: string,
  password: string,
  hostUrl: string
): Promise<AuthResult> {
  try {
    const instance = getAxiosInstance();
    const host = hostUrl.endsWith("/") ? hostUrl : `${hostUrl}/`;

    debugLog("[AUTH] Starting authentication flow...");
    debugLog(`[AUTH] Host URL: ${host}`);

    // Step 1: Get CSRF token (stored in cookie jar automatically)
    await instance.get(`${host}auth/get-csrf-token/`);
    debugLog("[AUTH] CSRF token requested");

    // Step 2: Extract CSRF token from cookie jar for the request header
    const maybeJar = (instance.defaults as Record<string, unknown>).jar;
    if (!(maybeJar instanceof CookieJar)) {
      debugLog("[AUTH] ERROR: Cookie jar not found on axios instance");
      return { success: false, error: "cookies", message: "Cookie jar not available for session authentication" };
    }
    const jar = maybeJar;
    const cookies = await jar.getCookies(host);
    debugLog(`[AUTH] Cookies after CSRF request: ${cookies.map(c => c.key).join(", ")}`);

    const csrfCookie = cookies.find((c) => c.key === "csrftoken");

    if (!csrfCookie) {
      debugLog("[AUTH] CSRF token not found in cookies");
      return { success: false, error: 'csrf', message: 'CSRF token not found in response' };
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
      debugLog(`[AUTH] ERROR: No Set-Cookie headers in login response!`);
      return { success: false, error: 'cookies', message: 'No session cookies received from server' };
    }

    // Verify cookies were stored in the jar
    const loginCookies = await jar.getCookies(host);
    debugLog(`[AUTH] Cookies after login: ${loginCookies.map(c => c.key).join(", ")}`);
    debugLog(`[AUTH] Total cookies stored: ${loginCookies.length}`);

    // Validate that session cookie was received
    const sessionCookie = loginCookies.find((c) => c.key === "session-id");
    if (!sessionCookie) {
      debugLog("[AUTH] ERROR: session-id cookie not found after login!");
      return { success: false, error: 'cookies', message: 'session-id cookie not found after login' };
    }

    // Log full cookie details for debugging
    loginCookies.forEach(c => {
      debugLog(`[AUTH] Cookie detail - ${c.key}: domain=${c.domain}, path=${c.path}, httpOnly=${c.httpOnly}, secure=${c.secure}`);
    });

    // Verify the session works with a test API call
    try {
      const verifyResponse = await instance.get(`${host}api/v1/users/me/`);
      if (verifyResponse.status !== 200) {
        debugLog(`[AUTH] Session verification failed with status: ${verifyResponse.status}`);
        return { success: false, error: 'credentials', message: 'Session verification failed' };
      }
      debugLog("[AUTH] Session verified successfully");
    } catch (verifyError) {
      debugLog(`[AUTH] Session verification request failed: ${verifyError}`);
      return { success: false, error: 'credentials', message: 'Could not verify session validity' };
    }

    isAuthenticated = true;
    debugLog("[AUTH] Authentication successful");
    return { success: true };
  } catch (error) {
    debugLog(`[AUTH] Authentication failed: ${error}`);

    if (axios.isAxiosError(error)) {
      if (!error.response) {
        return { success: false, error: 'network', message: 'Network error - could not connect to server' };
      }
      if (error.response.status === 401 || error.response.status === 403) {
        return { success: false, error: 'credentials', message: 'Invalid email or password' };
      }
      return { success: false, error: 'unknown', message: `Server error: ${error.response.status}` };
    }

    return { success: false, error: 'unknown', message: String(error) };
  }
}

/**
 * Checks whether a session is currently authenticated
 * @returns true if authenticated, false otherwise
 */
export function isSessionAuthenticated(): boolean {
  debugLog(`[AUTH] isSessionAuthenticated() called - returning: ${isAuthenticated}`);
  return isAuthenticated;
}

/**
 * Resets the authentication state and clears all session cookies
 *
 * This function:
 * 1. Removes all cookies from the cookie jar
 * 2. Clears the axios instance
 * 3. Resets authentication flag
 *
 * Call this when logging out or when authentication needs to be cleared.
 *
 * @returns Promise that resolves when authentication is reset
 */
export async function resetAuthentication(): Promise<void> {
  try {
    if (axiosInstance) {
      const maybeJar = (axiosInstance.defaults as Record<string, unknown>).jar;
      if (maybeJar instanceof CookieJar) {
        const jar = maybeJar;
        await jar.removeAllCookies();
        debugLog("[AUTH] Cookie jar cleared");
      }
    }
  } catch (error) {
    debugLog(`[AUTH] Error clearing cookies: ${error}`);
    // Continue with cleanup even if cookie removal fails
  } finally {
    axiosInstance = null;
    isAuthenticated = false;
    debugLog("[AUTH] Authentication reset");
  }
}
