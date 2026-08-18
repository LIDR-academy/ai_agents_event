# ARCHITECTURE — business-backend

El cliente Rails es el espejo del otro lado del cable del `estimator` (FastAPI).
Donde el servicio se organiza por **arquitecturas IA que componen** (CAG / RAG /
Agentic / conversation) sobre una **foundation** sin opinión, el cliente se
organiza por **los mismos contextos**, con una foundation que es *lo único que
habla HTTP con el servicio*. Contrato completo del servicio en
`../ai-service/ARCHITECTURE.md`.

## Mapa de capas

```
estimator (Python)                       business-backend (Rails)
─────────────────────────                ─────────────────────────────────────────
foundation/ (llm, guardrails…)     ←→    app/services/estimator_ai/   (clients HTTP + corpus)
domain/schemas + generation/<x>/schemas  app/models/<contexto>/        (POROs from_hash, espejo 1:1 Pydantic)
generation/conversation + agentic  ←→    conversation/  (chat multi-turno + traza ACB)
generation/rag                     ←→    rag/           (Chunking Lab S07; retriever S08+)
api/ (routers finos)               ←→    controllers + views           (transporte, flash mapping)
```

```
app/
├── services/estimator_ai/        # FOUNDATION — única puerta a FastAPI
│   ├── base_client.rb            #   Faraday + handle_response + errores tipados (EstimatorAi::*)
│   ├── estimations_client.rb     #   POST /api/v1/estimate                       (S04)
│   ├── sessions_client.rb        #   /sessions/* + estimate-acb                  (S05)
│   ├── embeddings_client.rb      #   POST /embeddings/compare                    (S07)
│   ├── config_client.rb          #   GET/PUT /api/v1/config/models               (Ajustes)
│   ├── ingestion_client.rb       #   /api/v1/ingestion/* (ilustrativo, S06)
│   └── budget_corpus.rb          #   corpus estático (lib/estimator_ai/data/)
│
├── models/
│   ├── estimation.rb             # AR raíz del contexto transaccional (payload JSONB)
│   ├── chat_session.rb           # AR raíz del contexto conversacional
│   ├── estimation/               # CONTEXTO estimación transaccional (S04)
│   │   └── request · response · result · phase
│   ├── conversation/             # CONTEXTO conversación + ACB (S05)
│   │   └── request
│   ├── rag/                      # CONTEXTO RAG (S07; crece en S08+)
│   │   ├── chunking_comparison   # AR — persistencia de runs (tabla chunking_comparisons)
│   │   ├── strategy              # catálogo de las 8 estrategias (espejo de ALL_STRATEGIES)
│   │   └── comparison_response · stats · token_distribution · query_result · top_chunk
│   └── ai/                       # CONTEXTO configuración del servicio (Ajustes)
│       ├── model_config          # un knob: effective/default/overridden + label/descr.
│       └── catalog               # snapshot completo del GET (knobs + available_models)
│
├── controllers/
│   ├── home_controller.rb        # dashboard raíz (un card por contexto)
│   ├── estimations_controller.rb · chat_sessions_controller.rb
│   ├── ai_settings_controller.rb # Ajustes: overrides de modelo en runtime
│   └── rag/chunking_comparisons_controller.rb
│
└── views/  (espejo de controllers; layout con navbar por contexto)
```

## Las 4 reglas

1. **Solo `services/estimator_ai/` habla HTTP.** Ningún controller usa Faraday
   directamente. Los clients heredan de `BaseClient` (conexiones + mapeo
   respuesta→error). La taxonomía de errores vive en el namespace raíz
   (`EstimatorAi::GuardrailViolation`, `::InvalidRequest`, `::SessionNotFound`,
   `::ServerError`); `IngestionClient` mantiene la suya propia porque su
   semántica (catálogo, jobs async) es distinta.
2. **Los POROs de contrato espejan los schemas Pydantic 1:1** (`from_hash` ↔
   `model_validate`). Si cambia el contrato en Python, cambia su espejo aquí —
   y solo aquí. Los AR guardan el payload completo en JSONB (`response_payload`)
   y lo tipan al leer (`to_response`).
3. **Los contextos no se importan entre sí** (la regla de siblings del
   estimator): `rag/` no toca `conversation/`; comparten solo la foundation.
   Excepción deliberada y mínima: `Conversation::Request` reutiliza los enums de
   `Estimation::Request` (el contrato compartido de project_type/detail_level).
4. **Controllers = transporte.** Validar parámetros, llamar al client, mapear
   errores tipados a flash con mensaje accionable, persistir el payload. La
   lógica de negocio vive en el servicio Python.

## Frontera con el servicio IA

- El cliente **nunca** habla con OpenAI/Anthropic: solo `ESTIMATOR_API_BASE_URL`.
- Timeout global 180 s (`config.estimator_ai.timeout`); las llamadas largas
  (compare con estrategias LLM ≈ 6 min) pasan un timeout por instancia
  (`EmbeddingsClient.new(timeout: 600)`) sin tocar el default.
- Flujos síncronos a propósito (sin infra de jobs): el camino por defecto del
  Lab usa estrategias gratuitas (segundos); las de pago son opt-in y avisadas.

## ¿Dónde va mi código nuevo?

| Si añades… | Va en… |
|---|---|
| Un endpoint nuevo del servicio | método en el client de su contexto (o client nuevo si es contexto nuevo) |
| El espejo de un schema Pydantic | `app/models/<contexto>/` (PORO `from_hash`) |
| Persistencia Rails de un flujo | AR raíz del contexto + migración (payload JSONB) |
| Una pantalla nueva | controller fino + vistas en `<contexto>/` + link en navbar/home |
| Interactividad de formulario | Stimulus controller (`app/javascript/controllers/`) — UX sugar, nunca load-bearing |
| Datos estáticos de referencia | `lib/estimator_ai/data/` |

## Mapa contexto ↔ sesión ↔ endpoints

| Contexto | Sesión del máster | Endpoints FastAPI | UI |
|---|---|---|---|
| `estimation` | S04 (output estructurado, guardrails, cache) | `POST /api/v1/estimate` | `/estimations` |
| `conversation` | S05 (memoria, adjuntos, tiers, ACB) | `/sessions/*`, `/sessions/:id/estimate(-acb)` | `/chat_sessions` |
| `rag` | S07 (chunking + embeddings) | `POST /embeddings/compare` | `/rag/chunking_comparisons` |
| `ai` (Ajustes) | transversal | `GET/PUT /api/v1/config/models` | `/ai_settings` + badge en navbar |
| (ingestion) | S06 (ilustrativo) | `/api/v1/ingestion/*` | — sin UI |
