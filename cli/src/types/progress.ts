import type { StackName } from "./terraform.js";

export interface LiveApplyOptions {
  title: string;
  action: ProgressAction;
  // Whose group table to use
  stack: StackName;
  // e.g. "usually 8–10 minutes"
  estimate?: string;
  // After this long, a calm "taking longer than usual" line appears
  usualSeconds?: number;
  // Added to a failure's message, e.g. how to continue
  hint: string;
}

export type ProgressAction = "build" | "remove";

export interface GroupDef {
  name: string;
  // Resource types; a trailing * matches a prefix (aws_cognito_* → every Cognito type)
  types: string[];
  // Shown next to the group while it runs
  notes?: Partial<Record<ProgressAction, string>>;
}

export interface GroupTable {
  groups: GroupDef[];
  noLine: string[];
}

export interface GroupProgress {
  name: string;
  note?: string;
  expected: Set<string>;
  running: Set<string>;
  completed: Set<string>;
  failed: boolean;
  // Milliseconds; first resource started, last one finished
  firstStart?: number;
  lastEnd?: number;
  // Time with at least one of its resources running; waiting on other groups doesn't count
  activeMs: number;
  runningSince?: number;
}

// waiting: started, but nothing of its own is being built right now (it waits on other parts)
export type GroupStatus = "pending" | "running" | "waiting" | "done" | "failed";

export interface Progress {
  action: ProgressAction;
  startedAt: number;
  groups: GroupProgress[];
}

export type RowStatus = GroupStatus;

export interface Row {
  name: string;
  status: RowStatus;
  seconds: number;
  // Shown after the time, e.g. a note or a phase
  detail?: string;
}

export interface Block {
  title: string;
  status: "running" | "done" | "failed";
  seconds: number;
  // e.g. "usually 8–10 minutes"; shown while running
  estimate?: string;
  rows: Row[];
  // A calm line under the rows, e.g. when it takes longer than usual
  warning?: string;
}

export interface ProgressDisplay {
  // Draws the final state and stops updating
  stop(): void;
}
