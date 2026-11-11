import type { LiveApplyOptions } from "./progress.js";
import type { Restore } from "./terraform.js";

export interface DeployOptions {
  lite?: boolean;
  fresh?: boolean;
  yes?: boolean;
}

export interface StepLabels {
  // Shown while planning and applying, and with its time when done
  title: string;
  // Shown when nothing changes; null removes the line
  unchanged: string | null;
  // Show the apply's groups live instead of a single spinner
  live?: Omit<LiveApplyOptions, "title" | "hint">;
}

export interface DataPlan {
  stackEmpty: boolean;
  restore?: Restore;
  summary: string;
}

export interface DestroyOptions {
  yes?: boolean;
}

export type DnsProvider = "route53" | "cloudflare" | "other";

export interface DomainChoice {
  domain: string;
  zoneId: string | null;
}

export interface RemoveOptions {
  yes?: boolean;
}
