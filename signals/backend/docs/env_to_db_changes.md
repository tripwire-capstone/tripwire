# API keys: from environment variables to the database (SIG-103)

*Nov 17, 2026. Three stacked PRs: the Settings page (`settings-page`), workers reading its keys (`settings-workers`), and the deploy no longer asking for keys (`settings-cli`). Read this if you work on code that calls Laminar or OpenAI, or on `tripwire deploy`.*

## In short

- **Before:** every worker read `LMNR_PROJECT_API_KEY` and `OPENAI_API_KEY` from its environment once, at startup. On AWS, `tripwire deploy` asked for both keys and stored them in SSM.
- **Now:** the user adds both keys on Tripwire's **Settings** page. They're checked, encrypted, and saved in the database. The poller, runner, clusterer and test-run look them up **on every loop**, so a new key works without a restart.
- **Locally, nothing changes** unless you use the page: with no key saved, everything falls back to the keys in `.env`.
- **On AWS, there's no fallback:** the deploy asks for nothing, and Tripwire shows "Not connected yet" until both keys are saved.

## How it works

```
Settings page ──PUT /api/settings/{laminar|openai}──► API checks the key with Laminar / OpenAI
                                                      └─► encrypts it (TRIPWIRE_SECRET_KEY) ─► projects table

poller / runner / clusterer / test-run, every loop ──► project_keys.py:
        saved key in projects?  ── yes ──► decrypt and use it
                                └─ no  ──► LMNR_PROJECT_API_KEY / OPENAI_API_KEY from the environment
                                           └─ none ──► wait (no crash), and say so once in the logs
```

- **Checks before saving,** so a typo never replaces a working key:
  - **Laminar:** `GET /v1/project` returns the project the key belongs to, and `SELECT 1` proves it can query. The user never types a project URL: the project ID comes from the key, so "Open in Laminar" links can't point at a different project.
  - **OpenAI:** listing models proves the key is accepted.
- **Only hints leave the server:** the first 4 and last 4 characters (`TGmC...vvzt`), as Laminar shows its own keys.
- **Encryption:** Fernet, from the `cryptography` package (`tripwire/crypto.py`). The master key is `TRIPWIRE_SECRET_KEY`, 64 hex characters, like Laminar's `AEAD_SECRET_KEY`. Postgres only ever holds ciphertext.

## What each part does with a missing key

| Part | With no key anywhere |
|---|---|
| Poller | Skips that project and keeps running |
| Runner | The run waits 60 s and retries **without using an attempt**; its `last_error` (Developer page) says "no OpenAI key: add it in Settings" |
| Clusterer | Skips the OpenAI steps (summarize, embed, LLM naming) and still runs the rest. Health reports "waiting for an OpenAI key", which counts as **healthy**, so the clustering-unhealthy alarm never fires for it |
| Test-run | Returns 503 "No OpenAI key yet: add it in Settings, then test again." |
| UI | A "Not connected yet" banner on every page, linking to Settings |

Each worker logs a missing key **once**, and again only when it changes ("using the Laminar key saved in Settings (TGmC...vvzt)"), not on every loop. A restart logs the current state once more.

## What changed, by area

| Area | Files | What |
|---|---|---|
| Database | `migrations/20261117130000_project_keys.sql` | OpenAI key columns on `projects`; the Laminar key columns become nullable (the old `not-encrypted-yet` placeholder becomes NULL); a check that each key and its hint are set together |
| Encryption | `tripwire/crypto.py` | `encrypt()` / `decrypt()`, with clear errors for a missing or wrong `TRIPWIRE_SECRET_KEY` |
| Key lookup | `tripwire/project_keys.py` | `project_key()` (saved → environment → missing), `save_key()`, `clear_key()`, `KeyWatch` (logs only on change), `single_project_key()` (for the clusterer) |
| API | `tripwire/api/settings.py`, `api/app.py` | `GET /api/settings`, `PUT` and `DELETE /api/settings/{kind}`; test-run uses the project's keys |
| Seed | `tripwire/seed.py`, `migrate.py` | The project starts with no saved keys |
| Poller | `poller/__main__.py` | `poll_all()`: each project's Laminar key on every poll |
| Runner | `runner/worker.py`, `jobs.py`, `__main__.py` | `ProjectClients.for_run()`: both keys per run; `jobs.load()` returns the run's project |
| Judge / LLM | `judge/judge.py`, `llm/_openai.py` | `judge(..., openai_api_key=)` → `_complete_openai(api_key=)` → `AsyncOpenAI(api_key=...)` |
| Clusterer | `clusterer/__main__.py`, `settings.py`, `health.py` | `OpenAISteps`: key looked up every pass; on a change, new clients go into the same `Preparer` and `Namer` (the skip list survives). `OPENAI_API_KEY` is no longer a startup setting |
| UI | `mock-ui/src/pages/Settings.jsx`, `components/AppLayout.jsx`, `src/api/` | The page, the sidebar link and icons (`@tabler/icons-react`), the banner |
| Terraform | `infra/persistent/secrets.tf`, `infra/stack/tripwire.tf`, `ecs.tf`, `variables.tf` | `TRIPWIRE_SECRET_KEY` generated in `persistent/` (survives destroy, like Laminar's own key); services no longer get the two keys from SSM; `laminar_project_id` removed |
| CLI | `cli/src/commands/deploy.ts`, `aws/oldKeys.ts` | No key prompts; a "Connect Tripwire" box at the end of the deploy; deletes the two keys older deploys stored in SSM, once |

## Rules for new code

1. **Never read `OPENAI_API_KEY` or `LMNR_PROJECT_API_KEY` from the environment in a worker or the API.** Get the project's key through `tripwire/project_keys.py`, so a key saved on the Settings page is used:
   ```python
   from tripwire.project_keys import project_key
   key = project_key(conn, project_id, "openai")   # key.value is None when there's no key anywhere
   ```
   Hand-run scripts (`try_naming.py`, `try_prompts.py`, `export_fixtures.py`) still read `.env`; that's fine.
2. **Look the key up per loop or per run, not once at startup.** Otherwise a key saved on the page needs a restart.
3. **A missing key isn't a crash.** Wait or skip, and report it once (`KeyWatch`).
4. **Never log or return a key.** Use `key.hint` for display.
5. **The judge needs the key passed in:** `judge(laminar, trace, version, openai_api_key=...)`. Fake judges in tests take the same keyword.

## After pulling

1. **Reset your local database:** `docker compose down -v && docker compose up --build` in `signals/backend`. There are new migrations (this one plus the recent summarizer and cluster-naming ones); without them the clusterer's naming step fails. This wipes your local data.
2. **Run `npm install` in `signals/mock-ui`** for the icon package.
3. **Optional:** to save keys on the Settings page locally, add an encryption key once:
   ```bash
   echo "TRIPWIRE_SECRET_KEY=$(openssl rand -hex 32)" >> .env
   ```
   No openssl: `python -c "import secrets; print(secrets.token_hex(32))"`. Without it, everything works with the keys in `.env`; only saving on the page shows an error saying what to add. Don't change it later: keys saved with the old one become unreadable (the page then asks to set them again).

## On AWS

- `tripwire deploy --lite` asks for no keys. At the end it shows **Connect Tripwire**: sign in to Laminar, create a project API key, and add it plus an OpenAI key in Tripwire → Settings. Signals start within about a minute.
- **destroy → deploy** (restoring the final snapshot) brings the saved keys back: they're in the database snapshot, and `TRIPWIRE_SECRET_KEY` lives in `infra/persistent`. A **`--fresh`** deploy starts with an empty database, so the keys are added again.
- The first deploy with this change deletes the two old SSM keys (`/tripwire/tripwire/openai_api_key`, `laminar_api_key`), so a running stack shows "Not connected yet" until the keys are saved once.
- The image must include these changes (pinned `:c091801` or later): an older image's poller crashes without `LMNR_PROJECT_API_KEY`.

## Decisions (with what was rejected)

| Topic | Chosen | Rejected and why |
|---|---|---|
| Where keys live | Encrypted columns on `projects` (allows a key per project later) | A separate settings table; SSM written by the page plus a service restart (AWS-only, one project, gives the web container write access to secrets) |
| Laminar project | Found from the key (`GET /v1/project`) | Asking for a project URL: a URL from one project and a key from another would pass the check and break the links |
| Master key on AWS | `infra/persistent/secrets.tf`, next to Laminar's `aead_secret_key` (moves with it when `persistent/` merges into the stack) | The stack layer (a redeploy would make restored keys unreadable); a CLI-made SSM parameter kept on destroy |
| Missing key, runner | Wait and retry without using an attempt | Fail at once |
| Missing key, clusterer | Skip the OpenAI steps; healthy "waiting" state, no alarm | Unhealthy (every fresh deploy would send an alert email) |
| Logging | Once per change | Every loop; every 5 minutes |
| Old SSM keys | Deleted by the next deploy | Left behind |

## Known limits

- **One project.** The clusterer uses the single project's OpenAI key; several projects would need each event's project key.
- **No roles.** Anyone who can sign in to Tripwire can change the keys.
- **Slack webhook:** the Settings page has a hidden Notifications section (`FEATURES.notifications`) for when the notifications table exists.
