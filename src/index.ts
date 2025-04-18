#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { getVersion } from "./common/version.js";
import { registerTools } from "./tools/index.js";

async function main() {
  const version = getVersion();

  const server = new McpServer({
    name: "plane-mcp-server",
    version,
    capabilities: {},
  });

  registerTools(server);

  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(`Plane MCP Server running on stdio: ${version}`);
}

main().catch((error) => {
  console.error("Fatal error in main():", error);
  process.exit(1);
});
