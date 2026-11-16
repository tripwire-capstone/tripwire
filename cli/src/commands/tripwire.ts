// The Tripwire part of `tripwire deploy`: the two keys, the migration task, starting the services.
// Order (deploy.ts): OpenAI key → stacks → Laminar up → Laminar key → migration → services.
import * as p from "@clack/prompts";
import { checkLaminarKey, checkOpenAiKey, projectIdFrom } from "../aws/keys.js";
import { migrationTarget, runMigration } from "../aws/migrate.js";
import { keyHint, putSecret, secretExists } from "../aws/secrets.js";
import { UserError } from "../core/errors.js";
import type { Log } from "../core/run.js";
import type { TripwireConfig, TripwireSettings } from "../types/config.js";
import type { KeyCheck, SecretKey } from "../types/tripwire.js";
import { answer, step } from "../ui/ui.js";

// For --yes runs: the keys can come from the environment instead of a prompt
const ENV = {
  openai_api_key: "TRIPWIRE_OPENAI_API_KEY",
  laminar_api_key: "TRIPWIRE_LAMINAR_API_KEY",
  projectId: "TRIPWIRE_LAMINAR_PROJECT_ID",
} as const;

const required = (value: string | undefined): string | undefined =>
  value && value.trim() ? undefined : "Required";

// Asks (hidden input) until the key passes its check; with --yes, reads it from the environment
const askKey = async (
  key: SecretKey,
  message: string,
  check: (value: string) => Promise<KeyCheck>,
  yes: boolean,
): Promise<string> => {
  const fromEnv = process.env[ENV[key]]?.trim();
  if (fromEnv || yes) {
    if (!fromEnv) throw new UserError(`Set ${ENV[key]} to run with --yes: Tripwire needs it and SSM doesn't have it yet.`);
    const result = await step("Checking the key", () => check(fromEnv));
    if (!result.ok) throw new UserError(result.reason);
    return fromEnv;
  }
  for (;;) {
    const value = answer(await p.password({ message, validate: required })).trim();
    const result = await step("Checking the key", () => check(value));
    if (result.ok) return value;
    p.log.warn(result.reason);
  }
};

const withTripwire = (config: TripwireConfig, settings: TripwireSettings): TripwireConfig => ({
  ...config,
  tripwire: { ...config.tripwire, ...settings },
});

// Before anything is built: the OpenAI key the judge, summarizer and embedder use (stored in SSM)
export const ensureOpenAiKey = async (log: Log, config: TripwireConfig, yes: boolean): Promise<TripwireConfig> => {
  if (await secretExists(log, config.region, "openai_api_key")) return config;
  p.note(
    "Tripwire calls OpenAI itself to judge traces and group events, so it needs its own API key.\n" +
      "It's stored encrypted in your AWS account (SSM) and never saved on this machine.",
    "OpenAI key",
  );
  const value = await askKey("openai_api_key", "OpenAI API key", (candidate) => checkOpenAiKey(candidate), yes);
  await step("Saving the key in AWS", () => putSecret(log, config.region, "openai_api_key", value));
  return withTripwire(config, { openAiKeyHint: keyHint(value) });
};

// Once Laminar is up: a project to watch and its API key. Skipped when SSM already has the key.
export const ensureLaminarKey = async (
  log: Log,
  config: TripwireConfig,
  laminarUrl: string,
  yes: boolean,
): Promise<TripwireConfig> => {
  if (await secretExists(log, config.region, "laminar_api_key")) return config;
  p.note(
    [
      `1. Open ${laminarUrl} and sign in (first time: the temporary password AWS emailed you).`,
      "2. Create a project, or open the one your app sends traces to.",
      "3. In the project's settings, create a project API key.",
      "",
      "Tripwire reads that project's traces with the key. It's stored encrypted in your AWS",
      "account (SSM) and never saved on this machine.",
    ].join("\n"),
    "Connect Tripwire to Laminar",
  );

  let projectId = projectIdFrom(process.env[ENV.projectId] ?? "");
  if (!projectId && yes) throw new UserError(`Set ${ENV.projectId} to run with --yes: Tripwire needs a Laminar project.`);
  while (!projectId) {
    const pasted = answer(await p.text({
      message: "Laminar project URL or ID",
      placeholder: `${laminarUrl}/project/<id>/traces`,
      validate: required,
    }));
    projectId = projectIdFrom(pasted);
    if (!projectId) p.log.warn("That doesn't contain a project ID (a UUID like 42fc1e36-6c0e-4a69-9850-b0e849bb4656).");
  }

  const value = await askKey(
    "laminar_api_key",
    "Laminar project API key",
    (candidate) => checkLaminarKey(laminarUrl, candidate),
    yes,
  );
  await step("Saving the key in AWS", () => putSecret(log, config.region, "laminar_api_key", value));
  return withTripwire(config, { laminarProjectId: projectId, laminarKeyHint: keyHint(value) });
};

// The one-off migration task: creates or updates Tripwire's database before the services change
export const migrateTripwire = async (
  log: Log,
  config: TripwireConfig,
  outputs: Record<string, unknown>,
): Promise<void> => {
  const environment: Record<string, string> = config.tripwire?.laminarProjectId
    ? { LMNR_PROJECT_ID: config.tripwire.laminarProjectId }
    : {};
  await step(
    "Preparing Tripwire's database",
    () => runMigration(log, config.region, migrationTarget(outputs), environment),
    () => "Tripwire's database is ready",
  );
};
