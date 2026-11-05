import type { TripwireConfig } from "./config.js";
import { UserError } from "./errors.js";
import { CommandError, ProgramNotFoundError, run, type Log } from "./run.js";

export function isTerraformRecentEnough(version: string): boolean {
  const [major, minor] = version.split(".").map(Number);
  return major > 1 || (major === 1 && minor >= 10);
}

export async function checkTerraform(log: Log): Promise<string> {
  let out: string;
  try {
    out = await run(log, "terraform", ["version", "-json"]);
  } catch (err) {
    if (err instanceof ProgramNotFoundError) {
      throw new UserError("Terraform isn't installed (or isn't on your PATH). Install 1.10 or newer.");
    }
    // It ran and failed: the error already shows Terraform's output and the log
    throw err;
  }

  const { terraform_version } = JSON.parse(out) as { terraform_version: string };
  if (!isTerraformRecentEnough(terraform_version)) {
    throw new UserError(`Terraform ${terraform_version} is too old. Install 1.10 or newer.`);
  }
  return terraform_version;
}

export interface CallerIdentity {
  accountId: string;
  arn: string;
}

export interface IdentityDescription {
  text: string;
  isRoot: boolean;
}

// Who the ARN from get-caller-identity is, in words; the ARN itself if its shape is unknown
export function describeIdentity(arn: string): IdentityDescription {
  const parts = arn.split(":");
  const resource = parts.length >= 6 ? parts.slice(5).join(":") : "";
  const segments = resource.split("/");

  if (resource === "root") return { text: "the root user", isRoot: true };
  if (segments[0] === "user" && segments.length >= 2) {
    return { text: `user ${segments[segments.length - 1]}`, isRoot: false };
  }
  if (segments[0] === "assumed-role" && segments.length >= 2) {
    return { text: `role ${segments[1]}`, isRoot: false };
  }
  if (segments[0] === "federated-user" && segments.length >= 2) {
    return { text: `federated user ${segments[1]}`, isRoot: false };
  }
  return { text: arn, isRoot: false };
}

export async function getAccountId(log: Log): Promise<string> {
  return (await getCallerIdentity(log)).accountId;
}

export async function getCallerIdentity(log: Log): Promise<CallerIdentity> {
  let out: string;
  try {
    out = await run(log, "aws", ["sts", "get-caller-identity", "--output", "json"]);
  } catch (err) {
    if (err instanceof ProgramNotFoundError) {
      throw new UserError("AWS CLI isn't installed (or isn't on your PATH). Install 2.32 or newer.");
    }
    if (err instanceof CommandError) {
      throw new UserError(
        `Couldn't use your AWS login:\n${err.stderr || "(no message from the AWS CLI)"}\n\n` +
          "Log in with `aws login` (add `--profile <name>` if you use a profile, " +
          "and set AWS_PROFILE to it), then run this again.\n" +
          `Log: ${log.path}`,
      );
    }
    throw err;
  }
  const { Account, Arn } = JSON.parse(out) as { Account: string; Arn: string };
  return { accountId: Account, arn: Arn };
}

// Tools are ready and the login is the deployment's account
export async function checkLogin(log: Log, config: TripwireConfig): Promise<void> {
  await checkTerraform(log);
  const accountId = await getAccountId(log);
  if (accountId !== config.accountId) {
    throw new UserError(
      `This deployment is in AWS account ${config.accountId}, ` +
        `but you're logged into ${accountId}.`,
    );
  }
}