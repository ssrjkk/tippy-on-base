\pset pager off
SELECT pid, state, wait_event_type, wait_event,
       now() - xact_start  AS xact_age,
       now() - query_start AS query_age,
       left(regexp_replace(query, '\s+', ' ', 'g'), 90) AS query
FROM pg_stat_activity
WHERE datname IS NOT NULL
ORDER BY xact_start NULLS LAST;

SELECT l.pid, l.locktype, l.mode, l.granted,
       COALESCE(c.relname, t.typedname, '(db)') AS object
FROM pg_locks l
LEFT JOIN pg_class c ON c.oid = l.relation
LEFT JOIN (SELECT oid, 'toast ' || oid AS typedname FROM pg_class WHERE relkind = 't') t ON t.oid = l.relation
WHERE NOT l.granted
ORDER BY l.pid;

SELECT pid, pg_blocking_pids(pid) AS blocked_by
FROM pg_stat_activity
WHERE cardinality(pg_blocking_pids(pid)) > 0;
