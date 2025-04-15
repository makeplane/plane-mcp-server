import { readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export function getVersion() {
  const packageJson = JSON.parse(readFileSync(join(__dirname, "..", "..", "package.json"), "utf8"));
  return packageJson.version;
}
