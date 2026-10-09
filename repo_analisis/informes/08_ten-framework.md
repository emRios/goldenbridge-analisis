# 08 — TEN Framework: runtime multilenguaje (C/C++/Rust/Go/Python), grafos, grupos de extensiones y frontera nativa ⇄ Python

Auditor: Claude (subagente). Fecha: 2026-10-09. Término de comparación: `../BRIEF_GOLDEN.md` (GoldenBridge `api@08932a6`, ai-engine `d059295`).
Repositorio **no modificado**, nada compilado ni ejecutado. Rutas relativas a `repos/ten-framework/`. Abreviaturas:
`RT/` = `core/src/ten_runtime/` · `PY/` = `core/src/ten_runtime/binding/python/` · `INC/` = `core/include_internal/ten_runtime/` · `UT/` = `core/src/ten_utils/` · `AG/` = `ai_agents/` · `EXT/` = `ai_agents/agents/ten_packages/extension/`.

Etiquetas: `[CÓDIGO]` archivo:símbolo:líneas · `[DOCUMENTACIÓN]` docs del repo · `[MEDICIÓN]` benchmark reproducible del repo (no existe ninguno para el camino de mensajes, ver §10) · `[INFERENCIA]` · `[PENDIENTE]`.

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/TEN-framework/ten-framework.git` |
| Branch analizado | `main` (default; clon superficial, `git rev-parse --is-shallow-repository` = `true`, 1 commit visible) |
| Commit | `ad20a1735293d2c03af6eeb00940d45d889dd0b0` |
| Fecha del commit (`git log -1 --format=%cI`) | `2026-10-03T11:31:35+08:00` — "feat: add optional jev thinking-mode voice router (#2344)" |
| Versión del runtime | `ten_runtime_python` `0.11.73` (`PY/manifest.json:4`); el ejemplo `voice-assistant` fija `ten_runtime_go 0.11` y `agora_rtc =0.23.9-t1` (`AG/agents/examples/voice-assistant/tenapp/manifest.json`) |
| Licencia observada | **Dos regímenes**: (a) raíz `LICENSE` = *Apache 2.0 "with the following additional conditions"* de Agora (cita literal en §14) y cabecera en cada archivo del núcleo: *"Licensed under the Apache License, Version 2.0, with certain conditions"* (p. ej. `RT/engine/engine.c:1-5`); (b) `AG/LICENSE` = Apache 2.0 sin condiciones adicionales (201 líneas, sin "Agora"/"compete"); las extensiones de `AG/` llevan cabecera "Licensed under the Apache License, Version 2.0." (p. ej. `EXT/streamid_adapter/extension.py:1-4`) `[CÓDIGO]` |
| Submódulos | 1: `core/ten_gn` (`https://github.com/TEN-framework/ten_gn.git`, sistema de build GN) — **no inicializado** (`git submodule status` → `-cf7036e…`). Irrelevante para el análisis de runtime. |
| Dependencias vendorizadas | `third_party/`: libuv, libwebsockets, mbedtls, msgpack, ffmpeg, curl, yyjson, nlohmann_json, node, googletest… `[CÓDIGO]` |
| Piezas **no** presentes en el repo | `agora_rtc` (extensión RTC nativa, se instala desde el registro TEN como paquete binario; ningún directorio `agora_rtc*` en el árbol) y `ten_ai_base` (clases base `AsyncTTS2BaseExtension`/`AsyncASRBaseExtension` importadas por `EXT/elevenlabs_tts2_python/extension.py:21`, sin fuente en el árbol) → todo lo que dependa de su interior es `[PENDIENTE]` |
| Mantenimiento visible | commit de 6 días antes de la auditoría con nº de PR #2344; ~90 extensiones en `EXT/`; documentación de agentes (`docs/ai/L1/*`) reciente `[DOCUMENTACIÓN]` |

---

## 2. Dictamen ejecutivo

1. **TEN no elimina el coordinador por trama: lo pone en tres sitios y, en su despliegue de referencia, lo replica con un proceso por sesión.** Dentro de un proceso, todo mensaje (audio incluido) entre extensiones de **grupos distintos** pasa por la cola `in_msgs` del **engine** (un hilo por grafo, o el hilo de la app por defecto) antes de llegar al hilo destino `[CÓDIGO]` `RT/extension_thread/msg_interface/common.c:ten_extension_thread_dispatch_msg:270-279` → `RT/engine/msg_interface/common.c:ten_engine_append_to_in_msgs_queue:286-303` → `ten_engine_dispatch_msg:383-504` → `ten_engine_post_msg_to_extension_thread:305-363`. El engine es un *hub* por grafo en el camino por trama.
2. **Toda extensión Python asíncrona del proceso comparte por defecto UN único hilo asyncio** (`PythonGlobalMainThread`, singleton, uvloop) `[CÓDIGO]` `PY/interface/ten_runtime/async_extension.py:_get_cached_thread_mode:45-67` (default `TEN_PYTHON_THREAD_MODE=single_thread`, l. 54) y `global_thread_manager.py:GlobalThreadManager:15-135`. Cada `on_audio_frame` entrega a Python = `PyGILState_Ensure` en el hilo C + `asyncio.run_coroutine_threadsafe` (una `Task` por trama) `[CÓDIGO]` `PY/native/extension/extension.c:proxy_on_audio_frame:322-357` + `async_extension.py:_proxy_on_audio_frame:394-419`. Es **el mismo antipatrón que Golden** (un loop de control pagando un salto por trama), con GIL añadido.
3. **El binding Python no soporta free-threading**: cero apariciones de `Py_GIL_DISABLED`, `Py_mod_gil` o `PyUnstable_Module_SetGIL` en `core/` `[CÓDIGO, búsqueda negativa]`; todo callback toma el GIL `[CÓDIGO]` `PY/native/common/common.c:ten_py_gil_state_ensure_internal:174-186`. Incompatible con la restricción Golden "Python 3.14t" sin trabajo de port `[INFERENCIA]`.
4. **El despliegue de referencia de agentes de voz es un proceso por canal**: el servidor Go lanza `tman run start` por sesión, mantiene un `map` en memoria y limita por `WORKERS_MAX` `[CÓDIGO]` `AG/server/internal/worker_linux.go:Worker.start:28-121`, `http_server.go:handlerStart:236-320`; `[DOCUMENTACIÓN]` `docs/ai/L1/02_architecture.md:93-104` ("Worker Process (one per session)"). Esto da aislamiento, no K: **K_worker = 1 sesión por diseño**.
5. **Respuesta a la pregunta específica**: *estructuralmente sí, en la práctica del repo no*. Una extensión C++ en su propio grupo recibe `on_audio_frame` en su hilo nativo sin GIL (`core/include/ten_runtime/binding/cpp/detail/extension.h:proxy_on_audio_frame:236-253`), y el grafo puede enrutar a Python sólo `data`/`cmd`. Pero ningún ejemplo lo hace: en todos, el barge-in lo decide Python (`main_control`) al llegar un resultado ASR remoto, el VAD es Python (`EXT/ten_vad_python`) y la purga de salida es un `cmd flush` hacia `agora_rtc`, binario cerrado `[CÓDIGO]` `AG/agents/examples/voice-assistant/tenapp/ten_packages/extension/main_python/extension.py:_on_asr_result:83-94, _interrupt:203-213`. Es un patrón **latente**, no implementado.
6. **Contratos útiles para Golden (adaptar, no adoptar)**: (a) correlación cmd→`cmd_result` por `cmd_id` con *path table* y timeout que sintetiza error y **descarta resultados tardíos** cuya ruta ya expiró; (b) política de cola asimétrica: **descartar datos/audio al llenarse, nunca comandos**; (c) un hilo dueño por grupo con *runloop* + `post_task` y verificación de pertenencia de hilo (`thread_check`) en cada acceso; (d) inyección de jitter aleatorio en `post_task` en builds de depuración para destapar carreras.
7. **Lo que Golden no debe copiar**: hub de engine en el camino por trama, loop Python global para todas las extensiones, una `Task` por trama, colas de comandos sin tope, ausencia de identidad de generación en `audio_frame` (purga por orden, con carrera entre `flush` y audio en vuelo), `os._exit(1)` ante cualquier excepción Python (dominio de fallo = proceso), y ausencia de timeout de `on_stop` (el drenaje depende de un `SIGKILL` externo a los 2 s).
8. **Licencia**: las condiciones adicionales de Agora prohíben desplegar el framework (o derivados) de forma que **"compete with Agora's offerings"** o **"enabling any third party to develop or deploy Applications"**, y limitan el uso a "your Application(s) solely for your benefit and the benefit of your direct End Users". Copiar código del núcleo a Golden lo convertiría en *Derivative Work* sujeto a esas condiciones. **Recomendación: no copiar código del núcleo; reimplementar patrones.** Valor para Golden: **estudiar** (patrones) / **descartar** como dependencia.

---

## 3. Diagrama del flujo relevante

Ejemplo real `voice-assistant` (grafo en `AG/agents/examples/voice-assistant/tenapp/property.json`), un proceso por sesión. Grupos → hilos: `default` (agora_rtc, weatherapi), `ten:unspecified_group` (streamid_adapter), `stt`, `chatgpt`, `tts`, `control`, `transcriber`.

```text
 Go server (AG/server)  ── POST /start ──▶ fork "tman run start" (1 proceso = 1 canal/sesión; WORKERS_MAX admisión)
                                               │
┌──────────────────────────────────────────────┼─────────────── PROCESO TEN (app) ─────────────────────────────────────────────────┐
│  hilo APP (runloop libuv)  ◀── engine comparte este runloop si one_event_loop_per_engine=false (default, RT/app/metadata.c:403) │
│     └─ ENGINE del grafo (graph_id=UUID): cola in_msgs (mutex, SIN tope) ── hub de TODO mensaje inter-grupo                      │
│           ▲ (1)                ▲ (5)            ▲ (b)          │ (2)          │ (6)            │ (c)                            │
│  ┌────────┴───────┐   ┌────────┴─────────┐   ┌──┴─────────┐ ┌──▼───────────┐ ┌▼────────────┐ ┌▼───────────────┐                │
│  │ T_default (C)  │   │ T_unspecified (C)│   │ T_tts (C)  │ │T_unspecified │ │ T_stt (C)   │ │ T_default (C)  │                │
│  │ agora_rtc      │   │ streamid_adapter │   │ tts proxy  │ │ proxy        │ │ stt proxy   │ │ agora_rtc      │                │
│  │ [binario]      │   │  (re-envío)      │   │            │ │ GIL+rcts (3) │ │ GIL+rcts(7) │ │ play/flush     │                │
│  └───▲────────────┘   └────────▲─────────┘   └────▲───────┘ └──────┬───────┘ └─────┬───────┘ └───────▲────────┘                │
│      │ RTC in                  │ (4) post_task      │ (a) post_task  │                │                     │ RTC out             │
│      │                ┌────────┴────────────────────┴────────────────▼────────────────▼─────────────────────┼────────────┐       │
│      │                │ PythonGlobalMainThread (uvloop, UNO por proceso): streamid_adapter · stt(deepgram) · llm · tts  │       │
│      │                │ · main_control · message_collector — 1 Task por trama (run_coroutine_threadsafe)                │       │
│      │                │ main_control: asr_result ⇒ _interrupt(): flush_llm · data tts_flush→tts · cmd flush→agora_rtc   │       │
│      │                └──────────────────────────────────────────────────────────────────────────────────────────────────┘       │
└──────┼───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
   Agora SD-RTN (fuera del repo)

 Trama entrante: agora_rtc ─(1)→ engine ─(2)→ T_unspec ─(3)→ PyLoop ─(4)→ T_unspec ─(5)→ engine ─(6)→ T_stt ─(7)→ PyLoop → WS Deepgram
                 = 7 traspasos de hilo; 2 por el engine, 2 por el loop Python global, 2 adquisiciones de GIL.
 Trama TTS:      PyLoop ─(a)→ T_tts ─(b)→ engine ─(c)→ T_default(agora_rtc) = 3 traspasos.
 Barge-in:       decisión en Python tras ASR remoto; `flush` y audio TTS en vuelo llegan a agora_rtc por fuentes distintas vía engine (carrera, §8).
```

---

## 4. Mapa de procesos, threads, loops y colas

| Componente | Tipo | Hilos/loops | Cola | ¿Acotada? | Evidencia |
|---|---|---|---|---|---|
| Servidor de agentes | proceso Go | goroutines HTTP + `timeoutWorkers` cada 5 s | `workers = gmap.New(true)` (mapa en memoria) | admisión por `WORKERS_MAX` (ejemplo 100) | `[CÓDIGO]` `AG/server/internal/worker_common.go:52-56, timeoutWorkers:148-172`; `http_server.go:255-259`; `AG/.env.example:13` |
| Worker de sesión | proceso (`tman run start`, `Setpgid`) | 1 app TEN | — | — | `[CÓDIGO]` `worker_linux.go:31-35` |
| App | hilo + runloop (libuv por defecto) | 1 | `in_msgs` de app | no | `[CÓDIGO]` `UT/io/general/loops/runloop.c:runloop_factory:82-112` (libuv primero) |
| Engine (1 por grafo) | hilo propio **sólo** si `one_event_loop_per_engine=true`; si no, **reusa el runloop de la app** | 0 ó 1 | `in_msgs` (lista + mutex) | **no** | `[CÓDIGO]` `RT/engine/internal/thread.c:ten_engine_init_individual_eventloop_relevant_vars:99-115`, `ten_engine_create_its_own_thread:81-97`; `RT/engine/engine.c:ten_engine_get_attached_runloop:239-252`; default `false` en `RT/app/metadata.c:403` |
| Extension thread (1 por grupo por grafo) | hilo + runloop | 1 por grupo | cola de tareas del runloop (lista enlazada + mutex) | **sólo para datos**: si `size ≥ 12800` se **descarta** el mensaje no-cmd; cmds y cmd_results se encolan siempre | `[CÓDIGO]` `INC/extension_thread/extension_thread.h:21,70-71`; `RT/extension_context/extension_context.c:453-460`; `RT/engine/msg_interface/common.c:325-334` |
| Grupo implícito | nodos sin `extension_group` van a `"ten:unspecified_group"` (un solo hilo compartido) | 1 | idem | idem | `[CÓDIGO]` `RT/extension/extension_info/extension_info.c:210-213`; `INC/common/constant_str.h:191` |
| Pendientes de arranque | listas `pending_msgs_received_in_init_stage` (hilo) y `pending_msgs_received_before_on_init_done` (extensión) | — | listas | **no** | `[CÓDIGO]` `RT/extension_thread/msg_interface/common.c:122-137`; `RT/extension/msg_handling.c:55-61` |
| Loop Python global | `threading.Thread("PythonGlobalMainThread")` + uvloop, singleton por proceso | 1 por proceso (modo `single_thread`, default) | cola de `call_soon_threadsafe` de asyncio | **no** | `[CÓDIGO]` `PY/interface/ten_runtime/global_thread_manager.py:21-27, 75-81, 110-129` |
| Loop Python por extensión | modo `multi_thread`: 1 hilo + loop asyncio por instancia de extensión | N | idem | no | `[CÓDIGO]` `async_extension.py:_proxy_on_configure_multi_thread:202-227` |
| Python síncrono (`Extension`) | callback en el hilo C del grupo, con GIL | — | — | — | `[CÓDIGO]` `PY/interface/ten_runtime/extension.py:_proxy_on_audio_frame:104-107` |
| Timers de path | 2 por extensión (IN/OUT), intervalo 10 s, timeout 3 min | en el hilo del grupo | — | — | `[CÓDIGO]` `INC/extension/path_timer.h:12-15`; `RT/extension/internal/path_timer.c:116-154` |

Drenaje del runloop: `process_remaining_tasks_safe` vacía la lista **hasta que esté vacía**, incluidas tareas añadidas durante el vaciado `[CÓDIGO]` `UT/io/general/loops/runloop.c:194-218`. Con productores sostenidos esto puede prolongar el vaciado y retrasar timers/IO del mismo loop `[INFERENCIA]`.

---

## 5. Ownership por sesión y recursos compartidos

- **Unidad de ownership**: el **grafo** (= engine, identificado por `graph_id` UUID generado en `ten_engine_set_graph_id`) `[CÓDIGO]` `RT/engine/engine.c:113-157`. Dentro, cada **extensión** es propiedad exclusiva del hilo de su grupo; la pertenencia se transfiere explícitamente al arrancar el hilo (`ten_extension_thread_inherit_thread_ownership:225-252`, `ten_extension_thread_add_all_created_extensions:587-649`) y se **verifica en cada acceso** con `ten_sanitizer_thread_check_do_check` (`UT/sanitizer/thread_check.c:123-148`), invocado desde los `*_check_integrity(self, true)` que abren casi todas las funciones `[CÓDIGO]`.
- **Sesión ↔ grafo**: el runtime no tiene noción de "llamada". En el despliegue de referencia, sesión = canal = proceso; el grafo se arranca como *predefined graph* con `auto_start` `[CÓDIGO]` `AG/server/internal/http_server.go:handlerStart:236-320`. Un mismo app **puede** alojar varios grafos (cada `start_graph` sin `graph_id` crea engine nuevo) `[CÓDIGO]` `RT/app/msg_interface/start_graph.c:218-227`, `RT/app/engine_interface.c:ten_app_create_engine:102-132`.
- **Compartido dentro del proceso**: hilo de la app (y, por defecto, el runloop de todos los engines), el loop Python global, el intérprete y el GIL, el registro de addons, `"ten:unspecified_group"` (dentro de un grafo).
- **Local por extensión**: `path_table` (IN/OUT), timers, propiedades, estado de usuario (p. ej. `TenVad` instanciado en `on_start` de cada `ten_vad_python`) `[CÓDIGO]` `EXT/ten_vad_python/extension.py:on_start:72-73`.
- **Estado de conversación en el ejemplo**: `main_control` mantiene `turn_id`, `session_id`, `sentence_fragment` en atributos de la instancia `[CÓDIGO]` `.../voice-assistant/tenapp/ten_packages/extension/main_python/extension.py:34-47`; no hay `epoch`.

---

## 6. Contrato de control/IPC

| aspecto | TEN | Evidencia |
|---|---|---|
| Tipos de mensaje | `cmd` (con `cmd_result`), `data`, `audio_frame`, `video_frame` + cmds internos (`start_graph`, `stop_graph`, `timer`, `timeout`, `trigger_life_cycle`, `close_app`) | `[CÓDIGO]` `RT/extension/msg_handling.c:238-258`; `RT/msg/cmd_base/cmd/*` |
| Direccionamiento | `loc = (app_uri, graph_id, extension_name)`; si el emisor no fija `dest`, el destino sale del grafo (conexiones declarativas por nombre de mensaje) | `[CÓDIGO]` `RT/extension/extension.c:ten_extension_determine_out_msgs:465-503`, `..._from_graph:360-457` |
| Enrutado intra-proceso | mismo grupo ⇒ llamada **síncrona y reentrante** en el mismo hilo; otro grupo ⇒ engine; otro grafo ⇒ app; otra app ⇒ `remote` (conexión de red, protocolo msgpack en `packages/core_protocols/msgpack`) | `[CÓDIGO]` `RT/extension_thread/msg_interface/common.c:212-295` |
| Correlación | sólo para cmds: `cmd_id` (generado si falta, `ten_cmd_base_gen_cmd_id_if_empty`), OUT path en el emisor, IN path en el receptor; el `cmd_result` vuelve por la tabla, no por `dest` | `[CÓDIGO]` `RT/engine/msg_interface/common.c:71-76`; `RT/extension/extension.c:600-622`; `RT/path/path_table.c:ten_path_table_add_in_path:202-271, add_out_path:273-312` |
| ACK | `cmd_result` con `is_final`; los resultados de *fan-out* se agregan en `path_group` según `result_return_policy` | `[CÓDIGO]` `RT/extension/extension.c:619-622`; `RT/path/path_table.c:467+` |
| Mensajes tardíos | si la ruta ya no existe (expirada o completada), el `cmd_result` **se descarta** silenciosamente | `[CÓDIGO]` `RT/path/path_table.c:ten_path_table_process_cmd_result:480-489`; `RT/extension/msg_handling.c:85-94`; `RT/extension/extension.c:566-573` |
| Timeout | path timeout por defecto **3 min**, revisado cada **10 s**; OUT expirado ⇒ se sintetiza `cmd_result` ERROR "Path timeout." y se entrega al emisor; IN expirado ⇒ se borra (un resultado posterior del receptor se descarta) | `[CÓDIGO]` `INC/extension/path_timer.h:12-15`; `RT/extension/internal/path_timer.c:26-114` |
| Destino ausente | cmd ⇒ `cmd_result` ERROR ("Failed to find destination" / "The extension[...] is invalid" / "destination extension has been terminated"); datos/audio ⇒ se descartan (sólo log) | `[CÓDIGO]` `RT/extension_thread/msg_interface/common.c:63-91`; `RT/engine/msg_interface/common.c:350-362, 480-497` |
| Datos/audio | **sin** id, sin seq, sin ACK, sin dedup; el runtime sólo añade `timestamp` interno para métrica | `[CÓDIGO]` `RT/engine/msg_interface/common.c:338-340`; campos de `audio_frame` en `RT/msg/audio_frame/pcm_frame.c:ten_raw_audio_frame_as_msg_clone:341-380` |
| Copia vs referencia | 1 destino ⇒ se pasa el `shared_ptr` (sin copia); destinos 2..N ⇒ `ten_msg_clone` (copia profunda del buffer si `owns_memory`). Python: `get_buf()` **copia** a `bytearray`; `lock_buf()` da `memoryview` sin copia con bloqueo de recurso | `[CÓDIGO]` `RT/extension/extension.c:need_to_clone_msg_when_sending:296-313`; `UT/value/value.c:ten_value_copy_buf:494-511`; `PY/native/msg/audio_frame.c:lock_buf:124-145, get_buf:188-203` |
| Frontera Python → C | `send_*` desde el loop Python ⇒ `ten_env_proxy_notify(sync=false)` ⇒ `post_task_tail` al runloop del hilo de la extensión | `[CÓDIGO]` `PY/native/ten_env/ten_env_send_audio_frame.c:149-199`; `RT/ten_env_proxy/internal/notify.c:89-166` |
| Frontera C → Python | callback en hilo C ⇒ GIL ⇒ `_proxy_on_*` ⇒ `run_coroutine_threadsafe` al loop global | `[CÓDIGO]` `PY/native/extension/extension.c:322-357`; `async_extension.py:323-419` |

Comparado con el contrato de referencia Golden `(session_id, session_epoch, turn_id, speech_id, seq)`: TEN aporta **sólo** la correlación de comandos (`cmd_id` + ruta con caducidad). `session_id`, `turn_id` y `request_id` existen únicamente como **propiedades de usuario** en los mensajes de los ejemplos (`metadata`, `request_id = f"tts-request-{turn_id}"`) `[CÓDIGO]` `.../main_python/extension.py:_send_to_tts:183-201`; `EXT/streamid_adapter/extension.py:37-46`. No hay epoch ni seq en ninguna capa.

---

## 7. Ruta de audio y frecuencia de cruces

1. **Entrada RTC**: `agora_rtc` (binario, `[PENDIENTE]` su interior) emite `audio_frame "pcm_frame"` hacia `streamid_adapter` `[CÓDIGO]` grafo `voice-assistant/tenapp/property.json` (conexión `agora_rtc.audio_frame.pcm_frame → streamid_adapter`).
2. **Cruce de grupo ⇒ engine**: `ten_extension_thread_dispatch_msg` ve grupo distinto ⇒ `ten_engine_append_to_in_msgs_queue` (mutex + `post_task_tail` de una tarea `ten_engine_handle_in_msgs_task` por mensaje; cada tarea vacía la lista entera) `[CÓDIGO]` `RT/extension_thread/msg_interface/common.c:270-274`; `RT/engine/msg_interface/common.c:246-267, 286-303, 90-196`.
3. **Engine ⇒ hilo destino**: búsqueda lineal del hilo por nombre de grupo, chequeo de tope 12800 (descarte de datos), `shared_ptr_clone`, `post_task_tail` `[CÓDIGO]` `RT/engine/msg_interface/common.c:437-478, 305-344`.
4. **Hilo destino ⇒ Python**: `proxy_on_audio_frame` toma el GIL, envuelve el frame (refcount, sin copia), llama a `_proxy_on_audio_frame`, que hace `run_coroutine_threadsafe(self._wrapper_on_audio_frame(...), main_loop)` y vuelve; el hilo C suelta el GIL `[CÓDIGO]` `PY/native/extension/extension.c:334-357`; `async_extension.py:394-419`.
5. **`streamid_adapter` (Python)** sólo añade `metadata.session_id` y reenvía `[CÓDIGO]` `EXT/streamid_adapter/extension.py:on_audio_frame:31-48` ⇒ pasos 2–4 otra vez hasta `stt` (Python, WS remoto).

Frecuencia: **cada trama de audio entrante cruza 2 veces el engine, 2 veces el loop Python global y adquiere 2 veces el GIL** en el grafo `voice-assistant` (7 traspasos de hilo). Con `ten_vad_python` (grafo `voice-assistant-with-ten-vad`), `streamid_adapter` hace *fan-out* a `stt` **y** `vad`: una copia profunda adicional por trama y el VAD corre **síncrono** dentro de la corrutina (`self.vad.process(...)`) en el loop global, que además **reenvía** cada trama recibida (`_send_audio_frame`, copia a un `AudioFrame` nuevo) `[CÓDIGO]` `EXT/ten_vad_python/extension.py:on_audio_frame:162-193, _send_audio_frame:131-150`.

Tamaño de trama: lo fija la extensión RTC (`[PENDIENTE]` para `agora_rtc`); en el ejemplo Twilio, una trama Twilio = un `AudioFrame` `[CÓDIGO]` `AG/agents/examples/voice-assistant-sip-twilio/tenapp/ten_packages/extension/main_python/extension.py:_forward_audio_to_ten:372-415`.

**Comparación con Golden**: Golden paga por mensaje de audio `submit` al pool + `resume` en un loop compartido por todas las sesiones. TEN-ejemplo paga, por trama y por sesión, 2× (GIL + `run_coroutine_threadsafe` + creación de `Task`) en un loop Python compartido por todas las extensiones del proceso, más 2 saltos por el engine. Es **más** coste de coordinación por trama, no menos; sólo es tolerable porque el proceso tiene una sesión `[INFERENCIA]`.

---

## 8. Secuencia de barge-in

Ejemplo `voice-assistant` (el único patrón de interrupción en el repo; `ten_ai_base` y `agora_rtc` no están):

1. STT remoto (Deepgram) devuelve parcial/final ⇒ extensión `stt` (Python) emite `data asr_result` ⇒ `main_control` (Python) `[CÓDIGO]` grafo `connections[0].data` (`asr_result` desde `stt`).
2. `_on_asr_result`: si `final` o `len(text) > 2` ⇒ `_interrupt()` `[CÓDIGO]` `main_python/extension.py:83-94`.
3. `_interrupt()`: vacía `sentence_fragment`; `agent.flush_llm()` (cancela la generación en el loop); `data tts_flush {flush_id: uuid4}` → `tts`; `cmd flush` → `agora_rtc` `[CÓDIGO]` `main_python/extension.py:203-213`.
4. `tts` purga su estado `[PENDIENTE: ten_ai_base]`; `agora_rtc` purga su buffer de reproducción `[PENDIENTE: binario]`.

**Dónde ocurre**: en Python, en el loop global, y **disparado por texto ASR remoto**, no por VAD local. Latencia = RTT del STT + saltos de §7 + saltos de vuelta (`main_control` → T_control → engine → T_default) `[INFERENCIA]`.

**Qué ya no puede retirarse**: las tramas TTS que ya salieron hacia Agora; y, por diseño del runtime, cualquier trama TTS que `tts` haya emitido antes de procesar `tts_flush` y esté en vuelo en `T_tts`, en `engine.in_msgs` o en la cola de `T_default`. `flush` (desde `T_control`) y el audio (desde `T_tts`) llegan a `agora_rtc` por fuentes distintas mezcladas en la FIFO del engine, sin identidad de generación en `audio_frame`, así que audio emitido *antes* del flush puede llegar *después* y reproducirse `[INFERENCIA desde CÓDIGO]` (`RT/engine/msg_interface/common.c:286-303, 342-344`; ausencia de campo de generación en `pcm_frame.c:355-365`). Que `agora_rtc` o `tts` filtren por `request_id`/`flush_id` es `[PENDIENTE]`.

**Antiejemplo telefónico (Twilio)**: el `main_control` del ejemplo SIP recibe el audio TTS y lo envía **"to all active Twilio calls"** (sin identidad de sesión); su `_interrupt()` envía `cmd flush` a `agora_rtc`, extensión que **no existe en ese grafo** (⇒ `cmd_result` ERROR "Failed to find destination"), y nunca envía el evento `clear` de Twilio: la cola remota de reproducción no se purga `[CÓDIGO]` `.../voice-assistant-sip-twilio/.../main_python/extension.py:on_audio_frame:261-274, _interrupt:347-357, send_audio_to_twilio:518-558`; nodos del grafo sin `agora_rtc` (`property.json`). Es exactamente el hueco que el brief Golden documenta en ai-engine ("no hay flush de reproducción para este proveedor").

**Contraste Golden**: Golden ya decide barge-in localmente con VAD y recorta el historial a lo reproducido; TEN-ejemplo no tiene equivalente a "spoken_text" truncado `[INFERENCIA, búsqueda: ningún recorte de historial en main_python]`.

---

## 9. Backpressure, fallos, reconexión y drenaje

**Backpressure**
- Única medida: descarte de mensajes **no-cmd** cuando la cola del hilo destino ≥ 12800 tareas, sólo en el salto engine→hilo `[CÓDIGO]` `RT/engine/msg_interface/common.c:325-334`. No se aplica al salto mismo-grupo, al `in_msgs` del engine, ni al loop Python. Cmds: sin tope.
- 12800 tramas de 10 ms ≈ 128 s de audio en cola antes de descartar `[INFERENCIA]`: el tope protege memoria, no latencia.
- El descarte es por hilo de grupo, no por sesión; como en la referencia hay una sesión por proceso, no afecta a otras llamadas. En multi-grafo por proceso, un grupo saturado no frena a otros grupos, pero el engine compartido (runloop de la app por defecto) y el loop Python global **sí** serían puntos de bloqueo comunes `[INFERENCIA]`.
- No hay métrica de lag del loop Python. Las métricas `extension_callback_execution_duration` y `extension_thread_msg_queue_stay_time` miden el lado C: para una `AsyncExtension` el callback C termina en cuanto programa la corrutina, así que **no ven** la cola real (asyncio) `[INFERENCIA desde CÓDIGO]` `RT/extension/extension.c:on_audio_frame:927-961`; `async_extension.py:394-419`.

**Fallos**
- Excepción no capturada en cualquier handler Python asíncrono ⇒ `os._exit(1)`: muere el proceso entero `[CÓDIGO]` `async_extension.py:_exit_on_exception:481-500`.
- Callback bloqueante: sólo `TEN_LOGW` si supera 100 ms (`TEN_EXTENSION_ON_XXX_WARNING_THRESHOLD_US`), sin watchdog `[CÓDIGO]` `INC/extension/extension.h:36`; `RT/extension/extension.c:942-953`.
- Cmd a extensión muerta o en `on_deinit` ⇒ `cmd_result` ERROR para no colgar al emisor; datos ⇒ descarte `[CÓDIGO]` `RT/extension/msg_handling.c:63-80`; `RT/engine/msg_interface/common.c:350-362`.
- Muerte de una conexión remota (multi-app) ⇒ el engine se cierra salvo `long_running_mode` `[CÓDIGO]` `RT/engine/internal/remote_interface.c:193-201`.
- Muerte del proceso de sesión ⇒ el Go server lo quita del mapa (`cmd.Wait()`) `[CÓDIGO]` `worker_linux.go:101-118`; no hay reinicio ni reasignación.

**Reconexión**: ninguna a nivel de runtime para extensiones; la reconexión a proveedores (WS STT/TTS) es responsabilidad de cada extensión `[PENDIENTE: ten_ai_base]`.

**Drenaje**
- Cierre de grafo: engine → `ten_extension_thread_close` → `on_stop` de cada extensión → espera `on_stop_done` → `on_deinit` `[CÓDIGO]` `RT/extension_thread/extension_thread.c:343-399`; `RT/extension/internal/close.c`. **Sin timeout de `on_stop`** en el runtime (búsqueda negativa de "timeout" en `close.c` de extensión/engine/hilo, sólo timers de path) `[CÓDIGO, búsqueda negativa]`.
- Proceso: el Go server manda `SIGTERM` al grupo, espera ≤ 2 s (20 × 100 ms) y luego `SIGKILL` `[CÓDIGO]` `worker_linux.go:Worker.stop:123-171`. Loop Python: cancela tareas pendientes con 0,5 s de gracia `[CÓDIGO]` `global_thread_manager.py:_cleanup_pending_tasks:137-186`.
- Inactividad: `timeoutWorkers` mata sesiones sin `/ping` en `QuitTimeoutSeconds` (default 60) `[CÓDIGO]` `worker_common.go:60-66, 148-172`.
- No hay drenaje de "no aceptar nuevas, terminar las vivas" a nivel de nodo `[CÓDIGO, búsqueda]`.

---

## 10. Escalado y uso real de núcleos

| nivel | TEN (referencia) | Evidencia |
|---|---|---|
| **K_loop** | Dos loops críticos por proceso: el engine (hub inter-grupo por grafo; por defecto el runloop de la app) y el loop Python global. Ninguna medición en el repo. | `[PENDIENTE]`; no hay benchmark de mensajería en `tests/` (sólo `tests/ten_utils/unit/ten_rwlock_test.cc` menciona benchmark) |
| **K_worker** | **1 sesión por proceso** por diseño del servidor de agentes. | `[CÓDIGO]` `worker_linux.go:31`; `[DOCUMENTACIÓN]` `docs/ai/L1/02_architecture.md:93` |
| **K_task** | `WORKERS_MAX` = cupo de admisión (ejemplo 100), **no** capacidad medida; cada sesión trae su intérprete, extensiones y modelos. | `[CÓDIGO]` `AG/.env.example:13`; `http_server.go:255-259` |
| **K_flota** | No existe capa de flota: el estado de sesiones vive en un `gmap` del proceso Go; no hay routing entre nodos ni autoescalado. | `[CÓDIGO]` `worker_common.go:52-56` |

Núcleos: dentro de un proceso, el paralelismo real es **un hilo nativo por grupo** (C/C++/Go). Para Python asíncrono, todo el trabajo cae en un hilo (modo `single_thread`) o en N hilos que se serializan en el GIL (modo `multi_thread`) porque el binding no soporta free-threading `[CÓDIGO]` + `[INFERENCIA]`. El uso de varios núcleos en la práctica proviene de **muchos procesos**, uno por sesión: **replicación**, no paralelización del coordinador. Si se alojaran 100 sesiones como 100 grafos en un proceso, con 7 grupos por grafo se tendrían ~700 hilos nativos, un loop Python y, por defecto, un runloop de app actuando de hub para los 100 engines `[INFERENCIA desde CÓDIGO]` `RT/engine/engine.c:239-252`, `RT/app/metadata.c:403`.

---

## 11. Patrones y antipatrones

**Implementados (útiles)**
- *Active Object / hilo dueño*: grupo ⇒ hilo con runloop; acceso externo sólo por `post_task`; `thread_check` verifica la pertenencia en cada llamada `[CÓDIGO]` `RT/extension_thread/extension_thread.c:254-341`; `UT/sanitizer/thread_check.c:123-148`.
- *Request–Reply con ruta caducable*: tabla de rutas IN/OUT por `cmd_id`, timeout que sintetiza error, descarte de resultados huérfanos `[CÓDIGO]` §6.
- *Bounded mailbox por clase*: descartar datos al llenarse, nunca control `[CÓDIGO]` `RT/engine/msg_interface/common.c:325-334`.
- *Cola de pre-arranque*: los mensajes que llegan antes de `on_init_done` se aparcan y se entregan después; los cmd_results sí se entregan antes `[CÓDIGO]` `RT/extension/msg_handling.c:41-61`.
- *Respuesta obligatoria a cmd*: si el destino desaparece o está cerrando, el runtime responde ERROR para que el emisor no se cuelgue `[CÓDIGO]` `RT/extension/msg_handling.c:63-80`.
- *Chaos en debug*: `ten_random_sleep_range_ms(0, 20)` antes y después de encolar cada tarea en builds `_DEBUG` `[CÓDIGO]` `UT/io/general/loops/runloop.c:671-674, 690-693`.
- *Grafo declarativo*: el emisor no conoce el destino; la topología se fija en JSON (`property.json`) y se valida/aplana con Rust (`RT/app/msg_interface/start_graph.c:106-170` bajo `TEN_ENABLE_TEN_RUST_APIS`) `[CÓDIGO]`.

**Latentes**
- *Media nativa + control Python*: extensión C++ en grupo propio con transporte, VAD y reproducción, que emite a Python sólo `data`/`cmd` semánticos. El runtime lo permite (§13), el repo no lo implementa.
- *Engine con hilo propio* (`one_event_loop_per_engine=true`) para aislar hubs por grafo `[CÓDIGO]` `RT/engine/internal/thread.c:99-115`.

**Antipatrones (no copiar)**
- Hub del engine en el camino por trama inter-grupo.
- Loop Python global singleton + una `Task` por trama vía `run_coroutine_threadsafe`.
- VAD síncrono dentro del loop asyncio compartido (`ten_vad_python`).
- Extensión Python sólo para reetiquetar cada trama (`streamid_adapter`).
- Colas de cmd y de `in_msgs` sin tope; descarte de datos **silencioso para el emisor** (sólo `TEN_LOGW`).
- Ausencia de identidad de generación en audio: la purga depende del orden y hay carrera (§8).
- Fan-out = copia profunda por destino adicional.
- `os._exit(1)` ante cualquier excepción de usuario.
- Sin timeout de `on_stop`; drenaje delegado a `SIGKILL`.
- Ejemplo telefónico: audio a "todas las llamadas activas" y `flush` a un destino inexistente.

---

## 12. Callers verificados de las piezas propuestas como referencia

| pieza | definición | callers verificados |
|---|---|---|
| `ten_engine_append_to_in_msgs_queue` | `RT/engine/msg_interface/common.c:286-303` | `RT/extension_thread/msg_interface/common.c:245, 261, 274, 329` (dispatch y cmd_result del hilo) |
| `ten_engine_post_msg_to_extension_thread` (tope 12800) | `RT/engine/msg_interface/common.c:305-363` | `ten_engine_dispatch_msg:473` ← `ten_engine_handle_in_msgs_sync:168` ← `ten_engine_handle_in_msgs_task:223` ← `ten_engine_handle_in_msgs_async:258-260` |
| `ten_extension_thread_dispatch_msg` | `RT/extension_thread/msg_interface/common.c:212-295` | `RT/extension/extension.c:ten_extension_dispatch_msg:630-636` (bucle sobre `result_msgs`) |
| `ten_path_table_process_cmd_result` (descarte de tardíos) | `RT/path/path_table.c:467+` | `RT/extension/msg_handling.c:87-88` (OUT, al recibir); `RT/extension/extension.c:567-568` (IN, al devolver) |
| timers de path | `RT/extension/internal/path_timer.c:116-154` | callbacks `..._in_path_timer_on_triggered:26-59`, `..._out_path_timer_on_triggered:61-114` → `ten_extension_handle_in_msg` (en `path_timer.c:110`); cierre en `RT/extension/internal/close.c:147-154` |
| `ten_env_proxy_notify` (Python→hilo) | `RT/ten_env_proxy/internal/notify.c:89-166` | `PY/native/ten_env/ten_env_send_audio_frame.c:184-185` (y análogos `send_data/send_cmd`) |
| `GlobalThreadManager.get_or_start_thread` | `global_thread_manager.py:49-101` | `async_extension.py:_proxy_on_configure_single_thread:187`; `get_thread()` en `_proxy_on_init/_start/_stop/_deinit/_cmd/_data/_audio_frame:236, 262, 288, 314, 333, 355, 406` |
| `proxy_on_audio_frame` (C→Python) | `PY/native/extension/extension.c:322-357` | registrado en la tabla de callbacks `PY/native/extension/extension.c:426`; invocado por `RT/extension/extension.c:ten_extension_on_audio_frame:942` ← `RT/extension/msg_handling.c:246-247` |
| `thread_check_do_check` | `UT/sanitizer/thread_check.c:123-148` | `RT/extension_thread/extension_thread.c:75`, `RT/engine/engine.c:44` y todos los `*_check_integrity(…, true)` |
| `Worker.start/stop` (proceso por sesión) | `AG/server/internal/worker_linux.go:28-171` | `http_server.go:handlerStart:312`, `handlerStop:347`, `worker_common.go:timeoutWorkers:160`, `CleanWorkers:174-201` |

---

## 13. Aplicabilidad a GoldenBridge

**Pregunta específica — ¿permite conservar audio y barge-in en una extensión nativa mientras Python recibe sólo eventos de conversación?**

- **Sí, como capacidad del runtime** `[CÓDIGO]`: (i) el callback C++ `on_audio_frame` corre en el hilo del grupo, sin GIL (`core/include/ten_runtime/binding/cpp/detail/extension.h:236-253`); (ii) si transporte, VAD, pacer y purga están en **el mismo grupo**, el intercambio de audio entre ellos es una llamada síncrona en ese hilo y **no pasa por el engine** (`RT/extension_thread/msg_interface/common.c:276-279`); (iii) el grafo puede declarar que a la extensión Python sólo le lleguen `data` (`asr_result`, `speech_started`…) y que ella sólo emita `data`/`cmd` (`tts_text_input`, `flush`), como ya hace `main_control` con texto.
- **No, en lo que el repo entrega** `[CÓDIGO]`: el transporte nativo de los ejemplos es `agora_rtc` (cerrado); para WebSocket sólo existe `EXT/websocket_server` en Python; el VAD es Python; el barge-in se decide en Python sobre ASR; STT y TTS son extensiones Python que reciben cada trama. Para Golden habría que **escribir** en C++ la extensión de transporte WS (protocolo ai-engine), Silero/SmartTurn sobre ORT C API, el pacer y la purga: es decir, **construir el MediaWorker nativo**; TEN sólo aportaría el envoltorio de mensajería.
- Aun así, el resultado sería un **MediaWorker por grafo** (= por llamada) con hilos propios, no un MediaWorker que atienda muchas llamadas; para K=100 por tarea habría que alojar 100 grafos por proceso, donde reaparecen el runloop de app como hub (salvo `one_event_loop_per_engine=true`) y el loop Python global `[INFERENCIA]`.

**Qué sí se traslada (como patrón, reescrito en Golden)**
1. Correlación de comandos C→M con `cmd_id` + caducidad + descarte de respuestas huérfanas ⇒ `Speak`/`CancelSpeech`/`SendFunctionCall` con ACK y timeout corto (no 3 min), y `TurnCompleted`/`SpeechInterrupted` tardíos descartados por ruta inexistente.
2. Cola asimétrica: el *mailbox* de cada sesión en el MediaWorker descarta (o fusiona) audio al superar un tope **en ms**, nunca comandos; y lo **cuenta** en una métrica por sesión (TEN sólo loguea).
3. Hilo dueño verificable: en Python 3.14t, cada `SessionState` guarda `owner_thread_id` y una aserción barata en accesos de depuración (equivalente a `thread_check`), útil para el refactor a MediaWorker por hilo.
4. Jitter inyectado en depuración en el `post`/`submit` entre loop y pool para destapar carreras de barge-in.
5. Respuesta obligatoria a comandos cuyo destino ya cerró ("session closed"), para que Conversación no espere indefinidamente.

**Qué no se traslada**: el grafo declarativo como mecanismo de enrutado por trama, el hub por grafo, el loop Python global, el proceso por sesión (no eleva K: lo fija en 1), y cualquier código del núcleo (licencia).

---

## 14. Riesgos de adopción o copia

- **Licencia (alto)**. Texto literal de `LICENSE` (raíz) `[CÓDIGO]`:
  > "1. You may not (i) host the TEN Framework or the Derivative Works on any End User devices, including but not limited to any mobile terminal devices or (ii) Deploy the TEN Framework in a way that competes with Agora's offerings and/or that allows others to compete with Agora's offerings, including without limitation enabling any third party to develop or deploy Applications.
  > 2. You may Deploy the TEN Framework solely to create and enable deployment of your Application(s) solely for your benefit and the benefit of your direct End Users. […]
  > 3. Derivative Works of the TEN Framework remain subject to this Open Source License."

  Riesgos `[INFERENCIA, requiere revisión legal]`: (a) un motor de voz IA para campañas de banca puede interpretarse como oferta que **compite** con la de Agora (conversational AI); (b) si Golden se presta como servicio a bancos que configuran sus propios agentes, podría leerse como "enabling any third party to … deploy Applications"; (c) "direct End Users" es ambiguo para un servicio B2B; (d) cualquier fragmento copiado del núcleo arrastra la condición 3. `AG/` es Apache 2.0 limpio, pero sus extensiones dependen del runtime condicionado. **Conclusión operativa: no incorporar ni copiar código del núcleo; reimplementar ideas.**
- **Técnico (alto)**: sin free-threading; exigiría reescribir Golden como extensiones TEN; el camino de audio útil (nativo) no existe y habría que construirlo; dependencia de registro TEN (`tman`) y de paquetes binarios.
- **Operativo (medio)**: `os._exit(1)` convierte cualquier error de usuario en caída del proceso; sin watchdog, sin timeout de `on_stop`; observabilidad ciega al loop Python.
- **Encaje Modal (alto)**: el modelo "proceso por sesión vía `fork` de `tman`" choca con un endpoint Modal que recibe WS como inputs concurrentes en un contenedor `[INFERENCIA]`.

---

## 15. Lecciones concretas que pasan a la matriz comparativa

1. **Los hubs reaparecen en los runtimes de grafo**: un framework de extensiones con un hilo por grupo **sigue teniendo coordinador por trama** si el enrutado inter-grupo pasa por un engine. Para Golden: dentro de un MediaWorker, VAD, pacer, purga y transporte de una sesión deben ejecutarse en el mismo hilo/loop dueño, sin un *dispatcher* intermedio. `[CÓDIGO]` `RT/extension_thread/msg_interface/common.c:270-279`.
2. **"Python sólo para eventos" exige que la frontera esté antes del primer salto a Python**: en TEN, en cuanto el STT/VAD/adaptador es Python, cada trama entra al loop global. El criterio para Golden es medible: **0 corrutinas creadas por trama en el loop de Conversación**. `[CÓDIGO]` `async_extension.py:394-419`.
3. **Purga por orden sin generación es insuficiente**: `flush` y audio salen de hilos distintos y se mezclan en una FIFO común; hace falta `speech_id`/epoch en cada trama de salida y descarte en el último salto (pacer/ai-engine). `[CÓDIGO]` `RT/engine/msg_interface/common.c:286-303`; `.../main_python/extension.py:203-213`.
4. **Política de cola por clase**: datos descartables con tope, control nunca descartado; añadir lo que TEN no tiene: tope en ms por sesión y contador expuesto. `[CÓDIGO]` `RT/engine/msg_interface/common.c:325-334`.
5. **Correlación de comandos con caducidad y descarte de huérfanos** es reutilizable como diseño del contrato C→M. `[CÓDIGO]` `RT/extension/internal/path_timer.c:61-114`; `RT/path/path_table.c:480-489`.
6. **Proceso por sesión = K_worker 1**: aísla, pero sólo replica; descartar como estrategia para K=100. `[CÓDIGO]` `AG/server/internal/worker_linux.go:31`.
7. **Observabilidad**: medir la cola donde realmente espera el trabajo (loop asyncio), no sólo la cola del hilo nativo. `[INFERENCIA desde CÓDIGO]` `RT/extension/extension.c:927-961`.

---

## Respuestas explícitas a las 18 preguntas obligatorias

1. **Unidad de ownership**: grafo (engine, `graph_id`) → grupo (hilo) → extensión. Sesión = proceso en la referencia. `[CÓDIGO]` §5.
2. **¿Quién recibe cada paquete de audio?** La extensión de transporte (`agora_rtc`, binario; o Python `main_control` en Twilio) y luego, por grafo, cada extensión destino en el hilo de su grupo; las Python lo reciben en el loop global. `[CÓDIGO]` §7.
3. **¿Cuántas veces cruza el runtime de control cada paquete?** En `voice-assistant`: 2 veces el engine y 2 veces el loop Python global por trama entrante (7 traspasos de hilo); 1 vez el engine por trama TTS saliente. `[CÓDIGO]` + `[INFERENCIA]` §3, §7.
4. **¿Loop único compartido?** Sí, dos: el runloop de la app (hub de todos los engines si `one_event_loop_per_engine=false`, default) y el loop Python global (todas las `AsyncExtension` del proceso). Trabajo: enrutado inter-grupo; todos los handlers Python, incluido VAD y reetiquetado por trama. `[CÓDIGO]` `RT/app/metadata.c:403`; `global_thread_manager.py:15-27`.
5. **¿Cómo usa varios núcleos?** Hilos nativos por grupo; Python asíncrono en un hilo (o N con GIL); en la práctica, **procesos por sesión** (réplicas). `[CÓDIGO]` §10.
6. **Asignación y afinidad**: una sesión = un proceso lanzado por el servidor Go con `channel_name` como clave; la afinidad es total porque el proceso es de la sesión; no hay balanceo entre nodos. `[CÓDIGO]` `http_server.go:236-320`.
7. **Estado compartido vs local**: compartido = app, runloop de app, intérprete/GIL, loop Python, grupo implícito; local = `path_table`, timers, estado de cada extensión. `[CÓDIGO]` §5.
8. **Protocolo de control**: memoria intra-proceso (`shared_ptr` + `post_task` sobre runloops con mutex); entre apps, conexiones de red con protocolo msgpack; Go server ↔ worker por HTTP local (`/cmd`). `[CÓDIGO]` `RT/ten_env_proxy/internal/notify.c`; `packages/core_protocols/msgpack`; `worker_common.go:update:120-146`.
9. **Orden, correlación, ACK, dedup, tardíos**: FIFO por runloop; mismo origen→mismo destino conserva orden; fuentes distintas se intercalan en el engine; correlación y ACK sólo para cmds (`cmd_id`, `cmd_result`, `is_final`); sin dedup; tardíos de cmd descartados por ruta inexistente; datos/audio sin nada de esto. En Python, una `Task` por mensaje: el orden de inicio es FIFO, pero handlers con `await` antes de emitir pueden intercalarse `[INFERENCIA]`. `[CÓDIGO]` §6.
10. **Backpressure sin frenar llamadas ajenas**: descarte de datos por hilo de grupo a 12800; en la referencia, el aislamiento real lo da el proceso por sesión. `[CÓDIGO]` §9.
11. **¿Dónde ocurre el barge-in y dónde se purga?** Decisión en Python (`main_control`) por resultado ASR; purga en `tts` (`tts_flush`) y en `agora_rtc` (`cmd flush`), ambas fuera del repo. `[CÓDIGO]` §8; `[PENDIENTE]` interior de la purga.
12. **Audio no retirable**: lo ya enviado a la red RTC y, por la carrera, el audio TTS en vuelo en colas de hilo/engine al llegar el flush (sin identidad de generación). `[INFERENCIA desde CÓDIGO]` §8.
13. **Muerte de Media, Control o canal**: excepción Python ⇒ `os._exit(1)` (muere todo); destino cerrado ⇒ `cmd_result` ERROR o descarte de datos; caída de remoto ⇒ cierre del engine salvo `long_running_mode`; muerte del proceso ⇒ el Go server lo olvida, sin reinicio. `[CÓDIGO]` §9.
14. **Drenaje**: `on_stop`→`on_deinit` sin timeout en runtime; `SIGTERM` + 2 s + `SIGKILL` desde el Go server; 0,5 s de cancelación en el loop Python; sin drenaje de nodo. `[CÓDIGO]` §9.
15. **¿Duplica modelos?** Sí: un proceso por sesión ⇒ intérprete, extensiones y modelos por sesión; incluso dentro de un proceso, `TenVad` por instancia. `[CÓDIGO]` `worker_linux.go:31`; `EXT/ten_vad_python/extension.py:72-73`.
16. **Métricas para la rodilla**: `extension_thread_msg_queue_stay_time` (por app/grafo/grupo), `extension_callback_execution_duration`, `extension_cmd_processing_duration`, `extension_lifecycle_duration` (con `TEN_ENABLE_TEN_RUST_APIS`), y log de callbacks > 100 ms. Ciegas al lag del loop Python. `[CÓDIGO]` `RT/app/service_hub/telemetry/telemetry.c:12-85`; `RT/extension_thread/msg_interface/common.c:115-119`.
17. **Reutilizable legal y técnicamente**: patrones (§13), no código del núcleo (licencia con condiciones de Agora). Extensiones de `AG/` son Apache 2.0, pero su valor para Golden es bajo (Python por trama). `[CÓDIGO]` `LICENSE`, `AG/LICENSE`.
18. **¿Exigiría reemplazar Asterisk, ai-engine, Modal o proveedores?** Asterisk y ai-engine pueden quedarse (habría que escribir una extensión de transporte WS nativa para el protocolo de ai-engine); STT/TTS/LLM pueden quedarse (habría que escribir extensiones). **Modal**: el modelo de proceso por sesión del servidor de agentes no encaja; habría que alojar N grafos por contenedor (reintroduciendo hubs) o crear procesos por WS dentro del contenedor. Y obliga a abandonar Python 3.14t free-threaded en la capa Python. En la práctica: **reemplazar el runtime de GoldenBridge entero**, alternativa de alto impacto sin ganancia de K demostrada. `[INFERENCIA]`

---

## Piezas reutilizables (plantilla)

```text
Segmento: Correlación de comandos con ruta caducable (path table + path timers)
Responsabilidad principal: asociar cada cmd con su cmd_result, sintetizar error por timeout y descartar resultados huérfanos
Pattern principal: Request–Reply / Correlation Identifier con Timeout
Estado: implemented
Confianza: high
Evidencia: RT/path/path_table.c:ten_path_table_add_out_path:273-312, ten_path_table_process_cmd_result:467-489;
           RT/extension/internal/path_timer.c:ten_extension_out_path_timer_on_triggered:61-114;
           INC/extension/path_timer.h:12-15 (3 min / 10 s)
Callers verificados: RT/extension/extension.c:ten_extension_dispatch_msg:600-614 (alta OUT);
           RT/extension/msg_handling.c:87-88 (consumo OUT); RT/extension/extension.c:567-568 (consumo IN)
Contrato de entrada/salida: in = cmd con cmd_id (auto-generado si falta); out = cmd_result(status, is_final, detail);
           sin ruta ⇒ descarte silencioso
Política de error: timeout ⇒ cmd_result ERROR "Path timeout."; destino ausente/cerrando ⇒ cmd_result ERROR
Ownership: tabla por extensión, mutada sólo en el hilo del grupo (thread_check)
Riesgo: timeout por defecto (3 min) inútil para tiempo real; resolución de 10 s; búsqueda lineal por cmd_id
Aplicación posible en Golden: contrato C→M: Speak/CancelSpeech/SendFunctionCall con ack_id, timeout ~1-2 s,
           y descarte en Conversación de TurnCompleted/SpeechInterrupted cuyo (epoch, turn_id) ya no está abierto
```

```text
Segmento: Mailbox con política por clase de mensaje
Responsabilidad principal: impedir crecimiento sin límite de la cola del hilo destino sin perder control
Pattern principal: Bounded Mailbox / Load Shedding selectivo
Estado: implemented (parcial: sólo en el salto engine→hilo)
Confianza: high
Evidencia: RT/engine/msg_interface/common.c:ten_engine_post_msg_to_extension_thread:325-334;
           INC/extension_thread/extension_thread.h:21 (EXTENSION_THREAD_QUEUE_SIZE 12800)
Callers verificados: RT/engine/msg_interface/common.c:ten_engine_dispatch_msg:473
Contrato de entrada/salida: in = msg; si cola ≥ tope y msg no es cmd/cmd_result ⇒ no se entrega; si no ⇒ post_task_tail
Política de error: descarte con TEN_LOGW, sin aviso al emisor ni contador
Ownership: cola del runloop del hilo destino (mutex)
Riesgo: tope en nº de mensajes (≈128 s de audio a 10 ms), no en latencia; no cubre in_msgs del engine ni el loop Python;
        cmds sin tope
Aplicación posible en Golden: inbox por sesión en el MediaWorker con tope en ms de audio (p. ej. 200-300 ms),
        descarte/fusión del audio más viejo, comandos nunca descartados, contador por sesión expuesto en métricas
```

```text
Segmento: Hilo dueño con runloop y verificación de pertenencia
Responsabilidad principal: garantizar que el estado de una extensión sólo se toca desde su hilo; acceso externo vía post_task
Pattern principal: Active Object (+ aserción de afinidad de hilo)
Estado: implemented
Confianza: high
Evidencia: RT/extension_thread/extension_thread.c:ten_extension_thread_main_actual:254-308, ten_extension_thread_start:316-341;
           UT/sanitizer/thread_check.c:ten_sanitizer_thread_check_do_check:123-148;
           RT/ten_env_proxy/internal/notify.c:ten_env_proxy_notify:89-166 (atajo si call_by_me, post_task si no)
Callers verificados: RT/extension_context/extension_context.c:115 (start), 453-460 (create);
           PY/native/ten_env/ten_env_send_audio_frame.c:184-185 (notify desde Python)
Contrato de entrada/salida: in = (func, from, arg) encolado; out = ejecución en el hilo dueño, FIFO
Política de error: post sobre runloop cerrándose ⇒ rc=-2, el llamador sintetiza cmd_result ERROR o libera el msg
Ownership: una extensión ⇄ un hilo durante toda su vida; transferencia explícita al arrancar
Riesgo: si todas las piezas de una sesión no comparten hilo, el traspaso requiere hub; drenaje "hasta vacío" puede
        retrasar timers con productores sostenidos
Aplicación posible en Golden: MediaWorker = hilo dueño de un subconjunto de sesiones (3.14t); SessionState con
        owner_thread_id y aserción en modo debug; Conversación sólo publica comandos con post/call_soon_threadsafe
```

```text
Segmento: Media nativa con control Python por eventos (grupo C++ + extensión Python de control)
Responsabilidad principal: retener transporte, VAD, pacer y purga en código nativo; Python sólo recibe data/cmd
Pattern principal: Ports and Adapters sobre bus de mensajes (frontera semántica)
Estado: latent
Confianza: medium
Evidencia: core/include/ten_runtime/binding/cpp/detail/extension.h:proxy_on_audio_frame:236-253 (callback nativo sin GIL);
           RT/extension_thread/msg_interface/common.c:276-279 (mismo grupo ⇒ entrega síncrona sin engine);
           .../main_python/extension.py:126-130, 183-213 (control Python que ya sólo usa data/cmd para LLM/TTS/flush)
Callers verificados: ninguno en el repo con audio+VAD+purga nativos (el transporte nativo agora_rtc es binario)
Contrato de entrada/salida: in nativo = audio_frame; out a Python = data {speech_started, asr_result, playback_*};
           in desde Python = data tts_text_input / cmd flush
Política de error: la del runtime (cmd_result ERROR / descarte de datos)
Ownership: un grupo nativo por grafo (= por sesión)
Riesgo: no existe implementación de referencia; requiere escribir en C++ transporte WS, Silero/SmartTurn (ORT C API), pacer;
        licencia del núcleo; un grafo por sesión
Aplicación posible en Golden: confirma la frontera Media–Conversación del brief; el MediaWorker debe construirse
        (en Python 3.14t por hilo o nativo), no adoptarse de TEN
```

```text
Segmento: Loop Python global para todas las extensiones asíncronas + Task por trama
Responsabilidad principal: ejecutar handlers async de todas las extensiones Python de un proceso
Pattern principal: Single-threaded Event Loop compartido (Reactor global)
Estado: not recommended
Confianza: high
Evidencia: PY/interface/ten_runtime/async_extension.py:45-67 (default single_thread), 394-419;
           PY/interface/ten_runtime/global_thread_manager.py:15-135; PY/native/extension/extension.c:322-357
Callers verificados: todas las _proxy_on_* de AsyncExtension (async_extension.py:162-419)
Contrato de entrada/salida: in = mensaje desde hilo C con GIL; out = Task en el loop global
Política de error: excepción ⇒ os._exit(1) (async_extension.py:481-500)
Ownership: un hilo para todo el proceso
Riesgo: reproduce el cuello de Golden (coordinación por trama en un hilo) con GIL añadido
Aplicación posible en Golden: ninguna; sirve como contraejemplo y como criterio de PoC ("0 tareas por trama en Conversación")
```

---

## Respuesta a la pregunta central

TEN **no elimina** el coordinador único del camino por trama ni lo paraleliza: dentro de un proceso, el audio inter-grupo pasa por el engine y todo lo Python por un único loop asyncio con GIL. Su escalado de referencia es **réplica** (un proceso por sesión, K_worker = 1). Lo aprovechable para Golden son contratos (correlación con caducidad, cola por clase, hilo dueño verificable), reimplementados sin copiar código por las condiciones de licencia de Agora.
