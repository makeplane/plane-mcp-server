import fs from "fs/promises";
import os from "os";
import path from "path";

const logFile = path.join(os.tmpdir(), "plane-mcp-debug.log");
const DEBUG_ENABLED = process.env.PLANE_MCP_DEBUG === 'true' || process.env.PLANE_MCP_DEBUG === 'verbose';

export async function debugLog(message: string): Promise<void> {
  if (!DEBUG_ENABLED) return;

  const timestamp = new Date().toISOString();
  const logMessage = `[${timestamp}] ${message}\n`;

  try {
    await fs.appendFile(logFile, logMessage);
    console.error(message);
  } catch (error) {
    console.error(`[DEBUG] Log write failed: ${error}`);
  }
}
