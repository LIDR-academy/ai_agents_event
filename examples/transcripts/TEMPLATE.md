# Diagnóstico arquitectónico — Sesión 09 (pre-work)

> Plantilla del entregable. Rellena las cuatro secciones obligatorias y guarda el resultado como
> `arquitectura-actual.md` en la raíz del repositorio. Tus observaciones van en español; los
> comandos, payloads y nombres de campo van en inglés.

---

## 1. Diagrama de la arquitectura actual

> Las tres capas (frontend, backend de negocio, servicio IA) con los módulos del servicio IA que
> existen al cierre de Sesión 08. Baja un nivel en el servicio IA y marca **dónde acaba** lo
> implementado. No dibujes lo que falta — eso es la sección 4.

_(diagrama aquí: ASCII, Mermaid, imagen…)_

---

## 2. Trace anotado de `02_ambiguous.txt`

> Trace manual a través del sistema tal como está. Para cada paso: la llamada ejecutada, la
> respuesta cruda y un comentario de una o dos frases.

### Paso 1 — Embeber la transcripción completa

_(comando + vector: dimensionalidad/norma, primera y última componente + comentario)_

### Paso 2 — Búsqueda semántica (top-5)

_(comando + respuesta cruda con chunks y distancias)_

### Paso 3 — Lectura de los chunks devueltos

_(para cada chunk: presupuesto histórico, sector, ¿relevante? Sé honesto.)_

---

## 3. Diagnóstico: cinco fallos identificados

> Cinco fallos concretos y verificables que impiden hoy convertir la transcripción en una
> estimación de calidad. Para cada uno: Problema observado / Causa probable / Propuesta de solución.

### Fallo 1 — _(título)_
- **Problema observado:**
- **Causa probable:**
- **Propuesta de solución:**

### Fallo 2 — _(título)_
- **Problema observado:**
- **Causa probable:**
- **Propuesta de solución:**

### Fallo 3 — _(título)_
- **Problema observado:**
- **Causa probable:**
- **Propuesta de solución:**

### Fallo 4 — _(título)_
- **Problema observado:**
- **Causa probable:**
- **Propuesta de solución:**

### Fallo 5 — _(título)_
- **Problema observado:**
- **Causa probable:**
- **Propuesta de solución:**

### Otros _(opcional)_

---

## 4. Propuesta de evolución arquitectónica

> Segundo diagrama de la misma arquitectura de tres capas, con las cajas/módulos que añadirías para
> cerrar el flujo transcripción → estimación generada. Marca claramente lo NUEVO respecto a la
> sección 1.

_(diagrama aquí)_

> Párrafo (≤10 líneas): responsabilidad de cada módulo nuevo, qué dato fluye entre ellos, y qué
> pieza es la más crítica — la que atacarías primero si solo pudieras construir una.
