# 06 — LiveKit Agents (Python): dispatcher, pool de procesos, IPC, interrupción

> Auditoría comparativa contra GoldenBridge (rama `api` @ `08932a6`, ver `../BRIEF_GOLDEN.md`).
> Repositorio **no modificado**, nada instalado ni ejecutado. Rutas relativas a
> `repos/livekit-agents/` salvo indicación. Abreviaturas de ruta:
> `A/` = `livekit-agents/livekit/agents/`, `P/` = `livekit-plugins/`.
>
> Etiquetas: `[CÓDIGO]` archivo:símbolo:líneas · `[DOCUMENTACIÓN]` docstring/README del repo ·
> `[MEDICIÓN]` benchmark reproducible del repo · `[INFERENCIA]` · `[PENDIENTE]`.

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/livekit/agents.git` |
| branch | `main` (clon superficial: `git rev-parse --is-shallow-repository` = `true`) |
| commit | `786a76622c8be48a2a66d9dbc5f97dab96e8e504` |
| fecha (`git log -1 --format=%cI`) | `2026-10-09T14:35:17-04:00` |
| último mensaje | `feat(cerebras): add qwen-3.8-27b to CerebrasChatModels (#7680)` |
| versión del paquete | `1.8.5` (`A/version.py`) |
| licencia observada | **Apache-2.0** (`LICENSE`, `NOTICE`). Además **`MODEL_LICENSE`**: "LiveKit Model License Agreement": los modelos propietarios de LiveKit (turn-detector) "can only be used together with the LiveKit Agents framework" `[DOCUMENTACIÓN]` |
| submódulos | ninguno (`.gitmodules` no existe) |
| dependencias nativas externas al repo | `livekit==1.1.20` (rtc, FFI Rust), `livekit-local-inference>=0.2.7`, `livekit-protocol` (`livekit-agents/pyproject.toml:30-33`) `[CÓDIGO]`. Su código **no está en este repo** → lo que ocurra dentro de `rtc.AudioStream`, `rtc.AudioSource`, `livekit.local_inference` es `[PENDIENTE]` |
| mantenimiento visible | commit del mismo día de la auditoría; ~90 plugins en `livekit-plugins/`; suite `tests/` con pruebas específicas de IPC (`tests/test_ipc.py`), drenaje (`tests/test_drain_timeout.py`), memoria supervisada (`tests/test_supervised_proc_memory.py`), interrupciones (`tests/test_interruption*`, `test_false_interruption_resume.py`) |
| benchmarks de capacidad | **ninguno**. No hay medición reproducible de sesiones por proceso/host, CPU ni latencia → toda capacidad aquí es `[INFERENCIA]` o `[PENDIENTE]` |

---

## 2. Dictamen ejecutivo

1. **Unidad de aislamiento = 1 job (= 1 sala/llamada) por proceso hijo** en el modo por defecto
   (`JobExecutorType.PROCESS`, salvo Windows). El hijo rechaza un segundo job
   (`A/ipc/job_proc_lazy_main.py:_JobProc.entrypoint:241-246`) y **termina cuando acaba el job**
   (`_start_job:320-324`); el proceso no se reutiliza `[CÓDIGO]`.
2. **El audio nunca cruza el IPC ni el proceso coordinador.** El proceso principal (`AgentServer`)
   sólo pasa al hijo `url`+`token` del job (`A/ipc/proto.py:StartJobRequest:86-128`); el hijo crea
   su propio `rtc.Room()` (`job_proc_lazy_main.py:_start_job:287`) y recibe la media directamente
   del SFU (livekit-server). El "dispatcher de media" es el SFU, no Python `[CÓDIGO]`+`[INFERENCIA]`.
3. **Dentro del hijo, el camino por trama sigue íntegro en Python, en UN event loop por llamada**:
   `AudioStream` → `_data_ch` → `_forward_audio_task` → `push_audio` → canales de STT/VAD/
   interrupción → `VADStream._main_task` → **un `run_in_executor` por ventana Silero**
   (`P/livekit-plugins-silero/.../vad.py:VADStream._main_task:422`). Es el mismo patrón
   `submit`+`resume` que Golden paga hoy — la diferencia es que LiveKit lo paga **para una sola
   llamada por loop** `[CÓDIGO]`. El propio repo lo declara: "A voice pipeline runs VAD, playout,
   and every provider stream on one event loop" (`A/telemetry/loop_monitor.py:3-4`) `[DOCUMENTACIÓN]`.
4. Por tanto **LiveKit Agents no eleva K_loop: lo fija en 1 y replica** (K_worker = 1 llamada).
   Elimina el coordinador único del camino por trama *por no compartir el loop*, no por
   paralelizar un loop compartido. Para Golden (100 llamadas/tarea) esto equivale a 100 intérpretes
   Python por tarea Modal `[INFERENCIA]`.
5. **Lo reutilizable para Golden es el plano de supervisión y admisión**, no el de media:
   `SupervisedProc` (socketpair + framing de longitud + handshake con timeout + ping/pong que mide
   lag del loop del hijo + kill escalonado + monitor de memoria PSS), la **reserva de capacidad
   antes de aceptar** (`_reserved_slots` + carga efectiva), el **pool precalentado** con
   `forkserver` + `gc.freeze()` para compartir modelos COW, el `loop_monitor` y la línea de
   tiempo de despacho. También el **contrato de salida de audio** (segmentos capturados/terminados,
   `clear_buffer`, `PlaybackFinishedEvent{playback_position, synchronized_transcript}`, contador
   `_interruption_generation`) y el `SpeechHandle` (autorización por generación, `interrupt()`,
   timeout de interrupción).
6. **El "inference executor" compartido existe** (`A/ipc/inference_proc_executor.py`,
   `A/ipc/inference_proc_lazy_main.py`): un proceso por worker con los modelos cargados una vez,
   al que los jobs piden inferencia por IPC. Pero (a) sólo lo usa el turn-detector **de texto**
   (EOU por `chat_ctx` JSON, `P/livekit-plugins-turn-detector/.../base.py:predict_end_of_turn:264-297`),
   no Silero; (b) **cada petición atraviesa el loop del proceso principal** (doble salto
   hijo→principal→inferencia, `job_proc_executor.py:_do_inference_task:140-161`); (c) el proceso de
   inferencia **procesa las peticiones en serie** (`inference_proc_lazy_main.py:entrypoint:85-87`
   hace `await` dentro del `async for`), pese a tener un `ThreadPoolExecutor(cpu_count)`; (d) no
   propaga cancelaciones. Válido como patrón para SmartTurn (0,07 inv/s-sesión), **inviable** para
   Silero (31,25/s por llamada) `[CÓDIGO]`+`[INFERENCIA]`.
7. Valor global para Golden: **ADAPTAR** (supervisión, admisión, prewarm/COW, contrato de
   playback e interrupción). **DESCARTAR** el modelo "una llamada por proceso" como vía para elevar K
   y el inference executor tal cual.

---

## 3. Diagrama del flujo relevante

```
                      livekit-server (SFU + dispatcher de jobs)          [fuera de este repo]
                        ▲  WS /agent (protobuf)            ▲ WebRTC media (Opus/RTP)
   availability/assign  │  update_worker cada 2,5 s        │ directo, por job
                        │                                  │
┌───────────────────────┴───────────────┐                  │
│ PROCESO PRINCIPAL  AgentServer         │                  │
│ 1 event loop (control)                 │                  │
│  _recv_task ── availability ─▶ _answer_availability       │
│               (refresh load, _reserved_slots += 1,        │
│                request_fnc → accept → espera assignment   │
│                ≤7,5 s → ProcPool.launch_job)              │
│  _load_task 0,5 s (load_fnc=CPU% media 2,5 s)             │
│  ProcPool: cola de procesos calientes (num_idle)          │
│  SupervisedProc × N: ping 2,5 s · pong timeout 60 s ·     │
│                     memoria PSS 5 s · kill escalonado     │
│  InferenceProcExecutor (relay de InferenceRequest) ───────┼──────────┐
└──────┬──────────────────────┬──────────────────────────┘  │          │
       │ socketpair (len‖msg)  │ socketpair log             │          │ socketpair
       │ Init/Ping/Pong/       │                            │          ▼
       │ StartJob/Shutdown/    │                 ┌──────────┴───────────────────────┐
       │ InferenceReq/Resp     │                 │ PROCESO DE INFERENCIA (1/worker) │
       ▼                       ▼                 │ runners EOU (ONNX, intra_op≤4)   │
┌────────────────────────────────────────────┐   │ atiende peticiones EN SERIE       │
│ PROCESO HIJO = 1 JOB = 1 LLAMADA            │   └──────────────────────────────────┘
│ 1 event loop (_ProcClient.run)              │
│  rtc.Room() propio ◀═══ media WebRTC ═══════╪══ (el principal no ve audio)
│  AudioStream(24 kHz, 50 ms) ─▶ _data_ch ──▶ AgentSession._forward_audio_task
│     └▶ AgentActivity.push_audio ─▶ STT audio_ch · VAD _vad_ch · interruption_ch · EOT
│           VADStream._main_task: resample QUICK → ventana 512 → run_in_executor(Silero)
│     eventos VAD ─▶ on_vad_inference_done ─▶ _interrupt_by_audio_activity ─▶ SpeechHandle.interrupt
│  _scheduling_task (heap de SpeechHandle) ─▶ _authorize_generation
│  _tts_task / _pipeline_reply_task ─▶ AudioOutput.capture_frame ─▶ _audio_buf ─▶
│     _forward_audio ─▶ rtc.AudioSource(queue 200 ms) ─▶ WebRTC
│  interrupción: cancel tasks → clear_buffer → wait_for_playout → transcript sincronizado
└─────────────────────────────────────────────┘
```

---

## 4. Mapa de procesos, threads, loops y colas

### 4.1 Procesos y loops

| entidad | qué es | loop/hilos | evidencia |
|---|---|---|---|
| `AgentServer` (proceso principal) | registro con el servidor, admisión, pool, supervisión, relay de inferencia, HTTP health/Prometheus | 1 event loop; hilo `worker_cpu_load_monitor` (CPU%); un hilo `proc_join_thread` por hijo; `run_in_executor` para `load_fnc` y muestreo PSS | `A/worker.py:_DefaultLoadCalc:84-114`, `run:595-855`; `A/ipc/supervised_proc.py:_start:240-249`, `_memory_monitor_task:609-611` `[CÓDIGO]` |
| hijo `job_proc` | un job | `asyncio.new_event_loop()` propio (`A/ipc/proc_client.py:run:62-67`) + `loop_monitor` (heartbeat 20 ms + hilo watchdog) + executor por defecto (Silero) o executor de 1 hilo por stream (VAD nativo) + hilos del runtime FFI de rtc | `proc_client.py:58-94`; `A/telemetry/loop_monitor.py:71-83`; `P/.../silero/vad.py:422`; `A/inference/vad.py:_VADStream.__init__:150-154`; `A/ipc/_preload.py:75-76` ("The runtime itself (FfiClient.instance) starts threads, so it stays per process") `[CÓDIGO]` |
| modo `THREAD` | un job por **hilo** del proceso principal | cada hilo crea su propio loop vía el mismo `_ProcClient.run` | `A/ipc/job_thread_executor.py:_start:145-150`; `job_proc_lazy_main.py:thread_main:599-620` `[CÓDIGO]` |
| `agents_inference_process` | modelos compartidos (EOU) | 1 loop + `ThreadPoolExecutor(max_workers=ceil(cpu_count))` | `A/ipc/inference_proc_lazy_main.py:_InferenceProc.__init__:55-57` `[CÓDIGO]` |
| forkserver (Linux) | plantilla COW de los hijos | — | `A/worker.py:748-757`; `A/ipc/_preload_freeze.py:10-11` (`gc.collect(); gc.freeze()`) `[CÓDIGO]` |

Por defecto: `PROCESS` en Linux/macOS, `THREAD` en Windows (`A/worker.py:127-131`); la consola y
Jupyter fuerzan `THREAD` (`A/cli/cli.py:228`, `A/jupyter.py:47`) `[CÓDIGO]`.

### 4.2 Colas

| cola | dueño | acotada | evidencia |
|---|---|---|---|
| `_msg_chan` (mensajes al servidor) | principal | **sí, 128** (`send` bloquea) | `A/worker.py:600`, `_queue_msg:1067-1076` |
| `_warmed_proc_queue` (procesos calientes) | `ProcPool` | no (`asyncio.Queue()`), pero su llenado lo limita `_main_task` al objetivo de idle | `A/ipc/proc_pool.py:68`, `_main_task:283-298` |
| `ipc_ch` (mensajes IPC recibidos, por proceso) | `SupervisedProc` / `_ProcClient` | **no** (`aio.Chan()` → `maxsize=0`) | `supervised_proc.py:_supervise_task:436`; `proc_client.py:105`; `A/utils/aio/channel.py:Chan.__init__:50-56` |
| socket IPC | kernel | buffer del socketpair; `send_bytes` hace `drain()` → backpressure de escritura | `A/utils/aio/duplex_unix.py:_AsyncDuplex.send_bytes:45-52` |
| `_data_ch` (tramas de entrada del participante) | hijo | **no**; el productor hace `await send` (sin límite, nunca bloquea) | `A/voice/room_io/_input.py:45`, `_forward_task:180-187` |
| `audio_ch` STT, `_vad_ch`, `_interruption_ch` | `AudioRecognition` | **no** (`send_nowait`) | `A/voice/audio_recognition.py:176,907,947`, `_push_audio:758-774` |
| `VADStream._input_ch` / `_event_ch` | `VADStream` | **no** | `A/vad.py:105-106`, `push_frame:149-153` |
| `_speech_q` | `AgentActivity` | no; heap por prioridad + timestamp | `A/voice/agent_activity.py:348`, `_schedule_speech:1996-2007` |
| `_audio_buf` (salida) | `_ParticipantAudioOutput` | **no**; se vacía al interrumpir | `A/voice/room_io/_output.py:59`, `_wait_for_playout:255-256` |
| `rtc.AudioSource` | nativo | **sí, `queue_size_ms=200`**; `clear_queue()` | `_output.py:54,263,293` (implementación `[PENDIENTE]`) |
| `_active_requests` (inferencia) | `_InfClient` / `InferenceProcExecutor` | no; dict por `request_id` | `job_proc_lazy_main.py:145`; `inference_proc_executor.py:48` |

---

## 5. Ownership por sesión y recursos compartidos

- **Dueño de la llamada**: el proceso hijo (`_JobProc`) posee `rtc.Room`, `JobContext`,
  `AgentSession`, `AgentActivity`, `AudioRecognition`, streams VAD/STT/TTS, `AudioOutput` y el
  historial (`_chat_ctx`) — todo en un loop, sin locks entre sesiones porque no hay otra sesión
  `[CÓDIGO]` (`job_proc_lazy_main.py:_start_job:281-324`).
- **Estado en el principal por job**: `RunningJobInfo` dentro de su `JobExecutor`
  (`job_proc_executor.py:launch_job:163-183`), `JobStatus`, y la lista `ProcPool._executors`.
  `active_jobs` se deriva de esa lista (`A/worker.py:active_jobs:921-922`) `[CÓDIGO]`.
- **Compartido por proceso** (no por sesión): `JobProcess.userdata` (resultado de `prewarm_fnc`,
  típicamente `silero.VAD.load()`, `P/.../silero/vad.py:load:88-93` `[DOCUMENTACIÓN]`). En
  `PROCESS` hay 1 sesión por proceso ⇒ cada llamada tiene su sesión ORT. `VAD.stream()` comparte la
  `InferenceSession` entre streams del mismo objeto VAD y crea estado RNN/contexto por stream
  (`OnnxModel.__init__:68-73`) `[CÓDIGO]` — el mismo diseño "modelo compartido, estado por sesión"
  que Golden.
- **Compartido por worker**: el proceso de inferencia (modelos EOU de texto) y, con `forkserver`,
  las páginas COW de los módulos precargados (`_preload.py:1-21`): `av`, singletons de
  `livekit.local_inference` (VAD y, condicionado, EOT ~244 MB), la librería nativa rtc, `openai`,
  `httpx` `[CÓDIGO]`+`[DOCUMENTACIÓN]`. El comentario de `_local_inference_models:67-69` precisa
  que un hijo *spawn* **no** comparte los pesos EOT.

---

## 6. Contrato de control/IPC

**Transporte** `[CÓDIGO]`: `socket.socketpair()` por proceso (más un segundo para logs)
(`supervised_proc.py:_start:197-198`). Framing: 4 bytes big-endian de longitud + cuerpo
(`duplex_unix.py:recv_bytes:33-43`, `send_bytes:45-52`). Cuerpo: `int32 MSG_ID` + campos
serializados a mano (`channel.py:_read_message:24-31`, `_write_message:34-41`,
`write_bytes/write_string/...:62-120`). Sin versión de protocolo, sin `seq`, sin ACK genérico.

**Mensajes** (`A/ipc/proto.py:13-252`):

| id | mensaje | dirección | uso |
|---|---|---|---|
| 0/1 | `InitializeRequest{ping_interval, ping_timeout, high_ping_threshold, http_proxy}` / `InitializeResponse{error}` | P→H / H→P | handshake; el hijo corre `prewarm` (`initialize_process_fnc`) antes de responder |
| 2/3 | `PingRequest{timestamp}` / `PongResponse{last_timestamp, timestamp}` | P→H / H→P | vida + **lag del loop del hijo** |
| 4 | `StartJobRequest{job (protobuf), accept args, url, token, worker_id, fake_job, received/accepted/assigned/launched_at}` | P→H | asignación (control puro, sin audio) |
| 5/10/11/6 | `ShutdownRequest{reason}` → `ShutdownRequestAck` → `ShuttingDown` → `Exiting{reason}` | P→H, H→P | cierre ordenado |
| 7/8 | `InferenceRequest{method, request_id, data}` / `InferenceResponse{request_id, data?, error}` | H→P→Inf y vuelta | inferencia compartida |
| 9 | `DumpStackTraceRequest` | P→H | diagnóstico (Windows sin SIGUSR1) |

**Correlación**: la identidad de sesión es **implícita en el canal** (un socketpair = un job) — por
eso ningún mensaje lleva `job_id` salvo `StartJobRequest`. La única correlación explícita es
`request_id` de inferencia, resuelta con un dict de futures
(`job_proc_lazy_main.py:_InfClient:142-181`; `inference_proc_executor.py:_main_task:67-80`)
`[CÓDIGO]`. Respuestas con `request_id` desconocido se registran y descartan
(`inference_proc_executor.py:72-77`). Orden: el de un stream UNIX (FIFO, fiable) `[INFERENCIA]`.

**Contrato con el servidor** (`A/worker.py:_connection_task:1079-1172`, `_run_ws:1174-1250`): WS
`/agent` con JWT, `register` → `RegisterWorkerResponse`; mensajes `availability`, `assignment`,
`termination`; el worker envía `availability{available, participant_*}`,
`update_worker{status WS_AVAILABLE|WS_FULL, load, job_count}` cada `UPDATE_STATUS_INTERVAL=2.5 s`,
`update_job{JS_RUNNING|JS_SUCCESS|JS_FAILED}` y, tras reconectar, `migrate_job{job_ids}`
(`_report_active_jobs:1548-1561`) `[CÓDIGO]`.

---

## 7. Ruta de audio y frecuencia de cruces

**Entrada (por llamada, dentro del hijo)** `[CÓDIGO]` salvo donde se indica:

1. `rtc.AudioStream.from_track(track, sample_rate, num_channels, frame_size_ms, noise_cancellation)`
   (`room_io/_input.py:_create_stream:374-390`). Defaults: **24 kHz, tramas de 50 ms**
   (`room_io/types.py:59-61, 249-251`). Decodificación Opus, resampleo y framing ocurren en el lado
   nativo `[INFERENCIA por los parámetros]` / `[PENDIENTE: python-sdks no está en el repo]`.
2. Cada trama: `_on_first_frame`, AGC `apm.process_stream(frame)` (llamada nativa síncrona en el
   loop, `_input.py:_process_frame:368-371`), `await _data_ch.send(frame)` (`_forward_task:180-187`).
3. `AgentSession._forward_audio_task` → `AgentActivity.push_audio`
   (`agent_session.py:2035-2042`; `agent_activity.py:1654-1686`): decide si sustituir por silencio
   (AEC warm-up o habla no interrumpible, `1658-1680`).
4. `AudioRecognition._push_audio` (`audio_recognition.py:748-774`): `send_nowait` a STT, VAD, AMD,
   detector de interrupción y turn-detector — **hasta 5 destinos por trama**.
5. `_vad_task._forward` → `VADStream.push_frame` → `_main_task`: resampleo `rtc.AudioResampler`
   QUICK 24k→16k (`silero/vad.py:378-397`), acumulación hasta 512 muestras, conversión a f32 y
   **`await loop.run_in_executor(None, model, data)` por ventana** (`vad.py:422`). El VAD nativo
   nuevo hace lo mismo con un executor de 1 hilo por stream (`A/inference/vad.py:331-333`).

**Frecuencias por llamada** `[INFERENCIA aritmética sobre defaults del código]`: 20 tramas/s de
entrada (50 ms); 31,25 ventanas Silero/s a 16 kHz (512 muestras = 32 ms) ⇒ 31,25 `submit`+`resume`
por segundo al executor — **idéntico a Golden** (`chunk_samples=256 @ 8 kHz` = 31,25/s).

**Cruces del runtime de control por paquete**:

| frontera | cruces/paquete | evidencia |
|---|---|---|
| IPC principal⇄hijo | **0** — sólo control (tabla §6) | `proto.py:IPC_MESSAGES:239-252` `[CÓDIGO]` |
| loop del proceso principal | **0** | el principal nunca abre `rtc.Room` para jobs reales; sólo `StartJobRequest` con token `[CÓDIGO]` |
| loop del hijo (control de *esa* llamada) | ≥ 1 por trama (+ ~1,6 resumes de executor por trama) | pasos 2-5 `[CÓDIGO]` |
| proceso de inferencia | 0 para audio; 1 por predicción EOU (texto) | `turn_detector/base.py:285-289` `[CÓDIGO]` |

**Salida**: TTS → `AudioOutput.capture_frame` → `AudioByteStream` re-trocea a 50 ms
(`samples_per_channel = sample_rate // 20`, `_output.py:60-62`) → `_audio_buf` → `_forward_audio` →
`await rtc.AudioSource.capture_frame` (cola nativa 200 ms) (`_output.py:120-132, 279-318`)
`[CÓDIGO]`.

---

## 8. Secuencia de barge-in

`[CÓDIGO]` salvo indicación; todo ocurre **en el loop del hijo de esa llamada**.

1. **Detección**: `VADStream` emite `INFERENCE_DONE` → `AudioRecognition._on_vad_event` →
   `AgentActivity.on_vad_inference_done` (`agent_activity.py:2450-2474`): si
   `speech_duration >= interruption["min_duration"]` (default **0,5 s**, `voice/turn.py:190-198`)
   llama `_interrupt_by_audio_activity`.
2. **Filtros** (`_interrupt_by_audio_activity:2313-2378`): interrupción habilitada; AEC warm-up;
   `min_words` sobre la transcripción actual (default 0); `allow_interruptions` del
   `SpeechHandle` actual.
3. **Dos modos**:
   - *pausa* (si `false_interruption_timeout` y la salida `can_pause`): `audio_output.pause()`,
     estado `listening`, se arma el temporizador de falsa interrupción (default 2,0 s) para
     **reanudar** si no llega transcripción (`2360-2371`; `_start_false_interruption_timer:4900-4962`).
   - *interrupción*: `self._current_speech.interrupt(source="audio_activity")` (`2376`).
4. **`SpeechHandle.interrupt`** (`speech_handle.py:201-227`) → `_cancel` (`284-304`): resuelve
   `_interrupt_fut` y arma `INTERRUPTION_TIMEOUT = 5.0 s` (`speech_handle.py:16`) que cancela a la
   fuerza las tareas si la generación no termina.
5. **Tarea de habla** (`_tts_task_impl:3136-3368`): `wait_if_not_interrupted` (`3274`) despierta;
   `cancel_and_wait(*all_tasks)` (TTS, forwarding de audio y texto) y
   **`audio_output.clear_buffer()` + `await wait_for_playout()`** (`3290-3295`).
6. **Purga** (`_ParticipantAudioOutput`): `clear_buffer` limpia el `AudioByteStream`, reporta el
   tramo reproducido y activa `_interrupted_event` (`_output.py:148-162`); `_wait_for_playout`
   vacía `_audio_buf`, calcula `pushed_duration - queued_duration`, llama
   **`_audio_source.clear_queue()`**, incrementa `_interruption_generation` y emite
   `playback_finished(playback_position, interrupted=True)` (`226-277`). `_forward_audio` descarta
   tramas posteriores a la interrupción y tramas pausadas de una generación anterior
   (`295-305`).
7. **Historial truncado a lo reproducido**: se usa `playback_ev.synchronized_transcript` si la
   propia trama llegó a sonar (`3300-3326`) y se añade el mensaje con `interrupted=True`
   (`3348-3357`; en `_pipeline_reply_task_impl` igual, `3862-3900`). Es el **mismo acoplamiento
   Media→Conversación** que Golden (`_cancel_active_tts` escribe el historial truncado) — LiveKit no
   lo separa porque todo vive en un proceso.
8. **Resultados tardíos**: no hay sello de generación en las tramas; el descarte es por
   **cancelación de tareas** + `speech.done()` al desencolar (`_scheduling_task:2016-2031`) +
   comprobaciones `if speech_handle.interrupted` tras cada espera (`3166-3170`, `3652-3680`,
   `3782`, `3821`, `4090`) + `_interruption_generation` en la salida. Para inferencia, `request_id`
   con *supersede* (`A/inference/eot/base.py:run_inference:180-188`,
   `_resolve_prediction:282-296` "Stale response is ignored").
9. **Prevención antes de hablar**: la TTS de un habla interrumpible espera, además de la
   autorización, `_user_silence_event` (`3156-3162`) — no empieza a hablar sobre el usuario.

---

## 9. Backpressure, fallos, reconexión y drenaje

**Backpressure** `[CÓDIGO]`: en el camino de audio **no hay colas acotadas** (§4.2); la única
cota real es la cola nativa de 200 ms de `AudioSource` (salida) y el `drain()` del socket IPC. El
aislamiento entre llamadas **no lo da el backpressure sino el proceso**: una llamada atascada sólo
atasca su loop `[INFERENCIA]`. En modo `THREAD` esa garantía desaparece en CPU (GIL) y en fallos.

**Muerte del hijo (Media)** `[CÓDIGO]`: `proc_join_thread` → `_join_fut` → `_supervise_task`
registra `exitcode`, cancela ping/lectura (`supervised_proc.py:459-474`); `ProcJobExecutor`
fija `JobStatus.FAILED` si `exitcode≠0` (`job_proc_executor.py:_supervise_task:131-138`);
`ProcPool._monitor_process_task` emite `process_closed` (`proc_pool.py:275-280`) → el worker envía
`update_job{JS_FAILED}` (`worker.py:_update_job_status:1531-1546`). **No hay reintento local del
job**; la reasignación queda en el servidor `[PENDIENTE: livekit-server]`.

**Hijo colgado**: ping cada 2,5 s; aviso si el pong tarda > `high_ping_threshold` 0,5 s
("process is unresponsive"); **kill** si no hay pong en `ping_timeout` 60 s
(`supervised_proc.py:_read_ipc_task:486-495`, `_ping_pong_task:513-538`;
`proc_pool.py:233-236`). El pong lo responde el loop del hijo (`proc_client.py:118-122`) ⇒ el
retardo **mide el lag de ese loop** `[CÓDIGO]`. Kill escalonado: `SIGUSR1` (volcado de pilas,
0,5 s) → `kill()` (`_send_kill_signal:393-422`). Memoria: PSS/USS/RSS cada 5 s; kill si supera
`job_memory_limit_mb` (default 0 = desactivado), aviso con rate-limit sobre
`job_memory_warn_mb` (default 1000) (`_memory_monitor_task:600-664`; `worker.py:199-204`).

**Muerte del principal (Control)**: el socket se cierra → el `_read_ipc_task` del hijo recibe
`DuplexClosed` → `_done_cb` → salida y cancelación del job; además autochequeo
`ping_timeout+10 s` sin mensajes (`proc_client.py:103-157`). Los hijos ignoran SIGINT/SIGTERM
(`job_proc_lazy_main.py:5-15`; `supervised_proc.py:_mask_ctrl_c:51-79`) ⇒ el principal decide el
cierre `[CÓDIGO]`.

**Caída del canal worker⇄servidor**: reintentos con `min(retry*2, 10) s` hasta `max_retry=16`
(`worker.py:1152-1169`, `226`); los jobs **siguen vivos** (tienen su propia conexión de sala) y al
re-registrar se informan con `MigrateJobRequest` (`1148-1149`, `1548-1561`) `[CÓDIGO]`. Dominio de
fallo de media separado del de control.

**Proceso de inferencia**: un solo proceso por worker, creado en `run()` (`worker.py:602-618`);
**no hay reinicio**: el health check devuelve 503 si no está vivo (`worker.py:652-654`). Las
peticiones pendientes del lado job quedan colgadas hasta el `timeout=3` del plugin
(`turn_detector/base.py:287-289`) `[CÓDIGO]`; el reinicio queda al orquestador externo
`[INFERENCIA]`.

**Drenaje** `[CÓDIGO]`: SIGTERM/SIGINT → `server.drain()` y luego `aclose()`
(`cli/cli.py:436-442`). `drain()` (`worker.py:928-956`): `_draining=True` → `update_worker{WS_FULL}`
→ espera las tareas de disponibilidad en vuelo (jobs ya aceptados se lanzan) → `join()` de cada
hijo con job; `drain_timeout` default **3600 s** (`worker.py:65,206`); `TimeoutError` capturado
para que `aclose` siempre corra (`tests/test_drain_timeout.py:1-12`). Con drenaje activo
`_is_available()` devuelve False (`1335-1337`). Cierre del job (`_shutdown_job:422-506`): espera al
entrypoint 15 s → `AgentSession.aclose()` con tope 60 s → `on_session_end` (tope
`session_end_timeout` 300 s) → `ShuttingDown`/`Exiting` → `room.disconnect()` → callbacks.
`aclose` del supervisor: `ShutdownRequest` → espera ack `close_timeout` (default 10 s,
`shutdown_process_timeout`) → si no, kill (`supervised_proc.py:aclose:309-355`).

---

## 10. Escalado y uso real de núcleos

- **Paralelismo real entre núcleos: por procesos** (uno por llamada) `[CÓDIGO]`. En modo
  `THREAD`, N loops en N hilos de un proceso: bajo GIL no hay paralelismo de CPU Python; el repo no
  documenta ni prueba free-threading `[INFERENCIA]`/`[PENDIENTE]`.
- **Inicialización acotada**: `MAX_CONCURRENT_INITIALIZATIONS = min(cpu, 4)`
  (`proc_pool.py:26,67,247`); procesos calientes `num_idle_processes` default `min(cpu, 4)` en prod
  (`worker.py:208-210`), ajustado dinámicamente a `min(ceil((threshold-load)/job_load), default)`
  (`worker.py:_load_task:821-832`).
- **Admisión** (`worker.py:_answer_availability:1350-1467`): refresca `load_fnc` (no se fía del
  muestreo periódico, `_refresh_worker_load:1312-1320`), compara la **carga efectiva**
  `load + reserved_slots × (load/active_jobs)` con `load_threshold` (default prod **0,7**,
  `worker.py:149`) y **reserva el hueco antes** de llamar a `request_fnc`
  (`_reserved_slots += 1`, `1366-1368`), liberándolo al terminar la tarea de petición, que incluye
  la espera de assignment (≤ `ASSIGNMENT_TIMEOUT=7.5 s`) y el `launch_job` (`1398-1426,1461-1463`).
- **`load_fnc` por defecto = CPU%** media de 5 muestras de 0,5 s (`_DefaultLoadCalc:84-114`); en
  LiveKit Cloud se fuerzan el `load_fnc` y el umbral por defecto (`worker.py:577-590`).

**Capacidad (distinción obligatoria)**:

| nivel | LiveKit Agents | evidencia |
|---|---|---|
| **K_loop** | **1 llamada por loop**, por diseño | `job_proc_lazy_main.py:241-246, 320-324` `[CÓDIGO]` |
| **K_worker** (proceso Media) | **1 llamada** (`PROCESS`); en `THREAD`, N loops por proceso sin aislamiento de CPU/fallo | `[CÓDIGO]` |
| **K_task** (host `AgentServer`) | lo que admita `load_threshold` sobre CPU% (y memoria si se fija límite). **Sin medición en el repo** | `[PENDIENTE]` |
| **K_flota** | despacho del servidor entre workers | `[PENDIENTE: livekit-server]` |

Para Golden: aplicar este modelo da K_task = N procesos × 1, es decir **replicación**; no mejora
la eficiencia de un loop. Con la regresión medida de Golden (0,022 núcleos/llamada de DSP) la CPU
útil no es el problema, pero cada proceso añade intérprete, runtime FFI con hilos propios, sesión
ORT, `loop_monitor` con dos despertares cada 20 ms (el repo lo reconoce: "two wake-ups per tick
(heartbeat and watchdog) in every job process add up", `loop_monitor.py:80-81`) — coste fijo por
llamada **no medido** `[PENDIENTE]`.

---

## 11. Patrones y antipatrones

**Implementados (útiles)** `[CÓDIGO]`

- *Supervisor de procesos* con handshake, heartbeat que mide lag, kill escalonado y monitor de
  memoria — `SupervisedProc` (Template Method: `_create_process`/`_main_task` abstractos,
  `supervised_proc.py:141-145`), reutilizado por job e inferencia.
- *Pool de objetos precalentados* (`ProcPool`) con objetivo dinámico y carrera cola-vs-spawn
  (`_acquire_proc:114-163`, reintentos 3×).
- *Reserva antes de compromiso* (`_reserved_slots`) — evita sobre-aceptación cuando dos peticiones
  llegan antes de que el job aparezca en `active_jobs` (comentario `worker.py:1346-1347`).
- *Precarga COW* con `forkserver` + `gc.freeze()` (`_preload_freeze.py`).
- *Canal tipado con framing por longitud* y registro `MSG_ID → clase` (`proto.py:239-252`).
- *Handle de generación* (`SpeechHandle`): futures `scheduled/authorized/interrupted/done`,
  generaciones por paso (`_generations`, `_num_steps`), timeout de interrupción.
- *Contrato de playback por segmentos*: contador capturados/terminados, `wait_for_playout` que
  espera *todos* los segmentos previos (`io.py:261-275`), `PlaybackFinishedEvent` con posición y
  transcript sincronizado, `PlaybackProgressedEvent` "Reported once the audio can no longer be
  discarded, so it is never revised" (`io.py:128-139`).
- *Supersede por `request_id`* para inferencias asíncronas (EOT).
- *Monitor de bloqueo del loop* (`loop_monitor`) y *línea de tiempo de despacho*
  (`_record_dispatch_timeline:553-584`).

**Latentes** `[INFERENCIA]`

- Modo `THREAD` = "N loops por proceso" con el mismo protocolo IPC: es la forma más cercana a un
  MediaWorker multi-loop, pero sin kill posible (`job_thread_executor.py:aclose:208-212`) ni
  `ping_timeout` (sólo aviso, `_monitor_task:317-323`).
- `InferenceRunner` como registro de métodos por nombre: generalizable a cualquier modelo
  compartido.

**Antipatrones / no recomendados** `[CÓDIGO]`

- **Proceso de inferencia en serie**: `async for msg in cch: … await self._handle_inference_request(msg)`
  (`inference_proc_lazy_main.py:85-87`) ⇒ una petición a la vez para todo el worker (HOL); el
  `ThreadPoolExecutor(cpu_count)` queda infrautilizado.
- **Relay de inferencia por el loop del principal** (`job_proc_executor.py:_main_task:120-129`,
  `_do_inference_task:140-161`): el principal vuelve a ser coordinador de todas las peticiones de
  todos los jobs.
- **Sin cancelación aguas abajo**: el timeout del job no cancela el trabajo en el proceso de
  inferencia (no existe mensaje de cancelación en `proto.py`); el resultado tardío se calcula y se
  descarta.
- **Colas sin cota** en todo el camino de audio.
- **Carga = CPU%**: para un sistema cuyo cuello es coordinación y no CPU (Golden) es la señal
  equivocada.

---

## 12. Callers verificados de las piezas propuestas como referencia

| pieza | callers verificados |
|---|---|
| `SupervisedProc.start/initialize/aclose/kill` | `ProcPool._proc_spawn_task:247-254` (start, initialize), `_main_task:301` (aclose en cierre), `launch_job:182` (aclose si falla), `AgentServer.run:761-762` (inferencia), `AgentServer.aclose:1046-1047`, `AgentServer._handle_termination:1496` `[CÓDIGO]` |
| `ProcPool.launch_job` | `AgentServer._answer_availability._on_accept:1426`, `_reload_jobs:1276`, `simulate_job` (`worker.py:1014`) `[CÓDIGO]` |
| `ProcPool.set_target_idle_processes` | `AgentServer.run._load_task:830,832` `[CÓDIGO]` |
| `_reserved_slots` / `_get_effective_load` | `_answer_availability:1368,1461-1462`; `_is_available:1348`; `_update_worker_status:1508` `[CÓDIGO]` |
| `AgentServer.drain` | `cli/cli.py:_run_worker:439` `[CÓDIGO]`; `tests/test_drain_timeout.py` |
| `InferenceExecutor.do_inference` | `EOUModelBase.predict_end_of_turn` (`P/livekit-plugins-turn-detector/.../base.py:287-289`); en el principal `ProcJobExecutor._do_inference_task:152` y `ThreadJobExecutor._do_inference_task:252` `[CÓDIGO]` |
| `_InferenceRunner.register_runner` | `turn_detector/english.py:34`, `multilingual.py:118` `[CÓDIGO]` |
| `SpeechHandle.interrupt` | `AgentActivity.interrupt:1916,1925`, `_interrupt_by_audio_activity:2376`, `_schedule_speech:1984,1993`, `user_turn` `2786,2904`, `_cancel_speech_pause:4993` `[CÓDIGO]` |
| `SpeechHandle._authorize_generation` | `AgentActivity._scheduling_task:2032` `[CÓDIGO]` |
| `AudioOutput.clear_buffer` | `_tts_task_impl:3294`; `forward_generation` (comentario `3870`) `[CÓDIGO]` |
| `loop_monitor.start_monitoring` | `_ProcClient.run:67` (cada hijo); `cli/cli.py:318` (principal, `name="worker"`, sin spans) `[CÓDIGO]` |
| tests de contrato IPC | `tests/test_ipc.py`: `test_proc_pool:280`, `test_slow_initialization:363`, `test_proc_pool_launch_job_raises_when_all_spawns_fail:423`, `test_job_slow_shutdown:527`, `test_job_graceful_shutdown:596`, `test_thread_job_graceful_shutdown:671`, `test_inference_executor_is_alive_after_proc_closed:763` (no ejecutados) |

---

## 13. Aplicabilidad a GoldenBridge

**Diferencia estructural decisiva** `[INFERENCIA]`: LiveKit evita el relay de audio porque **cada
hijo marca hacia fuera** (al SFU) con un token; la red de media (livekit-server) es el
distribuidor. En Golden el audio **entra**: ai-engine es cliente WS del endpoint Modal. No existe
un SFU que haga de distribuidor, así que la pieza equivalente — "entregar cada WS al MediaWorker
dueño sin relay Python por trama" — **no está en este repo**: habría que construirla (p. ej.
aceptación en un proceso y traspaso del descriptor al worker, o un puerto por worker detrás de
`@modal.web_server`), lo cual queda `[PENDIENTE]` de verificar con Modal (BRIEF: multiproceso
interno exige `@modal.web_server(puerto)`).

| necesidad de Golden | LiveKit Agents | aplicación |
|---|---|---|
| supervisar W MediaWorkers dentro de una tarea | `SupervisedProc` + `proto` | **adaptar**: mismo esqueleto (socketpair, framing, Init con timeout, ping/pong = lag del loop del worker, kill escalonado, PSS) |
| admitir llamadas sin sobre-aceptar | `_reserved_slots` + carga efectiva + `WS_FULL` | **adaptar** a `max_inputs`/rechazo en el accept del WS; **cambiar la señal**: no CPU%, sino `cycle_p90`/lag del loop o sesiones activas frente a la rodilla medida (16-25) |
| no pagar carga de modelos por worker | prewarm + `forkserver` + `gc.freeze()` | **adaptar** si Golden pasa a W procesos; medir PSS real `[PENDIENTE]` |
| SmartTurn compartido | `InferenceProcExecutor` | **estudiar**: viable por frecuencia (0,07 inv/s-sesión ⇒ ~7 inv/s a 100 llamadas `[INFERENCIA]`), pero corrigiendo serialización, doble salto y cancelación |
| Silero por trama | en el loop de cada llamada, 1 `run_in_executor` por ventana | **no aporta**: es el patrón que Golden ya tiene y que satura su loop compartido |
| barge-in local y purga | `SpeechHandle` + `clear_buffer` + `_interruption_generation` + `PlaybackFinishedEvent` | **adaptar** al contrato `SpeechInterrupted{spoken_text}`/`CancelSpeech`/`PlaybackFinished`: `playback_position` + `synchronized_transcript` es exactamente lo que Media debe devolver a Conversación en vez de mutar el historial |
| descarte de obsoletos | cancelación en proceso + `request_id` supersede | **adaptar** `request_id` para SmartTurn; para la frontera Media↔Conversación LiveKit **no ofrece** `epoch/seq` porque su canal es 1:1 por sesión — Golden, con canal multiplexado, sí los necesita |
| elevar K por tarea | 1 llamada por proceso | **descartar** como vía: replica; 100 llamadas = 100 procesos |

**Mapeo al contrato de referencia `(session_id, session_epoch, turn_id, speech_id, seq)`**:
`job_id` ↔ `session_id` (implícito por canal); no hay `epoch` (el proceso no se reutiliza: la
"época" es el PID); `speech_id` ↔ `SpeechHandle.id`; `generation_id = f"{speech_id}_{num_steps}"`
(`speech_handle.py:110-118`, sólo telemetría) ↔ pasos LLM/tool dentro de un `speech_id`;
`_interruption_generation` ↔ contador local de purga en Media; `request_id` ↔ correlación de
inferencia. No hay `seq`/dedup porque el transporte es un stream local fiable `[CÓDIGO]`. Lección:
si Golden mantiene un canal por sesión, `seq` sobra; si multiplexa sesiones en un canal
MediaWorker↔Conversación, `session_id`+`epoch` son obligatorios `[INFERENCIA]`.

---

## 14. Riesgos de adopción o copia

- **Licencia**: Apache-2.0 permite copiar/adaptar con atribución y `NOTICE` `[DOCUMENTACIÓN]`.
  **Los modelos de LiveKit (turn-detector) están bajo `MODEL_LICENSE`, que prohíbe su uso fuera
  de LiveKit Agents** → no portables a Golden. Silero (modelo) es de terceros; la licencia de
  `livekit-local-inference` no está en el repo `[PENDIENTE]`.
- **Acoplamiento**: `proto.py` importa `livekit.protocol.agent` y `job.py`
  (`proto.py:7-9`); `SupervisedProc` importa telemetría propia. Importar `livekit.agents.ipc`
  arrastra `livekit` (rtc nativo), `psutil`, OpenTelemetry → copiar y recortar, no depender.
- **Modelo de proceso por llamada**: multiplica memoria y hilos nativos; dentro de una tarea Modal
  con 100 llamadas, 100 intérpretes `[INFERENCIA]`; coste no medido `[PENDIENTE]`.
- **`forkserver`** sólo en Linux y con hilos nativos que no deben existir antes del fork (el repo
  lo cuida: "Everything here is fork-safe … no threads", `_preload.py:4-5`). Golden 3.14t + ORT +
  uvloop requiere verificar que nada arranque hilos en la plantilla `[PENDIENTE]`.
- **Inferencia compartida**: copiarla tal cual introduce HOL entre todas las llamadas y un relay
  por el coordinador — reintroduce el cuello que se quiere quitar.
- **Señal de carga**: copiar `load_fnc` por CPU llevaría a admitir llamadas por encima de la
  rodilla de Golden (CPU sobrada a N=100, E1).
- **Adoptar LiveKit completo** exigiría sustituir el camino Asterisk→ai-engine→WS por LiveKit SIP +
  SFU + Agents (ver informes 04/05): alternativa de **alto impacto** (proveedor, túnel, ai-engine,
  despliegue Modal), no justificada por esta auditoría.

---

## 15. Lecciones concretas que pasan a la matriz comparativa

1. **"Sin coordinador por trama" puede lograrse por no-compartición (K_loop=1)**: LiveKit lo
   consigue con 1 job/proceso y media que llega directa del SFU a cada hijo
   (`job_proc_lazy_main.py:241-246, 287`). No es una técnica para subir K por loop.
2. **El heartbeat como medidor de rodilla**: el pong lo contesta el loop del worker
   (`proc_client.py:118-122`) y el supervisor compara con `high_ping_threshold`
   (`supervised_proc.py:486-492`) — Golden puede usar el mismo canal de supervisión para exportar
   lag del loop por MediaWorker y alimentar la admisión.
3. **Reserva antes de aceptar** (`worker.py:1366-1368, 1461-1462`) + estado `WS_FULL` al drenar:
   patrón directo para `max_inputs` y drenaje en Modal.
4. **El contrato de salida de audio debe devolver posición y transcript sincronizado**
   (`io.py:111-118`; `_output.py:226-277`) y la purga debe incluir un contador de generación para
   descartar tramas pausadas/tardías (`_output.py:73, 295-297`).
5. **Inferencia compartida sólo para eventos de baja frecuencia y nunca en serie ni vía el
   coordinador** (`inference_proc_lazy_main.py:85-87`; `job_proc_executor.py:140-161`).
6. **Carga por CPU no sirve cuando el cuello es coordinación** (`worker.py:84-114`).
7. **Precarga COW con `gc.freeze()`** reduce memoria duplicada si se pasa a W procesos
   (`_preload_freeze.py:10-11`).

---

## Respuestas explícitas a las 18 preguntas

1. **Unidad de ownership**: el **job** (una sala; en telefonía, una llamada), poseído por un
   proceso hijo (`PROCESS`) o un hilo con loop propio (`THREAD`) `[CÓDIGO]` §5.
2. **¿Quién recibe cada paquete?** El runtime rtc nativo del hijo (vía WebRTC desde el SFU) y luego
   el loop Python de ese hijo (`_ParticipantAudioInputStream._forward_task`) `[CÓDIGO]`; el
   principal nunca.
3. **Cruces del runtime de control**: **0** por el principal/IPC; ≥1 por trama por el loop del hijo,
   que es a la vez control y media de *esa* llamada (§7).
4. **¿Loop único compartido?** En el principal sí (todas las sesiones), pero sólo hace control de
   baja frecuencia: disponibilidad, assignment, estado cada 2,5 s, carga cada 0,5 s, ping de cada
   hijo cada 2,5 s y **relay de cada petición de inferencia** `[CÓDIGO]`. En el camino de media no
   hay loop compartido (salvo modo `THREAD` en CPU vía GIL).
5. **Núcleos**: procesos (uno por job) + proceso de inferencia con thread pool; el SO reparte
   `[CÓDIGO]`/`[INFERENCIA]`.
6. **Asignación y afinidad**: el servidor ofrece (`availability`) → el worker reserva y acepta →
   `assignment` con token → `ProcPool` saca un proceso caliente → `StartJobRequest`. La afinidad es
   total: el job vive y muere en ese proceso (`_start_job:320-324`) `[CÓDIGO]`.
7. **Compartido vs local**: compartido = proceso de inferencia, páginas COW de precarga, el
   principal; local = todo el estado de sesión, VAD/STT/TTS, historial (§5).
8. **Protocolo**: socketpair UNIX con framing longitud+`MSG_ID` (IPC); WS protobuf con el servidor;
   WebRTC para media (§6).
9. **Orden/correlación/ACK/dedup/tardíos**: orden por stream local; correlación implícita por canal
   y `request_id` en inferencia; ACK sólo en shutdown (`ShutdownRequestAck`); sin dedup; tardíos:
   cancelación de tareas, `speech.done()`, `interrupted`, `_interruption_generation`, supersede por
   `request_id` (§8.8).
10. **Backpressure sin afectar a otras llamadas**: por aislamiento de proceso, no por colas (las del
    audio no tienen cota). La cola de salida nativa de 200 ms es la única cota por llamada (§9).
11. **Barge-in y purga**: en el loop del hijo — `on_vad_inference_done` → `SpeechHandle.interrupt` →
    cancelación → `clear_buffer` → `clear_queue` del `AudioSource` (§8).
12. **Audio no retirable**: lo ya entregado por `AudioSource` a la pila WebRTC/red y el *jitter
    buffer* del receptor `[INFERENCIA]`; lo que está en `_audio_buf`, `AudioByteStream` y la cola de
    200 ms **sí** se purga `[CÓDIGO]`. En pausa, el código reconoce que puede sonar un resto de
    sílaba ("TODO(long): ignore frames from previous syllable", `_output.py:298`).
13. **Muerte de Media / Control / canal**: hijo muere → `JS_FAILED`, sin reintento local; principal
    muere → hijos salen por `DuplexClosed`/autochequeo; canal con el servidor cae → jobs siguen y se
    re-informan con `MigrateJobRequest`; proceso de inferencia muere → sin reinicio, health 503 (§9).
14. **Drenaje**: SIGTERM → `drain()` (`WS_FULL`, espera jobs, tope 3600 s) → `aclose()`; por job,
    secuencia con topes 15 s / 60 s / 300 s / `close_timeout` + kill (§9).
15. **Duplicación de modelos**: sí, por proceso (sesión ORT de Silero en cada hijo vía prewarm);
    mitigado por `forkserver`+COW para lo precargado y por el proceso de inferencia para EOU
    `[CÓDIGO]`; magnitud no medida `[PENDIENTE]`.
16. **Métricas para la rodilla**: Prometheus `lk_agents_active_job_count`,
    `lk_agents_child_process_count`, carga CPU, `lk_agents_proc_initialize_duration_seconds`
    (`A/telemetry/metrics.py:8-62`); aviso "process is unresponsive" con `delay` del pong; spans
    `event_loop_blocked` con pila muestreada (`loop_monitor.py:1-26`, umbrales 100/500 ms);
    `e2e_latency`, `playback_latency`, `tts_node_ttfb` por turno (`agent_activity.py:3331-3346`);
    latencias de despacho por etapa (`job_proc_lazy_main.py:553-584`). No hay métrica de K ni de
    ciclo por trama `[CÓDIGO]`.
17. **Reutilizable legal y técnicamente**: el código de `ipc/` (supervisor, framing, pool), la lógica
    de reserva/admisión y el contrato `AudioOutput`/`SpeechHandle` como **patrones a copiar y
    recortar** bajo Apache-2.0; **no** los modelos LiveKit (`MODEL_LICENSE`).
18. **¿Reemplazos?** Adoptar el framework completo exigiría LiveKit server + SIP en lugar de
    Asterisk→ai-engine→WS (alto impacto). Adaptar los patrones **no** exige reemplazar Asterisk,
    ai-engine, Modal ni proveedores; sí exige construir en Golden el traspaso del WS entrante al
    MediaWorker dueño, que LiveKit no necesita por tener SFU.

---

## Piezas reutilizables

```text
Segmento: Supervisor de proceso worker (SupervisedProc + _ProcClient + proto/channel/duplex_unix)
Responsabilidad principal: arrancar, inicializar, vigilar (vida, lag, memoria), cerrar y matar un proceso hijo
Pattern principal: Template Method (SupervisedProc) + Heartbeat + Handshake + Escalated Shutdown
Estado: implemented
Confianza: high
Evidencia: A/ipc/supervised_proc.py:_start:190-250, initialize:260-307, aclose:309-355, _send_kill_signal:393-422, _supervise_task:424-474, _read_ipc_task:476-511, _ping_pong_task:513-538, _memory_monitor_task:600-664; A/ipc/proc_client.py:_monitor_task:99-160; A/utils/aio/duplex_unix.py:33-52; A/ipc/proto.py:13-252
Callers verificados: ProcPool._proc_spawn_task:247-254; ProcPool._main_task:301; AgentServer.run:761-762; AgentServer.aclose:1044-1047; AgentServer._handle_termination:1496
Contrato de entrada/salida: socketpair; frame = u32 BE longitud ‖ i32 MSG_ID ‖ campos; Init→InitResp(error) con timeout; Ping(ts)/Pong(last_ts, ts) cada ping_interval; Shutdown→Ack→ShuttingDown→Exiting
Política de error: init timeout → dump + kill; sin pong en ping_timeout → kill; pong > high_ping_threshold → warning; PSS > memory_limit → kill; Shutdown sin ack en close_timeout → kill; DuplexClosed en lectura resuelve futures de cierre
Ownership: el supervisor (proceso principal) es dueño del ciclo de vida; el hijo ignora SIGINT/SIGTERM
Riesgo: el principal relaya también inferencia (debe quitarse); ping_timeout 60 s es lento para media; sin reinicio automático
Aplicación posible en Golden: supervisor de W MediaWorkers dentro de la tarea Modal; usar el RTT del pong como lag del loop de cada worker y exportarlo como señal de admisión; acortar timeouts; quitar todo relay de datos
```

```text
Segmento: Admisión con reserva previa (AgentServer._answer_availability / _get_effective_load / _update_worker_status) + ProcPool caliente
Responsabilidad principal: decidir si aceptar una sesión nueva sin sobre-aceptar y tener el ejecutor listo antes de que llegue
Pattern principal: Reservation / Admission Control + Object Pool (prewarm)
Estado: implemented
Confianza: high
Evidencia: A/worker.py:_refresh_worker_load:1312-1320, _get_effective_load:1322-1333, _is_available:1335-1348, _answer_availability:1350-1467, _update_worker_status:1498-1529, _load_task:801-832, drain:928-956; A/ipc/proc_pool.py:_acquire_proc:114-163, launch_job:165-195, _main_task:283-303
Callers verificados: _handle_availability:1293-1297 (desde _recv_task:1230-1231); _run_ws._load_task:1177-1182; cli/cli.py:439 (drain)
Contrato de entrada/salida: AvailabilityRequest → available true/false; reserved_slots++ antes de request_fnc, -- al terminar la tarea (incluye assignment ≤7,5 s y launch); estado WS_AVAILABLE/WS_FULL cada 2,5 s; WS_FULL inmediato al drenar
Política de error: assignment timeout → AssignmentTimeoutError; request_fnc sin respuesta → rechazo automático; launch falla → reintento con proceso nuevo (3×)
Ownership: proceso principal
Riesgo: la señal por defecto es CPU% (incorrecta para Golden); estimación lineal load/active_jobs
Aplicación posible en Golden: admisión en el accept del WS (o en max_inputs de Modal) con reserva previa y señal = sesiones activas vs rodilla medida / cycle_p90 del worker; drenaje = "FULL" inmediato + esperar sesiones
```

```text
Segmento: Inference executor compartido (InferenceProcExecutor + _InferenceProc + _InfClient + _InferenceRunner)
Responsabilidad principal: cargar modelos una vez por worker y servir inferencias a todos los jobs por IPC
Pattern principal: Shared Service Process + Request/Response con correlación por request_id + Registry de runners
Estado: implemented (sólo para EOU de texto); not recommended tal cual para Golden
Confianza: high
Evidencia: A/inference_runner.py:16-37; A/ipc/inference_proc_executor.py:_main_task:67-80, do_inference:82-105; A/ipc/inference_proc_lazy_main.py:_InferenceProc:53-57, entrypoint:84-94 (await en serie 86-87), _handle_inference_request:96-111; A/ipc/job_proc_executor.py:_main_task:120-129, _do_inference_task:140-161; A/ipc/job_proc_lazy_main.py:_InfClient:142-181; P/livekit-plugins-turn-detector/livekit/plugins/turn_detector/base.py:initialize:96-140, predict_end_of_turn:264-297
Callers verificados: EOUModelBase.predict_end_of_turn → JobContext.inference_executor (_InfClient) → ProcJobExecutor/ThreadJobExecutor._do_inference_task → InferenceProcExecutor.do_inference; registro: turn_detector/english.py:34, multilingual.py:118; creación: AgentServer.run:602-618
Contrato de entrada/salida: InferenceRequest{method, request_id, data: bytes} → InferenceResponse{request_id, data|None, error}; el job espera con timeout (3 s en EOU)
Política de error: excepción del runner → error en la respuesta → RuntimeError en el job; respuesta desconocida → warning y descarte; muerte del proceso → futures colgados hasta el timeout del job, sin reinicio (health 503)
Ownership: proceso de inferencia (modelos); proceso principal (relay y tabla de requests); job (futures propios)
Riesgo: (1) serialización: una inferencia a la vez para todo el worker; (2) doble salto por el loop del principal; (3) sin cancelación; (4) payload por IPC copiado 2 veces; (5) si se usara para VAD por trama recrearía el coordinador único (31,25 msg/s/llamada)
Aplicación posible en Golden: patrón para SmartTurn compartido entre MediaWorkers (baja frecuencia), corrigiendo: canal directo worker→servicio (sin relay), despacho concurrente o por lotes, cancelación/supersede por (session_id, turn_id, request_id), y reinicio supervisado. Silero debe quedarse dentro del worker dueño. Antes: medir si una sesión ORT por worker ya basta (no hay evidencia de que compartir sea necesario)
```

```text
Segmento: Contrato de salida de audio y purga (AudioOutput + _ParticipantAudioOutput)
Responsabilidad principal: reproducir audio por segmentos, purgar al interrumpir y devolver qué se reprodujo realmente
Pattern principal: Segment counter + Sink chain (Decorator/Proxy) + generation counter de purga
Estado: implemented
Confianza: high
Evidencia: A/voice/io.py:PlaybackFinishedEvent:111-118, PlaybackProgressedEvent:128-139, on_playback_finished:227-254, wait_for_playout:261-275, capture_frame:300-305, flush:307-310, clear_buffer:312-314; A/voice/room_io/_output.py:54,73,120-162,226-277,279-318
Callers verificados: AgentActivity._tts_task_impl:3274-3326; _pipeline_reply_task_impl (forward_generation, 3862-3871); _scheduling_task:2039-2040 (resume)
Contrato de entrada/salida: capture_frame abre segmento; flush lo cierra; clear_buffer lo interrumpe; cada segmento termina con playback_finished(playback_position, interrupted, synchronized_transcript)
Política de error: playback_finished de más → warning e ignorado; tramas posteriores a la interrupción o de generación anterior → descartadas
Ownership: el dueño de la sesión (un único loop)
Riesgo: depende de que el sink conozca la cola nativa (queued_duration); en Golden la cola real está en ai-engine (jitter 300 ms) sin flush para este proveedor (BRIEF)
Aplicación posible en Golden: modelo para PlaybackFinished/SpeechInterrupted{spoken_text, playback_position} emitidos por MediaWorker; el historial truncado lo escribe Conversación a partir del evento, no Media
```

```text
Segmento: SpeechHandle (autorización por generación, interrupción, timeout)
Responsabilidad principal: identidad y ciclo de vida de una locución; punto único de interrupción y de descarte
Pattern principal: Handle/Future de operación cancelable + cola por prioridad con autorización explícita
Estado: implemented
Confianza: high
Evidencia: A/voice/speech_handle.py:16, 41-85, 110-118, 201-227, 274-304, 322-365; A/voice/agent_activity.py:_schedule_speech:1978-2007, _scheduling_task:2010-2042, interrupt:1892-1935
Callers verificados: ver §12 (interrupt, _authorize_generation)
Contrato de entrada/salida: create(allow_interruptions) → scheduled → authorized (por paso/generación) → interrupted|done; generation_id = speech_id_numsteps
Política de error: interrupt() sobre habla no interrumpible → RuntimeError salvo force; si no termina en 5 s tras interrumpir → cancelación forzada de tareas
Ownership: AgentActivity (loop de la sesión)
Riesgo: el descarte se apoya en cancelación en proceso; no sirve a través de un límite de red sin sellos explícitos
Aplicación posible en Golden: speech_id + contador de pasos para Speak/SpeakChunk; timeout de interrupción en MediaWorker; "autorización" = Conversación sólo puede emitir Speak para el speech autorizado vigente
```

---

## Anexo — Respuesta a la pregunta específica

> *¿Qué puede reutilizar Golden para supervisión y asignación y qué no sirve para elevar K porque
> aísla una llamada por proceso?*

**Reutilizable (adaptar)**: el supervisor `SupervisedProc` y su protocolo (handshake, heartbeat que
mide lag del loop del hijo, kill escalonado, PSS); la admisión con reserva previa y estado
`FULL`/drenaje; el pool precalentado con `forkserver`+`gc.freeze()`; el `loop_monitor`; la línea
de tiempo de despacho; el contrato `AudioOutput`/`PlaybackFinishedEvent` y el `SpeechHandle`.

**No sirve para elevar K**: el modelo 1 job = 1 proceso = 1 loop (`job_proc_lazy_main.py:241-246`),
que da K_loop = K_worker = 1 y sólo escala replicando; el camino por trama sigue siendo
`submit`+`resume` por ventana Silero en Python (`silero/vad.py:422`); el inference executor tal
cual (serie + relay por el principal); `load_fnc` por CPU. Lo que LiveKit sí demuestra es que un
**coordinador de control puede quedar fuera del camino de audio** cuando cada dueño de sesión
recibe su media por su propio transporte — en Golden ese transporte (traspaso del WS entrante al
worker dueño) hay que construirlo.
