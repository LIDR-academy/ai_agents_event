-- ============================================================
-- Sesión 08 · Reversión post-ensayo (test run → estado pre-sesión)
-- ============================================================
--
-- Devuelve la BBDD al ESTADO PRE-FLIGHT-COMPLETO (el punto de arranque del
-- directo): 30.000 chunks sintéticos + 60 reales cargados, SIN ningún índice
-- vectorial y sin overrides persistentes. NO re-embebe nada: conserva los 30k
-- del pre-flight para no repagar embeddings (~$0.03 / ~5-10 min).
--
-- Qué revierte (todo lo que el ensayo de la Sesión 08 deja tras de sí):
--   · los índices HNSW creados en B2.2 / B3.2 / B4.1 (vector y halfvec),
--   · los ~100 chunks sintéticos extra insertados en B5.2,
--   · el override global de ef_search si se fijó con ALTER DATABASE (B3.1),
--   · los contadores de pg_stat (idx_scan, last_analyze) para un "antes" limpio.
--
-- Qué NO toca:
--   · los 30.000 chunks sintéticos del pre-flight ni los 60 reales,
--   · el documento sintético que los agrupa,
--   · ningún archivo del repo ni migración Alembic (repository.py no se editó).
--
-- Cómo ejecutarlo (desde la raíz del repo, AL TERMINAR el test run):
--
--   docker compose exec -T vector-db psql -U estimator -d estimator \
--     < ai-service/scripts/sql_s08/99_revert_live_session.sql
--
-- Cuántos sintéticos conservar: por defecto 30000 (lo que inserta el pre-flight).
-- Si cargaste otra cantidad en el pre-flight, pásala al invocar:
--   ... psql ... -v keep_synthetic=50000 < .../99_revert_live_session.sql
--
-- Idempotente y fail-safe: si no hay índices, no borra nada; si hay <= keep_synthetic
-- sintéticos, no borra ningún chunk. Puede ejecutarse varias veces sin daño.

\if :{?keep_synthetic}
\else
  \set keep_synthetic 30000
\endif

\echo '== Estado ANTES de revertir =='
SELECT
    (SELECT count(*) FROM chunks)                              AS total_chunks,
    (SELECT count(*) FROM chunks WHERE chunk_type='synthetic') AS synthetic_chunks;
SELECT indexrelname FROM pg_stat_user_indexes WHERE relname='chunks' ORDER BY indexrelname;

-- 1. Borrar los chunks sintéticos insertados DURANTE la sesión (B5.2),
--    conservando los :keep_synthetic más antiguos (los del pre-flight). Se
--    distinguen por id (autoincrement): los del pre-flight tienen los ids más
--    bajos; cualquier sintético posterior es del ensayo. Si hay <= keep_synthetic
--    sintéticos, la subconsulta devuelve NULL y no se borra nada (fail-safe).
DELETE FROM chunks
WHERE chunk_type = 'synthetic'
  AND id > (
      SELECT id
      FROM chunks
      WHERE chunk_type = 'synthetic'
      ORDER BY id
      OFFSET (:keep_synthetic - 1)
      LIMIT 1
  );

-- 2. Dropear todos los índices vectoriales del ensayo (idempotente).
--    Cubre también un índice dejado INVALID por un CONCURRENTLY abortado (B3.2).
DROP INDEX IF EXISTS chunks_embedding_idx;
DROP INDEX IF EXISTS chunks_embedding_halfvec_idx;

-- 3. Revertir el override global de ef_search si se fijó con ALTER DATABASE (B3.1).
--    El SET / SET LOCAL de sesión NO persiste; esto sí. Inofensivo si no se fijó.
ALTER DATABASE estimator RESET hnsw.ef_search;

-- 4. Resetear estadísticas para un "antes" prístino: idx_scan vuelve a 0, de modo
--    que la demo del antipatrón en vivo (idx_scan=0 → índice ignorado, B2.3/B5.1)
--    arranca limpia. ANALYZE repuebla a continuación las estadísticas del planner.
SELECT pg_stat_reset();
ANALYZE chunks;

\echo '== Estado DESPUÉS de revertir (debe coincidir con pre-flight step 7/10) =='
SELECT
    (SELECT count(*) FROM chunks)                              AS total_chunks,
    (SELECT count(*) FROM chunks WHERE chunk_type='synthetic') AS synthetic_chunks,
    (SELECT count(*) FROM documents WHERE document_type='synthetic_test') AS synthetic_docs;
-- Esperado: solo índices relacionales (chunks_pkey, ix_chunks_*); NINGÚN hnsw.
SELECT indexrelname FROM pg_stat_user_indexes WHERE relname='chunks' ORDER BY indexrelname;
