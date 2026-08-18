# Session 11 — Citación verificable + baseline RAGAS

Entregable del pre-work de la Sesión 11. Cubre las dos partes del enunciado:

1. **Citación verificable a nivel de línea** (Parte 1).
2. **Baseline de calidad de generación con RAGAS** sobre el golden set extendido (Parte 2).

Todo el código (schema, prompt, verificación, harness) está en inglés; estas notas y el
`ground_truth` del golden set van en español, como permite el enunciado.

---

## Qué se cambió (Parte 1)

- **Schema** (`app/generation/rag/schemas.py`):
  - `SourceReference{chunk_id, document_id, evidence}` — la cita verificable de una línea.
  - `TaskItem` (cada línea de estimación) ahora lleva `grounded: bool` y `sources: list[SourceReference]`
    (antes `list[int]`). Un `model_validator` impone la regla de integridad:
    `grounded=True` ⇒ ≥1 fuente; `grounded=False` ⇒ sin fuentes y sin horas inventadas.
  - `CitationReport` / `LineCitation` — la salida del verificador.
- **Ensamblado de contexto** (`context_assembler.py`): cada `<source>` ahora expone también
  `document_id`, para que el modelo pueda copiar el presupuesto histórico concreto en cada cita.
- **Prompt de generación** (`prompt_builder.py`): atribución obligatoria por línea — `chunk_id`
  exacto del `<source>`, `document_id`, y `evidence` **verbatim** (no parafraseado); `grounded=false`
  cuando no hay soporte (sin horas a ojo).
- **Verificación post-generación** (`validation.py::verify_citations`): recorre cada línea y
  comprueba que todo `chunk_id` citado esté en el conjunto de chunks recuperados. Distingue líneas
  *grounded*, *dangling* (id inventado) e *insufficient* (sin datos). Se integra en el orquestador
  (`estimator.py`, con un reintento correctivo + downgrade a `confidence=low` si no se repara) y en
  el stage `/v1/estimate/stages/generate` (campo `citation_report` + `fabricated_source_ids`).

El contrato HTTP no cambia de forma: solo se enriquece el cuerpo con las fuentes por línea.

---

## Reporte de verificación de citaciones (obligatorio)

Generado por `scripts/demo_verify_citations_s11.py` (offline, sin red): una estimación grounded
real con **una citación colgante plantada a propósito** (`chunk_id=999`, nunca recuperado) y una
línea sin datos suficientes. Demuestra los tres criterios de aceptación de la Parte 1.

```
=== Citation verification report ===
lines: 4  grounded: 2  dangling: 1  insufficient: 1
verified citations: 2
dangling citations: ['999']

module                   line                     status        cited -> dangling
----------------------------------------------------------------------------------------
Authentication & SCA     OAuth 2.0 backend        grounded      ['101'] -> -
PSD2 & Open Banking      Open banking connectors  grounded      ['102'] -> -
Ledger                   Transaction ledger       dangling      ['999'] -> ['999']
Reporting                Regulatory reporting     insufficient  [] -> -

ACCEPTANCE: PASS
```

- **grounded=True con fuente real**: las 2 primeras líneas citan chunks 101/102 que sí estaban en
  el contexto.
- **citación colgante detectada**: la línea "Transaction ledger" cita 999 → marcada `dangling`.
- **sin datos suficientes**: "Regulatory reporting" no se rellena con horas, se marca `insufficient`
  y se expresa como Assumption.

El informe sobre una estimación **real del pipeline** (no plantada) se adjunta por cada consulta
en `evals/ragas_baseline_s11.json` (campo `citation_report` por query).

---

## Baseline RAGAS (Parte 2)

- **Golden set**: `evals/golden_generation_s11.json` — extiende las 5 consultas Q1–Q5 del golden
  set de la Sesión 10 (`golden_retrieval.json`) añadiendo un `ground_truth` (estimación de
  referencia por experto, en engineer-days ≈ horas/8, derivada de los presupuestos relevantes).
- **Harness**: `scripts/eval_ragas_s11.py` (recoge muestras ejecutando el pipeline real
  reformulate→retrieve→assemble→generate) + `scripts/score_ragas_s11.py` (puntúa con RAGAS).
- **Juez**: `gpt-4o-mini`; **embeddings**: `text-embedding-3-small`. Métricas:
  `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`.

> **Nota operativa**: `ragas 0.4.x` importa en carga `langchain_community.chat_models.vertexai`,
> que el `langchain-community` actual del proyecto ya no expone. Por eso el scoring corre en un venv
> aislado (`score_ragas_s11.py` registra un stub de Vertex, que nunca se instancia con juez OpenAI).
> Crear el venv aislado **una sola vez** (desde `ai-service/`; no toca el venv del proyecto):
> ```bash
> uv venv --python 3.11 .ragas-venv
> uv pip install --python .ragas-venv/bin/python 'ragas>=0.4,<0.5' langchain-openai datasets pandas
> ```
> Flujo de dos pasos:
> ```bash
> # 1) recoger muestras con el pipeline real (venv del proyecto, stack arriba + corpus ingerido)
> DATABASE_URL='postgresql+psycopg://estimator:estimator@localhost:5433/estimator' \
>   uv run python scripts/eval_ragas_s11.py --collect-only samples.json
> # 2) puntuar en el venv aislado. El scorer usa ChatOpenAI/OpenAIEmbeddings y NO lee .env,
> #    así que OPENAI_API_KEY debe estar exportada en el entorno.
> export OPENAI_API_KEY=sk-...
> .ragas-venv/bin/python scripts/score_ragas_s11.py samples.json --out evals/ragas_baseline_s11.json
> ```

### Tabla de métricas (4 métricas × 5 consultas + promedio)

Run real (juez `gpt-4o-mini`, embeddings `text-embedding-3-small`). Datos completos en
`evals/ragas_baseline_s11.json`.

| query | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---|---|
| Q1 | 0.418 | 0.000 | 1.000 | 0.000 |
| Q2 | 0.426 | 0.061 | 1.000 | 0.000 |
| Q3 | 0.564 | 0.000 | 1.000 | 0.571 |
| Q4 | 0.877 | 0.000 | 1.000 | 0.000 |
| Q5 | 0.476 | 0.104 | 1.000 | 0.000 |
| **average** | 0.552 | 0.033 | 1.000 | 0.114 |

### Verificación de citaciones sobre las estimaciones reales (las 5)

Salida real del verificador (`verify_citations`) sobre cada estimación generada por el pipeline:

| query | líneas | grounded | dangling | insufficient | citas verificadas |
|---|---|---|---|---|---|
| Q1 | 33 | 27 | 0 | 6 | 32 |
| Q2 | 35 | 35 | 0 | 0 | 41 |
| Q3 | 30 | 17 | 0 | 13 | 24 |
| Q4 | 31 | 6 | 0 | 25 | 11 |
| Q5 | 40 | 31 | 0 | 9 | 36 |
| **total** | **169** | **116** | **0** | **53** | **144** |

**Citaciones colgantes: 0/169 líneas.** Cada línea `grounded=True` cita un chunk real del contexto;
las 53 líneas sin soporte se marcan `insufficient` (no inventan horas), no se rellenan.

### Nota de hallazgos (lo que más chirría)

- **`context_precision` perfecto (1.0) pero `context_recall` casi nulo (0.11, solo Q3 > 0).** La
  recuperación trae exactamente los presupuestos relevantes (precision alta), pero el juez no
  consigue atribuir el `ground_truth` al contexto: el `ground_truth` está en **engineer-days** y el
  corpus en **horas**, así que las cifras no casan línea a línea. Q4 (IoT) es el peor caso —
  `confidence=low`, 25/31 líneas sin datos — la recuperación más floja del set.
- **`answer_relevancy` ≈ 0 en casi todas.** La "pregunta" es un *brief* declarativo y la "respuesta"
  una tabla estructurada de módulos→tareas; la métrica (que regenera preguntas desde la respuesta y
  las compara con la pregunta) está mal planteada para este formato. Es un artefacto de medición,
  no señal de calidad — lo atacamos en el directo reformulando pregunta/respuesta.
- **`faithfulness` media (0.55) pese a citación correcta.** El generador descompone cada componente
  histórico en 4–8 subtareas y reparte horas entre ellas; el juez no puede atribuir 1:1 cada cifra
  derivada a la cifra del `<source>`, así que la fidelidad baja aunque la citación por línea sea
  real. Aquí está el valor de la citación verificable: separa "la cita existe" de "la cifra se
  deduce de la cita".
- **Anomalía de unidades (sale gratis del baseline):** en 4/5 consultas el modelo copia las **horas**
  históricas como **engineer-days** (Q1 total 528, Q2 460, Q5 639 ≈ suma de horas del presupuesto),
  mientras que Q4 sí estima en días (63). Inconsistencia de unidades a corregir en el prompt.

**Resumen para el directo:** citaciones colgantes 0/169; `context_recall` 0.11 (lastrado por el
desajuste days/horas y por Q4); `answer_relevancy` ≈ 0 por mismatch de formato pregunta/respuesta;
`faithfulness` 0.55 con `context_precision` 1.0.
