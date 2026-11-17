# Tripwire on AWS: what changed and how to work with it

*Written Nov 16, 2026, with the work on branch `tripwire-on-aws` that deploys the Tripwire backend (poller, runner, clusterer, API + UI) next to Laminar with `tripwire deploy --lite`. Read this if you change `signals/backend`, `signals/mock-ui`, `infra/` or `cli/`.*

## In short

- **Local development is unchanged.** `docker compose up --build` in `signals/backend`, UI with `npm run dev` on `:5174`, API on `:8080` at `/api/...`, same `.env`. No database reset needed.
- **On AWS**, Tripwire lives under Laminar's address: `https://<laminar-host>/tripwire/`. Laminar's own "Signals" sidebar item leads there.
- **One image** (backend code + built UI) runs as **four ECS services**: poller, runner, clusterer, and the API, which also serves the UI.
- Tripwire's data is in its **own `tripwire` database on Laminar's RDS instance**. A one-off **migration task** prepares it on every deploy.
- **Three habits** keep code working on AWS: see [Rules](#rules).

## How it runs on AWS

```
Browser ──► load balancer (Cognito login)
              ├─ /tripwire/*            ──► Tripwire API container (UI + /api)
              ├─ /project/*/signals*    ──► redirect to /tripwire/signals
              └─ everything else        ──► Laminar
Tripwire's poller / runner / clusterer ──► Laminar's API inside the VPC, RDS (tripwire database), OpenAI
```

- **Path prefix.** The load balancer sends everything under `/tripwire/` to the API container. `tripwire/api/serve.py` strips the prefix, so `app.py` still sees `/api/...`. Every other path under `/tripwire/` gets the UI (the router handles it in the browser).
- **Settings and secrets.** On AWS, settings come from the ECS task definitions in `infra/stack/tripwire.tf`, not from `.env`. Secrets come from SSM.
- **Keys.** The OpenAI key and the Laminar project API key are asked for by `tripwire deploy` and stored in SSM. Terraform only knows their names, so the values never enter its state.
- **Database.** It's a separate `tripwire` database with its own `tripwire` user. Services never use the RDS master login; only the migration task does.

## What changed, by area

| Area | Files | What |
|---|---|---|
| Backend | `signals/backend/tripwire/migrate.py` | The migration task's script. It creates the `tripwire` user and database if missing, enables pgvector, applies new files in `migrations/` (recorded by filename in `schema_migrations`), seeds, and exits 0/1. Safe to rerun |
| | `signals/backend/tripwire/api/serve.py` | Runs the unchanged `app.py` under `TRIPWIRE_BASE_PATH` (`/tripwire` on AWS) and serves the built UI. Compose still runs `app.py` directly |
| UI | `mock-ui/src/basePath.js`, `index.html`, `src/api/client.js`, `src/api/dev.js`, `src/main.jsx`, `vite.config.js` | The UI reads its prefix at runtime from `index.html`'s `<base href>` (`/` locally; `serve.py` writes `/tripwire/` on AWS), so one build works anywhere |
| | `mock-ui/public/` | Tab icon (logo kit 90, the sneaker) and the "Tripwire" title |
| Image | `signals/backend/Dockerfile`, `signals/.dockerignore`, `compose.yaml` | The Dockerfile's build context is now `signals/`. Its stages are `ui` (Node build), `backend` (Python) and `full` (published). Compose builds only `backend`, so no Node build locally |
| Terraform | `infra/stack/tripwire.tf` | Four services (created only once a migration has succeeded); the migration task definition; the database URL and password in SSM; the load balancer rule for `/tripwire`; the Laminar Signals redirect; the clusterer's `--alive` health check; alarms |
| | `infra/stack/variables.tf`, `ecs.tf`, `outputs.tf` | `tripwire_image` (the published image, pinned by digest); permission to read Tripwire's secrets; `SIGNALS_ENABLED=true` on Laminar's frontend; outputs the CLI reads |
| CLI | `cli/src/commands/tripwire.ts`, `deploy.ts`, `aws/secrets.ts`, `aws/keys.ts`, `aws/migrate.ts`, `ui/tripwireRows.ts` | The key prompts, the migration task, the second apply, and the "Starting Tripwire" display |

## What `tripwire deploy --lite` does

1. Asks for the **OpenAI key** if SSM doesn't have it, checks it, and stores it.
2. Builds or updates the AWS stack (bootstrap, persistent, stack), including Tripwire's database credentials, migration task and load balancer rules. It doesn't create the services yet.
3. **Starting Laminar.**
4. **Connect Tripwire to Laminar**, only if SSM has no key yet: the user signs in to Laminar, creates a project and an API key, and pastes the project URL and the key. The CLI checks the key with one query, then stores it.
5. **Starting Tripwire**:
   - **Database:** the migration task runs once and must succeed.
   - **Then the services:** a second, quiet apply moves them to the new image, and the CLI waits for **Dashboard** (the API) and **Backend** (poller, runner, clusterer) to run.
6. Shows both URLs (Tripwire and Laminar).

A failed migration stops the deploy before the services change. Rerunning `tripwire deploy --lite` is safe: saved keys are skipped and the migration only applies what's new.

## Rules

### 1. Frontend: call the API through the client, never with a raw `/api`

The UI's API calls go through `src/api/client.js` (and `src/api/dev.js`), which add the prefix: `/api/...` locally, `/tripwire/api/...` on AWS.

A new call written directly, like `fetch("/api/signals/3/notes")`, works locally but breaks on AWS. The browser asks for `https://<laminar-host>/api/...`, so the load balancer sends it to Laminar.

- Call the API through the client functions.
- Link between pages with the router's links (`<Link to="/signals">`), never a hand-written `href="/signals"`.
- Files in `public/` are referenced with relative paths (`./favicon.svg`), never `/favicon.svg`.

### 2. Backend: a new setting also goes in Terraform

Locally, settings come from `.env`. On AWS they come only from the task definitions in `infra/stack/tripwire.tf`.

Say you add `RUNNER_MAX_FINDINGS` to the code and your `.env`. It works locally, but AWS silently uses the default in the code. With no default, the service refuses to start.

- If a setting should differ from its default on AWS, add it to that service's entry in `local.tripwire_services` in `infra/stack/tripwire.tf`, or tell whoever owns the infra.
- Secrets (keys, passwords, tokens) need an SSM parameter, not a plain env var.
- Keep adding every setting to `.env.example` as today (a test checks it).

### 3. Migrations: every change is a new file

Locally, Postgres runs every file in `migrations/` once, when the database volume is new. That's why we run `docker compose down -v` after pulling a migration.

On AWS the database keeps its data across deploys. So the migration task records which files it has run, **by filename**, and on each deploy runs only new ones.

**Adding tables is fine.** For example, notifications:
1. Add a new file, e.g. `migrations/20261117120000_notifications.sql`.
2. Locally: `docker compose down -v && docker compose up --build`.
3. On AWS: the next `tripwire deploy` runs the new file, then restarts the services on the new code. Existing data is untouched.

**What breaks:**
- **Editing a merged file:** AWS already ran it and won't again, so the change never reaches AWS, while new local databases get it. Put changes in a new file (`ALTER TABLE ...`).
- **Renaming a merged file:** AWS sees a new name and runs it again. `CREATE TABLE` fails because the table already exists, and the deploy stops.

## Adding an endpoint or a worker

- **New API routes go under `/api/...`.** `serve.py` only sends paths starting with `/api` to the API; anything else under `/tripwire/` gets the UI page. An MCP endpoint, for example, belongs at `/api/mcp`: `localhost:8080/api/mcp` locally and `/tripwire/api/mcp` on AWS.
- **Endpoints that programs call** (MCP clients, webhooks) can't get past the Cognito login page. They need their own load balancer rule that skips Cognito, at a priority before Tripwire's rule (25), for example 23. The endpoint then checks a token itself. Ask whoever owns the infra; it's a few lines in `tripwire.tf`. Laminar's `/v1/*` works the same way today.
- **A new worker** runs from the same image with a different start command. Add it to `local.tripwire_services` in `tripwire.tf`, to `TRIPWIRE` in `cli/src/aws/ecs.ts` (so `tripwire deploy` waits for it), and to the Backend row in `cli/src/ui/tripwireRows.ts`. A worker that must never run twice (like the poller) needs `maximum_percent = 100` and `minimum_healthy_percent = 0`.
- **Laminar already has its own MCP** at `/v1/mcp` (tools `query_laminar_sql`, `get_trace_context`, `ask_agent`), using a project API key. Avoid duplicating it.

## Publishing a new image

The deploy uses the image pinned in `infra/stack/variables.tf` (`tripwire_image`), so backend or UI changes reach AWS only after a new image is published and pinned:

```bash
docker login ghcr.io          # GitHub token with write:packages
cd signals
TAG=$(git rev-parse --short HEAD)
docker buildx build --platform linux/arm64 -f backend/Dockerfile \
  -t ghcr.io/tripwire-capstone/tripwire-backend:$TAG --push .
docker buildx imagetools inspect ghcr.io/tripwire-capstone/tripwire-backend:$TAG | grep -m1 Digest
```

Put the digest into `tripwire_image` (with a comment naming the commit), commit, then run `tripwire deploy --lite`. The package is public, so ECS downloads it without a login. A GitHub Actions workflow is planned to do this on every merge.

## Known limits (for now)

- **One Laminar project.** Tripwire watches the project whose key was given at deploy. Laminar's "Signals" item redirects to the same Tripwire page from any project. For more projects later, the redirect becomes `/tripwire#{path}` plus a UI route that reads the project ID.
- **Keys are asked for by the CLI.** A settings page (OpenAI key, Laminar URL and key, Slack webhook) is planned. It needs keys stored encrypted in `projects` and the workers reading them from there.
- **The Signals pages update on Refresh.** Only `/dev` refreshes on its own. A "new data" notice, or live updates like Laminar's Realtime toggle, is a possible follow-up.
- **Laminar's sidebar item says "Signals" and opens in the same tab.** Renaming it or opening a new tab would need a fork of Laminar. Cmd+click opens a new tab.
- **`tripwire destroy` keeps the two keys in SSM,** and reuses them on the next deploy.
