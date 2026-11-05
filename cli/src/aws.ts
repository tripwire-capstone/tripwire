import { DEPLOYMENT_NAME } from "./paths.js";
import { CommandError, run, type Log } from "./run.js";

export interface Zone {
  id: string;
  name: string;
}

interface Tag {
  Key: string;
  Value: string;
}

// null if the plan couldn't be checked
export async function getFreePlan(log: Log): Promise<boolean | null> {
  try {
    const out = await run(log, "aws", [
      "freetier", "get-account-plan-state",
      "--region", "us-east-1", "--output", "json",
    ]);
    return (JSON.parse(out) as { accountPlanType: string }).accountPlanType === "FREE";
  } catch (err) {
    if (err instanceof CommandError) return null;
    throw err;
  }
}

// A name people recognise the account by: its name, else its alias; null if neither
export async function getAccountLabel(log: Log): Promise<string | null> {
  try {
    const out = await run(log, "aws", ["account", "get-account-information", "--output", "json"]);
    const { AccountName } = JSON.parse(out) as { AccountName?: string };
    if (AccountName) return AccountName;
  } catch (err) {
    // Often not allowed for limited logins; try the alias instead
    if (!(err instanceof CommandError)) throw err;
  }

  try {
    const out = await run(log, "aws", ["iam", "list-account-aliases", "--output", "json"]);
    const { AccountAliases } = JSON.parse(out) as { AccountAliases?: string[] };
    return AccountAliases?.[0] ?? null;
  } catch (err) {
    if (err instanceof CommandError) return null;
    throw err;
  }
}

// e.g. "AWS account 123456789012 (Acme), as user admin"
export function formatAccount(accountId: string, label: string | null, identity: string): string {
  const named = label ? ` (${label})` : "";
  return `AWS account ${accountId}${named}, as ${identity}`;
}

export async function listRegions(log: Log): Promise<string[]> {
  const out = await run(log, "aws", [
    "ec2", "describe-regions", "--region", "us-east-1",
    "--query", "Regions[].RegionName", "--output", "json",
  ]);
  return JSON.parse(out) as string[];
}

// Zones that are the domain or a parent of it, closest first
export function matchingZones(zones: Zone[], domain: string): Zone[] {
  return zones
    .filter((z) => domain === z.name || domain.endsWith(`.${z.name}`))
    .sort((a, b) => b.name.length - a.name.length);
}

// A zone the persistent stack created
export function isOwnZone(tags: Tag[]): boolean {
  const has = (key: string, value: string) =>
    tags.some((t) => t.Key === key && t.Value === value);
  return has("Project", DEPLOYMENT_NAME) && has("Stack", "persistent");
}

// Public hosted zones in the account (private ones aren't reachable from the internet)
export async function listPublicZones(log: Log): Promise<Zone[]> {
  const out = await run(log, "aws", ["route53", "list-hosted-zones", "--output", "json"]);
  const { HostedZones } = JSON.parse(out) as {
    HostedZones: { Id: string; Name: string; Config: { PrivateZone: boolean } }[];
  };

  return HostedZones.filter((z) => !z.Config.PrivateZone).map((z) => ({
    id: z.Id.replace("/hostedzone/", ""),
    name: z.Name.replace(/\.$/, ""),
  }));
}

// IDs of the zones the persistent stack created (tags are read 10 zones per call, the API's limit)
export async function ownZoneIds(log: Log, zoneIds: string[]): Promise<Set<string>> {
  const own = new Set<string>();
  for (let start = 0; start < zoneIds.length; start += 10) {
    const out = await run(log, "aws", [
      "route53", "list-tags-for-resources", "--resource-type", "hostedzone",
      "--resource-ids", ...zoneIds.slice(start, start + 10), "--output", "json",
    ]);
    const { ResourceTagSets } = JSON.parse(out) as {
      ResourceTagSets: { ResourceId: string; Tags: Tag[] }[];
    };
    for (const set of ResourceTagSets) {
      if (isOwnZone(set.Tags)) own.add(set.ResourceId);
    }
  }
  return own;
}

// Public zones a user can deploy into: everything except the zones Tripwire created
export async function listUserZones(log: Log): Promise<Zone[]> {
  const zones = await listPublicZones(log);
  const own = await ownZoneIds(log, zones.map((z) => z.id));
  return zones.filter((z) => !own.has(z.id)).sort((a, b) => a.name.localeCompare(b.name));
}

// Closest public zone for the domain; null if none or ours
export async function findExistingZone(log: Log, domain: string): Promise<Zone | null> {
  const closest = matchingZones(await listPublicZones(log), domain)[0];
  if (!closest) return null;
  const own = await ownZoneIds(log, [closest.id]);
  return own.has(closest.id) ? null : closest;
}

// The name servers Route 53 gave the zone
export async function zoneNameServers(log: Log, zoneId: string): Promise<string[]> {
  const out = await run(log, "aws", ["route53", "get-hosted-zone", "--id", zoneId, "--output", "json"]);
  const { DelegationSet } = JSON.parse(out) as { DelegationSet?: { NameServers?: string[] } };
  return DelegationSet?.NameServers ?? [];
}

// Record types that would clash with Laminar's A record at exactly this name
const CLASHING_TYPES = ["A", "AAAA", "CNAME"];

export async function recordTypesAt(log: Log, zoneId: string, hostname: string): Promise<string[]> {
  const out = await run(log, "aws", [
    "route53", "list-resource-record-sets", "--hosted-zone-id", zoneId,
    "--query", `ResourceRecordSets[?Name=='${hostname}.'].Type`, "--output", "json",
  ]);
  return (JSON.parse(out) as string[]).filter((type) => CLASHING_TYPES.includes(type));
}
