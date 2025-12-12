import axios, { AxiosInstance } from "axios";
import { wrapper } from "axios-cookiejar-support";
import { CookieJar } from "tough-cookie";
import { debugLog } from "./debug.js";

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
let authenticationTime: number | null = null;
const SESSION_TIMEOUT_MS = 3600000; // 1 hour

debugLog(`[AUTH] Module loaded - PID: ${process.pid}`).catch(() => {});

/**
 * Gets or creates an Axios instance with cookie jar support for session authentication
 * @returns Configured Axios instance with cookie persistence
 */
export function getAxiosInstance(): AxiosInstance {
  if (!axiosInstance) {
    debugLog("[AUTH] Creating new axios instance with cookie jar").catch(() => {});
    const jar = new CookieJar();
    axiosInstance = wrapper(axios.create({
      jar,
      withCredentials: true,
      timeout: 30000 // 30 second timeout
    }));
  } else {
    debugLog("[AUTH] Reusing existing axios instance").catch(() => {});
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

    await debugLog("[AUTH] Starting authentication flow...");
    await debugLog(`[AUTH] Host URL: ${host}`);

    // Step 1: Get CSRF token (stored in cookie jar automatically)
    // Use explicit path to ensure we capture path-scoped cookies
    const csrfUrl = `${host}auth/get-csrf-token/`;
    const csrfResponse = await instance.get(csrfUrl);
    await debugLog(`[AUTH] CSRF response status: ${csrfResponse.status}`);
    
    // Only log headers if verbose debug is enabled to avoid leaking config details
    if (process.env.PLANE_MCP_DEBUG === 'verbose') {
      await debugLog(`[AUTH] CSRF response headers: ${JSON.stringify(csrfResponse.headers)}`);
    }
    
    await debugLog("[AUTH] CSRF token requested");

    // Step 2: Extract CSRF token from cookie jar for the request header
    const maybeJar = (instance.defaults as Record<string, unknown>).jar;
    if (!(maybeJar instanceof CookieJar)) {
      await debugLog("[AUTH] ERROR: Cookie jar not found on axios instance");
      return { success: false, error: "cookies", message: "Cookie jar not available for session authentication" };
    }
    const jar = maybeJar;
    // Check cookies on the specific CSRF URL to ensure we get path-scoped cookies
    const cookies = await jar.getCookies(csrfUrl);
    await debugLog(`[AUTH] Cookies after CSRF request: ${cookies.map(c => c.key).join(", ")}`);

    const csrfCookie = cookies.find((c) => ["csrftoken", "csrf", "XSRF-TOKEN"].includes(c.key));

    if (!csrfCookie) {
      await debugLog("[AUTH] CSRF token not found in cookies");
      return { success: false, error: 'csrf', message: 'CSRF token not found in response' };
    }

    // Step 3: Login with email, password, and CSRF token
    // Send as form data (application/x-www-form-urlencoded) not JSON
    const formData = new URLSearchParams();
    formData.append('email', email);
    formData.append('password', password);

    await debugLog(`[AUTH] Sending login request to: ${host}auth/sign-in/`);
    await debugLog(`[AUTH] Login email: ${email}`);
    // Do NOT log password
    await debugLog("[AUTH] CSRF token found");

    const loginResponse = await instance.post(
      `${host}auth/sign-in/`,
      formData.toString(),
      {
        headers: {
          "X-CSRFToken": csrfCookie.value,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        maxRedirects: 0, // Don't follow redirects, we just need the cookies
        validateStatus: (status) => (status >= 200 && status < 300) || status === 302, // Accept 2xx and 302 (redirect) as success
      }
    );

    // Log response details
    await debugLog(`[AUTH] Login response status: ${loginResponse.status}`);
    const headerNames = Object.keys(loginResponse.headers ?? {});
    await debugLog(`[AUTH] Login response headers present: ${headerNames.join(", ")}`);

    // Log ALL headers for debugging ONLY if verbose
    if (process.env.PLANE_MCP_DEBUG === 'verbose') {
      await debugLog(`[AUTH] Login response headers FULL: ${JSON.stringify(loginResponse.headers)}`);
    }

    // Check if Set-Cookie headers are present
    const setCookieHeader = loginResponse.headers['set-cookie'];
    if (setCookieHeader) {
      await debugLog(`[AUTH] Set-Cookie headers received: ${Array.isArray(setCookieHeader) ? setCookieHeader.length : 1} cookie(s)`);
    } else {
      await debugLog(`[AUTH] WARNING: No Set-Cookie headers in login response! Checking cookie jar anyway...`);
    }

    // Verify cookies were stored in the jar
    const loginCookies = await jar.getCookies(host);
    await debugLog(`[AUTH] Cookies after login: ${loginCookies.map(c => c.key).join(", ")}`);
    await debugLog(`[AUTH] Total cookies stored: ${loginCookies.length}`);

    // Validate that session cookie was received
    const sessionCookieNames = ["session-id", "sessionid", "plane_session"];
    const sessionCookie = loginCookies.find((c) => sessionCookieNames.includes(c.key));
    if (!sessionCookie) {
      await debugLog(`[AUTH] WARNING: No standard session cookie found (looked for: ${sessionCookieNames.join(", ")})`);
    }

    // Log full cookie details for debugging - gated
    if (process.env.PLANE_MCP_DEBUG === 'verbose') {
      loginCookies.forEach(c => {
        debugLog(`[AUTH] Cookie detail - ${c.key}: domain=${c.domain}, path=${c.path}, httpOnly=${c.httpOnly}, secure=${c.secure}`).catch(() => {});
      });
    }

    // Verify the session works with a test API call
    try {
      const verifyUrl = `${host}api/users/me/`;
      await debugLog(`[AUTH] Attempting to verify session with: ${verifyUrl}`);
      await debugLog(`[AUTH] Cookies being sent: ${loginCookies.map(c => c.key).join(", ")}`);

      const verifyResponse = await instance.get(verifyUrl);
      await debugLog(`[AUTH] Verification response status: ${verifyResponse.status}`);
      // Log only non-sensitive data if possible, or truncate heavily
      await debugLog(`[AUTH] Verification response data: ${JSON.stringify(verifyResponse.data).substring(0, 50)}...`);

      if (verifyResponse.status !== 200) {
        await debugLog(`[AUTH] Session verification failed with status: ${verifyResponse.status}`);
        return { success: false, error: 'credentials', message: 'Session verification failed' };
      }
      await debugLog("[AUTH] Session verified successfully");
    } catch (verifyError) {
      if (axios.isAxiosError(verifyError)) {
        await debugLog(`[AUTH] Session verification axios error - status: ${verifyError.response?.status}, message: ${verifyError.message}`);
        // Avoid logging full sensitive data in error responses
        await debugLog(`[AUTH] Verification error response status: ${verifyError.response?.status}`);
      }
      await debugLog(`[AUTH] Session verification request failed: ${verifyError}`);
      return { success: false, error: 'credentials', message: 'Could not verify session validity' };
    }

    isAuthenticated = true;
    authenticationTime = Date.now();
    await debugLog("[AUTH] Authentication successful");
    return { success: true };
  } catch (error) {
    // Reset auth state on failure to avoid stale state
    isAuthenticated = false;
    authenticationTime = null;
    
    await debugLog(`[AUTH] Authentication failed: ${error}`);

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
  if (!isAuthenticated || !authenticationTime) {
    debugLog(`[AUTH] isSessionAuthenticated() - not authenticated`).catch(() => {});
    return false;
  }

  const isStale = Date.now() - authenticationTime > SESSION_TIMEOUT_MS;
  if (isStale) {
    debugLog(`[AUTH] Session expired, resetting authentication`).catch(() => {});
    isAuthenticated = false;
    authenticationTime = null;
    return false;
  }
  
  debugLog(`[AUTH] isSessionAuthenticated() called - returning: ${isAuthenticated}`).catch(() => {});
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
        await debugLog("[AUTH] Cookie jar cleared");
      }
    }
  } catch (error) {
    await debugLog(`[AUTH] Error clearing cookies: ${error}`);
    // Continue with cleanup even if cookie removal fails
  } finally {
    axiosInstance = null;
    isAuthenticated = false;
    authenticationTime = null;
    await debugLog("[AUTH] Authentication reset");
  }
}
