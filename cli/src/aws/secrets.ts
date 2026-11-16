import { DEPLOYMENT_NAME } from "../core/paths.js";
import { run, type Log } from "../core/run.js";
import type { SecretKey } from "../types/tripwire.js";

// Where a key lives in SSM; infra/stack/tripwire.tf builds the same names (tripwire_cli_secret_arn)
export const secretName = (key: SecretKey): string => `/${DEPLOYMENT_NAME}/tripwire/${key}`;

// The last 4 characters, the only part of a key the CLI keeps (in tripwire.json)
export const keyHint = (value: string): string => value.slice(-4);

// Whether the parameter exists; reads its name only, never its value
export const secretExists = async (log: Log, region: string, key: SecretKey): Promise<boolean> => {
  const out = await run(log, "aws", [
    "ssm", "describe-parameters", "--region", region,
    "--parameter-filters", `Key=Name,Values=${secretName(key)}`,
    "--query", "length(Parameters)", "--output", "text",
  ]);
  return out.trim() !== "0";
};

// The request put-parameter reads from stdin, so the value never appears as an argument (arguments
// are logged and visible to other programs on the machine)
export const putParameterInput = (key: SecretKey, value: string): string =>
  JSON.stringify({ Name: secretName(key), Value: value, Type: "SecureString", Overwrite: true });

// Stores the key encrypted (SecureString, AWS's default SSM key), replacing any earlier value
export const putSecret = async (log: Log, region: string, key: SecretKey, value: string): Promise<void> => {
  await run(
    log,
    "aws",
    ["ssm", "put-parameter", "--region", region, "--cli-input-json", "file:///dev/stdin", "--query", "Version", "--output", "text"],
    { input: putParameterInput(key, value), logStdout: false },
  );
};
