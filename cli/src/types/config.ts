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
}
