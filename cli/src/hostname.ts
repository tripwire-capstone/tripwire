// Same rule as the Terraform `domain` variable in infra/persistent/variables.tf
const DOMAIN = /^([a-z0-9-]+\.)+[a-z]{2,}$/;

// The subdomain offered by default, e.g. laminar.example.com for example.com
const DEFAULT_SUBDOMAIN = "laminar";

export function normalizeHostname(input: string): string {
  return input.trim().toLowerCase().replace(/\.$/, "");
}

export function suggestHostname(zoneName: string): string {
  return `${DEFAULT_SUBDOMAIN}.${normalizeHostname(zoneName)}`;
}

// Whether hostname is the zone itself or a name under it
export function isInZone(hostname: string, zoneName: string): boolean {
  const host = normalizeHostname(hostname);
  const zone = normalizeHostname(zoneName);
  return host === zone || host.endsWith(`.${zone}`);
}

// What's wrong with the answer, or undefined if it's fine (the shape clack's validate expects)
export function hostnameProblem(input: string | undefined, zoneName?: string): string | undefined {
  const hostname = normalizeHostname(input ?? "");
  const example = zoneName ? suggestHostname(zoneName) : "laminar.example.com";

  if (hostname === "") return `Enter an address, e.g. ${example}`;
  if (hostname.includes("://")) return `Leave out https:// and enter just the address, e.g. ${example}`;
  if (hostname.includes("/")) return `Enter only the address, without a path, e.g. ${example}`;
  if (!DOMAIN.test(hostname)) return `Use a host name like ${example}`;
  if (zoneName && !isInZone(hostname, zoneName)) {
    return `The address must end in ${normalizeHostname(zoneName)}, e.g. ${example}`;
  }
  return undefined;
}
