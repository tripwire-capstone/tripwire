import type { FetchFn, KeyCheck } from "../types/tripwire.js";

const unreachable = (service: string, error: unknown): KeyCheck => ({
  ok: false,
  reason: `Couldn't reach ${service} to check the key (${error instanceof Error ? error.message : String(error)}).`,
});

// One cheap call (list models) to check the OpenAI key before storing it
export const checkOpenAiKey = async (key: string, fetchFn: FetchFn = fetch): Promise<KeyCheck> => {
  try {
    const response = await fetchFn("https://api.openai.com/v1/models", {
      headers: { Authorization: `Bearer ${key}` },
    });
    if (response.ok) return { ok: true };
    if (response.status === 401) return { ok: false, reason: "OpenAI says this key isn't valid." };
    return { ok: false, reason: `OpenAI answered ${response.status} when checking the key.` };
  } catch (error) {
    return unreachable("OpenAI", error);
  }
};

// One SELECT 1 through Laminar's SQL API, the same API the poller uses. /v1/* skips the Cognito
// login on the load balancer (project API keys protect it), so this works from the user's machine.
export const checkLaminarKey = async (laminarUrl: string, key: string, fetchFn: FetchFn = fetch): Promise<KeyCheck> => {
  try {
    const response = await fetchFn(`${laminarUrl.replace(/\/$/, "")}/v1/sql/query`, {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ query: "SELECT 1", parameters: {} }),
    });
    if (response.ok) return { ok: true };
    if (response.status === 401 || response.status === 403) {
      return { ok: false, reason: "Laminar says this key isn't valid. Copy a project API key from the project's settings." };
    }
    return { ok: false, reason: `Laminar answered ${response.status} when checking the key.` };
  } catch (error) {
    return unreachable("Laminar", error);
  }
};

// The project ID is the UUID in a Laminar project URL (…/project/<id>/traces); accept either
export const projectIdFrom = (input: string): string | null =>
  input.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i)?.[0]?.toLowerCase() ?? null;
