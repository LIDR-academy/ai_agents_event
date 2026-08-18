# Estimator — estimación de software con un flujo de agentes

De la **transcripción de una reunión con cliente** a un **presupuesto desglosado** y una
**propuesta comercial**, con dos paradas en las que decide una persona.

El sistema lee la conversación, la descompone en módulos y tareas, busca cuántas horas
costaron tareas parecidas en presupuestos históricos, y monta el precio. No es un asistente
al que preguntas: es un flujo que se ejecuta, se detiene donde tiene que detenerse, y te
entrega un documento.

---

## Levantarlo

Necesitas Docker y una clave de OpenAI. Cuatro comandos:

```bash
cp .env.example .env
# abre .env y pon tu OPENAI_API_KEY — es lo único obligatorio

docker compose up --build

# carga el corpus de presupuestos históricos (ver más abajo: NO es opcional)
docker compose exec ai-service python scripts/build_task_corpus.py --ingest

open http://localhost:3000/demo
```

El primer arranque tarda unos minutos: compila gemas nativas y descarga imágenes. Los
siguientes son rápidos.

### Cuatro cosas que conviene saber antes de empezar

**El corpus histórico hay que cargarlo a mano.** Es el tercer comando y no lo hace nadie por
ti. Sin él, el sistema no tiene presupuestos contra los que comparar: cada tarea sale sin
horas y el flujo *parece* roto sin estarlo. El script genera 60 proyectos de ejemplo
(~1.500 tareas) y los indexa. Es **idempotente**: si vuelves a lanzarlo verás muchos `409`,
que significan «ya estaba». Lánzalo **dentro del contenedor** (`docker compose exec`), no
desde tu máquina: fuera no hereda el token entre servicios y la primera petición daría 401.

**Empieza en `/demo`, no en `/`.** La portada todavía no enlaza este flujo. `/demo` es un
atajo que te deja directamente en la pantalla de arranque.

**Las migraciones se aplican solas** al arrancar cada servicio. No tienes que ejecutar nada.
(Si alguna vez necesitas evitarlo, `RUN_MIGRATIONS=false`.)

**De `.env.example`, solo `OPENAI_API_KEY` viene vacía a propósito.** El resto de valores
funcionan tal cual para desarrollo local. Para cualquier despliegue real, genera además un
`AI_SERVICE_TOKEN` con `openssl rand -hex 32`.

---

## Cómo funciona el flujo

Cinco pantallas. Tú intervienes en tres.

### 1 · Arranque

Fijas dos parámetros de negocio y pegas la conversación:

- **Tarifa** — lo que cobras por hora, en €/h
- **Contingencia** — un colchón porcentual sobre el total
- **Transcripción** — pegada a mano o subida con **«Subir .txt»**

Pulsas **«Arrancar el grafo →»**.

> Hay transcripciones de ejemplo en `examples/transcripts/`. La más corta, `01_clear.txt`,
> es una discovery call de una fintech que quiere el backend de una app de banca móvil.

### 2 · Flujo en vivo

Aquí no haces nada. Cada agente ocupa una fila y va reportando lo que hace según termina;
la pantalla se refresca sola y se recarga cuando el flujo llega a una parada humana.

**Esto tarda un par de minutos.** No se ha colgado — ver la tabla de tiempos más abajo.

### 3 · 🧑 Puerta humana 1 — revisión del desglose

El flujo **se detiene** y te enseña el árbol de módulos y tareas que ha propuesto. Puedes
renombrar, borrar y añadir lo que quieras: **esto es lo que se va a estimar**. Todavía no
hay horas por ningún lado.

Al aprobar, se lanza una búsqueda por **cada** tarea que hayas dejado, en paralelo.

### 4 · 🧑 Puerta humana 2 — revisión final

Vuelve con las horas puestas y un informe de fiabilidad. Aquí solo tocas **las horas**: la
estructura ya la aprobaste.

Las tareas **sin precedente histórico** aparecen marcadas y vacías. Eso es deliberado: el
sistema prefiere decirte que no lo sabe a inventarse un número. Las rellenas tú.

Decides también si quieres que se redacte la propuesta comercial.

### 5 · Resultado

Tres cifras arriba — **jornadas**, **horas** y **precio** — y el desglose por módulo, que se
despliega hasta el detalle de cada tarea con sus horas y su coste.

La propuesta se descarga en **PDF** o en **Markdown**, y puedes **regenerarla** las veces que
quieras sin repetir el flujo.

### Dos cosas que sorprenden

**El precio no lo calcula ningún agente.** Es una multiplicación: las horas que el sistema
fundamentó contra presupuestos reales, por la tarifa que pusiste tú, más tu contingencia. La
propuesta comercial se limita a citar esa cifra; no la deriva, no la redondea.

**Las pausas son de verdad.** El estado se guarda en Postgres, no en la sesión del navegador.
Puedes cerrar la pestaña en una puerta humana y volver mañana.

### Cuánto tarda cada tramo

Medido sobre una transcripción real que produjo 11 módulos y 75 tareas:

| Tramo | Tiempo |
|---|---|
| Clasificar la transcripción | ~4 s |
| Proponer módulos y tareas | **~2 min** ← el más largo |
| Calcular las horas de las 75 tareas | **~2,5 s** |
| Recuperar las tareas dudosas | ~1 min |
| Redactar el informe | ~15 s |
| Redactar la propuesta | ~8 s |

---

## Qué hace cada agente

En las pantallas cada paso lleva nombre y cara. Esto es lo que hay detrás:

| | Agente | Qué hace | Modelo |
|---|---|---|---|
| 🕶 | **Morpheus** | Juzga la complejidad de la transcripción y la reformula en un brief limpio | `gpt-4o-mini` |
| 🥋 | **Neo** | Descompone el brief en módulos → tareas, todavía sin horas | `gpt-5` |
| 🧑 | **El Operador** | *Eres tú.* Revisa y aprueba el desglose | — |
| 🎛 | **Tank** | Calcula las horas de cada tarea, todas en paralelo | **ninguno** |
| 🏍 | **Trinity** | Reintenta las tareas dudosas y consolida la estimación | `gpt-5` |
| 🔮 | **El Oráculo** | Redacta el informe de fiabilidad. No toca ninguna cifra | `gpt-4o` |
| 🧑 | **El Operador** | *Otra vez tú.* Completas las horas que faltan y validas | — |
| 📐 | **El Arquitecto** | Redacta la propuesta comercial | `gpt-4o` |

**Fíjate en Tank.** El paso que produce el número que el cliente acaba firmando es el único
que **no usa ningún modelo de lenguaje**: es una búsqueda vectorial sobre el corpus de
presupuestos históricos, con un consenso ponderado por lo parecidas que sean las tareas. Si
para una tarea no encuentra nada suficientemente parecido, no devuelve horas — la marca.

---

## Arquitectura en dos minutos

Cinco contenedores:

```
        tú :3000
           │
           ▼
   business-backend ───────▶ ai-service ───────▶ vector-db   (pgvector)
       (Rails)          red    (FastAPI)    red      │
           │          interna              interna   └────────▶ redis
           ▼
        postgres
```

**`business-backend` es el único servicio que publica un puerto.** El servicio de IA y las
dos bases de datos no son accesibles desde tu máquina: solo existen dentro de la red interna
de Docker. Esa es la propiedad de seguridad del montaje, no un detalle de configuración.

Hay **dos Postgres a propósito** y no se leen entre sí: `vector-db` guarda los embeddings del
servicio de IA (pgvector es una extensión de Postgres, así que el almacén vectorial y su
relacional son el mismo motor); `postgres` es la base de datos de Rails.

**Rails nunca habla con OpenAI.** Todas las llamadas a modelos salen del servicio de IA.

---

## Qué más hay

| Ruta | Qué es |
|---|---|
| `/demo` | Atajo a la pantalla de arranque del flujo |
| `/rag/graph_estimation_runs` | El flujo de agentes y su histórico de ejecuciones |
| `/agents/profiles` | Los agentes, con su modelo y su papel. Perfiles personalizables |
| `/agents/graph_flow` | Diagrama del flujo, de solo lectura |
| `/rag/estimation_runs` | Un asistente equivalente pero paso a paso, con cada etapa relanzable |
| `/rag/index_runs` | Añadir documentos al corpus vectorial |
| `/rag/chunking_comparisons` | Laboratorio para comparar estrategias de troceado ⚠️ gasta dinero real |
| `/estimations`, `/chat_sessions` | Estimación transaccional y estimación conversacional |
| `/ai_settings` | Cambiar los modelos en caliente, sin reiniciar |

> En las pantallas del flujo de agentes la barra de navegación se reduce a **«Grafo»** y
> **«Agentes»**. Es intencionado, para no tener el resto de la aplicación a un clic mientras
> se proyecta.

---

## Para seguir leyendo

| Documento | Qué cuenta |
|---|---|
| [`docs/deployment-local.md`](docs/deployment-local.md) | Despliegue en detalle, las dos capas de autenticación, modo desarrollo y problemas conocidos |
| [`ai-service/ARCHITECTURE.md`](ai-service/ARCHITECTURE.md) | Las capas del servicio de IA y sus reglas de dependencia |
| [`business-backend/ARCHITECTURE.md`](business-backend/ARCHITECTURE.md) | Cómo el cliente Rails refleja los contratos del servicio |
| [`.env.example`](.env.example) | Todas las variables de entorno, comentadas |

### Comandos útiles

```bash
docker compose ps                                    # estado y salud de los 5 servicios
docker compose logs -f ai-service                    # ver trabajar a los agentes
docker compose exec business-backend bin/rails console
docker compose exec vector-db psql -U estimator -d estimator
docker compose down                                  # parar, conservando los datos
docker compose down -v                               # parar y BORRAR los volúmenes
```

Para desarrollo, un override publica los puertos internos y activa la recarga en caliente:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```
