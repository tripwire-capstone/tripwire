import * as p from "@clack/prompts";
import { bucketExists } from "../bootstrap.js";
import { checkLogin } from "../checks.js";
import { readConfig, writeConfig, type TripwireConfig } from "../config.js";
import { UserError } from "../errors.js";
import { syncInfra } from "../infra.js";
import { bundledInfraDir, deploymentDir } from "../paths.js";
import { Log, run } from "../run.js";
import { discardPlan, findResource, tfApply, tfInit, tfPlan, tfState, writeSettings } from "../terraform.js";
import { stateBucketName } from "../tfvars.js";
import { answer, step } from "../ui.js";
import { hasUser, normalizeEmail, withoutUser, withUser } from "../users.js";

// Only the Cognito users change; nothing else in the stack is planned
const USERS_TARGET = "-target=aws_cognito_user.laminar";
const POOL = "aws_cognito_user_pool.main";

export interface RemoveOptions {
  yes?: boolean;
}

async function loadConfig(): Promise<TripwireConfig> {
  const config = await readConfig();
  if (!config) throw new UserError("Run `tripwire init` first.");
  return config;
}

// Checks the login, then returns the user pool ID, or null if the stack isn't deployed
async function prepare(log: Log, config: TripwireConfig): Promise<string | null> {
  await step(
    "Checking Terraform and your AWS login",
    () => checkLogin(log, config),
    () => `AWS account ${config.accountId}`,
  );
  return step("Checking the deployment", async () => {
    await syncInfra(bundledInfraDir(), deploymentDir());
    await writeSettings(config);
    if (!(await bucketExists(log, stateBucketName(config.accountId, config.region)))) return null;
    await tfInit(log, "stack");
    const pool = findResource(await tfState(log, "stack"), POOL);
    return pool ? String(pool.values.id) : null;
  });
}

// Applies the new user list to Cognito, then saves it
async function applyUsers(log: Log, config: TripwireConfig, message: string): Promise<void> {
  await step(message, async () => {
    await writeSettings(config);
    if (await tfPlan(log, "stack", [USERS_TARGET])) await tfApply(log, "stack");
    else await discardPlan("stack");
  });
  await writeConfig(config);
}

async function userStatus(log: Log, config: TripwireConfig, poolId: string, email: string): Promise<string> {
  return (await run(log, "aws", [
    "cognito-idp", "admin-get-user", "--user-pool-id", poolId, "--username", email,
    "--region", config.region, "--query", "UserStatus", "--output", "text",
  ])).trim();
}

export async function listUsers(): Promise<void> {
  const config = await loadConfig();
  p.intro("tripwire users");
  p.note(config.laminarUsers.join("\n"), "Can sign in");
  p.outro("Add or remove with `tripwire users add <email>` / `tripwire users remove <email>`.");
}

export async function addUser(rawEmail: string): Promise<void> {
  const email = normalizeEmail(rawEmail);
  const config = await loadConfig();
  p.intro("tripwire users add");
  const log = new Log("users-add");
  const poolId = await prepare(log, config);

  if (hasUser(config.laminarUsers, email)) {
    if (!poolId) {
      p.outro(`${email} is already a user.`);
      return;
    }
    // A temporary password expires after 7 days; resending gives a new one
    if ((await userStatus(log, config, poolId, email)) !== "FORCE_CHANGE_PASSWORD") {
      p.outro(`${email} is already a user and has signed in. A lost password can be reset from the login page.`);
      return;
    }
    const resend = answer(await p.confirm({
      message: `${email} is already a user but hasn't signed in yet. Email a new temporary password?`,
    }));
    if (!resend) {
      p.outro("Nothing changed.");
      return;
    }
    await step("Sending a new temporary password", () => run(log, "aws", [
      "cognito-idp", "admin-create-user", "--user-pool-id", poolId, "--username", email,
      "--message-action", "RESEND", "--region", config.region,
      "--query", "User.Username", "--output", "text",
    ]));
    p.outro(`Sent. ${email} should get an email from AWS shortly.`);
    return;
  }

  const updated = { ...config, laminarUsers: withUser(config.laminarUsers, email) };
  if (!poolId) {
    await writeConfig(updated);
    p.outro(`Saved. ${email} gets access on your next \`tripwire deploy --lite\`.`);
    return;
  }
  await applyUsers(log, updated, `Adding ${email}`);
  p.outro(`Added. ${email} should get an email from AWS with a temporary password.`);
}

export async function removeUser(rawEmail: string, options: RemoveOptions): Promise<void> {
  const email = normalizeEmail(rawEmail);
  const config = await loadConfig();
  const remaining = withoutUser(config.laminarUsers, email);
  p.intro("tripwire users remove");
  const log = new Log("users-remove");
  const poolId = await prepare(log, config);

  const updated = { ...config, laminarUsers: remaining };
  if (!poolId) {
    await writeConfig(updated);
    p.outro(`Saved. ${email} won't be created on your next deploy.`);
    return;
  }

  if (!options.yes) {
    const sure = answer(await p.confirm({
      message: `Remove ${email}? They can't sign in again; a session already open lasts until it expires.`,
      initialValue: false,
    }));
    if (!sure) {
      p.outro("Nothing changed.");
      return;
    }
  }
  await applyUsers(log, updated, `Removing ${email}`);
  p.outro(`Removed ${email}. Their Laminar account and data are kept.`);
}