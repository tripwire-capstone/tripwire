import { DEPLOYMENT_NAME } from "../core/paths.js";
import { run, type Log } from "../core/run.js";

// Before the Settings page, `tripwire deploy` asked for the OpenAI and Laminar keys and stored them
// in SSM under these names, outside Terraform. Keys now live in Tripwire → Settings (encrypted in its
// database), and nothing reads these anymore, so deploy deletes them once.
export const OLD_KEY_NAMES = ["openai_api_key", "laminar_api_key"].map(
  (key) => `/${DEPLOYMENT_NAME}/tripwire/${key}`,
);

// Deletes whichever of the old keys still exist; returns their names (none on later deploys).
// delete-parameters skips names that don't exist instead of failing, so this is safe to rerun.
export const removeOldKeys = async (log: Log, region: string): Promise<string[]> => {
  const out = await run(log, "aws", [
    "ssm", "delete-parameters", "--region", region,
    "--names", ...OLD_KEY_NAMES,
    "--query", "DeletedParameters", "--output", "text",
  ]);
  return out.split(/\s+/).filter((name) => name.length > 0 && name !== "None");
};
