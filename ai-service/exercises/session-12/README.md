# Sesión 12 — Un agente hecho a mano (bucle manual sobre la Responses API)

El sistema de estimación funciona bien con un **pipeline fijo**: reformulas, recuperas, generas.
Pero una transcripción real que mezcla, por ejemplo, un backend de negocio + una integración con un
ERP + una app móvil obliga a **buscar presupuestos históricos por separado para cada componente**,
calcular parciales y consolidar. No sabes de antemano cuántas búsquedas harás ni en qué orden:
depende de lo que diga la transcripción.

Ahí es donde una **capa agéntica** aporta lo que el pipeline fijo no tiene: **decidir qué hacer en
cada paso**. En este ejercicio lo construyes **a mano, sin framework**.

> Un agente no es magia. Es un bucle que llama a un LLM que **decide**, ejecuta **tools** y para
> cuando ha terminado.

## Qué construyes

Un agente que recibe una transcripción, la descompone en componentes, usa **dos tools**
(`search_budgets` y `calculate_estimate`), **itera en un bucle manual** (razona → actúa → observa →
repite) y devuelve una estimación estructurada **junto a una traza** de su razonamiento.

Vive en el **servicio IA** (Python + FastAPI). `search_budgets` envuelve tu retrieval de S9–S10 —
**no lo reimplementes**. `calculate_estimate` es una función determinista de Python.

## Material de esta carpeta (tu kit de partida)

| Fichero | Para qué |
|---|---|
| `sample_transcript_simple.txt` | Un único componente. Para depurar el bucle **barato** con `gpt-5-mini`. |
| `sample_transcript_complex.txt` | Cuatro componentes distintos. La transcripción de los criterios de aceptación. |
| `reference_retrieval.py` | **Red de seguridad**: un stub de recuperación con presupuestos enlatados, sin base de datos. Úsalo solo si tu pipeline no está listo. Lo ideal es envolver el tuyo. |
| `calculate_estimate_skeleton.py` | Esqueleto del cálculo determinista, con `TODO`s, para que no pierdas tiempo en el modelo de costes. |

## Las dos tools (+ una opcional)

Defínelas con **JSON Schema** y `strict: true`. En la **Responses API** el schema es **plano**
(`{"type": "function", "name": ..., "description": ..., "parameters": {...}}`), a diferencia de Chat
Completions. Nombres, descripciones y parámetros **en inglés**.

- **`search_budgets(query, filters?)`** — recupera presupuestos históricos para **un** componente.
  Envuelve tu retrieval híbrido + reranking de S9–S10.
- **`calculate_estimate(components)`** — calcula el desglose y el total a partir de los componentes y
  sus importes de referencia. Determinista, sin LLM.
- **`validate_estimate(components, total_hours)`** *(opcional, recomendada)* — guardrails de
  verificación al estilo S4: rangos razonables, componentes sin presupuesto, totales incoherentes.

> La **calidad de las descripciones importa**: es lo único que el modelo lee para decidir cuándo usar
> cada tool. Escríbelas para un modelo que no ve tu código. En el directo optimizaremos esto.

## Conduce el bucle tú mismo

La Responses API devuelve items `function_call` (con `call_id`, `name`, `arguments`) y **se detiene
esperándote**. Ese ida-y-vuelta *es* el bucle:

1. Recorre `response.output` buscando `function_call`.
2. Ejecuta la función con los `arguments` (parseados desde JSON).
3. Devuelve el resultado como `function_call_output` con el **mismo `call_id`**.
4. Vuelve a llamar encadenando con `previous_response_id`.
5. Repite mientras haya `function_call`. Sal cuando no haya más. **Pon un máximo de iteraciones** como
   salvaguarda.

## Requisito de traza

Por iteración: razonamiento + acción + observación. Formato mínimo aceptable:

```
STEP 1
  reasoning:   <qué decidió el agente y por qué>
  action:      search_budgets(query="...", filters={...})
  observation: <resumen de los ítems devueltos>
```

## Criterios de aceptación (con `sample_transcript_complex.txt`)

- Identifica **más de un componente** y hace **más de una** llamada a `search_budgets`.
- Llama a `calculate_estimate` con los componentes y sus referencias.
- Termina por sí solo (ni bucle infinito ni corte a mitad).
- Produce una estimación estructurada coherente.
- La traza muestra, por paso, razonamiento + acción + observación.

## Entregable

Envía a Lia antes del directo: (1) el enlace a tu repositorio con el agente dentro del servicio IA,
y (2) la traza de ejecución para `sample_transcript_complex.txt`.

## Coste de API

Depura primero la **mecánica del bucle** con `gpt-5-mini` y la transcripción simple. Cuando sea
sólido, cambia a `gpt-5` con esfuerzo `medium` para la ejecución real sobre la compleja. Así el gasto
se mantiene por debajo de un par de dólares.

---

## Solución de referencia (en este repo)

La resolución completa ya está integrada en el servicio IA (compárala **después** de intentarlo):

- `app/generation/agentic/agent_schemas.py` — modelos de traza, resultado y argumentos de tools.
- `app/generation/agentic/agent_tools.py` — los schemas planos `strict:true` + las implementaciones
  (`search_budgets` envuelve `retrieve()`; `calculate_estimate` y `validate_estimate` deterministas).
- `app/generation/agentic/agent_loop.py` — el bucle manual sobre `client.responses.create`.
- `scripts/run_agent_s12.py` — ejecuta el agente e imprime la traza.

Cómo ejecutar la referencia:

```bash
# Depuración barata del bucle (retrieval real: stack arriba + corpus de tareas ingerido)
docker compose exec ai-service python scripts/run_agent_s12.py \
    exercises/session-12/sample_transcript_simple.txt --model gpt-5-mini --effort minimal

# Depuración offline con el stub (sin base de datos)
uv run python scripts/run_agent_s12.py \
    exercises/session-12/sample_transcript_simple.txt --model gpt-5-mini --stub

# Ejecución real (entregable) sobre la transcripción compleja
docker compose exec ai-service python scripts/run_agent_s12.py \
    exercises/session-12/sample_transcript_complex.txt --model gpt-5 --effort medium \
    --out exercises/session-12/example_trace_complex.txt
```

> `search_budgets` filtra por `chunk_type='historical_task'`, así que el retrieval real necesita el
> corpus de tareas ingerido: `docker compose exec ai-service python scripts/build_task_corpus.py --ingest`.

La traza de la ejecución real queda en `example_trace_complex.txt` (el entregable).
