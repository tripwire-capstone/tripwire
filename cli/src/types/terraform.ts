export interface Resource {
  address: string;
  values: Record<string, unknown>;
}

export interface State {
  values?: { root_module: { resources?: Resource[] } };
}

export interface Plan {
  // type is the resource type, e.g. aws_db_instance
  resource_changes?: { address: string; type?: string; change: { actions: string[] } }[];
}

export type ResourceAction = "create" | "update" | "delete" | "replace" | "read" | "noop";

export interface ResourceHook {
  address: string;
  resourceType: string;
  action: ResourceAction;
}

// The events the CLI uses; everything else is ignored. message is Terraform's own text line.
export type TerraformEvent =
  | ({ type: "apply_start"; message: string } & ResourceHook)
  | ({ type: "apply_progress"; message: string; elapsedSeconds: number } & ResourceHook)
  | ({ type: "apply_complete"; message: string; elapsedSeconds: number } & ResourceHook)
  | ({ type: "apply_errored"; message: string } & ResourceHook)
  | {
      type: "diagnostic";
      message: string;
      severity: "error" | "warning";
      summary: string;
      detail: string;
      address?: string;
    }
  | { type: "change_summary"; message: string; add: number; change: number; remove: number }
  | { type: "other"; message: string };

export interface RawEvent {
  type?: unknown;
  "@message"?: unknown;
  hook?: {
    resource?: { addr?: unknown; resource_type?: unknown };
    action?: unknown;
    elapsed_seconds?: unknown;
  };
  diagnostic?: { severity?: unknown; summary?: unknown; detail?: unknown; address?: unknown };
  changes?: { add?: unknown; change?: unknown; remove?: unknown };
}

export type StackName = "bootstrap" | "persistent" | "stack";

export interface Restore {
  dbSnapshotId: string;
  dataVolumeSnapshotId: string;
}

export type Value = string | number | string[];
