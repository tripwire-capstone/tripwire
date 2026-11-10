-- Is data flowing? Run with:
--   docker compose exec -T db psql -U tripwire -d tripwire < check.sql

\echo '== Rows per table'
SELECT 'traces' AS table_name, count(*) FROM traces
UNION ALL SELECT 'signal_runs', count(*) FROM signal_runs
UNION ALL SELECT 'signal_events', count(*) FROM signal_events
UNION ALL SELECT 'event_embeddings', count(*) FROM event_embeddings
UNION ALL SELECT 'cluster_members', count(*) FROM cluster_members;

\echo '== Poller: newest trace and last successful poll, per project'
SELECT p.name, max(t.ended_at) AS newest_trace_ended, p.last_polled_at
FROM projects p LEFT JOIN traces t ON t.project_id = p.id
GROUP BY p.id ORDER BY p.id;

\echo '== Runner: traces still waiting for a run (should drop to 0 within seconds)'
SELECT count(*) AS waiting FROM traces t
WHERE NOT EXISTS (SELECT 1 FROM signal_runs r WHERE r.trace_id = t.id);

\echo '== Per signal: runs, events, clustered, in the pool'
SELECT s.name,
       count(DISTINCT r.id) AS runs,
       count(DISTINCT e.id) AS events,
       count(DISTINCT cm.event_id) AS clustered,
       count(DISTINCT m.event_id) - count(DISTINCT cm.event_id) AS in_pool
FROM signals s
JOIN signal_versions v ON v.signal_id = s.id
LEFT JOIN signal_runs r ON r.signal_version_id = v.id
LEFT JOIN signal_events e ON e.run_id = r.id
LEFT JOIN event_embeddings m ON m.event_id = e.id
LEFT JOIN cluster_members cm ON cm.event_id = e.id
GROUP BY s.id ORDER BY s.id;
