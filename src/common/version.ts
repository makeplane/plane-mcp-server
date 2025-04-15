import pkg from "../../package.json" with { type: "json" };

export function getVersion() {
  return pkg.version;
}
