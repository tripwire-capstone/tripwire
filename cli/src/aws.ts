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

// Closest public zone for the domain; null if none or ours
export async function findExistingZone(log: Log, domain: string): Promise<Zone | null> {
  const out = await run(log, "aws", ["route53", "list-hosted-zones", "--output", "json"]);
  const { HostedZones } = JSON.parse(out) as {
    HostedZones: { Id: string; Name: string; Config: { PrivateZone: boolean } }[];
  };

  const zones = HostedZones.filter((z) => !z.Config.PrivateZone).map((z) => ({
    id: z.Id.replace("/hostedzone/", ""),
    name: z.Name.replace(/\.$/, ""),
  }));

  const closest = matchingZones(zones, domain)[0];
  if (!closest) return null;

  const tagsOut = await run(log, "aws", [
    "route53", "list-tags-for-resource", "--resource-type", "hostedzone",
    "--resource-id", closest.id, "--output", "json",
  ]);
  const { ResourceTagSet } = JSON.parse(tagsOut) as { ResourceTagSet: { Tags: Tag[] } };
  return isOwnZone(ResourceTagSet.Tags) ? null : closest;
}