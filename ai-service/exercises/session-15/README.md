# Sesión 15 — Ejercicio previo: arquitectura de producción y despliegue

> Hasta ahora el sistema arrancaba porque **tú** sabías el orden. Eso no es una arquitectura:
> es una costumbre. Containerizar no va de meter cosas en cajas, va de que el orden de arranque,
> las dependencias y la frontera de seguridad dejen de vivir en tu cabeza y pasen a estar
> escritas en un fichero que cualquiera puede ejecutar.

Rama: `session_15`. Solución de referencia: la raíz del repo (`docker-compose.yml`,
`docker-compose.dev.yml`, `.env.example`) + `ai-service/Dockerfile` +
`business-backend/Dockerfile` + `docs/deployment-local.md`.

---

## Lo que ya tenías (y qué se rompe)

Al cerrar la S14 el repo ya tenía dos `Dockerfile` y tres `docker-compose.yml`. Parecía hecho.
No lo estaba, y conviene ver por qué antes de tocar nada:

| Síntoma | Por qué importa |
|---|---|
| El compose raíz era solo un `include:` de los dos de subproyecto | No tenía bloque `services:`; no levantaba nada por sí mismo, y arrancar desde una subcarpeta creaba **otro** proyecto de compose con volúmenes distintos |
| Ninguna de las dos imágenes llevaba el código dentro | Rails no copiaba nada y el servicio IA no copiaba `alembic/`, `scripts/` ni `data/`. Entraban por bind mount. **En cloud no hay bind mount**: nada de esto se podía desplegar |
| El servicio IA publicaba el 8000 al host | Cualquiera en tu máquina podía saltarse el backend de negocio |
| Media API sin autenticar | `POST /api/v1/estimate`, `/sessions`, `/embeddings`, `POST /search` y — el peor — `PUT /api/v1/config/*`, que **muta la configuración de modelos en runtime** |
| No había `.env` en la raíz, y `.env` no estaba en el `.gitignore` raíz | Un `.env` en la raíz se habría commiteado con las claves dentro |

La S15 no añade una funcionalidad de IA. Arregla las cinco.

---

## Nivel 1 — Que arranque con un comando

Aplana el compose a **un solo fichero en la raíz** con los cuatro servicios del enunciado (aquí
son cinco, porque Redis sostiene las cachés CAG desde la S4).

Dos decisiones que parecen de detalle y no lo son:

- **`depends_on` con `condition: service_healthy`**, no el `depends_on` a secas. El corto solo
  espera a que el contenedor *arranque*, no a que el servicio esté *listo*. Es la diferencia
  entre "Rails espera a Postgres" y "Rails arranca, Postgres todavía no acepta conexiones y
  revienta".
- **Nombres de volumen estables.** Si renombras la clave de un volumen, Docker crea uno nuevo y
  vacío. Con el corpus vectorial eso significa volver a pagar los embeddings. Aquí el servicio
  se llama `vector-db` pero el volumen sigue siendo `estimator_postgres_data`, a propósito.

## Nivel 2 — Imágenes autocontenidas

Que `docker run` funcione sin el árbol de fuentes al lado. Para el servicio IA eso implica
copiar también `alembic/`, `alembic.ini`, `scripts/` y `data/`.

Y decidir **dónde corren las migraciones**. Estaban en el `command:` del compose, que es
justo el sitio donde no sobreviven a un despliegue en cloud. Van a un **entrypoint**:

```sh
#!/bin/sh
set -e
alembic upgrade head
exec "$@"
```

El `exec` no es cosmético: sin él, uvicorn queda como hijo del shell, el shell es PID 1 y
`docker stop` manda el SIGTERM al shell en vez de a la aplicación. El contenedor tarda 10
segundos en morir y se apaga a lo bruto.

## Nivel 3 — La frontera de seguridad

**El servicio IA no lleva `ports:`.** Solo `business-backend` publica al host. Compruébalo de
verdad: `curl http://localhost:8000/health` desde el host tiene que **fallar**.

Y añade el token de servicio. Aquí hay tres decisiones que dan de sí:

1. **Middleware, no dependencia por router.** Con `Depends(...)` proteges los endpoints que te
   acuerdas de proteger. Lo que había sin autenticar en este repo era exactamente lo que se le
   olvidó a alguien en la S9. Un middleware no se olvida de nada.
2. **`/health` va exento.** El healthcheck de Docker no puede llevar credenciales. Si proteges
   `/health`, el contenedor no llega nunca a `healthy`, `depends_on` no se cumple y el backend
   de negocio no arranca. Es seguro porque no revela nada ni ejecuta trabajo.
3. **`secrets.compare_digest`, nunca `==`.** La comparación normal corta en el primer byte que
   difiere, y ese tiempo de respuesta filtra el prefijo del secreto.

Y la que más se discute: **si el token no está configurado, el middleware se apaga**. Es lo
contrario de las claves de la S9 (vacía ⇒ 401 en todo). El motivo es que esta capa envuelve la
aplicación entera: si por defecto estuviera encendida, el día que la mergeas se caen los ~570
tests y el `uv run uvicorn` de todo el mundo.

---

## Solución de referencia (en este repo)

| Fichero | Qué resuelve |
|---|---|
| `docker-compose.yml` (raíz) | Los 5 servicios; solo `business-backend` con `ports:` |
| `docker-compose.dev.yml` | Override local: puertos de depuración, bind mounts, hot reload |
| `.env.example` (raíz) | Todas las variables, sin valores reales |
| `ai-service/Dockerfile` + `docker-entrypoint.sh` | Imagen autocontenida; migraciones en el entrypoint |
| `business-backend/Dockerfile` + `bin/docker-entrypoint` | Idem, con `db:prepare` |
| `ai-service/app/api/service_token.py` | El middleware del token |
| `business-backend/app/services/estimator_ai/base_client.rb` | Envía el token en **todas** las llamadas |
| `docs/deployment-local.md` | Arranque + las 5 comprobaciones |

---

## Cómo ejecutar

```bash
cp .env.example .env          # pon tu OPENAI_API_KEY
                              # y un AI_SERVICE_TOKEN (openssl rand -hex 32)
docker compose build
docker compose up
```

Las cinco comprobaciones, con la número 3 como la que de verdad importa:

```bash
docker compose ps                                   # 5 arriba, healthy
curl -I http://localhost:3000                       # 200

curl --max-time 3 http://localhost:8000/health      # DEBE fallar: connection refused
docker compose exec business-backend \
  curl -s http://ai-service:8000/health             # {"status":"healthy",...}

docker compose down && docker compose up -d         # los datos siguen ahí
```

Modo desarrollo (recupera el 8000, los bind mounts y el hot reload):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

---

## Material de esta carpeta

| Fichero | Para qué sirve |
|---|---|
| `README.md` | Este enunciado |
| `example_run_compose.txt` | Traza real de las 5 comprobaciones sobre el stack levantado — el entregable probatorio |

**Aviso honesto:** este ejercicio deja el sistema *reproducible y desplegable*, no *endurecido*.
Rails sigue corriendo con `RAILS_ENV=development` dentro del contenedor, y no es un descuido:
el bloque `production:` de `config/database.yml` es el multi-base de Rails 8 (primary + cache +
queue + cable), así que necesita cuatro bases, sus migraciones y un `SECRET_KEY_BASE`. Está
escrito en `docs/deployment-local.md` en lugar de barrido bajo la alfombra.

---

## Criterios de "hecho"

- [x] `docker compose up` levanta los 5 servicios desde cero
- [x] `docker compose ps` los muestra `healthy`
- [x] `http://localhost:3000` sirve la interfaz
- [x] `http://localhost:8000` **no** responde desde el host
- [x] El servicio IA sí responde en `http://ai-service:8000` dentro de la red
- [x] Sin `X-Service-Token` todo devuelve 401 salvo `/health` y `/docs`
- [x] Una estimación end-to-end funciona íntegramente en contenedores
- [x] `docker compose down && docker compose up` conserva los datos
- [x] `.env.example` versionado con todas las variables; `.env` en `.gitignore`
- [x] `docs/deployment-local.md` con el arranque y las 5 comprobaciones

---

## Tests

```bash
# Servicio IA (red no necesaria)
cd ai-service && uv run pytest tests/api/test_service_token.py -v

# Backend de negocio
cd business-backend && bin/rails test test/services/estimator_ai/service_token_test.rb
```

Dos de estos tests fijan errores reales, no comportamiento hipotético:

- `test_health_stays_open_so_the_healthcheck_works` — si `/health` deja de estar exento, el
  contenedor nunca llega a `healthy` y el arranque se bloquea en cascada. Falla de forma
  desconcertante: la aplicación funciona, pero compose no la levanta.
- `multipart requests also carry the default headers` — `multipart_conn` en Rails **nunca**
  aplicó las cabeceras por defecto. Estuvo latente mientras todos los endpoints multipart
  estaban abiertos; el token app-wide lo habría convertido en un 401 en producción.

---

## Qué se difiere al directo

El despliegue en cloud propiamente dicho. Trae anotado **qué no te funcionó**: errores de build,
DNS entre contenedores, servicios que arrancan antes de tiempo, o el servicio IA alcanzable
cuando no debería. El primer bloque de la sesión es para eso.
