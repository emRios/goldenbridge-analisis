# Encargo para Codex: dictamen independiente sobre el MediaWorker nativo de GoldenBridge

Fecha: 2026-10-09. Actúa como arquitecto de sistemas concurrentes de media en tiempo real con experiencia en C++ y en runtimes de voz. Quiero un **dictamen independiente**, basado en código y en evidencia, sobre tres decisiones ya tomadas provisionalmente y un experimento pendiente de diseñar. No implementes nada. No modifiques GoldenBridge, ai-engine ni los repositorios descargados. No hagas commits.

Este encargo reabre expresamente el split Media–Conversación y el motor Media nativo; la prohibición histórica de `CLAUDE.md` no lo bloquea. Conserva las demás reglas del proyecto: evidencia etiquetada, ninguna capacidad inventada, lectura del estado efectivo y separación entre hechos, mediciones e inferencias.

Etiqueta cada afirmación: `[CÓDIGO]` (archivo:símbolo:líneas), `[DOCUMENTACIÓN]`, `[MEDICIÓN]`, `[INFERENCIA]`, `[PENDIENTE]`.

---

## 1. Contexto que debes leer, en este orden

1. `CLAUDE.md` — reglas de trabajo y hechos medidos del cuello.
2. `context/ARQUITECTURA_STACK.md` — arquitectura autoritativa; no la cuestiones.
3. `doctos/AUDITORIA_REFACTOR_websocket_server_api_08932a6.md` — mapa del monolito, ownership, invariantes, hallazgos H1-H8.
4. `doctos/DICTAMEN_MULTIPROCESO_POR_TAREA_MODAL_20260928.md` — por qué N Goldens por tarea sólo empaqueta réplicas.
5. `doctos/DICTAMEN_FRONTERA_MEDIA_CONVERSACION_20260928.md` — la frontera, el contrato `(session_id, session_epoch, turn_id, speech_id, seq)` y los hechos de ai-engine.
6. `repo_analisis/README.md`, `repo_analisis/MATRIZ_COMPARATIVA.md`, `repo_analisis/PROPUESTA_APLICABLE_GOLDEN.md`, `repo_analisis/DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md` y los ocho informes de `repo_analisis/informes/`.
7. `prompts/EVENT_LOOP.md`, aplicando las correcciones de `CLAUDE.md`.

Código: GoldenBridge rama `api` @ `08932a64645bff74030ad2bd63543dd2334b8101` (el checkout tiene trabajo local en otra rama: usa `git show api:<ruta>` y `git grep … api`). ai-engine `Asterisk-AI-Voice-Agent-FX` rama `feat/goldenbridge-modal-coalescer-60ms` @ `d059295d2468486bc6f8d2c883a6e41599d695f9`. Repositorios auditados, sin modificar, en `repo_analisis/repos/` (commits en `repo_analisis/IDENTIDAD.tsv`).

---

## 2. Resumen de lo establecido (no re-derivar; sí cuestionar si encuentras evidencia en contra)

### El cuello `[MEDICIÓN]`

GoldenBridge atiende todas las llamadas con un solo event loop (Python 3.14.7 free-threaded, uvloop). Por cada mensaje de audio de cada llamada el loop hace un `submit` al pool `vad` (4 hilos) y un `resume` de la corrutina. **La CPU no es la restricción**: 100 llamadas piden ~2,3 núcleos de DSP. El trabajo crece 2,9× de 1-5 a 41+ sesiones y el ciclo 35×. Rodilla: 16-25 sesiones en `.143` (medida a 20 ms / 50 fps, con co-tenencia y spin); techo 51 (3.14t), 58 con afinidad. `steno_p90 = 0,10 ms` plano ⇒ la orquestación STT/LLM/TTS no pesa; **el cuello es el camino de audio a cadencia de trama**. Silero: 401 µs/chunk × 31,25/s = 0,0125 núcleos/llamada (72 % del job). SmartTurn: 0,0704 inv/s-sesión, 0,0043 núcleos/llamada ⇒ ~61 ms de CPU por inferencia, bajo un lock global. TTFAB real p50 558 ms, p90 1.330 ms. Modal: arranque frío 33 s.

Desde entonces ai-engine coalesce la entrada a **60 ms / 480 B** (16,7 mensajes/s por llamada en vez de 50). La rodilla con 60 ms **no está medida** `[PENDIENTE]`.

### Lo concluido en los dictámenes `[CÓDIGO]`+`[INFERENCIA]`

- N procesos Golden completos por tarea Modal = empaquetado de réplicas; no cambia la capacidad por loop.
- La frontera Media–Conversación es coherente con el código. Entre ambos **no cruza audio**, sólo texto y eventos. Pero el split por sí solo **no** resuelve el cuello: si Media conserva un loop único para todas sus llamadas, el cuello sigue dentro de Media.
- Los repositorios que eliminan el coordinador por trama (FreeSWITCH en C, LiveKit server y LiveKit SIP en Go) comparten un mecanismo: **una unidad de ejecución dueña por llamada o por pista, que recibe, procesa y emite cada trama de forma síncrona dentro de sí**, con el control recibiendo sólo eventos. Los que mantienen el audio en un loop (LiveKit Agents, Pipecat, TEN, los tres en Python) sólo replican procesos.
- **Ninguno de esos precedentes corre inferencia de IA por trama dentro del trabajador dueño.** Es la combinación sin precedente; por eso hace falta el experimento.

### Hechos verificados que condicionan el diseño `[CÓDIGO]`

- `websockets==17.0.1` (modo síncrono) crea un hilo lector por conexión dentro de la librería (`sync/connection.py:130-136`) y entrega cada mensaje por un traspaso de hilo; permite un receptor y un emisor concurrentes, no dos receptores (L312-323, L470-481).
- El extractor de features de SmartTurn es propio: `src/processors/whisper_features.py` (log-mel estilo Whisper en numpy; `transformers` no está instalado en 3.14t). Hay que portarlo con paridad.
- soxr (`soxr==1.1.0`) y ONNX Runtime (`onnxruntime==1.24.4`) ya son librerías C; Python sólo las envuelve.
- Oráculo de paridad existente: `test/reference/features.npz`, `vad_probs.npz`, `stack.json` (features, probabilidades de Silero y 14 decisiones de SmartTurn, umbral 0,6, sobre un corpus fijo; CPU con `avx512_vnni`). El gate corre en `docker/arranque.sh` antes de servir.
- Pruebas de carreras entre hilos ya existentes: `tools/racetests/t1…t8`.
- `noisereduce` es Python puro y casi nunca corre a 60 ms (ventana mínima de 100 ms): puede quedar fuera del port sin cambiar comportamiento.
- El lock global de SmartTurn, a 100 llamadas: 7 inferencias/s × ~61 ms ⇒ ~43 % de ocupación; empieza a encolar. El comentario del código afirma que `Run` de ORT no es seguro entre hilos; no está verificado.
- ai-engine descarta todos los JSON crudos de GB (su dispatcher exige `call_id`); no hay flush de reproducción; no reconecta si cae el WS a GB.

### Restricciones fijas

Python 3.14.7 free-threaded para Conversación · ORT 1.24.4 · ORT secuencial · `GB_ORT_INTRA_OP=1` · `OMP_NUM_THREADS=1` · `OPENBLAS_NUM_THREADS=1` · gate de referencia por CPU (`avx512_vnni`) · aislamiento por sesión · coalescer de entrada de 60 ms · despliegue en Modal (`@modal.web_server`, 1 input por WS, `max_inputs` por contenedor) · Asterisk y ai-engine se quedan en `.143`.

---

## 3. Las tres decisiones provisionales que debes auditar

### Decisión 1: el MediaWorker se reescribe en un lenguaje nativo, no en Python sin GIL

Se analizó primero Python 3.14t con un hilo (o dos) por llamada. Riesgos propios de Python que los precedentes no enfrentan: recolector de basura que detiene todos los hilos, contención de refcount sobre la sesión ORT y numpy compartidos, cientos de hilos Python planificados por el SO, y, en el escenario de dos hilos, unos 25 campos de `SessionState` que hoy son seguros sólo por correr en el mismo loop y pasarían a ser carreras reales. Se decidió explorar un MediaWorker nativo con Conversación en Python.

### Decisión 2: C++ (17/20) en vez de C

Razones: la API idiomática de ORT es C++ (RAII sobre la API C) y Silero corre 31 veces/s por llamada; el cleanup idempotente por llamada con tres conexiones y varios hilos es un destructor en C++ y un sistema de refcounts y locks a mano en C (el de FreeSWITCH); las librerías WebSocket dominantes en C imponen su propio event loop, y las de C++ (Beast, IXWebSocket, uWebSockets) permiten hilo bloqueante por conexión; `std::jthread`/`stop_token`/`std::atomic` para la cancelación cooperativa; `extern "C"` y nanobind dejan abiertas tanto la entrega como proceso aparte como la extensión dentro del proceso Python. C sólo gana si el worker fuera un módulo de Asterisk/FreeSWITCH o necesitara una ABI estable fuera de la imagen. Disciplina pendiente: cero asignaciones y sin excepciones en el camino por trama (atraparlas en el borde del hilo o compilar ORT con `ORT_NO_EXCEPTIONS`).

### Decisión 3: tres escenarios de concurrencia en C++, con A como principal

| | **A · un hilo por llamada, reactor propio** | **S · W hilos-shard, muchas llamadas por hilo** (modelo mediasoup) | **B · hilo de entrada + hilo de salida por llamada** |
|---|---|---|---|
| Cómo espera | un `epoll`/`io_context` por llamada sobre sus 3 sockets (ai-engine, Steno, Ursula) y timers | un reactor por shard sobre los sockets de todas sus llamadas | dos reactores por llamada: entrada (ai-engine + Steno), salida (Ursula + timer del pacer) |
| Dueño del estado | un hilo, sin locks | un hilo por shard, sin locks entre llamadas | dos hilos; estado compartido diseñado: flag de corte, generación activa, `tts_playing` para el VAD; `std::atomic` + mutex por llamada |
| Latencias dentro de la llamada | acopladas: Steno lento o SmartTurn (~61 ms) frena el pacer de esa llamada | acopladas entre llamadas del shard | aisladas |
| Aislamiento entre llamadas | total | el riesgo principal: una llamada con socket lento bloquea su shard; exige envíos no bloqueantes y colas acotadas por llamada | total |
| SmartTurn | a un pool pequeño; un traspaso por turno | obligatorio fuera del shard | en entrada o al pool |
| Barge-in | variable local | variable local del shard | flag atómico entrada→salida, corte ≤ 1 tick del pacer |
| Traspasos por mensaje | 0 | 0 | 0 por dirección |
| Hilos a 100 llamadas | ~100 + pool | W ≈ núcleos + pool | ~200 + pool |
| Balanceo | ninguno | al aceptar, afinidad de por vida; desbalance ~binomial | ninguno |
| Capacidad | K_loop deja de existir; K_worker lo fijan CPU y planificador | K_loop = llamadas/shard; K_worker = W × K_loop menos desbalance (paralelismo, no eficiencia por loop) | como A |
| Verificación | ASan/UBSan | ASan/UBSan | TSan obligatorio |
| Precedente | FreeSWITCH | mediasoup | LiveKit SIP (goroutines de lectura y de mezcla/escritura) |
| Riesgo principal | costo del planificador con cientos de hilos `[PENDIENTE]` | una llamada lenta daña a su shard; desbalance | carreras de datos; cleanup con dos hilos vivos |

Veredicto provisional: A como escenario principal del experimento; S como contraste de capacidad por núcleo; B como contraste de aislamiento de latencias. Supuestos: Conversación sigue en Python; el MediaWorker arranca como proceso aparte con ABI C hacia Python, dejando la extensión dentro del proceso para después.

Definiciones obligatorias: `K_loop` = capacidad de un loop Media · `K_worker` = capacidad de un proceso Media · `K_task` = suma efectiva de workers limitada por CPU, memoria y desbalance · `K_flota` = capacidad agregada de tareas Modal. **No presentes `N × K_loop` como aumento de K por loop.**

---

## 4. Preguntas que debes responder

1. **Tesis.** ¿Es correcto que el mecanismo "dueño por llamada con cadena por trama síncrona" ataca la causa medida? ¿Hay evidencia en el código de GoldenBridge (`_handle_audio_batch`, `process_audio_batch`, `_process_frame_sync`, el pool `vad`) de un costo por trama que ese mecanismo **no** eliminaría? ¿Qué parte del ciclo medido (`submit`, `resume`, `post`) sobrevive en cada escenario?
2. **Python descartado.** ¿Fue prematuro? Enumera qué habría que medir para rehabilitar Python 3.14t con un loop por llamada (hilo por llamada con su propio loop asyncio) antes de reescribir en C++, y cuánto costaría esa medición frente al port.
3. **C contra C++.** ¿Compartes las razones? ¿Falta alguna consideración (toolchain en la imagen `debian:trixie-slim`, enlace con `libonnxruntime` 1.24.4 fuera del wheel de Python, versión de libsoxr, TLS, tamaño del binario, tiempo de compilación en CI)?
4. **Escenarios A/S/B.** Corrige la tabla fila por fila donde esté mal o incompleta. Añade filas que falten (por ejemplo: pila por hilo y memoria a 100 y 500 llamadas; comportamiento ante `SIGTERM`/drenaje en Modal; cómo encaja `max_inputs`; qué pasa con el watchdog de cierre de turno, que hoy consulta cada 50 ms). ¿Cuál agrega más complejidad y cuál más riesgo? ¿Cambiarías el escenario principal?
5. **Lo compartido.** Sesiones ORT de Silero y SmartTurn, el lock global de SmartTurn (~43 % a 100 llamadas), el pool de inferencia. ¿Cómo lo resolverías en cada escenario? ¿Varias sesiones del modelo, verificar la seguridad de `Run` entre hilos, IoBinding? Cuantifica memoria si puedes.
6. **Paridad.** Diseña la compuerta de paridad del port: log-mel propio contra `features.npz`, probabilidades de Silero contra `vad_probs.npz`, decisiones contra `stack.json`, soxr misma versión, tablas µ-law, gates de barge-in. ¿Qué tolerancias? ¿Qué puede romper la paridad aunque el modelo y ORT sean los mismos (orden de operaciones en float, FFT distinta, normalización)?
7. **Frontera con Python.** Proceso aparte con ABI C / IPC local, o extensión dentro del proceso de Conversación (nanobind). Ventajas, riesgos, y cómo afecta al contrato `(session_id, session_epoch, turn_id, speech_id, seq)` y a sus siete correcciones de `MATRIZ_COMPARATIVA.md` §3 (carril prioritario para cancelar, purga por clase, `emitted_ms` del pacer, parada en dos fases, confirmación y caducidad, heartbeat con lag, `seq` con hueco ⇒ cierre).
8. **Modal.** El MediaWorker nativo detrás de `@modal.web_server`: ¿acepta el WS directamente sin relay Python? ¿Cómo convive con el gate de referencia de `arranque.sh` y con el proceso Python de Conversación en el mismo contenedor? ¿Qué cambia en memoria y arranque frío?
9. **Riesgos que no vimos.** Lista lo que falte: planificador con cientos de hilos, prioridad de hilos, `SO_RCVBUF`, TLS por conexión (tres por llamada), reconexión de Steno/Ursula, límites de descriptores, observabilidad para atribuir la rodilla (lag por hilo, profundidad de cola por llamada).
10. **Experimento.** Diseña el experimento decisivo: laboratorio mínimo (sin Conversación ni split completo), simuladores locales de Steno y Ursula, cliente `tools/loadtest/td1_client.py` a 60 ms con habla y barge-in, escalera 10 → 25 → 50 → 75 → 100 llamadas, misma caja y mismo corpus para A, S, B y el monolito actual. Métricas: latencia trama→decisión VAD p50/p90/p99 por banda, detección→corte de envío en barge-in, profundidad de cola por llamada, CPU por hilo y total, hilos, RSS, lag del generador separado del lag de Golden. Criterios falsables de aceptación y de rechazo, y cómo atribuir si la ganancia viene de eficiencia o de paralelismo (comparar A contra S con W = 1).
11. **Esfuerzo.** Estimación por fase (port con paridad, laboratorio, medición, integración con el contrato) y riesgo dominante de cada una. `[INFERENCIA]`, con supuestos explícitos.

---

## 5. Entrega

Un documento `doctos/DICTAMEN_CODEX_MEDIAWORKER_CPP_20261009.md` con:

1. Dictamen: aceptar, ajustar o rechazar cada una de las tres decisiones.
2. Tabla A/S/B corregida y completada.
3. Respuestas explícitas a las once preguntas.
4. Diseño del experimento con criterios falsables.
5. Lista de riesgos, con qué medición cierra cada uno.
6. Estimación de esfuerzo.
7. Lo que harías distinto, con su evidencia.

Criterios de calidad: cita archivo, símbolo y líneas; sigue callers y rutas de error, no sólo el happy path; no uses documentación comercial como `[MEDICIÓN]`; no afirmes llamadas por worker, CPU ni latencias sin medición; si un dato no es verificable con el material disponible, márcalo `[PENDIENTE]` y di qué lo cerraría.

La pregunta final que debes responder es:

> ¿Un MediaWorker en C++ con un dueño por llamada elimina el cuello medido de GoldenBridge, o sólo lo traslada; y cuál de los tres escenarios debe ponerse primero bajo prueba?
