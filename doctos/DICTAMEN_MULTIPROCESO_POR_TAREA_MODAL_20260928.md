# Dictamen — propuesta Codex: varios procesos Golden por tarea Modal

Fecha: 2026-09-28 · Evaluador: Claude (Fable 5) · Base: rama `api` @ `08932a64645bff74030ad2bd63543dd2334b8101`, `context/ARQUITECTURA_STACK.md`, `doctos/AUDITORIA_REFACTOR_websocket_server_api_08932a6.md`, documentación oficial de Modal (consultada 2026-09-28).

El checkout local está en `feat/coalescer-integrity-probe` (mismo commit, cambios locales sólo de test); toda la evidencia sale de `git show api:` y `git grep … api`, sin tocar el árbol de trabajo.

Etiquetas: `[CÓDIGO]` · `[DOCUMENTACIÓN]` (docs oficiales de Modal / del repo) · `[MEDIDO]` (corpus de CLAUDE.md o dumps) · `[INFERIDO]` · `[ABIERTO]`.

---

## 1. Dictamen

**Ajustar.** La propuesta no es una mejora arquitectónica del cuello: es **empaquetado de réplicas** dentro de un contenedor mayor. Cuatro workers de 25 llamadas dentro de una tarea son, en capacidad por loop, en CPU y en memoria de modelos, lo mismo que cuatro contenedores de 25. La única ventaja material es **operativa**: divide por N el número de contenedores y de arranques fríos (33,0 s `[MEDIDO]`) por unidad de capacidad. A cambio pierde el balanceo con conocimiento de carga que Modal ya da entre contenedores, agranda el dominio de fallo (100 llamadas por tarea caída en vez de 25) y exige cambios reales de arranque que hoy el código no permite (`uvicorn.run(app-objeto)` no admite workers — el propio repo lo documenta `[CÓDIGO]` `tools/loadtest/td1_analyze.py` L125-127).

Lo que sí es correcto y verificado de la propuesta: la **afinidad por llamada es natural en el código** — todo lo que le pasa a una llamada vive en el proceso que aceptó su WebSocket — así que repartir llamadas entre procesos no necesita IPC ni estado compartido (§5). Eso hace la variante B *viable*; no la hace *necesaria*.

**El paso decisivo no es elegir B: es medir K de UNA tarea en Modal** con la imagen pineada y la cadencia de 60 ms del coalescer. La rodilla 16-25 se midió en `.143` a 20 ms/50 fps, con co-tenencia y con el spin que la imagen ya elimina `[MEDIDO]`; con 60 ms cada llamada genera 16,7 mensajes/s en vez de 50, y el experimento natural del corpus (lotes grandes → `submit_p90` −71 %, `rvad_p90` −56 % `[MEDIDO]`) apunta a que K por loop en Modal será mayor que 25 `[INFERIDO]`. Con ese número se decide todo: `contenedores = 2000/K`, y si el resultado (p. ej. 40-80 contenedores) es operable —Modal admite hasta 4.000 por función `[DOCUMENTACIÓN]`— **B no aporta nada que D no dé sin tocar código**. B queda como optimización de empaquetado a activar sólo si el número de contenedores resultara doloroso en la práctica.

---

## 2. Argumentos de Codex: correctos, incompletos, equivocados

### Correctos

1. **Afinidad total por llamada** `[CÓDIGO]`. Verificado símbolo a símbolo: la máquina de estados recibe el servidor como ejecutor (`set_action_executor(self)`, SM L116) y sus acciones (`execute_function` WS L9416, `inject_prompt` L9494, `get_call_metadata` L9400) resuelven por `self.sessions` del mismo proceso; `FunctionCallResponse` llega por el mismo WebSocket; los timers (silencio, guard, watchdog, fondo) nacen en `_handle_settings` del mismo proceso. **No existe ninguna ruta HTTP externa que actúe sobre una llamada** (rutas: `/health`, `/api/voices`, `/api/campaigns*`, `POST /api/campaigns/prepare`→410, y el WS; WS L2051-2122).
2. **Repartir llamadas reparte el trabajo por paquete** `[CÓDIGO]`. Todo el costo caliente (pre, submit, resume, post, envíos a Steno) es por llamada; no hay estado cruzado entre sesiones en el camino de audio (verificado por `test_aislamiento_entre_sesiones.py` y la auditoría §6).
3. **Honestidad de alcance**: la propuesta no afirma subir K por loop ni bajar CPU por llamada. Correcto — y es la clave del dictamen: si K por loop no cambia, N workers ≡ N réplicas.
4. Su hipótesis lateral (un split MediaWorker/Control dejaría casi toda la carga en MediaWorker) es plausible `[INFERIDO]` pero irrelevante: ese split está fuera de discusión por la regla de trabajo 1 de CLAUDE.md.

### Incompletos

1. **Sin mecanismo de distribución.** No dice cómo N procesos comparten el puerto que Modal proxya, ni que el arranque actual lo impide: `GoldenBridgeServer.run` pasa el objeto app a `uvicorn.run` (WS L9578-9591) y `create_app` (WS L9594) construye un servidor sin configuración — no hay factory por import-string `[CÓDIGO]`.
2. **Sin contabilidad Modal.** Una conexión WebSocket = una function call = **un input durante toda la conexión**; la concurrencia por contenedor la fija `@modal.concurrent(max_inputs, target_inputs)` y al excederla Modal encola o escala `[DOCUMENTACIÓN]`. B no cambia esa cuenta: el contenedor declara N×K_worker y Modal sigue sin ver los workers internos.
3. **Sin análisis de balanceo interno.** Con socket compartido, el kernel reparte por conexión sin conocer la carga; con llamadas largas el reparto es ~binomial: 100 llamadas en 4 workers → σ≈4,3, p99 del peor worker ≈ 33-35 `[INFERIDO]`. Hay que dimensionar cada worker por encima de su cuota nominal o correr más workers pequeños. Modal, entre contenedores, sí respeta el headroom por contenedor (el propio repo lo lee: `input_headroom` en `tools/monitoring/modal_stats_poller.py` `[CÓDIGO]`).
4. **Sin costo de memoria.** Cada proceso duplica torch (envoltorio de Silero, `torch==2.13.0+cpu` en el lock `[CÓDIGO]`), las dos sesiones ORT, el extractor de features y las cachés de audio (greeting/filler/script se calientan por proceso). Magnitud `[ABIERTO]`: medible hoy mismo leyendo `mem_before_mb` de los logs "Periodic cleanup completed" en los dumps existentes `[CÓDIGO]` (el campo ya se emite).
5. **Sin decir dónde vive el límite de sesiones** ni cómo se drena/reporta salud por worker (§4.2).

### Equivocados o engañosos

1. **"Aumentar capacidad por contenedor aprovechando varios núcleos"** sugiere que el límite era CPU. El corpus ya estableció lo contrario: 100 llamadas piden ~2,3 núcleos y el techo lo pone la coordinación en un hilo `[MEDIDO]`. Los núcleos extra sólo se usan añadiendo loops — y "más loops" es exactamente "más réplicas", se empaqueten donde se empaqueten.
2. **"Un Golden de 100 llamadas"** es una identidad aritmética (N×K), no una capacidad nueva. Si la ventaja es reducir el número de contenedores, hay que decirlo así — y esta evaluación lo dice expresamente: **esa es la única ventaja**.

---

## 3. Respuestas a las preguntas

### 3.1 ¿Resuelve el problema o reagrupa réplicas? (pregunta 1)

| nivel | qué lo fija | A (hoy) | B (N procesos/tarea) | D (N tareas chicas) |
|---|---|---|---|---|
| **por loop** | rodilla de coordinación `[MEDIDO .143: 16-25]`; en Modal `[ABIERTO]` | K_loop | K_loop (sin cambio) | K_loop (sin cambio) |
| **por proceso** | = por loop (1 loop/proceso) | K_loop | K_loop | K_loop |
| **por tarea** | empaquetado | K_loop | **N × K_loop − margen de desbalance** | K_loop |
| **flota (2000)** | `2000 / K_loop` loops en total | igual | igual | igual |
| **coste flota** | reserva CPU+mem total | ≈ igual | ≈ igual | ≈ igual |

`[INFERIDO]` A igualdad de recursos totales no hay ventaja de rendimiento, CPU ni memoria de B sobre D: los modelos, torch y los pools se duplican **por proceso** en ambos. Las diferencias reales:

- **A favor de B**: 1/N contenedores → 1/N arranques fríos de 33 s en rampas y menos churn del autoescalador; un solo gate de referencia y una descarga de imagen por cada N loops.
- **A favor de D**: balanceo por contenedor con límite duro (`max_inputs`) respetado por el scheduler de Modal, frente al accept() ~aleatorio del kernel dentro de B; dominio de fallo de K llamadas en vez de N×K; aislamiento de CPU por contenedor (en B un worker en pico compite con sus hermanos dentro del mismo cgroup `[INFERIDO]`); **cero cambios de código** (D es la arquitectura ya decidida en `ARQUITECTURA_STACK.md` §5: "cada contenedor de GB atiende ~50 llamadas; Modal gestiona la flota").

**Sí: la ventaja de B consiste exclusivamente en reducir el número de contenedores** (y sus arranques fríos). Con K_Modal ≥ 25, 2000 llamadas son ≤ 80 contenedores — muy por debajo del límite de 4.000 por función `[DOCUMENTACIÓN]` y del orden de magnitud que la flota gotenks (STT/TTS/LLM) ya maneja hoy.

### 3.2 Distribución de conexiones y ownership (pregunta 2)

**Mecanismo compatible con Golden y Modal** `[CÓDIGO+DOCUMENTACIÓN]`:

1. **Puerto compartido: sí.** Un supervisor (los workers de uvicorn, o gunicorn) abre el socket de escucha y lo hereda a N procesos hijos; todos hacen `accept()` sobre el mismo fd y el kernel entrega cada conexión nueva a uno. Alternativa equivalente: `SO_REUSEPORT` con un socket por worker. En ambos casos **el proceso que acepta el WS es dueño de la llamada hasta el final** — que es exactamente la afinidad que la propuesta pide y que el código ya garantiza (§2.1).
2. **Cambios de arranque necesarios**: hoy imposible sin tocar código — `uvicorn.run(self.app)` con app-objeto no admite `workers>1` (`[CÓDIGO]` WS L9582 y la advertencia ya escrita en `td1_analyze.py` L126-127). Hace falta una factory por import-string que reproduzca el cableado completo de `main()` (config → overrides de campaña → `create_processors` → warm-up → `GoldenBridgeServer`), porque `create_app` (WS L9594) no lo hace. Detalle en §6.
3. **Desbalance**: sin un proxy con least-connections no hay reparto por carga; se gobierna con dimensionamiento: `max_sessions` por worker ≈ cuota nominal + 2σ (p. ej. 25 nominal → tope 34), o más workers pequeños. El rechazo por capacidad ya existe (1013 en WS L2135-2144 `[CÓDIGO]`), pero con reparto aleatorio un 1013 con hermanos libres **pierde la llamada** salvo que el ai-engine reintente la conexión — comportamiento del engine `[ABIERTO]`. Por eso el límite que debe saturarse primero es el de Modal, no el del worker.
4. **Dónde se aplica el límite: en ambos, con roles distintos.** Primario: `max_inputs` del contenedor (Modal deja de enrutar conexiones nuevas a la tarea llena `[DOCUMENTACIÓN]`). Cinturón: `max_sessions` por worker, dimensionado con el margen de desbalance para que en operación normal nunca se alcance.
5. **Acciones hacia la llamada**: no hay nada que enrutar entre workers. Todas las acciones posteriores (timeout de estado, AMD, farewell, hangup, transferencia — que es una función externa de campaña por `execute_function`, sin ruta especial `[CÓDIGO]` auditoría §8.D) nacen dentro del proceso dueño. La **afinidad del WebSocket abierto** es la única que existe y la da el accept; no hay peticiones HTTP posteriores sobre una llamada que requieran afinidad (rutas verificadas en §2.1).
6. **Dispatcher Python: rechazado.** Un proceso que reciba y reenvíe el audio de toda la tarea tocaría N×16,7 mensajes/s en un solo loop Python — exactamente el trabajo por paquete cuya rodilla motiva todo esto `[MEDIDO]`. Si algún día hiciera falta least-connections interno, sería nginx/haproxy dentro de la imagen (fuera de Python), y esa complejidad ya supera la de dejar que Modal balancee contenedores chicos `[INFERIDO]`.

### 3.3 Compatibilidad efectiva con Modal (pregunta 3)

**Lo que hay en el repo** `[CÓDIGO]`: una imagen autocontenida (`Dockerfile` → `goldenbridge:314t`) cuyo ENTRYPOINT (`docker/arranque.sh`) corre el gate de referencia (VNNI/ORT) y ejecuta `python -m src.main --port ${GB_PUERTO}`; el servidor escucha en 8765. **El wrapper de Modal de GoldenBridge NO está en el repo**: la búsqueda de `modal` en `api` sólo devuelve el autoscaler retirado (`src/scaling/`, que gobierna la app *gotenks-hybrid* de STT/TTS/LLM, no a GB), el poller de estadísticas y documentación. Cómo está montado el endpoint `shejo--golden-bridge-bridge-serve.modal.run` (decorador, `max_inputs`, `cpu`, `mem`, `min_containers`, `scaledown_window`) **no es verificable desde el repo** `[ABIERTO]` — hay que pedir/leer ese archivo antes de cualquier decisión de empaquetado.

**Lo que la documentación oficial permite** `[DOCUMENTACIÓN]` (modal.com/docs, consultado 2026-09-28):

- **`@modal.web_server(puerto)`** es el primitivo para "imagen con servidor propio": Modal ejecuta la función, que puede lanzar el proceso servidor (subprocess), y proxya HTTP/WS al puerto; el servidor debe ligarse a `0.0.0.0` (el arranque actual ya lo hace: `server.host` default `0.0.0.0` `[CÓDIGO]` default.yaml L7). Es el único modo compatible con N procesos internos. `@modal.asgi_app` sirve la app en el propio proceso de Modal → **incompatible con multiproceso**.
- **WebSockets**: soportados en `asgi_app` y `web_server`; **una conexión WS = una function call = un input** mientras dure; para conexiones simultáneas se usa `@modal.concurrent(max_inputs, target_inputs)`; mensajes de hasta 2 MiB; **sin permessage-deflate (RFC 7692)** — coherente con GB, que ya lo apaga (`ws_per_message_deflate=false`, WS L9590 `[CÓDIGO]`).
- **Proxy-auth** (`Modal-Key`/`Modal-Secret`): protección del edge de Modal, aplica a todos los tipos de endpoint — coincide con lo que CLAUDE.md registra del endpoint actual.
- **Autoescalado**: `min_containers`, `max_containers`, `buffer_containers`, `scaledown_window`; límite duro 4.000 contenedores por función; al exceder `max_inputs` las conexiones nuevas se encolan o escalan.
- **No documentado en las páginas consultadas** `[ABIERTO]`: el drenaje de WebSockets vivos en scale-down o redeploy (¿corta las conexiones o espera?), la política exacta de colocación de inputs entre contenedores calientes, y el tope máximo admitido de `max_inputs`. Los tres se observan de forma barata en el PoC-0 (desplegar con llamadas sintéticas vivas y mirar qué les pasa).

**Conclusión de compatibilidad**: el despliegue actual (imagen + arranque propio) **se conserva para A y para D sin tocar nada**. Para B hay que (i) confirmar que el wrapper usa `web_server` —o migrarlo—, y (ii) cambiar el arranque interno a multiproceso (§6). Nada de esto es una capacidad exótica de Modal, pero **nada de esto está configurado hoy** de forma verificable.

### 3.4 Estado y riesgos de la rama `api` para multiproceso (pregunta 4)

Qué **impide** hoy correr N workers, y qué es sólo consecuencia esperable `[CÓDIGO]`:

| pieza | símbolos | ¿bloquea B? | tratamiento |
|---|---|---|---|
| Arranque monoproceso | `GoldenBridgeServer.run` L9578 (`uvicorn.run(app-objeto)`), `create_app` L9594 incompleto, `main()` sin factory | **Sí** | factory + workers (§6) |
| Registry de sesiones | `self.sessions` + `_sessions_lock` L1304-1305 | No — privado por proceso; correcto porque nada externo busca llamadas por id (§3.2.5) | dejar |
| Capacidad | `max_sessions: 120` (default.yaml L9); check L2135 | No bloquea, pero queda mal dimensionado: N workers × 120 sin coordinar | por-worker = cuota+margen; primario en Modal |
| Cachés globales | `greeting_cache`, `_global_filler_cache`, `_script_audio_cache` L1288-1301; `precompute_greetings` | No — se duplican por proceso: N× síntesis de warmup y N× RAM de audio | aceptar; medir |
| Conexiones STT/TTS | por sesión (`stt_connection`, `tts_connection`), adopción lazy en el proceso dueño | No — afinidad natural | dejar |
| Campaña / FunctionCall / transferencia / hangup / inject_prompt | SM L116, L837, L939; WS L9416, L9494 | No — todo in-process (§2.1) | dejar |
| `/health` y métricas | L2072-2082 por proceso; HEALTHCHECK del Dockerfile pega a un worker cualquiera | No bloquea; reporta 1/N de la tarea | agregar suma opcional; Modal no lo usa para rutar |
| Cleanup | `_periodic_cleanup` (gc, L1334) y reaper por proceso | No | dejar |
| Pool VAD y ORT | `_VAD_EXECUTOR` perezoso por proceso (VAD L38-73); sesiones ORT creadas post-arranque del worker | No, **si** cada worker importa y construye sus procesadores (sin preload/fork de sesiones ORT) | factory por worker; nunca `preload_app` |

Qué puede ser **privado de cada proceso**: todo lo anterior. Coordinación adicional **inevitable**: ninguna en el plano de datos; sólo dimensionamiento (límites) y, opcionalmente, agregación de salud/telemetría. No se comparte ningún buffer mutable entre workers; orden de audio, `turn_seq`, barge-in y aislamiento quedan intactos porque no cruzan el proceso `[CÓDIGO]`.

### 3.5 CPU, modelos y runtime (pregunta 5)

- **Pins** `[CÓDIGO]`: la imagen fija `GB_ORT_INTRA_OP=1`, `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1` como ENV (Dockerfile) — heredados por cualquier hijo. Importa porque el default *en código* es `intra_op=0` = "que ORT decida" (SMART L121): sin el ENV, **cada** worker abriría hilos por núcleo. Python 3.14.7 free-threaded y `onnxruntime==1.24.4` vienen del lock (`requirements-314t.lock`) `[CÓDIGO]`; ORT_SEQUENTIAL e `inter_op=1` en código (SMART L119-120).
- **Gate de comportamiento**: `arranque.sh` corre la referencia congelada una vez por contenedor; cubre a los N workers (misma CPU, mismas banderas `avx512_vnni`) `[CÓDIGO]`. Ningún cambio necesario.
- **Memoria duplicada por proceso**: torch (envoltorio de Silero) + 2 sesiones ORT + extractor de features + intérprete. Magnitud `[ABIERTO]`; medición barata: `mem_before_mb` de los logs "Periodic cleanup completed" ya presentes en los dumps de campaña (proceso único de hoy = costo de 1 worker). La reserva `mem` del contenedor B debe multiplicarse por N — idéntico costo agregado que D.
- **Pools y locks que se multiplican**: N × `ThreadPoolExecutor("vad", 4)` + N × executor por defecto (la inferencia smart-turn corre en `run_in_executor(None, …)`, no en el pool vad — auditoría §13.2) → con N=4 y `cpu=4`, hasta ~16 hilos DSP listos sobre 4 núcleos. Con los pins no giran en vacío, pero conviene evaluar `vad.inference_workers` 4→2 por worker `[ABIERTO: PoC-B]`. Los locks (`_state_lock`, `_inference_lock`, `_infer_lock`) son por proceso/por sesión; multiplicarlos no crea contención nueva.
- **Inicialización segura**: cada worker debe construir sus procesadores **después** de nacer (import-string lo garantiza; jamás crear sesiones ORT antes de un fork). El warm-up de Modal/gotenks ya se salta en backend `gotenks` (`main()` L494-506 `[CÓDIGO]`), así que el arranque por worker es carga de modelos, no red.
- **CPU y throttling**: 100 llamadas ≈ 2,3-2,6 núcleos de DSP `[MEDIDO]` + red/TLS/serialización. La reserva actual del contenedor GB en Modal `[ABIERTO]` (wrapper fuera del repo). En B los workers comparten el cgroup: un pico de uno degrada a los demás; en D el aislamiento es por contenedor `[INFERIDO]`.
- **Fallos**: worker caído → el supervisor de uvicorn lo repone y caen ~K_worker llamadas `[INFERIDO: verificar el comportamiento de reinicio de uvicorn 0.52 en el PoC]`; contenedor caído → Modal repone (33 s frío `[MEDIDO]`) y caen **todas** las llamadas de la tarea: 100 en B contra 25 en D. Multiplicar procesos **no** crea capacidad si la reserva de CPU no acompaña: B con `cpu=1` y 4 workers es estrictamente peor que A.

---

## 4. Tabla comparativa A–D

| | **A** — 1 proceso/tarea (hoy) | **B** — N procesos/tarea (Codex) | **C** — MediaWorker + Control | **D** — N tareas chicas de Golden completo |
|---|---|---|---|---|
| Capacidad por loop | K_loop (`[MEDIDO .143]` rodilla 16-25; en Modal `[ABIERTO]`) | = A | no multiplica loops del camino de audio | = A |
| Capacidad por tarea | K_loop (hoy `max_sessions: 120` opera sobre la rodilla `[MEDIDO]` — problema de **dimensionado**, no de arquitectura) | N×K_loop − margen de desbalance | — | K_loop |
| Balanceo de llamadas | scheduler de Modal entre tareas | kernel accept() ~aleatorio dentro; Modal entre tareas | — | scheduler de Modal con `max_inputs` respetado por contenedor `[DOCUMENTACIÓN]` |
| Cambios de código | ninguno | factory + supervisor + límites por worker (§6) | refactor mayor | **ninguno** (sólo config/wrapper) |
| Memoria | 1× modelos+torch | N× por tarea (= D por loop) | ~1× + colas | 1× por tarea |
| Dominio de fallo | K llamadas | **N×K llamadas** por contenedor caído; K_worker por worker caído | proceso media = todo | K llamadas |
| Contenedores para 2000 | 2000/K | 2000/(N·K) | — | 2000/K (≤ 80 si K≥25; límite Modal 4.000 `[DOCUMENTACIÓN]`) |
| Arranques fríos por unidad de capacidad | 1 por K | **1 por N·K** (la ventaja real) | — | 1 por K (33 s c/u `[MEDIDO]`) |
| Riesgos propios | dimensionado K | desbalance interno, throttling compartido, salud por worker, drenaje | **vetado por regla de trabajo 1 de CLAUDE.md** — no se evalúa como opción | churn de contenedores en rampas (mitigable con `min/buffer_containers` `[DOCUMENTACIÓN]`) |
| Veredicto | corregir K, no la forma | sólo si el nº de contenedores de D duele | fuera de alcance | **camino por defecto**: es la arquitectura ya decidida (`ARQUITECTURA_STACK.md` §5) |

---

## 5. Mecanismo implementable (si B se activara) y ownership

Resumen operativo del §3.2: `@modal.web_server(GB_PUERTO)` en el wrapper (confirmar/migrar `[ABIERTO]`) → dentro del contenedor, `arranque.sh` (gate una vez) ejecuta **uvicorn con `--workers N` y factory por import-string**; el socket lo abre el supervisor y los N workers aceptan del mismo fd; el worker que acepta un WS es dueño de la llamada completa (DSP, VAD/SmartTurn, STT, conversación, LLM, TTS, barge-in, cleanup) sin ninguna coordinación nueva, porque el código ya ancla todo al proceso del handler `[CÓDIGO]`. Límite primario `max_inputs = K_tarea` (Modal); cinturón `max_sessions = ceil(K_tarea/N) + 2σ` por worker. Sin dispatcher Python; sin colas compartidas; sin compartir sesiones ORT entre procesos.

## 6. Cambios mínimos necesarios (archivos y símbolos reales)

Para **D / medir K (PoC-0): cero cambios de código**. Sólo config: `server.max_sessions = K` (config/default.yaml L9 o `active.yaml`) y, en el wrapper de Modal (fuera del repo `[ABIERTO]`), `max_inputs = K`, `cpu/mem` acordes, `min_containers/buffer_containers` para las rampas.

Para **B** (sólo tras decidirlo con números):

1. `src/main.py` — extraer de `main()` una factory `build_app()` (cargar config → merge de overrides de campaña → `create_processors` → warm-up → `GoldenBridgeServer(…)` → `return server.app`) utilizable como import-string; `main()` queda para el modo monoproceso. Hoy no existe: `create_app` (websocket_server.py L9594) construye el servidor **sin** configuración.
2. `src/server/websocket_server.py:GoldenBridgeServer.run` (L9578-9591) — sustituir por el supervisor multiworker preservando `ws_per_message_deflate=False` (hoy kwarg programático; verificar su equivalente en CLI o usar `uvicorn.Server(Config(...))` por worker).
3. `docker/arranque.sh` — `exec` del supervisor con `--workers ${GB_WORKERS:-1}`; el gate de referencia queda donde está (una vez por contenedor).
4. `config/default.yaml` — `max_sessions` pasa a semántica por-worker (documentar la relación con `max_inputs`).
5. `vad.inference_workers` (config L133) — evaluar reducción por worker `[ABIERTO: PoC-B]`.
6. Opcional: `/health` agregado (suma de workers) si operación lo pide; Modal no lo usa para enrutar.
7. Cliente de carga `tools/loadtest/td1_client.py` — para pegar al endpoint de Modal necesita **cabeceras `Modal-Key`/`Modal-Secret`** (hoy no las envía `[CÓDIGO]`) y **cadencia 60 ms/480 B** (hoy fija 160 B/20 ms, `SILENCE_FRAME` L59) — esto es del PoC, no de producción.

## 7. PoC decisivo

**PoC-0 — "K de una tarea A en Modal" (primero; sin ninguna fase del refactor).** Ninguna fase F1-F7 de la auditoría es prerrequisito; la ganancia cuantitativa de B/D queda pendiente de este número, pero la viabilidad del diseño ya está resuelta arriba.

- Montaje: 1 tarea con la imagen `goldenbridge:314t` (gate ON), `cycle_probe: true` en el `active.yaml` montado; generador `td1_client.py` (modo T-D1b con habla, `utterances/listos/`) corriendo en `.143` con `taskset` (como el runbook), parcheado con proxy-auth y 60 ms; `modal_stats_poller` como pre-flight y durante (flota gotenks sana = el cuello observado es de GB).
- Carga: escalera 10 → 20 → 30 → 40 → 60 → 80 → 100 llamadas, 10-15 min por escalón, habla con pausas de 8 s (~turnos cada 14 s, como producción `[MEDIDO]`). El runbook de burst ya fija las dos condiciones (ventana sin campañas; OK al costo — escalón de 40 primero) `[CÓDIGO]`.
- Métricas: `cycle/submit/job/resume_vad p50-p90` (CYCLE-PROBE), `resume_vad_p90` y `cycle_p90` contra la curva de rodilla de CLAUDE.md, time-to-first-audio-byte p50/p90 punta a punta, `inbox_depth_max`, `max_arrival_gap_ms`, cierres 1011, RSS (`Periodic cleanup`), CPU del contenedor. **Separar lag del generador**: el cliente ya registra su propio lag de loop y lleva controles (`--controls`); todo escalón con lag de cliente alto se descarta, no se interpreta.
- Criterio: K_Modal = mayor N sostenido con `cycle_p90` y `rvad_p90` dentro de la banda 6-15 de la curva medida (no la banda 16-25), TTFAB p90 ≤ 1.330 ms (el p90 real actual `[MEDIDO]`) y cero 1011. Extra barato: durante un escalón vivo, forzar un redeploy/scale-down y observar qué pasa con los WS (cierra el `[ABIERTO]` de drenaje).
- Decisión: `contenedores = 2000/K_Modal`. Si ese número es operable → **D, terminado; B no se implementa**.

**PoC-B — "empaquetado" (sólo si 2000/K_Modal duele).** Mismo presupuesto total: una tarea `cpu=4, mem=M` con W ∈ {1, 2, 4} workers (requiere sólo los cambios §6.1-3, ninguna fase del refactor), contra la referencia D: 4 tareas `cpu=1, mem=M/4, max_inputs=K`. Carga 4×K llamadas. Métricas por worker + desbalance (max−min sesiones por worker), RSS y CPU por proceso, y un kill −9 a un worker con las demás llamadas vivas. Aceptación: métricas por loop de W4 a 4K dentro de ±15 % de A a K y de D a 4×K; cualquier diferencia se atribuye al empaquetado (desbalance/throttling) y decide.

## 8. Recomendación final

1. **No implementar B ahora.** Registrarla como opción de empaquetado, no como mejora del cuello.
2. **Correr PoC-0 ya**: es barato, no exige refactor, usa herramientas que ya existen (cliente T-D1 + runbook de burst + CYCLE-PROBE + poller), y produce el único número que dimensiona la flota: K_Modal con coalescer de 60 ms e imagen pineada. Dos parches menores al cliente (proxy-auth, 60 ms).
3. **Conseguir el wrapper de Modal de GB** (no está en el repo): sin él no se puede afirmar cómo están hoy `max_inputs`, `cpu`, `mem` ni el decorador — y cualquier plan B/D se configura ahí.
4. Con K_Modal en mano: fijar `max_sessions = max_inputs = K` (hoy 120 opera dentro de la zona degradada `[MEDIDO]`), dimensionar `contenedores = 2000/K` (D), y sólo si ese número resulta operativamente doloroso, activar B con el mecanismo del §5 y validarlo con PoC-B.

**Respuesta a la pregunta final:** la propuesta **no ofrece una mejora arquitectónica** para el cuello de GoldenBridge — la capacidad por loop, que es donde vive el límite medido, no cambia. **Empaqueta réplicas existentes dentro de un contenedor mayor**, con una ventaja operativa real y acotada (menos contenedores y menos arranques fríos) y con costos propios (peor balanceo interno que el scheduler de Modal, mayor dominio de fallo, cambios de arranque hoy inexistentes). Es una decisión de empaquetado que debe tomarse después de medir K en Modal, no una alternativa al trabajo sobre el cuello.
