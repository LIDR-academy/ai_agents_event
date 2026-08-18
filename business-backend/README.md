# business-backend

Rails 8 frontend + business backend for the AI-engineering monorepo. Consumes the FastAPI service in `../ai-service/` for LLM estimations.

Organized by **contexts mirroring the Master's modules** — `estimation` (S04, transactional), `conversation` (S05, multi-turn + ACB) and `rag` (S07, Chunking Lab) — over an `EstimatorAi` foundation that is the only layer talking HTTP to the service. Full rules and layer map in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Secciones de la UI

- **`/`** — home dashboard: un card por contexto del máster.
- **`/estimations`** — estimación transaccional (S04).
- **`/chat_sessions`** — conversación multi-turno con adjuntos, tiers y modo Actor-Critic-Boss (S05).
- **`/ai_settings`** — **Ajustes**: cambia los modelos LLM del servicio (primario, fallback, critic, metadata, compression y chunkers) **en caliente** vía `PUT /api/v1/config/models` — sin editar `.env` ni recrear contenedores. El modelo primario activo se muestra siempre como badge en la navbar (oculto si el servicio está caído). «Por defecto» restaura el valor del `.env`.
- **`/rag/chunking_comparisons`** — **Chunking Lab** (S07): comparativa de las 8 estrategias de chunking sobre el corpus de 17 presupuestos (`lib/estimator_ai/data/`), con estadísticas por estrategia (tokens, huérfanos/obesos, coste, segundos), barras de coste y playground de retrieval (top-k por similitud coseno, badges parent/child en `hierarchical`). Cada run se persiste (`chunking_comparisons`, payload JSONB) para revisitar resultados caros (contextual_retrieval ≈ $0.14 / 3 min) sin re-pagar. Las estrategias de pago son opt-in y van avisadas en el formulario; la llamada usa un timeout propio de 600 s.

## Stack

- Ruby 3.4.4 / Rails 8.0.5
- PostgreSQL 16 (containerized)
- Tailwind CSS 4 via `tailwindcss-rails` (standalone binary, no Node required)
- Hotwire (Turbo + Stimulus) + Importmap + Propshaft
- Solid Cache / Queue / Cable — all in-memory in development, no Redis

## Quick start (Docker, recommended)

Since Session 15 there is a **single compose file at the repo root** that brings up all five
services; this project no longer has its own `docker-compose.yml`.

```bash
cd ..                     # repo root
cp .env.example .env      # fill in OPENAI_API_KEY and AI_SERVICE_TOKEN
docker compose up --build
```

App at http://localhost:3000. Healthcheck at `/up`. Full walkthrough and the five verification
checks in [`../docs/deployment-local.md`](../docs/deployment-local.md).

The first build compiles native gems (`bootsnap`, `nokogiri`, `pg`, `debug`) and may take several minutes; subsequent builds are cached unless `Gemfile.lock` changes.

## Quick start (local, no Docker)

Requires Postgres listening on the local Unix socket.

```bash
bin/setup        # bundle install + db:prepare
bin/dev          # Puma + tailwindcss:watch via foreman, port 3000
```

`config/database.yml` reads `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_USER`, `DATABASE_PASSWORD` from ENV. When unset, Rails falls back to the Unix socket — that is the local-without-docker path.

## Common commands

```bash
docker compose exec business-backend bin/rails console
docker compose exec business-backend bin/rails db:migrate
docker compose exec business-backend bin/rails test
docker compose exec business-backend bash

# Connect to the dev DB with psql
docker compose exec postgres psql -U postgres estimator_web_development

# Live logs
docker compose logs -f business-backend

# Add a gem (rebuild to bake it into the image)
docker compose exec business-backend bundle add <gem>
docker compose build business-backend
```

## Cross-service calls

All five services share one network, so Rails reaches the AI service at `http://ai-service:8000`
(`ESTIMATOR_API_BASE_URL`) — by **service name**, never `localhost`, which inside a container
means that container.

Every request carries `X-Service-Token` (Session 15), injected once in
`app/services/estimator_ai/base_client.rb` and validated by middleware on the FastAPI side.
`RagEstimateClient` adds its own `X-API-Key` on top; the base client *merges* rather than
replaces, so the RAG endpoints send both. A 401 from either layer raises
`EstimatorAi::Unauthorized`, which almost always means the two services disagree about
`AI_SERVICE_TOKEN`.

## Production / Kamal

The image is **self-contained** since Session 15 (source is baked in, Tailwind is built at image
build time, it runs as a non-root `rails` user), so it can be deployed without the source tree
next to it. It still boots with `RAILS_ENV=development`: the `production:` block in
`config/database.yml` is the Rails 8 multi-database layout (primary + cache + queue + cable), so
switching needs four databases, their migrations and a real `SECRET_KEY_BASE`. That is the live
session's job — see the "Qué falta para producción de verdad" section of
[`../docs/deployment-local.md`](../docs/deployment-local.md).

The `kamal` and `thruster` gems and the `.kamal/` + `config/deploy.yml` files are leftovers from
`rails new` and currently unused; they do not load at runtime (`require: false`).
