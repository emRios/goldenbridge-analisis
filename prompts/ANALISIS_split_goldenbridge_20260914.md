# Análisis del split de GoldenBridge: MediaWorker / Control (14-sep-2026)

Topología fija: `context/ARQUITECTURA_STACK.md`. Contrato de ejecución de GB
Modal (3.14.7t · ORT 1.24.4 secuencial · `GB_ORT_INTRA_OP=1` · `OMP`/`OPENBLAS=1`
· gates de referencia e ISA · aislamiento por sesión) **se preserva**; nada de lo
que sigue lo cambia.

Código inspeccionado: `GoldenBridge` rama `api` @ `08932a6`;
`Asterisk-AI-Voice-Agent-FX` @ `0d652fa` sólo para el lado receptor.

Etiquetas: **[código]** verificado con archivo:línea · **[inferencia]** deducción
con supuesto explícito · **[pendiente]** requiere medición.

---

## 0 · Dictamen

> **Justificado sólo si F3 lo demuestra — y F3 debe correrse con el coalescer
> de 60 ms ya activo.**

La razón es de secuencia, no de arquitectura: la carga que el split movería
fuera del loop de Control es **exactamente la que el coalescer recorta 3×** sin
tocar GB. Medir la rodilla antes de 60 ms mide un problema que un cambio de
config ya está eliminando. El split sólo tiene sentido contra la rodilla
**residual** después de 60 ms.

| señal en la rampa F3 (con 60 ms) | qué dice | decisión |
|---|---|---|
| `submit_p90`, `resume_vad_p90`, `cycle_p90` crecen con N · `job_p90`, `steno_p90`, `stt/llm/tts_latency` **planos** | el loop de coordinación es el cuello | **split justificado** — atacaría lo que crece |
| `job_p90` crece con N (DSP/pool) · o `frames_per_cycle` sube y `resolution=fallback` sube (lote de 600 ms) · o timers/watchdogs dominan | batch / DSP / temporizadores | **split no ataca el cuello** → `inbox_batch_max`, pool, timers |
| `stt_latency_ms` / `llm` / `tts` p90 crecen · `cycle_p90` y `resume_vad_p90` planos | proveedores remotos | **no refactorizar GB** |

**Estado de la evidencia hoy:**
- **[código]** el loop hace cinco tipos de trabajo (§1.3); tres son audio, dos son conversación.
- **[medido, .143, pre-60 ms]** `job_p90` ×1,5 · `cycle_p90` ×5 · `resume_vad_p90` ×13 entre 1-5 y 16-25 sesiones; `steno_p90` = 0,10 ms plano. Apunta a coordinación — **pero en una caja con co-tenencia y spin, y a 20 ms**.
- **[pendiente]** la misma tabla en una tarea Modal limpia, con 60 ms, `max_containers=1`.

**El P0 no es evidencia de K.** Probó compatibilidad de 60 ms a N=10. No dice nada de la rodilla.

---

## 1 · Lo que el código dice del monolito

### 1.1 · Recorrido completo de una trama **[código]**

```
ai-engine ──WS binario µ-law──► websocket_server.py

 _handle_connection (2124)          registra self.sessions[sid]; crea inbox (Queue)
   ├─ reader task  (read_decouple)  websocket.receive() → sella _last_audio_arrival (2550) → inbox.put
   └─ processor task (2596)         inbox → carry → _drain_inbox_frames → _drain_batch (2781)
        └─ _run_audio_frames (2789)                            [ciclo · CYCLE-PROBE 2828-2945]
             ├─ _audio_pre_vad                                  cuenta frames/bytes            [pre]
             ├─ await vad_processor.process_audio_batch (1159)  ─► pool "vad"                  [submit]
             │      job (worker): µ-law→pcm16 · soxr(_resample_state) · RMS
             │           · noisereduce (si ≥100 ms) · Silero inline (31,25/s)
             │           · acumular turn_audio · smart-turn si silencio ≥ stop_secs   [job]
             │      sellos _probe_job_start/_end (1139-1156) · _probe_vad_returned (1209)
             ├─ vuelta al loop                                                          [resume]
             └─ results → barge_in_ready / turn_complete (3699-3900)
                   ├─ barge_in_ready → _cancel_active_tts (8445)
                   └─ turn_complete → _resolve_turn_complete (3924)
                         └─ _process_speech_streaming (3978): stt_processor.send_finalize → transcript
                              └─ _process_transcript (4443): sm.process_user_message (4551)
                                   └─ _start_tracked_speech (7158): gen = turn_seq; create_task(
                                        _speak_response (7216): _reserve_agent_speech → TTS Ursula
                                          → _PacedAudioSender (clase ~630): pacer.sleep_needed()
                                            → _safe_send_bytes ──WS binario──► ai-engine
```

Funciones de negocio (transferencia, hangup, `CallEventNotification`):
`create_function_call_request` (6557, 9458) → WS JSON → **el engine las ejecuta**
→ `FunctionCallResponse` (2993, 3490) → `sm_pending_function_ids.discard` (3511).
GB **no** toca ARI ni Asterisk; sólo pide.

### 1.2 · Estado por sesión — qué es, dónde vive **[código]**

**`SessionState` (830-1060)** — ~90 campos. Los que importan para un límite de proceso:

| grupo | campos | naturaleza |
|---|---|---|
| **transporte** | `websocket`, `inbox: asyncio.Queue`, `_ws_disconnected`, `websocket_send_lock` | socket + cola del loop |
| **lote / job** | `_batch_carry: deque`, `_batch_idle: Event`, `_frames_in_flight`, `_cycle_probe` | coordinación loop↔pool |
| **audio** | `audio_buffer: List[bytes]`, `pre_context_buffer: deque(200)`, `vad_processor` | **bytes de audio** |
| **TTS / barge-in** | `active_tts_task`, `tts_task_lock`, `turn_seq`, `cancellation_event`, `tts_cancelled`, `active_speech: _ActiveSpeech(sender: _PacedAudioSender)`, `active_tts_stream`, `tts_audio_playing: Event`, `_tts_starting_at`, `tts_connection` | estado caliente de generación |
| **STT** | `stt_connection`, `_stt_connect_task`, `_finalize_inflight`, `_pending_flush_fragment` | conexión Steno por sesión |
| **timers** | `silence_monitor_task`, `turn_watchdog_task`, `no_interaction_guard_task`, `_pending_flush_watchdog`, `background_audio_task` | tareas asyncio del loop |
| **conversación** | `conversation_history`, `campaign: CampaignSessionState`, `sm_pending_function_ids`, `variables`, `functions` | estado de negocio |

**`SmartTurnVADProcessor` por sesión (360-470, `create_session_instance` 506)**:
`turn_audio: np.ndarray`, `_tts_playing`, `_tts_start_time`, `_speech_start_time`,
`_resample_state` (soxr), `_stt_resample_state` (soxr → Steno), `_state_lock: RLock`,
`_voice_ts_lock: Lock`, `_inference_gate: asyncio.Lock`, `_gated_frames`,
`_fallback_start_time`, todo el bloque de barge-in (`_bg_*`), `_midspeech_*`.
Sub-instancias: `vad = self.vad.create_session_instance()` (Silero: wrapper por
sesión con estado LSTM, `InferenceSession` **compartida**), `smart_turn =
self.smart_turn.create_session_instance()` (`instance.session = self.session`:
ORT **compartida**). `noise_processor` compartido, stateless.

**Registries:** `self.sessions: dict` (alta en 2171, baja en `finally` 2481 y en
`_idle_session_reaper` 1458). `_release_worker_sessions` (6094) cierra STT/TTS
por sesión, idempotente.

### 1.3 · Qué hace el loop, por tipo de trabajo **[código]** + **[medido]**

| # | trabajo del loop | cadencia por llamada | dónde | ¿lo mueve el coalescer? | ¿lo movería el split? |
|---|---|---|---|---|---|
| a | lectura WS + `inbox.put` | 50/s → **16,7/s** | reader task | **sí, 3×** | sí (a MW) |
| b | submit + resume por lote | ~11/s fuera de TTS · **50/s durante TTS** (`_batch_eligible`=False) → 3× menos | processor task | **sí, 3×** | sí (a MW) |
| c | **pacer de TTS de salida**: un `await` por chunk (`_PacedAudioSender._run`) | ~10/s durante TTS (chunks ~100 ms) | tarea por speech | no | sí (a MW) |
| d | JSON STT/LLM/TTS, `FunctionCall` round-trip, historial | ~1-3/s | loop | no | **no** (queda en Control) |
| e | timers: silence monitor, turn watchdog, no-interaction guard, flush watchdog, fallback | ~1/s | tareas del loop | no | **no** (queda en Control) |

**[medido]** `steno_p90` = 0,10 ms plano hasta 26-40 ⇒ (d) no crece con N.
**[inferencia]** (e) es cardinalidad de tareas, no CPU; crece linealmente en
número pero cada una duerme. **[pendiente]** su coste real en el loop.

⇒ **Lo que satura el loop es (a)+(b)+(c).** El coalescer ya recorta (a) y (b).
El split movería (a)+(b)+(c) a otro proceso y dejaría (d)+(e) en Control.

### 1.4 · Barge-in y cancelación: carreras ya protegidas **[código]**

| protección | mecanismo | qué evita |
|---|---|---|
| **FIX-E `_reserve_agent_speech`** (2679) | sella `_tts_starting_at` **síncrono** antes de cualquier `await`; espera `_batch_idle` si `_frames_in_flight > 0` (timeout `_AGENT_SPEECH_BARRIER_TIMEOUT`) | que un job en vuelo lea `_tts_playing` antes de que TTS lo mute |
| **`_batch_eligible`** (2719) | False si TTS vivo, naciendo (`_tts_starting_at` + TTL) o `tts_audio_playing` | que un lote cruce una mutación de estado del TTS |
| **`tts_task_lock`** en `_cancel_active_tts` (8445) | serializa: `clear_turn_audio` → `tts_cancelled` → `cancellation_event.set()` → parar `active_tts_stream` → `active_tts_task.cancel()` → `flush_tts_session` | doble cancelación / TTS huérfano |
| **`turn_seq` + `_speech_gen`** (`_start_tracked_speech` 7158 → `_speak_response` 7256) | `turn_seq += 1` en `_process_speech_streaming` (4005) y `_process_speech` (4255); el speech aborta si `session.turn_seq != _speech_gen` | que una respuesta **de un turno anterior** hable tarde |
| **`_inference_gate`**, **`_state_lock`**, **`_voice_ts_lock`** | por sesión, en el procesador | orden y aislamiento entre el job (worker) y el loop |

**Todas son in-process: locks de asyncio/threading, un `Event`, un contador.**
Un límite de proceso convierte cada una en un protocolo:

| hoy | tras un split |
|---|---|
| `barge_in_ready` en el resultado del job → `_cancel_active_tts` en el mismo `await` | MW detecta → **debe parar el pacer localmente** → notifica a Control → Control cancela LLM/TTS en vuelo → `Cancel{gen_id}` a MW |
| `_reserve_agent_speech` espera `_batch_idle` (memoria compartida) | Control pide `Speak{gen_id}` → MW debe aplicar la reserva **él** (posee `_frames_in_flight`) |
| `turn_seq` es un `int` en `SessionState` | `gen_id` viaja en cada mensaje; los dos lados lo comparan |

---

## 2 · Mapa de responsabilidades y estado

| responsabilidad | monolito hoy **[código]** | MediaWorker propuesto | Control propuesto | por qué |
|---|---|---|---|---|
| WS de audio con ai-engine (entrada) | reader task | **MW** | — | posee los bytes; único lector del socket |
| WS de audio (TTS de salida) + `_PacedAudioSender` | `_speak_response` → sender | **MW** | — | el pacer es trabajo de 50 fps de salida; el socket es uno |
| µ-law→pcm16, soxr, RMS, noisereduce | job en pool `vad` | **MW** (mismo pool) | — | bytes + estado soxr |
| Silero VAD (31,25/s) | job | **MW** | — | LSTM por sesión; `InferenceSession` compartida |
| smart-turn + `turn_audio` | job / `_inference_gate` | **MW** | — | necesita `turn_audio` (bytes); ORT compartida; **contrato congelado se preserva** |
| detección de fin de turno (`turn_complete`) | resultado del job | **MW emite `TurnEnd`** | consume | decisión de audio |
| STT streaming (Steno WS por sesión) | `stt_connection`, `_stt_resample_state` **dentro del job** | **MW** *(ver nota)* | recibe `SttFinal` | el audio a Steno sale del job; moverlo a Control lo haría cruzar dos veces |
| LLM (Cerebras) | `_process_transcript` → SM → LLM | — | **Control** | texto, ~1/s |
| TTS (Ursula) — síntesis | `_speak_response`, `tts_connection` | **MW** recibe `Speak{text}` y sintetiza | decide **qué** decir | los bytes de TTS nacen y mueren en MW; Control no debe ver audio |
| barge-in — **detección y corte inmediato** | job → `_cancel_active_tts` | **MW** (corte local del pacer) | recibe `BargeIn` | la latencia de corte no puede pagar un hop |
| barge-in — cancelar LLM/generación | mismo `_cancel_active_tts` | aplica `Cancel{gen_id}` | **Control** decide y emite | la generación vive en Control |
| `turn_seq` / `gen_id` | `int` en sesión | valida | **emite** | un solo dueño del contador |
| transferencia, hangup, `CallEventNotification` | `FunctionCallRequest` → engine | — | **Control** | negocio |
| campaña y máquina de estados | `state_machine_engine` | — | **Control** | texto y estado |
| timers de silencio / watchdogs | tareas del loop | `TurnWatchdog` (necesita audio) | silence monitor, no-interaction (texto/tiempo) | por dato que observan |
| health, `/api/*` | FastAPI en el mismo proceso | — | **Control** | |
| ciclo de vida de sesión | `self.sessions`, `finally`, reaper | MW: vida del socket | **Control: dueño lógico**; ordena cierre | un solo dueño del `call_id` |

**Nota STT:** hoy el resampleo a Steno y el envío del audio viven **dentro del
job** (`_stt_resample_state` "job-owned", `stt_prepared`). Si STT se quedara en
Control, cada lote de audio cruzaría el límite dos veces. **Queda en MW.**

### Estado por sesión — destino en un split

| elemento | destino | motivo |
|---|---|---|
| `audio_buffer`, `pre_context_buffer`, `turn_audio`, `pending_audio`, `audio_buffer` de Silero | **local en MW, nunca cruza** | bytes; regla no negociable |
| `_resample_state`, `_stt_resample_state` (soxr) | **local en MW** | estado de filtro |
| estado LSTM de Silero, `_tts_playing`, `_bg_*`, `_midspeech_*`, `_fallback_*` | **local en MW** | decisión de audio |
| `inbox`, `_batch_carry`, `_batch_idle`, `_frames_in_flight` | **local en MW** | coordinación loop↔pool |
| `_PacedAudioSender`, `active_tts_stream`, `tts_connection` | **local en MW** | bytes de salida |
| `turn_seq` → `gen_id` | **evento** (en todo mensaje) | correlación |
| `is_speaking`, `turn_complete`, `speech_start/end_time` | **evento** (`SpeechStart/End`, `TurnEnd`) | Control los necesita como hechos, no como bytes |
| transcript | **evento** (`SttFinal`) | texto |
| `conversation_history`, `campaign`, `sm_pending_function_ids`, `variables` | **local en Control** | negocio |
| `websocket` con el engine | **MW** | uno solo |
| `cancellation_event`, `tts_task_lock`, `active_tts_task` | **desaparecen**; los sustituye `Cancel{gen_id}` idempotente | eran sincronización in-process |

---

## 3 · Diseño del límite MW ↔ Control (si F3 lo justifica)

**Transporte:** un canal **por llamada**, orden FIFO garantizado por el
transporte (pipe/UDS en la misma tarea; WS en tareas separadas). **Sin broker,
sin base de datos.** El código no tiene nada que un tercero resuelva mejor: el
estado caliente es por llamada y vive en MW; Control sólo necesita hechos.

Cabecera común: `{v: 1, call_id, seq, ts_ms}` — `seq` monótono **por emisor y
por llamada**.

### MW → Control

| mensaje | campos adicionales | orden | entrega | nota |
|---|---|---|---|---|
| `SpeechStart` | `turn_id` | estricto | at-least-once, idempotente por `turn_id` | equivale a `create_user_started_speaking` |
| `SpeechEnd` | `turn_id`, `speech_ms` | estricto | ídem | |
| `TurnEnd` | `turn_id`, `resolution ∈ {smartturn, midspeech, fallback}`, `prob` | **estricto** | **exactamente una vez por `turn_id`**; MW no reemite | dispara STT finalize → LLM |
| `SttFinal` | `turn_id`, `text`, `stt_latency_ms` | estricto tras `TurnEnd` | at-least-once, idempotente por `turn_id` | si STT queda en MW |
| `BargeIn` | `turn_id`, **`gen_id_interrupted`**, `local_cut_ms` | estricto | at-least-once | MW **ya cortó** el pacer antes de emitir |
| `TtsDone` | `gen_id`, `bytes`, `partial: bool` | estricto | at-least-once, idempotente por `gen_id` | equivale a `create_agent_audio_done` |
| `MediaClosed` | `reason` | último | exactamente una vez | el socket con el engine murió |

### Control → MW

| mensaje | campos adicionales | orden | entrega | nota |
|---|---|---|---|---|
| `Speak` | **`gen_id`**, `text`, `add_to_history`, `is_greeting` | estricto | **exactamente una vez por `gen_id`**; MW descarta `gen_id` ≤ último cancelado o ya visto | MW aplica FIX-E localmente antes de arrancar TTS |
| `Cancel` | **`gen_id`** (o `all: true`) | puede adelantar a `Speak` pendientes | **at-least-once, idempotente** | si llega antes que su `Speak`, MW recuerda el `gen_id` como cancelado (TTL) |
| `SetVadMode` | `sensitive`, `post_tts` | estricto | at-least-once | equivale a `set_barge_in_mode`, `_sensitive_mode` |
| `Hangup` | `reason` | último | exactamente una vez | MW drena parcial, cierra socket, emite `MediaClosed` |

### Reglas

- **`gen_id` lo emite sólo Control**, monótono por llamada; sustituye a `turn_seq`. MW valida `Speak.gen_id > último aplicado`.
- **Barge-in en dos tiempos:** (1) MW para el pacer y vacía su cola de TTS **localmente** al detectar `barge_in_ready` — sin esperar a nadie; (2) emite `BargeIn{gen_id_interrupted}`; (3) Control cancela LLM en vuelo, sube `gen_id`, y emite `Cancel{gen_id_interrupted}` (idempotente; MW ya lo aplicó). La latencia de corte **no paga el hop**; sólo la cancelación de la generación lo paga.
- **Duplicados:** todos los eventos de MW son idempotentes por `turn_id`/`gen_id`; `TurnEnd` no se reemite (si se pierde, `Control` lo detecta por `SttFinal` sin `TurnEnd` previo y lo sintetiza).
- **Backpressure:** cola por llamada acotada en ambos sentidos. En MW→Control, descartar el más viejo **salvo** `TurnEnd`, `BargeIn`, `MediaClosed`. En Control→MW, `Cancel` y `Hangup` saltan la cola.
- **Timeouts:** MW espera `Speak` tras `SttFinal` hasta `T_reply` (hoy el `fallback_timeout_secs` y el `turn_watchdog` cumplen ese papel en el monolito **[código]**); si vence, emite `TurnEnd{resolution: fallback}` de nuevo **no** — emite `ReplyTimeout` y Control decide.
- **Pérdida de conexión MW↔Control:** MW es dueño del socket con el engine: termina el audio en curso, no acepta nuevos turnos, y tras `T_orphan` hace hangup y `MediaClosed`. Control marca la llamada muerta y ejecuta `execute_terminal_on_exit` (6081) con `after_cancel=True`.
- **Cierre normal:** Control → `Hangup` → MW drena y cierra → `MediaClosed` → Control libera. **Un solo dueño del `call_id` en cada instante: Control.**

---

## 4 · Topologías de despliegue

| | (i) 2 procesos, 1 tarea Modal | (ii) MW y Control en tareas separadas | (iii) MW absorbido por ai-engine |
|---|---|---|---|
| afinidad por llamada | natural: mismo contenedor | **[pendiente de comprobar]** Modal enruta *inputs* a contenedores; una llamada = 2 inputs en 2 apps ⇒ hace falta rendezvous por `call_id` que Modal no da nativo | natural: ya vive en el engine |
| loops que se replican | 2 por tarea | 1 por tarea de cada tipo; escalan aparte | 1 en el engine (`asyncio`, 1 proceso **[código engine.py]**) |
| estado caliente | MW (mismo host) | MW; Control sólo hechos | ai-engine en `.143` |
| modelos | **1 copia por tarea** (sólo MW los carga) | 1 copia por tarea MW; Control **cero** | copia en el engine + **contrato ORT/gate/ISA duplicado en otro código base** |
| superficie de fallos | crash de MW = pierde audio de sus llamadas; crash de Control = pierde decisiones. Ambos ⇒ llamada muerta | + red entre planos + rendezvous | el engine ya es SPOF de media; suma VAD+ST a 16 núcleos compartidos con lasik/postgres |
| **¿eleva K?** | **sólo si** (a)+(b)+(c) dominan y (d)+(e) son materiales. K_tarea = min(K_MW, K_Control) | ídem, y K_Control se vuelve casi irrelevante (10 ev/s) | **empeora**: recrea el mismo loop un salto antes, con peor co-tenencia |

**Por qué partir Python no sube K solo:** K está acotado por el loop **más
cargado**. Si MW hereda (a)+(b)+(c) — que es lo que satura hoy — entonces
K_MW ≈ K_mono × (1 + fracción de (d)+(e)). Con `steno_p90` plano, (d) es
pequeño; (e) es **[pendiente]**. Si (d)+(e) son el 10 % del loop, el split
compra un 10 %. **La métrica que lo demuestra:** `submit_p90 + resume_vad_p90`
a N fijo (p. ej. 60) en MW vs en el monolito, misma rampa, mismo contrato ORT.
Si no bajan, el split no era la palanca.

**(iii) no se recomienda** además porque exigiría reproducir en ai-engine el
contrato congelado de SmartTurn (ORT 1.24.4, artefacto, gate de referencia e ISA)
— fuera de alcance y doble fuente de verdad.

---

## 5 · Plan de refactor — sólo si F3 lo justifica

Regla en todas las fases: **nunca dos dueños de una llamada**; el flag decide
uno u otro al abrir la sesión y no cambia en vivo.

| fase | cambio | símbolos reales | pruebas de orden/aislamiento | métrica de éxito | rollback | riesgo principal |
|---|---|---|---|---|---|---|
| **A · caracterizar** | tests que fijan el comportamiento actual, sin tocar producción | `_run_audio_frames`, `process_audio_batch`, `_cancel_active_tts`, `_reserve_agent_speech`, `_start_tracked_speech`/`_speak_response`, `_PacedAudioSender` | orden de frames por sesión con marcas; dos sesiones intercaladas nunca mezclan; barge-in cancela **sólo** el `gen` en vuelo; `turn_seq` viejo aborta | suite verde sobre `api` | n/a | tests que fijan bugs actuales como "correctos" |
| **B · puerto** | extraer `AudioPlane` (interfaz) con adaptador **in-process** que envuelve exactamente lo de hoy; flag `GB_TOPOLOGY=mono` | nuevo `src/server/audio_plane.py` (puerto) + `InProcessAudioPlane`; `websocket_server.py` llama al puerto en los 5 puntos de §1.1 | suite A idéntica; `cycle_p90`/`resume_vad_p90` **sin cambio** a N fijo | mismo K que hoy (rampa corta N=30) | quitar el flag: código previo intacto | overhead de la indirección; tentación de "arreglar" al extraer |
| **C · split en la tarea** | `ProcessAudioPlane`: MW como proceso hijo en la misma tarea Modal, contrato §3 por pipe/UDS; `GB_TOPOLOGY=split` | `src/media_worker/` (loop propio, pool `vad`, `_PacedAudioSender`, `SmartTurnVADProcessor`); Control conserva SM, LLM, `FunctionCallRequest` | suite A + tests del contrato: `Cancel` antes de `Speak`, duplicados, `MediaClosed` durante `Speak`, dos llamadas en un MW | funcional 10/10 con audios reales; barge-in corta en < hoy + 20 ms | `GB_TOPOLOGY=mono` | cancelación distribuida; fuga de MW huérfano |
| **D · comparar K** | misma rampa que F3 (§4 de la estrategia), mono vs split, **mismo contrato ORT, mismos 60 ms** | — | — | K_split ≥ 1,3 × K_mono **y** `submit+resume` bajan a N fijo | descartar C | medir en Modal limpio, no en `.143` |
| **E · tareas separadas** | sólo si D gana **y** (ii) resuelve afinidad | app Modal de Control aparte; rendezvous por `call_id` | reconexión, orden bajo pérdida | K_Control ≫ K_MW; coste por llamada | volver a C | rendezvous **[pendiente de comprobar]** en Modal |

**Criterio de descarte global:** si en D `K_split < 1,3 × K_mono`, se para. El
coste de C no se amortiza con menos.

---

## 6 · Patrones y riesgos

### Diagnóstico primero **[código]**

`websocket_server.py` tiene **9.611 líneas** y una clase que posee: transporte
WS, lectura desacoplada, batching, ciclo y probe, resolución de turno, puente a
la SM, generación y cancelación de TTS, pacer de salida, despacho de funciones al
engine, salud, `/api/campaigns`, limpieza y reaper. Es **un solo dueño de todas
las responsabilidades**. El seam natural ya existe en el código —
`_run_audio_frames` / `process_audio_batch` / `_PacedAudioSender` forman un
bloque cohesivo de "audio" — pero no tiene interfaz: se invoca por atributos de
`SessionState` compartidos.

La duplicación de límite de integración que sí prueba el código: el estado del
TTS se lee desde **tres sitios** con tres mecanismos (`_batch_eligible` mira
`active_tts_task`/`tts_task_start`/`_tts_starting_at`/`tts_audio_playing`/
`vad._tts_playing`; `_reserve_agent_speech` sella `_tts_starting_at`;
`_cancel_active_tts` muta bajo `tts_task_lock`). Cinco banderas para un hecho.
Un split **obliga** a reducirlas a un `gen_id` — lo cual es una mejora aunque
no se haga el split.

### Patrones que aplican (y sólo éstos)

- **Puerto/adaptador** para `AudioPlane` (fase B): permite `mono`/`split` con el mismo llamador. Es el único patrón que el plan necesita introducir.
- **Máquina de estados** para el turno: **ya existe** (`state_machine_engine`); no se crea otra. Lo que falta es que el turno de audio tenga estado explícito en MW (`idle → speech → silence → resolving → ended`) en vez de banderas sueltas.
- **Mediador**: Control ya lo es de hecho; el contrato §3 lo hace explícito.

No se propone reescritura; no se propone broker.

### Riesgos, con la mitigación que el diseño ya lleva

| riesgo | por qué aparece | mitigación en §3/§5 |
|---|---|---|
| **orden de audio** | dos loops, dos colas | un canal FIFO por llamada; `seq` por emisor; bytes nunca cruzan |
| **fuga de sesión** | dos registries | Control dueño lógico; `MediaClosed` obligatorio; reaper en ambos con TTL |
| **buffers huérfanos** | MW muere con `turn_audio` | son bytes locales: mueren con el proceso; Control no los referencia |
| **estado VAD/SmartTurn** | tentación de sincronizarlo | **no cruza**; sólo hechos (`SpeechStart/End`, `TurnEnd`) |
| **cancelación distribuida** | `_cancel_active_tts` era un lock | corte local en MW **antes** del evento; `Cancel{gen_id}` idempotente; `Speak` con `gen_id` monótono |
| **reconexión** | canal MW↔Control cae | MW termina audio y cuelga tras `T_orphan`; no hay estado que reconstruir en Control salvo hechos ya recibidos |
| **TTS duplicado** | `Speak` reenviado | exactamente-una-vez por `gen_id` en MW |
| **degradación de barge-in** | hop añadido | el **corte** no paga hop; sólo la cancelación de LLM. Medir `local_cut_ms` |
| **memoria de modelos** | dos procesos | sólo MW carga ORT (Silero + SmartTurn, sesiones compartidas por wrapper, igual que hoy); Control **cero** |
| **free-threaded** | locks por sesión + un pool | los locks (`_state_lock`, `_voice_ts_lock`, `_inference_gate`) **viajan enteros a MW**; nada nuevo se comparte entre hilos. El límite de proceso **elimina** memoria compartida entre planos |
| **lote de 600 ms / noisereduce** (60 ms) | independiente del split | `inbox_batch_max: 3` si F3 lo muestra; es config |

---

## 7 · Resultado ejecutivo

**Recomendación:** **no hacer el split ahora.** Activar 60 ms en ai-engine
(estrategia separada), correr F3 en Modal limpio con `max_containers=1`, y
decidir con la tabla.

**Condición que lo activa:** en F3 con 60 ms, `submit_p90 + resume_vad_p90`
crecen con N mientras `job_p90`, `steno_p90` y las latencias remotas quedan
planas, **y** K < 100. Si además `frames_per_cycle`/`resolution=fallback`
crecen, primero `inbox_batch_max: 3` — es config y precede al split.

**El menor experimento que lo refuta:** F3 en un solo escalón, N=60, 60 ms,
tarea Modal limpia. Si `resume_vad_p90 < 1 ms` y `cycle_p90 < 5 ms`, el loop no
es el cuello a 60 y el split **no** es la palanca hacia 100.

**Esfuerzo y riesgo**

| | esfuerzo | riesgo | qué lo acota |
|---|---|---|---|
| diseño del contrato + fase A (caracterización) | 3-5 días | bajo | tests que fijan comportamiento actual, sin producción |
| PoC fases B+C (puerto + split en la tarea, bajo flag) | 2-3 semanas | medio-alto | cancelación distribuida; flag `mono` intacto |
| refactor de producción (D+E, tareas separadas) | 6-10 semanas | alto | rendezvous en Modal **[pendiente de comprobar]**; sólo si D ≥ 1,3× |

**Decisiones que NO se toman hasta tener F3/K:**
1. si el split se hace o no;
2. topología (i) vs (ii);
3. si STT queda en MW (§2 lo asume; F3 puede mostrar que Steno pesa en el loop);
4. transporte del contrato (pipe/UDS vs WS);
5. `inbox_batch_max` — su valor correcto con 60 ms sale de F3;
6. `max_inputs`, `max_sessions` (hoy 120 sin validar), `max_containers` — todos derivan de K.

**Pendiente de comprobar (no inventado):** afinidad por `call_id` entre dos
apps Modal; coste real de (e) timers en el loop; comportamiento de
`_prestream_queue` del engine con 480 B bajo pérdida de ACK.
