// What the stack's outputs give the migration step
export interface MigrationTarget {
  cluster: string;
  taskDefinition: string;
  subnets: string[];
  securityGroup: string;
}

// How one migration task ended
export interface MigrationOutcome {
  exitCode: number | null;
  // ECS's reason when the container never ran (e.g. it couldn't pull the image or read a secret)
  stoppedReason?: string;
  taskId: string;
}

// Where "Starting Tripwire" is: the database step first, then the services; times are Date.now() values
export interface TripwireStart {
  stage: "database" | "services";
  startedAt: number;
  databaseDoneAt?: number;
  databaseFailed?: boolean;
}

// How runMigration checks on the migration task; tests pass a fake clock and sleep
export interface MigrationWait {
  intervalMs?: number;
  limitMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}
