import { join } from "node:path";
import * as p from "@clack/prompts";
import { findExistingZone, getFreePlan, listRegions } from "../aws.js";
import { adoptStateBucket } from "../bootstrap.js";
import { checkTerraform, getAccountId } from "../checks.js";
import { configPath, readConfig, writeConfig, type TripwireConfig } from "../config.js";
import { UserError } from "../errors.js";
import { syncInfra } from "../infra.js";
import { bundledInfraDir, deploymentDir } from "../paths.js";
import { Log } from "../run.js";
import { answer, step } from "../ui.js";

const REGIONS = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1"];
const DOMAIN = /^([a-z0-9-]+\.)+[a-z]{2,}$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function splitEmails(text: string): string[] {
  return text.split(",").map((e) => e.trim()).filter(Boolean);
}

async function askRegion(log: Log, current?: string): Promise<string> {
  const listed = !current || REGIONS.includes(current);
  const choice = answer(
    await p.select({
      message: "AWS region",
      initialValue: listed ? (current ?? "us-east-1") : "other",
      options: [
        ...REGIONS.map((r) => ({ value: r, label: r })),
        { value: "other", label: "Other…" },
      ],
    }),
  );
  if (choice !== "other") return choice;

  const regions = await step("Loading regions", () => listRegions(log));
  return answer(
    await p.text({
      message: "Region code",
      initialValue: current,
      validate: (v) => (v && regions.includes(v) ? undefined : "Not an AWS region"),
    }),
  );
}

async function askZone(log: Log, domain: string): Promise<string | null> {
  const zone = await step("Looking for your domain in Route 53", () =>
    findExistingZone(log, domain),
  );
  if (!zone) return null;

  const use = answer(
    await p.confirm({ message: `Use your Route 53 zone ${zone.name}? No NS records needed.` }),
  );
  return use ? zone.id : null;
}

export async function init(): Promise<void> {
  p.intro("tripwire init");
  const log = new Log("init");
  const saved = await readConfig();

  const [tfVersion, accountId] = await step(
    "Checking Terraform and your AWS login",
    async () => [await checkTerraform(log), await getAccountId(log)],
    ([version, account]) => `Terraform ${version}, AWS account ${account}`,
  );

  if (saved && saved.accountId !== accountId) {
    throw new UserError(
      `This folder is for AWS account ${saved.accountId}, ` +
        `but you're logged into ${accountId}.`,
    );
  }

  const proceed = answer(
    await p.confirm({ message: `Set up Tripwire in AWS account ${accountId}?` }),
  );
  if (!proceed) {
    p.cancel("Nothing changed.");
    return;
  }

  let region: string;
  let domain: string;
  let zoneId: string | null;

  if (saved?.lastDeploy) {
    ({ region, domain, zoneId } = saved);
    p.log.info(`Region ${region} and domain ${domain} are fixed after the first deploy.`);
  } else {
    region = await askRegion(log, saved?.region);
    domain = answer(
      await p.text({
        message: "Domain for Laminar",
        placeholder: "laminar.example.com",
        initialValue: saved?.domain,
        validate: (v) => (v && DOMAIN.test(v) ? undefined : "Use a host name like laminar.example.com"),
      }),
    );
    zoneId = await askZone(log, domain);
  }

  const alertEmail = answer(
    await p.text({
      message: "Email for alerts",
      initialValue: saved?.alertEmail,
      validate: (v) => (v && EMAIL.test(v) ? undefined : "Enter an email address"),
    }),
  );

  let laminarUsers: string[];
  if (saved) {
    laminarUsers = saved.laminarUsers;
    p.log.info("Logins unchanged. Use `tripwire users` to change them.");
  } else {
    const extra = answer(
      await p.text({
        message: `Other emails that can log in (${alertEmail} can already)`,
        placeholder: "Comma-separated, or leave empty",
        validate: (v) =>
          splitEmails(v ?? "").every((e) => EMAIL.test(e)) ? undefined : "Use emails separated by commas",
      }),
    );
    laminarUsers = [...new Set([alertEmail, ...splitEmails(extra ?? "")])];
  }

  let freePlan = await step("Checking your AWS plan", () => getFreePlan(log), (free) =>
    free === null ? "Couldn't check your AWS plan" : free ? "Free plan" : "Paid plan",
  );
  if (freePlan === null) {
    freePlan = answer(
      await p.confirm({ message: "Is this a free-plan AWS account?", initialValue: false }),
    );
  }
  if (freePlan) {
    p.log.info("Free plan: backups kept 1 day, data box is c7i-flex.large.");
  }

  const config: TripwireConfig = {
    accountId,
    region,
    domain,
    zoneId,
    alertEmail,
    laminarUsers,
    freePlan,
    lastDeploy: saved?.lastDeploy,
  };
  await writeConfig(config);

  await syncInfra(bundledInfraDir(), deploymentDir());
  await step(
    "Looking for an existing deployment",
    () => adoptStateBucket(log, join(deploymentDir(), "bootstrap"), config),
    (imported) =>
      imported === null
        ? "No existing deployment, deploy will create one"
        : imported.length > 0
          ? "Found an existing deployment and took over its state bucket"
          : "State bucket already managed here",
  );

  p.outro(`Saved ${configPath()}\nNext: tripwire deploy --lite`);
}