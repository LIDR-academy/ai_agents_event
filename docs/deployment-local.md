# Despliegue local con Docker Compose

Sesión 15 · Módulo 6 — Arquitectura de producción y despliegue

Todo el sistema (backend de negocio, servicio IA, base de datos relacional y base de datos
vectorial) arranca con **un solo comando**, sin depender de qué tengas instalado en tu máquina
ni de que recuerdes el orden de arranque.

---

## Nomenclatura

El programa evita la palabra "backend" a secas porque hay dos. En este repo:

| Pieza del enunciado | Carpeta | Servicio de compose | Stack |
|---|---|---|---|
| backend de negocio | `business-backend/` | `business-backend` | Rails 8 |
| servicio IA | `ai-service/` | `ai-service` | FastAPI |
| base de datos relacional | — | `postgres` | Postgres 16 |
| base de datos vectorial | — | `vector-db` | pgvector (Postgres 16 + extensión) |
| — | — | `redis` | Redis Stack (cachés CAG) |

**La BBDD vectorial y la relacional del servicio IA son el mismo contenedor.** pgvector es una
extensión de Postgres, así que los embeddings viven en una columna `vector` junto al resto de
los datos. No hace falta un Qdrant ni un Chroma aparte. Ojo: `postgres` y `vector-db` son dos
instancias **distintas** a propósito — cada servicio es dueño de sus datos y ninguno lee la base
del otro.

---

## Arranque

```bash
cp .env.example .env
# edita .env: pon tu OPENAI_API_KEY y genera un AI_SERVICE_TOKEN
#   openssl rand -hex 32

docker compose build
docker compose up
```

La interfaz queda en <http://localhost:3000>.

El primer arranque tarda unos minutos (compila gemas nativas y descarga las imágenes). Los
siguientes son rápidos.

> **Nunca subas tu `.env`.** Está en `.gitignore`; lo que se versiona es `.env.example`, sin
> valores reales.

---

## Las 5 comprobaciones

### 1. Los servicios están arriba y sanos

```bash
docker compose ps
```

Los cinco servicios en `running`, y `ai-service`, `postgres`, `vector-db` y `redis` en
`(healthy)`. `business-backend` no arranca hasta que `ai-service` y `postgres` están sanos —
lo fuerza `depends_on: condition: service_healthy`, que es lo que elimina el clásico
"arrancó antes que la base de datos".

El healthcheck del servicio IA apunta a `/health`, que **no llama al LLM ni toca la base de
datos**: solo dice si el proceso está vivo. Si llamara al modelo, cada 30 segundos estarías
pagando tokens por comprobar que el contenedor sigue en pie.

### 2. El backend de negocio responde desde el host

```bash
curl -I http://localhost:3000
```

`HTTP/1.1 200 OK`. Es el único servicio con `ports:` publicado.

### 3. El servicio IA NO es accesible desde el host

Esta es la comprobación que da sentido al ejercicio:

```bash
curl --max-time 3 http://localhost:8000/health
# curl: (7) Failed to connect to localhost port 8000: Connection refused
```

Ese fallo es el resultado correcto. El servicio `ai-service` no declara `ports:`, así que
Docker no abre ningún puerto del host hacia él.

Y sí es alcanzable desde dentro de la red, por nombre de servicio:

```bash
docker compose exec business-backend curl -s http://ai-service:8000/health
# {"status":"healthy","version":"0.1.0","environment":"development"}
```

Dos detalles que conviene entender:

- **`http://ai-service:8000`, no `http://localhost:8000`.** Dentro de un contenedor, `localhost`
  es ese contenedor. Compose da resolución DNS por nombre de servicio.
- **`/health` responde sin token a propósito.** Es la única ruta exenta (junto a `/docs`),
  porque el healthcheck de Docker no puede llevar credenciales. Es seguro porque no revela nada
  ni ejecuta trabajo.

Cualquier otra ruta sí exige el token:

```bash
# Sin token → 401
docker compose exec business-backend \
  curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST http://ai-service:8000/api/v1/estimate

# Con token → ya no es 401 (será 422 por payload vacío, que es otra cosa)
docker compose exec business-backend \
  curl -s -o /dev/null -w '%{http_code}\n' \
  -H "X-Service-Token: $AI_SERVICE_TOKEN" \
  -X POST http://ai-service:8000/api/v1/estimate
```

Estar dentro de la red **no** es una credencial: la red impide que llegue el host, pero
cualquier otro contenedor de la misma red podría llamar al servicio. El token es lo que
convierte "interno" en "autorizado".

### 4. Una estimación end-to-end

Desde <http://localhost:3000>, lanza una estimación. El recorrido completo es:

```
navegador → business-backend (Rails)
              → ai-service (FastAPI)          [con X-Service-Token]
                  → vector-db (pgvector)      [recuperación de presupuestos]
                  → redis                     [cachés CAG]
              ← estimación estructurada
```

Para verlo en los logs mientras ocurre:

```bash
docker compose logs -f ai-service
```

### 5. Los datos sobreviven a un reinicio

```bash
docker compose down
docker compose up -d
docker compose ps
```

Todo vuelve sin pasos manuales. Las estimaciones y el corpus ingestado siguen ahí porque las
bases escriben en volúmenes con nombre (`postgres_data`, `estimator_postgres_data`,
`redis_data`), que `down` no borra.

`docker compose down -v` **sí** los borra. Con el corpus vectorial eso significa volver a pagar
los embeddings de la ingesta, así que piénsatelo dos veces.

---

## Las dos capas de autenticación

No son redundantes; responden preguntas distintas.

| Capa | Cabecera | Alcance | Pregunta que responde |
|---|---|---|---|
| Token de servicio (S15) | `X-Service-Token` | Middleware, toda la app salvo `/health` y `/docs` | ¿Eres un servicio autorizado a hablar conmigo? |
| Claves por router (S9) | `X-API-Key` | Routers `/v1/retrieval/*` y `/v1/estimate/*` | ¿Qué endpoints concretos puedes usar? |

Rails manda ambas: el token en todas las peticiones (`BaseClient`), y la clave donde hace falta
(`RagEstimateClient`). El token **no** abre los routers con clave — hay un test que lo fija
(`tests/api/test_service_token.py::test_the_two_auth_layers_are_independent`).

Lo que la capa nueva arregla: hasta la S14, `POST /api/v1/estimate`, `/sessions`, `/embeddings`,
`POST /search` y los `PUT /api/v1/config/*` no tenían ninguna autenticación. Ese último es el
peor: muta la configuración de modelos en runtime.

**Si dejas `AI_SERVICE_TOKEN` vacío, el middleware se desactiva.** Es deliberado (permite correr
los tests y `uv run uvicorn` en local sin fricción) y es lo contrario de las claves de la S9,
donde una clave vacía devuelve 401 en todo. En cualquier despliegue real, ponle valor.

---

## Modo desarrollo

El compose estricto no publica puertos de depuración ni monta el código. Para trabajar en local:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Eso añade:

| | Estricto | + override de dev |
|---|---|---|
| Servicio IA en el host | no accesible | `localhost:8000` (+ `/docs`) |
| BBDD vectorial | interna | `localhost:5433` |
| BBDD relacional | interna | `localhost:5434` |
| Redis / RedisInsight | interna | `localhost:6379` / `localhost:8001` |
| Código | dentro de la imagen | bind mount |
| Recarga | no | `--reload` (uvicorn) y `bin/dev` (Rails) |

> `postgres` se publica en **5434**, no en 5432: en macOS es habitual tener ya un Postgres de
> Homebrew ocupando el 5432 y acabarías conectándote a la base equivocada.

Usa el fichero estricto como referencia de "cómo va a producción". El override publica cosas
que en producción serían un agujero.

---

## Qué falta para producción de verdad

Este ejercicio deja el sistema **reproducible y desplegable**, no endurecido. Lo que queda,
dicho explícitamente en lugar de omitido:

1. **Rails sigue en `RAILS_ENV=development`.** Las imágenes ya son autocontenidas (el código va
   dentro, no por bind mount), que es el requisito para desplegar en cloud. Pero pasar a
   producción no es cambiar una variable: `config/database.yml` trae el bloque multi-base de
   Rails 8 (`primary` + `cache` + `queue` + `cable`, o sea solid_cache/solid_queue/solid_cable),
   así que exige cuatro bases de datos, sus migraciones, un `SECRET_KEY_BASE` real y
   `assets:precompile`. Es trabajo del directo, no del ejercicio previo.
2. **Secretos en `.env`.** Suficiente en local; en cloud van a un gestor de secretos.
3. **CORS con `allow_origins=["*"]`** en el servicio IA. Válido mientras solo le hable Rails
   server-side; hay que cerrarlo si algún día lo llama un navegador.
4. **Sin TLS.** El tráfico entre contenedores va en claro por la red interna.
5. **Una réplica de cada servicio**, sin límites de recursos ni política de reinicio más allá de
   `unless-stopped`.

---

## Problemas conocidos

**El servicio IA entra en bucle de reinicio tras cambiar de rama.** Las ramas comparten el
volumen de Postgres, así que la cabeza de Alembic de la rama más nueva se queda estampada y
`alembic upgrade head` falla al volver a una rama antigua. Se arregla re-estampando la revisión
correcta y borrando las tablas específicas de la otra rama.

**`docker compose up` falla por nombre de contenedor en uso.** Quedan contenedores de nombre
fijo de otra rama:

```bash
docker rm -f ai-service business-backend vector-db redis business-backend-postgres
docker compose up -d
```

**El puerto 5432 del host ya está ocupado.** Solo afecta al override de dev, que por eso publica
la base relacional en 5434.

**Una gema nueva no aparece en el contenedor.** Desde la S15 las gemas van dentro de la imagen,
así que basta con reconstruir:

```bash
docker compose build business-backend
```

**Los tests del servicio IA fallan con 401 en todo.** Tienes `AI_SERVICE_TOKEN` en
`ai-service/.env`, y pydantic lo lee al correr pytest desde esa carpeta. El token vive en el
`.env` de la **raíz** (el que usa Docker). Hay un fixture `autouse` en `tests/conftest.py` que
lo neutraliza, precisamente para que esto no pueda pasar.

---

## Comandos útiles

```bash
docker compose ps                          # estado y salud
docker compose logs -f ai-service          # logs de un servicio
docker compose exec business-backend bin/rails console
docker compose exec vector-db psql -U estimator -d estimator
docker compose exec postgres psql -U postgres estimator_web_development
docker compose down                        # parar (conserva los datos)
docker compose down -v                     # parar y BORRAR los volúmenes
```
