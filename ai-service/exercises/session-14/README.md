# Sesión 14 — Sistema multi-agente: supervisor, mínimo privilegio e intervención humana

En la Sesión 13 el flujo de estimación pasó a ser un **grafo explícito**: nodos, estado tipado,
checkpointer, trazas. Funciona. Y para un proceso con una secuencia estable —estimar software, en su
forma canónica, la tiene— **el grafo lineal es la respuesta correcta**.

Esta sesión cruza una frontera concreta. No la de "más nodos con nombres más ambiciosos" (eso es
teatro de arquitectura, y se paga en latencia, en coste por token y en madrugadas depurando). La
frontera real es esta:

> **Quien decide qué se ejecuta a continuación deja de ser el código y pasa a ser el modelo.**

Eso es lo que separa un *workflow* de un sistema *agéntico*. No la cantidad de nodos: quién es dueño
del control flow.

## Lo que ya tenías (y no se toca)

Un sistema multi-agente aquí **no es infraestructura nueva**. Es el grafo de la S13 reorganizado:

- El **estado tipado** de la S13 es la pizarra compartida. `SupervisorState` **hereda** de
  `EstimationState`, así que los reducers acumuladores (`budget_matches`, `errors`) vienen ya
  puestos.
- El **checkpointer** (`AsyncPostgresSaver`) de la S13 es lo que permite pausar y reanudar. Los dos
  grafos lo comparten; el router namespacea los `thread_id` como `s14:<estimation_id>` para que los
  estados no se mezclen.
- Las **tools** son las de la S12. No se crean tools nuevas: **se reparten**.

El flujo S13-live (`classifier → structure → gates → fan-out`) y sus endpoints quedan **intactos**.
Lo de esta sesión convive con ellos.

> **Nota sobre nombres.** El enunciado llama `calculate_estimate` a la tool de cálculo. En este repo
> esa tool se llama **`derive_task_hours`** (consenso ponderado por distancia sobre los análogos
> históricos, sin LLM); `calculate_estimate` solo existe como esqueleto del alumno en
> `exercises/session-12/calculate_estimate_skeleton.py`. Es la misma función con otro nombre.

## Nivel 1 — Supervisor + agentes especializados

El grafo lineal de cinco nodos se reorganiza en una topología **supervisor / workers**. La forma es
una **estrella**, que es lo que debería parecer un sistema con supervisor cuando lo dibujas:

```
        START
          │
          ▼
   ┌─▶ supervisor ──Command(goto)──┬──▶ requirements_extractor ──┐
   │                               ├──▶ budget_searcher ─────────┤
   │                               ├──▶ estimate_generator ──────┤
   └──────── aristas de vuelta ────┼──▶ coherence_validator ─────┘
            (estáticas)            │
                                   └──▶ human_review_gate ──▶ END
```

Las cinco aristas `supervisor → {agentes, gate}` **no existen en la definición del grafo**:
`Command(goto=...)` las dibuja en runtime. Ese es el punto.

### Privilegio de tools (mínimo privilegio)

| Agente | Tools que puede usar |
|---|---|
| `requirements_extractor` | **ninguna** (solo el modelo) |
| `budget_searcher` | `search_budgets` |
| `estimate_generator` | `derive_task_hours` (la tool de "cálculo") |
| `coherence_validator` | `validate_estimate` |
| `supervisor` | **ninguna**: solo enruta |

Esto no es solo higiene de seguridad. El artículo lo dice: la tasa de elección incorrecta de tool
sube con el número de opciones. Aquí **cada agente ve como mucho UNA tool**, así que no hay nada que
equivocar. La propiedad de seguridad y la de precisión salen del mismo reparto.

Y es **exigible**, no documentación: `guarded_dispatch` comprueba la allowlist **antes** de ejecutar.
El extractor no importa siquiera el camino de tools.

### El enrutado es del modelo, pero enjaulado

`supervisor.py` llama al LLM con un `SupervisorDecision` (`next_agent` constreñido a un `Literal` +
`reason`). Tres frenos deterministas deciden si la elección del modelo sobrevive:

1. **Presupuesto de pasos** (`SUPERVISOR_MAX_STEPS`). Aristas cíclicas + router LLM es exactamente
   cómo un grafo hace ping-pong para siempre.
2. **Guarda de legalidad**. Rechaza un destino cuyas entradas no existen todavía, y re-visitas de un
   agente que ya actuó.
3. **Fallback determinista**. Si el modelo falla o propone algo ilegal, se cae a la escalera de
   dependencias. El grafo termina correctamente **aunque el LLM esté completamente roto** — que es
   además lo que permite que los tests no toquen la red.

Cada decisión se escribe en `routing_history` con su razón y su `source` (`llm` / `fallback` /
`limit`). Una decisión que nadie puede inspeccionar es un acto de fe.

> **Sin `temperature`.** `LLMWrapper.complete_structured` no expone ese parámetro, y añadirlo tocaría
> `foundation`, de la que dependen todas las sesiones. El presupuesto de determinismo se gasta en el
> schema constreñido, la guarda de legalidad y un digest de estado corto y factual.

## Nivel 2 — Intervención humana

La puerta de la S13 pausa **siempre**. Ésta pausa ante una **señal**: el grafo corre solo cuando los
números están bien anclados, y para exactamente cuando no. Una puerta que salta siempre es un
formulario, no un control.

Tres condiciones de disparo (basta una):

1. **Confianza baja** — por debajo de `SUPERVISOR_CONFIDENCE_THRESHOLD` (0.6 por defecto).
2. **Fuera de rango histórico** — algún componente se sale del rango que implican sus referencias.
3. **Sin precedente** — menos de `SUPERVISOR_MIN_GROUNDED_RATIO` de los componentes tiene análogo.

La confianza es **determinista**, calculada en el validador: no se fía del `confidence` que el
propio LLM se autoinforma, sino que lo escala por la fracción realmente anclada y lo penaliza por
cada issue de los guardarraíles.

El validador escribe **hechos**; la puerta es dueña del **veredicto**. Ese reparto es lo que permite
mover el umbral por configuración sin tocar el validador.

Cuando salta, el endpoint devuelve `status = "awaiting_human_review"` en vez de la estimación final,
y el estado queda persistido en el checkpoint. El endpoint de reanudación lo continúa con la
decisión (`approve` / `adjust` / `reject`).

### La disciplina del `interrupt()`

`interrupt()` **re-ejecuta el nodo entero** al reanudar. Por eso la puerta llama a `interrupt()` lo
primero, antes de cualquier escritura. Los reducers son *keyed* (idempotentes) como red de
seguridad, pero apoyarse en ellos para arreglar un orden malo esconde el bug en vez de corregirlo.

## Nivel 3 — Validación de acciones y auditoría

- **Validación antes de ejecutar**: `guarded_dispatch` rechaza y registra cualquier tool fuera de la
  allowlist declarada. `dispatch_tool` **no llega a llamarse**.
- **Auditoría**: cada acción (de modelo o de tool, incluidas las denegadas) deja una fila en
  `agent_contributions` y un evento `agent_action` en structlog. Un run completo se reconstruye
  desde el log:

```bash
docker compose logs estimator | jq -c \
  'select(.event == "agent_action" and .estimation_id == "<id>")
   | [.step, .agent, .tool, .outcome, .result_summary]'
```

Para ver una denegación real en la traza, sin meter una llamada incorrecta en el código de
producción:

```bash
uv run python scripts/run_supervisor_s14.py --memory --stub --violate
# busca la fila [DENIED] en el bloque AUDIT TRAIL, y el evento agent_privilege_denied en el log
```

## Solución de referencia (en este repo)

| Fichero | Qué es |
|---|---|
| `app/domain/graph/supervisor/state.py` | `SupervisorState` **heredado** del de la S13 + los dos acumuladores *keyed* (`agent_contributions`, `routing_history`). |
| `app/domain/graph/supervisor/privilege.py` | La tabla `AGENT_PRIVILEGES`, `PrivilegeViolation` y `guarded_dispatch` (comprobar → ejecutar → auditar). **Nivel 3**. |
| `app/domain/graph/supervisor/agents.py` | Los cuatro especialistas como funciones puras, reutilizando prompts y guardarraíles de `graph/nodes.py`. |
| `app/domain/graph/supervisor/supervisor.py` | El enrutador hecho a mano: `Command(goto=…)` + los tres frenos. |
| `app/domain/graph/supervisor/gate.py` | `review_reasons` (las tres condiciones, función pura) + `human_review_gate` (`interrupt()`). **Nivel 2**. |
| `app/domain/graph/supervisor/build.py` | `build_supervisor_graph(checkpointer)` — la estrella, con `destinations=` explícito. |
| `app/domain/schemas/supervisor_estimation.py` | El contrato HTTP. |
| `app/api/routers/estimate_supervisor.py` | Los tres verbos (START / resume / state). |
| `scripts/run_supervisor_s14.py` | Ejecuta el flujo e imprime enrutado + privilegio + auditoría + revisión. |

El grafo se construye en el `lifespan` de `app/main.py` compartiendo el checkpointer de la S13, y se
guarda en `app.state.supervisor_graph`.

## Cómo ejecutar

```bash
# Smoke offline: sin Postgres (MemorySaver) y con retrieval enlatado.
# Solo necesita OPENAI_API_KEY para el router y los agentes LLM.
uv run python scripts/run_supervisor_s14.py --memory --stub

# Contraste (ver el aviso sobre el corpus offline más abajo).
uv run python scripts/run_supervisor_s14.py --memory --stub \
    --transcript exercises/session-14/sample_transcript_happy_path.txt

# Nivel 3: una denegación real en la traza.
uv run python scripts/run_supervisor_s14.py --memory --stub --violate

# Ejecución real (entregable): stack arriba + corpus ingerido.
docker compose exec ai-service python scripts/build_task_corpus.py --ingest
docker compose exec ai-service python scripts/run_supervisor_s14.py \
    --out exercises/session-14/example_run_edge_case.txt
```

### Paseo del human-in-the-loop con HTTP

```bash
# START → status "awaiting_human_review" + pending_review.reasons
http POST :8000/v1/estimate/supervisor X-API-Key:$ESTIMATE_API_KEY \
     transcript=@exercises/session-14/sample_transcript_edge_case.txt

# STATE → la pausa sobrevive (persistida en el checkpoint)
http GET :8000/v1/estimate/supervisor/<id>/state X-API-Key:$ESTIMATE_API_KEY

# RESUME → continúa hasta el final con la decisión humana
http POST :8000/v1/estimate/supervisor/<id>/resume X-API-Key:$ESTIMATE_API_KEY \
     decision=approve note="revisado con el cliente"

# RESUME otra vez → 409 (ya no hay nada pendiente)
```

## Material de esta carpeta

| Fichero | Para qué |
|---|---|
| `sample_transcript_edge_case.txt` | Diseñada para **disparar la pausa**: un dominio sin precedente (QKD, mainframe COBOL de Aduanas), alcance contradictorio ("es un formulario" vs. 400.000 eventos/s + SOC2 + 6 idiomas) y un componente desproporcionado frente a sus análogos (una pantalla de login con driver biométrico propio y HSM). |
| `sample_transcript_happy_path.txt` | Un portal de proveedores corriente: alcance cerrado, componentes con análogos. Pensada como contraste. |
| `example_run_edge_case.txt` | La traza comprometida del entregable (ejecutada con `--memory --stub`). |

> **Aviso honesto sobre el corpus offline.** Con `--stub`, el corpus enlatado de
> `exercises/session-12/reference_retrieval.py` es pequeño y genérico: casi cualquier estimación
> sale con `grounded_ratio` bajo y **las dos transcripciones acaban pausando** (confianza ~0.40
> frente al umbral 0.60). El contraste real —una que pausa y otra que no— necesita el corpus
> histórico ingerido (`scripts/build_task_corpus.py --ingest`). En la traza comprometida verás
> disparada **una** condición (confianza baja), no las tres; las tres están cubiertas por separado
> en `tests/domain/graph/supervisor/test_gate.py`.
>
> Si quieres ver el contraste sin base de datos, baja el umbral para la ejecución limpia:
> `SUPERVISOR_CONFIDENCE_THRESHOLD=0.35 uv run python scripts/run_supervisor_s14.py --memory --stub
> --transcript exercises/session-14/sample_transcript_happy_path.txt`

## Criterios de "hecho"

- [x] El supervisor está construido a mano con `StateGraph` + `Command`; cada decisión de enrutado
      aparece en `routing_history` con su razón.
- [x] Cada agente accede solo a sus tools; el extractor no usa ninguna tool de negocio.
- [x] El estado es tipado, **heredado** del de la S13, con reducers acumuladores.
- [x] El grafo corre de principio a fin y el endpoint devuelve la estimación con su `status`.
- [x] La pausa humana se dispara con la señal de confianza y persiste en el checkpoint;
      `status = "awaiting_human_review"`.
- [x] El endpoint de reanudación continúa desde el checkpoint con la decisión humana.
- [x] Existe una traza completa de una ejecución que pasa por la pausa y se reanuda.

## Tests

Sin red y sin clave:

```bash
uv run pytest tests/domain/graph/supervisor tests/api/test_estimate_supervisor.py -v
```

Dos de ellos merecen leerse porque fijan bugs reales que aparecieron al ejecutar el flujo:

- `test_an_empty_search_result_does_not_loop_the_router` — si la "terminación" de un agente se mide
  por si produjo salida en vez de por si actuó, una búsqueda que legítimamente no encuentra nada
  reenvía al mismo agente para siempre.
- `test_repeated_tool_calls_in_one_step_are_kept_apart` — un agente llama a su tool una vez por
  componente; si la clave del reducer no incluye los argumentos, la segunda llamada **sustituye** a
  la primera y la auditoría pierde filas.

## Qué se difiere al directo

- Patrón de **competición**: agente conservador vs. agresivo + sintetizador. (La divergencia entre
  ambos es información que hoy no tienes: si uno dice 340h y otro 190h, eso te está diciendo que el
  proyecto tiene mucha incertidumbre estructural.)
- **Hardening** de sandboxing más allá del mínimo privilegio básico.
- Testing del flujo HITL con más transcripciones edge case.
