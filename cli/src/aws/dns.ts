import { Resolver } from "node:dns/promises";

function normalize(names: string[]): string[] {
  return names.map((n) => n.toLowerCase().replace(/\.$/, "")).sort();
}

export function sameNameServers(a: string[], b: string[]): boolean {
  return a.length > 0 && normalize(a).join() === normalize(b).join();
}

// The domain's NS records as public DNS sees them
export async function publicNameServers(domain: string): Promise<string[]> {
  const resolver = new Resolver();
  resolver.setServers(["1.1.1.1", "8.8.8.8"]);
  try {
    return await resolver.resolveNs(domain);
  } catch {
    return [];
  }
}