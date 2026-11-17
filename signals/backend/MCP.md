# Tripwire MCP server

Lets a coding agent (Claude Code, Cursor, Codex…) ask Tripwire what's going wrong in an AI agent:
which clusters of problems are biggest, and what happens in one of them. It only reads.

```
coding agent ──MCP over HTTP──▶ mcp service (:8090/mcp) ──HTTP──▶ api service (:8080) ──▶ Postgres
                                       │
                                       └── checks Cognito access tokens (when auth is on)
```

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_problem_clusters` | `days` (1–90, default 7), `limit` (1–50, default 10) | clusters with the most warning or critical events in the range, across every signal, each with its ID |
| `get_cluster` | `cluster_id`, `examples` (1–10, default 3) | the cluster's counts, conversations, first and last seen; a summary and likely cause per event (up to 20, most typical first); example events with severity, fields, the judge's analysis and the Laminar link |

Answers are Markdown. Errors (API down, unknown cluster, bad arguments) come back as tool errors
with a sentence the agent can pass on.

Examples are the events closest to the cluster's center, one per conversation first, so three
examples aren't three turns of one chat. The data comes from `GET /api/overview` and
`GET /api/clusters/{id}` (see `signals/mock-ui/SIGNALS_API.md`).

## Running it locally

It's the `mcp` service in `compose.yaml`, so `docker compose up --build` starts it with everything
else. It listens on `http://localhost:8090/mcp`, published on this machine only.

Without the `MCP_COGNITO_*` settings, **auth is off**: the log says so, and anyone who can reach the
port can read clusters. That's why compose publishes it on `127.0.0.1` only. Requests addressed to
any other host name are refused (421), so a web page can't reach it through a look-alike domain.

Connect Claude Code (once, from any folder):

```bash
claude mcp add --transport http --scope user tripwire http://localhost:8090/mcp
```

Then in Claude Code, `/mcp` shows `tripwire` connected with 2 tools. Ask, for example:
"Using Tripwire, what are the biggest problems in my agent this week? Explain the top cluster."

## Auth with Cognito

With all three settings below, every request needs a Cognito access token. They come from the
deployed stack:

```bash
cd infra/stack
terraform output mcp_cognito_settings
```

Put them in `signals/backend/.env` (`MCP_COGNITO_REGION`, `MCP_COGNITO_USER_POOL_ID`,
`MCP_COGNITO_CLIENT_ID`) and restart the service. With only some of them, the server refuses to start.

Users log in with the same email and password as the Laminar UI (the same pool), so anyone added
with `tripwire users add` can use it. They add the server with the MCP app client's ID:

```bash
claude mcp add --transport http --scope user tripwire <server URL>/mcp --client-id <MCP_COGNITO_CLIENT_ID> --callback-port 53682
```

then run `/mcp` in Claude Code, pick `tripwire` and choose Authenticate. What happens:

1. Claude Code calls `/mcp` without a token and gets a 401 naming
   `/.well-known/oauth-protected-resource/mcp`.
2. That document names the Cognito pool as the login server and `tripwire-mcp/read` as the scope.
3. Claude Code opens Cognito's login page in the browser (PKCE, no client secret). If the user is
   still logged in to Cognito, it returns at once; otherwise they type their password.
4. Cognito sends the browser back to `http://localhost:53682/callback`; Claude Code exchanges the
   code for an access token (1 hour) and a refresh token (30 days) and keeps them.
5. Every MCP request carries the access token. The server checks its signature against the pool's
   public keys, the issuer, `token_use = access`, the MCP app client ID, the expiry and the
   `tripwire-mcp/read` scope. Anything else is a 401.

Cognito access tokens have no audience claim, so the app client ID and the scope tie a token to this
server: a Laminar UI login has neither and is refused.

**Not yet tried against a real pool.** The checks are unit tested with our own signing keys. The
first time a stack is deployed, check the whole login once, in particular that Claude Code accepts
Cognito's discovery document (`<issuer>/.well-known/openid-configuration`) and that the callback
path matches.

Terraform (`infra/stack/cognito.tf`): the `tripwire-mcp` resource server with its `read` scope, and
the public `-mcp` app client with the callback above.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `TRIPWIRE_API_URL` | `http://localhost:8080` | the API it reads (compose: `http://api:8080`) |
| `MCP_PUBLIC_URL` | `http://localhost:8090/mcp` | the address users connect to; its path is where MCP is served |
| `MCP_HOST`, `MCP_PORT` | `127.0.0.1`, `8090` | where it listens (compose: `0.0.0.0` inside the container) |
| `MCP_COGNITO_REGION`, `MCP_COGNITO_USER_POOL_ID`, `MCP_COGNITO_CLIENT_ID` | empty | all three: auth on; none: auth off |

## Code

```
tripwire/mcp_server/
  server.py     the MCPServer, its two tools, host-name protection, main()
  api.py        calls to the Tripwire API; failures become readable tool errors
  format.py     the tools' Markdown answers
  auth.py       CognitoVerifier: checks access tokens
  settings.py   settings from the environment; auth on, off, or refuse to start
tests/test_mcp_server.py   settings, tools against a fake API, token checks with our own keys, 401s
```

It uses the `mcp` package 2.x, where the high-level server is `MCPServer`
(`from mcp.server.mcpserver import MCPServer`; it was `FastMCP` in 1.x).

## Not built yet

Deploying it to AWS (it needs the signals backend deployed first), traces in the answers, and any
tool that writes.
