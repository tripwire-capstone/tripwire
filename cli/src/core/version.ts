import { readFileSync } from "node:fs";

const pkg = JSON.parse(
  // cli/package.json: two levels up from src/core/ (or dist/core/ once built)
  readFileSync(new URL("../../package.json", import.meta.url), "utf8"),
) as { version: string };

export const CLI_VERSION = pkg.version;