#!/usr/bin/env node
import * as p from "@clack/prompts";
import { Command } from "commander";
import { deploy } from "./commands/deploy.js";
import { destroy } from "./commands/destroy.js";
import { init } from "./commands/init.js";
import { addUser, listUsers, removeUser } from "./commands/users.js";
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
  .option("-y, --yes", "skip typing delete to confirm")
  .action(destroy);

const users = program
  .command("users")
  .description("List, add or remove who can sign in");

users
  .command("list")
  .description("Show who can sign in")
  .action(listUsers);

users
  .command("add <email>")
  .description("Let someone sign in; AWS emails them a temporary password")
  .action(addUser);

users
  .command("remove <email>")
  .description("Stop someone from signing in")
  .option("-y, --yes", "skip the confirmation question")
  .action(removeUser);

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