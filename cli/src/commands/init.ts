import { join } from "node:path";
import * as p from "@clack/prompts";
import {
  findExistingZone,
  formatAccount,
  getAccountLabel,
  getFreePlan,
  listRegions,
  listUserZones,
  recordTypesAt,
  zoneNameServers,
} from "../aws/aws.js";
import { adoptStateBucket } from "../terraform/bootstrap.js";
import { checkTerraform, describeIdentity, getCallerIdentity } from "../aws/checks.js";
import { configPath, readConfig, writeConfig } from "../core/config.js";
import { publicNameServers, sameNameServers } from "../aws/dns.js";
import { UserError } from "../core/errors.js";
import { hostnameProblem, isInZone, normalizeHostname, suggestHostname } from "../aws/hostname.js";
import { syncInfra } from "../terraform/infra.js";
import { bundledInfraDir, deploymentDir } from "../core/paths.js";
import { Log } from "../core/run.js";
import { answer, step } from "../ui/ui.js";
import type { Zone } from "../types/aws.js";
import type { DnsProvider, DomainChoice } from "../types/commands.js";
import type { TripwireConfig } from "../types/config.js";

const REGIONS = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1"];
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

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

const PROVIDER_NAMES: Record<DnsProvider, string> = {
  route53: "Route 53 (this AWS account)",
  cloudflare: "Cloudflare",
  other: "Another DNS provider",
};

// Stops unless public DNS already uses this Route 53 zone; otherwise the certificate never validates
async function checkZoneIsLive(log: Log, zone: Zone): Promise<void> {
  const [expected, actual] = await step(
    `Checking that ${zone.name} uses this Route 53 zone`,
    async () => [await zoneNameServers(log, zone.id), await publicNameServers(zone.name)],
    () => `${zone.name} uses this Route 53 zone`,
  );
  if (sameNameServers(actual, expected)) return;

  throw new UserError(
    `${zone.name} doesn't point at its Route 53 zone yet.\n` +
      `  Public DNS shows: ${actual.length > 0 ? actual.join(", ") : "nothing"}\n` +
      `  It should be:     ${expected.join(", ")}\n` +
      "Set these name servers at your domain's registrar, wait for DNS to update, then run init again.",
  );
}

// The Route 53 zone to use: the only one, or the user's pick; null if the account has none
async function askRoute53Zone(log: Log, savedZoneId: string | null): Promise<Zone | null> {
  const zones = await step("Looking for your domains in Route 53", () => listUserZones(log));
  if (zones.length === 0) return null;
  if (zones.length === 1) {
    p.log.info(`Using your Route 53 domain ${zones[0].name}`);
    return zones[0];
  }
  return answer(
    await p.select({
      message: "Which domain should Laminar use?",
      initialValue: zones.find((z) => z.id === savedZoneId),
      options: zones.map((z) => ({ value: z, label: z.name })),
    }),
  );
}

// An address in the zone that nothing else uses (or that the user says is fine)
async function askHostnameInZone(log: Log, zone: Zone, saved?: string): Promise<string> {
  let initialValue = saved && isInZone(saved, zone.name) ? saved : suggestHostname(zone.name);
  for (;;) {
    const hostname = normalizeHostname(
      answer(
        await p.text({
          message: "Address for Laminar",
          initialValue,
          validate: (v) => hostnameProblem(v, zone.name),
        }),
      ),
    );

    const taken = await step(
      `Checking that ${hostname} is free`,
      () => recordTypesAt(log, zone.id, hostname),
      (types) => (types.length === 0 ? `${hostname} is free` : `${hostname} is in use`),
    );
    if (taken.length === 0) return hostname;

    const useAnyway = answer(
      await p.confirm({
        message: `${hostname} already has DNS records (${taken.join(", ")}). Use it anyway?`,
        initialValue: false,
      }),
    );
    if (useAnyway) return hostname;
    initialValue = hostname;
  }
}

async function askRoute53Domain(log: Log, saved: TripwireConfig | null): Promise<DomainChoice | null> {
  const zone = await askRoute53Zone(log, saved?.zoneId ?? null);
  if (!zone) {
    p.log.warn("This AWS account has no public Route 53 domains. Pick where your DNS is hosted instead.");
    return null;
  }
  await checkZoneIsLive(log, zone);
  return { domain: await askHostnameInZone(log, zone, saved?.domain), zoneId: zone.id };
}

async function askOtherDomain(
  log: Log,
  provider: DnsProvider,
  saved: TripwireConfig | null,
): Promise<DomainChoice> {
  const domain = normalizeHostname(
    answer(
      await p.text({
        message: "Address for Laminar",
        placeholder: "laminar.example.com",
        initialValue: saved?.domain,
        validate: (v) => hostnameProblem(v),
      }),
    ),
  );

  // The domain may be in Route 53 after all
  const zone = await step("Looking for your domain in Route 53", () => findExistingZone(log, domain));
  if (zone) {
    const use = answer(
      await p.confirm({ message: `Use your Route 53 zone ${zone.name}? No NS records needed.` }),
    );
    if (use) {
      await checkZoneIsLive(log, zone);
      return { domain, zoneId: zone.id };
    }
  }

  p.log.info(
    `Tripwire will create a DNS zone for ${domain}. During deploy you'll add 4 NS records ` +
      `for it at ${PROVIDER_NAMES[provider]}; deploy shows them and waits until they work.`,
  );
  return { domain, zoneId: null };
}

// Where the DNS is hosted decides how the domain is chosen
async function askDomain(log: Log, saved: TripwireConfig | null): Promise<DomainChoice> {
  for (;;) {
    const provider = answer(
      await p.select<DnsProvider>({
        message: "Where is your domain's DNS hosted?",
        initialValue: saved?.zoneId ? "route53" : undefined,
        options: (Object.keys(PROVIDER_NAMES) as DnsProvider[]).map((value) => ({
          value,
          label: PROVIDER_NAMES[value],
        })),
      }),
    );
    if (provider !== "route53") return askOtherDomain(log, provider, saved);

    const choice = await askRoute53Domain(log, saved);
    if (choice) return choice;
  }
}

export async function init(): Promise<void> {
  p.intro("tripwire init");
  const log = new Log("init");
  const saved = await readConfig();

  const { accountId, arn, label } = await step(
    "Checking Terraform and your AWS login",
    async () => {
      const version = await checkTerraform(log);
      const identity = await getCallerIdentity(log);
      return { version, ...identity, label: await getAccountLabel(log) };
    },
    ({ version }) => `Terraform ${version}`,
  );

  if (saved && saved.accountId !== accountId) {
    throw new UserError(
      `This folder is for AWS account ${saved.accountId}, ` +
        `but you're logged into ${accountId}.`,
    );
  }

  const identity = describeIdentity(arn);
  p.log.info(formatAccount(accountId, label, identity.text));
  if (identity.isRoot) {
    p.log.warn("You're logged in as the root user. AWS recommends an IAM or SSO login.");
  }

  const proceed = answer(await p.confirm({ message: "Set up Tripwire in this account?" }));
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
    ({ domain, zoneId } = await askDomain(log, saved));
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
    const loginEmail = answer(
      await p.text({
        message: "Email to log in to Laminar",
        initialValue: alertEmail,
        validate: (v) => (v && EMAIL.test(v) ? undefined : "Enter an email address"),
      }),
    );
    laminarUsers = [loginEmail];
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