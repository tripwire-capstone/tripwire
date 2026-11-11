// Terraform's machine-readable output (`terraform apply -json`), one JSON object per line.
// Ported from the nestor-CLI prototype (tripwire-infra, cli/src/terraform/events.ts).

import type { RawEvent, ResourceAction, ResourceHook, TerraformEvent } from "../types/terraform.js";

const RESOURCE_ACTIONS: ResourceAction[] = ["create", "update", "delete", "replace", "read", "noop"];

function asNumber(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asAction(value: unknown): ResourceAction | undefined {
  return RESOURCE_ACTIONS.includes(value as ResourceAction) ? (value as ResourceAction) : undefined;
}

// The resource a hook event is about, or undefined if the event is malformed
function hookOf(raw: RawEvent): ResourceHook | undefined {
  const address = raw.hook?.resource?.addr;
  const action = asAction(raw.hook?.action);
  if (typeof address !== "string" || !action) return undefined;
  // resource_type is given; the address's first part is a fallback (aws_vpc.main → aws_vpc)
  const resourceType = asText(raw.hook?.resource?.resource_type) || address.split(".")[0];
  return { address, resourceType, action };
}

// One output line → an event, or undefined if it isn't Terraform JSON (e.g. a plain-text warning)
export function parseTerraformEvent(line: string): TerraformEvent | undefined {
  let raw: RawEvent;
  try {
    raw = JSON.parse(line) as RawEvent;
  } catch {
    return undefined;
  }
  if (typeof raw !== "object" || raw === null) return undefined;
  const message = asText(raw["@message"]);

  if (
    raw.type === "apply_start" ||
    raw.type === "apply_progress" ||
    raw.type === "apply_complete" ||
    raw.type === "apply_errored"
  ) {
    const hook = hookOf(raw);
    if (!hook) return { type: "other", message };
    const elapsedSeconds = asNumber(raw.hook?.elapsed_seconds);
    switch (raw.type) {
      case "apply_start":
        return { type: "apply_start", message, ...hook };
      case "apply_progress":
        return { type: "apply_progress", message, elapsedSeconds, ...hook };
      case "apply_complete":
        return { type: "apply_complete", message, elapsedSeconds, ...hook };
      case "apply_errored":
        return { type: "apply_errored", message, ...hook };
    }
  }

  if (raw.type === "diagnostic" && raw.diagnostic) {
    const address = asText(raw.diagnostic.address);
    return {
      type: "diagnostic",
      message,
      severity: raw.diagnostic.severity === "error" ? "error" : "warning",
      summary: asText(raw.diagnostic.summary),
      detail: asText(raw.diagnostic.detail),
      ...(address ? { address } : {}),
    };
  }

  if (raw.type === "change_summary" && raw.changes) {
    return {
      type: "change_summary",
      message,
      add: asNumber(raw.changes.add),
      change: asNumber(raw.changes.change),
      remove: asNumber(raw.changes.remove),
    };
  }

  return { type: "other", message };
}

// Seconds as the CLI shows durations elsewhere: "45s", "1m 40s"
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  return minutes === 0 ? `${seconds}s` : `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}
