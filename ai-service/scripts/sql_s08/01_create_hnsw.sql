-- ============================================================
-- Sesión 08 · Bloque 2.2
-- Construcción del índice HNSW principal con vector_cosine_ops
-- ============================================================
--
-- Cómo ejecutarlo: psql corre en el contenedor de Postgres, que NO tiene
-- este archivo montado. Dos opciones desde la raíz del repo:
--
--   docker compose exec -T vector-db psql -U estimator -d estimator \
--     < ai-service/scripts/sql_s08/01_create_hnsw.sql
--
-- o abrir psql interactivo y pegar los bloques uno a uno (recomendado en el
-- directo, para comentar cada paso):
--
--   docker compose exec vector-db psql -U estimator -d estimator

-- Verificar el estado actual: NO debería haber índice vectorial todavía.
-- Solo veréis los índices relacionales del ejercicio (B-tree + GIN).
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname = 'chunks';

-- Construir el índice HNSW. Las tres decisiones que tomamos aquí:
--   · m = 16              → conexiones por nodo del grafo. Default razonable
--                           para 1536 dimensiones; subirlo mejora recall a
--                           costa de memoria y tiempo de construcción.
--   · ef_construction=128 → tamaño de la lista de candidatos al construir.
--                           Subido frente al default de pgvector (64): mejor
--                           grafo a cambio de un build más lento.
--   · vector_cosine_ops   → operator class de COSENO. Tiene que estar
--                           alineada con el operador <=> de las queries;
--                           si no lo está, Postgres ignora el índice
--                           EN SILENCIO (lo veremos en el Bloque 2.3).
CREATE INDEX chunks_embedding_idx
ON chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 128);

-- Verificar que el índice ya existe y cuánto ocupa.
-- Comparad su tamaño con el de la tabla: el HNSW no es gratis.
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname = 'chunks';
