// The two keys the CLI asks for and stores in SSM (infra/stack/tripwire.tf reads them by name)
export type SecretKey = "openai_api_key" | "laminar_api_key";

// The result of checking a key against its service before storing it
export type KeyCheck = { ok: true } | { ok: false; reason: string };

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

// fetch, or a fake of it in tests
export type FetchFn = typeof fetch;

// Where "Starting Tripwire" is: the database step first, then the services; times are Date.now() values
export interface TripwireStart {
  stage: "database" | "services";
  startedAt: number;
  databaseDoneAt?: number;
  databaseFailed?: boolean;
}
