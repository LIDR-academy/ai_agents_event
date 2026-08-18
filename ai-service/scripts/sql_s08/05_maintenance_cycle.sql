-- ============================================================
-- Sesión 08 · Bloque 5.2
-- Ciclo de mantenimiento: ANALYZE, VACUUM, REINDEX CONCURRENTLY
-- ============================================================
--
-- Contexto del directo: acabamos de insertar ~100 chunks sintéticos con
-- insert_synthetic_chunks_s08.py para simular el crecimiento del corpus.
-- Ahora toca el ciclo que mantiene la BBDD vectorial sana en producción.
--
-- OJO: REINDEX ... CONCURRENTLY y VACUUM no pueden ejecutarse dentro de una
-- transacción. En psql interactivo (autocommit) no hay problema; si pegáis
-- todo el archivo de golpe, psql lo ejecuta sentencia a sentencia y también
-- funciona.

-- Estado previo: filas vivas/muertas y cuándo se actualizaron estadísticas.
-- Tras la inserción sintética, last_analyze estará desfasado.
SELECT n_live_tup, n_dead_tup, last_analyze, last_vacuum
FROM pg_stat_user_tables
WHERE relname = 'chunks';

-- Paso 1 — ANALYZE: recalcula las estadísticas que usa el planner.
-- Rápido y no bloqueante. Tras una carga grande, SIEMPRE.
ANALYZE chunks;

-- Comprobad que last_analyze acaba de cambiar:
SELECT n_live_tup, n_dead_tup, last_analyze
FROM pg_stat_user_tables
WHERE relname = 'chunks';

-- Paso 2 — VACUUM ANALYZE: además de estadísticas, recupera el espacio de
-- las filas muertas (updates/deletes). En esta tabla casi todo son inserts,
-- así que habrá poco que recuperar — lo importante es el hábito.
VACUUM ANALYZE chunks;

-- Paso 3 — REINDEX CONCURRENTLY: reconstruye el índice HNSW sin bloquear
-- las queries. Por debajo: construye un índice paralelo nuevo, espera a que
-- esté listo y hace un swap atómico con el viejo. Cuesta el doble de espacio
-- temporal y más tiempo que un REINDEX normal, pero el servicio no se entera.
-- En producción: ventana de bajo tráfico.
REINDEX INDEX CONCURRENTLY chunks_embedding_halfvec_idx;

-- Verificar que el índice sigue activo y operativo tras el swap.
-- (En Postgres 16, REINDEX CONCURRENTLY conserva las estadísticas de uso:
-- idx_scan NO se resetea — el contador acompaña al índice nuevo.)
SELECT
    indexrelname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS size,
    idx_scan
FROM pg_stat_user_indexes
WHERE relname = 'chunks';

-- Cadencias orientativas en producción:
--   · ANALYZE   → lo dispara autovacuum solo; manual tras cargas masivas.
--   · VACUUM    → semanal (o autovacuum bien configurado).
--   · REINDEX   → mensual, o ante degradación observada de recall/latencia
--                 (el grafo HNSW se va degradando con muchos updates/deletes).
