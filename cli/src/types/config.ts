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

// No keys or key hints: keys are added in Tripwire → Settings and stored in its database, and the
// Laminar project comes from the key saved there
export interface TripwireSettings {
  // The image the four services run now; empty until the first migration succeeds
  servicesImage?: string;
}
