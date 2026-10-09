# Propuesta aplicable a GoldenBridge — MediaWorker con dueño por llamada

Diseño derivado de los ocho informes de `informes/`, comparado con el estado real de GoldenBridge (`api` @ `08932a6`) y ai-engine (`d059295`). No es una combinación de proyectos: toma el **mecanismo** que los tres sistemas que paralelizan comparten (FreeSWITCH, LiveKit server, LiveKit SIP) y lo traslada a lo que Python 3.14 free-threaded permite por primera vez.

Etiquetas: `[CÓDIGO]` · `[DOCUMENTACIÓN]` · `[MEDICIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.

---

## 1. Tesis

**El cuello de Golden no se resuelve separando Media de Conversación, sino cambiando quién ejecuta la cadena por trama.** Hoy un único event loop coordina las tramas de todas las llamadas, y cada mensaje de audio le cuesta un `submit` al pool `vad` más un `resume` de corrutina `[MEDICIÓN]` (CLAUDE.md: el ciclo crece 35× con 2,9× de trabajo; `steno_p90` plano). Los tres repositorios que eliminan ese coordinador usan una **unidad de ejecución dueña por llamada** — thread nativo en FreeSWITCH, goroutine en LiveKit — que **recibe, procesa y emite cada trama de forma síncrona dentro de sí misma** `[CÓDIGO]` (`switch_ivr_async.c:5185-5197`; `receiver_base.go:877-1019`; `media_pipeline.go:321-339`). Los tres repositorios Python que mantienen el audio en un loop asyncio (Pipecat, LiveKit Agents, TEN) **sólo replican** `[CÓDIGO]`.

En Python con GIL ese mecanismo no existía: un hilo por llamada no corría en paralelo. **Con 3.14t sí corre** `[INFERENCIA]`, y Golden ya depende de eso para su pool DSP. La propuesta es aplicarlo a toda la cadena por trama, no sólo al DSP.

**Límite del precedente (corrección del 2026-10-09).** Los tres repositorios prueban el **mecanismo de reparto**, no la capacidad con la carga de Golden. Están escritos en C (FreeSWITCH) y Go (LiveKit server y SIP), y su trabajo por trama es mucho más liviano: LiveKit server sólo reenvía paquetes; LiveKit SIP decodifica, remuestrea y mezcla; sólo FreeSWITCH puede ejecutar análisis dentro del hilo de la llamada. **Ninguno corre inferencia de IA por trama dentro del trabajador dueño**; en el ecosistema LiveKit esa parte vive en LiveKit Agents, que es Python y sólo replica. Por lo tanto:

| afirmación | evidencia |
|---|---|
| el trabajador por llamada elimina el coordinador del camino por trama | `[CÓDIGO]` en los tres |
| el cuello de Golden es ese coordinador | `[MEDICIÓN]` (CLAUDE.md) |
| el mecanismo ataca la causa correcta | `[INFERENCIA]` |
| el mecanismo aguanta Silero y SmartTurn por trama | **sin precedente** — `[PENDIENTE]` |
| funciona en Python 3.14t con 100 llamadas | `[PENDIENTE]`: riesgos propios de Python que los precedentes no enfrentan — GC que detiene todos los hilos, contención de refcount sobre la sesión ORT y numpy compartidos, el lock global de SmartTurn con 100 hilos, ~200 hilos Python planificados por el SO |

La propuesta es una **hipótesis a falsar con el PoC de §10**, no una solución copiada. Si el MediaWorker se escribiera en Go o Rust, esos riesgos de Python desaparecen y el riesgo principal pasa a ser la **paridad de decisiones** del modelo (soxr, máquina de estados de Silero, features de SmartTurn, gates de barge-in), verificable contra las referencias congeladas de `test/reference/`.

Separar Media de Conversación sigue siendo correcto y necesario (frontera del dictamen del 28-sep), pero **por sí solo replica el problema dentro de Media**: si Media conserva un loop único para todas sus llamadas, su K_loop es el de hoy.

---

## 2. Frontera definitiva Media–Conversación

Se mantiene la del dictamen del 28-sep con las siete correcciones C1-C7 de `MATRIZ_COMPARATIVA.md` §3. Resumen:

- **Media** es dueño exclusivo de: WS con ai-engine, orden y buffers, decodificación/resampleo, Silero y SmartTurn por sesión, detección de inicio/fin de habla, barge-in local, STT streaming y Finalize, cabeza de interrupción, TTS y pacer, presupuesto de salida, generación activa, cleanup técnico.
- **Conversación** es dueño exclusivo de: historial, campaña, variables, router, LLM, tools y correlación, transferencia/hangup/terminales, timers conversacionales, decisión del texto, truncado del historial a lo reproducido.
- **Entre ambos: cero audio.** Sólo eventos y comandos identificados.

Evidencia de que esta frontera es la correcta y no otra: jambonz (decisión en Node, audio en FS), FreeSWITCH (event socket sin audio) y mediasoup (`AudioLevelObserver` emite eventos, nunca tramas) la trazan en el mismo lugar `[CÓDIGO]`. Pipecat y TEN, que la trazan dentro del mismo loop, son exactamente los que no escalan por loop `[CÓDIGO]`.

---

## 3. Interior del MediaWorker

### 3.1 Modelo de concurrencia: dueño por llamada, no loop compartido

```
 proceso MediaWorker (Python 3.14t, uno por tarea Modal en la fase 1)
 ├── hilo aceptador  — servidor WS síncrono (websockets.sync, 1 hilo por conexión)
 │
 ├── por llamada i  (todos en paralelo real, sin loop compartido)
 │    ├── hilo de entrada_i   recv(ai-engine) → decod/resample → Silero → gate SmartTurn
 │    │                       → send(Steno) en el mismo hilo → detección inicio/fin/barge-in
 │    │                       → eventos a Conversación (cola acotada, sin await)
 │    ├── hilo de salida_i    recv(Ursula) → pacer → send(ai-engine) bajo lock de envío
 │    └── estado_i            MediaSession: buffers, VAD, LSTM, resamplers, speech activo
 │
 ├── recursos compartidos de sólo inferencia
 │    ├── Silero InferenceSession   Run concurrente (ya así hoy: vad_processor.py copy.copy)
 │    └── smart-turn InferenceSession  lock global (ya así hoy; 0,07 inv/s por sesión)
 │
 └── hilo de control     eventos/comandos con Conversación; heartbeat con lag
```

Por qué así, contra la evidencia:

| decisión | precedente | en Golden hoy |
|---|---|---|
| **la cadena por trama corre síncrona en el dueño**; ni `submit` ni `resume` por mensaje | FreeSWITCH media bug en el thread de sesión; LiveKit SIP `rtpLoop`; LiveKit `forwardRTP` `[CÓDIGO]` | cada mensaje: `run_in_executor` + `await` en el loop compartido `[CÓDIGO]` SMART L1080-1087 |
| **el socket del cliente lo lee el dueño**, no un relay | LiveKit SIP bind por llamada (`media_port.go:468-513`); mediasoup socket dentro del worker `[CÓDIGO]` | lector y consumidor en el loop compartido |
| **las conexiones STT/TTS de la llamada viven en sus hilos** | FS: ASR alimentado desde el thread de sesión `[CÓDIGO]` | Steno/Ursula por llamada, pero sobre el loop compartido |
| **un único hand-off acotado por dirección** | LiveKit SIP cola socket→SSRC de 10 (`readStream.write:164-187`) `[CÓDIGO]` | inbox ilimitado (`inbox_maxsize: 0`) |
| **modelos compartidos sólo como recurso de inferencia** | — | ya así: Silero `Run` concurrente, estado LSTM por sesión `[CÓDIGO]` VAD L210-L214 |

Disponibilidad técnica verificada: `websockets==17.0.1` está pineado en `requirements-314t.lock` y trae `websockets.sync.server` (hilo por conexión) y `websockets.sync.client` `[CÓDIGO]`. La seguridad de enviar desde un hilo mientras otro recibe en la misma conexión síncrona se verifica en el PoC `[PENDIENTE]`.

### 3.2 Número y rol de hilos

Por llamada: **2 hilos** (entrada y salida) más el DSP ejecutado dentro del de entrada. 100 llamadas ≈ 200 hilos + aceptador + control. La CPU no es la restricción: 100 llamadas piden ~2,3 núcleos de DSP `[MEDICIÓN]`. Lo que hay que medir es el costo del planificador del SO con 200 hilos Python y las pausas de GC de 3.14t `[PENDIENTE]`.

Alternativa si 200 hilos resultan caros: **W hilos-shard**, cada uno dueño de un subconjunto fijo de llamadas con su propio loop, el modelo Rust de mediasoup (`rust/src/worker/utils.rs:54-97`, estado `thread_local`) `[CÓDIGO]`. Ojo: eso es W × K_loop dentro de un proceso — paraleliza, no sube K_loop — y es la opción "varios event loops" que el usuario archivó en `EVENT_LOOP.md` §4C. Se mide como contraste, no como diseño principal.

### 3.3 Ubicación de cada pieza

| pieza | dónde | nota |
|---|---|---|
| decod µ-law, resampleo soxr | hilo de entrada | stateful, nunca compartido |
| Silero | hilo de entrada, `Run` sobre sesión compartida | 31,25 inv/s/llamada; sin pool intermedio |
| SmartTurn | hilo de entrada, bajo el lock global actual | 0,07 inv/s/llamada; candidato a proceso de inferencia compartido sólo si el lock se vuelve cola `[PENDIENTE]` (Agents `inference_proc_lazy_main.py`, corrigiendo su serialización) |
| envío a Steno, Finalize, cabeza de interrupción | hilo de entrada | el orden entrada→Steno queda garantizado por ser el mismo hilo |
| Ursula, pacer, presupuesto | hilo de salida | presupuesto reducido (§5) |
| detección de barge-in | hilo de entrada | corta vía flag atómico que el hilo de salida comprueba por trama (patrón `CF_BREAK` de FreeSWITCH) |

---

## 4. Distribución y afinidad sin relay por trama

- **Modal**: `@modal.web_server(puerto)` lanza el proceso MediaWorker; Modal proxya cada WS al contenedor y cuenta 1 input por conexión `[DOCUMENTACIÓN]` (Modal, 28-sep). `max_inputs` del contenedor = K_task objetivo.
- **Dentro del contenedor**: el servidor WS síncrono del propio MediaWorker acepta la conexión y le asigna su hilo dueño. **No existe relay Python**: el socket lo lee el dueño desde el primer byte. Equivale al bind por llamada de LiveKit SIP, con el accept del kernel haciendo de demultiplexor `[INFERENCIA]`.
- **Fase 1: un MediaWorker por tarea.** La variante de varios procesos MediaWorker por tarea hereda el análisis del dictamen multiproceso (socket compartido, desbalance ~binomial) y sólo se considera si un proceso no llena la tarea.
- **Afinidad de comandos**: Conversación direcciona por `session_id`; el MediaWorker es el único dueño, así que no hay enrutado interno.
- **Admisión**: por `max_inputs` en Modal y, dentro, por lag medido (C6), no por CPU% — LiveKit Agents usa CPU% (`worker.py:_DefaultLoadCalc:84-114`), incorrecto para el cuello de Golden.

---

## 5. Secuencia completa de barge-in

1. Hilo de entrada: Silero + gates confirman habla durante TTS.
2. Mismo hilo, sin esperar a nadie: levanta `break` en el `speech` activo (flag atómico) e invalida la generación (C4).
3. Hilo de salida: en la siguiente trama ve el flag, deja de enviar y descarta su cola (`sender.abort()` de hoy) y lo pendiente de Ursula; registra `emitted_ms`.
4. Hilo de entrada: snapshot y limpieza de `turn_audio`, reanuda Steno y le entrega la cabeza de la interrupción (pasos 4-6 de hoy, ya locales `[CÓDIGO]` WS L8493-8611).
5. Media emite por el carril prioritario `SpeechInterrupted{speech_id, emitted_ms, spoken_text}` (C1, C3).
6. Conversación cancela su stream LLM y trunca el historial; el canal FIFO garantiza que eso ocurre antes del `TurnCompleted` del turno interruptor.
7. El siguiente `Speak` lleva generación nueva; los `SpeakChunk` de la vieja se descartan en Media.

**Lo que no se puede retirar** `[CÓDIGO]`: lo ya entregado a ai-engine (su cola de reproducción y jitter de ~300 ms) y lo ya escrito a AudioSocket. Dos palancas, en orden de costo:

- **Reducir el presupuesto de salida de Golden** (hoy 0,6 s, WS L1235) — la lección de LiveKit SIP: dimensionar el búfer posterior al punto de decisión en lugar de construir un protocolo de purga (`mixer.go:30-33`) `[CÓDIGO]`. Medible punta a punta sin tocar ai-engine.
- **Orden de flush hacia ai-engine**: mensaje `PlaybackClear{speech_id}` que el proveedor convierta en `ProviderBargeIn` con `call_id` (costura latente verificada en `d059295`, ENG:8213) `[CÓDIGO]`.

---

## 6. Caídas

| cae | efecto | política |
|---|---|---|
| hilo de una llamada | sólo esa llamada | cierre idempotente de sus recursos; `SessionClosed{error}` |
| proceso MediaWorker | todas sus llamadas (inevitable: posee el audio) | Modal repone el contenedor; ai-engine debe reconectar — **hoy no lo hace** `[CÓDIGO]` DG:1762-1804 |
| Conversación | ninguna llamada pierde audio | Media entra en modo huérfano: farewell configurado y hangup con gracia |
| canal Media↔Conversación | igual que caída de Conversación | heartbeat (C6) + `seq` con hueco (C7) |

Supervisión: handshake, heartbeat con lag y kill por inactividad, tomados de `supervised_proc.py` de LiveKit Agents `[CÓDIGO]`.

---

## 7. Compatibilidad con las restricciones fijas

- **Python 3.14.7 free-threaded**: requisito, no obstáculo. Es lo que hace viable el dueño por hilo.
- **ORT 1.24.4 secuencial, `GB_ORT_INTRA_OP=1`, `OMP/OPENBLAS=1`**: sin cambio. Cada `Run` sigue siendo de un hilo; el paralelismo viene de llamar desde muchos hilos a la sesión compartida.
- **Gates de modelo y `avx512_vnni`**: sin cambio; un solo gate por contenedor en `arranque.sh`.
- **Aislamiento por sesión**: se refuerza, porque el estado de cada llamada pasa a ser propiedad de un hilo.
- **Coalescer de 60 ms**: compatible; reduce a un tercio las tramas que cada hilo procesa.

---

## 8. Qué puede ser Python y qué no

**Python 3.14t, a demostrar con el PoC**: todo el MediaWorker descrito. La CPU sobra; la pregunta abierta es si el planificador del SO, la contención de refcount en objetos compartidos y el GC permiten 100 llamadas por proceso.

**Si el PoC falla**, la alternativa de alto impacto es un MediaWorker nativo (Go, siguiendo LiveKit SIP) con Silero/SmartTurn vía ORT C API y Python sólo en Conversación. Costo: reescribir DSP, STT/TTS streaming y pacer; perder los gates de referencia actuales hasta re-generarlos. No se recomienda abrirla antes de medir.

---

## 9. Capacidad: qué mejora y qué no

```
K_loop   — deja de existir como cuello: no hay loop compartido por trama
K_worker — llamadas por proceso: limitadas por CPU (~2,3 núcleos/100 llamadas de DSP), planificador y GC   [PENDIENTE]
K_task   — = K_worker en la fase 1 (un proceso por tarea)
K_flota  — 2000 / K_task tareas
```

Esto **no** es `N × K_loop`: no se replican loops, se elimina el loop del camino por trama. Si el PoC muestra que el costo por llamada crece lineal con la concurrencia en lugar de con la pendiente actual (2,9× de trabajo frente a 35× de ciclo), la mejora es de eficiencia, no de paralelismo empaquetado.

---

## 10. PoC mínimo con criterios falsables

**Objetivo**: medir K_worker del modelo "dueño por hilo" contra el monolito actual, con el mismo pipeline DSP y el mismo hardware.

**Alcance mínimo**: no hace falta Conversación ni el split. Un MediaWorker de laboratorio que: acepta WS con `websockets.sync`, por llamada corre decod → soxr → Silero → gate SmartTurn en su hilo, envía a un Steno simulado local, y en salida reproduce un TTS pregrabado con el pacer. Cliente: `td1_client.py` a 60 ms con habla (ya existe; requiere el parche de cadencia).

**Comparación**: mismo escalón 10 → 25 → 50 → 75 → 100 llamadas contra Golden actual en la misma caja.

**Métricas**: latencia trama→decisión VAD p50/p90/p99 por banda de concurrencia (análogo de `cycle_p90`), tiempo detección→corte de envío en barge-in, profundidad de cola por llamada, CPU del proceso, número de hilos, pausas de GC, RSS.

**Criterios**:
- **Acepta** si a 100 llamadas la latencia trama→decisión p90 queda en la banda que hoy se mide a 6-10 sesiones (`cycle_p90` ≈ 4,2 ms) y p99 < 20 ms, sin errores ni pérdidas de trama.
- **Rechaza** si la latencia p90 crece con la concurrencia con pendiente igual o peor que la curva actual, o si el GC o el planificador producen pausas > 60 ms (una trama coalescida).
- **Atribuye** comparando contra la variante de W hilos-shard con loop (§3.2) en la misma corrida: si ambas rinden igual, la ganancia es de paralelismo, no del modelo de dueño.

Duración estimada: 5-8 días-persona para el laboratorio y 1 ventana de medición `[INFERENCIA]`.
