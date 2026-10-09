# Dictamen — frontera Media–Conversación en GoldenBridge

Fecha: 2026-09-28 · Arquitecto/auditor: Claude (Fable 5) · Encargo: viabilidad de separar GoldenBridge en un proceso **Media** y un proceso **Conversación** con dueño único por estado, barge-in local y aislamiento completo entre llamadas. No se implementó ningún cambio.

**Base verificada**: GoldenBridge rama `api` @ `08932a64645bff74030ad2bd63543dd2334b8101` (checkout en `feat/coalescer-integrity-probe`, mismo commit, cambios locales sólo de test; evidencia por `git show`/`git grep`). ai-engine (`Asterisk-AI-Voice-Agent-FX`) rama `feat/goldenbridge-modal-coalescer-60ms` @ `d059295d2468486bc6f8d2c883a6e41599d695f9` (checkout limpio en ese commit). Documentos: `ARQUITECTURA_STACK.md`, auditoría de refactor `08932a6`, dictamen multiproceso 2026-09-28.

Nota de alcance: la regla de trabajo 1 de CLAUDE.md marcaba este split como no-reabrir; este encargo lo reabre explícitamente, así que se evalúa como decisión del dueño del proyecto. El dictamen multiproceso se usa sólo como contraste (§10).

Etiquetas: `[CÓDIGO]` · `[MEDICIÓN]` · `[DOCUMENTACIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.
Coordenadas: `WS@L` = GoldenBridge `src/server/websocket_server.py` · `SMART`/`VAD`/`STENO`/`TTS`/`SM` como en la auditoría · `DG` = ai-engine `src/providers/deepgram.py` · `ENG` = `src/engine.py` · `SPM` = `src/core/streaming_playback_manager.py` · `AS` = `src/audio/audiosocket_server.py` · `CFGE` = `config/ai-agent.yaml` del engine.

---

## 1. Dictamen

**Aceptar la frontera, con tres ajustes obligatorios y una advertencia de valor.**

La frontera Media–Conversación es técnicamente coherente con el código actual: el reparto de responsabilidades propuesto coincide casi uno a uno con bloques que ya existen y ya están (o pueden estar) bajo dueño único, y **ningún audio necesita cruzar el IPC** — entre Media y Conversación sólo viajan texto y eventos, lo que elimina de raíz la clase de riesgo más grave (mezclar audio entre llamadas). El barge-in puede quedar íntegramente local a Media: los diez pasos de la secuencia obligatoria ya existen como símbolos, ocho de ellos ya son locales al camino de media, y los dos restantes (historial y cancelación del LLM) se convierten en eventos ordenados (§6).

Los tres ajustes sin los cuales la frontera no funciona:

1. **La escritura de historial en el cancel** (`_cancel_active_tts` → `_write_interrupted_history`, WS@L8525) se convierte en el evento `SpeechInterrupted` sobre un canal por-sesión FIFO, y Conversación procesa eventos de una sesión **en serie**: así el historial se actualiza *antes* de que el `TurnCompleted` del turno interruptor dispare el siguiente LLM, que es la garantía que hoy da el orden síncrono dentro del receive-loop `[CÓDIGO]`.
2. **El entrelazado LLM→TTS por frase** (`_process_llm_tts_streaming`, WS@L4830) se convierte en un stream de comandos `SpeakChunk`: Conversación arma frases desde los tokens; Media sintetiza y pacea. Es la costura de mayor esfuerzo (cinco variantes de habla, auditoría §8.C).
3. **La identidad de turno y de habla se vuelve explícita**: `turn_id` lo acuña Media al cierre comprometido del turno (hoy `turn_seq++` en WS@L4005/L4255 `[CÓDIGO]`), `speech_id` lo acuña Conversación, y Media rechaza cualquier `Speak` con turno viejo — generalización del guard de generación que ya existe (WS@L7189/L7255 `[CÓDIGO]`).

**Advertencia de valor** `[MEDICIÓN]`+`[PENDIENTE]`: el cuello medido es el camino de audio (submit/resume del DSP a cadencia de tramas), y ese camino queda entero en Media. `steno_p90 = 0,10 ms` plano en todas las bandas ⇒ la orquestación STT/LLM/TTS que se muda a Conversación casi no pesa en el loop hoy. La ganancia directa de capacidad por loop es **no demostrada**; las ganancias defendibles son de otra clase: jitter de percentiles altos fuera del loop de audio, aislamiento de fallos y despliegue de Conversación sin tocar el audio, memoria (Conversación no carga torch/ORT) y la opción estratégica de escalar Conversación aparte. Por la regla 3 de CLAUDE.md, el split sólo se adopta si la fase de medición (F6) muestra un número punta a punta; la ausencia de K no bloquea el diseño, y este documento lo entrega completo.

**Respuesta a la pregunta final**: sí, GoldenBridge puede separarse en Media y Conversación manteniendo dueño único por estado, barge-in local y aislamiento entre llamadas. El contrato mínimo es el de §5: identidad `(session_id, session_epoch, turn_id, speech_id, seq)`, ocho eventos M→C, seis comandos C→M, orden FIFO por sesión en ambas direcciones, dedup por `(epoch, seq)`, sin exactly-once, sin audio en el IPC.

---

## 2. Flujo actual reconstruido (símbolos, estado, tareas, locks, efectos)

Los ocho tramos pedidos, contra `api` `[CÓDIGO]`. "Loop" = event loop único del proceso; "task" = tarea asyncio distinta del receive-path.

| # | Tramo | Símbolos y callers | Estado leído/escrito | Tarea/loop | Locks/barreras | Efectos externos | Depende de SessionState |
|---|---|---|---|---|---|---|---|
| 1 | Audio entrante → VAD/SmartTurn | `_session_reader` L2520 → `inbox` → `_session_processor` L2595 → planificador L2719-2968 → `_audio_pre_vad` L3569 → `vad.process_audio[_batch]` (SMART L1046/L1159, pool `vad`) | escribe relojes de llegada, `audio_buffer`, `_batch_*`, `_frames_in_flight`; DSP muta soxr/LSTM/`turn_audio` por sesión | reader task + processor (receive-path); job en hilo del pool | `_state_lock` (RLock) sostenido todo el lote (SMART L1140); `_batch_idle` | envío en vivo a Steno dentro del post | inbox, carry, buffers, vad_processor, stt_connection |
| 2 | Inicio de habla | `_audio_post_vad` L3718-3775 | `is_speaking=True`, `speech_start_time`, reset ventana diagnósticos, `last_user_activity` | receive-path | — | `UserStartedSpeaking` al cliente; flush de pre-contexto a Steno; `sm._reset_timeout_timer()` L3738 | is_speaking, pre_context_buffer, campaign |
| 3 | Fin de turno → Finalize | `_resolve_turn_complete` L3924 (guard `is_speaking` antes del primer await L3938) ← trama L3897 y watchdog L8982; `_process_speech_streaming` L3978: `turn_seq++` L4005, `send_finalize` (STENO L627, `finalize_lock`, 3 s), fallback `transcribe`, merge de fragmento | `turn_seq`, `audio_buffer` (vacía), `_finalize_inflight`, `_stt_finalize_failures` | receive-path (o watchdog task vía el mismo método) | `finalize_lock` del STENO | `UserStoppedSpeaking`; Finalize/transcribe por red; `discard_pending` | turn_seq, banderas de Finalize/fragmento |
| 4 | Transcripción → campaña/LLM | `_process_transcript` L4443: absorber terminal, gracia post-TTS, AMD, history.append(user), `sm.process_user_message`, opener, router→`sm.execute_transition`, `create_task(_process_llm_tts_streaming)` L4781 | `conversation_history`, `variables`, `was_interrupted` (consume), `first_transcript_time`, `amd_*` | receive-path; el LLM+TTS nace como task | `tts_task_lock` al registrar | router HTTP; `ConversationText(user)`, `AgentThinking` | history, campaign, variables, active_tts_task |
| 5 | Texto → TTS → salida | `_process_llm_tts_streaming` L4830: `_reserve_agent_speech` L4859, regex de frase por token L5264, `tts_processor.synthesize` (semáforo 4, TTS L769) → `_PacedAudioSender.push` → `_safe_send_bytes` L9256 (budget + `websocket_send_lock`); variantes: `_speak_response` L7216, filler L8012, greeting caché L1832, error L5910 | bloque TTS de 20 campos; history (assistant) al final | task de habla + task interna del sender | barrera FIX-E (`_batch_idle`); `tts_task_lock`; send lock | audio binario al cliente; `AgentStartedSpeaking`/`AgentAudioDone`/`ConversationText` | todos los campos TTS, outbound_budget |
| 6 | Barge-in → corte → recuperación | detección: dict del DSP `barge_in_ready` L3838-3893 → `_cancel_active_tts` L8445: buffer clear L8477, snapshot+clear `turn_audio` bajo lock del VAD L8493-8511, `tts_cancelled`+`cancellation_event` L8514-8515, `sender.abort()` L8524, `_write_interrupted_history` L8525, task.cancel + espera 0,2 s L8545-8550, reanuda STT L8600, `_flush_turn_audio_to_stt` L8609, `was_interrupted=True` L8617 | history (¡escritura directa!), turn_audio, banderas TTS | receive-path (síncrono hasta el flush) | `tts_task_lock`; `_vad_state_lock` | flush WS de TTS (no-op por defecto, TTS L583); cancel remoto fire-and-forget | history, active_speech, vad_processor, stt_connection |
| 7 | FunctionCall y respuesta | LLM: `_dispatch_tool_call_batch` L6311 → `_handle_function_call` L6491 (valida, par en history, `FunctionCallRequest`); SM: `execute_function` L9416 (sin validar, `sm_pending_function_ids`); respuesta: `_handle_function_response` L3489 (follow-up LLM en línea si no es SM) | history (8 sitios del par), sm_pending | receive-path (respuesta) / task de habla (emisión LLM) | `_sessions_lock` en `execute_function` | `FunctionCallRequest` al cliente | history, sm_pending_function_ids, functions |
| 8 | Hangup/transferencia/cleanup | terminal: `_run_terminal_on_exit` L6043 (en el finally del TTS) → `sm.execute_terminal_on_exit` → `execute_function("hangup_call"/transfer)`; `_release_worker_sessions` L6094 (idempotente); teardown `finally` L2297-2515; reaper L1390/`_evict_session` L1424 | `_terminal_on_exit_scheduled`, `_worker_sessions_released`, todo el teardown | finally del task de habla + handler + reaper | `_sessions_lock`; shield en reaper | cierra Steno/Ursula; `gc.collect` (L1357) **en el hilo del loop** | campaign, conexiones, tareas |

Del lado ai-engine, el mismo flujo continúa así `[CÓDIGO]`: audio del caller entra por AudioSocket (320 B/20 ms slin), el engine lo convierte y **coalesce a 480 B/60 ms** hacia GB (`_send_or_coalesce_provider_audio` ENG:5689-5845; flush parcial por stall de 120 ms; sin flush por fin de habla); el TTS de GB llega como bytes sin etiqueta → cola por llamada de 256 mensajes (ENG:8648, descarta en lleno ENG:8726) → jitter de 15×20 ms (SPM:483-488) → pacer absoluto de 20 ms → AudioSocket (`writer.write+drain`, AS:263-267, TCP_NODELAY). `FunctionCallRequest` lo ejecuta el propio proveedor (DG:1440-1454) contra el registro de tools (transfer = ARI `continue`, hangup = farewell + `HangupReady` → `hangup_channel` tras 2,5 s).

---

## 3. Evidencia por responsabilidad propuesta

**Media (todas verificadas como bloques existentes)** `[CÓDIGO]`: WebSocket y FIFO (§2.1-2 de la auditoría; L2520-2677) · audio de entrada/salida y buffers/carry/batching (L2719-2968) · decodificación/resampleo y estado Silero/SmartTurn por sesión (SMART/VAD; clones con locks propios, `test_aislamiento_entre_sesiones.py`) · inicio/fin de habla (L3718, L3924) · barge-in local (L3838-3893, L8445) · STT streaming + Finalize + cabeza de interrupción (L3780-3834, L4049, L8383-8443, L8609) · conexión TTS y recepción del audio (TTS L738-789; adopción L4862/L7347) · pacer, presupuesto y serialización (`_TTSPacer` L366, `_OutboundAudioBudget` L411, `_PacedAudioSender` L584, `_safe_send_bytes` L9256) · generación activa y descarte de resultados viejos (`turn_seq`/`_speech_gen` L7189/L7255; `_reserve_agent_speech` L2679) · cleanup de media (`finally` L2297-2456, `_release_worker_sessions` L6094).

**Conversación** `[CÓDIGO]`: historial (24 escritores; los de política en L4541, L5534, L5847, L6542-6556, L6939-6956, L9468) · máquina de estados (SM L79-85, ejecutor inyectado L116) · variables/extracción (L4704-4718, SM `handle_extract_data`) · interpretación del transcript (L4443) · router (L4649) · LLM (llm_processor; generador asíncrono cancelable con `partial_response` `[CÓDIGO]`) · tool calls y correlación (L6311-6558, L3489-3567, L9416-9492) · transferencia/hangup/terminales (L6043-6092; transfer sin ruta especial, §8.D de la auditoría) · decisión del texto (atajos estáticos L5056-5142, strict L5599-5655, breaker L5556-5597, follow-up L7024) · actualización del historial según lo reproducido (hoy la hace Media en L8283-8337 con el ledger del sender — **la excepción que la frontera corrige**).

**Cruces actuales que violan la frontera y deben romperse** `[CÓDIGO]`:

| cruce | símbolo | dirección hoy | solución |
|---|---|---|---|
| Cancel escribe historial | L8525 → `_write_interrupted_history` L8283 | Media muta estado de Conversación | evento `SpeechInterrupted{spoken_text}` (§6 paso 9) |
| Cancel mata al LLM | `task_to_cancel.cancel()` L8545 cancela LLM+TTS juntos | Media cancela trabajo de Conversación | Media aborta audio; Conversación cancela su stream al recibir el evento; `SpeechRejected` corta chunks tardíos |
| `was_interrupted` | escrito L8617, consumido L4525-4532, desarmado L4132 | bandera compartida | viaja en `TurnCompleted.was_interrupted` (derivada del `SpeechInterrupted` previo) |
| Timeout de estado | `sm._reset_timeout_timer` desde L3738/L6035/L7596; timeout → `inject_prompt` L9494 → habla directa | Conversación toca ciclo de media y Media dispara timers de campaña | timers conversacionales en Conversación, alimentados por `SpeechStarted`/`PlaybackStarted`/`PlaybackFinished`; el prompt de timeout se vuelve `Speak` |
| Texto→TTS entrelazado | L5202-5457 | un task mezcla LLM (C) y síntesis (M) | `Speak`+`SpeakChunk` en streaming |
| Overrides de VAD de campaña | `_handle_settings` L3308-3311 aplica `apply_overrides` | Conversación (campaña) configura Media | comando `ConfigureSession{vad_overrides, barge_in_enabled}` una vez por sesión |
| Terminal en el finally del TTS | L6041/L7602 | Media ejecuta política terminal | `PlaybackFinished{outcome}` → Conversación corre on_exit y emite `SendFunctionCall`/`CloseSession` |
| Re-prompt sordo | L4101-4126 habla desde el camino STT | Media decide texto | `TurnFailed` → Conversación decide el re-prompt |

---

## 4. Mapa definitivo de ownership

Regla base: **SessionState no se comparte**. Se parte en `MediaSessionState` y `ConvSessionState`; lo que uno necesita del otro deja de ser campo y pasa a ser mensaje.

| Estado/recurso | Dueño actual (símbolo) | Dueño propuesto | ¿Cruza IPC? | Regla de consistencia | Riesgo |
|---|---|---|---|---|---|
| WebSocket con ai-engine | handler + `session.websocket` L1013/L2163 | **Media** | nunca (sólo Media lo toca) | un escritor serializado (`websocket_send_lock` L936) | bajo |
| inbox y carry | L1039, L1061 | **Media** | nunca | orden carry→pendiente→inbox (L2611-2620) | bajo |
| buffers y resamplers (`audio_buffer`, `pre_context_buffer`, soxr) | SessionState + procesadores | **Media** | nunca | no idempotentes; vaciados sólo por Media | bajo |
| VAD/SmartTurn (estado + locks) | `session.vad_processor` | **Media** | nunca | clones por sesión; modelo ORT compartido sólo como recurso (SMART L143-151) | bajo |
| Conexión STT + resultados pendientes | `stt_connection` (STENO) | **Media** | nunca; el transcript cruza como `TurnCompleted` | `finalize_lock` single-flight; purga sólo con transcript (L4155) | medio (contrato Steno↔Media queda igual) |
| Conexión TTS | `tts_connection`, `_tts_connect_task` | **Media** | nunca; el texto cruza como `SpeakChunk` | adopción lazy queda en Media | medio (costura §5) |
| Pacer y presupuesto | `_TTSPacer`/`_OutboundAudioBudget`/sender | **Media** | nunca | presupuesto único por sesión; un sender vivo por speech | bajo |
| `turn_seq` → `turn_id` | L891; ++ en L4005/L4255 | **Media acuña**, Conversación referencia | sí (en `TurnCompleted` y `Speak`) | monótono por (sesión, epoch); Media rechaza `Speak` con turno viejo (= L7255) | bajo |
| Generación activa de habla | `active_tts_task`, `active_speech`, `cancellation_event`, `_tts_starting_at` | **Media** (`speech_id` acuñado por C) | sí (ids en Speak/Cancel/eventos) | un speech activo por sesión; estados: pending→speaking→done/interrupted/rejected | medio |
| Historial | `conversation_history` (24 sitios) | **Conversación** | sí (spoken_text en eventos) | sólo C escribe; `SpeechInterrupted` se procesa antes que el `TurnCompleted` siguiente (FIFO) | **alto** — es el ajuste 1 |
| Campaña (SM completo) | `session.campaign.state_machine` | **Conversación** | no (sus acciones salen como comandos) | on_enter/on_exit y `_terminal_on_exit_executed` quedan en C | medio |
| FunctionCall pendientes | `sm_pending_function_ids` L962 | **Conversación** | sí (`SendFunctionCall`→ y `ClientFunctionResponse`←) | correlación por `fn_call_id`; timeout nuevo en C (hoy no existe `[CÓDIGO]`) | medio |
| Timers conversacionales (silencio, no-interacción, timeout de estado, gracia terminal) | tasks en SessionState + SM | **Conversación** | alimentados por eventos | pausados por `PlaybackStarted/Finished` (hoy: `tts_audio_playing`) | medio |
| Timers técnicos de media (watchdog de cierre, reaper idle/no-audio) | L8918, L1390 | **Media** | `SessionClosed` al evictar | watchdog sigue leyendo `_frames_in_flight`/carry local | bajo |
| Registry de sesiones | `self.sessions` + `_sessions_lock` | **Media** (por proceso Media); C mantiene su vista por eventos | implícito (Opened/Closed) | C descarta estado al recibir `SessionClosed` o al caer el epoch | bajo |
| Cleanup | `finally` L2297 + `_release_worker_sessions` | **Media** (recursos media) y **C** (su estado) | `SessionClosed{stats}` | idempotencia por lado; C nunca cierra sockets de media | medio |
| Cachés greeting/filler/script (audio) | servidor L1288-1301 | **Media** (son bytes de audio) | `Speak{cacheable}` da la pista | clave = texto+formato+estilo (L7424-7433) | bajo |
| AMD | `amd_detector` + `amd_*` L1079-1082 | **Conversación** (clasifica transcript) | no | ventana por `connected_at` (viaja en `SessionOpened`) | bajo |

**Campos que desaparecen de cada lado**: `MediaSessionState` pierde `conversation_history`, `campaign`, `variables`, `system_prompt`, `functions`, `sm_pending_function_ids`, `amd_*`, `was_interrupted`, `initial_static_text_fired`, `_inject_prompt_active`; conserva todo el bloque de audio/TTS/STT/batching más `turn_id` y el `speech` activo. `ConvSessionState` nace con historial, campaña, variables, pendientes, timers, `last: {turn_id, speech_id}`; no contiene ni un byte de audio, ni conexiones, ni tareas de media. **Se convierten en mensajes**: transcript, texto a sintetizar, texto reproducido/interrumpido, ids de turno/habla, FunctionCall y su respuesta, overrides de configuración, y las marcas de reproducción que gobiernan timers.

---

## 5. Contrato Media–Conversación (mínimo, versionado)

**Principios** `[INFERENCIA sobre CÓDIGO]`: (a) **cero audio en el IPC** — sólo texto y eventos; (b) un canal duplex **por sesión** (o multiplexado con clave de sesión), FIFO en cada dirección; (c) at-least-once + dedup, nunca exactly-once; (d) todo mensaje se valida contra `(session_id, session_epoch)` y, cuando aplica, `(turn_id, speech_id)`; (e) el envelope lleva `v` (versión de protocolo) y ambos lados rechazan mayor-desconocida.

**Envelope**: `{v, session_id, session_epoch, seq, event_id, ts_mono, type, body}`. `seq` monótono por (sesión, dirección); receptor guarda `last_seq`: menor o igual ⇒ duplicado (descarta), salto ⇒ pérdida ⇒ la sesión se cierra con error (resincronizar una llamada de voz a mitad no tiene semántica útil). `session_epoch` lo acuña Media al crear la sesión y cambia si Media renace: un id reutilizado tras reconexión no puede aceptar mensajes del epoch anterior.

### Eventos Media → Conversación

| evento | body | notas de contrato |
|---|---|---|
| `SessionOpened` | settings crudos del cliente (incl. `campaign`, `variables` — hoy DG:251-252 los manda top-level `[CÓDIGO]`), formatos, `connected_at` | 1 por epoch; C responde `ConfigureSession` antes de que Media procese audio dependiente de campaña (Media bufferea como hoy espera el connect de STT) |
| `SpeechStarted` | `turn_hint_ts` | informativo (timers de C); sin ACK; duplicado inocuo |
| `TurnCompleted` | `turn_id`, `transcript`, `origin: finalize\|fallback\|auto_flush\|watchdog`, `was_interrupted`, `stt_latency_ms` | **el** disparador del LLM; exactamente uno por `turn_id`; Media garantiza que cualquier `SpeechInterrupted` del habla previa va antes en el canal |
| `TurnFailed` | `turn_id`, `audio_bytes`, `consecutive_failures` | C decide re-prompt sordo (hoy L4101-4126) |
| `SpeechInterrupted` | `speech_id`, `reason: barge-in\|new-turn\|ws_lost`, `spoken_text` (del ledger, L512/L8297), `synthesized_text` | C escribe el historial interrumpido y cancela su stream LLM de ese speech; idempotente por `speech_id` |
| `PlaybackStarted` | `speech_id`, `ttfab_ms` | pausa timers de C (hoy `_cancel_timeout_timer` L4909/L7307) |
| `PlaybackFinished` | `speech_id`, `outcome: natural\|cancelled\|error`, `played_text` | reanuda timers; si C tenía terminal pendiente → corre on_exit (hoy L6041) |
| `ClientFunctionResponse` | `fn_call_id`, `name`, `content` | passthrough del WS (hoy L3489) |
| `ClientInject` | `content` | passthrough de `InjectAgentMessage`; C decide (normalmente re-emite `Speak`) |
| `MediaError` / `SessionClosed` | razón, stats de cierre (los de L2492-2508) | `SessionClosed` es terminal: C libera su estado |

### Comandos Conversación → Media

| comando | body | semántica |
|---|---|---|
| `ConfigureSession` | `vad_overrides` (los `_parsed_processor_overrides` de hoy, L3308), `barge_in_enabled`, `greeting_fallback_text`, `farewell_on_orphan_text` | 1 por epoch; ACK implícito: Media no abre el gate de campaña hasta aplicarlo |
| `Speak` | `speech_id`, `in_reply_to_turn_id \| origin: greeting\|timeout\|silence\|followup`, `style`, `cacheable` | abre generación; Media valida turno vigente; si viejo → `SpeechRejected{speech_id, current_turn_id}` (equivale al abort de L7255) |
| `SpeakChunk` | `speech_id`, `chunk_seq`, `text`, `final: bool` | streaming por frase; Media sintetiza/pacea; tras `SpeechInterrupted` o `SpeechRejected`, chunks del mismo `speech_id` se descartan en silencio |
| `CancelSpeech` | `speech_id \| all`, `reason` | idempotente; usado por C en cambios de política (hoy `_handle_new_user_turn_tts` es de Media y no necesita esto) |
| `SendFunctionCall` | `fn_call_id`, `name`, `args_json` | Media lo serializa al WS (hoy L9455-9482); **sin ACK de entrega** — igual que hoy (`_send_message` no propaga el fallo, L9370 `[CÓDIGO]`); la confirmación de negocio es `ClientFunctionResponse`; timeout y reintento son política de C |
| `CloseSession` | `reason`, `after_playback: bool` | Media cierra el WS del cliente tras drenar (o ya) y emite `SessionClosed` |

**Reglas transversales**: mensaje tardío = válido si epoch y seq cuadran pero turno/speech viejos ⇒ respuesta explícita (`SpeechRejected`) o descarte contabilizado en métrica, nunca error fatal. **Backpressure**: colas acotadas por sesión (p. ej. 64 eventos / 256 chunks); productor bloquea **sólo esa sesión**; si C no drena eventos en `T_ev` (p. ej. 5 s) Media lo trata como C-caído para esa sesión. **Pérdida del IPC**: Media entra en modo huérfano — deja de aceptar `Speak`, mantiene el audio y los guardias técnicos, habla el `farewell_on_orphan_text` configurado y cuelga tras una gracia (misma filosofía que `_farewell_and_hangup` L8840); Conversación, al perder a Media, descarta todas las sesiones de ese epoch (los `session_id` son uuid4 por conexión, L2132, así que no hay reutilización que confundir `[CÓDIGO]`). Timeouts concretos por mensaje: sólo dos hacen falta — `ConfigureSession` (si no llega en 3 s tras `SessionOpened`, Media rechaza la llamada con 1013, espejo del guard de capacidad L2135) y el `T_ev` de drenaje; el resto vive del flujo.

---

## 6. Secuencia de barge-in auditada (los 10 pasos contra el código)

| paso | símbolo actual `[CÓDIGO]` | ¿ya es local a Media? | dependencia a romper |
|---|---|---|---|
| 1. Detecta `barge_in_ready` | resultado del DSP (SMART; gates `turn_met_barge_in_gates`) leído en L3838-3893 | **sí** | — |
| 2. Invalida la generación activa localmente | `tts_cancelled=True` + `cancellation_event.set()` L8513-8515 (sticky en el sender L776-778) | **sí** | — |
| 3. Aborta el pacer sin esperar a Conversación | `speech.sender.abort()` L8524 (síncrono, pre-lock-release) | **sí** | — |
| 4. Conserva y limpia `turn_audio` | snapshot→clear→desarme bajo `_vad_state_lock` L8493-8511 | **sí** | — |
| 5. Reanuda STT | `streaming_paused=False` L8600 | **sí** | — |
| 6. Entrega la cabeza a STT | `_flush_turn_audio_to_stt` L8609 (resampleo one-shot, antes del Finalize por orden del receive-loop) | **sí** | — |
| 7. Publica `SpeechInterrupted` | **no existe**: hoy es la mutación directa `_write_interrupted_history` L8525 + `was_interrupted` L8617 | nuevo | crear el evento con `spoken_text` calculado del ledger (L512) — Media tiene texto y bytes |
| 8. Conversación cancela LLM/pendiente | hoy `task.cancel()` L8545 mata LLM+TTS juntos | mixto | C cancela su generador al recibir el evento; mientras tanto Media ya descarta `SpeakChunk` del speech interrumpido (los tokens sobrantes se pierden inofensivamente) |
| 9. Conversación actualiza historial | `_write_interrupted_history` L8283-8337 (variantes "solo dijiste"/"no escuchó") | pasa a C | **orden garantizado por el canal**: `SpeechInterrupted` precede en FIFO al `TurnCompleted` del turno interruptor (Media emite el evento en el paso 7, y el Finalize del nuevo turno ocurre después en el mismo receive-path, L8609 → L3974 → L4178); C procesa en serie por sesión ⇒ el siguiente LLM siempre ve el historial actualizado — la misma garantía que hoy da la ejecución síncrona `[CÓDIGO]` |
| 10. El siguiente turno sólo habla con generación nueva | `turn_seq++` L4005/L4255; `_speech_gen` L7189; abort L7255 | ya existe | se vuelve explícito: `TurnCompleted{turn_id}` y `Speak{in_reply_to_turn_id}`; Media rechaza turnos viejos |

Nota sobre `_cancel_active_tts` completo: los pasos 2-6 y el desarme de flags (L8583-8601) quedan en Media tal cual; sólo salen de él la escritura de historial (→ evento) y la cancelación del task combinado (→ dos cancelaciones, una por lado). El reset temprano de `tts_cancelled` a los 0,2 s (hallazgo H4 de la auditoría) deja de importar: el `outcome` del habla viaja en el evento, no en una bandera compartida `[INFERENCIA]`.

---

## 7. Aislamiento entre llamadas

**Ya cumplido por el código** `[CÓDIGO]`: llamada fijada a su proceso dueño (todo cuelga del handler del WS; sin rutas externas por llamada — auditoría §2); buffers/VAD/LSTM/resamplers/locks/conexiones por sesión (clones SMART L506-594, VAD L189-231; verificado por `test_aislamiento_entre_sesiones.py` y `test_vad_executor.py` L230); modelos ONNX compartidos sólo como recurso de inferencia con estado temporal por sesión (SMART L143-151); un solo escritor de bytes por sesión (`websocket_send_lock`); descarte de habla obsoleta por generación (L7255); `session_id` uuid4 por conexión (L2132) ⇒ sin reutilización dentro de GB; sin cola global de audio (colas por sesión).

**Nuevo, exigido por el contrato**: identidad + `seq` en todo mensaje interno (hoy no existe porque todo son referencias en memoria — que es precisamente por lo que hoy nada se mezcla); validación `(session_id, epoch, turn_id, speech_id)` en ambos extremos con métrica de rechazos; canal por sesión (o multiplexado con clave obligatoria) para que un mensaje tardío jamás pueda aterrizar en otra llamada ni en otro turno; `session_epoch` para que un id que reaparezca tras reinicio de Media no acepte mensajes del pasado. Regla de diseño explícita: **prohibida** cualquier cola compartida sin clave de sesión y secuencia — y como el audio no cruza el IPC, la clase "PCM de la llamada A suena en la B" sólo puede originarse dentro de Media, donde los mecanismos actuales ya la cubren y las pruebas de mezclas existentes la vigilan (`tools/loadtest/verifica_mezclas.py` + `firmas.json`, T-D1c `[CÓDIGO]`).

---

## 8. Relación con ai-engine (límite Golden → engine)

Verificado sobre `d059295` `[CÓDIGO]`:

- **El audio binario hacia ai-engine no lleva generación** — confirmado en ambos lados: GB envía bytes crudos (`_safe_send_bytes`) y el engine encola `{data, streaming_chunk, encoding, sample_rate}` sin id (DG:1732-1761; colas de bytes ENG:8724, SPM:849). Un chunk tardío de un habla cancelada es indistinguible.
- **`UserStartedSpeaking` NO produce ninguna limpieza**: el proveedor sólo lo loguea (DG:1424-1429) y el dispatcher del engine descarta **todo** JSON crudo de GB por carecer de `call_id` (ENG:8201-8203). Efecto lateral único: cualquier JSON a mitad de ráfaga sintetiza un `AgentAudioDone` de frontera (DG:1586-1593) que cierra el segmento **sin purgar nada**.
- **`AgentAudioDone` marca límite, no purga**: en modo continuo sólo `mark_segment_boundary` + fin de gating (ENG:8746-8757); el audio encolado se reproduce entero (drenaje esperado explícitamente, SPM:947-962). Con `cleanup_after_tts` dispara el hangup diferido 2,5 s (ENG:8891-8920).
- **Barge-in local del engine**: existe como fallback flush-only (`_maybe_provider_barge_in_fallback` ENG:7052-7183 → `_apply_barge_in_action` ENG:7252-7346, con ventana de supresión de salida de 1.200 ms) y deepgram está en su allowlist por defecto (CFG engine 409), **pero el yaml del repo lo apaga** (`barge_in.enabled: false`, CFGE:11; VAD enhanced también off). Nunca notifica a GB. Config efectiva en `.143` `[PENDIENTE]`.
- **Los bytes ya enviados no se retiran**: el engine escribe tramas TLV de 20 ms y no existe ningún flush hacia Asterisk (AS:263-267; TYPE_TERMINATE sólo se parsea entrante, AS:230).
- **Reconexión**: si el WS a GB cae, el proveedor **no reconecta** — la llamada sigue contra un socket muerto hasta el hangup (DG:1762-1804; nada resetea `provider_session_active`, ENG:6243/6799) `[CÓDIGO]`. Sin epoch/generación en el engine (MOD:51-168). Hallazgo lateral serio, independiente del split.

**¿El contrato actual es suficiente?** Para el corte percibido: hoy el barge-in **no está roto** — el corte real lo produce GB dejando de enviar, y la cola residual está acotada por el pacing de GB (presupuesto ≤ 0,6 s `[CÓDIGO]` WS L1235) más el búfer del engine (jitter 300 ms + resto ≤ 20 ms + arranque 40-200 ms) ⇒ cola tras el corte ≈ **0,7-1,1 s** `[INFERENCIA]`; verificado el mecanismo, **no la cifra** — exige la prueba E2E de abajo. Para acotarla a ~100-300 ms sí hace falta una **orden explícita de interrupción**, y existe una costura latente barata: el engine ya tiene el handler genérico `ProviderBargeIn/interruption → _apply_barge_in_action` (ENG:8213-8253) que hoy es inalcanzable porque el proveedor reenvía el JSON de GB sin inyectar `call_id`. Cambio mínimo (fuera de este refactor, para el equipo del engine): el proveedor sintetiza `{type: ProviderBargeIn, call_id}` al recibir un nuevo mensaje GB→cliente `PlaybackClear` emitido por `_cancel_active_tts` — correlacionado por llamada; correlarlo por generación exige etiquetar el audio, que es un cambio mayor y sólo se justifica si la medición E2E lo pide. **No** debe cablearse el flush a `UserStartedSpeaking`: GB lo emite en cada apertura de VAD (L3740), incluidas las espurias durante TTS, y cortaría reproducción con ruido `[CÓDIGO+INFERENCIA]`.

**Distinción pedida de "último byte"** `[CÓDIGO]`: (1) *enviado por Golden*: `audio_out_bytes`/presupuesto (L9282-9285) y el ledger del pacer — es lo que gobierna el historial "solo dijiste"; (2) *aceptado por ai-engine*: `_provider_bytes`/`enqueued_bytes` (ENG:8718-8725) y `tx_bytes` del SPM (SPM:2607-2613, Prometheus); (3) *reproducido por AudioSocket/Asterisk*: **no se modela en ningún lado** — el proxy más cercano es `tx_bytes` + ≤1 trama en vuelo + búferes TCP/Asterisk. Consecuencia directa `[INFERENCIA]`: la estimación de GB de "lo que oyó el cliente" (reloj de pared del pacer, L401-408) ignora el arranque del jitter del engine, así que el prefijo interrumpido del historial puede incluir ~0,2-0,5 s de palabras nunca oídas; la prueba "historial coherente con audio reproducido" (§12) debe medirlo en los tres puntos antes de tocar nada.

---

## 9. Topologías compatibles con el split

El contrato no transporta audio, así que **ninguna topología necesita un dispatcher que toque tramas** — el requisito "el audio va directo al Media dueño" se cumple por construcción: el audio sólo existe entre ai-engine y Media.

| criterio | T1 · Media + Conversación, misma tarea (2 procesos) | T2 · N Media + 1-M Conversación, misma tarea | T3 · Media y Conversación en tareas Modal separadas |
|---|---|---|---|
| Afinidad por llamada | trivial (1 Media) | por accept del socket compartido (mismo mecanismo y desbalance ~binomial que el dictamen multiproceso §3.2) | llamada→tarea Media por scheduler de Modal; Media→Conversación por conexión saliente estable |
| IPC | socket UNIX / pipe local; framing JSONL o msgpack | UDS por par Media↔C | red entre tareas: Modal no documenta red privada tarea↔tarea de serie ⇒ C expondría su propio endpoint autenticado `[DOCUMENTACIÓN/PENDIENTE]` |
| Balanceo | n/a | igual que B del dictamen (kernel) para llamadas; C único = sin balanceo | Modal balancea Medias; Medias eligen C (round-robin/consistent-hash por sesión) |
| Dominio de fallo | la tarea (como hoy); C caído → Media huérfano habla farewell y cuelga con gracia; **Media caído mata el audio igual que hoy** | C caído afecta a TODAS las llamadas de la tarea (N×K) — peor que T1 por llamada | el mejor: redeploy de C sin tocar audio **sólo si** se implementa re-attach por epoch (complejidad ↑↑) |
| Latencia añadida | decenas de µs-ms por mensaje; a tasa de turno (~1/14 s por llamada `[MEDICIÓN]`) es irrelevante para TTFAB | igual | ~ms por evento por red; sigue siendo tasa de turno, no de trama — tolerable `[INFERENCIA]` |
| Backpressure | colas por sesión en memoria/UDS | igual | ventanas TCP + colas; `T_ev` más generoso |
| Memoria de modelos | modelos sólo en Media; **C sin torch/ORT** (hoy el proceso único carga todo) | modelos × N Media (idéntico al análisis del dictamen B) | igual que T1 por tarea Media |
| Escalado independiente | no | parcial | **sí**: Media escala por llamadas, C por turnos/s (LLM-bound) |
| Recuperación si muere una mitad | supervisor local reinicia C; Media aplica modo huérfano; sesiones se pierden sólo si muere Media | igual + blast radius mayor de C | C con re-attach: sesiones sobreviven; Media muerto = llamadas muertas (inevitable: posee el audio) |

**Orden recomendado**: T1 para el PoC y la primera producción (mínimo mecanismo nuevo); T3 es el pago estratégico y se decide después con datos; T2 sólo si la densidad por tarea vuelve a doler — y entonces es literalmente el dictamen multiproceso aplicado al proceso Media, con su misma matemática de desbalance (§10).

---

## 10. Relación con el dictamen multiproceso (2026-09-28)

Compatibles y ortogonales. Aquel dictamen estableció: (a) replicar Goldens completos no cambia K por loop — sigue válido: **este split tampoco lo cambia por sí mismo**, porque el camino de audio queda entero en el loop de Media `[MEDICIÓN steno_p90 plano]`; (b) la afinidad por llamada es natural — este diseño la explota y la formaliza con identidad; (c) el mecanismo multi-proceso-por-tarea (socket compartido, factory, límites por worker) — se hereda tal cual para T2 aplicado a Media; (d) el PoC-0 "medir K_Modal de A" — **sigue siendo previo**: fija la línea base contra la que F6 compara el split. Diferencia de naturaleza: aquello era empaquetado de réplicas; esto mueve responsabilidades y cambia contratos internos — puede ganar jitter/fallos/memoria aunque no gane K, y eso se decide midiendo, no argumentando `[PENDIENTE]`.

**Qué sale exactamente del loop de Media** `[CÓDIGO]`: el manejo del stream LLM token a token (regex de frase L5264 por token, limpieza L1466, strip de tools L1519), todas las mutaciones/poda/compresión de historial (L1669, L1705), el router (await HTTP L4649), AMD, parseo/despacho de tool calls (L6311-6558), transiciones y contexto de campaña (L5000-5054), strict-enforcement (handoff a hilo L5606), follow-ups (L7024), REST de campañas, y el heap conversacional que hoy engorda los `gc.collect` del loop (L1357). **Qué permanece**: reader/FIFO, batching y pool DSP, envíos a Steno, Finalize, recepción/paceo TTS, presupuesto, watchdogs, fondo, barge-in, cleanup de media — es decir, todo lo que la curva de rodilla mide `[MEDICIÓN]`. **Ganancia plausible y honesta**: colas p95-p99 del loop más cortas al retirar el trabajo por turno y los picos de historial/regex; menor heap ⇒ pausas gc menores en Media; reinicio/deploy de C sin caída de audio; C sin ONNX/torch. Magnitud: `[PENDIENTE]` — F6.

---

## 11. Fases del refactor (ruta hacia la separación, con rollback)

Continuación de la auditoría (sus F0-F1 se subsumen aquí). Cada fase termina verde en CI antes de avanzar; rollback = bandera de modo + cuerpos delegadores intactos.

| fase | símbolos reales afectados | estado que cambia de dueño | pruebas obligatorias | rollback | condición para avanzar |
|---|---|---|---|---|---|
| **R0. Caracterización** | `_session_processor`, `_cancel_active_tts`, `_process_llm_tts_streaming`, `_speak_response`, `_handle_function_response`, `_run_terminal_on_exit` | ninguno | las de la auditoría F0 + **traza dorada por escenario** (turno normal, barge-in temprano/medio/tardío, doble cancel, terminal, inject, deaf): secuencia exacta de mensajes WS + mutaciones de history; instrumentar corte E2E en 3 puntos (§12.13) | sólo tests | trazas doradas estables en CI |
| **R1. Ownership in-process** | romper los 8 cruces de §3: `_cancel_active_tts` deja de escribir history (emite objeto-evento interno consumido en línea por un componente Conversación embebido); `turn_seq`→`turn_id` acuñado en `_resolve_turn_complete`; toda habla entra por un único punto `Speak(speech_id, …)` que envuelve las 5 variantes; timers conversacionales reubicados detrás de eventos internos | history y campaña pasan a un objeto `Conversation` interno; Media deja de leerlos | todas las 699 + trazas doradas byte-idénticas; `test_history_truncation`, `test_turn_audio_flush`, `test_terminal_*`, `test_parallel_tool_calls` | los eventos internos son llamadas síncronas — revertir = inlinear de nuevo | trazas idénticas al monolito |
| **R2. Contrato tipado in-process** | definir los mensajes de §5 (dataclasses + versión); colas por sesión dentro del mismo proceso; el componente Conversación consume de la cola (ya no en línea) | `was_interrupted`, transcript, spoken_text y fn-calls viajan sólo como mensajes | golden-trace + nuevas: orden `SpeechInterrupted`→`TurnCompleted`; `SpeechRejected` en turno viejo; dedup por seq | flag `GB_CONTRACT=0` vuelve a R1 | ninguna regresión de latencia por turno (medida local) |
| **R3. IPC local** | serialización (JSONL/msgpack) sobre socketpair/UDS; supervisor arranca ambos procesos (factory de `src/main.py` del dictamen §6); Media conserva modelos/gates; C arranca sin ONNX | primer cruce real de proceso; `session_epoch` activo | pruebas de §12.7-10 (pérdida IPC, muertes, cierre con comandos pendientes) + fuzz de duplicados/tardíos | flag `GB_SPLIT=0` = un proceso (R2) | supervivencia limpia a kill de cada mitad |
| **R4. Dos procesos en despliegue** | `docker/arranque.sh` lanza supervisor; imagen única, dos procesos; gate de referencia una vez | ninguno nuevo | humo en Modal (una tarea, T1); trazas E2E contra ai-engine real | variable de entorno | llamadas reales de laboratorio OK |
| **R5. Fallos, aislamiento y calidad** | — | — | batería completa de §12 con `verifica_mezclas` a 100 sesiones; caos: kill C bajo barge-in, kill Media, IPC drop a mitad de Speak | — | 0 mezclas, 0 dobles-respuesta, historiales coherentes |
| **R6. Medición y comparación** | CYCLE-PROBE + cliente T-D1b 60 ms (parches del dictamen §6.7) | — | escalera de K en T1 vs monolito A a igual reserva; TTFAB, `cycle/rvad p90`, corte E2E, RSS por proceso | volver a A | decisión: adoptar/limitar/descartar con número punta a punta (regla 3) |

---

## 12. Pruebas mínimas exigidas (diseño concreto)

1. **Dos llamadas intercaladas con marcas** — reuso directo: `td1_client.py --speech utterances/listos` + `firmas.json` + `verifica_mezclas.py` `[CÓDIGO]`; criterio: 0 firmas cruzadas en transcript y en audio TTS de vuelta.
2. **Cien sesiones sin contaminación** — T-D1c (100 sesiones) sobre T1; añade verificación de `session_id` en cada evento IPC logueado.
3. **Misma call_id, distinto `session_epoch`** — simular renacimiento de Media: reproducir mensajes del epoch 1 contra epoch 2 ⇒ 100 % rechazados con métrica, 0 efectos.
4. **Barge-in con Conversación retrasada** — inyectar 2 s de pausa en el consumidor C: el corte de audio (pasos 1-7) debe ocurrir en Media sin degradación; el historial se actualiza tarde pero **antes** del LLM del turno siguiente (aserto sobre orden de canal).
5. **TTS viejo tras cancelación** — `SpeakChunk` de un `speech_id` interrumpido llegando después: descartado en silencio, contador `stale_chunks` > 0, 0 bytes al cliente.
6. **Dos turnos consecutivos con cancelación tardía** — turno T1 interrumpido, T2 inmediato: exactamente una respuesta para T2, ninguna resurrección de T1 (`SpeechRejected` visible).
7. **Pérdida y reconexión del IPC** — matar el UDS: Media entra en modo huérfano (farewell + hangup con gracia); C marca sesiones muertas; sin excepciones no capturadas.
8. **Muerte de Media** — kill −9: C recibe EOF, limpia; el supervisor reinicia Media; llamadas de ese Media se pierden (aserto: se pierden *limpiamente*: Steno/Ursula cerrados por timeout, sin zombies — reusar `test_worker_session_release` como oráculo de idempotencia).
9. **Muerte de Conversación** — kill −9 con 10 llamadas vivas: audio y guardias siguen; farewell huérfano suena; historiales quedan truncados pero consistentes.
10. **Cierre con comandos pendientes** — `CloseSession` con `SpeakChunk` en vuelo y `SendFunctionCall` sin respuesta: drenaje definido, ningún mensaje aplicado tras `SessionClosed`.
11. **Preservación de la cabeza de interrupción** — oráculo actual: `test_turn_audio_flush.py` `[CÓDIGO]`; E2E: utterance conocida que interrumpe, el transcript final contiene sus primeras palabras.
12. **Historial coherente con lo reproducido** — comparar `spoken_text` del evento contra el audio realmente emitido por el engine (tx_bytes→texto vía marcas por frase); cuantificar el sesgo del reloj de pared (§8).
13. **Corte medido en tres puntos** — timestamp de `cancellation_event` en GB, último `tx_bytes` creciente en SPM, y silencio en la captura AudioSocket (o RTP espejo): reporta `corte_GB→engine` y `engine→oído`; línea base antes de R1 y regresión en R5/R6. Es también la prueba que decide si la orden explícita de interrupción hacia ai-engine (§8) vale su costo.

---

## 13. Riesgos de mezcla y cómo se eliminan

| riesgo | origen posible | eliminación |
|---|---|---|
| PCM de una llamada en otra | sólo dentro de Media (el IPC no lleva audio) | mecanismos actuales por sesión (§7) + pruebas 1-2; sin colas globales de bytes |
| Transcript/respuesta en llamada equivocada | mensaje IPC mal ruteado | clave `(session_id, epoch)` obligatoria + canal por sesión + rechazo contabilizado (pruebas 3, 5) |
| Respuesta de un turno viejo | Speak tardío | `turn_id` + `SpeechRejected` (= L7255 generalizado) |
| Historial de otra habla | `spoken_text` cruzado | `speech_id` en `SpeechInterrupted/PlaybackFinished`; C ignora ids desconocidos |
| Sesión resucitada tras reinicio | id reutilizado | `session_epoch` (prueba 3) |
| Doble respuesta al mismo turno | dedup fallido | `TurnCompleted` único por `turn_id` + C idempotente por turno (prueba 6) |

---

## 14. PoC y criterios de aceptación

**PoC-split (tras R3, en laboratorio; no requiere Modal)**: una caja, supervisor con Media+C (T1), ai-engine real en `.143` apuntando al puerto local, 10 → 40 llamadas sintéticas T-D1b con habla y barge-ins forzados (utterance que pisa el TTS). Criterios: trazas doradas idénticas al monolito en los 7 escenarios de R0; corte E2E (prueba 13) sin regresión > 50 ms vs línea base; pruebas 4-6 y 8-10 verdes; RSS: C sin torch/ORT (aserto de import). **PoC-capacidad (R6, ventana de campaña o burst pagado)**: escalera de concurrencia T1 vs A a igual reserva, con CYCLE-PROBE y TTFAB; el split se adopta sólo si mejora un número punta a punta (p99 de `rvad`/`cycle` o TTFAB p90) sin empeorar ninguno, o si se adopta por sus beneficios operativos con ese costo cuantificado — decisión explícita, no por defecto (regla 5 de CLAUDE.md).

## 15. Estimación de esfuerzo y riesgo `[INFERENCIA]`

| fase | esfuerzo (días-persona) | riesgo | dominante |
|---|---|---|---|
| R0 | 5-8 | bajo | armar trazas doradas E2E con engine real |
| R1 | 10-15 | **alto** | las 5 variantes de habla y los hallazgos H1-H4 de la auditoría (doble finally, reset temprano de flags) |
| R2 | 5-8 | medio | semántica de idempotencia/orden |
| R3 | 6-10 | medio-alto | pérdida de IPC y supervisor |
| R4 | 3-5 | bajo | empaquetado |
| R5 | 6-9 | medio | caos + mezclas a 100 |
| R6 | 3-5 + ventana | bajo | costo del burst con habla (~1.700 turnos GPU a N=100 `[CÓDIGO]` runbook) |
| **Total** | **38-60 (≈ 8-12 semanas · 1 persona)** | — | — |

Riesgos transversales: deuda de pruebas E2E (hoy **cero** tests recorren `_session_processor` o `_process_llm_tts_streaming` de punta a punta `[CÓDIGO]`) — por eso R0 es innegociable; el hallazgo del engine "sin reconexión al caer el WS de GB" (§8) convierte cualquier inestabilidad nueva del lado GB en llamadas colgadas contra socket muerto — conviene arreglarlo en el engine antes de R4 `[CÓDIGO]`; y la config efectiva (activa en `.143`/Modal y `ai-agent.yaml` real) sigue sin auditar `[PENDIENTE]`.
