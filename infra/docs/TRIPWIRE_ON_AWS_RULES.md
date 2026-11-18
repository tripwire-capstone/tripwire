# Tripwire on AWS: what changed and how to work with it

*Written Nov 16, 2026, with the work on branch `tripwire-on-aws` that deploys the Tripwire backend (poller, runner, clusterer, API + UI) next to Laminar with `tripwire deploy --lite`. Updated Nov 17 for the Settings page (SIG-103), where API keys moved from environment variables to the database: details in [`signals/backend/docs/env_to_db_changes.md`](../../signals/backend/docs/env_to_db_changes.md). Read this if you change `signals/backend`, `signals/mock-ui`, `infra/` or `cli/`.*

## In short

- **Local development works as before.** `docker compose up --build` in `signals/backend`, UI with `npm run dev` on `:5174`, API on `:8080` at `/api/...`, same `.env`. After pulling a new migration, reset the local database (`docker compose down -v`, see [rule 3](#3-migrations-every-change-is-a-new-file)); after a new UI package, run `npm install` in `signals/mock-ui`.
- **On AWS**, Tripwire lives under Laminar's address: `https://<laminar-host>/tripwire/`. Laminar's own "Signals" sidebar item leads there.
- **One image** (backend code + built UI) runs as **five ECS services**: poller, runner, clusterer, notifier (Slack alerts), and the API, which also serves the UI.
- Tripwire's data is in its **own `tripwire` database on Laminar's RDS instance**. A one-off **migration task** prepares it on every deploy.
- **Three habits** keep code working on AWS: see [Rules](#rules).

## How it runs on AWS

```
Browser ──► load balancer (Cognito login)
              ├─ /tripwire/*            ──► Tripwire API container (UI + /api)
              ├─ /project/*/signals*    ──► redirect to /tripwire/signals
              └─ everything else        ──► Laminar
Tripwire's poller / runner / clusterer / notifier ──► Laminar's API inside the VPC, RDS (tripwire database), OpenAI
```

- **Path prefix.** The load balancer sends everything under `/tripwire/` to the API container. `tripwire/api/serve.py` strips the prefix, so `app.py` still sees `/api/...`. Every other path under `/tripwire/` gets the UI (the router handles it in the browser).
- **Settings and secrets.** On AWS, settings come from the ECS task definitions in `infra/stack/tripwire.tf`, not from `.env`. Secrets come from SSM.
- **Keys.** The OpenAI key and the Laminar project API key are added by the user in Tripwire → Settings, after the deploy. They're stored encrypted in Tripwire's database with `TRIPWIRE_SECRET_KEY` (generated in `infra/persistent/secrets.tf`, next to Laminar's own encryption key, so a destroy + restore can still read them). The deploy doesn't ask for them, and they never touch Terraform. The poller, runner and clusterer look them up on every loop, so a key saved there is used without a restart.
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
| CLI | `cli/src/commands/deploy.ts`, `aws/migrate.ts`, `aws/oldKeys.ts`, `ui/tripwireRows.ts` | The migration task, the second apply, the "Starting Tripwire" display, the "Connect Tripwire" steps at the end, and deleting the two keys older deploys stored in SSM |

## What `tripwire deploy --lite` does

1. Builds or updates the AWS stack (bootstrap, persistent, stack), including Tripwire's database credentials, migration task and load balancer rules. It doesn't create the services yet.
2. **Starting Laminar.**
3. **Starting Tripwire**:
   - **Database:** the migration task runs once and must succeed.
   - **Then the services:** a second, quiet apply moves them to the new image, and the CLI waits for **Dashboard** (the API) and **Backend** (poller, runner, clusterer, notifier) to run.
4. Deletes the two keys older deploys stored in SSM, if they're still there (once).
5. Shows both URLs, then **Connect Tripwire**: sign in to Laminar, create a project API key, add it and an OpenAI key in Tripwire → Settings, and (on a new install, which has no signals) create a signal from a template. Until then Tripwire shows "Not connected yet" and the workers wait (no crash, no alarm).

A failed migration stops the deploy before the services change. Rerunning `tripwire deploy --lite` is safe: the migration only applies what's new, and keys saved in Settings stay in the database.

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
- Secrets are never a plain env var in `tripwire.tf`. Which kind of secret decides where it lives:
  - **Provided by the user** (an API key, a Slack webhook URL): saved on the Settings page, encrypted in the database with `TRIPWIRE_SECRET_KEY` (`tripwire/crypto.py`). Workers read it through `tripwire/project_keys.py` on every loop, never from the environment, so a new value works without a restart. Not in SSM, not in Terraform.
  - **Generated for the infrastructure** (the database password, `TRIPWIRE_SECRET_KEY`): created by Terraform and stored in SSM; services get them through their `secrets` list in `tripwire.tf`.
- Keep adding every setting to `.env.example` as today (a test checks it).

### 3. Migrations: every change is a new file

Locally, Postgres runs every file in `migrations/` once, when the database volume is new. That's why we run `docker compose down -v` after pulling a migration.

On AWS the database keeps its data across deploys. So the migration task records which files it has run, **by filename**, and on each deploy runs only new ones.

**Adding tables is fine.** For example, notifications:
1. Add a new file named with the current date and time, e.g. `migrations/20261118120000_notifications.sql`. It should sort after every existing file: a fresh local database runs them in name order, so a table it depends on must already exist.
2. Locally: `docker compose down -v && docker compose up --build`.
3. On AWS: the next `tripwire deploy` runs the new file, then restarts the services on the new code. Existing data is untouched.

**What breaks:**
- **Editing a merged file:** AWS already ran it and won't again, so the change never reaches AWS, while new local databases get it. Put changes in a new file (`ALTER TABLE ...`).
- **Renaming a merged file:** AWS sees a new name and runs it again. `CREATE TABLE` fails because the table already exists, and the deploy stops.

## Adding an endpoint or a worker

- **New API routes go under `/api/...`.** `serve.py` only sends paths starting with `/api` to the API; anything else under `/tripwire/` gets the UI page. An MCP endpoint, for example, belongs at `/api/mcp`: `localhost:8080/api/mcp` locally and `/tripwire/api/mcp` on AWS.
- **Endpoints that programs call** (MCP clients, webhooks) can't get past the Cognito login page. They need their own load balancer rule that skips Cognito, at a priority before Tripwire's rule (25), for example 23. The endpoint then checks a token itself. Ask whoever owns the infra; it's a few lines in `tripwire.tf`. Laminar's `/v1/*` works the same way today.
  Tripwire's MCP server works this way: it's inside the API at `/tripwire/api/mcp`, the `tripwire_mcp` rule (23) skips Cognito for it and for its login document (`/.well-known/oauth-protected-resource/tripwire/api/mcp`, at the host's root), and it checks Cognito access tokens itself. See `signals/backend/MCP.md`.
- **A new worker** runs from the same image with a different start command. Add it to `local.tripwire_services` in `tripwire.tf`, to `TRIPWIRE` in `cli/src/aws/ecs.ts` (so `tripwire deploy` waits for it), and to the Backend row in `cli/src/ui/tripwireRows.ts`.
  - **Secrets:** `["database_url", "secret_key"]`, like the other workers. `secret_key` (`TRIPWIRE_SECRET_KEY`) is what decrypts keys saved on the Settings page.
  - **Keys it uses** (OpenAI, Laminar, a Slack webhook): read them through `tripwire/project_keys.py` on every loop, and with no key, wait or skip instead of crashing (the rules are in `env_to_db_changes.md`).
  - **Exactly one copy** for a worker that must never run twice (like the poller, or a notifier that would otherwise send every message twice): `maximum_percent = 100` and `minimum_healthy_percent = 0`.
- **Laminar already has its own MCP** at `/v1/mcp` (tools `query_laminar_sql`, `get_trace_context`, `ask_agent`), using a project API key. Avoid duplicating it.

## Publishing a new image

The deploy uses the image pinned in `infra/stack/variables.tf` (`tripwire_image`), so backend or UI changes reach AWS only after a new image is published and pinned:

**Commit first.** The image is built from the files on disk, but its tag comes from the current commit, so building before committing gives an image whose tag names the wrong commit.

```bash
docker login ghcr.io          # GitHub token (classic) with write:packages, not your password
cd signals
TAG=$(git rev-parse --short HEAD)
docker buildx build --platform linux/arm64 -f backend/Dockerfile \
  -t ghcr.io/tripwire-capstone/tripwire-backend:$TAG --push .
docker buildx imagetools inspect ghcr.io/tripwire-capstone/tripwire-backend:$TAG | grep -m1 Digest
```

Put the digest into `tripwire_image` (with a comment naming the commit), commit, then run `tripwire deploy --lite`. The package is public, so ECS downloads it without a login. A GitHub Actions workflow is planned to do this on every merge.

## Known limits (for now)

- **One Laminar project.** Tripwire watches the project whose key is saved in Settings. Laminar's "Signals" item redirects to the same Tripwire page from any project. For more projects later, the redirect becomes `/tripwire#{path}` plus a UI route that reads the project ID.
- **Keys after a `--fresh` deploy.** A deploy that restores the final snapshot brings the saved keys back. A `--fresh` deploy starts with an empty database, so the keys are added in Settings again.
- **The Signals pages update on Refresh.** Only `/dev` refreshes on its own. A "new data" notice, or live updates like Laminar's Realtime toggle, is a possible follow-up.
- **Laminar's sidebar item says "Signals" and opens in the same tab.** Renaming it or opening a new tab would need a fork of Laminar. Cmd+click opens a new tab.
- **One OpenAI key for clustering.** The clusterer uses the (single) project's OpenAI key; several projects would need a key per event's project.

## Stacked PRs

When one PR builds on another (PR 2's base is PR 1's branch), merging the bottom one first leaves the next PR still pointing at that branch, so merging it lands the code in the old branch instead of `main`. That's how #42 and #43 missed `main` and needed #44.

- **Before merging a PR, check its base is `main`.** After its parent PR merges, change the base to `main` ("Edit" next to the PR title) if GitHub hasn't already.
- Or merge from the top: each PR into its base branch first, and the bottom PR into `main` last, which brings them all.
