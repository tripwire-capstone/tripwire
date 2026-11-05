# Frustration evaluation dashboard

This React page loads saved frustration scores from Postgres through a Python API. It loads automatically and has a Refresh button; no report upload is needed. The API and threshold slider never call the classifier.

## Set up the database

Install [Docker with Compose](https://docs.docker.com/compose/install/) and start Docker. The same database setup works on macOS, Windows, and Linux; a separate Postgres installation is not required.

Activate your virtual environment from the repository root:

macOS / Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Then install the Python packages:

```bash
python -m pip install -r requirements.txt
```

For a new checkout, create a root `.env` using `.env.example` as a starting point. If `.env` already exists, keep your existing API key and add the database settings:

```dotenv
POSTGRES_PASSWORD=local_dev_password
DATABASE_URL=postgresql://signals:local_dev_password@localhost:5434/signals_prototype
```

The password in `DATABASE_URL` must match `POSTGRES_PASSWORD`. Keep credentials in the root `.env`; the frontend uses no database credentials.

Start the database with this command on any supported OS:

```bash
docker compose up -d --wait db
```

Docker creates `signals_prototype` and applies `schema.sql` on the first startup. Port 5434 keeps this database separate from a local Postgres server (5432) and from Laminar's own Postgres (5433). Each developer has their own database; existing data from a separate Postgres installation is not copied.

Data stays in the `postgres_data` Docker volume when the container stops. To stop the database:

```bash
docker compose stop db
```

To inspect its tables without installing `psql` on your computer:

```bash
docker compose exec db psql -U signals -d signals_prototype -c '\dt'
```

The schema initializer runs only for a new, empty volume. Later edits to `schema.sql` need to be applied as SQL migrations to an existing database. Changing the password in `.env` also does not update an existing database user's password.

## Run an evaluation

From the repository root, activate the project virtual environment and run:

```bash
python -m eval.evaluate_frustration --split dev --threshold 0.5
```

The evaluator loads the selected gold split into `turns` and saves probabilities in `results`. It resumes unfinished runs and reuses scores for the same signal and question version. Labels, split, and source metadata never go to the classifier. A JSON export is still written to `eval/reports/frustration-dev.json` for sharing, but the dashboard reads the database.

When you change the prompt, model, or input handling, increment `QUESTION_VERSION` in `classifier/config.py` before evaluating again, and restart the API. Older scores remain in the database; the API shows the current version.

Add `--split test --threshold <chosen-value>` only for the final test run after tuning on dev. Opening the test view never runs that evaluation automatically.

## Start the API

In a terminal at the repository root, with the virtual environment active:

```bash
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8100
```

`GET /api/results?split=dev` reads saved scores joined to their turns. It matches labels from the existing gold file using `gold_case_id`, so no additional label table is required. `split=test` must be requested explicitly. Empty and incomplete runs include a pending-case count. API requests use read-only database transactions and do not need an OpenAI key.

## Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open the local address printed by Vite. It proxies `/api` requests to Python on port 8100. Keep both terminals running; choose Refresh after an evaluation finishes. `npm run preview` also proxies to the API after a build.

The page starts on dev with a threshold of 0.50. Adjust it on dev, then select test to inspect final results at that same threshold. The test slider is disabled. The threshold stays selected while the page remains open; a page reload starts again on dev at 0.50.

## Checks

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
cd frontend
npm run build
```

Database integration checks run only when `TEST_DATABASE_URL` points to a disposable Postgres database. They create and remove their own schema and use synthetic cases with a mocked classifier; they never evaluate the real test split or call OpenAI.

To include database checks, first create a separate test database:

```bash
docker compose exec db createdb -U signals signals_prototype_test
```

Add `TEST_DATABASE_URL` to the root `.env`, using the same connection URL as `DATABASE_URL` with the database name changed to `signals_prototype_test`. Then run `python -m unittest discover -s tests -v` again.
