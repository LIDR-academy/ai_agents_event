-- ============================================================
-- Sesión 08 · Bloque 5.1
-- Queries de monitorización del estado de los índices
-- ============================================================
--
-- Estas queries son las que os lleváis a producción. La primera es LA query
-- canónica: copiadla a vuestro README y ejecutadla cada vez que algo vaya
-- "inexplicablemente lento".

-- Query canónica: estado de todos los índices sobre chunks.
--   · idx_scan      → cuántas veces se ha usado el índice.
--   · last_idx_scan → cuándo fue la última vez (Postgres 16+).
--   · size          → cuánto ocupa.
SELECT
    indexrelname AS index_name,
    idx_scan AS scans,
    last_idx_scan AS last_used,
    pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname = 'chunks'
ORDER BY idx_scan DESC;

-- Estadísticas de actividad sobre la tabla: filas vivas vs muertas y cuándo
-- se recalcularon las estadísticas por última vez. Muchas filas muertas =
-- toca VACUUM; last_analyze antiguo tras una carga grande = toca ANALYZE.
SELECT
    relname,
    n_live_tup AS live_rows,
    n_dead_tup AS dead_rows,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
WHERE relname = 'chunks';

-- Si un índice vectorial tiene idx_scan = 0 después de servir queries
-- semánticas, casi seguro es el antipatrón operador/operator class del
-- Bloque 2.3. Para verificar la operator class de cada índice vectorial:
SELECT
    i.indexrelname,
    op.opcname AS operator_class
FROM pg_stat_user_indexes i
JOIN pg_index pgi ON pgi.indexrelid = i.indexrelid
JOIN pg_opclass op ON op.oid = ANY (pgi.indclass::oid[])
WHERE i.relname = 'chunks'
  AND (op.opcname LIKE 'vector%' OR op.opcname LIKE 'halfvec%');

-- Complementos fuera de psql (mencionados, no demostrados):
--   · pg_stat_statements → ranking de queries por tiempo acumulado.
--   · Los logs estructurados del servicio (structlog, en el stack desde la
--     Sesión 03): el evento rag_search_done lleva search_time_ms — sirve
--     para correlacionar picos de latencia end-to-end con los planes de aquí.
