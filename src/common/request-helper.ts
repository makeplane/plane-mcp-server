import axios, { AxiosRequestConfig } from "axios";

export async function makePlaneRequest<T>(method: string, path: string, body: any = null): Promise<T> {
  const hostUrl = process.env.PLANE_API_HOST_URL || "https://api.plane.so/";
  const host = hostUrl.endsWith("/") ? hostUrl : `${hostUrl}/`;
  const url = `${host}api/v1/${path}`;
  const headers: Record<string, string> = {
    "X-API-Key": process.env.PLANE_API_KEY || "",
  };

  // Only add Content-Type for non-GET requests
  if (method.toUpperCase() !== "GET") {
    headers["Content-Type"] = "application/json";
  }

  try {
    const config: AxiosRequestConfig = {
      url,
      method,
      headers,
    };

    // Only include body for non-GET requests
    if (method.toUpperCase() !== "GET" && body !== null) {
      config.data = body;
    }

    const response = await axios(config);
    return response.data;
  } catch (error) {
    if (axios.isAxiosError(error)) {
      throw new Error(`Request failed: ${error.message}`);
    }
    throw error;
  }
}
