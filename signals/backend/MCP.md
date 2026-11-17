# Tripwire MCP server

Lets a coding agent (Claude Code, Cursor, Codex…) ask Tripwire what's going wrong in an AI agent:
which clusters of problems are biggest, and what happens in one of them. It only reads.

It runs **inside the API**, at `/api/mcp`:

| Where | Address | Login |
|---|---|---|
| Local (compose) | `http://localhost:8080/api/mcp` | none (auth off) |
| AWS | `https://<laminar-host>/tripwire/api/mcp` | Cognito, the same users as Laminar and the Tripwire UI |

```
coding agent ──MCP over HTTP──▶ API container ─┬─ /api/mcp ──▶ MCP server ──(in-process)──▶ /api/overview, /api/clusters/{id}
                                               └─ everything else ──▶ the API as before
```

`tripwire/api/asgi.py` puts the two together and is what uvicorn runs (compose directly; on AWS
through `serve.py`, which adds the `/tripwire` prefix and the UI). The tools call the API's own routes
in-process (`httpx.ASGITransport`), so there's no second service and no network hop.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_problem_clusters` | `days` (1–90, default 7), `limit` (1–50, default 10) | clusters with the most warning or critical events in the range, across every signal, each with its ID |
| `get_cluster` | `cluster_id`, `examples` (1–10, default 3) | the cluster's counts, conversations, first and last seen; a summary and likely cause per event (up to 20, most typical first); example events with severity, fields, the judge's analysis and the Laminar link |

Answers are Markdown. Errors (unknown cluster, bad arguments, API failure) come back as tool errors
with a sentence the agent can pass on.

Examples are the events closest to the cluster's center, one per conversation first, so three
examples aren't three turns of one chat.

## Local use

`docker compose up --build` starts it with the API; the API's log says
`MCP at http://localhost:8080/api/mcp, auth off`. Connect Claude Code once, from any folder:

```bash
claude mcp add --transport http --scope user tripwire http://localhost:8080/api/mcp
```

In Claude Code, `/mcp` shows `tripwire` connected with 2 tools. Then ask, for example:
"Using Tripwire, what are the biggest problems in my agent over the last 30 days? Explain the top
cluster."

With auth off, MCP is only served when `MCP_PUBLIC_URL` is a local address (`localhost`,
`127.0.0.1`); anywhere else the API starts without it and logs why. Requests addressed to another
host name are refused (421), so a web page can't reach it through a look-alike domain. The local
API itself has no login either, so MCP exposes nothing the API doesn't already.

## On AWS

Nothing to configure: `infra/stack/tripwire.tf` gives the API container `MCP_PUBLIC_URL` and the
three `MCP_COGNITO_*` settings, so auth is always on there. The load balancer rule `tripwire_mcp`
(priority 23) sends `/tripwire/api/mcp` and its login document to the API container **without** the
browser login, because an agent can't fill in a login page; the MCP server checks a token instead.

It reaches AWS like any backend change: publish a new image, pin it in `tripwire_image`, run
`tripwire deploy --lite` (see `infra/docs/TRIPWIRE_ON_AWS_RULES.md`).

Users add it with the MCP app client's ID (`terraform output mcp_cognito_settings`):

```bash
claude mcp add --transport http --scope user tripwire https://<laminar-host>/tripwire/api/mcp --client-id <MCP_COGNITO_CLIENT_ID> --callback-port 53682
```

then run `/mcp` in Claude Code, pick `tripwire` and choose Authenticate:

1. Claude Code calls `/tripwire/api/mcp` without a token and gets a 401 naming
   `https://<laminar-host>/.well-known/oauth-protected-resource/tripwire/api/mcp`.
2. That document names the Cognito pool as the login server and `tripwire-mcp/read` as the scope.
3. Claude Code opens Cognito's login page in the browser (PKCE, no client secret). Someone still
   logged in to Cognito (say from the Laminar UI) comes straight back; otherwise they type their
   password.
4. Cognito sends the browser to `http://localhost:53682/callback`; Claude Code exchanges the code for
   an access token (1 hour) and a refresh token (30 days) and keeps them.
5. Every MCP request carries the access token. The server checks its signature against the pool's
   public keys, the issuer, `token_use = access`, the MCP app client ID, the expiry and the
   `tripwire-mcp/read` scope. Anything else is a 401.

Cognito access tokens have no audience claim, so the app client ID and the scope tie a token to this
server: a token from the browser login has neither and is refused.

**Not yet tried against a real pool.** The checks are unit tested with our own signing keys, and the
routing under `/tripwire` is tested. On the first deploy, try the whole login once, in particular
that Claude Code accepts Cognito's discovery document (`<issuer>/.well-known/openid-configuration`)
and the callback path.

Terraform: `cognito.tf` (the `tripwire-mcp` resource server with its `read` scope, the public `-mcp`
app client), `tripwire.tf` (the API's MCP settings, the `tripwire_mcp` rule), `outputs.tf`
(`mcp_cognito_settings`).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `MCP_PUBLIC_URL` | `http://localhost:8080/api/mcp` | the address clients connect to; named in the login document |
| `MCP_COGNITO_REGION`, `MCP_COGNITO_USER_POOL_ID`, `MCP_COGNITO_CLIENT_ID` | empty | all three: auth on; none: auth off; only some: the API refuses to start |

To try the Cognito login locally against a deployed pool, put the three values from
`terraform output mcp_cognito_settings` in `.env` and restart the API.

## Code

```
tripwire/api/asgi.py       the API with MCP inside: what uvicorn runs
tripwire/api/serve.py      on AWS: adds the /tripwire prefix and the UI; passes the login document through
tripwire/mcp_server/
  server.py     the MCPServer and its two tools; with_mcp() mounts it at /api/mcp
  api.py        calls to the API's routes; failures become readable tool errors
  format.py     the tools' Markdown answers
  auth.py       CognitoVerifier: checks access tokens
  settings.py   settings from the environment; auth on, off, or refuse to start
tests/test_mcp_server.py   settings, tools against a fake API, token checks, routing and 401s
tests/test_serve.py        MCP and its login document under the /tripwire prefix
```

It uses the `mcp` package 2.x, where the high-level server is `MCPServer`
(`from mcp.server.mcpserver import MCPServer`; it was `FastMCP` in 1.x).

Laminar has its own MCP at `/v1/mcp` (SQL over traces, trace context). This one is about Tripwire's
clusters and findings, so the two complement each other.

## Not built yet

Traces in the answers, and any tool that writes.
