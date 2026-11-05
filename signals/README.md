# Signals prototype

A Signals feature (named behaviors detected in LLM conversations, like User Frustration) built on top of an
unmodified, self-hosted [Laminar](https://github.com/lmnr-ai/lmnr). We talk to Laminar only as a service:
traces go in through its SDK, come back out through its SQL API, and our own pipeline classifies, stores and shows them.

## Layout

| Folder | What's in it |
| --- | --- |
| `sync/` | Track A: poll Laminar's SQL API and reconstruct conversations |
| `classifier/` | Track B: classify conversations, store results, show them |
| `backend/` | Read-only Python API for saved evaluation results |
| `frontend/` | React evaluation dashboard; see its README for setup |
| `docs/` | The seam contract (conversation shape) and decisions |
| `eval/labeling/` | Terminal tools for hand-labeling the evaluation sample |
| `eval/labels/` | Our label files and agreement reports |
| `eval/sample-key.csv` | Scenario and dev/test split for each sampled turn; open only after labeling |

The synthetic traffic comes from a separate app (park_demo), which plays the customer whose traces we read.

## Labeling the sample

See [`eval/labeling/README.md`](eval/labeling/README.md).

## Evaluation dashboard

See [`frontend/README.md`](frontend/README.md) to configure Postgres, save classifier scores, and run the API and React page. The dashboard loads results automatically without file uploads.

## Run everything with Docker

You need [Docker with Compose](https://docs.docker.com/compose/install/) and a running, self-hosted Laminar.

1. **Laminar.** Install it with its [Docker Compose guide](https://laminar.sh/docs/self-hosting/docker-compose.md)
   (`git clone https://github.com/lmnr-ai/lmnr && cd lmnr && docker compose up -d`). The first start runs
   migrations for a few minutes. Open http://localhost:5667, create a project, and under
   Settings -> Project API Keys create a **Default** key (sync needs read access to the SQL API).
2. **`.env`.** `cp .env.example .env`, then fill in `OPENAI_API_KEY`, `LMNR_PROJECT_API_KEY` and
   `LMNR_UI_PROJECT_URL` (the project's address in the Laminar UI, up to the project ID).
3. **Start the pipeline** (database, sync, classifier, API, web page):
   ```bash
   docker compose up -d --build
   ```
   Open http://localhost:5173. Logs: `docker compose logs -f sync` (or `classifier`, `api`, `web`).
4. **Send traffic** with [park-traffic](https://github.com/vkarklins/park-traffic). Set it up once per its README
   (its own virtual environment, `pip install -r requirements.txt`, and a `.env` with the same Laminar key).
   Then, in its folder with its virtual environment active:
   `python -m harness.run --n 8 --mix baseline --bugs all --seed 2 --label demo`.
   Turns show up on the Live tab about 30-60 s after they finish.

How it fits together: our Python services join Laminar's Docker network (`lmnr_default`) and reach its API
as `app-server`. If you see `network lmnr_default not found`, Laminar isn't running; start it first.

After a reboot, run `docker compose up -d` again, first in Laminar's folder, then in this one.
After changing Python code, rebuild that service: `docker compose up -d --build sync`. The web page reloads
by itself. To work on one piece directly, stop its service (`docker compose stop sync`) and run it as below.

### Ports

| Port | What |
| --- | --- |
| 5667 | Laminar UI |
| 8000 / 8001 | Laminar API (HTTP / gRPC) |
| 5433 | Laminar's own Postgres |
| 5434 | Our Postgres (`turns`, `results`) |
| 8100 | Our API |
| 5173 | Our web page |

## Running pieces directly (without Docker)

Create a virtual environment and `pip install -r requirements-dev.txt -r sync/requirements.txt`.
With `.env` filled in and the database running (`docker compose up -d --wait db`), each piece runs from the repo root:

```bash
python -m sync.run
python -m classifier.live
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8100
cd frontend && npm install && npm run dev
```

See [`frontend/README.md`](frontend/README.md) for the evaluation dashboard, the evaluator and the checks.
