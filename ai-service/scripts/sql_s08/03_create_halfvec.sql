-- ============================================================
-- Sesión 08 · Bloque 4.1
-- Creación del índice halfvec en paralelo al vector original
-- ============================================================
--
-- halfvec almacena cada componente en 16 bits (half precision) en vez de 32.
-- La columna embedding NO cambia (sigue siendo vector(1536), full precision
-- en la tabla): solo el ÍNDICE se construye sobre la expresión casteada.
-- Resultado: un índice ~50% más pequeño que, para embeddings normalizados
-- como los de OpenAI, devuelve resultados indistinguibles.

-- Tamaño del índice vector actual, para tener la referencia delante.
SELECT pg_size_pretty(pg_relation_size('chunks_embedding_idx')) AS vector_index_size;

-- Crear el índice halfvec sobre la expresión (embedding::halfvec(1536)).
-- Ojo a los dos detalles:
--   · el doble paréntesis: es un índice de EXPRESIÓN, no de columna.
--   · halfvec_cosine_ops: la operator class tiene que ser la de halfvec.
-- Y la consecuencia que no hay que olvidar: este índice solo sirve a
-- queries que ordenen por LA MISMA expresión casteada. Una query que
-- ordene por `embedding <=> ...` (sin cast) NO lo usará.
CREATE INDEX chunks_embedding_halfvec_idx
ON chunks
USING hnsw ((embedding::halfvec(1536)) halfvec_cosine_ops)
WITH (m = 16, ef_construction = 128);

-- Comparar los tamaños de ambos índices, lado a lado.
-- Esperado: el halfvec ocupa aproximadamente la mitad.
SELECT
    indexrelname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname = 'chunks'
  AND indexrelname LIKE '%embedding%';

-- Verificación rápida de que el índice halfvec responde: misma query top-5
-- con la expresión casteada. En el plan debería aparecer
-- "Index Scan using chunks_embedding_halfvec_idx".
EXPLAIN ANALYZE
SELECT id, chunk_type
FROM chunks
ORDER BY (embedding::halfvec(1536)) <=> (
    SELECT embedding::halfvec(1536) FROM chunks ORDER BY id LIMIT 1
)
LIMIT 5;
