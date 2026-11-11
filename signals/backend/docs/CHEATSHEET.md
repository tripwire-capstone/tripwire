# Tripwire dev cheat sheet

Commands for day-to-day work on the Tripwire backend. Run them from `signals/backend/` unless a section says otherwise.

## Run the backend

| To do this | Run |
| --- | --- |
| Start everything (add `-d` to run in the background) | `docker compose up --build` |
| Follow a service's logs | `docker compose logs -f poller` (or `runner`, `clusterer`, `db`) |
| Restart one service after a code change | `docker compose up -d --build clusterer` |
| **Wipe the database**: after a new migration, or on `relation "…" does not exist`. Deletes local data. | `docker compose down -v && docker compose up --build` |

**Before opening a PR:** `source .venv/bin/activate && pytest -q` (database running).

## Generate traffic

**tau-traffic**, from `traffic/tau-bench/` at the repo root (first time: see its [README](../../../traffic/tau-bench/README.md)):

| To do this | Run |
| --- | --- |
| Wizard: asks everything, prints the command to repeat | `./tau-traffic` |
| 3 retail conversations now | `./tau-traffic --domain retail -n 3 --yes` |
| 40 conversations spread over 8 hours | `./tau-traffic -n 40 --over 8h --yes` |

**park-traffic**, only when you need specific planted bugs: [PARK_TRAFFIC.md](PARK_TRAFFIC.md)

Check it arrived with the `check.sql` report below. Nothing new? The generator's `LMNR_PROJECT_API_KEY` must match the one in `signals/backend/.env`.

## Database

| To do this | Run |
| --- | --- |
| Open psql | `docker compose exec db psql -U tripwire -d tripwire` |
| "Is data flowing?" report | `docker compose exec -T db psql -U tripwire -d tripwire < check.sql` |

```sql
-- Newest events with their signal
SELECT e.id, s.name, e.severity, e.description, e.created_at
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
JOIN signals s ON s.id = v.signal_id
ORDER BY e.created_at DESC LIMIT 10;

-- Summaries by status and error kind (pending + service = waiting on OpenAI)
SELECT status, error_kind, count(*) FROM event_summaries GROUP BY 1, 2 ORDER BY 1, 2;

-- Clusters with their sizes
SELECT c.id, c.name, count(m.event_id) AS events
FROM clusters c LEFT JOIN cluster_members m ON m.cluster_id = c.id
GROUP BY c.id ORDER BY events DESC;
```
