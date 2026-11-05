#!/usr/bin/env node
import * as p from "@clack/prompts";
import { Command } from "commander";
import { deploy } from "./commands/deploy.js";
import { init } from "./commands/init.js";
import { UserError } from "./errors.js";
import { CommandError } from "./run.js";
import { CLI_VERSION } from "./version.js";

const program = new Command();

program
  .name("tripwire")
  .description("Deploy Tripwire and self-hosted Laminar to your own AWS account")
  .version(CLI_VERSION);

program
  .command("init")
  .description("Check your tools and AWS login, then ask the setup questions")
  .action(init);

program
  .command("deploy")
  .description("Create or update the deployment")
  .option("--lite", "deploy in Lite mode")
  .option("--fresh", "start with empty data instead of restoring from snapshots")
  .option("-y, --yes", "skip the confirmation question")
  .action(deploy);

program
  .command("destroy")
  .description("Remove the stack, keeping one set of final snapshots")
  .option("-y, --yes", "skip typing the name to confirm")
  .action((options) => {
    console.log("destroy: not built yet", options);
  });

try {
  await program.parseAsync();
} catch (err) {
  if (err instanceof UserError || err instanceof CommandError) {
    p.cancel(err.message);
    process.exitCode = 1;
  } else {
    throw err;
  }
}