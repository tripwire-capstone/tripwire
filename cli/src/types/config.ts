// Contents of tripwire.json
export interface TripwireConfig {
  accountId: string;
  region: string;
  domain: string;
  zoneId: string | null;
  alertEmail: string;
  laminarUsers: string[];
  freePlan: boolean;
  lastDeploy?: {
    mode: string;
    cliVersion: string;
    at: string;
  };
  lastDestroy?: {
    at: string;
  };
  // The Signals backend (infra/stack/tripwire.tf); missing before the first Tripwire deploy
  tripwire?: TripwireSettings;
}

// Never the keys themselves: they live in SSM (aws/secrets.ts); only their last 4 characters
export interface TripwireSettings {
  // The image the four services run now; empty until the first migration succeeds
  servicesImage?: string;
  // The Laminar project Tripwire watches (the UUID in its Laminar URL)
  laminarProjectId?: string;
  openAiKeyHint?: string;
  laminarKeyHint?: string;
}
