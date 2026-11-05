import { UserError } from "./errors.js";
import { CommandError, run, type Log } from "./run.js";

export function isTerraformRecentEnough(version: string): boolean {
  const [major, minor] = version.split(".").map(Number);
  return major > 1 || (major === 1 && minor >= 10);
}

export async function checkTerraform(log: Log): Promise<string> {
  let out: string;
  try {
    out = await run(log, "terraform", ["version", "-json"]);
  } catch (err) {
    if (err instanceof CommandError) {
      throw new UserError("Terraform not found. Install 1.10 or newer.");
    }
    throw err;
  }

  const { terraform_version } = JSON.parse(out) as { terraform_version: string };
  if (!isTerraformRecentEnough(terraform_version)) {
    throw new UserError(`Terraform ${terraform_version} is too old. Install 1.10 or newer.`);
  }
  return terraform_version;
}

export async function getAccountId(log: Log): Promise<string> {
  let out: string;
  try {
    out = await run(log, "aws", ["sts", "get-caller-identity", "--output", "json"]);
  } catch (err) {
    if (err instanceof CommandError) {
      throw new UserError(`AWS CLI missing or not logged in. Run \`aws login\` first.\nLog: ${log.path}`);
    }
    throw err;
  }
  return (JSON.parse(out) as { Account: string }).Account;
}