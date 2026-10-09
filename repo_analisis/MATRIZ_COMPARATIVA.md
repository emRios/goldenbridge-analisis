# Matriz comparativa — motores de media y arquitecturas Media–Conversación

Fuente: informes `informes/01…08`, cada uno con citas `[CÓDIGO]` archivo:símbolo:líneas sobre los commits de `IDENTIDAD.tsv`. Esta matriz resume; la evidencia está en los informes. Ningún repositorio contiene un benchmark reproducible de capacidad: **toda cifra de llamadas por worker es `[PENDIENTE]`** y no se usa aquí.

Etiquetas: `[CÓDIGO]` · `[DOCUMENTACIÓN]` · `[MEDICIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.

---

## 1. La pregunta central: ¿qué hace cada uno con el coordinador del camino por trama?

| repo | ¿el control toca tramas? | ¿quién ejecuta la cadena por trama? | veredicto | evidencia clave |
|---|---|---|---|---|
| **FreeSWITCH** | no (event socket sin audio) | **un thread del SO por llamada**: lee su socket RTP, corre media bugs/ASR, reproduce y corta | **elimina y paraleliza** | `switch_ivr_async.c:speech_callback:5185-5197`; `switch_ivr_play_say.c:1678-1685` (02) |
| **LiveKit server** | no (señal, sala y bus nunca ven RTP) | **una goroutine dueña por pista** (`forwardRTP`), una sola transferencia por paquete | **elimina y paraleliza** | `pkg/sfu/receiver_base.go:forwardRTP:877-1019` (04) |
| **LiveKit SIP** | no (control = fusibles y atómicos) | **goroutines por llamada**, cadena síncrona en el contexto de la llamada, un único hand-off acotado | **elimina y paraleliza** | `pkg/sip/media_pipeline.go:rtpLoop:321-339` (05) |
| **mediasoup** | no (0 cruces IPC por paquete) | **W workers de un hilo y un `uv_loop`**, estado `thread_local`, el router fijo a un worker | **elimina del control; replica loops de media** | `Transport.cpp:1589-1641` → `Router.cpp:669-719` (01) |
| **jambonz** | no (Node sólo ve eventos ESL) | FreeSWITCH local (nativo multihilo) | **elimina** (delegando a un motor nativo) | `tts-streaming-buffer.js:_api:338-345`; `gather.js:717-724` (03) |
| **LiveKit Agents** | no en el proceso principal | **un proceso Python por llamada**; dentro, un loop hace todo por trama (Silero con `run_in_executor` por ventana) | **sólo replica** (K_loop = 1) | `ipc/job_proc_executor.py`; `silero/vad.py:422` (06) |
| **Pipecat** | sí: media y conversación en el mismo loop | todos los pipelines del proceso en **un** loop; 6-7 procesadores con cola y tarea por trama | **sólo replica** (y con más saltos por trama que Golden `[INFERENCIA]`) | `workers/runner.py:141`; `frame_processor.py` (07) |
| **TEN** | sí: audio entre grupos pasa por el engine y entra 2× al loop Python global | un hilo por grupo de extensiones; Python en un loop asyncio con una `Task` por trama | **sólo replica** (proceso por sesión) | `async_extension.py:_proxy_on_audio_frame:394-419` (08) |

**Patrón común de los tres que paralelizan de verdad** `[INFERENCIA]` sobre `[CÓDIGO]`: (1) una **unidad de ejecución barata dueña de cada llamada/pista** (thread nativo o goroutine); (2) la **cadena por trama es síncrona dentro del dueño** — recibir, procesar y emitir sin volver a ningún planificador compartido por trama; (3) el control recibe **eventos semánticos** de baja frecuencia; (4) la afinidad se fija **antes** de que fluya la media (handshake SDP, selección de nodo por sala), y luego demultiplexa el kernel. GoldenBridge hoy incumple (1) y (2): todas sus llamadas comparten un loop, y cada mensaje de audio hace `submit` al pool + `resume` de corrutina en ese loop — el ciclo que la curva de rodilla mide.

---

## 2. Matriz por dimensión

| dimensión | mediasoup | FreeSWITCH | jambonz | LiveKit server | LiveKit SIP | LiveKit Agents | Pipecat | TEN |
|---|---|---|---|---|---|---|---|---|
| **Media fuera del runtime de control** | sí, total (salvo `DirectTransport`) | sí, total | sí (en FS) | sí | sí | sí respecto al principal; no dentro del job | **no** | **no** |
| **Unidad de afinidad** | router→worker de por vida | canal→thread | llamada (`CallSession`) + diálogo SIP | sala→nodo; pista→goroutine | llamada→socket UDP propio | job→proceso | pipeline→loop (por construcción) | grafo; sesión = proceso |
| **Paralelismo entre núcleos** | réplica de loops 1-hilo | threads SO, 1/llamada | control 1 núcleo/host; media FS | goroutines (M:N) | goroutines (M:N) | procesos 1/llamada + proceso de inferencia | sólo inferencia ONNX en hilos | 1 hilo/grupo; Python 1 loop |
| **Workers por proceso/tarea** | Node: 1 worker/proceso · Rust: N hilos/proceso | N threads/proceso | 1 Node + 1 FS por host | 1 proceso/nodo | 1 proceso/contenedor | 1 llamada/proceso | N pipelines/loop | 1 sesión/proceso |
| **Audio a través de IPC** | no (opcional `DirectTransport`) | no (`unicast` opcional) | no (TTS no-stream 1×/enunciado como archivo) | no | no (sí WebRTC al SFU) | 0 | no por defecto; sí en modo bus | memoria `shared_ptr`; 7 saltos de hilo/trama en la referencia |
| **Orden por sesión** | FIFO por canal | cola FIFO + prioritaria por sesión | TCP del WS + loop | 1 lector por pista + munger | 1 lector por SSRC + `SeqWriter` | implícito (1 loop/sesión) | FIFO por procesador en 3 prioridades | FIFO por runloop; intercalado entre fuentes |
| **Protección frente a obsoletos** | UUID no reutilizable + `#closed` | parcial (`Application-UUID`, `CF_TRANSFER`) | por rol (`bugname`), no por generación — defecto | **generación atómica + parada en 2 fases**; `seq` con hueco = cierre | cuarentena de puerto; puntero atómico a generación | `SpeechHandle`, `_interruption_generation` | `context_id` TTS como generación | `cmd_id` + caducidad; audio sin generación |
| **Backpressure** | ninguno en IPC (colas sin tope) | colas 256 por sesión con rechazo; listener 100k → kill | marcas de agua de texto TTS 1000/200 por llamada | señal 200 acotada; colas de media sin tope | **todas por llamada y acotadas** (10 / 100 ms / 1) | cola nativa 200 ms; resto sin tope | ninguno | datos descartados a 12.800; resto sin tope |
| **Barge-in y purga** | no aplica | en el thread dueño por trama; `uuid_break all` vacía cola + `CF_BREAK` | detecta FS, decide Node, purga FS (`uuid_break`, `tts_streaming clear`) | no aplica (flag de silencio) | no hay; búfer post-decisión de ≤100+20(+60) ms | local, VAD → `interrupt` → `clear_buffer`; devuelve `PlaybackFinished{position, transcript}` | purga selectiva por `interruptible`; reset de resampler | Python decide tras ASR remoto; carrera flush/audio en vuelo |
| **Supervisión y reinicio** | muerte por EOF en cascada; sin heartbeat ni reinicio | ninguno | pm2 + `exit(1)`; reintento FS cada 3 s | pánico → exit; nodo sin stats 5 s → reasignación | timeouts de media; reconexión de sala | **handshake, ping 2,5 s, kill 60 s, monitor PSS** | `ErrorFrame` + política | `cmd.Wait` sin reinicio |
| **Drenaje** | ninguno | `fsctl pause` + `shutdown elegant` | diseño correcto **pero roto** (SIGTERM→exit antes del manejador) | `SHUTTING_DOWN` sin tope; **defecto: sigue admitiendo** | health 503 + espera a 0 llamadas | SIGTERM → `drain` (tope 3600 s) | `EndFrame`; SIGTERM cancela | SIGTERM, 2 s, SIGKILL |
| **Dominio de fallo** | proceso del worker (Node) | proceso entero | host | nodo | proceso | **1 llamada** | proceso entero | proceso (= sesión) |
| **Memoria duplicada** | por worker (sin modelos) | ninguna | ninguna en Node | ninguna | por llamada: códecs, soxr, PeerConnection | **por proceso**: ORT por hijo (mitigado con forkserver + `gc.freeze`) | ORT por sesión (Silero + smart-turn) | intérprete + modelos por sesión |
| **Observabilidad para atribuir rodilla** | rusage, dump; sin lag de loop | HEARTBEAT, `playback_ms`; sin lag de ciclo | métricas FS; sin lag de loop | **histograma de latencia de reenvío por paquete**; sin profundidad de cola | latencia E2E in/out por llamada, drift ppm | **pong del heartbeat = lag del loop del hijo**; `event_loop_blocked` | TTFB por procesador | estancia en cola, duración de callbacks |
| **Complejidad de integración (componente)** | alta | muy alta | muy alta | alta | alta | alta | alta (API en migración) | muy alta |
| **Impacto sobre Asterisk/ai-engine (adoptar)** | ai-engine pasa a RTP | reemplaza/duplica Asterisk | reemplaza ambos; no encaja en Modal | WebRTC/SIP en vez del WS | trunk SIP + SFU | reemplaza ambos | ninguno sobre Asterisk; ai-engine debe aceptar flush | posible con extensión WS; no encaja Modal/3.14t |
| **Licencia** | ISC | MPL-1.1 (vendorizadas mixtas) | MIT (+ dep MPL-2.0) | Apache-2.0 (+ MIT ion-sfu) | Apache-2.0 (+ LGPL soxr por cgo) | Apache-2.0; **modelos LiveKit no portables** | BSD-2 | Apache-2.0 **+ condiciones Agora en el núcleo** |
| **Valor para Golden** | ADAPTAR patrones | ADAPTAR patrones | ADAPTAR patrones | ADAPTAR patrones | **ADAPTAR (el más cercano)** | ADAPTAR supervisión/admisión/playback | ADAPTAR contrato de interrupción | ESTUDIAR; no copiar núcleo |

**Ningún repositorio se recomienda adoptar como componente**: todos los que resuelven el cuello lo hacen en un runtime nativo (C, Go) que exige reemplazar Asterisk, ai-engine o el transporte WS, o no encaja en Modal; los que son Python (Agents, Pipecat, TEN) sólo replican. Detalle y criterios en `DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md`.

---

## 3. Correcciones al contrato de referencia `(session_id, session_epoch, turn_id, speech_id, seq)`

El contrato del dictamen del 28-sep sobrevive en su núcleo y se corrige en siete puntos con evidencia:

| # | corrección | evidencia | por qué |
|---|---|---|---|
| C1 | **Carril prioritario por sesión**: `CancelSpeech`/`CloseSession` no esperan detrás de `SpeakChunk` | FreeSWITCH `uuid_break all` vacía la cola y levanta flag (`mod_commands.c:5029-5042`, `switch_core_session.c:1348-1371`); Pipecat `SystemFrame` adelanta a los de datos | la FIFO pura retrasa el corte tanto como chunks haya encolados |
| C2 | **Purga por clase**: al cancelar se purgan `SpeakChunk{speech_id}`; nunca `ConfigureSession`, `SendFunctionCall`, `CloseSession` | Pipecat `FrameQueue.reset:74-87`; TEN cola por clase (`engine/msg_interface/common.c:325-334`) | vaciar todo pierde acciones de negocio |
| C3 | **`SpeechInterrupted` y `PlaybackFinished` llevan `played_ms`/`emitted_ms` medidos en el pacer** además de `spoken_text` | FreeSWITCH `PLAYBACK_STOP{playback_ms}` (`switch_ivr_play_say.c:2013-2056`); Agents `PlaybackFinishedEvent{playback_position, synchronized_transcript}` (`voice/io.py:111-118`) | Conversación trunca con un número, no con una estimación implícita |
| C4 | **Parada de generación en dos fases** en Media: marcar → esperar trabajadores de la generación vieja → reiniciar estado → abrir la nueva | LiveKit `receiver_base.go:restartInternal:310-412` | evita que un chunk en vuelo de la generación vieja sobreviva al reinicio del resampler/pacer |
| C5 | **Confirmación de ejecución separada del encolado** y **caducidad** por comando: `Accepted` ≠ `Done`; vencido el plazo se sintetiza error y se descarta la respuesta huérfana | FreeSWITCH `+OK` = encolado, `CHANNEL_EXECUTE_COMPLETE{Application-UUID}` = hecho; TEN `path_timer.c:61-114`, `path_table.c:467-489` | hoy `SendFunctionCall` no tiene timeout ni confirmación |
| C6 | **Heartbeat con lag**: Media publica `Heartbeat{loop_lag_ms, sessions, queue_depth}`; el supervisor y la admisión lo usan en lugar de CPU% | Agents `proc_client.py:_monitor_task:118-122`, `supervised_proc.py:486-495`, admisión `worker.py:1322-1368` | el cuello de Golden es coordinación, no CPU |
| C7 | **`seq` con hueco ⇒ cierre**, duplicado ⇒ recorte | LiveKit `signal.go:signalMessageReader.Read:233-252` | ya estaba en el contrato; ahora con precedente verificado |

Pendiente para una fase posterior (exige cambiar ai-engine): **generación en el audio Media→engine** — jambonz y TEN demuestran que purgar sólo por orden deja carreras (`gather.js:1015-1019`; TEN `_interrupt:203-213`).
