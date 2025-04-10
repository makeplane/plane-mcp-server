import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import { registerCycleIssueTools } from "./cycle-issues.js";
import { registerCycleTools } from "./cycles.js";
import { registerIssueTools } from "./issues.js";
import { registerMetadataTools } from "./metadata.js";
import { registerModuleIssueTools } from "./module-issues.js";
import { registerModuleTools } from "./modules.js";
import { registerProjectTools } from "./projects.js";
import { registerUserTools } from "./user.js";
import { registerWorkLogTools } from "./work-log.js";

export const registerTools = (server: McpServer) => {
  registerMetadataTools(server);
  registerUserTools(server);

  registerProjectTools(server);
  registerModuleTools(server);
  registerModuleIssueTools(server);
  registerIssueTools(server);
  registerCycleTools(server);
  registerCycleIssueTools(server);

  registerWorkLogTools(server);
};
