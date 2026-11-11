# What the Tripwire backend needs from the infrastructure

*Started Nov 11, 2026 by the Clusters pair. For whoever adds the Tripwire backend services (poller, runner,
clusterer, api in `signals/backend/`) to this Terraform. Today `stack/` deploys Laminar only; none of the
backend services has an ECS task definition yet. Add to this file whenever backend work creates a new
infrastructure requirement.*

Each item says **why**, **what to add**, and **where the decision is recorded**.

---

## 1. Clusterer: ECS health check (required)

**Why.** On its own, ECS only knows whether the clusterer's process is running. A clusterer that is stuck
(for example waiting forever on a database call) still looks fine. The clusterer ships a health-check
command, but it does nothing unless the task definition tells ECS to run it. **Nothing reads
`signals/backend/compose.yaml` on AWS**, so the local `healthcheck:` doesn't carry over.

**What to add.** In the clusterer's container definition (same shape as `stack/ecs.tf`'s app-server):

```hcl
healthCheck = {
  command     = ["CMD", "python", "-m", "tripwire.clusterer.health", "--alive"]
  interval    = 30   # seconds between checks
  timeout     = 10
  retries     = 3    # unhealthy after 3 failures in a row
  startPeriod = 60   # grace period after the task starts
}
```

- **Use `--alive`, not the plain command.** `--alive` only fails when the clusterer is frozen (no pass has
  finished in the last few minutes). ECS **replaces** an essential container whose health check fails, and
  a restart only fixes a frozen process. The plain command checks whether clustering is *working*
  (Q12's rules), which fails for reasons a restart can't fix (OpenAI down, expired key, database down,
  a backlog), so it would cause restart loops.
- **Keep the timing in step with `compose.yaml`** (the clusterer's `healthcheck:`). Different numbers are
  harmless but confusing.

**Decided in:** `signals/backend/docs/clustering/DESIGN.md` (Q12) and the Clusters pair's PR 5.
**Status:** the command ships with the Clusters pair's PR 5 (health check); same timing as `compose.yaml`.

## 2. Clusterer: alarm on "clustering stalled" (recommended)

**Why.** Q12's two rules (an event unfinished for over 15 minutes; the last 10 summaries all failed) are
**reported, not acted on**: the clusterer logs a WARNING line when one trips, again every few minutes
while it lasts, and once when it clears. On AWS that line lands in CloudWatch Logs, where nobody reads it
unless an alarm does.

**What to add.** A metric filter on the clusterer's log group matching the fixed prefix
**`clustering unhealthy:`** (every such warning starts with it, e.g. `clustering unhealthy: event 812 has
waited 23 minutes to be clustered (limit 15)` or `clustering unhealthy: the last 10 summaries all failed`),
and an alarm on that metric sending to the existing `aws_sns_topic.alerts` (as the alarms in
`stack/alerts.tf` already do). The clusterer repeats the warning every 5 minutes while a problem lasts, so
"at least 1 match in 10 minutes" is a reasonable alarm rule. It logs `clustering healthy again` when the
problem clears (could drive an OK notification). The prefix is the constant `UNHEALTHY_PREFIX` in
`tripwire/clusterer/health.py`: change both together.

**Decided in:** the Clusters pair's PR 5 (decision 2, "two separate checks").

## 3. Clusterer: settings arrive as environment variables

**Why.** The clusterer reads every setting once at startup (`tripwire/clusterer/settings.py`) and **refuses
to start** on a bad value, printing every problem. On ECS that shows as a container that exits at once,
an unhealthy service, and the message in CloudWatch Logs. This is intentional: a misconfigured clusterer
that runs would produce plausible-looking but wrong clusters.

**What to add.** Pass settings through the task definition's `environment` (non-secret). Defaults are
in `settings.py` and `signals/backend/.env.example`; set only what differs.

| Variable | Default | Note |
|---|---|---|
| `CLUSTERER_MODE` | `real` | `placeholder` runs without OpenAI (skeleton stand-in) |
| `CLUSTERER_INTERVAL_SECONDS` | 10 | |
| `CLUSTER_PREPARE_BATCH` | 50 | |
| `CLUSTER_ASSIGN_THRESHOLD` | 0.30 | temporary until calibrated; must be in (0, 1] |
| `CLUSTER_MIN_EVENTS` / `CLUSTER_MIN_CONVERSATIONS` | 3 / 2 | conversations can't exceed events |
| `CLUSTER_POOL_WINDOW_DAYS` | 14 | |
| `CLUSTER_HEALTH_STALL_MINUTES` / `CLUSTER_HEALTH_FAILURE_RUN` | 15 / 10 | Q12's two rules |
| `CLUSTER_HEALTH_ALIVE_MINUTES` | 3 | `--alive` fails after this long without a pass; must exceed the interval |
| `CLUSTERER_LAST_PASS_FILE` | `/tmp/clusterer-last-pass` | where each pass records its time; must be writable in the container |

- **If Terraform exposes any of these as variables** (likely only the threshold), give them `validation`
  blocks with **the same ranges** as `settings.py`. That's a second copy of the rule, so keep it to the few
  settings a deployer would change and keep the ranges identical.

**Decided in:** the Clusters pair's PR 4 (decision 3, settings).

## 4. Clusterer: `OPENAI_API_KEY` as a secret (required)

**Why.** In real mode (the default) the clusterer's prepare step calls OpenAI: it summarizes each event
(`SUMMARIZER_MODEL`, default `gpt-6-luna`) and embeds the summary (`text-embedding-3-small`). Without the
key, the clusterer **refuses to start** (item 3): on ECS the container exits at once and the service keeps
restarting it, with `OPENAI_API_KEY is required in real mode` in CloudWatch Logs.

**What to add.**
- `OPENAI_API_KEY` from **Secrets Manager** via the task definition's `secrets` / `valueFrom`, never in plain
  `environment` (same as `DATABASE_URL`'s password). The clusterer never prints it (its settings hide it).
- Outbound HTTPS from the clusterer's tasks to `api.openai.com` (a NAT gateway or equivalent if they run in
  private subnets).
- If the Signals pair's runner uses OpenAI too, it can share the secret, but note that both then share the
  key's rate limits (the clusterer backs off on its own after a rate-limit error).

**Decided in:** `signals/backend/docs/clustering/DESIGN.md` ("Prepare step") and Design Notes Q21.

## 5. Database connection for the backend services

**Why.** Every backend service (and the clusterer's health check, which runs inside the clusterer's
container) connects through `DATABASE_URL`. Locally that's the compose `db` container; on AWS it's RDS.

**What to add.**
- `DATABASE_URL` pointing at the RDS endpoint, with the **password from Secrets Manager** (the task
  definition's `secrets` / `valueFrom`, as the Laminar tasks do), never in plain `environment`. Prefer
  `manage_master_user_password` or a generated password, per the team's Terraform rules.
- Security groups: the backend tasks need to reach RDS on 5432 (the health check reuses the clusterer's
  network path, so nothing extra for it).
- **pgvector:** the migrations run `CREATE EXTENSION vector`; RDS for PostgreSQL supports it.
- **Open question:** does the backend get its own database on the existing RDS instance (`stack/rds.tf`,
  Laminar's Postgres 16), or its own instance?

---

## Index

| # | Item | For | Required? | Arrives with |
|---|---|---|---|---|
| 1 | ECS `healthCheck` with `--alive` | clusterer | Yes | Clusters PR 5 |
| 2 | CloudWatch alarm on "clustering stalled" | clusterer | Recommended | Clusters PR 5 |
| 3 | Settings as env vars; Terraform validation matches `settings.py` | clusterer | Yes | Clusters PR 4 |
| 4 | `OPENAI_API_KEY` from Secrets Manager; outbound HTTPS to OpenAI | clusterer | Yes | Clusters PR 3+4 |
| 5 | `DATABASE_URL` to RDS, password from Secrets Manager, pgvector | all backend services | Yes | now |
