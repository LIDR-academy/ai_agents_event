# Arquitectura del estimador

Este documento es el **contrato de arquitectura** del servicio. El código de cada sesión
nueva debe respetarlo. Si una pieza no encaja en ninguna capa, primero se decide dónde vive
(y se actualiza este documento), no se crea otra carpeta suelta en la raíz.

## 1. Las tres arquitecturas de IA (y por qué componen)

El estimador no es un único patrón de generación: a lo largo del máster apila tres,
**de forma aditiva**, sobre una base común:

- **CAG — Cache-Augmented Generation** (`app/generation/cag/`). Responde sin tocar el LLM
  cuando ya hay una respuesta equivalente: primero un acierto exacto (SHA-256), luego un
  acierto por similitud vectorial.
- **RAG — Retrieval-Augmented Generation** (`app/generation/rag/`). Convierte un corpus
  (presupuestos históricos) en chunks + embeddings y —a partir de la Sesión 8— los persiste
  y los recupera para enriquecer el prompt con conocimiento citable.
- **Agéntica** (`app/generation/agentic/`). Un bucle Actor-Crítico-Boss que itera y audita la
  estimación antes de aceptarla, apoyado en la conversación multi-turno
  (`app/generation/conversation/`).

**Tesis central**: estas tres capas no se conocen entre sí. **Componen únicamente a través del
conductor** (`app/domain/estimation_service.py`). Esa es la regla que evita que el proyecto
vuelva a degenerar en una sucesión de carpetas acopladas.

## 2. El pastel de capas

```
app/
├── main.py                 # factory FastAPI + lifespan
├── config.py               # settings singleton (get_settings, @lru_cache)
├── dependencies.py         # composition root: wiring de singletons
│
├── foundation/             # plomería sin opinión de arquitectura AI
│   ├── llm/                #   LLMWrapper (LiteLLM + Instructor) + runtime_config.py (overrides de modelo en Redis)
│   ├── prompts/            #   loader Jinja2 + plantillas versionadas (estimation/v1..v3, …)
│   ├── guardrails/         #   input (moderación+injection+PII) / output (filter de scope)
│   ├── attachments/        #   extracción de texto de PDF/DOCX subidos
│   └── persistence/        #   engine SQLAlchemy + repositories (jobs, mappings)
│
├── domain/                 # contrato + conductor
│   ├── schemas/            #   EstimationRequest/Result/Response (contrato con Rails)
│   ├── estimation_service.py  # EstimationService — punto de composición del pipeline fijo
│   └── graph/              #   S13: el flujo como StateGraph de LangGraph (otro conductor)
│
├── generation/             # las 3 arquitecturas que componen + substrato conversacional
│   ├── cag/                #   exact.py + semantic.py
│   ├── rag/                #   chunking/ + embedding/ + analysis/ + store/ + ingest_service.py + retriever.py
│   ├── agentic/            #   boss.py + critic.py
│   └── conversation/       #   models, store, metadata_extractor, tier_resolver, compression/
│
├── ingestion/              # pipeline batch (offline) que alimenta RAG
│   └── catalog/ loaders/ parsers/ cleaning/ pii/ documents/ orchestrator.py architecture.py
│
└── api/                    # transporte: routers finos, sin lógica de negocio
    ├── estimations.py      #   POST /api/v1/estimate
    ├── sessions.py         #   /sessions/*
    ├── ingestion.py        #   /api/v1/ingestion/*
    ├── embeddings.py       #   POST /embeddings/ingest (persiste desde S8) + /embeddings/compare
    ├── search.py           #   POST /search (búsqueda semántica, S8)
    ├── config.py           #   GET/PUT /api/v1/config/models (modelos en runtime)
    ├── security.py         #   X-API-Key por router (S9): retrieval / estimate
    └── service_token.py    #   X-Service-Token app-wide (S15), middleware
```

**Las dos capas de autenticación** viven ambas en `api/` porque las dos son transporte, pero no
son intercambiables:

- `security.py` son **dependencias por ruta** (`Depends(require_estimate_key)`): protegen lo que
  te acuerdas de proteger. Responden *qué* endpoints puede usar quien llama.
- `service_token.py` es **middleware**: envuelve la aplicación entera salvo `/health` y los docs.
  Responde *si* quien llama puede hablar con el servicio. Es middleware precisamente porque lo
  que quedó sin autenticar hasta la S15 fue lo que se le olvidó a alguien ruta por ruta.

Ninguna de las dos sustituye a la otra, y un token de servicio válido **no** abre los routers con
`X-API-Key` (lo fija `tests/api/test_service_token.py`).

## 3. Reglas de dependencias (MUST / MUST NOT)

De más-importado a menos. Cada capa **solo** puede importar de las que tiene por encima:

| Capa | PUEDE importar | NO PUEDE importar |
|---|---|---|
| `config.py` | (nada interno) | — |
| `foundation/*` | `config` | `domain`, `generation`, `ingestion`, `api` |
| `domain/schemas/*` | `config`, `foundation` | `generation`, `api` |
| `generation/<x>/*` | `config`, `foundation`, `domain/schemas` | `api`, `dependencies`, **otro hermano de `generation`** |
| `domain/estimation_service.py` (CONDUCTOR) | todos los hermanos de `generation` + `foundation` + `schemas` | `api`, `dependencies` |
| `ingestion/*` | `config`, `foundation`, `domain/schemas`, `generation/rag` | `api`, el conductor |
| `api/*` | `dependencies`, `domain` (schemas + conductor), excepciones de `foundation` | lógica de negocio |
| `dependencies.py` (COMPOSITION ROOT) | cualquier cosa | (lo importan solo `api/` y los tests) |
| `main.py` | `api`, `config` | — |

**Dos aristas especiales, explícitas:**
1. `agentic` **puede** importar `conversation` (lo agéntico se construye sobre el multi-turno).
   La inversa está **prohibida**.
2. Los hermanos de `generation` se encuentran **solo** en el conductor. Si dos capas necesitan
   colaborar, el método que las une va en `EstimationService`, nunca un import cruzado.

## 4. El conductor

`app/domain/estimation_service.py::EstimationService` es el único sitio donde se cablean las
capas. Sus tres entradas y qué tocan:

- `estimate()` — guardrails(in) → **cag** (exacto + semántico) → prompts → **llm** → guardrails(out) → cag.store
- `estimate_conversational()` — **conversation** (historial+metadata+compresión) → prompts → llm → guardrails(out)
- `estimate_with_acb()` — **agentic** (Boss orquesta Actor+Critic sobre la conversación)

Cuando RAG entre en el camino de petición (S8+), el paso de retrieval se añade **aquí**, como
un step más del conductor, no dentro de otra capa.

**Sesión 13 — el grafo también es conductor.** El grafo de LangGraph
(`app/domain/graph/`) re-expresa el flujo de estimación como cinco nodos secuenciales sobre un
estado tipado. Compone `generation/rag` (retrieval) + `foundation/llm` (generación), así que
vive **en `domain/`, junto a `estimation_service.py`** — es un conductor más, no un hermano de
`generation`. Sus nodos son funciones puras `state → actualización parcial` que se auto-cablean
las dependencias con imports locales `from app.dependencies import ...` (el mismo toque tolerado
que usa `app/generation/rag/agent_retrieval.py`), de modo que ninguna capa de `generation` se
importa entre sí. La persistencia (checkpointer `AsyncPostgresSaver` sobre el Postgres del
proyecto) y la observabilidad (Logfire, un span por nodo) se cablean en el `lifespan` de
`main.py`; el endpoint `POST /v1/estimate/graph` (`api/`) sólo invoca `app.state.graph`.

## 5. Composition root

`dependencies.py` y `config.py` viven en la **raíz**, por encima de las capas, a propósito: el
composition root tiene permiso para alcanzar cualquier capa (es su trabajo cablear), así que no
puede pertenecer a ninguna. Toda fábrica de singletons (`get_llm_wrapper`, `get_cache`,
`get_semantic_cache`, `get_session_store`, los chunkers, `get_estimation_service`) está aquí.

## 6. Camino de la petición principal

```
POST /api/v1/estimate
  └→ app/api/estimations.py                         (HTTP fino, mapeo de errores)
       └→ app/domain/estimation_service.py::estimate()
            1. app/foundation/guardrails/input.py     (moderación + injection + PII)
            2. app/generation/cag/exact.py            (acierto exacto SHA-256)
            3. app/generation/cag/semantic.py         (similitud vectorial redisvl)
            4. app/foundation/prompts/loader.py       (Jinja2 versionado)
            5. app/foundation/llm/wrapper.py          (Instructor + validators + re-prompt)
            6. app/foundation/guardrails/output.py    (filter de scope)
            7. cag.exact.set() + cag.semantic.store()
            8. return EstimationResponse(result, prompt_version, cached)
```

## 7. ¿Dónde va mi código nuevo?

| Si añades… | Va en… |
|---|---|
| Backend LLM, plantilla de prompt, guardrail nuevo | `foundation/` |
| Retrieval, chunking, vector store, embeddings | `generation/rag/` |
| Rol de agente o paso de orquestación | `generation/agentic/` |
| Estrategia de cache | `generation/cag/` |
| Lógica de memoria conversacional | `generation/conversation/` |
| Endpoint HTTP | `api/` (fino) + factory en `dependencies.py` |
| Composición entre capas | método en `EstimationService` (`domain/`), **nunca** import cruzado |
| Fuente de datos / parser / limpieza offline | `ingestion/` |

## 8. Contratos públicos que NO se rompen

- **Rutas HTTP**: `/api/v1/estimate`, `/sessions/*`, `/api/v1/ingestion/*`, `/embeddings/ingest`,
  `/search`, `/api/v1/config/models`.
  El cliente Rails (`business-backend`) depende de ellas y de la forma JSON de
  `EstimationResponse` / `ACBResponse`.
- **`EstimationResult`** (`domain/schemas/estimation.py`): el orden de campos importa para
  Instructor (`phases` antes que `total_cost_eur`/`total_duration_weeks`) y los dos
  `model_validator` (`phases_sum_matches_total`, `low_confidence_requires_out_of_scope_prefix`)
  son las reglas de negocio que disparan el re-prompt.

## 9. Roadmap (slots reservados)

- `generation/rag/store/` — persistencia pgvector. **Implementado en el previo de la Sesión 8**
  (modelos `documents`/`chunks` + repositorio async). El índice HNSW se añade en el directo.
- `generation/rag/retriever.py` — recuperación semántica. **Implementado en el previo de la
  Sesión 8** (k-NN por distancia coseno). El filtrado por metadatos/acceso se añade en el directo.
- `generation/rag/ingest_service.py` — orquestación chunk → embed → persist en una transacción
  (composición intra-RAG: chunker + embedder + store, permitida dentro del sibling).
- Integración del retriever en `EstimationService.estimate()` (RAG en el pipeline de
  estimación) — sesiones posteriores; la composición irá en el conductor, como manda la §7.

## Apéndice — Mapa de migración de rutas (vieja → nueva)

| Antes | Ahora |
|---|---|
| `app/services/estimation.py` | `app/domain/estimation_service.py` |
| `app/services/llm_wrapper.py` | `app/foundation/llm/wrapper.py` |
| `app/services/cache.py` | `app/generation/cag/exact.py` |
| `app/cache/semantic.py` | `app/generation/cag/semantic.py` |
| `app/services/boss.py` | `app/generation/agentic/boss.py` |
| `app/services/critic.py` | `app/generation/agentic/critic.py` |
| `app/guardrails/*` | `app/foundation/guardrails/*` |
| `app/prompts/*` | `app/foundation/prompts/*` |
| `app/attachments/*` | `app/foundation/attachments/*` |
| `app/persistence/*` | `app/foundation/persistence/*` |
| `app/schemas/*` | `app/domain/schemas/*` |
| `app/sessions/*` | `app/generation/conversation/*` |
| `app/embedding_pipeline/base.py` | `app/generation/rag/chunking/base.py` |
| `app/embedding_pipeline/chunker.py` | `app/generation/rag/chunking/structural.py` |
| `app/embedding_pipeline/strategies/*` | `app/generation/rag/chunking/strategies/*` |
| `app/embedding_pipeline/embedder.py` | `app/generation/rag/embedding/embedder.py` |
| `app/embedding_pipeline/{similarity,comparison}.py` | `app/generation/rag/analysis/*` |
| `app/embedding_pipeline/schemas.py` | `app/generation/rag/schemas.py` |
| `app/embedding_pipeline/router.py` | `app/api/embeddings.py` |
| `app/routers/*` | `app/api/*` |
