# Auditoría de refactorización interna — GoldenBridge, rama `api` @ `08932a6`

Fecha: 2026-09-25 · Auditor: Claude (Fable 5.1) · Encargo: mapa de piezas aislables **dentro del proceso actual**, sin mover ejecución, sin procesos separados, sin IPC, sin Modal, sin capacidad.

Etiquetas: `[código]` verificado sobre objetos git de `api` · `[inferencia]` deducido del código · `[pendiente]` exige ejecución o dato externo.
Coordenadas: `WS@L####` = `src/server/websocket_server.py` en `api`; `SMART`, `VAD`, `STENO`, `STT`, `TTS`, `SM`, `MIX`, `MAIN`, `CFG` según §1.

---

## 0. Dictamen

- `[código]` Commit `08932a64645bff74030ad2bd63543dd2334b8101` (autor 2026-08-31, commit 2026-09-03). El checkout local está en `feat/coalescer-integrity-probe`, **mismo commit**, con 63 líneas sin commit (ledger `audio_integrity_probe`, sólo test) y 3 tests sin seguir. Toda la evidencia sale de `git show api:` y `git grep … api`, no del árbol de trabajo.
- `[código]` `websocket_server.py`: 9.611 líneas. `GoldenBridgeServer` (WS@L1085-L9593): **101 métodos, 64 `async`**. Los "123 métodos" de Codex cuentan también los de `_CycleProbe`, `_TTSPacer`, `_OutboundAudioBudget` y `_PacedAudioSender`. `SessionState` (WS@L830-L1083): **105 campos declarados + 1 dinámico** (`_stt_finalize_failures`, creado por `getattr` en WS@L4084).
- `[código]` Diagnóstico: Mediator sobrecargado hasta God Object. 21 sitios de creación de tareas, 24 escrituras a `conversation_history` (8 de ellas codifican el mismo par tool_call/result), 8 `isinstance` sobre proveedores concretos, 30 `hasattr` sobre el VAD, 19 accesos a campos de `stt_connection`, 16 lecturas de `websocket.client_state.name`.
- `[inferencia]` **Primera extracción: el planificador adaptativo** (WS@L2719-L2968). Coincido con Codex en la pieza. Corrijo la interfaz: el consumidor (`_session_processor`) llama a **tres** operaciones más el guard (`_drain_inbox_frames`, `_drain_batch`, `_run_audio_frames`, `_batch_eligible`), no a una; y el conjunto mínimo de dependencias ya está documentado por la fixture `_srv()` de `test_batch_adaptive.py` L58-L68, que hoy invoca los métodos reales con un `SimpleNamespace` como `self`. La extracción debe conservar ese idioma o rompe 24 llamadas de test.
- Cinco hallazgos que el análisis de Codex no recoge (§3.5, §13): doble `finally` anidado en los atajos estáticos del camino streaming; handlers de texto que hablan **en línea** y bloquean la FIFO; espera en línea de hasta 30 s desde la pila de tramas en `_handle_new_user_turn_tts`; `_cancel_active_tts` resetea `tts_cancelled` a los 0,2 s mientras el `finally` de la tarea cancelada aún duerme 300 ms; y la fuga de conexión STT nunca adoptada en cierres no-AMD, que Codex etiqueta como inferencia y cuyo flujo de control es `[código]`.

---

## 1. Alcance

| clave | archivo (rama `api`) | líneas |
|---|---|---|
| WS | `src/server/websocket_server.py` | 9.611 |
| MAIN | `src/main.py` | 633 |
| SMART | `src/processors/smart_turn_processor.py` | 2.108 |
| VAD | `src/processors/vad_processor.py` | 607 |
| STENO | `src/processors/steno_stt_processor.py` | 1.201 |
| STT | `src/processors/stt_processor.py` | 455 |
| TTS | `src/processors/tts_processor.py` | 1.415 |
| SM | `src/campaign/state_machine_engine.py` | 1.755 |
| MIX | `src/audio/background_mixer.py` | 335 |
| OBS | `src/observability.py` | 119 |
| CFG | `config/default.yaml` | — |

**Configuración versionada** `[código]`: `read_decouple: true` (CFG L45) · `inbox_maxsize: 0` (L46) · `inbox_batch_max: 10` (L72) · `cycle_probe: false` (L80) · `vad.inference_workers: 4` (L133) · `vad.frame_offload: true` (L142) · `vad.use_smart_turn: true` (L143) · `vad.watchdog_enabled: true` (L148) · `vad.stop_secs: 0.6` (L127) · `vad.sample_rate: 8000` (L129) · `stt.prepared_audio: true` (L210) · `barge_in.min_tts_time: 1.2` (L168).
`[pendiente]` El servicio carga `config/active.yaml` **sin merge** (CFG L42-L44). CLAUDE.md registra `cycle_probe: true` desplegado. La configuración efectiva no se auditó; todo "camino legacy" de este documento se clasifica por la versionada.

**Inyección** `[código]`: `MAIN:create_processors` L115-L399 construye VAD, STT, LLM, TTS, router y AMD; MAIN L548-L572 los pasa al constructor de `GoldenBridgeServer` junto con 13 bloques de configuración.

**Tests** `[código]`: 699 funciones `test_` en 54 archivos. Ningún test construye el servidor completo salvo `test_scaling_retired.py` L37 (HTTP, sin WebSocket). Tres idiomas de construcción: (1) método real desenlazado sobre un stub o `SimpleNamespace` (14 archivos, ~25 enlaces); (2) `MagicMock(spec=GoldenBridgeServer)` con métodos reales vía `__get__` (4 archivos, 21 enlaces); (3) `GoldenBridgeServer.__new__` (2 archivos). **Ningún test abre `/v1/agent/converse`**; `load_test.py` es un script manual.

**Límites**: no se ejecutó la suite (checkout en otra rama con cambios locales) `[pendiente]`; no se auditó `active.yaml`; no se modificó código.

---

## 2. Mapa de dependencias internas de `GoldenBridgeServer`

```
MAIN:create_processors ──inyección──▶ GoldenBridgeServer.__init__          WS@L1090
                                        │ FastAPI /health · REST campañas · WS endpoint   WS@L2051-L2122
                                        ▼
                               _handle_connection                           WS@L2124
                                 ├ admisión + registro (sessions, _sessions_lock)   L2134-L2166
                                 ├ VAD por sesión (create_session_instance)         L2174-L2188
                                 ├ mixer por sesión (clone_for_session | reset_position compartido)  L2190-L2198
                                 ├ span de llamada                                   L2206
                                 ├ read_decouple ─┬─ _session_reader (task)          L2520   socket → inbox FIFO (+sentinel)
                                 │                └─ _session_processor              L2595   único consumidor
                                 │                     ├ texto ──▶ _handle_text_message  L2970
                                 │                     │             ├ _handle_settings        L3024  (bootstrap: campaña, STT/TTS connect, greeting EN LÍNEA, tareas)
                                 │                     │             ├ _handle_keepalive        L3470
                                 │                     │             ├ _handle_inject_agent_message L3478 → _speak_response EN LÍNEA
                                 │                     │             └ _handle_function_response L3489 → LLM + _speak_response EN LÍNEA
                                 │                     └ audio ──▶ planificador  L2719-L2968
                                 │                                   carry → _drain_inbox_frames → _drain_batch → _run_audio_frames
                                 │                                     ├ 1 trama: _handle_audio_message L3647 (pre → vad.process_audio → post)
                                 │                                     └ N tramas: _handle_audio_batch  L2807 (pre×N → vad.process_audio_batch → post×consumidas)
                                 │                                          ├ _audio_pre_vad   L3569  (adopta STT connect; buffers)
                                 │                                          └ _audio_post_vad  L3679  (speech-start, live STT, banking, barge-in, cierre)
                                 │                                                ├ _cancel_active_tts       L8445
                                 │                                                └ _resolve_turn_complete    L3924  ◀── también _turn_close_watchdog L8982
                                 │                                                      ├ _handle_new_user_turn_tts L8205 (cancela o ESPERA hasta 30 s)
                                 │                                                      ├ _process_speech_streaming L3978 (Finalize → fallback transcribe)
                                 │                                                      └ _process_speech           L4242 (transcribe legacy)
                                 │                                                            ▼
                                 │                                                   _process_transcript  L4443  ◀── _handle_auto_flush_transcript L4379, _flush_fragment_watchdog L4433
                                 │                                                      ├ absorber terminal · gracia post-TTS · AMD (_handle_answering_machine L9376)
                                 │                                                      ├ history · SM.process_user_message · opener (_start_tracked_speech)
                                 │                                                      ├ IntentRouter → SM.execute_transition
                                 │                                                      └ create_task(_process_llm_tts_streaming L4830) + on_tts_done → _post_turn_cleanup L9007
                                 │                                                              ├ _speak_response L7216 (atajo estático / breaker / strict)
                                 │                                                              ├ _dispatch_tool_call_batch L6311 → _handle_function_call L6491 · _handle_internal_function L6560 → _generate_state_followup L7024
                                 │                                                              ├ _PacedAudioSender L584 → _safe_send_bytes L9256
                                 │                                                              └ finally → _run_terminal_on_exit L6043 → _release_worker_sessions L6094
                                 └ finally (teardown)                                L2297-L2515
Timers/callbacks que entran desde fuera de la FIFO:
  _turn_close_watchdog L8918 · _monitor_user_silence L8757 · _no_interaction_guard L8876 · _stream_background_audio L8635
  · _flush_fragment_watchdog L4413 · STENO auto-flush (L603) → _handle_auto_flush_transcript
  · SM timeout → inject_prompt L9494 · SM acciones → execute_function L9416 / get_call_metadata L9400
  · _idle_session_reaper L1390 → _evict_session L1424
```

**Callers verificados (grep sobre `api`, sólo sitios productivos)** `[código]`:

| símbolo | callers (WS@L) |
|---|---|
| `_process_transcript` | 4178 (Finalize) · 4313 (legacy) · 4379 (auto-flush) · 4433 (watchdog de fragmento) |
| `_resolve_turn_complete` | 3897 (trama) · 8982 (watchdog) |
| `_process_speech_streaming` / `_process_speech` | 3916/3918 (Silero-only) · 3974/3976 |
| `_speak_response` | 1854 · 2028 · 2049 · 3425 · 3487 · 3561 · 5137 · 5592 · 5650 · 7013 · 7191 · 8197 · 8814 · 8852 · 9563 (15 sitios) |
| `_start_tracked_speech` | 4126 (re-prompt sordo) · 4579 (opener) · 6290 · 7137 (no-streaming, muertos según comentario L7169-L7173) |
| `_process_llm_tts_streaming` | 4782 (task) · 7106 (follow-up, en línea dentro de la tarea) |
| `_cancel_active_tts` | 3886 (barge-in) · 8241 (nuevo turno) |
| `_reserve_agent_speech` | 1866 · 4859 · 7247 · 8055 |
| `execute_function` | 8862 (farewell) · 9394/9395 (AMD) · SM L837, L939 |
| `inject_prompt` / `get_call_metadata` | SM L934 · SM L1625 |
| `_handle_function_call` | 6339 · 6429 · 6930 |
| `_handle_internal_function` | 6412 · 6457 · 6519 · 6620 · 6691 · 6714 (recursivo) |
| `_dispatch_tool_call_batch` | 3553 · 5545 · 5689 · 6283 · 7130 · 8190 |
| `_release_worker_sessions` | 1444 (evict) · 6092 (terminal) · 8874 (farewell) · 9397 (AMD) |
| `_run_terminal_on_exit` | 6041 · 7602 · 9037 |
| `_safe_send_bytes` | 648 · 800 (sender) · 1907 (greeting caché) · 5937 (error hablado) · 8001 (filler inline) · 8702/8714 (fondo) |
| `_reset_vad_turn_state` | 4925 · 7344 · 8077 (un test cuenta estos 3 sitios por grep del fuente: `test_vad_reset_regression.py` L345) |
| `_trim_conversation_history` | 21 sitios |
| `_sessions_lock` | 1305 · 1397 · 1457 · 2134 · 2479 · 9443 · 9524 (`get_call_metadata` L9408 lee **sin** lock) |

---

## 3. Diagnóstico previo a patrones

### 3.1 SRP y God Object

`[código]` La clase necesita "y" once veces para describirse: transporte WS **y** admisión **y** bootstrap de Settings **y** planificación de audio **y** reacciones al DSP **y** estrategias STT **y** política conversacional **y** despacho de herramientas **y** ciclo de habla **y** lifecycle/teardown **y** cachés globales **y** observabilidad. Señales: 101 métodos; `_process_llm_tts_streaming` de 1.213 líneas (L4830-L6042); `_handle_settings` de 446 (L3024-L3469); `_process_transcript` de 387 (L4443-L4829); `_handle_connection` de 396 con un `finally` de 218 líneas.

`[inferencia]` No es un Facade que delegue: contiene política y estado temporal (barreras, generaciones, ventanas de gracia). Es un **Mediator** al que se le fueron acumulando políticas. El problema es de ownership y contratos temporales cruzados, no de "falta de patrones".

### 3.2 Estado global

`[código]`
- `sessions` + `_sessions_lock` (WS@L1304-L1305); cachés `greeting_cache`, `_global_filler_cache`, `_script_audio_cache`, cada una con `asyncio.Lock` (L1288-L1301); bandera `_greetings_precomputed` (L1322).
- Pool de hilos VAD global, 4 workers, creación perezosa bajo `_VAD_EXECUTOR_LOCK` (VAD L38-L73; `configure_vad_executor` desde MAIN L143-L144).
- Smart-turn: sesión ORT, `feature_extractor` e `_inference_lock` (`threading.Lock`) **compartidos por referencia** entre clones (SMART L143-L145); ORT en `ORT_SEQUENTIAL`, `inter_op=1`, `intra_op` desde `GB_ORT_INTRA_OP` (SMART L119-L121). La inferencia smart-turn corre en `run_in_executor(None, …)`, es decir en el **executor por defecto del loop, no en el pool `vad`** (SMART L1097-L1100, L1225-L1230), bajo `_inference_gate` (asyncio.Lock por sesión).
- Silero: sesión ORT compartida por `copy.copy` del wrapper, estado LSTM y contadores por instancia (VAD L189-L231); `_infer_lock` es `asyncio.Lock` por sesión (VAD L161).
- `gc.collect()` en el hilo del loop desde el `finally` de cada cierre y tras cada turno streaming, limitado a una vez por 60 s (WS@L1343, L1357, L2484, L9005).

`[inferencia]` Nada de esto es un antipatrón por sí mismo. La frontera intocable es **modelo compartido / estado de señal por sesión** (verificada por `test_aislamiento_entre_sesiones.py` L119-L171 y `test_vad_executor.py` L230).

### 3.3 Fuga de infraestructura

`[código]`
- `isinstance(self.stt_processor, StenoSTT)` / `UrsulaProcessor`: WS@L2402, L2432, L3318, L3350, L6136, L6140, L6210, L6227.
- `hasattr(self.*_processor, …)`: 12 sitios. `hasattr(vad, …)`: 30 sitios; `_reset_vad_turn_state` (L280-L327) escribe 10+ campos privados del VAD por `hasattr`.
- Campos de `stt_connection` leídos/escritos desde el servidor: `connected`, `streaming_paused`, `_last_finalize_time` en 19 sitios (p. ej. L3601, L3749, L6026-L6027, L7587-L7588, L8600).
- Campos privados del VAD escritos desde el servidor: `_fallback_start_time` (L8511), `_tts_playing` leído en el guard (L2751), `_barge_in_enabled` (L9238), `_probe_*` (L2927-L2934).
- `websocket.client_state.name` en 16 sitios; `self.tts_processor._http_client` y `ursula_base_url` en la ruta `/api/voices` (L2062-L2066).
- `websocket` viaja como parámetro por 20 métodos **y** vive en `session.websocket` (L2163); sólo `execute_function` e `inject_prompt` leen el campo (L9450, L9531).

`[inferencia]` Hay adapters parciales (Strategy por proveedor inyectado) pero ningún límite estable. Crear una jerarquía abstracta ahora mentiría sobre contratos que difieren (§8).

### 3.4 Integración duplicada

Resumen (detalle en §8): cleanup de proveedores en **tres** rutas; ciclo de habla en **cinco** variantes; resolución de turno STT con **cuatro** entradas a `_process_transcript`; emisión de FunctionCall en **dos** emisores + interno; LLM posterior a función en **dos**; codificación del par tool_call/result en **8 sitios** (L6545, L6643, L6748, L6779, L6832, L6866, L6942, L9471); pacing de salida en **tres** formas (sender pacer, pacer inline en `_play_filler_audio` L7960, ninguno en `_send_cached_greeting` L1907).

### 3.5 Hallazgos adicionales (no recogidos por Codex)

**H1. Doble `finally` anidado en los atajos estáticos del streaming** `[código]`. `_process_llm_tts_streaming` llama **en línea** a `_speak_response` en tres puntos (L5137 atajo estático, L5592 breaker, L5650 strict) y luego hace `return`. El `finally` de `_speak_response` (L7553-L7602) y el de `_process_llm_tts_streaming` (L5973-L6041) ejecutan **ambos** la secuencia de salida: `post_tts_silence_ms` duerme dos veces (L7582 y L6021); `tts_last_complete_time` se sella dos veces (L7580 y L6019, la segunda ~300 ms después); `_reset_timeout_timer` y `_run_terminal_on_exit` corren dos veces (idempotentes por `_terminal_on_exit_executed`, SM L144, y por `_worker_sessions_released`); `active_tts_task` se anula en el `finally` interno (L7564) **mientras la tarea externa sigue viva**. `[inferencia]` Entre L7564 y L6002 `_batch_eligible` ya devuelve True (todas sus banderas están limpias), de modo que las mutaciones restantes del `finally` externo (`set_tts_playing(False)`, `streaming_paused=False`, `_reset_timeout_timer`) pueden caer a mitad de un lote. Efecto probablemente benigno, pero rompe la afirmación "las mutaciones del ciclo TTS caen entre lotes". La ventana de gracia post-TTS (500 ms por defecto, L4475) queda medida desde el segundo sello. Cualquier unificación del lifecycle TTS cambia este comportamiento salvo que se caracterice primero.

**H2. Handlers de texto que hablan en línea y bloquean la FIFO** `[código]`. `_session_processor` `await`ea `_handle_text_message` (L2639). Dentro: `_handle_settings` habla el greeting en línea (L3425 `_speak_response`, L3431 `_deliver_greeting`), `_handle_inject_agent_message` (L3487) y `_handle_function_response` (LLM no-streaming L3547 + `_speak_response` L3561) también. Mientras dura esa habla (síntesis + `_wait_for_tts_playback` + 300 ms), **no se consume el inbox, no corre VAD, no hay barge-in por tramas** y el inbox (ilimitado) crece. El camino de transcript, en cambio, crea una tarea (L4781). `[inferencia]` Asimetría de contrato entre los dos orígenes de habla; cualquier caracterización de `_session_processor` debe fijarla (Codex sólo propone verificar orden).

**H3. Espera en línea de hasta 30 s desde la pila de tramas** `[código]`. `_handle_new_user_turn_tts` (llamado desde `_audio_post_vad` → `_resolve_turn_complete`, L3970) en la rama "barge-in deshabilitado o gates no cumplidos" hace `await asyncio.wait_for(session.active_tts_task, 30.0)` (L8273). Si ocurre dentro de `_handle_audio_batch`, `_frames_in_flight` permanece > 0 y `_batch_idle` cerrado durante toda la espera; cualquier `_reserve_agent_speech` desde un timer (silencio, guard, `inject_prompt`) agota el backstop de 1 s y procede "FIX-E bypassed" (L2710-L2717). No es un arranque de habla (los cuales sí se convirtieron a tarea), es una **espera** de habla con el mismo efecto sobre la barrera. `[pendiente]` Frecuencia en producción (log "Agent-speech barrier timed out").

**H4. `tts_cancelled` se resetea antes de que termine el `finally` cancelado** `[código]`. `_cancel_active_tts` espera la tarea sólo 0,2 s (L8548) y luego pone `tts_cancelled=False` y limpia `cancellation_event` (L8584-L8585). El `finally` de la tarea cancelada duerme `post_tts_silence_ms` (300 ms, L6023/L7584) después de anular `active_tts_task`, así que sus pasos posteriores (`_flush_talkover_head_on_natural_end` L6031, `_run_terminal_on_exit(after_cancel=session.tts_cancelled)` L6041/L7602) leen **False**. El propio código documenta que la bandera "no es fiable para comprobaciones post-tarea" (L8104-L8106). `[inferencia]` La gracia terminal tras cancelación (2.500 ms) depende de qué camino gane la bandera `_terminal_on_exit_scheduled`; `test_terminal_cancel_close.py` L128 fija "un solo hangup", no cuál gracia.

**H5. Fuga de conexión STT nunca adoptada en cierres no-AMD** `[código]` (flujo) · `[pendiente]` (efecto remoto). `session.stt_connection` sólo se adopta en la **primera trama de audio** (L3586-L3589). `_release_worker_sessions` cierra huérfanos únicamente con `reason == "amd_hangup"` (L6165-L6169); para `idle_evict`, `terminal_state`, `farewell_hangup` cancela, `await`ea, descarta el resultado y pone el atributo a None (L6154-L6163). El `finally` del handler hace lo mismo (L2414-L2426). Camino concreto: reaper "no-audio" (L1401-L1410, sesión sin una sola trama) → `_evict_session` → conexión Steno completada y nunca adoptada → nadie la cierra desde GoldenBridge. `test_worker_session_release.py` L196 (`terminal_state_leaves_tts_orphan_untouched`) **fija el comportamiento actual**, así que cambiarlo no es "sin cambio funcional".

**H6. `flush_tts_session` en barge-in es un no-op por defecto** `[código]`. WS@L8556 llama `flush_tts_session`; TTS L583 retorna si `_send_flush_on_cancel` está apagado, y el comentario TTS L179-L182 dice que por defecto lo está. La cancelación remota efectiva es `cancel_ursula_session` fire-and-forget (L8567-L8572).

**H7. Atributo de sesión no declarado** `[código]`. `_stt_finalize_failures` nace por `getattr(session, …, 0) + 1` en L4084 y se lee en L4088-L4136 y L4230. No está en `SessionState`. Cualquier inventario de estado que se base en el dataclass lo omite.

**H8. Test que lee el fuente** `[código]`. `test_vad_reset_regression.py` L345-L365 cuenta por grep del texto de `websocket_server.py` los tres sitios de `_reset_vad_turn_state`. Mover cualquiera de L4925, L7344, L8077 a otro módulo rompe ese test aunque el comportamiento sea idéntico.

---

## 4. Piezas candidatas a aislar dentro del proceso

| # | Pieza candidata | Responsabilidad única (¿coherente?) | Símbolos actuales | Callers | Estado que posee hoy | Dependencias entrantes / salientes | Efectos laterales | Invariantes | Riesgo de extracción |
|---|---|---|---|---|---|---|---|---|---|
| 1a | **Lector de socket** | Recibir, sellar llegada, encolar. **Coherente.** | `_session_reader` L2520 · `_SENTINEL_DISCONNECT` L74 | `_handle_connection` L2226 | escribe `inbox`, `_ws_disconnected`, `_last_audio_arrival`, `_max_arrival_gap_ms`, `_max_payload_bytes`, `inbox_depth_max`, `_inbox_full_waits` | entra: `websocket.receive`; sale: `inbox.put` | ninguno externo | sentinel siempre detrás de lo recibido; nunca descarta; bloquea si `inbox_maxsize>0` | **Bajo** (75 líneas, un caller, sin tests: fase 0 primero) |
| 1b | **Consumidor FIFO** | Un consumidor por sesión, orden carry → pendiente → inbox, dispatch texto/audio. Coherente **salvo** por H2 (habla en línea de los handlers de texto). | `_session_processor` L2595 | `_handle_connection` L2230 | crea `_batch_carry`, `_batch_idle`, `_cycle_probe`; local `pending_msg` | sale: `_handle_text_message`, `_drain_inbox_frames`, `_drain_batch`, `_run_audio_frames`, `_send_message` | error protocolar al cliente | inbox nunca tocado con carry o pendiente vivos (L2611-L2620); descarte de audio post-desconexión sólo | **Medio** (sin tests; H2) |
| 1c | **Admisión + bootstrap** | Registro, VAD/mixer por sesión, Settings, campaña, conexiones, greeting, tareas. **Mezcla 5 responsabilidades.** | `_handle_connection` L2124-L2296 · `_handle_settings` L3024 | endpoint L2122 · `_handle_text_message` L3010 | identidad, `settings`, formatos, `variables`, `campaign`, `_stt_connect_task`, `_stt_warm_task`, `_tts_connect_task`, `background_audio_task`, `silence_monitor_task`, `turn_watchdog_task`, `no_interaction_guard_task` | `CampaignLoader`, `validate_campaign`, `CampaignStateMachine.set_action_executor(self)`, `vad.apply_overrides`, `StenoSTT.connect_session`, `UrsulaProcessor.connect_tts_session`, `precompute_greetings` (global, una vez) | 4 tareas por sesión + 2 connect + 1 warm; cierre 1008 en campaña inválida | `SettingsApplied` antes de conectar STT (L3314 → L3319); VAD por sesión obligatorio (L2186) | **Alto** |
| 1d | **Teardown** | Cancelar tareas, cerrar proveedores, liberar memoria, desregistrar. Coherente pero **duplicado** con 6a. | `finally` L2297-L2515 · `_evict_session` L1424 | — | anula 12 campos | `cancel_ursula_session`, `disconnect_session`, `disconnect_tts_session`, `state_machine.cleanup`, `_periodic_cleanup` (gc) | `gc.collect` global | orden: TTS activo → Ursula → tareas → SM → warm → STT → TTS → buffers → registro | **Medio-alto** (§8.A) |
| 2 | **Planificador adaptativo de audio** | Formar lotes, carry, fallback por trama, contabilidad y fusible. **Coherente.** | `_batch_eligible` L2719 · `_drain_inbox_frames` L2755 · `_drain_batch` L2781 · `_run_audio_frames` L2789 · `_handle_audio_batch` L2807 (+ `_CycleProbe` incrustado L2828-L2965) | `_session_processor` L2647, L2652 · `_batch_eligible` desde `_drain_*` | lee/escribe `_batch_carry`, `_frames_in_flight`, `_batch_idle`, `_batch_disabled`, `_max_batch_frames`, `_batch_jobs`, `_batch_frames_total`, `_batch_cuts`; lee `_tts_starting_at`, `active_tts_task`, `tts_task_start`, `tts_audio_playing`, `_stt_connect_task`, `vad._frame_offload`, `vad._tts_playing` | entra: `_session_processor`; sale: `_audio_pre_vad`, `vad.process_audio_batch`, `_audio_post_vad`, `_handle_audio_message`, `_send_message` | error protocolar; log del fusible | pre una vez por trama (`pre_done`); post en orden y sin intercalar; `_frames_in_flight=0` y `_batch_idle.set()` en `finally` (L2967-L2968); carry encabeza el siguiente lote | **Medio** (mejor cubierto: 33 tests) |
| 2' | **Barrera de habla** (compañera de 2, **no** moverla) | Sellar reserva y esperar lote en vuelo. | `_reserve_agent_speech` L2679 · `_TTS_STARTING_TTL` L85 · `_AGENT_SPEECH_BARRIER_TIMEOUT` L186 | 1866 · 4859 · 7247 · 8055 | escribe `_tts_starting_at` | lee `_frames_in_flight`, `_batch_idle` | log ruidoso al agotar backstop | sello **antes** del primer `await` (L2703) | Queda en el servidor |
| 3a | **Pre-DSP** | Bookkeeping y buffers antes del job. Coherente **salvo** la adopción lazy del STT (L3586-L3612). | `_audio_pre_vad` L3569 | 2837 (lote) · 3664 (trama) | `last_activity`, `audio_in_*`, `audio_buffer`, `_audio_buffer_bytes`, `stt_connection` (adopción), `_stt_connect_task` | `_wire_stt_connection_hooks`, `vad.set_sensitive_mode` | recorte del buffer a la mitad (L3636-L3639) | **no idempotente** (cuenta y acumula) | Bajo si se separa la adopción STT |
| 3b | **Post-DSP (reacciones)** | Interpretar el resultado y ejecutar efectos. **Mezcla**: eventos de habla, envío/banking STT, barge-in, cierre de turno, métricas, reset de timeout de campaña. | `_audio_post_vad` L3679 · `_handle_audio_message` L3647 | 2889 (lote) · 3677 (trama) | `is_speaking`, `speech_start_time`, `pre_context_buffer`, ventana de diagnósticos, `last_user_activity`, `silence_prompt_count` | `stt_processor.send_prepared_audio/send_audio_chunk/reconnect_session`, `state_machine._reset_timeout_timer`, `_cancel_active_tts`, `_resolve_turn_complete`, `_send_message` | envíos a Steno, `UserStartedSpeaking` | post_i banca, post_j (j>i) vacía: sin intercalar (L3690-L3693); `stt_prepared` ≡ crudo | **Alto** |
| 3c | **DSP por sesión** (ya extraído) | Silero + smart-turn + resampleo + turn_audio | `SmartTurnVADProcessor` (SMART L278) · `SileroVADProcessor` | 2843 · 3672 · 8980 | todo el estado de señal + `_state_lock` RLock, `_inference_gate`, `_voice_ts_lock` | pool `vad` global; executor por defecto para smart-turn | — | `_state_lock` sostenido durante todo el lote (SMART L1140); corte en primer evento (L1145-L1151) | **No mover** |
| 4a | **Resolución de turno STT** | Convertir un cierre en una transcripción. **Mezcla** 4 entradas con contratos distintos (§8.B). | `_resolve_turn_complete` L3924 · `_process_speech_streaming` L3978 · `_process_speech` L4242 · `_handle_auto_flush_transcript` L4315 · `_hold_flush_fragment` L4381 · `_consume_pending_flush_fragment` L4396 · `_flush_fragment_watchdog` L4413 · `_reconnect_stt` L4218 · `_wire_stt_connection_hooks` L4180 · `_warm_stt_finalize` L8339 | 3897 · 8982 · STENO L603 (callback) | `turn_seq`, `is_speaking`, `speech_end_time`, `audio_buffer`, `_auto_flush_turn_time`, `_finalize_inflight`, `_pending_flush_fragment`, `_pending_flush_watchdog`, `_last_streaming_turn_time`, `_stt_finalize_failures` (dinámico), `_deaf_reprompts_sent`, `has_real_user_turn`, `is_first_user_response` | `send_finalize` (lock + 3 s, STENO L627-L643) · `transcribe` (open 15 s, lectura sin tope, STENO L1111-L1132) · `discard_pending` · `_start_tracked_speech` · `_handle_new_user_turn_tts` | `UserStoppedSpeaking`; reconexión STT en tarea; re-prompt sordo | `is_speaking=False` antes del primer `await` (L3938-L3940); `turn_seq++` sólo en L4005 y L4255; purga sólo con transcript | **Alto** |
| 4b | **Política conversacional** | Transcript → respuesta o acción. **Mezcla**: absorber, gracia, AMD, history, SM, opener, router, extracción, transición, compresión, LLM. | `_process_transcript` L4443 · `_trim_conversation_history` L1669 · `_compress_history_post_transition` L1705 | 4 entradas | `first_transcript_time`, `amd_*`, `conversation_history`, `was_interrupted`, `initial_static_text_fired`, `variables` | `amd_detector.classify`, `sm.process_user_message`, `intent_router.classify_intent`, `sm.execute_transition`, `sm.emit_partial_extraction`, `_start_tracked_speech`, `create_task(_process_llm_tts_streaming)` | `ConversationText(user)`, `AgentThinking`; tarea de habla | extracción antes de transición (L4698-L4718); a lo sumo una transición del router + una auto | **Muy alto** |
| 4c | **Despacho de herramientas** | Ordenar y ejecutar tool calls. Coherente en `_dispatch_tool_call_batch`; `_handle_internal_function` mezcla transición + history + atajo estático + follow-up. | `_dispatch_tool_call_batch` L6311 · `_handle_function_call` L6491 · `_handle_internal_function` L6560 · `_generate_state_followup` L7024 · `_is_transition_like` L6303 · `execute_function` L9416 · `_handle_function_response` L3489 | ver §2 | `sm_pending_function_ids`, `conversation_history` | `sm.handle_extract_data`, `sm.execute_transition`, `sm._check_flex_completion`, `create_function_call_request`, `_send_message`, `_speak_response` (L7013 en línea), `_filler_then_followup` | `FunctionCallRequest` al cliente | extracts → externos → una transición → follow-up (L6379-L6489); sin timeout de respuesta | **Alto** (2 emisores, §8.D) |
| 5 | **Ciclo de habla del agente** | Sintetizar, ordenar, cancelar. **Cinco variantes** de la misma secuencia entrada/salida (§8.C). | `_process_llm_tts_streaming` L4830 · `_speak_response` L7216 · `_start_tracked_speech` L7158 · `_filler_then_followup` L8012 · `_play_filler_audio` L7925 · `_play_streaming_filler` L7637 · `_send_cached_greeting` L1832 · `_cancel_active_tts` L8445 · `_write_interrupted_history` L8283 · `_handle_new_user_turn_tts` L8205 · `_flush_turn_audio_to_stt` L8383 · `_flush_talkover_head_on_natural_end` L7604 · `_wait_for_tts_playback` L9165 · `_make_paced_sender` L9080 · `_PacedAudioSender` L584 (extraído) · `_ActiveSpeech` L814 · `_TTSPacer` L366 | ver §2 | `active_tts_task`, `tts_task_lock`, `cancellation_event`, `tts_cancelled`, `cancel_reason`, `was_interrupted`, `_reentry_pending`, `active_speech`, `history_written_on_cancel`, `tts_audio_playing`, `tts_task_start`, `tts_last_complete_time`, `_tts_starting_at`, `active_tts_stream`, `tts_connection`, `_tts_connect_task`, `ursula_session_id`, `_terminal_on_exit_scheduled`, `turn_seq` (lectura) | `tts_processor.synthesize` (semáforo 4, TTS L769), `flush_tts_session` (no-op por defecto), `cancel_ursula_session`, `vad.set_tts_playing/set_barge_in_mode/get_turn_audio/clear_turn_audio`, `_reset_vad_turn_state`, `stt_connection.streaming_paused`, `sm._cancel_timeout_timer/_reset_timeout_timer`, `_run_terminal_on_exit` | audio al cliente; `AgentStartedSpeaking`/`AgentAudioDone`/`ConversationText`; history | orden de bytes por sender único; `AudioDone` tras drenar (L5880-L5882); generación estancada aborta (L7255); history escrita en el cancel antes del próximo LLM (L8521-L8525); cabeza de barge-in bajo lock del VAD y flush tras reanudar STT (L8493-L8611) | **Muy alto** |
| 6a | **Liberación de proveedores** | Cerrar STT/TTS/Ursula una vez. **Coherente e idempotente.** | `_release_worker_sessions` L6094 · `_run_terminal_on_exit` L6043 | 1444 · 6092 · 8874 · 9397 | `_worker_sessions_released`, anula 5 campos | `disconnect_session`, `disconnect_tts_session`, `cancel_ursula_session` (timeouts 2/2/1 s) | ninguno fuera de proveedores | idempotente; nunca lanza; huérfanos sólo AMD (H5) | **Bajo** para el primitivo; **medio-alto** para consolidar con 1d |
| 6b | **Guardias y timers** | Silencio, sin interacción, watchdog de cierre, fondo. Cada uno coherente. | `_monitor_user_silence` L8757 · `_no_interaction_guard` L8876 · `_farewell_and_hangup` L8840 · `_turn_close_watchdog` L8918 · `_stream_background_audio` L8635 · `_idle_session_reaper` L1390 · `_evict_session` L1424 | creados en `_handle_settings` L3387-L3468; reaper en L2129 | referencias de tareas; `silence_prompt_count`, `last_user_activity`, `first_transcript_time`, `outbound_budget` | `_speak_response` (en línea desde timer), `execute_function("hangup_call")`, `vad.check_watchdog_close`, `mixer.generate_standalone_chunk`, `_safe_send_bytes`, `_resolve_turn_complete` | habla y hangup desde fuera de la FIFO | watchdog no cierra con lote en vuelo ni carry ni inbox no vacío (L8971-L8979); watchdog fuera durante TTS | **Bajo-medio** (buena cobertura) |
| 7 | **Observabilidad** | Eventos de protocolo, contadores, span, probe. Coherente en emisión; el probe está incrustado en 2. | `_emit_event` L9355 · `_safe_event_attrs` L9318 · `_send_message` L9365 · contadores en `_safe_send_bytes` L9282 · `_CycleProbe` L119 · `start_voice_call_span` L2206 · logs de cierre L2486-L2515 | transversal | `audio_in_*`, `audio_out_*`, `_cycle_probe`, diagnósticos de llegada | OBS `emit_protocol_event`; campos `_probe_*` del VAD | logs | "out" se emite **antes** del envío y sin leer el `False` de `_safe_send_text` (L9369-L9370): describe intentos; contadores de bytes sólo tras envío efectivo (L9282) | **Bajo** |

---

## 5. Evaluación por pieza

### 5.1 Transporte y ciclo de sesión (1a-1d)

- **¿Coherente?** No como bloque. Son cuatro responsabilidades: lector (1a), consumidor (1b), admisión/bootstrap (1c), teardown (1d). El lector es coherente; `_handle_connection` no.
- **Estado local a `SessionState`**: `websocket`, `inbox`, `_ws_disconnected`, relojes de llegada, profundidad, contadores. El registro `sessions` sigue en el servidor.
- **Inyección posible**: `websocket.receive` (o el propio `websocket`), `_handle_text_message`, `_drain_inbox_frames`, `_drain_batch`, `_run_audio_frames`, `_send_message`, reloj. El sentinel debe seguir siendo el mismo objeto de módulo (lo comparan por identidad L2622 y L2772 y lo usa `test_batch_adaptive.py` L333).
- **Acoplamiento**: `_sessions_lock`; tarea del lector cancelada en el `finally` del processor (L2231-L2238); `asyncio.Queue`; el `finally` de 218 líneas; `_periodic_cleanup` (gc).
- **Tests existentes**: **ninguno** referencia `_handle_connection`, `_session_reader`, `_session_processor`, `_handle_settings`, `_handle_text_message`. `test_batch_adaptive.py` L315-L372 mete `{"bytes"}`, `{"text"}` y el sentinel en una `asyncio.Queue` real pero sólo ejercita `_drain_*`.
- **Extracción mínima sin cambio semántico**: sólo `_session_reader` (receive → sello → put → sentinel). `_session_processor` queda como dueño del dispatch.
- **Prueba de caracterización previa**: WebSocket falso con secuencia `Settings → audio A → audio B → texto → audio C → disconnect`; verificar (i) orden exacto de `pre/vad/post/text`, (ii) sentinel al final, (iii) descarte de audio tras `_ws_disconnected` y conservación del texto, (iv) cancelación del lector, (v) **H2**: un handler de texto que `await`ea habla bloquea el consumo de audio hasta que termina, (vi) `inbox_maxsize>0` bloquea al lector en vez de descartar.

### 5.2 Ingesta y planificación de audio (2)

- **¿Coherente?** Sí. Es la pieza más cohesionada: cinco métodos, un caller, invariantes escritas en docstrings y verificadas por tests.
- **Estado local a `SessionState`**: `_batch_carry`, `_batch_idle`, `_frames_in_flight`, `_batch_disabled`, `_tts_starting_at`, los contadores `_batch_*`, `_max_batch_frames`. No duplicar nada de esto en el colaborador.
- **Inyección posible** (es exactamente lo que la fixture `_srv()` de `test_batch_adaptive.py` L58-L68 provee o parchea): `_inbox_batch_max`, `_batch_eligible`, `_audio_pre_vad`, `_audio_post_vad`, `_handle_audio_message`, `_send_message`, `session.vad_processor.process_audio_batch`. El probe se lee por `getattr(session, "_cycle_probe")` (L2828), pensado para stubs.
- **Acoplamiento**: `await` a `vad.process_audio_batch` (SMART L1159, sostiene `_state_lock` todo el lote); `_reserve_agent_speech` lee `_frames_in_flight`/`_batch_idle` (L2704-L2708); el watchdog los lee (L8978); `_batch_eligible` lee 6 banderas del ciclo TTS (L2742-L2752).
- **Tests existentes**: `test_batch_adaptive.py` (33; L105-L308 y L719 a nivel procesador, L315-L693 a nivel servidor con los métodos **reales** de esta pieza y stubs para pre/post), `test_batch_speak_deadlock.py` (5; barrera real, batch "espejo" reimplementado), `test_cycle_probe.py` (4), `test_frame_offload.py` (12, procesador), `test_turn_watchdog.py` (gates que leen `_frames_in_flight`/`_batch_carry`).
- **Extracción mínima**: mover L2719-L2968 a un colaborador (o funciones de módulo) cuyo `self`/contexto exponga **sólo** los siete nombres de arriba; dejar en `GoldenBridgeServer` cinco delegadores con la misma firma. `_reserve_agent_speech` **no** se mueve (cuatro callers del ciclo de habla). El bloque `[CYCLE-PROBE]` viaja con el código o se borra antes (su propio encabezado L88-L118 lo pide); no crear una segunda copia.
- **Prueba de caracterización previa**: la de 5.1 sobre `_session_processor` real, más una prueba de que los delegadores siguen siendo invocables como métodos desenlazados con `SimpleNamespace` (el idioma de 24 llamadas de `test_batch_adaptive.py`), y una traza `pre → job → post` con corte a mitad de lote que verifique que la trama devuelta al carry lleva `pre_done=True` y encabeza el lote siguiente **antes** que cualquier mensaje del inbox.

### 5.3 Procesamiento de media (3a-3c)

- **¿Coherente?** El DSP ya está aislado (3c). `_audio_pre_vad` es casi coherente (bookkeeping) salvo por la adopción lazy del STT. `_audio_post_vad` mezcla seis cosas (§4).
- **Estado local**: VAD/smart-turn, resamplers, `turn_audio`, `pre_context_buffer`, `is_speaking`, relojes: por sesión, sin excepción. Modelo, `feature_extractor`, `_inference_lock` de smart-turn y pool: compartidos con los locks actuales.
- **Inyección posible**: operaciones ya existentes del STT (`send_prepared_audio`, `send_audio_chunk`, `reconnect_session`), `_send_message`, `_cancel_active_tts`, `_resolve_turn_complete`, `sm._reset_timeout_timer`.
- **Acoplamiento**: `_state_lock` (RLock) tomado por `_vad_state_lock` desde el servidor (L284, L7622, L8493); `_inference_gate`; pool `vad`; executor por defecto para smart-turn; `stt_connection.streaming_paused` leído para decidir banking (L3781, L3825); `session.tts_task_start` como señal de TTS activo (L3824).
- **Tests existentes**: `test_precontext_dedup.py` (6, con `_handle_audio_message`/`_audio_pre_vad`/`_audio_post_vad` **reales**), `test_frame_offload.py`, `test_vad_executor.py`, `test_vad_reset_regression.py` (incluye el grep del fuente, H8), `test_vad_silence_reset.py`, `test_aislamiento_entre_sesiones.py`.
- **Extracción mínima**: **no mover** `_audio_post_vad`. Primero separar dentro del método "interpretar resultado" (dict/bool → `is_speech`, `turn_complete`, `barge_in_ready`, `stt_prepared`) de "ejecutar efectos", manteniendo el mismo orden síncrono/asíncrono. Separar la adopción del STT de `_audio_pre_vad` sólo después de caracterizar que el lote nunca la alcanza (guard L2742).
- **Prueba de caracterización previa**: tabla de resultados VAD (dict con y sin `stt_prepared`; bool) → secuencia observable `speech-start → flush pre-context → live send → banking → barge-in | turn-close | fallback Silero`. Debe fijar que con `streaming_paused=True` no se banca nada (L3822-L3834).

### 5.4 Orquestación de turnos (4a-4c)

- **¿Coherente?** No: tres piezas. Resolución STT (4a), política conversacional (4b), despacho de herramientas (4c). `_process_transcript` no es "orquestador de turnos": incluye absorber terminal, gracia post-TTS, AMD, history, SM, opener, router, extracción, transición, compresión y creación de la tarea de habla.
- **Estado local**: `turn_seq`, banderas de Finalize/fragmento, contadores de fallo (incluido el dinámico H7), `conversation_history`, `variables`, `sm_pending_function_ids`, `amd_*`, `first_transcript_time`.
- **Inyección posible**: para 4a, `send_finalize`, `transcribe`, `discard_pending`, `_start_tracked_speech`, `_handle_new_user_turn_tts` y el único callback de salida `_process_transcript`; para 4b, `amd_detector`, `intent_router`, `sm`, `llm_processor`, `_start_tracked_speech`; para 4c, `sm`, `create_function_call_request`, `_send_message`, `_generate_state_followup`.
- **Acoplamiento**: `StenoSessionConnection.finalize_lock` (STENO L122, L640); callback auto-flush creado como tarea en STENO L603; `_flush_fragment_watchdog` como tarea; `_process_llm_tts_streaming` como tarea con `on_tts_done` → `_post_turn_cleanup` (L4795-L4818); history mutada desde 24 sitios sin lock (seguro sólo por ejecutar en el loop).
- **Tests existentes**: `test_fragment_merge.py` (8, `_process_speech_streaming` y auto-flush **reales**), `test_barge_in_disabled_path.py` (fallback sliced y purga), `test_turn_watchdog.py` (`_resolve_turn_complete` real), `test_auto_flush_race.py`, `test_stt_pending_results_purge.py`, `test_terminal_absorber.py` (`_process_transcript` real, sólo el absorber), `test_first_speaker_client.py`, `test_parallel_tool_calls.py` (`_dispatch_tool_call_batch` real), `test_flex_followup.py` (`_handle_internal_function` real). **Ninguno** para `execute_function`, `_handle_function_response`, `_handle_function_call`, `inject_prompt`.
- **Extracción mínima**: en 4a, aislar la selección "Finalize persistente / fallback `transcribe` / legacy `transcribe`" en una función que devuelva `(transcript, stt_latency)` y siga entrando por el mismo `_process_transcript`; **no** tocar dónde se incrementa `turn_seq` (L4005, L4255) ni la dedup con `_auto_flush_turn_time`. En 4c, extraer la codificación común "par tool_call/result + `create_function_call_request` + `_send_message`" (8 sitios) como función pura; validación y `sm_pending_function_ids` se quedan en los envoltorios.
- **Prueba de caracterización previa**: la misma utterance por las cuatro entradas debe producir **un** `ConversationText(user)`, **una** mutación de history y **nunca** dos respuestas; fijar explícitamente cuándo cambia `turn_seq` y que auto-flush/watchdog **no** lo cambian. Para 4c: roundtrip `FunctionCallRequest → FunctionCallResponse` para LLM externo (con y sin error), acción del SM (follow-up omitido), `hangup_call`, respuesta tardía o ausente, socket ya cerrado (`_send_message` no propaga el `False`, L9370).

### 5.5 Ciclo de habla del agente (5)

- **¿Coherente?** `_PacedAudioSender` y `_OutboundAudioBudget` sí. Alrededor hay **cinco** variantes de la secuencia entrada/salida: streaming (L4859-L4945 / L5973-L6041), estática (L7247-L7344 / L7553-L7602), filler (L8055-L8077 / L8129-L8147), greeting en caché (L1866-L1872 / L1916-L1929, sin sender ni cancelación), error hablado (L5910-L5938, `_safe_send_bytes` directo sin pacer ni history). Diferencias de contrato en §8.C, más H1 y H4.
- **Estado local**: todo el bloque de 20 campos listado en §4/5. `turn_seq` se lee aquí y se escribe en 4a.
- **Inyección posible**: `tts_processor.synthesize`, `_make_paced_sender`, `_send_message`, `_wait_for_tts_playback`, `_run_terminal_on_exit`, `_reserve_agent_speech`, mutadores del VAD (`set_tts_playing`, `set_barge_in_mode`, `get_turn_audio`, `clear_turn_audio`).
- **Acoplamiento**: `tts_task_lock` (adquirido en 8 sitios); tarea interna del sender; generador asíncrono de TTS bajo semáforo global (TTS L769); timer de filler (L5196); callbacks `on_tts_done` (L4795) y `_on_done` (L7198); `_cancel_active_tts` desde la pila de tramas; timers externos que llaman `_speak_response` en línea (silencio L8814, farewell L8852, `inject_prompt` L9563).
- **Tests existentes**: `test_tts_pacing.py`, `test_outbound_budget.py`, `test_history_truncation.py` (`_write_interrupted_history` real), `test_turn_audio_flush.py` (`_cancel_active_tts` y `_flush_turn_audio_to_stt` reales), `test_bargein_diagnostics.py` (filler y reentry hold), `test_batch_speak_deadlock.py` (`_speak_response` real sólo para el guard de generación, L182). **Ninguna** prueba recorre `_process_llm_tts_streaming` ni `_filler_then_followup` de extremo a extremo.
- **Extracción mínima**: **ninguna** todavía. Primero fijar por prueba la secuencia de entrada y de salida de cada variante (ver abajo) y las anidaciones (H1). Sólo entonces concentrar la secuencia común en dos funciones privadas **sin** mover estado.
- **Prueba de caracterización previa**: parametrizada por variante (streaming, estática, filler, greeting caché, error) y por momento de cancelación (antes del primer byte, a mitad, al final): eventos emitidos y su orden, bytes enviados, history resultante, `streaming_paused` pausa/reanuda, `AudioDone` sí/no, `tts_last_complete_time` sellado o no, `_run_terminal_on_exit` con qué `after_cancel`, y **para el atajo estático dentro del streaming, que el `finally` externo vuelva a ejecutar la secuencia** (H1) — se caracteriza, no se corrige.

### 5.6 Lifecycle (6a-6b, 1d)

- **¿Coherente?** `_release_worker_sessions` sí (idempotente, no lanza, timeouts explícitos). Los guardias son coherentes por separado. El teardown del handler duplica el cierre de proveedores.
- **Estado local**: referencias de tareas, conexiones, `campaign`, buffers, `websocket`, `_worker_sessions_released`, `_terminal_on_exit_scheduled`.
- **Inyección posible**: `disconnect_session`, `disconnect_tts_session`, `cancel_ursula_session`, `websocket.close`, `state_machine.cleanup`.
- **Acoplamiento**: `_sessions_lock`; `asyncio.shield` en el reaper (L1443); `isinstance` sobre proveedores (8 sitios); `gc.collect` en el loop; `_periodic_cleanup` llamado también tras cada turno (L9005).
- **Tests existentes**: `test_worker_session_release.py` (16, primitivo real; L196 fija que `terminal_state` **no** cierra huérfanos), `test_idle_evict.py` (3), `test_terminal_on_exit.py` (12), `test_terminal_cancel_close.py` (6), `test_no_interaction_guard.py` (9), `test_turn_watchdog.py`, `test_background_audio.py`, `test_global_cancel_return.py` (`_post_turn_cleanup` real).
- **Extracción mínima**: hacer que el `finally` delegue **sólo** el bloque proveedores (L2318-L2330, L2387-L2456) en `_release_worker_sessions(reason="disconnect")`. Cambios observables de esa delegación: orden (el primitivo cancela tareas de connect **antes** de desconectar, el `finally` después) y `ursula_session_id = None` (el `finally` no lo anula). Ambos `[inferencia]` inocuos en teardown. La política de huérfanos para razones no-AMD (H5) es una **decisión de contrato**, no parte de esta extracción.
- **Prueba de caracterización previa**: teardown completo con connect tasks pendientes y ya terminadas, para `disconnect`, `terminal_state`, `farewell_hangup`, `amd_hangup`, `idle_evict` (idle y no-audio): qué se cierra, qué se descarta, cuántas veces se llama cada `disconnect_*`, y que ninguna excepción escapa.

### 5.7 Observabilidad (7)

- **¿Coherente?** Emisión sí (`_emit_event`, `_safe_event_attrs`). El momento de medición está repartido: "out" antes del envío (intentos), bytes tras envío, probe dentro del planificador leyendo `_probe_*` del VAD.
- **Estado local**: contadores y probe en `SessionState`; el span en el handler.
- **Inyección posible**: `emit_protocol_event`, logger, reloj.
- **Acoplamiento**: `websocket_send_lock` para contadores; campos `_probe_*` del VAD; `call_span.close()` en el `finally` externo (L2518).
- **Tests existentes**: `test_cycle_probe.py` (4, con encabezado "BORRAR ESTE ARCHIVO ENTERO"), `test_outbound_budget.py`, parte de `test_bargein_diagnostics.py`. Ninguno para `_emit_event`/`_send_message`.
- **Extracción mínima**: sólo formato/emisión. No mover el instante de actualización de contadores.
- **Prueba de caracterización previa**: definir si "out" significa intento o envío confirmado (hoy: intento, L9369-L9370) y fijarlo.

---

## 6. Inventario de estado por sesión y ownership

| estado | dueño actual | mutabilidad / concurrencia | decisión de ownership |
|---|---|---|---|
| Identidad, `settings`, formatos, `variables`, `system_prompt`, `functions`, `greeting` | `SessionState` | bootstrap; `variables` también desde router (L4709) y SM | Mantener en `SessionState` |
| `websocket`, `inbox`, `websocket_send_lock` | `SessionState` (+ parámetro `websocket` en 20 métodos) | una cola y un lock por sesión | No mover; decidir una sola fuente del socket al extraer 1a/1b |
| Registro `sessions` | `GoldenBridgeServer` + `_sessions_lock` | compartido; `get_call_metadata` lee sin lock (L9408) | Mantener en servidor |
| `_batch_carry`, `_batch_idle`, `_frames_in_flight`, `_batch_disabled`, `_tts_starting_at`, contadores `_batch_*` | `SessionState` | caliente por trama; leídos por watchdog y barrera | Fuente única en `SessionState`; el colaborador de fase 1 no copia nada |
| `audio_buffer`, `_audio_buffer_bytes`, `pre_context_buffer` | `SessionState` | orden estricto, no idempotente; vaciados en 5 sitios de turno (L4022, L4272, L4931, L7316, L8477) más el teardown (L2459) | Mantener por sesión |
| Estado Silero (LSTM, contadores, `pending_audio`, `_infer_lock`) | `session.vad_processor.vad` | por sesión; ORT compartido por `copy.copy` (VAD L213) | No trasladar |
| Estado smart-turn (`turn_audio`, resamplers, timers, `_state_lock` RLock, `_inference_gate`, `_voice_ts_lock`, `_tts_playing`, `_barge_in_enabled`) | `session.vad_processor` | por sesión; el job sostiene `_state_lock` todo el lote | No trasladar |
| Modelo smart-turn, `feature_extractor`, `_inference_lock` | `SmartTurnProcessor` prototipo (SMART L143-L145) | compartidos por referencia; ORT secuencial | Mantener compartido |
| `stt_connection` (`connected`, `streaming_paused`, `_last_finalize_time`, `finalize_lock`, `pending_results`, callbacks) | `StenoSessionConnection` + escrituras del servidor en 19 sitios | dos dueños coordinados por lock y callback | No copiar; documentar el contrato conjunto antes de 4a |
| `_stt_connect_task`, `_stt_warm_task`, `_tts_connect_task` | `SessionState` | adopción lazy (STT: primera trama L3586; TTS: primera síntesis L4862/L7347) | Mantener; la adopción lazy es la raíz de H5 |
| Fragmentos/Finalize (`_auto_flush_turn_time`, `_finalize_inflight`, `_pending_flush_*`, `_last_streaming_turn_time`) | `SessionState` | escritos por 4 entradas de 4a | Mantener; fijar contrato en fase 0 |
| `_stt_finalize_failures` | **dinámico** (L4084) | no declarado | Declarar en `SessionState` (cambio sin semántica) |
| `conversation_history` | `SessionState`, 24 escritores | mutada sin lock; snapshots por copia en LLM | Mantener; consolidar la codificación del par tool_call/result |
| Campaña (`CampaignSessionState`: `current_state`, `extracted_data`, `state_stack`, cooldowns, `static_suppressed_state`, …) | `CampaignStateMachine.session_state` (SM L79-L85) | por turno; no contiene history LLM | Mantener; no crear tercer dueño |
| `sm_pending_function_ids` | `SessionState` | añadido en `execute_function`, descartado en `_handle_function_response`; sin timeout | Mantener |
| Bloque TTS (20 campos, §4 fila 5) | `SessionState` | mutado por 5 variantes + cancel + teardown | Mantener; H1/H4 antes de tocar |
| `outbound_budget` | `SessionState` (perezoso, L9247) | consumido por TTS, filler, fondo, greeting | Único por sesión |
| Tareas de guardias y timers | `SessionState` | canceladas en el `finally` | Mantener referencias en sesión |
| Contadores y `_cycle_probe` | `SessionState` | calientes | Junto al estado que miden |
| Cachés greeting/filler/script | `GoldenBridgeServer` | globales con lock | Servidor |
| `background_mixer` | `SessionState.background_mixer` (clone MIX L305-L326) con **fallback al mixer compartido** (L2197, L8655) | `_loop_position` por sesión sólo con clon | Exigir clon; el fallback es trampa latente, no violación con el proveedor actual |

---

## 7. Invariantes de concurrencia y audio que toda extracción debe preservar

| invariante | garantía actual | evidencia | riesgo de extracción |
|---|---|---|---|
| FIFO por llamada: texto y audio comparten cola | un lector, un consumidor | `_session_reader` L2557, `_session_processor` L2620 | colas separadas reordenan Settings/audio y FunctionCall/audio |
| Handlers de texto **bloquean** el consumo (H2) | `await` en línea | L2639, L3425, L3487, L3561 | convertirlos a tarea cambia barge-in y orden de eventos |
| carry → pendiente → inbox | orden fijo | L2611-L2620 | leer el inbox con carry vivo adelanta audio posterior a un texto |
| Pre una sola vez | `(bytes, pre_done)` | L2761-L2764, L2835-L2837, L2877, L2920 | repetir pre duplica contadores y `audio_buffer` |
| Corte en el primer evento | el job corta en transición, inferencia, cierre o barge-in | SMART L1145-L1151 | postear tramas más allá del corte cambia VAD/turno |
| `_state_lock` sostenido durante todo el lote | job bajo RLock | SMART L1140 | mutadores del loop esperan al job entero (`test_frame_offload.py` L535) |
| Settle | `_frames_in_flight=0`, `_batch_idle.set()` en `finally` | L2967-L2968 | contador atascado silencia watchdog y barrera |
| Barrera lote/habla | sello antes del primer `await`; espera ≤ 1 s | L2703-L2708; TTL L85 | mutar VAD/TTS a mitad de lote rompe equivalencia serial (H1, H3 la erosionan) |
| Aislamiento entre sesiones | procesadores y locks clonados; modelo compartido | SMART L133-L151, VAD L189-L231, tests de aislamiento | compartir resamplers, LSTM o locks serializa o contamina |
| Cierre de turno único | `is_speaking=False` antes del primer `await` | L3938-L3940 | mover el guard tras un `await` permite dos cierres |
| Generación de habla | `turn_seq++` sólo en L4005 y L4255; habla diferida compara `_speech_gen` | L7189, L7255 | una respuesta vieja suena en el turno nuevo |
| Cancelación atómica | `tts_task_lock`; la tarea se desregistra antes de esperar | L8463-L8541, L8544-L8550 | esperar bajo el lock bloquea; la bandera se resetea a los 0,2 s (H4) |
| Historial de lo oído | sender sticky-abort + ledger; history escrita en el cancel | `_PacedAudioSender._run` L776-L778, `_write_interrupted_history` L8283 | el siguiente LLM ve texto no oído o duplicado |
| Cabeza de barge-in | snapshot+clear+desarme bajo lock del VAD; flush tras reanudar STT | L8493-L8511, L8599-L8611 | reordenar pierde o duplica las primeras palabras |
| Finalize single-flight | `finalize_lock`, deadline 3 s | STENO L640, L704 | auto-flush y cierre se roban resultados |
| Audio de salida serializado | todas las fuentes pasan por `_safe_send_bytes` + `websocket_send_lock` | L9280 | enviar fuera intercala bytes |
| Presupuesto único de salida | `outbound_budget` compartido; sleep fuera del lock | L9268-L9278 | un presupuesto por productor acumula backlog invisible |
| Un solo sender vivo | `sender.finish()` antes de crear otro | L5136, L5543, L5591, L5649 | dos senders intercalan audio |
| `AudioDone` tras el último byte | `push(silencio)` → `finish()` → `AudioDone` | L5877-L5882, L7539-L7544 | `AudioDone` antes de drenar corta el final |
| FunctionCall ordenado | extracts → externos → una transición → follow-up | L6379-L6489 | paralelizar cambia negocio |
| Terminal una vez | `_terminal_on_exit_scheduled` + `_terminal_on_exit_executed` | L6060-L6063, SM L144 | doble hangup/transfer o terminal mudo |
| Liberación idempotente | `_worker_sessions_released` | L6108-L6110 | dos rutas sin el mismo guard cierran/recrean conexiones |
| Watchdog no cierra contra estado no visto | gates: `is_speaking`, no TTS, starvation 150 ms, inbox vacío, sin vuelo, sin carry | L8953-L8979 | cerrar contra tramas no procesadas trunca el transcript |
| Tres sitios de `_reset_vad_turn_state` (H8) | test por grep del fuente | `test_vad_reset_regression.py` L345 | mover cualquiera de L4925/L7344/L8077 rompe el test |

---

## 8. Rutas duplicadas y consolidaciones candidatas

### 8.A Cleanup de proveedores

```text
Ruta canónica candidata: _release_worker_sessions (WS@L6094)
Por qué es canónica:
  - tests que codifican el comportamiento deseado: sí (test_worker_session_release.py, 16)
  - impone timeout: sí (2,0 s STT, 2,0 s TTS, 1,0 s Ursula)
  - política de error explícita: sí (captura y loguea, nunca lanza)
  - inyección en vez de estado global: parcial (isinstance sobre self.stt/tts_processor)
  - exportada en API pública: no (privada)
Ruta competidora: finally de _handle_connection (WS@L2297-L2456) · _evict_session (L1424, compone la canónica + close + pop)
Diferencia de contrato:
  - huérfanos: canónica cierra sólo con reason == "amd_hangup" (L6165-L6169); finally descarta siempre (L2417, L2447)
  - orden: canónica cancela connect tasks ANTES de desconectar (L6151-L6163) para evitar re-materialización; finally desconecta primero y cancela después
  - ursula_session_id: canónica lo anula (L6204); finally no
  - alcance: finally además cancela TTS activo, fondo, silencio, watchdog, guard, SM cleanup, warm task, vacía buffers y desregistra — nada de eso está en la canónica
Callers verificados:
  - canónica: L1444 (idle_evict) · L6092 (terminal_state) · L8874 (farewell_hangup) · L9397 (amd_hangup)
  - finally: todo cierre de handler
Evaluación de código muerto: ninguna ruta es muerta
Impacto de migración:
  Si se elimina el bloque de proveedores del finally: se pierde nada funcional si la canónica corre con reason="disconnect"; cambia el orden interno y se anula ursula_session_id
  Si se envuelve: el finally queda como orquestador de tareas/registro y delega proveedores
Consolidación recomendada: fase 2a (delegación pura, comportamiento de huérfanos intacto); fase 2b (política de huérfanos por razón) como decisión de contrato separada, con H5 como caso de prueba y test_worker_session_release L196 revisado explícitamente
```

### 8.B Resolución de turno STT

```text
Ruta canónica candidata: ninguna — son estrategias con contratos deliberadamente distintos
Rutas:
  1. Finalize persistente: _resolve_turn_complete → _process_speech_streaming → send_finalize (lock, 3 s) → fallback transcribe si vacío y hay audio_data (L4055-L4064) → held fragment (L4072) → _process_transcript L4178
  2. Legacy: _resolve_turn_complete → _process_speech → transcribe (STENO: open 15 s, lectura sin tope; Deepgram: WS → HTTP 30 s) → _process_transcript L4313
  3. Auto-flush: STENO L603 create_task(callback) → _handle_auto_flush_transcript → gates (TTS vivo, cooldown 3 s, fragmento corto con Finalize en vuelo, cola de turno 1,5 s) → _process_transcript L4379 con stt_latency=0.0
  4. Watchdog de fragmento: _flush_fragment_watchdog → _process_transcript L4433, envuelto en try/except (las otras no)
Diferencia de contrato:
  - turn_seq++: sólo 1 y 2 (L4005, L4255)
  - has_real_user_turn: sólo 1 y 2
  - discard_pending: sólo 1 con transcript (L4155-L4156)
  - dedup: 1 salta si _auto_flush_turn_time < 5 s (L3988-L3998); 3 y 4 la sellan
  - fallos: 1 cuenta fallos, reconecta a 2 fallos y habla re-prompt sordo (L4082-L4133); 2 sólo loguea
  - _handle_new_user_turn_tts: sólo desde _resolve_turn_complete (1 y 2)
Callers verificados: ver §2
Evaluación de código muerto: ruta 2 viva sólo sin stt_connection persistente (Silero-only L3915-L3918 o fallo de connect L3606-L3612)
Consolidación recomendada: frontera "resultado de turno" que devuelva (transcript, latencia, origen) al mismo _process_transcript; no fusionar conexiones ni timeouts
```

### 8.C Ciclo de habla del agente

```text
Ruta canónica candidata: ninguna todavía. _PacedAudioSender (L584) y UrsulaProcessor.synthesize (TTS L738) son los únicos componentes canónicos
Rutas y callers:
  S1 _process_llm_tts_streaming (L4830): 4782 (tarea), 7106 (en línea dentro de tarea)
  S2 _speak_response (L7216): 15 sitios; en línea desde processor (3425, 3487, 3561), desde tarea streaming (5137, 5592, 5650, 7013), desde timers (8814, 8852, 9563), como tarea vía _start_tracked_speech (7191)
  S3 _filler_then_followup (L8012): 7100
  S4 _send_cached_greeting (L1832): 2026, 2047
  S5 error hablado dentro de LLMStreamingError (L5910-L5938): sólo S1
Diferencia de contrato (entrada):
  - reserva: S1 L4859 antes de todo; S2 L7247 antes de todo; S3 L8055 tras lanzar LLM; S4 L1866; S5 ninguna (hereda S1)
  - adopción _tts_connect_task: S1 antes de mutar VAD (L4862); S2 después (L7347)
  - history: S2 pre-append (L7280-L7285, rewrite in place en cancel); S1 al final o en cancel (L5534, L5847, L5955, L8325); S4 append inmediato (L1899); S3 y S5 nunca
  - AgentStartedSpeaking: S1 en primer chunk (L5419); S2 en primer chunk (L7482); S3 antes de audio (L8093); S4 antes de audio (L1880); S5 antes del primer chunk (L5926)
  - pacing: S1/S2 sender; S3 pacer inline (L7960); S4 y S5 ninguno (blob y directo)
  - cancelación: S1/S2/S3 registran active_tts_task; S4 y S5 no son cancelables
Diferencia de contrato (salida):
  - S1 L5973-L6041 y S2 L7553-L7602: misma secuencia de 13 pasos; S3 L8129-L8147: 8 pasos sin talkover ni terminal; S4 L1916-L1929: 5 pasos, tts_last_complete_time incondicional
  - anidamiento S1→S2 ejecuta la salida dos veces (H1)
Evaluación de código muerto: _process_llm_tts_nonstreaming (L6240) y sus _start_tracked_speech (6290, 7137) muertos mientras el LLM tenga generate_response_streaming (comentario L7169-L7173) — no confirmado en ejecución [pendiente]
Consolidación recomendada: ninguna antes de la caracterización de §5.5; luego dos privadas "entrar en habla" / "salir de habla" con parámetros explícitos (history cuándo, AgentStartedSpeaking cuándo, terminal sí/no) y sin mover estado
```

### 8.D Emisión de FunctionCall

```text
Ruta canónica candidata: ninguna; dos emisores con ownership distinto del follow-up
Ruta A: _handle_function_call (L6491) ← 6339, 6429, 6930
  valida nombre contra session.functions si no está vacío (L6523-L6531); history par (L6542-L6554); _send_message; follow-up lo genera _handle_function_response (L3543-L3567) con LLM no-streaming EN LÍNEA en el processor
Ruta B: execute_function (L9416) ← 8862, 9394, 9395, SM L837, SM L939
  busca sesión bajo _sessions_lock; usa session.websocket; NO valida; añade call_id a sm_pending_function_ids (L9462); history par (L9468-L9480); _send_message; _handle_function_response omite el follow-up (L3509-L3517)
Ruta interna: _handle_internal_function (L6560) ← 6 sitios (recursivo); no sale al cliente; escribe el par en 6 formas (L6640-L6654, L6745-L6761, L6776-L6793, L6829-L6846, L6863-L6880, L6939-L6955)
Diferencia de contrato: validación (A sí, B no); pendientes (B sí); fuente del socket (A parámetro, B campo); follow-up (A sí, B no); sin timeout de respuesta en ambos; _send_message no propaga False (L9370) → "sent" puede registrarse con socket cerrado
Callers verificados: arriba
Evaluación de código muerto: ninguna muerta; transferencia = función externa de campaña por ruta B (no hay ruta especial: único "transfer" no-comentario es L6096)
Consolidación recomendada: una función "codificar par + request + enviar" (pura respecto a política) usada por A, B e interna; validación, pendientes y follow-up permanecen en los envoltorios. Prueba de roundtrip antes (5.4)
```

### 8.E LLM posterior a función

`_handle_function_response` (L3547, no-streaming, en línea en el processor) frente a `_generate_state_followup` (L7024: pipeline streaming como tarea anidada, filler, o no-streaming). Mantener separados; documentar cuándo existe segundo LLM. Sin tests para el primero.

### 8.F Codificación del par tool_call/result en history

8 sitios (§3.4) con el mismo dict `{"role":"assistant","content":None,"tool_calls":[{...}]}` + `{"role":"tool", …}` y `_trim_conversation_history` detrás. Consolidación: helper puro; cambio sin semántica; sin prueba directa hoy salvo `test_history_truncation.py`/`test_history_compression.py` para el recorte.

### 8.G Registro de sesión

Handler registra (L2166) y borra (L2479-L2481); reaper borra (L1457-L1458). Tres búsquedas por id: `execute_function` y `inject_prompt` bajo lock; `get_call_metadata` sin lock (L9408). Tras la evicción, las acciones del SM encuentran "session not found" (L9446-L9448). Mantener en servidor; caracterizar evicción concurrente.

### 8.H Bucle acoplado legacy

`read_decouple=false` (L2239-L2295): sin batching, sin descarte post-desconexión, sin sentinel, `_handle_audio_message` por trama. Default versionado: desacoplado. No borrar hasta comprobar `active.yaml` `[pendiente]`.

---

## 9. Clasificación de patrones

```text
Segmento: GoldenBridgeServer
Patrón principal: Mediator
Estado: implementado, sobrecargado
Confianza: alta
Evidencia: coordina 6 colaboradores inyectados y 9 tareas/timers por sesión con condicionales sobre el estado de todos ellos (L2719-L2753, L8205-L8281, L6053-L6092)
Patrones cercanos: God Object (antipatrón), Facade (no: contiene política)
Riesgo: otra fachada escondería el tamaño sin resolver ownership
Siguiente actividad: extraer una responsabilidad cohesionada (fase 1)

Segmento: campaña (CampaignStateMachine)
Patrón principal: State (+ Command para acciones on_enter/on_exit)
Estado: implementado
Confianza: alta
Evidencia: SM L79-L85, execute_transition, _execute_function_call L816, _execute_hangup L936, executor inyectado L116
Riesgo: romper orden on_enter/on_exit o la idempotencia L144
Siguiente actividad: no tocar; probar callbacks desde el servidor (5.4)

Segmento: VAD / STT / TTS / LLM inyectados
Patrón principal: Strategy + Adapter
Estado: implementado parcialmente
Confianza: alta
Evidencia: constructor L1090-L1115; isinstance en 8 sitios y hasattr en 42 rompen la abstracción
Patrones cercanos: Ports and Adapters (latente)
Riesgo: interfaz universal prematura mentiría (Finalize/transcribe/auto-flush no son intercambiables)
Siguiente actividad: catalogar las operaciones realmente usadas por integración (hecho en §3.3) y encapsular llamadas concretas una a una

Segmento: FunctionCall
Patrón principal: Command
Estado: latente
Confianza: media
Evidencia: nombre, args, id, respuesta correlacionada; dos emisores (§8.D)
Riesgo: ownership doble del follow-up
Siguiente actividad: caracterizar roundtrip; extraer emisión común

Segmento: _handle_text_message
Patrón principal: dispatcher explícito por isinstance
Estado: implementado
Confianza: alta
Evidencia: L3009-L3016, cuatro tipos
Patrones cercanos: Chain of Responsibility (no recomendado: indirección sin necesidad)
Siguiente actividad: ninguna

Segmento: _batch_eligible
Patrón principal: guard sobre estado propio mutable (State, no Strategy)
Estado: implementado
Confianza: alta
Evidencia: L2732-L2753 lee banderas que el ciclo de habla muta
Riesgo: convertirlo en Strategy externa ocultaría que el discriminador es estado interno
Siguiente actividad: mantener como guard inyectable (la fixture ya lo hace así)

Segmento: _PacedAudioSender / _OutboundAudioBudget
Patrón principal: productor/consumidor + presupuesto de tasa
Estado: implementado, extraído
Confianza: alta
Evidencia: L584-L810, L411-L461, tests propios
Siguiente actividad: ninguna; no reemplazar
```

---

## 10. Orden de refactorización incremental

| fase | símbolos reales | actividad y pruebas | rollback | riesgo |
|---|---|---|---|---|
| **0. Caracterización** | `_session_processor`, `_session_reader`, `_handle_text_message` (H2), rutas STT (8.B), ciclo de habla por variante (5.5, H1, H4), FunctionCall roundtrip (5.4), teardown por razón (5.6), `_stt_finalize_failures` declarado | Añadir las pruebas descritas; ejecutar las 699 existentes en un worktree limpio de `api` `[pendiente]` | sólo tests | Bajo |
| **1. Planificador interno** | `_batch_eligible`, `_drain_inbox_frames`, `_drain_batch`, `_run_audio_frames`, `_handle_audio_batch` (+ bloque `[CYCLE-PROBE]`) | Extraer a colaborador/funciones con el conjunto de dependencias de `_srv()`; delegadores con firma idéntica y **invocables desenlazados**; `_reserve_agent_speech` se queda. Correr `test_batch_adaptive`, `test_batch_speak_deadlock`, `test_cycle_probe`, `test_frame_offload`, `test_precontext_dedup`, `test_turn_watchdog` | reponer cuerpos detrás de las firmas | Medio |
| **2a. Delegación de proveedores** | `finally` L2318-L2330, L2387-L2456 → `_release_worker_sessions(reason="disconnect")` | Comportamiento de huérfanos intacto; verificar orden y `ursula_session_id`; `test_worker_session_release`, `test_idle_evict`, `test_terminal_*` | restaurar bloque directo | Medio |
| **2b. Política de huérfanos** (decisión, no refactor) | `_release_worker_sessions` L6165-L6169 | Decidir por razón; revisar `test_worker_session_release` L196; prueba H5 | revertir política | Medio-alto |
| **3. Emisión FunctionCall** | `_handle_function_call`, `execute_function`, `_handle_internal_function` (8 sitios de history), `_handle_function_response` | Helper "par + request + envío"; validación/pendientes/follow-up en envoltorios; roundtrip de 5.4 | inline previo | Medio |
| **4. Resolución STT** | `_resolve_turn_complete`, `_process_speech_streaming`, `_process_speech`, auto-flush, watchdog de fragmento | Encapsular selección Finalize/fallback/legacy devolviendo al mismo `_process_transcript`; `turn_seq` y dedup intactos | delegadores | Alto |
| **5. Lifecycle TTS compartido** | `_process_llm_tts_streaming`, `_speak_response`, `_filler_then_followup`, `_send_cached_greeting`, `_cancel_active_tts` | Sólo tras 5.5; dos privadas entrar/salir sin mover estado; decidir explícitamente H1 (mantener o eliminar la doble salida) | rutas antiguas seleccionables | Muy alto |
| **6. Conversación / bootstrap** | `_process_transcript`, `_handle_settings`, `_handle_connection` (admisión) | Pasos pequeños: campaña/router, opener, tareas de bootstrap | revertir por paso | Muy alto |
| **7. Observabilidad** | `_emit_event`, `_send_message`, `_CycleProbe`, contadores | Separar formato/emisión sin mover el instante de medición; borrar el probe si el diagnóstico cerró | emisión inline | Bajo |

**Interfaz mínima justificable para la fase 1** `[inferencia]` (sólo después de la fase 0). El consumidor `_session_processor` necesita cuatro operaciones con las firmas actuales: `_batch_eligible(session) -> bool`, `_drain_inbox_frames(session, carry) -> mensaje|None`, `_drain_batch(session, carry) -> list`, `_run_audio_frames(websocket, session, frames)`. Sus dependencias hacia fuera son exactamente: `_inbox_batch_max`, `_audio_pre_vad`, `_audio_post_vad`, `_handle_audio_message`, `_send_message`, `session.vad_processor.process_audio_batch`, y el probe por `getattr`. No posee copias de carry, eventos, contadores, VAD ni websocket.

---

## 11. Piezas que NO deben extraerse todavía

1. `[inferencia]` **`SessionState` en subestados.** 106 campos con 24 escritores de history y 5 vaciadores de `audio_buffer`; dividir datos ahora crea ownership doble.
2. `[inferencia]` **`_audio_post_vad` como "media".** Contiene STT, campaña, barge-in y cierre de turno (5.3).
3. `[inferencia]` **`_process_transcript` completo.** Punto de convergencia de 11 políticas (5.4).
4. `[inferencia]` **Unificar las cinco variantes de habla.** Sin prueba de camino completo; H1 y H4 sin caracterizar (5.5).
5. `[código]` **`_reserve_agent_speech`.** Cuatro callers del ciclo de habla; su sello síncrono es la garantía FIX-E (L2703).
6. `[código]` **VAD/smart-turn, resamplers, locks, pool.** Garantías free-threaded y de orden (SMART L1140; VAD L161).
7. `[inferencia]` **`_PacedAudioSender`, `_OutboundAudioBudget`, `_TTSPacer`.** Ya extraídos y probados.
8. `[inferencia]` **`_handle_connection` completo.** Admisión, transporte, bootstrap y teardown se cortan por separado.
9. `[inferencia]` **Lifecycle completo.** Primero 2a; luego decidir 2b.
10. `[pendiente]` **Caminos legacy** (bucle acoplado, `_process_llm_tts_nonstreaming`, `_process_speech` con Deepgram). Falta la configuración efectiva.
11. `[inferencia]` **Interfaz universal STT/TTS.** Contratos no intercambiables (8.B, 8.C).
12. `[inferencia]` **`CampaignStateMachine`.** Ya es State con suite amplia.
13. `[inferencia]` **`_handle_text_message` a Chain of Responsibility.** Dispatcher pequeño y explícito.
14. `[código]` **Los tres sitios de `_reset_vad_turn_state`** (H8) hasta reescribir `test_vad_reset_regression.py` L345.
15. `[código]` **`_periodic_cleanup`** (gc en el loop). Fuera del alcance funcional; cualquier cambio necesita una métrica de latencia punta a punta.
16. Separación de procesos o IPC: fuera de este encargo y no necesaria para ningún corte de arriba.

---

## 12. Recomendación

**Primera extracción interna más segura y útil: el planificador adaptativo de audio** (WS@L2719-L2968), precedida por dos pruebas de caracterización que hoy no existen: `_session_processor` real recorriendo audio, texto, sentinel y desconexión sobre la misma FIFO (incluyendo que un handler de texto que habla bloquea el consumo, H2), y la verificación de que los delegadores siguen siendo invocables como métodos desenlazados con un `SimpleNamespace`.

Conservar: el estado en `SessionState`; las cinco firmas públicas como delegadores; `_reserve_agent_speech` en el servidor; `_audio_pre_vad` y `_audio_post_vad` sin cambios; la secuencia pre → DSP → post; `pre_done`, `_frames_in_flight`, `_batch_idle` y el fusible; el sentinel como objeto de módulo.

Por qué esta y no otra: es la única pieza con responsabilidad única, un caller, invariantes escritas y 33 tests que ya ejercitan los métodos reales con las dependencias inyectadas por la fixture. Es además el camino de audio a 50 fps que el análisis de capacidad va a seguir midiendo, así que tenerlo aislado y probado tiene uso inmediato sin cambiar una línea de semántica de VAD, smart-turn, STT, barge-in, `turn_seq`, TTS, FunctionCall, transferencia, hangup o cleanup.

Segunda candidata, de valor inmediato y riesgo bajo: declarar `_stt_finalize_failures` en `SessionState` y consolidar los 8 sitios que codifican el par tool_call/result (8.F). Ninguna de las dos cambia comportamiento.

---

## 13. Contraste con el análisis de Codex

### 13.1 Coincidencias verificadas `[código]`

- Commit, rama del checkout y cambios locales; 9.611 líneas; 699 tests.
- Todas las coordenadas de línea citadas por Codex para WS (L2124, L2520, L2595, L2679, L2719-L2968, L3024, L3489, L3569, L3647, L3679, L3924, L3978, L4180, L4242, L4315, L4443, L4830, L6043, L6094, L6311, L6491, L6560, L7158, L7216, L8445, L8982, L9256, L9355, L9365, L9416) son exactas.
- Callers de `_process_transcript` (4) y de `_release_worker_sessions` (4): exactos.
- Procesadores: ORT secuencial e `inter_op=1` (SMART L119-L120); `_inference_lock` compartido y `_buffer_lock` por sesión (L143-L149); pool global con lock (VAD L38-L73); `finalize_lock` y deadline 3 s (STENO L122, L640, L704); `transcribe` con `open_timeout=15` y sin tope de lectura (L1111, L1132); Deepgram WS → HTTP 30 s (STT L270-L284, L423); `synthesize` con semáforo y sesión persistente (TTS L769-L777); `set_action_executor` L116, `_execute_function_call` L816, `_execute_hangup` L936; `clone_for_session` MIX L305; MAIN L548.
- Config versionada (los 7 valores citados) y su advertencia sobre `active.yaml`.
- Ausencia de tests para `_handle_connection`, `_session_reader`, `_session_processor`, `_handle_settings`, `execute_function`, `_handle_function_response`; `_speak_response` real sólo en el test del guard de generación; ningún test de camino completo para `_process_llm_tts_streaming`.
- Diagnóstico SRP/Mediator/God Object; frontera modelo compartido / estado por sesión; fuga de infraestructura con los mismos síntomas.
- Timeouts iguales (1 s / 2 s) en `finally` y primitivo; huérfanos sólo `amd_hangup`; `_cancel_active_tts` con `aclose` 50 ms, espera 200 ms, flush condicional y cancelación remota fire-and-forget.
- `_handle_function_call` valida y `execute_function` no; sin timeout de FunctionCall; `_send_message` no propaga `False`.
- Transferencia sin ruta especial; `_handle_text_message` dispatcher explícito; `SessionState` con `default_factory`.
- Recomendación principal (planificador primero) y lista de "no extraer".

### 13.2 Correcciones

| afirmación de Codex | corrección | evidencia |
|---|---|---|
| "123 métodos" en `GoldenBridgeServer` | 101 métodos (64 async) en la clase; 123 son todos los `def` con sangría de clase del archivo, incluidos los de 4 clases auxiliares; 146 `def` en total | conteo por regex sobre `api` |
| Commit "del 3 de septiembre" | fecha de commit 2026-09-03; fecha de autor 2026-08-31 | `git log --format=%ad/%cd` |
| "Interfaz mínima… una sola operación equivalente a `_run_audio_frames`" | el consumidor llama a `_drain_inbox_frames` (L2647) y `_drain_batch` (L2652) directamente; son cuatro operaciones (con el guard); la fixture `_srv()` L58-L68 ya define el conjunto de dependencias | WS@L2647-L2653; test L58-L68 |
| Tests de fase 1 "prueban helpers, no el consumidor" | correcto, pero además los tests invocan los métodos **desenlazados** con `SimpleNamespace`: la extracción debe preservar ese idioma o reescribir 24 llamadas | test L94-L98 |
| "Fuga de conexión nunca adoptada… `[inferencia]`" | el flujo de control es `[código]`: adopción STT sólo en primera trama (L3586), descarte para reason ≠ amd (L6154-L6169), y el reaper "no-audio" lo produce; el efecto remoto sigue `[pendiente]`; además `test_worker_session_release` L196 **fija** el comportamiento actual | H5 |
| Fase 2 "Cleanup de proveedor" como una sola actividad "Medio-alto" | dividir en 2a (delegación pura, comportamiento intacto) y 2b (política de huérfanos, decisión de contrato) | §8.A, §10 |
| "Ambos [streaming y estático] … resetean timeout; ejecutan terminal on_exit" como secuencia común | además la secuencia se ejecuta **dos veces** en los tres atajos estáticos del streaming | H1, L5137/L5592/L5650 |
| "Prueba previa [transporte]: orden exacto, sentinel, cola llena, cancelación" | falta fijar que los handlers de texto hablan en línea y bloquean la FIFO | H2 |
| Riesgo "Medio" para Transporte/FIFO como bloque | el lector solo es **bajo** (75 líneas, un caller); el consumidor es medio por H2; el bloque no debe evaluarse junto | §4 filas 1a/1b |
| "flush condicional" en `_cancel_active_tts` | condicional **y apagado por defecto** (`_send_flush_on_cancel`, TTS L179-L183): hoy es un no-op | H6 |
| "SmartTurn… 2º executor" (comentario del probe) mencionado sin precisar | la inferencia smart-turn corre en el executor por defecto del loop (`run_in_executor(None, …)`), no en el pool `vad` | SMART L1097-L1100 |

### 13.3 Omisiones de Codex (aportes de esta auditoría)

H1 doble `finally` anidado · H2 habla en línea desde handlers de texto · H3 espera de 30 s desde la pila de tramas · H4 reset de `tts_cancelled` a los 0,2 s · H6 flush no-op · H7 `_stt_finalize_failures` dinámico · H8 test que lee el fuente · 8 sitios de codificación del par tool_call/result · `get_call_metadata` sin lock · `gc.collect` en el loop como efecto global del lifecycle · `websocket` como parámetro y como campo · las cinco variantes de habla (Codex enumera dos más "greeting/cache/filler y error" sin sus contratos de entrada/salida) · el fallback al mixer compartido también en `_stream_background_audio` L8655.

### 13.4 Discrepancias de juicio `[inferencia]`

- Codex propone extraer el planificador "manteniendo firmas delegadoras"; añado que los delegadores deben seguir funcionando como métodos desenlazados y que el bloque `[CYCLE-PROBE]` debe viajar o borrarse, nunca duplicarse.
- Codex sitúa "Emisión FunctionCall" en fase 3 sin mencionar la codificación del par de history; ese helper es la parte realmente pura y de riesgo más bajo de esa fase.
- Codex no separa la decisión de contrato (huérfanos) del refactor (delegación); mezclarlas convierte una fase "sin cambio funcional" en una con cambio.
- Sobre la utilidad del corte 1: coincido, y la razón adicional es que es el camino de audio que la medición de capacidad seguirá instrumentando; tenerlo aislado evita que el próximo probe vuelva a incrustarse en el servidor.
