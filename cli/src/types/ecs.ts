export interface RolloutCheck {
  done: boolean;
  redeploy: string[];
  failed: string[];
}

export type Phase = "downloading" | "starting" | "checking health" | "healthy" | "restarting";

export interface TaskInfo {
  lastStatus: string;
  pullStartedAt?: string;
  pullStoppedAt?: string;
}

export interface RawTask {
  lastStatus: string;
  pullStartedAt?: string;
  pullStoppedAt?: string;
  createdAt?: string;
  group?: string;
  attachments?: { details?: { name: string; value: string }[] }[];
}

export interface StartupUpdate {
  states: Record<string, string>;
  // Missing when the phase lookup failed; show the last known one
  phases?: Record<string, Phase>;
  retried: string[];
}

export interface ServiceProgress {
  doneAt?: number;
  phase?: Phase;
  failed: boolean;
  retried: boolean;
}
