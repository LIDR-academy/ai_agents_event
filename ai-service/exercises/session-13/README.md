# Sesión 13 — El flujo de estimación como grafo de LangGraph

En la Sesión 12 el flujo de estimación era un **bucle agéntico hecho a mano** sobre la Responses
API (razona → actúa → observa). Funciona, pero se vuelve incómodo en cuanto hay varios pasos, ramas
condicionales o vuelta atrás.

En esta sesión ese flujo pasa a ser un **grafo explícito de LangGraph** que vive **dentro del
servicio IA**. De puertas afuera no cambia nada: el servicio sigue recibiendo una transcripción y
devolviendo una estimación estructurada con su `status`. El backend de negocio (Rails) ni se entera
de qué hay debajo.

Alcance de la pre-sesión: **Niveles 1 y 2 (obligatorios)** + **Nivel 3 (opcional)**. Queda **fuera**
(lo vemos en el directo): paralelismo con Send API, manejo de errores avanzado, intervención humana
(`interrupt()`) y optimización a partir de la traza. Por eso el grafo corre **en secuencial**.

## El grafo

```
START → extract_requirements → classify_components → search_budgets
      → generate_estimate → validate_and_consolidate → (arista condicional) → END
```

- `extract_requirements`: transcripción → lista de requisitos (LLM estructurado, `LLMWrapper`).
- `classify_components`: requisitos → componentes con su categoría (LLM estructurado).
- `search_budgets`: para cada componente, recupera presupuestos de referencia **uno tras otro**
  (reutiliza el `retrieve()` real de S9/S10 sobre `chunk_type='historical_task'`).
- `generate_estimate`: consolida los presupuestos en una estimación (LLM estructurado, anclado en
  las horas históricas recuperadas).
- `validate_and_consolidate`: guardrails deterministas + fija el `status` de salida.

## Solución de referencia (en este repo)

| Fichero | Qué es |
|---|---|
| `app/domain/graph/state.py` | El **estado tipado** (`TypedDict`) con dos reducers acumuladores (`budget_matches`, `errors`) vía `Annotated[..., operator.add]`. |
| `app/domain/graph/nodes.py` | Los **cinco nodos** como funciones puras (`state → actualización parcial`), cada uno envuelto en `logfire.span("node: …")`. |
| `app/domain/graph/build.py` | `build_graph(checkpointer)` — cablea y compila el grafo (Nivel 1) con la arista condicional del Nivel 3. |
| `app/domain/graph/checkpointer.py` | El **checkpointer** `AsyncPostgresSaver` sobre el Postgres del proyecto (reutiliza `DATABASE_URL`). |
| `app/domain/graph/observability.py` | Configuración de **Logfire** (un span por nodo; no-op sin token). |
| `app/domain/schemas/graph_estimation.py` | El contrato HTTP (request/response). |
| `app/api/routers/estimate_graph.py` | `POST /v1/estimate/graph` — el endpoint (mismo contrato de siempre). |
| `scripts/run_graph_s13.py` | Ejecuta el grafo e imprime la traza/estado. |

El grafo se construye en el `lifespan` de `app/main.py` (con el checkpointer) y se guarda en
`app.state.graph`; el endpoint lo invoca con `thread_id = estimation_id`.

## Cómo ejecutar

```bash
# Smoke parcialmente offline: sin Postgres (MemorySaver) y con retrieval enlatado.
# Sólo necesita OPENAI_API_KEY para los nodos LLM (extract/classify/generate).
uv run python scripts/run_graph_s13.py --memory --stub

# Ejecución real (entregable): stack arriba + corpus de tareas ingerido.
docker compose exec ai-service python scripts/build_task_corpus.py --ingest
docker compose exec ai-service python scripts/run_graph_s13.py \
    --out exercises/session-13/example_run_complex.txt
```

`--memory` usa un `MemorySaver` en memoria en vez del checkpointer de Postgres. `--stub` cambia el
retrieval real por el stub offline (`exercises/session-12/reference_retrieval.py`).

### La traza de Logfire (Nivel 2)

Con `LOGFIRE_TOKEN` en el entorno (token de escritura en <https://logfire.pydantic.dev>), cada
ejecución exporta **un span por nodo** dentro de la traza de la petición. Sin token, los spans se
ejecutan igual pero no se exportan, así que el servicio funciona en cualquier caso.

```bash
LOGFIRE_TOKEN=pylf_v1_... docker compose exec -e LOGFIRE_TOKEN estimator \
    python scripts/run_graph_s13.py
```

El checkpointer crea sus tablas (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) en el
Postgres del proyecto en el primer arranque (`await checkpointer.setup()`), conviviendo con las de
embeddings.

## Criterios de "hecho"

- [x] El grafo corre de principio a fin y el endpoint devuelve la estimación con su `status`; el
      contrato hacia el backend de negocio es el de siempre (`POST /v1/estimate/graph`).
- [x] El estado es tipado y tiene reducers acumuladores (`budget_matches`, `errors`).
- [x] Los cinco nodos son funciones puras que devuelven actualizaciones parciales.
- [x] El checkpointer persiste en el Postgres del proyecto y cada ejecución lleva su `thread_id`.
- [x] Existe una traza completa con un span por nodo (Logfire).

## Tests

Sin red y sin clave (`tests/domain/graph/`): el grafo se ejecuta de punta a punta con `MemorySaver`
y dobles del `LLMWrapper` + backend de retrieval.

```bash
uv run pytest tests/domain/graph -v
```

## En el directo (rama `session_13_live`)

El directo hace crecer este grafo secuencial de 5 nodos hasta un **pipeline multi-agente** con dos
handovers explícitos y dos puertas humanas. Los nodos pasan a ser agentes especializados en
`app/domain/graph/agents/`:

```
classifier_agent  ─Command(goto)─▶  structure_agent  ─▶  🧑 puerta 1 (interrupt)
   ─Send fan-out por tarea─▶  estimate_task_hours ×N  ─join─▶  recover_and_handover
   ─Command(goto)─▶  analysis_agent  ─▶  🧑 puerta 2 (interrupt)  ─cond─▶  proposal_agent | END
```

Novedades que se explican en la sesión (todo en `app/domain/graph/`):

- **Handovers** con `Command(goto=…, update=…)` (`classifier→structure`, `recover→analysis`): pasan
  control **y** estado.
- **Puertas humanas** con `interrupt()` (en `agents/gates.py`), reanudadas por el backend de negocio
  con `Command(resume=…)`. El estado sobrevive a la pausa gracias al checkpointer.
- **Fan-out** por tarea con la Send API + un reducer **keyed** (`merge_task_hours`) idempotente ante
  reanudación (evita el doble-append).
- **Recuperación agéntica** de tareas dudosas en el join (reutiliza `run_task_hours_recovery_agent`).
- **Endpoints** `POST /v1/estimate/graph` (arranque) + `POST …/{id}/resume` + `GET …/{id}/state`.
- **Wizard de grafo en Rails** (`business-backend`) que arranca y reanuda las dos puertas.

Recórrelo entero (auto-aprobando las puertas) con:

```bash
docker compose exec ai-service python scripts/run_graph_s13.py --out data/example_run_complex.txt
# o offline (sin DB, horas enlatadas; necesita OPENAI para los agentes LLM):
uv run python scripts/run_graph_s13.py --memory --stub
```

Guía del instructor: `guides/session-13-live-guide.md`.
