#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { Command } from "commander";

// Read our own version from package.json. The path works from both src/ (tsx) and dist/ (built),
// since each is one folder below cli/.
const pkg = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
) as { version: string };

const program = new Command();

program
  .name("tripwire")
  .description("Deploy Tripwire and self-hosted Laminar to your own AWS account")
  .version(pkg.version);

program
  .command("init")
  .description("Check your tools and AWS login, then ask the setup questions")
  .action(() => {
    console.log("init: not built yet");
  });

program
  .command("deploy")
  .description("Create or update the deployment")
  .option("--lite", "deploy in Lite mode")
  .option("--fresh", "start with empty data instead of restoring from snapshots")
  .option("-y, --yes", "skip the confirmation question")
  .action((options) => {
    console.log("deploy: not built yet", options);
  });

program
  .command("destroy")
  .description("Remove the stack, keeping one set of final snapshots")
  .option("-y, --yes", "skip typing the name to confirm")
  .action((options) => {
    console.log("destroy: not built yet", options);
  });

await program.parseAsync();