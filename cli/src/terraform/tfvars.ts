import { DEPLOYMENT_NAME } from "../core/paths.js";
import type { TripwireConfig } from "../types/config.js";
import type { Restore, StackName, Value } from "../types/terraform.js";

export function stateBucketName(accountId: string, region: string): string {
  return `${DEPLOYMENT_NAME}-tfstate-${accountId}-${region}`;
}

// A Terraform literal; ${ and %{ are escaped so they stay text
export function hclValue(value: Value): string {
  if (Array.isArray(value)) return `[${value.map(hclValue).join(", ")}]`;
  if (typeof value === "number") return String(value);
  return JSON.stringify(value).replace(/[$%]\{/g, (m) => m[0] + m);
}

function render(vars: Record<string, Value>): string {
  const lines = Object.entries(vars).map(([key, value]) => `${key} = ${hclValue(value)}`);
  return `# Written by tripwire; edits are overwritten\n${lines.join("\n")}\n`;
}

export function buildTfvars(
  stack: StackName,
  config: TripwireConfig,
  restore?: Restore,
): string {
  const vars: Record<string, Value> = { name: DEPLOYMENT_NAME, region: config.region };

  if (stack === "persistent") {
    vars.domain = config.domain;
    if (config.zoneId) vars.zone_id = config.zoneId;
  }

  if (stack === "stack") {
    vars.alert_email = config.alertEmail;
    vars.laminar_users = config.laminarUsers;
    if (config.freePlan) {
      vars.db_backup_retention_days = 1;
      vars.data_instance_type = "c7i-flex.large";
    }
    if (restore) {
      vars.db_snapshot_identifier = restore.dbSnapshotId;
      vars.data_volume_snapshot_id = restore.dataVolumeSnapshotId;
    }
  }

  return render(vars);
}

export function buildBackendHcl(accountId: string, region: string): string {
  return render({ bucket: stateBucketName(accountId, region), region });
}