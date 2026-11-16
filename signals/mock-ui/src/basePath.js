// Where the app lives: "/" locally, "/tripwire/" on AWS (the UI sits under Laminar's address there).
// The API container writes the prefix into index.html's <base href> as it serves the page
// (backend tripwire/api/serve.py), so one built UI works under any prefix without a rebuild.
// Always reach the API through BASE_PATH (src/api/client.js does); a raw fetch("/api/...") works
// locally but goes to Laminar on AWS. See infra/docs/TRIPWIRE_ON_AWS_RULES.md.
const href = document.querySelector("base")?.getAttribute("href") ?? "/";

export const BASE_PATH = href.endsWith("/") ? href : `${href}/`;

// React Router wants the prefix without the trailing slash ("/" for the root)
export const ROUTER_BASENAME = BASE_PATH.slice(0, -1) || "/";
