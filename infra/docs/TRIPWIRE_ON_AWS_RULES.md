# Working on the backend once Tripwire runs on AWS

*Written Nov 16, 2026, alongside the work that deploys the Tripwire backend (poller, runner, clusterer, API + UI) next to Laminar. Read this if you change `signals/backend` or `signals/mock-ui`.*

## What changes for local development

Nothing you have to do. `docker compose up --build` works as before, with the same `.env`, the same ports (UI on `:5174`, API on `:8080`) and the same API paths (`/api/...`). A few new files appear (`tripwire/migrate.py`, `tripwire/api/serve.py`, a `.dockerignore`); nothing local uses them.

## How it runs on AWS (in short)

- One image with all the backend code plus the built UI, published on GHCR.
- Four ECS services from that image: poller, runner, clusterer, and the API (which also serves the UI).
- Tripwire lives under a path on Laminar's address: `https://<laminar-host>/tripwire/`. The load balancer sends everything under `/tripwire/` to Tripwire's API container, which strips the prefix, so `app.py` still sees `/api/...`.
- Settings come from the ECS task definitions in `infra/stack/tripwire.tf`, not from `.env`. Secrets come from SSM.
- The database is a separate `tripwire` database on Laminar's RDS instance. A one-off migration task applies new migration files on every deploy.

Because of that, three habits matter.

## 1. Frontend: call the API through the client, never with a raw `/api`

The UI's API calls go through `src/api/client.js` (and `src/api/dev.js`), which puts the right prefix in front: `/api/...` locally, `/tripwire/api/...` on AWS.

A new call written directly, like `fetch("/api/signals/3/notes")`, works locally but breaks on AWS. The browser asks for `https://<laminar-host>/api/...` with no `/tripwire`, so the load balancer sends it to Laminar instead of Tripwire.

- Call the API through the client functions.
- Link between pages with the router's links (`<Link to="/signals">`), not a hand-written `href="/signals"`, so links get the prefix too.

## 2. Backend: a new setting also goes in Terraform

Locally, settings come from `.env`. On AWS they come only from the task definitions in `infra/stack/tripwire.tf`.

If you add an env var the backend reads (say `RUNNER_MAX_FINDINGS`) and put it in your `.env`, it works locally, but AWS silently uses the default in the code. If there's no default, the service refuses to start.

- If a new setting should differ from its default on AWS, add it to that service's task definition in `infra/stack/tripwire.tf` (or tell whoever owns the infra).
- Secrets (keys, passwords) need an SSM parameter, not a plain env var.
- Keep adding every setting to `.env.example` as today (a test checks it).

## 3. Migrations: every change is a new file

Locally, Postgres runs every file in `migrations/` once, when the database volume is new (that's why we run `docker compose down -v` after pulling a migration).

On AWS, the database is never new; it keeps its data across deploys. So the migration task records which files it has already run, **by filename**, and on each deploy runs only the files it hasn't seen.

**Adding tables is fine.** For example, notifications:
1. Add a new file, e.g. `migrations/20261117120000_notifications.sql`, with the `CREATE TABLE`.
2. Locally: `docker compose down -v && docker compose up --build`.
3. On AWS: the next `tripwire deploy` sees a filename it hasn't run, runs it, then restarts the services on the new code. Existing data is untouched.

**What breaks:**
- **Editing a merged file** (e.g. adding a column inside `..._notifications.sql` after it was deployed): AWS already ran it and won't run it again, so the column never appears on AWS, while new local databases get it. Put the change in a new file instead, e.g. `20261118090000_notifications_add_channel.sql` with `ALTER TABLE ... ADD COLUMN ...`.
- **Renaming a merged file:** AWS sees a new name and runs it again; `CREATE TABLE` fails because the table already exists, and the deploy stops.

Same rule the team already follows: never edit or rename a merged migration; add a new one.
