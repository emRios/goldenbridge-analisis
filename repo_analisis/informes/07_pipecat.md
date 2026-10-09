# 07 — Pipecat: contrato de frames, interrupciones y purga de audio pendiente

Auditoría comparativa contra GoldenBridge (`BRIEF_GOLDEN.md`, rama `api` @ `08932a6`). No se audita Golden; se compara con él.
Convención de evidencia: `[CÓDIGO]` = `archivo:símbolo:líneas` dentro de `repos/pipecat/` · `[DOCUMENTACIÓN]` = docstring, `AGENTS.md` o README del repo · `[MEDICIÓN]` = benchmark reproducible del repo (no hay ninguno, ver §10) · `[INFERENCIA]` · `[PENDIENTE]`.

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/pipecat-ai/pipecat.git` |
| branch | `main` (clon superficial: `git rev-list --count HEAD` = 1) |
| commit | `7d3bd9f4575bfb2a8495ea85cff4532499755c5b` ("Merge pull request #6089 from pipecat-ai/ss/soniox-error-category") |
| fecha (`git log -1 --format=%cI`) | `2026-10-09T12:58:04-04:00` |
| licencia observada | BSD 2-Clause, "Copyright (c) 2024–2026, Daily" (`LICENSE`); cabecera `SPDX-License-Identifier: BSD 2-Clause License` en cada fuente |
| submódulos | 0 (no hay `.gitmodules`) |
| paquete | `pipecat-ai`, `requires-python >=3.11`, `onnxruntime~=1.24.3`, `soxr~=1.0.0`, `numpy>=1.26.4,<3` (`pyproject.toml:6-51`) |
| señal de mantenimiento | `CHANGELOG.md`: 1.8.1 (2026-08-27), 1.9.0 (09-10), 1.10.0 (09-11), 1.11.0 (09-17), 1.12.0 (09-25); 145 fragmentos pendientes en `changelog/`; commit del mismo día de la auditoría. Mantenimiento muy activo, API en migración (`PipelineTask`→`PipelineWorker`, `PipelineRunner`→`WorkerRunner`, deprecaciones con retirada en 2.0.0) |
| estado del árbol | sin modificaciones; no se instaló nada; los tests no se ejecutaron (faltan dependencias, p. ej. `loguru`) → todo lo que dicen los tests es `[CÓDIGO]` sobre su contenido, no `[MEDICIÓN]` |

Nota de nombres: los nombres del encargo cambiaron en este commit. `src/pipecat/pipeline/task.py` y `runner.py` son ahora alias deprecados (`task.py:7-29`, `runner.py:1-37`); la implementación real está en `pipeline/worker.py` (`PipelineWorker`) y `workers/runner.py` (`WorkerRunner`). `StartInterruptionFrame` y `BotInterruptionFrame` **ya no existen** (grep sin resultados); el contrato actual es `InterruptionFrame` + `InterruptionWorkerFrame` (+ alias deprecado `InterruptionTaskFrame`, `frames.py:2145-2158`). `UninterruptibleFrame` sobrevive como marcador deprecado (`frames.py:154-170`); lo vigente es el campo `Frame.interruptible`.

---

## 2. Dictamen ejecutivo

1. **Pipecat no separa media de control. Una llamada es un pipeline de `FrameProcessor`s en un event loop de asyncio, y cada trama de audio cruza ese loop varias veces.** `[CÓDIGO]` Cada procesador no-direct tiene dos tareas asyncio (entrada y proceso) y una cola de prioridad (`frame_processor.py:FrameProcessor.__init__:258-286`). El audio de entrada es `SystemFrame` (`frames.py:InputAudioRawFrame:1670`): en cada salto paga un `put`/`get` en una `PriorityQueue`, un cambio de tarea y el fan-out a los observers. `[INFERENCIA]` En un pipeline telefónico típico eso da unos 6-7 saltos por trama de 20 ms.
2. **Varios pipelines comparten el loop.** `[CÓDIGO]` `WorkerRunner` construye su `TaskManager` sobre `asyncio.get_running_loop()` (`workers/runner.py:WorkerRunner.__init__:141`). El runner de desarrollo atiende cada WebSocket telefónico como una corrutina dentro del único proceso uvicorn (`runner/run.py:_run_telephony_bot:577-589`, `uvicorn.run:2260`). El propio repo dice que un loop compartido se atasca: su suite de evals lanza **un subproceso por sesión** porque, en un proceso compartido, "those loads would stall the event loop that paces every other run's audio" (`evals/_session_subprocess.py:7-12`) `[DOCUMENTACIÓN]`.
3. **No paraleliza entre núcleos ni elimina el coordinador.** Lo único que sale del loop es la inferencia: Silero en un `ThreadPoolExecutor(max_workers=1)` **por analizador**, con un viaje de ida y vuelta al hilo por cada trama (`audio/vad/vad_analyzer.py:VADAnalyzer.__init__:90-92`, `analyze_audio:178-192`), y smart-turn en otro executor por instancia (`audio/turn/smart_turn/base_smart_turn.py:_model_executor:179-183`). Es el mismo patrón `submit + resume` por trama que ya es el cuello de Golden, sin batching y con un pool por sesión. `[CÓDIGO]` El `WorkerBus` (local o Redis/PGMQ) es un pub/sub de difusión para traspasos entre agentes, no un mecanismo de reparto de media (`bus/bus.py:on_message_received:153-160`).
4. **Su valor para Golden está en el contrato de interrupción, no en la capacidad.** Es la referencia más completa de las revisadas para tres invariantes: (a) purga selectiva por la bandera `interruptible`, con prioridad de sistema y cancelación y recreación de la tarea de proceso; (b) identidad de generación en TTS (`context_id`), que se descarta de golpe al interrumpir y hace que se ignore lo que llega tarde; (c) emisor de salida que cancela sus tareas de audio y reloj, purga y reinicia el resampler. Todo esto se traduce directamente a `SpeechInterrupted`/`CancelSpeech`/`speech_id` (§13).
5. **Valor: ADAPTAR (patrones de interrupción y purga) · DESCARTAR como runtime o motor Media.** Adoptar el framework haría que Golden pagara más trabajo por trama en el loop que hoy `[INFERENCIA]`, y obligaría a reescribir su pipeline sobre una API que cambia cada semana.

---

## 3. Diagrama del flujo relevante

```
 Proveedor telefónico / ai-engine (WS)                 UN proceso Python · UN event loop asyncio
 ─────────────────────────────────────                 ───────────────────────────────────────────────────────────
   media 20 ms ──► FastAPIWebsocketInputTransport._receive_messages (tarea)          [fastapi.py:372-396]
                     serializer.deserialize: json+b64+ulaw2lin+soxr (síncrono, EN EL LOOP) [twilio.py:276-306]
                     push_audio_frame → asyncio.Queue (sin cota) → _audio_task_handler    [base_input.py:258-265,330-357]
                              │ push_frame (SystemFrame)
                              ▼
   ┌──────────── PipelineWorker (1 por llamada) ─ _push_queue ─ Source(direct) ────────────────────────────┐
   │ [STT]  in-queue(Prio) ─► in-task ─► process_audio_frame → WS STT (await)  ─► push (passthrough=True)  │
   │ [UserAgg] in-queue ─► in-task ─► VADController → run_in_executor(1 hilo/llamada, Silero ORT/llamada)  │
   │                                  UserTurnController → smart-turn.append_audio (síncrono)              │
   │                                  inicio de turno ⇒ broadcast_interruption() ⇅                        │
   │ [LLM]  in-queue ─► in-task (System) │ proc-queue ─► proc-task: stream de tokens (cancelable)          │
   │ [TTS]  ... proc-task ─► WS TTS ─► _audio_contexts[context_id] ─► audio_context_task ─► push         │
   │ [Output] proc-task ─► MediaSender[dest]: resample(soxr) → chunks 40 ms → FrameQueue                   │
   │            ├─ _audio_task: write_audio_frame → serialize → ws.send → sleep(chunk/2)  (2× tiempo real) │
   │            └─ _clock_task: frames con pts (TTSTextFrame) liberados al llegar su pts                   │
   │ [AssistantAgg] recibe sólo lo que pasó el Output; con Interruption → commit(interrupted=True)        │
   └── Sink(direct) ─► _sink_push_frame (fin, heartbeats, flush probe) ────────────────────────────────────┘
        cada salto: WorkerObserver.on_process_frame/on_push_frame → put en N colas de observers (sin cota)
   ws.send ◄────────────── audio µ-law 40 ms · en interrupción: {"event":"clear"} (sólo si el serializer lo define)

 Llamadas 2..N: otros PipelineWorker en EL MISMO loop (dev runner / WorkerRunner) — sin sharding, sin afinidad a núcleo.
 WorkerBus (AsyncQueueBus | RedisBus | PgmqBus): pub/sub de difusión entre workers; BusBridgeProcessor puede meter
 cualquier frame (incluido audio) en el bus salvo exclude_frames.
```

---

## 4. Mapa de procesos, hilos, loops y colas

| elemento | cantidad | cota | evidencia |
|---|---|---|---|
| procesos | 1 por servidor (dev runner); el repo no trae pool de procesos para sesiones. Un subproceso por sesión **sólo en evals** | — | `[CÓDIGO]` `runner/run.py:2260`; `evals/suite.py:1092-1151`; `evals/_session_subprocess.py:7-12` |
| event loops | 1 compartido por todos los `PipelineWorker` del proceso | — | `[CÓDIGO]` `workers/runner.py:141`; `utils/asyncio/task_manager.py:TaskManager.create_task:167-201` (`self._loop.create_task`) |
| tareas por procesador (no direct) | 2: `__input_frame_task` (frames de sistema inline) y `__process_frame_task` (datos/control) | — | `[CÓDIGO]` `frame_processor.py:__create_input_task:1204-1211`, `__create_process_task:1222-1229` |
| procesadores direct (sin colas) | `Pipeline`, `PipelineSource`, `PipelineSink`, `SyncParallelPipeline`, ramas de `ServiceSwitcher` | — | `[CÓDIGO]` `pipeline.py:36,72,113`; grep `enable_direct_mode=True` |
| cola de entrada | `FrameProcessorQueue(asyncio.PriorityQueue)`: Start=1, System=10, resto=20 + contador FIFO | **sin cota** | `[CÓDIGO]` `frame_processor.py:FrameProcessorQueue:131-182` |
| cola de proceso | `FrameQueue(asyncio.Queue)` con `reset()` selectivo | **sin cota** | `[CÓDIGO]` `utils/frame_queue.py:16-87` |
| cola de push del worker | `asyncio.Queue()` | **sin cota** | `[CÓDIGO]` `pipeline/worker.py:486` |
| cola de audio de entrada | `asyncio.Queue()` por transporte | **sin cota** | `[CÓDIGO]` `transports/base_input.py:_create_audio_task:318-322` |
| `MediaSender` por destino de salida | 1 por `transport_destination` (más el `None` por defecto); cada uno con `_audio_task`, `_clock_task`, `_video_task` y un `ThreadPoolExecutor(1)` para redimensionar imágenes | **sin cota** (`FrameQueue`, `PriorityQueue`) | `[CÓDIGO]` `transports/base_output.py:set_transport_ready:192-233`, `MediaSender.__init__:433-509`, `_create_audio_task:720-724`, `_create_clock_task:1182-1186` |
| hilos de inferencia | `ThreadPoolExecutor(1)` por `VADAnalyzer` + `ThreadPoolExecutor(1)` perezoso por `BaseSmartTurn` ⇒ ~2 hilos de SO por llamada | — | `[CÓDIGO]` `vad_analyzer.py:90-92`; `base_smart_turn.py:179-183` |
| sesiones ORT | Silero: 1 `InferenceSession` por `SileroVADAnalyzer` (intra=inter=1). Smart-turn v3: 1 sesión por `LocalSmartTurnAnalyzerV3` (`ORT_SEQUENTIAL`, intra=`cpu_count`=1). Las estrategias por defecto construyen una nueva por `UserTurnStrategies()` | — | `[CÓDIGO]` `silero.py:SileroOnnxModel.__init__:42-60`, `SileroVADAnalyzer.__init__:165`; `local_smart_turn_v3.py:70-76`; `turns/user_turn_strategies.py:default_user_turn_stop_strategies:44-53`, `UserTurnStrategies.__post_init__` |
| colas de observers | 1 `asyncio.Queue` + 1 tarea por observer; `put` por cada `on_process_frame` y `on_push_frame` (por defecto `observe_every_push=True`) | **sin cota** | `[CÓDIGO]` `pipeline/worker_observer.py:_create_proxy:212-217`, `on_push_frame:167-185`, `_send_to_proxy:227-231`; `observers/base_observer.py:129-138` |
| observers por defecto | `TurnTrackingObserver` (activo por defecto), `IdleFrameObserver` (idle 300 s), `RTVIObserver` (si no hay `bridged`); latencia/traza sólo con tracing | — | `[CÓDIGO]` `pipeline/worker.py:292,303,455-546` |
| bus | por suscriptor: `BusMessageQueue` (prioridad) + `data_queue` + 2 tareas | **sin cota** | `[CÓDIGO]` `bus/bus.py:BusSubscription:26-46`, `_start_dispatch_task:162-167`; `bus/queue.py:18-61` |
| heartbeats / idle | tarea de push cada 1 s + monitor (con `enable_heartbeats`, apagado por defecto); tarea idle | — | `[CÓDIGO]` `pipeline/worker.py:98-110,1598-1667` |

---

## 5. Ownership por sesión y recursos compartidos

- **Unidad de ownership = `PipelineWorker` (un pipeline por llamada).** El worker posee la cola de push, el reloj, los observers y la secuencia Start→…→End/Cancel (`pipeline/worker.py:run:862-906`, `_process_push_queue:1392-1433`) `[CÓDIGO]`. Cada `FrameProcessor` posee su estado mutable y sus colas. Sólo lo mutan sus propias tareas, salvo `pause/resume`, que se invocan desde otros procesadores mediante frames `FrameProcessorPause*` y comparan `frame.processor.name == self.name` (`frame_processor.py:1109-1125`) `[CÓDIGO]`.
- **Estado local por llamada**: buffers de VAD (`_vad_buffer`, `_vad_state`), buffer de smart-turn, contextos TTS (`_audio_contexts`), buffers de chunk y resampler del `MediaSender`, contexto LLM (`LLMContext`) en los agregadores `[CÓDIGO]` (`vad_analyzer.py:83`, `base_smart_turn.py:72`, `tts_service.py:409-411`, `base_output.py:465-474`).
- **Recursos compartidos entre llamadas**: el event loop y el `TaskManager` (`workers/runner.py:141`); en un `WorkerRunner` multi-worker, también el bus y el registro (`workers/runner.py:144-145`) `[CÓDIGO]`. **No** se comparten modelos ORT por defecto: cada analizador carga su sesión (§4). El docstring de `VADController.stop` dice "The analyzer may be shared" (`vad_controller.py:147-156`), pero `VADAnalyzer` guarda estado por flujo (`_vad_buffer`, contadores, `_prev_volume`). `[INFERENCIA]` Compartir una instancia entre llamadas mezclaría estados; sólo es seguro reutilizarla en la misma llamada.
- **Comparación con Golden**: Golden tiene un objeto con un `SessionState` de 106 campos por llamada y comparte el modelo ORT entre sesiones (BRIEF). Pipecat reparte el estado de la llamada entre ~8 procesadores, cada uno con su estado y su comunicación por frames. Eso evita la mutación directa Media→Conversación de Golden, a cambio de duplicar las sesiones ORT por llamada.

---

## 6. Contrato de control/IPC

- **Dentro de un pipeline (memoria, mismo loop)**: `Frame` dataclass con `id` (contador del proceso, `obj_id()`), `name`, `pts`, `broadcast_sibling_id`, `metadata`, `transport_source`, `transport_destination`, `interruptible` (`frames.py:Frame:65-108`) `[CÓDIGO]`. Hay tres clases: `SystemFrame` (inmediato, "not affected by user interruptions"), `DataFrame` y `ControlFrame` (ordenados y purgables) (`frames.py:113-148`) `[CÓDIGO]`. Dirección DOWNSTREAM/UPSTREAM (`frame_processor.py:FrameDirection:58-67`). **No hay ACK, ni seq por sesión, ni epoch, ni deduplicación general** `[CÓDIGO]`. Las únicas deduplicaciones están en `ParallelPipeline._seen_ids` (`parallel_pipeline.py:52,166-178`) y en `first_push` de los observers (`worker_observer.py:178-181`).
- **Worker ↔ pipeline**: los frames `WorkerFrame`/`WorkerSystemFrame` (`EndWorkerFrame`, `StopWorkerFrame`, `CancelWorkerFrame`, `InterruptionWorkerFrame`) se empujan dentro del pipeline y el worker los traduce en `EndFrame`/`CancelFrame`/`InterruptionFrame` (`pipeline/worker.py:_source_push_frame:1481-1523`, `_sink_push_frame:1546-1596`) `[CÓDIGO]`. Un `InterruptionWorkerFrame` **se salta la cola de push** y entra directo al pipeline para no quedar bloqueado detrás de un `EndFrame` en curso (`worker.py:1504-1510`) `[CÓDIGO]`.
- **Entre workers (bus)**: `BusMessage{id, name, source, target}`. Hay `BusDataMessage` (FIFO) y `BusSystemMessage` (adelanta a los datos) (`bus/messages.py:33-92`), además de `BusFrameMessage{frame, direction, bridge}` (`messages.py:95-117`) y la RPC de jobs con `timeout` (`pipeline/job_context.py:31-45`) `[CÓDIGO]`. Entrega por **difusión**: cada mensaje va a la cola de cada suscriptor y cada uno filtra (`bus/bus.py:on_message_received:153-160`; `bridge_processor.py:on_bus_message:138-168`) `[CÓDIGO]`. En red, `RedisBus` publica JSON (bytes en base64) a un canal pub/sub (`bus/network/redis.py:publish:88-96`; `bus/serializers/json.py:101-102`) `[CÓDIGO]`. Pub/sub de Redis no da durabilidad ni reentrega `[INFERENCIA]`.
- **Hacia el cliente telefónico**: `FrameSerializer` por proveedor. Twilio: `media` con µ-law en base64 y `clear` al recibir `InterruptionFrame` (`serializers/twilio.py:serialize:166-210`) `[CÓDIGO]`.

**Contra el contrato de referencia de Golden `(session_id, session_epoch, turn_id, speech_id, seq)`**: Pipecat sólo tiene un equivalente real de `speech_id` (`context_id` de TTS, §8). `turn_id` existe sólo como contador local del `TurnTrackingObserver` (`observers/turn_tracking_observer.py:72,184-199`) y no viaja en los frames. No tiene `epoch` ni `seq`, porque todo ocurre dentro de un proceso y con orden garantizado por las colas. **Lección**: el contrato de Golden necesita `epoch` y `seq` precisamente porque cruza una frontera que Pipecat nunca cruza con media.

---

## 7. Ruta de audio y frecuencia de cruces

**Entrada (por mensaje WS de 20 ms, pipeline típico `input, stt, user_agg, llm, tts, output, assistant_agg`):**

1. `_receive_messages` → `serializer.deserialize`: `json.loads`, base64, `audioop.ulaw2lin` y `soxr` streaming, **síncronos en el loop** (`fastapi.py:372-396`; `twilio.py:276-306`; `audio/utils.py:ulaw_to_pcm:226-246`; `soxr_stream_resampler.py:resample`) `[CÓDIGO]`.
2. `push_audio_frame` → cola de audio → `_audio_task_handler` → filtro opcional → `push_frame` (`base_input.py:258-265,330-357`) `[CÓDIGO]`.
3. Por cada procesador no-direct: `queue_frame` → `PriorityQueue.put` → la tarea de entrada despierta → `process_frame` (inline, por ser `SystemFrame`) → `push_frame` → siguiente (`frame_processor.py:699-727,1286-1312,1160-1202`) `[CÓDIGO]`. En cada salto, `on_process_frame` y `on_push_frame` hacen un `put` en cada cola de observer (`frame_processor.py:826-834,1173-1193`; `worker_observer.py:159-185`) `[CÓDIGO]`.
4. STT: `process_audio_frame` → `run_stt` (envío WS) y passthrough hacia abajo (`stt_service.py:421-469,474-480`; `audio_passthrough=True` por defecto, línea 97) `[CÓDIGO]`.
5. UserAgg: `VADController._handle_audio` → `analyze_audio` → `run_in_executor` (**viaje a un hilo por trama**) → Silero por bloque de 256/512 muestras (`vad_controller.py:170-200`; `vad_analyzer.py:178-248`) `[CÓDIGO]`. Después, `UserTurnController` → `TurnAnalyzerUserTurnStopStrategy._handle_input_audio` → `append_audio` **síncrono en el loop** (`frombuffer`, `list.append`, recorte) (`turn_analyzer_user_turn_stop_strategy.py:203-215`; `base_smart_turn.py:100-152`) `[CÓDIGO]`. La inferencia de smart-turn va al executor sólo cuando el VAD detecta el final del habla (`turn_analyzer_user_turn_stop_strategy.py:225-238`; `base_smart_turn.py:154-168`): el mismo gating que Golden.
6. El frame sigue pasando por LLM, TTS, Output y AssistantAgg (passthrough de `SystemFrame`) `[CÓDIGO]` (`base_output.py:386-387`; `llm_response_universal.py:887-888`).

`[INFERENCIA]` Recuento por trama de entrada, derivado del código y sin medir: unas 6-7 entregas por cola asyncio y cambios de tarea, 1 viaje ida y vuelta a un hilo, unos `2 × saltos × N_observers` (≈ 36 con 3 observers) `put` en colas de observers con sus despertares, más decodificación y resampleo síncronos. **Golden paga hoy 1 `submit` + 1 `resume` por mensaje (o por lote de hasta 10 fuera de TTS)** (BRIEF). En el camino por trama, Pipecat hace más trabajo de coordinación que Golden, no menos.

**Salida (TTS → cliente):**

1. Audio del proveedor → `append_to_audio_context(context_id)` → cola del contexto → `_audio_context_task_handler` → `push_frame` (`tts_service.py:1626-1665,1795-`) `[CÓDIGO]`.
2. Output (cola de proceso, `DataFrame`) → `MediaSender.handle_audio_frame`: resampleo con soxr **síncrono en el loop**, buffer de "runs" con la bandera de interrupción y corte en chunks de `audio_out_10ms_chunks × 10 ms` (**por defecto 4 = 40 ms**) (`base_output.py:631-654,144-148`; `base_transport.py:117`) `[CÓDIGO]`.
3. `_audio_task_handler` → `_internal_write_audio_frame`, con `wait_for(write_audio_frame, audio_out_write_timeout_secs=10)` (`base_output.py:1007-1068`; `base_transport.py:124`) `[CÓDIGO]`.
4. Transporte WS: serializa (`lin2ulaw`, base64, `json.dumps`), envía y `_write_audio_sleep` con `send_interval = (chunk/sample_rate)/2` ⇒ **envía al doble del tiempo real** (`fastapi.py:456,526-557,598-607`) `[CÓDIGO]`. `[INFERENCIA]` En una locución de T segundos, el cliente puede tener en su buffer hasta ~T/2 s de audio que ya no se puede retirar si el protocolo no tiene `clear`.
5. Tras escribir, el chunk se empuja hacia abajo, al AssistantAgg (otro salto y más fan-out a observers) (`base_output.py:1035-1038`) `[CÓDIGO]`.

**¿Cruza el runtime de control?** Pipecat no tiene un runtime de control separado: media y conversación son el mismo loop. Si se activa `bridged` o un `BusBridgeProcessor`, **cualquier frame no excluido, audio incluido, se publica en el bus** (`bridge_processor.py:process_frame:104-136`; `_BusEdgeProcessor.process_frame:207-221`) `[CÓDIGO]`. En el ejemplo `redis-handoff` el bridge va detrás de `aggregators.user()`, y el agregador reenvía `InputAudioRawFrame` por defecto (`examples/multi-worker/distributed-handoff/redis-handoff/main.py:95-110`; `llm_response_universal.py:887-888`). `[INFERENCIA]` En ese ejemplo, el audio de entrada viaja por Redis trama a trama salvo que se pase `exclude_frames`.

---

## 8. Secuencia de barge-in

**Disparo.** El VAD detecta el inicio del habla → `UserTurnController` → `LLMUserAggregator._on_user_turn_started` → `broadcast_frame(UserStartedSpeakingFrame)` y, si `enable_interruptions`, `broadcast_interruption()` (`llm_response_universal.py:1341-1366`) `[CÓDIGO]`. Cualquier procesador puede interrumpir (`frame_processor.py:broadcast_interruption:1018-1023`) `[DOCUMENTACIÓN]` (`AGENTS.md`, "Interruptions").

**Propagación.** `broadcast_interruption` primero **reinicia su propia cola de proceso** (`__reset_process_task`) y después emite dos `InterruptionFrame` hermanos (`broadcast_sibling_id`), uno hacia arriba y otro hacia abajo (`frame_processor.py:1018-1023,1039-1055`) `[CÓDIGO]`. Por ser `SystemFrame`, en cada procesador adelanta a los datos encolados (prioridad 10 frente a 20) y se procesa inline en la tarea de entrada (`frame_processor.py:131-182,1303-1304`) `[CÓDIGO]`.

**Qué vacía cada procesador (núcleo del contrato).** `FrameProcessor.process_frame` → `_start_interruption` (`frame_processor.py:838-840,1131-1158`) `[CÓDIGO]`:
- Si el frame **en curso** es interrumpible: `__cancel_process_task()` (cancela la corrutina en curso: el stream del LLM, un `run_tts` HTTP…) y `__create_process_task()`, que llama a `__reset_process_queue()`.
- Si el frame en curso **no** es interrumpible: no lo cancela y sólo purga la cola.
- `FrameQueue.reset()` quita los interrumpibles y **conserva los no interrumpibles en su orden** (`frame_queue.py:74-87`). Son no interrumpibles `EndFrame`, `StopFrame`, `EndWorkerFrame`, `StopWorkerFrame`, `PipelineFlushFrame`, `FunctionCallResultFrame`, `FunctionCallInProgressFrame`, `LLMContextSummaryResultFrame`, `ServiceUpdateSettingsFrame`, `AudioBuffer*RecordingFrame`, `Volume*Frame` (`frames.py:884,2012,2030,2183,2204,2235,2446,2476,2571,2663,2670,2717,2737`) `[CÓDIGO]`. Contrato fijado en tests: `tests/test_frame_processor.py:test_interruptible_frames:125`, `test_uninterruptible_frames:159`, `test_terminal_frames_survive_interruption:379` `[CÓDIGO]` (no ejecutados).

**Por servicio.**
- LLM: al cancelarse la tarea de proceso se corta `_process_context` (el stream corre dentro de `process_frame`: `services/openai/base_llm.py:673-688`); además cancela las function calls con `cancel_on_interruption` (`llm_service.py:_handle_interruptions:913-916`) `[CÓDIGO]`.
- TTS: `_handle_interruption` limpia el agregador de texto, los filtros y el secuenciador, y detiene la tarea de contextos de audio. `_serialization_queue.reset()` conserva los no interrumpibles. Llama a `on_audio_context_interrupted(ctx_id)` por cada contexto vivo (Cartesia envía `{"context_id","cancel":true}`), `reset_active_audio_context()`, `_turn_context_id=None`, y **recrea la tarea con un `_audio_contexts` vacío** (`tts_service.py:1141-1181,1784-1793`; `cartesia/tts.py:651-657`) `[CÓDIGO]`. **Cómo se descarta lo obsoleto**: el audio tardío del proveedor con un `context_id` antiguo encuentra `audio_context_available()==False` y se tira (`cartesia/tts.py:_process_messages:720-724`; `tts_service.py:append_to_audio_context:1643-1653`) `[CÓDIGO]`. Excepción: sólo se recrea un contexto si coincide con `_turn_context_id`, que la interrupción ya ha anulado.
- STT: sólo reinicia el estado de TTFB y deja pasar el frame; no cierra el stream (`stt_service.py:508-510,580-593`) `[CÓDIGO]`.
- Output (`BaseOutputTransport.process_frame`): primero la purga base, luego `push_frame(Interruption)` y después `MediaSender.handle_interruptions` (`base_output.py:379-381,393-404`) `[CÓDIGO]`. El `MediaSender` cancela `_clock_task` (pierde los `TTSTextFrame` temporizados que no han llegado a su pts) y `_video_task`; cancela y recrea `_audio_task` con una `FrameQueue` nueva, o hace `reset()` si hay no interrumpibles o mezclador. Recrea las tareas y llama a `_bot_stopped_speaking()`, que **vacía el buffer de chunks y reinicia el resampler** para que no se cuele audio residual en la siguiente locución, y emite `BotStoppedSpeakingFrame` en ambas direcciones (`base_output.py:602-629,829-858`) `[CÓDIGO]`. Tests: `test_interruption_without_mixer_recreates_audio_task:149`, `test_interruption_with_mixer_still_discards_queued_bot_audio:163`, `test_interruption_does_not_replay_aborted_audio:521`, `test_chunks_carry_the_flag_of_their_audio:203` en `tests/test_base_output_transport.py` `[CÓDIGO]`.
- Transporte WS: con `fixed_audio_packet_size` vacía `_audio_send_buffer`; **serializa el `InterruptionFrame`** (Twilio → `{"event":"clear"}`) y pone `_next_send_time=0` (`fastapi.py:499-514`; `twilio.py:186-188`) `[CÓDIGO]`.
- AssistantAgg (aguas abajo del Output): `_handle_interruptions` → `_trigger_assistant_turn_stopped(interrupted=True)` → `push_aggregation()` con el texto que **llegó a pasar el Output**, y emite `LLMContextAssistantTurnFrame` (`llm_response_universal.py:1958-1960,2506-2526`) `[CÓDIGO]`. Es decir: **el historial se trunca a lo "hablado" porque lo escribe un consumidor aguas abajo del reproductor, no porque el reproductor mute el historial**.

**Matiz de destino.** `_handle_frame` enruta la interrupción por `frame.transport_destination` (`base_output.py:395-404`) `[CÓDIGO]`. `broadcast_interruption` crea el frame sin destino. `[INFERENCIA]` Una interrupción difundida sólo purga el `MediaSender` por defecto (`None`); los destinos con nombre no se purgan salvo que alguien emita una interrupción con su destino.

**Riesgo de cuelgue.** `__cancel_process_task` espera la cancelación **sin timeout** (`frame_processor.py:1258-1262`; `task_manager.py:cancel_task:222-280`), mientras que la tarea de entrada usa un timeout de 3 s (`frame_processor.py:191,1213-1220`) `[CÓDIGO]`. `[INFERENCIA]` Si una librería se traga `CancelledError` dentro de `process_frame`, la interrupción bloquea la tarea de entrada de ese procesador y, con ella, todos sus frames de sistema, audio incluido.

---

## 9. Backpressure, fallos, reconexión y drenaje

- **Backpressure**: ninguna cola está acotada (§4) `[CÓDIGO]`. Las únicas válvulas son temporales: el timeout de escritura de audio (10 s), que marca el transporte como inutilizable "once" (`base_output.py:1040-1068`; test `test_peer_is_written_off_once:404`), y `pause_processing_all_frames_until` con timeout de 5 s (`frame_processor.py:755-817`) `[CÓDIGO]`. `[INFERENCIA]` Entre pipelines hay **aislamiento de colas pero no de CPU**: cada llamada tiene sus colas, pero el trabajo síncrono de una (decodificar, resamplear, `append_audio`, observers) retrasa a todas las del loop. Es el mismo modo de fallo que Golden y que la suite de evals de Pipecat evita con subprocesos (`_session_subprocess.py:7-12`).
- **Errores de procesador**: cualquier excepción en `process_frame` se convierte en `ErrorFrame` hacia arriba (`frame_processor.py:1264-1284`). El worker aplica `ProcessorUnusablePolicy` (CONTINUE por defecto, END o CANCEL) una sola vez por procesador (`worker.py:146-165,1513-1544`) `[CÓDIGO]`.
- **Caída del peer WS**: `_receive_messages` termina → `trigger_client_disconnected` (`fastapi.py:390-396`). Decide la aplicación, normalmente cancelando el worker `[CÓDIGO]`/`[INFERENCIA]`. **Reconexión**: los servicios TTS/STT reconectan sus WS (p. ej. Cartesia, `_receive_messages`, `cartesia/tts.py:765-771`), y STT guarda el audio mientras reconecta (`stt_service.py:194,433-435`) `[CÓDIGO]`. El transporte de entrada no reconecta: la sesión muere con el WS.
- **Idle**: cancela el worker a los 300 s sin frames de actividad (`worker.py:104,1628-1667`) `[CÓDIGO]`.
- **Drenaje**: `EndFrame` (no interrumpible) recorre el pipeline detrás de lo encolado. El `MediaSender` encola `EndFrame` en sus colas de audio y reloj y espera a sus tareas; el audio envía `audio_out_end_silence_secs=2` s de silencio (`base_output.py:554-578,1011-1014`; `base_transport.py:122`). El worker espera **sin timeout** a que `EndFrame` llegue al sink ("Ending flushes what is queued, so cutting the wait short would drop the audio") y a `CancelFrame` con 20 s (`worker.py:106,1235-1267`) `[CÓDIGO]`. `flush_pipeline()` usa una sonda `PipelineFlushFrame` de ida, vuelta e ida como barrera de vaciado, con timeout por falta de progreso (`worker.py:946-992,1435-1461`) `[CÓDIGO]`. **Despliegue**: `WorkerRunner` sólo atiende SIGTERM si `handle_sigterm=True` (por defecto `False`), y entonces **cancela**, no drena (`workers/runner.py:115,547-564`) `[CÓDIGO]`.
- **Bus/remoto**: el registro de workers no tiene liveness ni expiración (grep sin `timeout`/`heartbeat` en `registry/`) `[CÓDIGO]`. Un runner remoto muerto sólo se nota por los timeouts de jobs (`job_context.py:31-45,129-135`) `[CÓDIGO]`.

---

## 10. Escalado y uso real de núcleos

- **Mediciones**: el repo no tiene benchmarks de concurrencia, CPU por sesión ni latencia (búsqueda de `*bench*` y de "sessions per/calls per" sin resultados) → **ninguna `[MEDICIÓN]`**. `[PENDIENTE]` Cualquier capacidad de Pipecat por loop o por proceso.
- **Núcleos**: un loop por proceso. El paralelismo real se limita a ORT dentro de los executors por sesión (intra-op 1), es decir, inferencia en hilos y no coordinación `[CÓDIGO]` (§4). `pyproject` no declara soporte para free-threading `[CÓDIGO]` (`pyproject.toml:12-51`). `[PENDIENTE]` Comportamiento con 3.14t.
- **Escalado horizontal**: lo que el repo ofrece es un subproceso por sesión en evals y el `WorkerBus` en red para workers en procesos o máquinas distintos (`examples/multi-worker/README.md`, "Distributed bus") `[DOCUMENTACIÓN]`. El reparto de llamadas entre procesos o contenedores (Pipecat Cloud u otro) **no está en el repo** `[PENDIENTE]`.

Distinción obligatoria:

| | Pipecat | comentario |
|---|---|---|
| **K_loop** | `[PENDIENTE]` | `[INFERENCIA]` Más trabajo de coordinación por trama que Golden (§7). No es un mecanismo para subir K_loop |
| **K_worker** | = K_loop (1 loop por proceso) | Los hilos de VAD y smart-turn por sesión no quitan coordinación del loop; añaden 2 hilos y 2 sesiones ORT por llamada |
| **K_task** | N procesos × K_worker, si alguien los empaqueta | El repo no trae ese empaquetado para producción; es **replicación** |
| **K_flota** | fuera del repo | `[PENDIENTE]` |

---

## 11. Patrones y antipatrones

**Implementados y valiosos**
- Cola de prioridad de tres niveles (Start, System, resto) con FIFO dentro de cada nivel, mediante un contador que evita comparar frames (`frame_processor.py:131-182`) `[CÓDIGO]`.
- Purga selectiva por bandera de frame (`interruptible`), configurable por clase **o por instancia** (`frames.py:80-106`; `frame_queue.py:74-87`) `[CÓDIGO]`.
- Preempción por cancelación de la tarea de proceso, en vez de bandera cooperativa, con protección para el frame no interrumpible en curso (`frame_processor.py:1131-1158`) `[CÓDIGO]`.
- Identidad de generación en TTS (`context_id`) con un registro que se descarta de golpe y un hook de cancelación al proveedor (`tts_service.py:1141-1181,1616-1665,2035-2048`) `[CÓDIGO]`.
- Emisor por destino con cancelación y recreación de tareas, reinicio del resampler y chunks de 40 ms que conservan la bandera de su audio (`base_output.py:602-654,790-858`) `[CÓDIGO]`.
- Historial de la conversación escrito por un consumidor aguas abajo del reproductor (AssistantAgg detrás del Output), con `interrupted=True` `[CÓDIGO]` (§8).
- Sonda de vaciado (`PipelineFlushFrame`) como barrera de drenaje y traspaso (`worker.py:946-992`) `[CÓDIGO]`.
- Timeout de escritura que da por perdido al peer una sola vez (`base_output.py:1040-1068`) `[CÓDIGO]`.

**Antipatrones respecto a los objetivos de Golden**
- Saltos por procesador con cola y tarea propias en el camino de 50 fps (§7) `[CÓDIGO]`/`[INFERENCIA]`.
- `run_in_executor` por trama en un executor de un hilo por sesión, sin batching (`vad_analyzer.py:178-192`) `[CÓDIGO]`.
- Sesiones ORT y hilos duplicados por llamada (§4) `[CÓDIGO]`.
- Colas sin cota en toda la cadena, incluidos los observers (§4) `[CÓDIGO]`.
- Salida WS al doble del tiempo real: el residuo remoto crece con la locución (`fastapi.py:456`) `[CÓDIGO]`.
- `ParallelPipeline._seen_ids` crece sin límite con cada `frame.id` que pasa (`parallel_pipeline.py:52,173-174`) `[CÓDIGO]`. `[INFERENCIA]` Fuga de memoria proporcional a las tramas en llamadas largas si se usa en el camino de audio.
- Bus por difusión sin `seq`, `epoch` ni dedup; los mensajes de sistema adelantan a los de datos (`bus/bus.py:153-187`; `bus/queue.py:18-61`) `[CÓDIGO]`. No sirve como canal Media↔Conversación con garantías.
- SIGTERM → cancelar, no drenar (`workers/runner.py:556-564`) `[CÓDIGO]`.

---

## 12. Callers verificados de las piezas propuestas como referencia

| pieza | callers verificados |
|---|---|
| `FrameProcessor._start_interruption` | `FrameProcessor.process_frame:838-840` (todo procesador que llame a `super().process_frame`); `LLMService._handle_eager_end_of_turn_cancel` (`llm_service.py:918-932`) |
| `FrameProcessor.broadcast_interruption` | `LLMUserAggregator._on_user_turn_started` (`llm_response_universal.py:1363-1364`); alias deprecado `push_interruption_task_frame_and_wait` (`frame_processor.py:1025-1037`) |
| `FrameQueue.reset` | `FrameProcessor.__reset_process_queue` (`frame_processor.py:1240-1242`) ← `_start_interruption` y `__reset_process_task`; `MediaSender.handle_interruptions` (`base_output.py:619`); `TTSService._handle_interruption` (`tts_service.py:1159`) |
| `MediaSender.handle_interruptions` | `BaseOutputTransport._handle_frame:403-404` ← `process_frame:379-381` |
| `MediaSender._bot_stopped_speaking` (purga del buffer y reinicio del resampler) | `handle_interruptions:629`; `_handle_frame` en `TTSStoppedFrame:906-911`; fallback por timeout en `_next_frame:953-955,975-977`; `_maybe_bot_currently_speaking:871-877` |
| `TTSService._handle_interruption` | `TTSService.process_frame:886-887` |
| `TTSService.on_audio_context_interrupted` | `TTSService._handle_interruption:1160-1167`; override en `CartesiaTTSService:651-657` (y otros proveedores con WS) |
| `audio_context_available` como filtro de obsoletos | `append_to_audio_context:1643`; `CartesiaTTSService._process_messages:723` |
| `LLMAssistantAggregator._handle_interruptions` | `LLMAssistantAggregator.process_frame:1756-1757` |
| `PipelineWorker.flush_pipeline` | `_drain_pipeline:837-851` ← `end:795-806`, `activate_worker:808-835` |
| `_internal_write_audio_frame` (timeout de peer) | `_audio_task_handler:1028-1033`; `_send_silence:996-1005` |

---

## 13. Aplicabilidad a GoldenBridge

### 13.1 Respuestas a las 18 preguntas obligatorias

1. **Unidad de ownership**: el pipeline (`PipelineWorker`), uno por llamada. Dentro, cada `FrameProcessor` es dueño de su estado. Para el audio de salida, el `MediaSender` por `transport_destination` `[CÓDIGO]` (§5).
2. **¿Quién recibe cada paquete?** La tarea `_receive_messages` del transporte de entrada de esa llamada, en el loop compartido; después lo recibe cada procesador en orden (§7) `[CÓDIGO]`.
3. **¿Cuántas veces cruza el runtime de control?** No hay un runtime de control separado; el paquete pasa por el loop en cada salto: ~6-7 entregas por cola más 1 viaje a hilo por trama de entrada; la salida pasa por TTS → Output → AssistantAgg `[INFERENCIA]` (§7). En modo `bridged` puede cruzar además el bus `[CÓDIGO]`.
4. **¿Loop único compartido?** Sí, por todos los pipelines del proceso (`workers/runner.py:141`; `runner/run.py:577-589,2260`). Hace todo: E/S WS, serialización y codec µ-law, resampleo soxr, colas de cada procesador, fan-out a observers, lógica de turnos, `append_audio` de smart-turn, streams de LLM/TTS/STT y temporización de salida `[CÓDIGO]`.
5. **Varios núcleos**: sólo por inferencia ORT en executors de un hilo por sesión; ningún paralelismo de coordinación. Escalar = más procesos, fuera del repo, salvo evals (subproceso por sesión) `[CÓDIGO]`/`[PENDIENTE]`.
6. **Asignación y afinidad**: no hay asignador. La conexión WS que llega a un proceso queda en ese proceso y en ese loop hasta el final, por construcción `[CÓDIGO]`/`[INFERENCIA]`. En el bus, el "dueño" se elige por nombre (`target`, `bridge`) y con activación y desactivación de workers (`base_worker.py:accepts_bus_message:495-513`) `[CÓDIGO]`.
7. **Estado compartido y local**: compartidos el loop, el `TaskManager`, el bus y el registro. Local por llamada: todo lo demás, incluidas las sesiones ORT (§5) `[CÓDIGO]`.
8. **Protocolo de control**: frames en memoria; bus pub/sub (memoria, Redis pub/sub, PGMQ) con JSON y base64; serializers por proveedor hacia telefonía (§6) `[CÓDIGO]`.
9. **Orden, correlación, ACK, dedup y tardíos**: FIFO por procesador dentro de cada nivel, con frames de sistema que adelantan a los de datos. Correlación por `frame.id` local, `broadcast_sibling_id`, `context_id` (TTS) y `transport_destination`. Sin ACK. Dedup sólo en `ParallelPipeline` y observers. Tardíos: TTS los descarta por contexto desconocido; el LLM, por cancelación de su tarea (§8) `[CÓDIGO]`.
10. **Backpressure sin afectar a otras llamadas**: no hay; colas sin cota y loop compartido. La única protección es el timeout de 10 s por escritura (§9) `[CÓDIGO]`/`[INFERENCIA]`.
11. **¿Dónde ocurre el barge-in y dónde se purga?** Lo dispara el agregador de usuario a partir del VAD y la estrategia de turno. Se purga **en cada procesador** (cola de proceso más cancelación de la tarea), en TTS (contextos y cancelación al proveedor), en el `MediaSender` (cola de audio, reloj, buffer de chunks y resampler) y en el cliente mediante el `clear` del serializer (§8) `[CÓDIGO]`.
12. **Audio que ya no puede retirarse**: el chunk de 40 ms en escritura y todo lo enviado al cliente. Con el transporte WS, que envía al doble del tiempo real, eso puede ser hasta ~la mitad de la locución si el protocolo no tiene `clear` (`fastapi.py:456,598-607`) `[CÓDIGO]`/`[INFERENCIA]`. También la cola del proveedor si este no implementa `on_audio_context_interrupted` `[INFERENCIA]`.
13. **Si muere Media, Control o el canal**: son el mismo proceso, así que si muere el proceso mueren todas sus llamadas. Una excepción de procesador produce `ErrorFrame` y se aplica la política. Si se cae el WS del cliente, la aplicación cancela. Si muere un worker remoto del bus, no se detecta salvo por timeouts de jobs (§9) `[CÓDIGO]`.
14. **Drenaje en shutdown o despliegue**: `EndFrame` drena por pipeline y espera sin límite; SIGTERM cancela y sólo si está activado. No hay drenaje coordinado de muchas llamadas (§9) `[CÓDIGO]`.
15. **¿Duplica modelos o recursos?** Sí: una sesión ORT de Silero y una de smart-turn por llamada, más 2 hilos por llamada (§4) `[CÓDIGO]`. Entre procesos replicados se duplicaría además todo el runtime `[INFERENCIA]`.
16. **Métricas para atribuir una rodilla**: TTFB, procesamiento y uso por procesador (`MetricsFrame`; `frame_processor.py:495-633`), latencia usuario→bot y trazas (con tracing). El heartbeat mide el tiempo de recorrido de un frame por el pipeline, pero sólo lo registra a nivel trace y está apagado por defecto (`worker.py:1598-1626`) `[CÓDIGO]`. **No mide el retraso del loop, la profundidad de colas ni el tiempo de despacho por salto** `[CÓDIGO]` (búsqueda de `loop lag` sin resultados). `[INFERENCIA]` El heartbeat es el análogo más cercano al `cycle_probe` de Golden, pero mide por pipeline y no por loop.
17. **Reutilización legal y técnica**: BSD-2 permite copiar y adaptar con atribución y aviso de licencia `[CÓDIGO]` (`LICENSE`). Técnicamente, las piezas de §13.3 son pequeñas y portables; el framework entero no lo es (API inestable, deprecaciones con retirada en 2.0.0) `[CÓDIGO]`/`[INFERENCIA]`.
18. **¿Adoptarlo exige reemplazar algo?** Adoptarlo como runtime exigiría reescribir el interior de GB como un pipeline de Pipecat y escribir un `FrameSerializer` para el protocolo estilo Deepgram que usa ai-engine. No exige cambiar Asterisk ni Modal. Para que el barge-in remoto funcione, **sí exige un cambio en ai-engine**: hoy descarta los JSON de GB y no tiene flush (BRIEF), y el patrón de Pipecat depende de que el extremo respete un `clear`. Adaptar sólo los patrones no exige reemplazar nada `[INFERENCIA]`.

### 13.2 Las dos preguntas específicas

**a) ¿Cuánto trabajo por trama corre en el loop por llamada, y comparten loop varios pipelines?**
Todo salvo la inferencia: decodificación y resampleo, ~6-7 saltos por colas de prioridad con sus cambios de tarea, fan-out a ~3 observers por salto, lógica de VAD y turno, `append_audio`, y la mitad asíncrona del viaje al executor de Silero (§7). Sí, los pipelines del proceso comparten loop (§4, §13.1-4). `[INFERENCIA]` En el régimen que limita a Golden (coordinación de un hilo a cadencia de trama), la arquitectura de Pipecat cae en la misma categoría y con más trabajo por trama. Por eso no ofrece un camino para subir K_loop.

**b) Invariantes de interrupción traducibles a `SpeechInterrupted`, `CancelSpeech` y `speech_id`**

| # | invariante en Pipecat (evidencia) | traducción para Golden |
|---|---|---|
| I1 | La interrupción es de **sistema**: adelanta a los datos, pero no corta el frame en curso si es no interrumpible (`frame_processor.py:131-182,1131-1158`) | En el MediaWorker, `CancelSpeech` y el barge-in local van por un **carril prioritario** de la sesión, no detrás de los `SpeakChunk` encolados |
| I2 | Purga selectiva por `interruptible`, conservando el orden de lo que sobrevive (`frame_queue.py:74-87`); `EndFrame`, `FunctionCallResultFrame` y los ajustes sobreviven | La cola C→M por sesión purga `SpeakChunk{speech_id}` y audio pendiente, pero **nunca** `CloseSession`, `ConfigureSession` ni `SendFunctionCall`. Clasificación declarada por tipo de comando y no por código ad hoc |
| I3 | Preempción por cancelación de la tarea en curso y recreación de la tarea y la cola (`frame_processor.py:1149-1150,1222-1238`) | La tarea de habla de Golden (LLM→frases→TTS→sender) se cancela y se recrea limpia. Añadir **timeout** a la espera de cancelación (Pipecat no lo tiene en la tarea de proceso, §8) |
| I4 | **Identidad de generación**: `context_id` en un registro que se descarta entero al interrumpir; lo que llega tarde con un id desconocido se tira; contextos cancelados no se recrean (`tts_service.py:1141-1181,1643-1653`; `cartesia/tts.py:723`) | `speech_id` = `context_id`. El MediaWorker guarda `live_speech_ids`; un `SpeakChunk` o un audio TTS con un `speech_id` fuera del registro se descarta y se cuenta. Un `speech_id` cancelado queda marcado como muerto (sin recreación) |
| I5 | Hook al proveedor por cada contexto vivo (`on_audio_context_interrupted`; Cartesia `cancel`) | Al cancelar `speech_id`, enviar a Ursula el cancel o cierre de ese contexto si el protocolo lo admite `[PENDIENTE]` (protocolo de Ursula no auditado) |
| I6 | Emisor: cancelar las tareas de audio y reloj, cola nueva, vaciar el buffer de chunks y **reiniciar el resampler**; emitir `BotStoppedSpeaking` (`base_output.py:602-629,829-858`) | `_PacedAudioSender` por `speech_id`: abortar, vaciar, reiniciar el estado de soxr/µ-law y emitir `PlaybackFinished{speech_id, interrupted:true}` |
| I7 | **Flush remoto** como traducción del `InterruptionFrame` en el serializer (`twilio.py:186-188`; `fastapi.py:508-514`) | Golden necesita un `clear{call/session}` hacia ai-engine. Hoy imposible: el engine descarta los JSON de GB (BRIEF). **Cambio obligado en ai-engine** si se quiere quitar la cola residual (≈0,6 s + 300 ms + arranque) |
| I8 | El texto hablado lo decide el consumidor aguas abajo del reproductor (reloj por pts; AssistantAgg con `interrupted=True`) (`base_output.py:417-418,1194-1215`; `llm_response_universal.py:1958-1960,2506-2526`) | `SpeechInterrupted{speech_id, spoken_text, played_ms}` lo **calcula Media**, que es quien tiene el cursor de reproducción. **Conversación** trunca su historial al recibirlo. Desaparece la mutación directa de `_cancel_active_tts` sobre el historial |
| I9 | La interrupción no lleva id: interrumpe "lo actual". Es seguro sólo porque todo es en proceso y con orden (`frames.py:1240-1249`) | **No copiar**: a través de red, `CancelSpeech` **debe** llevar `speech_id` (y `epoch`). Si `speech_id ≠ actual`, se ignora sin efectos. Conviene que `SpeechInterrupted` también lo lleve, para que Conversación descarte tokens o tools tardíos de ese `speech_id` |
| I10 | Granularidad de salida 40 ms: acota el audio local en vuelo (`base_transport.py:117`; `base_output.py:144-148`) | Golden ya acota el presupuesto en 0,6 s. **No adoptar** el envío al doble del tiempo real de Pipecat (`fastapi.py:456`): agrandaría el residuo remoto |

### 13.3 Piezas reutilizables (plantilla)

```text
Segmento: FrameProcessorQueue + FrameQueue.reset + _start_interruption
Responsabilidad principal: entregar control por delante de los datos y purgar lo interrumpible conservando lo que debe sobrevivir
Pattern principal: Priority Queue de tres niveles + Command con bandera de purga (selective flush) + preempción por cancelación
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] frame_processor.py:FrameProcessorQueue:131-182; _start_interruption:1131-1158; __reset_process_task:1231-1238; utils/frame_queue.py:reset:74-87; frames.py:Frame.interruptible:80-106; tests/test_frame_processor.py:125,159,379 (no ejecutados)
Callers verificados: FrameProcessor.process_frame:838-840; broadcast_interruption:1018-1023; MediaSender.handle_interruptions:619; TTSService._handle_interruption:1159
Contrato de entrada/salida: entrada (frame, dirección, callback). Salida: orden FIFO por nivel. Ante una interrupción, la cola conserva sólo los items con interruptible=False, en su orden
Política de error: excepción en el handler → push_error(UNKNOWN) y continuar; cancelación de la tarea de proceso sin timeout (riesgo)
Ownership: cada procesador es dueño de sus dos colas y sus dos tareas; nadie más las muta
Riesgo: colas sin cota; espera de cancelación sin timeout; coste por salto en el camino de 50 fps si se replica por etapa
Aplicación posible en Golden: UNA cola por sesión en el MediaWorker para comandos C→M (CancelSpeech/CloseSession con prioridad; SpeakChunk purgable por speech_id) y para el audio de salida; no replicarla por etapa DSP
```

```text
Segmento: Registro de contextos de audio TTS por generación (context_id)
Responsabilidad principal: atar todo el audio y los marcadores de una locución a su generación y descartar en bloque al interrumpir
Pattern principal: Generation/Epoch token + Registry con invalidación atómica + Template Method (on_audio_context_interrupted)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] tts_service.py:_handle_interruption:1141-1181; create_audio_context:1616-1624; append_to_audio_context:1626-1665; _create_audio_context_task:1784-1789; on_audio_context_interrupted:2035-2048; cartesia/tts.py:_process_messages:720-724, on_audio_context_interrupted:651-657; frames.py:TTSAudioRawFrame.context_id:276-285
Callers verificados: TTSService.process_frame:886-887 → _handle_interruption; receptores WS de proveedores (Cartesia) → audio_context_available/append_to_audio_context
Contrato de entrada/salida: entrada (context_id, frame) del sintetizador o el receptor. Salida: frames hacia abajo en orden de contexto. Un context_id que no está registrado se descarta. Al interrumpir: registro vacío, _turn_context_id=None y cancel al proveedor por contexto vivo
Política de error: si falla el hook de cancelación, se registra y continúa, para no impedir el reinicio de la tarea (1162-1167)
Ownership: TTSService de la llamada
Riesgo: recreación implícita de un contexto si coincide con _turn_context_id (1645-1650); requiere disciplina para no revivir una generación cancelada
Aplicación posible en Golden: speech_id como token de generación en el MediaWorker: live_speech_ids; descartar SpeakChunk o audio Ursula tardíos; marcar los cancelados como muertos; métrica de descartes por speech_id
```

```text
Segmento: BaseOutputTransport.MediaSender (interrupción del emisor de salida)
Responsabilidad principal: trocear, marcar y emitir el audio de salida y, al interrumpir, purgar sin dejar residuo en la siguiente locución
Pattern principal: Active Object por destino (tareas de audio y reloj) + reset de estado de DSP (resampler) + timeout de peer
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] base_output.py:handle_interruptions:602-629; handle_audio_frame:631-654; _buffer_audio/_take_audio_chunk:790-813; _bot_stopped_speaking:829-858; _audio_task_handler:1007-1038; _internal_write_audio_frame:1040-1068; tests/test_base_output_transport.py:149,163,203,404,521 (no ejecutados)
Callers verificados: BaseOutputTransport._handle_frame:393-420 ← process_frame:358-391; _bot_stopped_speaking ← handle_interruptions, TTSStoppedFrame, fallback por timeout
Contrato de entrada/salida: entrada OutputAudioRawFrame(s) a cualquier tasa. Salida: chunks de N×10 ms a la tasa del transporte, cada uno con la bandera interruptible de su audio; frames temporizados liberados por pts. Al interrumpir: cola nueva (o reset), reloj cancelado, buffer vacío, resampler reiniciado y BotStoppedSpeaking
Política de error: excepción al escribir → log y no se propaga hacia abajo; timeout de escritura → push_error(permanent) una vez y transporte inutilizable
Ownership: un MediaSender por transport_destination
Riesgo: la interrupción sólo alcanza al destino del frame (None por defecto); el ritmo de salida depende del transporte (WS: 2×)
Aplicación posible en Golden: rediseñar _PacedAudioSender con este contrato (reinicio del resampler, chunk con bandera, timeout de peer) manteniendo el presupuesto de 0,6 s y el envío a tiempo real de Golden
```

```text
Segmento: Historial escrito aguas abajo del reproductor (LLMAssistantAggregator detrás del Output)
Responsabilidad principal: que la conversación registre sólo lo que se reprodujo y marque la interrupción
Pattern principal: Observer/consumidor aguas abajo (event sourcing del habla reproducida)
Estado: implemented
Confianza: medium (la precisión de "hablado" depende de los pts del TTS frente a la reproducción real del cliente; el envío a 2× la degrada) [INFERENCIA]
Evidencia: [CÓDIGO] llm_response_universal.py:_handle_interruptions:1958-1960; _trigger_assistant_turn_stopped:2506-2526; base_output.py:handle_timed_frame:676-682, _clock_task_handler:1194-1215; tts_service.py:start_word_timestamps:1500-1515
Callers verificados: LLMAssistantAggregator.process_frame:1756-1757
Contrato de entrada/salida: entrada TTSTextFrame liberados por el reloj del Output más InterruptionFrame. Salida: AssistantTurnStoppedMessage{content, interrupted} y LLMContextAssistantTurnFrame
Política de error: ninguna específica
Ownership: agregador de asistente de la llamada (= capa de conversación)
Riesgo: alineación aproximada texto↔audio
Aplicación posible en Golden: base para SpeechInterrupted{speech_id, spoken_text, played_ms}: Media emite; Conversación trunca. Rompe el acoplamiento de _cancel_active_tts
```

```text
Segmento: WorkerBus + BusBridgeProcessor como canal Media↔Conversación
Responsabilidad principal: intercambiar frames y RPC entre workers locales o remotos
Pattern principal: Publish/Subscribe por difusión con filtros en el receptor
Estado: not recommended (para media; aceptable para traspasos de agente)
Confianza: high
Evidencia: [CÓDIGO] bus/bus.py:on_message_received:153-160, _router_task:169-187; bus/queue.py:18-61; bridge_processor.py:104-168,207-221; bus/network/redis.py:88-96; bus/messages.py:33-117
Callers verificados: PipelineWorker con bridged (worker.py:555-574); examples/multi-worker/distributed-handoff/redis-handoff/main.py:95-110
Contrato de entrada/salida: BusMessage{source, target?}; entrega a todos los suscriptores; prioridad sistema>datos; sin seq, epoch ni ACK
Política de error: una excepción del suscriptor se registra y el despacho sigue
Ownership: el runner es dueño del bus; cada suscriptor de su cola
Riesgo: audio por el bus salvo exclude_frames; reordenación sistema/datos; sin liveness de workers remotos; Redis pub/sub sin durabilidad
Aplicación posible en Golden: ninguna para la frontera Media–Conversación; el contrato de Golden (FIFO por sesión, dedup por (epoch, seq)) es estrictamente más fuerte
```

---

## 14. Riesgos de adopción o copia

- **Capacidad**: adoptar el runtime añadiría saltos por cola, fan-out a observers y sesiones ORT por llamada al camino de 50 fps, justo donde Golden está limitado `[INFERENCIA]`. Antes de adoptar, haría falta un número punta a punta: `[PENDIENTE]` medir `job`/`cycle` con Pipecat a 16-25 y 41+ sesiones en `.143`.
- **Inestabilidad de API**: migración `Task`→`Worker` en curso y decenas de deprecaciones con retirada en 2.0.0 `[CÓDIGO]` (`task.py`, `runner.py`, `frames.py:2116-2158`). Copiar código implica seguir un objetivo móvil; copiar **patrones** no.
- **3.14t**: soporte de free-threading no declarado; `audioop-lts` para ≥3.13 `[CÓDIGO]` (`pyproject.toml:25`). `[PENDIENTE]`
- **Semántica por destino**: una interrupción sin `transport_destination` sólo purga el emisor por defecto `[INFERENCIA]` (§8). Si se copia, decidir explícitamente el alcance (por sesión).
- **Cancelación sin timeout** en la tarea de proceso (§8): copiar añadiendo un timeout y una métrica.
- **Ritmo 2×** del transporte WS (§7): no copiar.
- **Licencia**: BSD-2-Clause exige conservar el aviso de copyright y la lista de condiciones en código fuente y en binarios redistribuidos. Bajo riesgo `[CÓDIGO]` (`LICENSE`).
- **ai-engine**: el patrón de flush remoto (I7) no funciona sin cambiar el dispatcher del engine, que hoy descarta los JSON de GB (BRIEF). Es un cambio de alto impacto fuera de GB.

---

## 15. Lecciones concretas que pasan a la matriz comparativa

1. **Pipecat replica; no elimina ni paraleliza el coordinador por trama.** Un loop por proceso con todos los pipelines dentro; el trabajo por trama en el loop es mayor que el de Golden; el propio repo aísla sesiones en subprocesos cuando necesita ritmo estable (`evals/_session_subprocess.py:7-12`) `[CÓDIGO]`/`[DOCUMENTACIÓN]`.
2. **La purga correcta es selectiva y por bandera, no "vaciar todo"**: `FrameQueue.reset` conserva los terminales y los resultados de tools en orden (`frame_queue.py:74-87`) `[CÓDIGO]`. Golden debe clasificar sus comandos C→M de la misma forma.
3. **`speech_id` como registro de generaciones vivas que se invalida de golpe**, más el cancel al proveedor y el descarte de tardíos por id desconocido (`tts_service.py:1141-1181,1643-1653`; `cartesia/tts.py:723`) `[CÓDIGO]`.
4. **Un barge-in completo exige un flush remoto**: Pipecat lo consigue traduciendo la interrupción en el serializer (`twilio.py:186-188`) `[CÓDIGO]`. Golden no puede igualarlo sin cambiar ai-engine.
5. **El texto hablado lo decide un consumidor aguas abajo del reproductor**, no el reproductor escribiendo el historial (`llm_response_universal.py:1958-1960,2506-2526`) `[CÓDIGO]` → `SpeechInterrupted{spoken_text}` emitido por Media y aplicado por Conversación.
6. **Interrupción sin id = sólo válida en proceso.** A través de red, `CancelSpeech` debe llevar `speech_id`/`epoch` e ignorarse si está obsoleto (`frames.py:1240-1249`) `[CÓDIGO]`/`[INFERENCIA]`.
7. **Al interrumpir, reiniciar el estado DSP de salida** (resampler y buffer de chunks) para no dejar residuo en la siguiente locución (`base_output.py:829-858`; test `:521`) `[CÓDIGO]`.
8. **Observabilidad**: el heartbeat por pipeline es un buen primitivo (sonda en banda que mide el recorrido), pero Pipecat no mide el retraso del loop ni la profundidad de colas (`worker.py:1598-1626`) `[CÓDIGO]`. Golden ya tiene `cycle_probe`; conviene añadir la profundidad por cola de sesión.

---

*No se actualizó `repo_analisis/README.md` para no pisar el trabajo concurrente de otros auditores; su fila para Pipecat sería: descargado @ `7d3bd9f` · informe `informes/07_pipecat.md` completo.*
