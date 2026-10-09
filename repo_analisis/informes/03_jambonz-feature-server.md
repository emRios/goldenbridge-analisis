# 03 — jambonz-feature-server

Auditoría de arquitectura, comparada con GoldenBridge (GB). No se auditó GB: se usó `BRIEF_GOLDEN.md` como término de comparación.

**Leyenda de evidencia.** `[CÓDIGO]` = verificado en el checkout (`archivo:símbolo:líneas`). `[DOCUMENTACIÓN]` = README o documentación del propio repo. `[MEDICIÓN]` = benchmark reproducible del repo; **no hay ninguno** (ver §10). `[INFERENCIA]` = conclusión razonada a partir de la evidencia citada. `[PENDIENTE]` = no verificable con el material disponible: dependencias no instaladas (no se ejecutó `npm install`), módulos de FreeSWITCH, SBC y api-server, que están en otros repos. Los datos de GB vienen de `BRIEF_GOLDEN.md` y se marcan `(BRIEF)`.

Todas las rutas son relativas a `repo_analisis/repos/jambonz-feature-server/`.

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/jambonz/jambonz-feature-server.git` |
| branch | `main` (clon superficial, `git rev-parse --is-shallow-repository` = `true`) |
| commit | `85da181d7ab8302ce8e161facf5386f650d98ee4` — mensaje `0.9.14` |
| fecha (`git log -1 --format=%cI`) | `2026-10-04T09:20:16-04:00` |
| versión | `package.json` `"version": "0.9.14"`, `engines.node >= 20.x`; Dockerfile `node:24-alpine` |
| licencia observada | `LICENSE`: MIT, "Copyright (c) 2018-2024 FirstFive8, Inc."; `package.json` `"license": "MIT"` |
| submódulos | ninguno (no hay `.gitmodules`) |
| dependencias de licencia relevantes (de `package-lock.json`) | `drachtio-srf` 5.0.21 **MIT**; `drachtio-fsmrf` 4.1.2 **MIT**; `drachtio-modesl` 1.3.1 **MPL-2.0** (cliente ESL, transitiva); `@jambonz/speech-utils` 1.0.10 MIT; `@jambonz/realtimedb-helpers` 0.8.21 MIT; `ws` 8.18.3 MIT. FreeSWITCH (MPL) y los módulos jambonz de FreeSWITCH (`mod_audio_fork`, `*_tts_streaming`, `vad_*`, `*_s2s`) no están en este repo: su licencia queda `[PENDIENTE]`. |
| señal de mantenimiento | commit del 2026-10-04 (5 días antes de esta auditoría); CI badge en README; suite de regresión con docker-compose (`test/docker-compose-testbed.yaml`: mysql, redis, `drachtio/drachtio-server:0.8.26`, `drachtio/drachtio-freeswitch-mrf:0.9.2-4`) y tests unitarios `test/unit/*.test.js`. Con un clon superficial no se puede medir la cadencia histórica `[PENDIENTE]`. |
| tamaño de los segmentos | `lib/**/*.js` ≈ 24.984 líneas; `lib/session/call-session.js` 3.430 líneas, 222 métodos o accesores y 79 campos distintos asignados con `this.x =` solo en ese archivo `[CÓDIGO]` |

---

## 2. Dictamen ejecutivo

1. **jambonz saca el audio del runtime de control.** El proceso Node (feature server) nunca toca tramas de audio de llamada. El audio vive en FreeSWITCH, que corre en la misma máquina. Allí lo procesan módulos nativos: media bugs de STT, `mod_audio_fork`, TTS en streaming, VAD Silero y los módulos `*_s2s`. Node solo recibe eventos ESL de baja frecuencia (transcripciones, `playback-start/stop`, detección VAD, `tts_streaming::empty`) y emite comandos ESL (`uuid_break`, `uuid_<vendor>_tts_streaming send|flush|clear|stop`, `forkAudioStart/Stop`, `startTranscription`). `[CÓDIGO]` `lib/utils/tts-streaming-buffer.js:_api:338-345`, `lib/tasks/gather.js:_startTranscribing:717-724`, `lib/tasks/listen.js:178-185`, `lib/tasks/say.js:kill:450-476`. Ninguna búsqueda encontró `worker_threads`, `cluster` ni hilos en Node (`grep`, §4).
2. **No paraleliza el coordinador: lo elimina del camino por trama.** El coordinador único sigue existiendo: es un solo proceso Node con un solo event loop por host `[DOCUMENTACIÓN]` (README, pm2 `instances: 1`, `exec_mode: 'fork'`). Pero solo coordina eventos semánticos. El trabajo por trama (decodificación, VAD, STT streaming, TTS, pacing, RTP) lo hace un motor nativo multihilo ajeno a Node. Para Golden, esto confirma la frontera Media–Conversación del objetivo, con una condición: **Media debe ser un runtime que no comparta el loop con el control**. En jambonz ese runtime es FreeSWITCH, en C.
3. **La unidad de ownership es la llamada (`CallSession`).** Cada sesión tiene su propio WebSocket de control hacia la aplicación (`WsRequestor` por llamada), su propio endpoint FreeSWITCH (`ep`, con UUID) y su propio diálogo SIP (`dlg`). El aislamiento entre llamadas se consigue por identidad de objeto y canal dedicado, no por un dispatcher. `[CÓDIGO]` `lib/middleware.js:retrieveApplication:336-343`, `lib/session/call-session.js:constructor:154-160`, `lib/session/session-tracker.js:33-37`.
4. **Escala por réplicas de "feature server"** (Node + FreeSWITCH en el mismo host). Los nodos se eligen en el SBC mediante pings SIP `OPTIONS` con cabeceras `X-FS-Status: open|closed`, `X-FS-Calls` y `X-FS-ServiceUrl`. La afinidad es la del diálogo SIP durante toda la llamada. `[CÓDIGO]` `lib/utils/sbc-pinger.js:pingProxies:129-151`. El algoritmo de selección del SBC está en otro repo `[PENDIENTE]`.
5. **La protección frente a obsoletos es por rol y por reproducción, no por generación de conversación.** Hay tres guardas reales:
   - el filtro por `media-bugname` (`Task.eventIsForOurBug`);
   - el emparejamiento por `tts_playback_id` o nombre de archivo (`isMatchingEvent` en `say.js`);
   - el `uuid_break` condicionado a `taskId` (`play.js`).

   Pero el `bugname` se construye como `prefijo + vendor + "_transcribe"` (`gather.js:440-664`), así que **dos gathers sucesivos con el mismo proveedor comparten nombre**. Por eso el código parchea los eventos tardíos con heurísticas como `transcription-session-finished` y "empty transcript from old gather". Los comandos de la app (`tts:tokens`, `redirect`, `llm:tool-output`) no llevan época ni secuencia. El `msgid` de los comandos entrantes se ignora (`call-session.js:_onCommand:2314-2463`).
6. **Hay defectos de drenaje y de dominio de fallo.**
   - `ProcessMonitor.setupSignalHandlers` registra `SIGTERM → process.exit(0)` **antes** que el manejador de drenaje de `app.js`. Ese manejador quita el nodo de los sets de Redis y espera a que las llamadas terminen. Resultado: el drenaje en K8S queda anulado `[CÓDIGO + INFERENCIA]`.
   - `uncaughtException → process.exit(1)` tumba el control de todas las llamadas del host `[CÓDIGO]` `lib/utils/process-monitor.js:55-77`, `app.js:32-37,174-211`.
7. **Valor para Golden: ADAPTAR patrones, DESCARTAR adopción.** Adoptar jambonz exigiría reemplazar Asterisk, el túnel y ai-engine por drachtio + FreeSWITCH + SBC jambonz + MySQL + Redis. Además, el VAD y el SmartTurn de GB tendrían que reescribirse como módulos C de FreeSWITCH. Los patrones que sí sirven:
   - buffer de texto TTS acotado con marcas de agua y eventos pause/resume hacia Conversación;
   - comandos de cancelación ejecutados en el plano Media;
   - correlación por recurso de media (bug, playback);
   - señal de "cerrado a nuevas llamadas" publicada al balanceador.

   La lección negativa: el identificador por rol no basta. Golden necesita `speech_id`/`turn_id` por generación.

---

## 3. Diagrama del flujo relevante

```text
                       (otro repo)                         (otro repo)
 Carrier ── SIP ──▶  SBC jambonz  ── OPTIONS ping (X-FS-Status/X-FS-Calls) ◀── feature server
                        │  elige feature server [PENDIENTE: algoritmo]
                        │  INVITE (X-Account-Sid, X-Retain-Call-Sid…)
                        ▼
 ┌──────────────── HOST "feature server" (1 por réplica) ─────────────────────────────────┐
 │                                                                                         │
 │  drachtio-server (C++, SIP stack)  ◀── TCP control ──▶  Node: jambonz-feature-server   │
 │        [PENDIENTE: código]                               1 proceso · 1 event loop       │
 │                                                          ┌──────────────────────────┐   │
 │                                                          │ srf.invite → middleware  │   │
 │                                                          │ → new InboundCallSession │   │
 │                                                          │   .exec()  (cola tasks)  │   │
 │                                                          │ SessionTracker callSid→cs│   │
 │                                                          └───────┬──────────────────┘   │
 │                                    ESL inbound (api/bgapi) + ESL por endpoint (eventos) │
 │                                     ▲ eventos baja frecuencia     │ comandos            │
 │                                     │ (transcripción, VAD,        │ uuid_break,         │
 │                                     │  playback-*, tts empty,     │ *_tts_streaming,    │
 │                                     │  mod_audio_fork::*)         │ forkAudio*, play    │
 │  ┌──────────────────────────────────┴───────────────────────────────▼────────────────┐  │
 │  │ FreeSWITCH (C, multihilo nativo) — MEDIA                                          │  │
 │  │  canal/ep (uuid) ─ RTP ⇄ SBC                                                      │  │
 │  │  media bugs: STT vendor WS · vad_silero · mod_audio_fork (WS audio) · TTS stream  │  │
 │  │  · *_s2s (OpenAI/Deepgram VA/Ultravox…)                                           │  │
 │  │  AUDIO POR TRAMA: aquí y sólo aquí                                                │  │
 │  └───────────────┬──────────────────────────────────────────────┬───────────────────┘  │
 └──────────────────┼──────────────────────────────────────────────┼──────────────────────┘
                    │ WS audio (STT/TTS/LLM S2S/listen)            │ RTP
                    ▼                                              ▼
           proveedores remotos                                    SBC
 Node ── WS por llamada (ws.jambonz.org, JSON, msgid/ack) ──▶ APLICACIÓN del cliente (decide verbos)
 Node ── HTTP (callSid) ◀── jambonz-api-server (REST updateCall) [enruta por serviceUrl en Redis]
 Node ── Redis: proyección de llamada, sets active-fs, handoff de transferencia (TTL 30 s)
```

---

## 4. Mapa de procesos, threads, loops y colas

| componente | tipo | qué hace | evidencia |
|---|---|---|---|
| `node app.js` | 1 proceso, 1 event loop (libuv) | SIP (vía drachtio-srf), HTTP API, WS hacia la app, ESL hacia FS, cola de verbos por llamada | `[CÓDIGO]` `app.js:17-131`; `[DOCUMENTACIÓN]` README pm2 `instances: 1`, `exec_mode: 'fork'` |
| hilos o procesos en Node | ninguno para trabajo de llamada | `grep worker_threads\|require('cluster')\|child_process` solo da `cron-jobs.js` (`execSync` de limpieza) | `[CÓDIGO]` |
| drachtio-server | proceso C++ separado | pila SIP; Node se conecta por TCP (`srf.connect` o `srf.listen`) | `[CÓDIGO]` `app.js:51-62`; interior `[PENDIENTE]` |
| FreeSWITCH | proceso nativo separado, local ("all jambonz deployments have freeswitch installed locally alongside this app") | todo el audio | `[CÓDIGO]` `lib/utils/install-srf-locals.js:91-93` (comentario), `:84-113` |
| conexiones ESL | 1 conexión `mrf.connect` por FreeSWITCH del inventario (normalmente 1), con reintento cada 3 s; por endpoint, `ep.conn` recibe eventos (`ep.conn.on('esl::event::CUSTOM::*')`) | control y eventos | `[CÓDIGO]` `install-srf-locals.js:100-133`, `lib/tasks/task.js:playToConfMember:309`. Que cada endpoint tenga su propio socket ESL saliente (`listenAddress`) es `[INFERENCIA]` a partir de `install-srf-locals.js:94-95`; el código de drachtio-fsmrf no está instalado `[PENDIENTE]` |
| cola de verbos por llamada | `CallSession.tasks` (array), consumida por `exec()` con una sola `currentTask` a la vez | orquestación | `[CÓDIGO]` `call-session.js:exec:1378-1453` |
| tareas de fondo por llamada | `BackgroundTaskManager.tasks: Map<type, task>`, una por tipo (`listen`, `bargeIn`, `record`, `transcribe`, `ttsStream`) | concurrencia lógica dentro de la llamada | `[CÓDIGO]` `lib/utils/background-task-manager.js:29-60` |
| cola de envío WS antes de conectar | `WsRequestor.queuedMsg` (array, **sin tope**) | mensajes Node→app mientras se conecta o reconecta | `[CÓDIGO]` `lib/utils/ws-requestor.js:109-122,183-186` |
| mensajes en vuelo | `WsRequestor.messagesInFlight: Map<msgid,…>` con timer de 5 s | ACK de la app | `[CÓDIGO]` `ws-requestor.js:263-293`, `lib/config.js:57` |
| buffer de texto TTS | `TtsStreamingBuffer.queue`, **acotado**: HIGH_WATER 1.000, LOW_WATER 200 caracteres, chunk ≤ 1.800 | backpressure Conversación→Media | `[CÓDIGO]` `lib/utils/tts-streaming-buffer.js:8-10,111-120,324-328` |
| cola de `playAudio` (listen bidireccional) | array, tope 10 | audio devuelto por WS como archivos | `[CÓDIGO]` `lib/tasks/listen.js:5,294-296` |
| timers periódicos globales | métricas cada 20 s, métricas FS cada 30 s, ping OPTIONS (por defecto 30 s), reintento FS cada 3 s | operación | `[CÓDIGO]` `app.js:144-163`, `install-srf-locals.js:115-152`, `config.js:130`, `sbc-pinger.js:170-173` |

**Qué no hace el loop de Node.** No recibe, reenvía, decodifica, remuestrea ni analiza tramas de audio de la llamada. El único audio que pasa por Node es el TTS **no streaming**: `synthAudio` devuelve un `filePath`, que se registra en `cs.trackTmpFile` y FreeSWITCH lo reproduce después (`tts-task.js:310-327`, `call-session.js:_clearResources:2676-2685`). Ese cruce es **por enunciado, no por trama**. Si los bytes cruzan Node o solo un path depende de `@jambonz/speech-utils` `[PENDIENTE]`.

---

## 5. Ownership por sesión y recursos compartidos

**Dueño por llamada: `CallSession`** (subclases `InboundCallSession`, `RestCallSession`, `SipRecCallSession`, `ConfirmCallSession`, `AdultingCallSession`, `SmsCallSession`). La creación ocurre en `app.js:srf.invite:126-131` para las entrantes. Es dueña de:

| estado | campo | quién lo muta | evidencia |
|---|---|---|---|
| diálogo SIP (telefonía) | `cs.dlg` (creado en `propagateAnswer` con `srf.createUAS`) | `propagateAnswer`, `wrapDialog`, `_clearResources` | `[CÓDIGO]` `call-session.js:2747-2768,3080-3099,2662-2674` |
| endpoint de media | `cs.ep` (y `ep2` en siprec), `cs.ms` | `_evalEndpointPrecondition`, `replaceEndpoint`, `_clearResources` | `[CÓDIGO]` `call-session.js:2489-2591,2622-2657,2731-2740` |
| programa de la app | `cs.tasks`, `cs.currentTask`, `taskIdx`, `stackIdx` | `exec`, `replaceApplication`, `_injectTasks`, `kill`, `_onCommand` | `[CÓDIGO]` `call-session.js:1378-1453,2120-2205,2258-2294,2314-2463` |
| estado de reproducción (espejo) | `task.playComplete`, `cs.isCurTaskPlay`, `cs.taskInProgress`, `cs._isPlayingFillerNoise`, `ttsStreamingBuffer.{queue,_isFull,_connectionStatus}` | tasks y eventos FS | `[CÓDIGO]` `gather.js:881-902`, `call-session.js:1382-1383,3242-3260`, `tts-streaming-buffer.js:28-37` |
| canal de control | `cs.requestor` y `cs.notifier` (= `application.requestor`), un `WsRequestor` **por llamada** | `handover` (WS→HTTP), `_clearResources` | `[CÓDIGO]` `middleware.js:336-343`, `call-session.js:143-160,2686-2687` |
| historial conversacional mínimo | `cs.conversationTurns` (para prompts de STT OpenAI) | eventos `userSaid` y `botSaid` | `[CÓDIGO]` `call-session.js:167-169,3368-3392` |

**Recursos compartidos por todas las llamadas del proceso** `[CÓDIGO]`:
- el singleton `SessionTracker` (`Map callSid→CallSession`, `session-tracker.js:9-69`);
- `srf` y `srf.locals`: pool MySQL (`connectionLimit` por defecto 10), cliente Redis, `stats` y la función `getFreeswitch` (round robin sobre FS activos), en `install-srf-locals.js:155-297`;
- la conexión ESL inbound con FreeSWITCH;
- el logger pino asíncrono (`app.js:27`);
- el propio event loop.

**Fuentes duplicadas de verdad**:

| dato | fuente local | duplicado | riesgo |
|---|---|---|---|
| llamada viva y su host | `SessionTracker` | registro en Redis vía `updateCallStatus(CallInfo.toRedisRecord(obj), serviceUrl)` | Redis es una proyección escrita en cada cambio de estado (`call-session.js:128-130,836,3188`). Si el proceso muere, el registro queda huérfano hasta que otro lo limpie `[INFERENCIA]` |
| nodo admite llamadas | `dryUpCalls` (closure de `sbc-pinger.js`) | sets Redis `<cluster>:active-fs`, `<cluster>:fs-service-url`, `fsUUIDs` | Este repo solo **añade** `fsUUIDs` (K8S) y **quita** los tres en SIGTERM (`app.js:193-203`, `sbc-pinger.js:152-166`). Quién añade `active-fs` es `[PENDIENTE]` (probablemente el SBC) |
| dueño de una conferencia | conferencia FS local | hash Redis `conf:<account>:<name>` → `sipAddress` (el primero que escribe gana) | sin TTL visible. Si el FS dueño muere, el hash puede apuntar a un nodo muerto `[INFERENCIA]`; la semántica de `createHash` es `[PENDIENTE]` (`conference.js:_init:145-198`) |
| reproducción en curso | FS (real) | flags en Node (espejo) | hay carreras parcheadas a mano: `playback-start` llega después del kill y se emite `uuid_break` otra vez (`gather.js:_startFillerNoise:851-858`, `call-session.js:3253-3259`) |
| `lifecycleEmitter` | emisor original en `srf.locals` | `SnsNotifier` reasignado en la closure | ver §9: el estado ScaleIn de AWS no llega a `srf.locals` |

**Respuesta a la pregunta específica (estado telefónico, de reproducción y de aplicación sin mezclar llamadas).**
- **No mezcla llamadas** porque:
  - no hay dispatcher compartido de audio ni de eventos;
  - cada llamada tiene su propio WS a la app, su propio `ep` con sus listeners y su propio `dlg`;
  - la única tabla global, `SessionTracker`, solo se usa para resolver `callSid` en las peticiones REST (`lib/http-routes/api/update-call.js:retrieveCallSession:10-40`).
- **Las tres clases de estado están separadas por proceso, no por diseño de clase:**
  - el estado telefónico lo tienen drachtio-server (SIP) y FreeSWITCH (canal);
  - el estado de reproducción, FreeSWITCH;
  - las decisiones de aplicación, el servidor de la app del cliente, remoto.
- **Dentro de Node, las tres se reúnen otra vez en una clase-dios.** `CallSession` tiene 222 miembros y mezcla SIP (`_onReinvite`, `_handleRefer`), media (`_killAudio` vía tasks, `ttsStreamingBuffer`) y protocolo de app (`_onCommand`, `_lcc*`) `[CÓDIGO]`. Es el mismo antipatrón que el `SessionState` de 106 campos de GB (BRIEF). jambonz lo tolera porque el objeto solo procesa eventos de baja frecuencia.

---

## 6. Contrato de control/IPC

Hay tres fronteras de control. Ninguna transporta audio por trama.

### 6.1 Node ⇄ aplicación del cliente (WS `ws.jambonz.org`)

- **Conexión**: un `WsRequestor` por llamada. Se abre al primer `request` (`session:new`) con `handshakeTimeout` 1.500 ms y `maxPayload` 24 KiB. `[CÓDIGO]` `ws-requestor.js:123-182,341-378`, `config.js:53-54`.
- **Mensajes Node→app**: `{type, msgid, call_sid, hook?, data, b3?}`. `msgid` es un `short.generate()` nuevo por mensaje. `[CÓDIGO]` `ws-requestor.js:195-209`.
- **ACK**: en los tipos que esperan respuesta (`session:new`, `verb:hook`, `session:redirect`, …), la promesa queda en `messagesInFlight` hasta un `{type:'ack', msgid, data}` o hasta un timeout de 5 s (`ws-requestor.js:263-293`, `config.js:57`). Las notificaciones se envían sin esperar ACK: `call:status`, `verb:status`, `jambonz:error`, `llm:event`, `llm:tool-call`, `tts:streaming-event`, `tts:tokens-result`. La constante que las agrupa se llama, al revés, `MTYPE_WANTS_ACK`, y `wantsAck = !MTYPE_WANTS_ACK.includes(type)` (`ws-requestor.js:16-24,75`) `[CÓDIGO]`.
- **ACK desconocido**: se descarta con log (`_recvAck:537-548`). Es la única deduplicación del canal.
- **Comandos app→Node**: `{type:'command', msgid, command, queueCommand?, tool_call_id?, data, callSid?}`.
  - `_recvCommand` reemite el comando sin validar (`// TODO: validate command`, `ws-requestor.js:550-554`). `CallSession._onCommand` lo despacha con un `switch` (`call-session.js:2314-2446`).
  - **No se envía ACK al comando y su `msgid` no se usa.** No hay secuencia, época ni deduplicación.
  - `callSid` permite dirigir algunos comandos a la pierna B de un `dial` (`_lccSipRequest:1788`, `_lccDtmf:1818`, `_lccDub:1924`, `_lccBoostAudioSignal:1942`).
- **Validación de estado**: varios comandos solo se aceptan si `currentTask.name` coincide; por ejemplo, `llm:tool-output` exige una task `Llm*` (`call-session.js:1961-1982`), y el mute exige `Dial` o `Conference` (`1754-1762`). La guarda es **por tipo de task, no por instancia**: un `tool_call_id` tardío de un LLM anterior se entregaría a la task LLM actual `[INFERENCIA]`.
- **Reconexión**: si el WS se cierra con código distinto de 1000, `_onSocketClosed` programa una reconexión con backoff (500 ms ×2 hasta 2 s, luego +2 s) mientras `connections < maxReconnects` (≤ 5) (`ws-requestor.js:450-480`).
  - Al reconectar se envía `session:reconnect` con el **payload original de `session:new`** (`:192`), y los mensajes posteriores se encolan hasta su ACK (`:183-186,296-314`).
  - Si el ACK falla, se emite `reconnect-error` y `CallSession._onSessionReconnectError` cuelga la llamada (`call-session.js:2296-2312`).
  - Al cerrarse el socket, los mensajes en vuelo se rechazan (`_clearPendingMessages:389-396`) y **no se reenvían**.
- **Handover**: si la app devuelve un hook HTTP absoluto en `session:redirect`, el WS se cierra y se cambia a `HttpRequestor` (`ws-requestor.js:95-106`, `call-session.js:143-152`).
- **Defecto observado**: `_onMessage(content, isBinary)` comprueba `this.isBinary`, que nunca se asigna, en lugar del parámetro. El rechazo de mensajes binarios no funciona como pretende `[CÓDIGO]` `ws-requestor.js:482-488`.

### 6.2 Node ⇄ FreeSWITCH (ESL vía drachtio-fsmrf)

- **Comandos** (todos con el UUID del canal como primer argumento):
  - `ep.api('uuid_break', uuid)`;
  - `ep.api('uuid_<vendor>_tts_streaming', '^^|uuid|connect|send|flush|clear|stop')`;
  - `ep.api('uuid_voice_agent_s2s', '^^|uuid|session.create|host|path|bearer|key')`;
  - `ep.play(file)`, `ep.startTranscription({vendor, bugname, interim…})`, `ep.forkAudioStart({wsUrl, bugname, bidirectionalAudio…})`, `ep.startVadDetection(vad)`.

  `[CÓDIGO]` `tts-streaming-buffer.js:338-345`, `voice_agent_s2s.js:98-103,170-180`, `say.js:409`, `gather.js:717-724,829-836`, `listen.js:178-185`.
- **Eventos**: `ep.addCustomEventListener(event, handler)`, con los handlers registrados por task en `Task.eventHandlers` para retirarlos por referencia y no romper a otras tasks del mismo endpoint (`task.js:136-169`).
- **Correlación** en cuatro niveles:
  1. endpoint (`ep` y su `uuid`);
  2. media bug (`media-bugname` en la cabecera; `Task.eventIsForOurBug`, `task.js:110-134`);
  3. reproducción (`variable_tts_playback_id` o `evt.file`; `say.js:isMatchingEvent:18-30`);
  4. task (`taskId` aleatorio; `play.js:63-72`).
- **Respuesta de los comandos**: se valida con `res.body.startsWith('+OK')` y, si no, se lanza error. Es validación por código de retorno, **sin leer de vuelta el estado efectivo** `[CÓDIGO]` `tts-streaming-buffer.js:341-344`.

### 6.3 Node ⇄ resto de la plataforma

- **REST** `POST /v1/updateCall/:callSid` → `SessionTracker.get` → `cs.updateCall`. Responde 202 antes de ejecutar, salvo `sip_request` (`update-call.js:45-70`). Que el api-server sepa a qué FS llamar es `[INFERENCIA]`: usaría el `serviceUrl` proyectado en Redis (`call-session.js:128-130`).
- **Redis**: proyección de llamada; handoff de transferencia (`addKey(uuid, JSON, 30)`); hash de conferencia; sets de nodos activos (`task.js:340-380`, `middleware.js:290-303`, `conference.js:145-198`).

**Comparación con el contrato de referencia de Golden** `(session_id, session_epoch, turn_id, speech_id, seq)`:

| campo Golden | equivalente jambonz | brecha |
|---|---|---|
| `session_id` | `callSid` (y `X-Retain-Call-Sid` para conservarlo entre servidores) | equivalente `[CÓDIGO]` `middleware.js:50` |
| `session_epoch` | no existe. `session:reconnect` reutiliza el payload de `session:new` sin época | la app no puede distinguir comandos de antes y de después de la reconexión |
| `turn_id` | no existe. El turno es implícito: una `Gather` resuelta → `verb:hook` con `msgid` | correlación solo por `msgid` del hook, válida para la respuesta inmediata (ACK), no para comandos posteriores |
| `speech_id` | `playback_id` (FS) para TTS por archivo o API; el `id` de `tts:tokens` es solo eco para `tts:tokens-result` | el streaming TTS no tiene identidad de generación: `clear` borra todo y los tokens siguientes se aceptan sin condición (`tts-streaming-buffer.js:97-132,165-175`) |
| `seq` | no existe en comandos. El orden lo da el TCP del WS | sin deduplicación ni detección de huecos |

---

## 7. Ruta de audio y frecuencia de cruces

| flujo | ruta | ¿cruza Node? | evidencia |
|---|---|---|---|
| RTP del llamante | SBC → FreeSWITCH (canal `ep`) | **no** | `[CÓDIGO]` `ep` vía `ms.createEndpoint` (`media-endpoint.js:21`); el audio no se expone en ninguna API de Node |
| STT (gather/transcribe) | FS media bug → WS del proveedor (desde FS) → evento ESL con la transcripción → Node | **solo el texto**, a cadencia de resultados parciales o finales | `[CÓDIGO]` `gather.js:427-680,927-1166` |
| VAD | FS `vad_detect`/`vad_silero` → evento `vad_detect:detection` | solo el evento de detección | `[CÓDIGO]` `constants.json:196-201`, `gather.js:829-845,1303-1313`; config por defecto `vendor: 'silero'` (`lib/tasks/config.js:330-338`); el módulo es `[PENDIENTE]` |
| TTS streaming | app → `tts:tokens` (texto) → Node `TtsStreamingBuffer` → `uuid_<vendor>_tts_streaming send` → FS (WS al proveedor TTS, reproducción) | **solo texto** | `[CÓDIGO]` `tts-streaming-buffer.js:188-336` |
| TTS no streaming | `synthAudio` (speech-utils) → archivo o caché Redis → `ep.play(file)` | **una vez por enunciado** (o ninguna, si es `say:{…}` resuelto en FS) | `[CÓDIGO]` `tts-task.js:310-366`; detalle de bytes `[PENDIENTE]` |
| `listen` (audio a WS externo, el patrón de ai-engine) | FS `mod_audio_fork` ⇄ WS remoto directamente. Node recibe `connect/playAudio/killAudio/disconnect` | **no** | `[CÓDIGO]` `listen.js:178-185,195-214,284-311` |
| LLM speech-to-speech | FS `uuid_*_s2s session.create` abre el WS hacia el agente. Node recibe `ServerEvent` JSON | **no** el audio; sí los eventos JSON del agente | `[CÓDIGO]` `voice_agent_s2s.js:170-180,214-219,244-330`. Nota: el destino por defecto es `agent.deepgram.com` + `/v1/agent/converse`, el mismo protocolo que habla GB (`voice_agent_s2s.js:86-96`) |

**Cruces por paquete de audio en el runtime de control: 0.** Lo que llega a Node se mide en eventos por turno (decenas por minuto), no en tramas por segundo. La cadencia exacta no está medida en el repo `[PENDIENTE]`.

---

## 8. Secuencia de barge-in

**Variante A — `gather` con prompt anidado (`say`/`play`) y `bargein: true`:**
1. FreeSWITCH, en el media bug `gather_<vendor>_transcribe`, emite una transcripción interina, o bien `start-of-speech` o una detección VAD si `minBargeinWordCount === 0` `[CÓDIGO]` `gather.js:1187-1191,1303-1308`.
2. Node, en `Gather._onTranscription`, filtra por bug (`eventIsForOurBug`, `:936`), cuenta palabras (`:1006-1009`) y, si `words + bufferedWords >= minBargeinWordCount` (por defecto 1), emite `'vad'`, notifica `speech-bargein-detected` y llama a `_killAudio(cs)` (`:1128-1135`).
3. `_killAudio` mata la task anidada: `sayTask.kill(cs)` o `playTask.kill(cs)` (`gather.js:893-901`).
   - En `Say.kill` no streaming se envía `uuid_break`, se quitan los listeners `playback-*` y se resuelve la promesa pendiente (`say.js:450-476`).
   - En `Say.kill` streaming se llama a `cs.stopTtsStream()`, que envía `stream_closed` a la app y `uuid_<vendor>_tts_streaming stop` a FS (`call-session.js:1038-1044`, `tts-streaming-buffer.js:79-92`).
   - En `Play.kill` se emite `uuid_break` por el `stickyEventEmitter` solo si el `taskId` coincide con el de la reproducción en curso (`play.js:119-132,63-72`).
4. Si la gather no tiene prompt anidado (el audio venía de un `say` anterior o del TTS streaming de fondo), `_killAudio` emite `uuid_break` y llama a `cs.clearTtsStream()`. Esto envía `tts:streaming-event {event_type:'user_interruption'}` a la app y `… clear` a FS, y vacía el buffer de texto (`gather.js:881-891`, `call-session.js:1026-1032`).

**Variante B — "bot mode" (`config.bargeIn` con gather de fondo, apps WS):**
1. `BackgroundTaskManager._initBargeIn` arranca una `Gather` con prefijo `background_bargeIn_` y escucha `dtmf|vad|transcription|timeout` (`background-task-manager.js:103-134`).
2. El primero de esos eventos emite `bargeIn-done` una sola vez (`_bargeInHandled`, `:210-216`).
3. `CallSession` reacciona con `kill(true)`: mata la `currentTask` (por ejemplo, un `say`) y vacía la cola hasta el primer `config` con `bargeIn.enable === false` (`call-session.js:1068-1074,2177-2205`).
4. Si es *sticky*, la gather de fondo se relanza (`background-task-manager.js:120-126`).
5. Defecto: `enableBotMode` añade un listener `bargeIn-done` en cada llamada a la función sin quitar el anterior (`call-session.js:1068`). Con reconfiguraciones repetidas, el `kill(true)` se ejecuta N veces por evento `[CÓDIGO]`.

**¿Dónde ocurre?** La **detección** ocurre en Media (FS). La **decisión** y el **comando de purga** ocurren en el control (Node). Son dos saltos ESL locales, uno de evento y otro de comando. No se mide su latencia en el repo `[PENDIENTE]`. Esto funciona porque el loop de control no carga tramas. En GB, el mismo diseño heredaría la latencia del loop saturado (BRIEF: `cycle_p90` 122 ms a 41+ sesiones).

**¿Dónde se purga?** En FS: `uuid_break` detiene la reproducción del canal, y `*_tts_streaming clear|stop` vacía el módulo TTS. En Node: `TtsStreamingBuffer.clear()` o `stop()` vacía la cola de texto (`tts-streaming-buffer.js:165-175,79-92`).

**Qué no se puede retirar** `[INFERENCIA]`:
- el audio ya escrito a RTP por FS y el que esté en el jitter buffer del SBC o del terminal;
- el audio que el módulo TTS ya tenga sintetizado, si `clear` no lo descarta (el comportamiento del módulo es `[PENDIENTE]`);
- los tokens que la app siga enviando después de `user_interruption`: se aceptan, porque no hay `speech_id` (§6);
- en la variante A con `minBargeinWordCount ≥ 1`, al menos una palabra reconocida de habla del usuario solapada con el bot, es decir, el tiempo de latencia del STT interino.

---

## 9. Backpressure, fallos, reconexión y drenaje

**Backpressure**:
- **Texto TTS, por llamada** `[CÓDIGO]` `tts-streaming-buffer.js:111-120,324-328`, `call-session.js:3322-3330`, `say.js:153-191`.
  - Con más de 1.000 caracteres en cola, se responde `{status:'failed', reason:'full'}` y se emite `tts:streaming-event stream_paused` a la app.
  - Al bajar a 200 o menos, se emite `stream_resumed`.
  - El `say` streaming interno reintenta 5 veces con backoff ×1,5 hasta 10 s.
- **Audio**: Node no aplica backpressure al audio porque no lo ve. La de FS es `[PENDIENTE]`.
- **WS de control**: `queuedMsg` sin tope mientras se conecta (`ws-requestor.js:109-122`). `maxPayload` es de 24 KiB solo para lo recibido (`ws-requestor.js:352`).
- **Llamadas no relacionadas**: comparten el event loop. Un handler síncrono lento (por ejemplo, el `JSON.stringify` de payloads grandes, que se hace en varios sitios: `task.js:231`, `call-session.js:1055`) retrasa el **control** de todas las llamadas del host, no su audio `[INFERENCIA]`.

**Fallos** (dominio de fallo = host feature server):

| falla | comportamiento | evidencia |
|---|---|---|
| excepción no capturada en Node | `uncaughtException → process.exit(1)`: se pierde el control de **todas** las llamadas del proceso; pm2 lo reinicia (`autorestart: true`) | `[CÓDIGO]` `process-monitor.js:67-77`; `[DOCUMENTACIÓN]` README pm2. Qué hace FS con los canales al cerrarse los sockets ESL es `[PENDIENTE]` |
| promesa rechazada sin manejar | solo log | `[CÓDIGO]` `process-monitor.js:79-87` |
| se pierde FreeSWITCH | `esl::end` → `active=false`, reintento cada 3 s. Si no queda FS activo, Node se desconecta de drachtio (deja de aceptar INVITE). Las llamadas nuevas en una sesión sin FS reciben 480 `crankback: media server failure` | `[CÓDIGO]` `install-srf-locals.js:43-57,114-133`, `app.js:74-82`, `call-session.js:2731-2740`, `inbound-call-session.js:58-66` |
| fin de media en el endpoint | `ep.once('destroy')` con `reason === 'MEDIA_TIMEOUT'` → cuelga | `[CÓDIGO]` `media-endpoint.js:42-46`, `call-session.js:3206-3211` |
| se cae el WS de la app | reconexión ≤ 5 con `session:reconnect`; la llamada sigue ejecutando su task actual; si la reconexión falla, cuelga | `[CÓDIGO]` §6.1 |
| la app no responde a un hook | timeout de 5 s → rechazo; `ActionHookDelayProcessor` para rellenos o reintentos | `[CÓDIGO]` `ws-requestor.js:266-270`, `call-session.js:740-771` |
| asignación de endpoint falla con `SipError` | propaga el status SIP (por ejemplo, 488) al SBC | `[CÓDIGO]` `call-session.js:2555-2589` |

**Defectos en rutas de error** `[CÓDIGO]`:
- `_evalEndpointPrecondition` y `_evalUnansweredCallPrecondition` construyen `new Error(... 'call gone')` **sin `throw`** (`call-session.js:2490,2609`). La guarda "llamada terminada" no detiene la ejecución.
- `_stopVad` quita `this._onVadDetected` sin el `bind`, de modo que no retira el listener registrado (`gather.js:832` frente a `:842`). Queda mitigado porque `removeCustomEventListeners` se llama al resolver.

**Drenaje**:
- **Señal al balanceador** (`sbc-pinger.js:37-126,129-151`):
  - entrada: `SIGUSR1` (bare metal), SNS `Auto Scaling: Lifecycle action 'TERMINATING'` (AWS) o `SIGTERM` (K8S);
  - efecto: `dryUpCalls = true` → `OPTIONS` con `X-FS-Status: closed` → el SBC deja de enviar llamadas nuevas `[INFERENCIA]` (el SBC está en otro repo).
- **Fin**:
  - en K8S, `app.js:handle` quita el nodo de los sets de Redis, fija `operationalState = ScaleIn`, y cuando `SessionTracker` emite `idle` llama a `lifecycleEmitter.scaleIn = () => process.exit(0)` (`app.js:133-139,177-211`, `sbc-pinger.js:100-102`);
  - en AWS, `completeScaleIn()` solo se llama si hay 0 llamadas a los 5 s del aviso (`sbc-pinger.js:69-79`).
- **Defecto 1 (K8S)**: `ProcessMonitor.setupSignalHandlers()` (`app.js:37`) registra `SIGTERM → process.exit(0)` **antes** que `process.on('SIGTERM', handle)` (`app.js:174`). Node ejecuta los listeners en orden de registro y `process.exit` termina el proceso de forma síncrona. Por tanto, **el drenaje no llega a ejecutarse y las llamadas en curso se cortan** `[CÓDIGO]` `process-monitor.js:55-65` + `[INFERENCIA]` sobre la semántica de Node. Lo mismo pasa con `SIGINT`, que es la señal de parada por defecto de pm2.
- **Defecto 2 (AWS)**:
  - `installSrfLocals` desestructura `lifecycleEmitter` de forma síncrona (`install-srf-locals.js:74`), antes de que la IIFE asíncrona lo reasigne al `SnsNotifier` (`sbc-pinger.js:40-42,193-196`);
  - como `app.js:135` consulta `srf.locals.lifecycleEmitter.operationalState`, que nunca vale `ScaleIn` en modo AWS, `completeScaleIn` no se llama cuando terminan las llamadas;
  - el hook del ASG terminaría por su propio timeout `[INFERENCIA]`.
- **Defecto 3**: `srf.locals.disabled = true` (`app.js:179`) no se lee en ningún punto de este repo `[CÓDIGO]` (grep).
- **Migración en vez de drenaje**: no existe para todas las llamadas. Solo existe para conferencias y colas: REFER a otro feature server con los verbos restantes en Redis (TTL 30 s) y `X-Retain-Call-Sid` (`task.js:transferCallToFeatureServer:340-380`, `call-session.js:referCall:3041-3060`, `middleware.js:290-303`).

---

## 10. Escalado y uso real de núcleos

- **Paralelismo entre núcleos**:
  - **media**: lo da FreeSWITCH, nativo y multihilo. Su modelo de hilos se audita en `02_freeswitch.md`; no está en este repo `[PENDIENTE]` aquí;
  - **control**: un solo núcleo efectivo por host, el event loop de Node `[CÓDIGO]`;
  - **flota**: réplicas de feature server tras el SBC.
- **Asignación y afinidad**: el SBC elige el nodo con la información de `OPTIONS` (`X-FS-Calls`, `X-FS-Status`) y, en K8S, por servicio `K8S_SBC_SIP_SERVICE_NAME` (`sbc-pinger.js:23-33`). La afinidad la da el diálogo SIP mientras dura `[INFERENCIA]`. El REST localiza al dueño por `serviceUrl` (§6.3). Dentro del host, `getFreeswitch()` reparte en round robin entre FS activos (`install-srf-locals.js:158-162`).
- **Capacidad en términos K**:
  - `K_loop` (Node): lo limita la tasa de **eventos de control**, no la de tramas. Sin medición en el repo `[PENDIENTE]`.
  - `K_worker`: en el plano media, un proceso FreeSWITCH (ver informe 02); en control, `K_worker = K_loop`.
  - `K_task` (host): `min(K_loop_control, K_FS_media, CPU, memoria)`. Las únicas señales expuestas son `fs.media.channels.in_use/free`, `fs.media.cpu_idle` y `fs.sip.calls.count` (`install-srf-locals.js:136-152`, `app.js:145`).
  - `K_flota`: N hosts × `K_task`, limitado por el SBC.
- **Mediciones**: el repo no contiene benchmark ni prueba de carga. Los tests son funcionales: SIPp y docker-compose (`test/sipp.js`, `test/scenarios`). **Ninguna cifra de llamadas por servidor puede afirmarse** `[PENDIENTE]`.
- **Qué mejora y qué replica**: jambonz **no** aumenta `K_loop` de un loop que procese tramas. Lo que hace es **sacar las tramas del loop**. La capacidad de media depende de FreeSWITCH (nativo y multihilo), y el control queda en un loop que solo ve eventos. Es una mejora estructural de eficiencia del control, no un `N × K_loop`.

---

## 11. Patrones y antipatrones

**Implementados** `[CÓDIGO]`:

| patrón | dónde | comentario para Golden |
|---|---|---|
| Media nativa externa + control por eventos y comandos (en la línea de Ports & Adapters / Remote Control) | §7 | es exactamente la frontera Media–Conversación del objetivo |
| Command queue con una sola tarea activa (`tasks` + `currentTask`, `replaceApplication`, `_injectTasks`, `kill`) | `call-session.js:1378-1453,2120-2294` | en Conversación, cola de comandos por sesión con "una locución activa" |
| Cancelación cooperativa (`Task.kill` → `_killInProgress`; `notifyTaskDone`/`awaitTaskDone`) | `task.js:24-25,74-80,174-184` | equivale a `CancelSpeech` |
| Identificador de correlación por recurso de media (`bugname`, `playback_id`, `taskId`) | `task.js:126-134`, `say.js:18-30`, `play.js:63-72` | adoptar la idea, pero con generación (ver antipatrón 2) |
| Buffer acotado con marcas de agua y señales pause/resume (bounded buffer + flow-control signaling) | `tts-streaming-buffer.js` | `SpeakChunk` con watermarks por sesión |
| Sticky event (último evento cacheado para suscriptores tardíos), usado para resolver la carrera kill/start | `sticky-event-emitter.js`, `play.js:65` | la carrera start-después-de-cancel existe; resolverla con un token, no con caché |
| Health-signal de drenaje al balanceador (`X-FS-Status`) | `sbc-pinger.js:129-151` | Modal no tiene equivalente documentado (BRIEF) |
| Traspaso de estado por token con TTL (REFER `context-<uuid>` + Redis 30 s) | `task.js:340-380`, `middleware.js:290-303` | migración de sesión entre réplicas; no aplicable sin SIP |
| Proyección de estado a Redis centralizada (`CallInfo.toRedisRecord` en el constructor, "rather than at each call site") | `call-session.js:123-131` | un solo punto de escritura para la proyección |

**Antipatrones o defectos**:
1. Clase-dios `CallSession`: 222 miembros que mezclan SIP, media y protocolo de app `[CÓDIGO]`.
2. **Identidad por rol en vez de por generación**: `bugname = prefijo + vendor + '_transcribe'` (`gather.js:440-664`). Eso obliga a heurísticas para eventos de "old gather" (`gather.js:1015-1019,1317-1320`).
3. Comandos sin `seq`, sin época y sin ACK; `msgid` entrante ignorado (§6.1).
4. Validación de comandos por `currentTask.name` y no por instancia (§6.1).
5. Drenaje anulado por un `SIGTERM → exit(0)` anterior; `lifecycleEmitter` capturado antes de reasignarse (§9).
6. `uncaughtException → exit(1)`: dominio de fallo de proceso completo (§9).
7. Validación de comandos ESL por `+OK` sin leer de vuelta el estado (§6.2), lo contrario de la regla 4 de Golden.
8. Listeners acumulativos (`bargeIn-done`), `new Error` sin `throw`, `this.isBinary` (§8, §9, §6.1).
9. Cola `queuedMsg` sin tope (§9).

**Latente o no recomendado para Golden**:
- `voiceagent` en el verbo `llm`: FS podría conectarse directamente a `/v1/agent/converse` de GB (`voice_agent_s2s.js:86-96,170-180`). Es una alternativa de integración que sustituiría a ai-engine por FreeSWITCH. No se recomienda en esta fase porque reemplaza Asterisk y ai-engine (§14).

---

## 12. Callers verificados de las piezas propuestas como referencia

| pieza | definición | callers verificados |
|---|---|---|
| `TtsStreamingBuffer.bufferTokens` | `tts-streaming-buffer.js:97-132` | `call-session.js:_lccTtsTokens:2004` ← `_onCommand 'tts:tokens':2432-2434` ← `WsRequestor._recvCommand:550-554`; `call-session.js:_internalTtsStreamingBufferTokens:2012-2014` ← `say.js:handlingStreaming:153,164` |
| `TtsStreamingBuffer.flush` | `:139-163` | `call-session.js:_lccTtsFlush:2016-2018` ← `_onCommand 'tts:flush':2436-2438`; `say.js:193` |
| `TtsStreamingBuffer.clear` | `:165-175` | `call-session.js:clearTtsStream:1026-1032` ← `gather.js:_killAudio:889`; `call-session.js:_lccTtsClear:2020-2022` ← `_onCommand 'tts:clear':2440-2442` |
| `TtsStreamingBuffer.start/stop` | `:55-92` | `call-session.js:startTtsStream:1034-1036` ← `say.js:148`; `stopTtsStream:1038-1044` ← `say.js:kill:459`, `call-session.js:_clearResources:2694` |
| eventos Pause/Resume/Empty/Connect/ConnectFailure | `tts-streaming-buffer.js:117,327,405,364,372` | suscritos en `call-session.js:exec:1366-1373` → `_onTtsStreamingPause/Resume/Empty/Connected/ConnectFailure:3305-3345` |
| `Task.eventIsForOurBug` | `task.js:126-134` | `gather.js:936`, `transcribe.js:493`, `listen.js:257,261,285,308,314,320` |
| `isMatchingEvent` | `say.js:18-30` | `say.js:297` (playback-stop), `:377` (playback-start) |
| `uuid_break` condicionado a `taskId` | `play.js:63-72` (listener) | emisores: `play.js:kill:129`, `call-session.js:replaceApplication:2170-2174` |
| `CallSession.kill(onBackgroundGatherBargein)` | `call-session.js:2177-2205` | `call-session.js:1071` (bargeIn-done), `:3281` (`_clearTasks`, **sin callers**: código muerto según grep) |
| `replaceApplication` | `call-session.js:2120-2175` | `task.js:performAction:258`, `task.js:redirect:300`, `call-session.js:_onCommand:2325`, `_handleRefer:2939` |
| `pingProxies` (drenaje) | `sbc-pinger.js:129-151` | `:67` (ScaleIn), `:84`/`:91` (Standby), `:121` (SIGUSR1), `:172` (intervalo), `:189` (inicial) |
| `SessionTracker idle` → scale-in | `session-tracker.js:43-48` | `app.js:134-139`; `remove` ← `call-session.js:exec:1462` |
| `transferCallToFeatureServer` | `task.js:340-380` | `conference.js:_doJoin:272`, `enqueue.js:196` |

---

## 13. Aplicabilidad a GoldenBridge

| rasgo jambonz | situación en Golden (BRIEF) | aplicabilidad |
|---|---|---|
| El audio nunca entra en el runtime de control | el loop de GB paga `submit`+`resume` por mensaje de audio; es el cuello medido | **valida la frontera objetivo**: el MediaWorker debe ser dueño del WS de audio y de VAD, SmartTurn, STT y TTS. Conversación solo debe recibir eventos. jambonz demuestra que, con esa frontera, el barge-in **puede** decidirse en control sin daño, siempre que el control no tenga tramas |
| El plano media es un runtime nativo distinto, en el mismo host | GB quiere MediaWorkers en Python 3.14t dentro de la tarea Modal | jambonz **no** demuestra que Python pueda ser el motor media. Su motor es C. Para Golden, la pregunta K_loop de un MediaWorker Python sigue abierta y debe medirse (PoC-0 del dictamen multiproceso) |
| VAD Silero dentro del motor media, con evento hacia el control | Silero es el 72 % del job de GB (BRIEF) | coherente con poner VAD en el MediaWorker, nunca en Conversación |
| `TtsStreamingBuffer` acotado + pause/resume | GB tiene un presupuesto de salida de 0,6 s en audio; no hay backpressure de texto hacia el LLM | **adaptar**: Conversación→Media `SpeakChunk{speech_id, chunk_seq}` con watermarks y eventos `SpeakPaused/Resumed`. **Añadir `speech_id`**: el `clear` sin generación de jambonz deja pasar tokens tardíos |
| `uuid_break` + `clear` como comandos de purga en Media | GB cancela el TTS en el mismo objeto que muta el historial | **adaptar**: `CancelSpeech{speech_id}` ejecutado en Media, que devuelve `SpeechInterrupted{speech_id, spoken_text}`. jambonz **no** devuelve lo efectivamente reproducido: solo `stop-playback`/`kill-playback` (`say.js:461-465`) |
| Correlación `bugname`/`playback_id`/`taskId` | GB→engine sin identidad de generación; el engine descarta los JSON de GB | **adaptar con generación**: cada evento de Media lleva `(session_id, epoch, turn_id|speech_id)`. El defecto del `bugname` por rol es la prueba de que el rol no basta |
| WS por llamada a la app con `msgid`/ACK y `session:reconnect` | GB recibe un WS por llamada del engine; el engine no reconecta | **estudiar**: el `session:reconnect` con el payload original es un modelo simple de reanudación; le falta `epoch` |
| Drenaje por señal al balanceador | en Modal, drenaje de WS en scale-down no documentado (BRIEF) | **estudiar**: publicar "cerrado" y esperar a `idle` es el patrón correcto. La implementación de jambonz es defectuosa (§9). En Golden debe verificarse leyendo de vuelta el estado efectivo, no por el código de retorno |
| Handoff por REFER + Redis TTL | sin SIP en el tramo engine⇄GB | **descartar** como mecanismo; la idea del token de traspaso con TTL queda como latente |

**Lo que jambonz no resuelve para Golden**:
- cómo repartir llamadas entre varios MediaWorkers **dentro** de una tarea sin un relay Python por trama. jambonz delega ese reparto en el SBC (SIP) y en un FS por host;
- cómo elevar K_loop de un loop Python con DSP por trama.

---

## 14. Riesgos de adopción o copia

| riesgo | detalle |
|---|---|
| reemplazo de stack | adoptar jambonz exige reemplazar Asterisk del proveedor, el túnel y ai-engine (o insertar un SBC jambonz delante), FreeSWITCH con módulos jambonz, drachtio-server, MySQL y Redis. Es una alternativa de **alto impacto**: rehace todo el borde telefónico y obliga a portar VAD y SmartTurn de GB a C dentro de FS |
| incompatibilidad con Modal | el feature server supone FS local, SIP y pings OPTIONS al SBC; nada de eso existe en Modal (endpoint WS autoescalable). El modelo de afinidad por diálogo SIP no se traslada |
| licencia | el código del feature server es MIT, copiable con aviso. `drachtio-modesl` es MPL-2.0 (copyleft por archivo: modificar sus archivos obliga a publicarlos). FreeSWITCH es MPL. La licencia de los módulos jambonz de FS es `[PENDIENTE]` |
| defectos heredables | drenaje anulado, `exit(1)` global, comandos sin seq ni época, validación por `+OK` (§9, §11). Copiar el patrón sin corregirlos reproduce los fallos |
| falsa confianza en capacidad | no hay mediciones. Cualquier "N llamadas por feature server" sería inventado |
| Node frente a Python 3.14t | los patrones son de un runtime single-thread con I/O asíncrona. La parte de control es trasladable a asyncio; la de media no lo es (C nativo) |

---

## 15. Lecciones concretas que pasan a la matriz comparativa

1. **El coordinador del camino por trama se elimina, no se paraleliza.** El control (Node) no ve audio. Media es un proceso nativo multihilo `[CÓDIGO]` §7.
2. **La detección de barge-in en Media y la decisión en control es viable solo si el control no carga tramas.** Es una secuencia de dos saltos locales `[CÓDIGO]` §8.
3. **El backpressure de texto TTS por llamada, con marcas de agua (1.000/200) y señales pause/resume hacia la app, es implementable y barato** `[CÓDIGO]` `tts-streaming-buffer.js:8-10,111-120,324-328`.
4. **Correlacionar por rol (bugname) no basta.** Hace falta una generación por turno o locución. Sin ella aparecen heurísticas para eventos de "old gather" `[CÓDIGO]` `gather.js:440-664,1015-1019`.
5. **Un canal de control sin seq, sin época y sin ACK de comandos deja abiertos los tokens tardíos tras `clear`** `[CÓDIGO]` §6.1, §8.
6. **El dominio de fallo es el host completo**: un único proceso de control por host y `exit(1)` ante cualquier excepción `[CÓDIGO]` §9.
7. **El drenaje se basa en una señal al balanceador y la espera de `idle`.** El patrón es correcto, pero esta implementación está rota por el orden de los handlers de señal `[CÓDIGO]` §9.

### Respuestas explícitas a las 18 preguntas

1. **Unidad de ownership**: la llamada (`CallSession`), con `dlg` SIP, `ep` FreeSWITCH y `WsRequestor` propios. El host (feature server) es la unidad de despliegue `[CÓDIGO]` §5.
2. **Quién recibe cada paquete de audio**: FreeSWITCH (canal `ep` y sus media bugs). Nunca Node `[CÓDIGO]` §7.
3. **Cuántas veces cruza el runtime de control cada paquete**: 0. Solo cruzan eventos (texto, detecciones, fin de reproducción) y, en TTS no streaming, el archivo una vez por enunciado `[CÓDIGO]` §7.
4. **¿Loop único compartido? ¿Qué trabajo hace?**: sí, un event loop Node por host, compartido por todas las llamadas. Hace SIP de aplicación, HTTP REST, WS con la app, ESL (comandos y eventos), la cola de verbos, la normalización de transcripciones, Redis/MySQL y métricas. No hace trabajo por trama `[CÓDIGO]` §4.
5. **Varios núcleos**: el control no (single-thread). La media sí, con hilos nativos de FreeSWITCH `[PENDIENTE]` aquí. La flota usa réplicas de host `[CÓDIGO]` §10.
6. **Asignación y afinidad**: el SBC elige el host con datos de `OPTIONS` (`X-FS-Status`, `X-FS-Calls`) o por servicio en K8S. La afinidad la da el diálogo SIP y, para REST, el `serviceUrl` en Redis. Dentro del host, round robin entre FS. Se mueve de host por REFER para conferencias y colas `[CÓDIGO]` + `[PENDIENTE]` (SBC) §10, §9.
7. **Estado compartido y local**: local por llamada en `CallSession`. Compartido en proceso: `SessionTracker`, pools y ESL. Compartido entre hosts por Redis: proyección de llamada, sets de nodos, hash de conferencia, handoff con TTL `[CÓDIGO]` §5.
8. **Protocolo de control**: WS JSON por llamada hacia la app (`ws.jambonz.org`, `msgid`/ACK); ESL hacia FS; SIP OPTIONS hacia el SBC; HTTP REST desde el api-server; Redis para coordinación entre hosts `[CÓDIGO]` §6.
9. **Orden, correlación, ACK, deduplicación, tardíos**:
   - orden: TCP del WS y procesamiento secuencial en el loop;
   - correlación: `callSid`, `msgid` (Node→app), `bugname`, `playback_id`, `taskId`;
   - ACK: solo de la app hacia los mensajes de Node, con timeout de 5 s;
   - deduplicación: solo se descartan ACK desconocidos;
   - mensajes tardíos: filtros por bug, playback y taskId, más heurísticas de "old gather". No hay epoch ni seq en comandos `[CÓDIGO]` §6.
10. **Backpressure sin detener llamadas ajenas**: el texto TTS es por llamada (watermarks 1.000/200, pause/resume); `playAudio` tiene tope 10; `queuedMsg` no tiene tope. El event loop compartido es el único acoplamiento entre llamadas, pero solo para control `[CÓDIGO]` §9.
11. **Dónde está el barge-in y dónde la purga**: la detección, en FS (STT/VAD). La decisión, en Node (`Gather._onTranscription`, `_onVadDetected`, o el bargeIn de fondo → `cs.kill(true)`). La purga, en FS (`uuid_break`, `*_tts_streaming clear|stop`) y en el buffer de texto de Node `[CÓDIGO]` §8.
12. **Audio no retirable**: lo ya escrito a RTP o en el jitter buffer remoto, lo ya sintetizado si el módulo no lo descarta (`[PENDIENTE]`), los tokens enviados por la app tras `user_interruption` y la palabra mínima de solape cuando `minBargeinWordCount ≥ 1` `[INFERENCIA]` §8.
13. **Si muere Media, Control o el canal**:
    - muere FS: reintento cada 3 s; si no queda FS, Node deja de aceptar llamadas; las llamadas nuevas reciben 480;
    - muere Node: `exit(1)` y reinicio por pm2, con el control de todas las llamadas del host perdido (el destino de los canales es `[PENDIENTE]`);
    - cae el WS de la app: reconexión ≤ 5 con `session:reconnect` y, si falla, cuelgue `[CÓDIGO]` §9.
14. **Drenaje en shutdown o despliegue**: `SIGUSR1`, SNS o `SIGTERM` (K8S) → `X-FS-Status: closed` → espera a `idle` → salida. **En la práctica, el `SIGTERM` lo anula `ProcessMonitor`** y AWS no completa el scale-in con llamadas activas `[CÓDIGO + INFERENCIA]` §9.
15. **¿Duplica modelos o recursos por proceso?**: en Node no hay modelos. STT y TTS son remotos y los abre FS por canal. VAD Silero corre en FS: si carga un modelo por canal o compartido es `[PENDIENTE]`. Por host se duplican el pool MySQL (10) y el cliente Redis `[CÓDIGO]` §5.
16. **Métricas para atribuir la rodilla**: `fs.sip.calls.count`, `fs.media.channels.in_use/free`, `fs.media.cpu_idle`, `fs.media.calls_per_second`, `app.hook.connect_time`, `app.hook.ws_response_time`, `app.hook.response_time`, `app.hook.remote_close`, `tts.response_time`, y spans OTel por verbo con latencia STT (`stt.latency_ms`, `talkspurts`). **No hay métrica de lag del event loop** ni de latencia comando ESL→efecto, que es la que delataría la rodilla del control `[CÓDIGO]` §4, `gather.js:1369-1391`.
17. **Qué se puede reutilizar legal y técnicamente**: legalmente, el código del feature server es MIT y se puede copiar con aviso de copyright. Técnicamente, conviene reimplementar los patrones en Python, no copiar código: buffer TTS con watermarks, correlación por recurso más generación, cancelación cooperativa de tasks, señal de drenaje. Evitar modificar archivos MPL (`drachtio-modesl`, FreeSWITCH) sin publicar los cambios `[CÓDIGO]` §1, §14.
18. **¿Exigiría reemplazar Asterisk, ai-engine, Modal o los proveedores?**: adoptarlo entero, sí: reemplaza Asterisk y ai-engine por SBC + drachtio + FreeSWITCH, y no encaja en el modelo de Modal. Los proveedores STT/TTS tendrían que ser los soportados por los módulos de FS. Adaptar sus patrones no exige reemplazar nada `[INFERENCIA]` §14.

### Piezas reutilizables

```text
Segmento: lib/utils/tts-streaming-buffer.js (TtsStreamingBuffer) + call-session.js:_lccTtsTokens/_lccTtsFlush/_lccTtsClear
Responsabilidad principal: recibir tokens de texto de Conversación, trocearlos por frontera de frase (≤1.800 car.) y empujarlos al TTS del plano media; aplicar backpressure por llamada.
Pattern principal: Bounded Buffer con marcas de agua + señalización de control de flujo (pause/resume) + marcador de flush en cola.
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] tts-streaming-buffer.js:8-10 (MAX_CHUNK 1800, HIGH 1000, LOW 200), :97-132 (bufferTokens → {status:'failed',reason:'full'} + emit Pause), :139-163 (flush con marcador), :165-175 (clear), :188-336 (_feedQueue dos fases), :324-328 (Resume), :338-345 (_api uuid_<vendor>_tts_streaming).
Callers verificados: call-session.js:2004 (← _onCommand 'tts:tokens':2432), :2013 (← say.js:153,164), :2017 (← 'tts:flush':2437, say.js:193), :1030 y :2021 (← gather.js:889, 'tts:clear':2441), :1035 (← say.js:148), :1042 (← say.js:459, _clearResources:2694); eventos → call-session.js:1366-1373,3305-3345.
Contrato de entrada/salida: entrada {id, tokens} | flush | clear; salida tts:tokens-result {id, status:'ok'|'failed', reason?}, tts:streaming-event {stream_open|stream_paused|stream_resumed|user_interruption|stream_closed}.
Política de error: conexión fallida → status Failed y rechazo de tokens; error al enviar un chunk → log y continuar; excepción en _feedQueue → vaciar cola.
Ownership: un buffer por CallSession (creado en exec:1367); lo muta solo el loop de Node.
Riesgo: sin speech_id/generación: tras clear acepta tokens tardíos; el 'id' de tts:tokens es solo eco; el comando ESL se valida por '+OK' sin leer estado.
Aplicación posible en Golden: comando C→M SpeakChunk{session_id, epoch, speech_id, chunk_seq, final} con cola acotada por sesión en el MediaWorker; eventos M→C SpeakPaused/SpeakResumed; CancelSpeech{speech_id} descarta cola y rechaza chunks de ese speech_id o anteriores.
```

```text
Segmento: correlación de eventos de media — task.js:eventIsForOurBug + say.js:isMatchingEvent + play.js (uuid_break condicionado a taskId vía StickyEventEmitter)
Responsabilidad principal: aceptar solo eventos del plano media que pertenezcan al recurso (media bug / reproducción / task) del consumidor actual y evitar purgar la reproducción de otra task.
Pattern principal: Correlation Identifier por recurso + guarda de cancelación por token.
Estado: implemented (con defecto: el bugname es por rol, no por generación)
Confianza: high
Evidencia: [CÓDIGO] task.js:110-134; say.js:18-30, :294-301 (descarta playback-stop de un play anterior), :374-384; play.js:63-72, :119-132; call-session.js:2170-2174; gather.js:440-664 (bugname = prefijo+vendor+'_transcribe'), :1015-1019, :1317-1320 (heurísticas de "old gather").
Callers verificados: eventIsForOurBug ← gather.js:936, transcribe.js:493, listen.js:257,261,285,308,314,320; isMatchingEvent ← say.js:297,377; 'uuid_break' sticky ← play.js:129, call-session.js:2173.
Contrato de entrada/salida: evento FS con cabeceras media-bugname / variable_tts_playback_id / file → booleano de pertenencia; emisión 'uuid_break'(task) → uuid_break solo si task.taskId coincide.
Política de error: falla abierta (true) si falta cabecera o bugname (compatibilidad con FS antiguos); evento no coincidente → log y re-suscripción once.
Ownership: cada Task tiene sus handlers (Task.eventHandlers) y su taskId; el endpoint es compartido entre tasks de la misma llamada.
Riesgo: el fail-open y la identidad por rol dejan pasar eventos de una generación anterior del mismo rol; el emparejamiento por nombre de archivo no es único con caché TTS [INFERENCIA].
Aplicación posible en Golden: todos los eventos M→C y el audio M→engine llevan (session_id, epoch, turn_id|speech_id); el consumidor descarta por desigualdad de generación, nunca falla abierto; CancelSpeech es idempotente y condicionado a speech_id.
```

```text
Segmento: CallSession.exec / replaceApplication / _injectTasks / kill + Task.kill/notifyTaskDone/awaitTaskDone
Responsabilidad principal: ejecutar el programa de la aplicación como cola de comandos por llamada con una sola tarea activa; reemplazar o inyectar comandos y cancelar la activa.
Pattern principal: Command Queue + cancelación cooperativa (single active command invariant).
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] call-session.js:1378-1453 (bucle; espera _awaitCommandsOrHangup si WS), :2120-2175 (replace: tasks nuevas, stackIdx++, kill currentTask o despertar), :2258-2294 (inyección antes de Gather), :2177-2205 (kill y purga hasta config bargein=false), :3232-3262; task.js:24-25,74-80,174-184.
Callers verificados: replaceApplication ← task.js:258,300, call-session.js:2325,2939; kill(true) ← call-session.js:1071; currentTask.kill ← call-session.js:1508 (_callReleased), :1686, :2161, :2182, :2271.
Contrato de entrada/salida: entrada: lista de verbos (redirect con o sin queueCommand); salida: verb:status starting/finished, actionHook al resolver.
Política de error: BADPRECONDITIONS/NonFatalTaskError → siguiente task; otro error → romper el bucle y limpiar (_clearResources).
Ownership: la CallSession (un loop); stackIdx solo se registra en log, no se usa para rechazar resultados tardíos.
Riesgo: sin generación aplicada a resultados (stackIdx no filtra nada); guardas de precondición sin throw (call-session.js:2490,2609); clase-dios.
Aplicación posible en Golden: en Conversación, cola de comandos por sesión con una locución activa; Speak nuevo con interrupt → CancelSpeech del speech_id activo; usar stackIdx-like como session_epoch/turn_id y SÍ filtrar con él.
```

```text
Segmento: drenaje — sbc-pinger.js:pingProxies/dryUpCalls + session-tracker idle + app.js:handle
Responsabilidad principal: anunciar al balanceador que el nodo no acepta llamadas nuevas y salir cuando no queden sesiones.
Pattern principal: Health-signal de drenaje (connection draining) + latch de idle.
Estado: implemented con defectos (no recomendado copiarlo tal cual)
Confianza: medium
Evidencia: [CÓDIGO] sbc-pinger.js:37-126,129-151,193-196; session-tracker.js:43-48; app.js:133-139,174-211; defectos process-monitor.js:55-65 + app.js:37 (SIGTERM → exit(0) primero), install-srf-locals.js:74 (captura del emitter).
Callers verificados: pingProxies ← sbc-pinger.js:67,84,91,121,172,189; idle ← app.js:134.
Contrato de entrada/salida: entrada: SIGUSR1 / SNS TERMINATING / SIGTERM (K8S); salida: OPTIONS X-FS-Status closed, X-FS-Calls, X-FS-ServiceUrl; removeFromSet en Redis; exit al llegar a 0.
Política de error: error de OPTIONS → log; sin reintento específico.
Ownership: estado de proceso (closure dryUpCalls), duplicado en sets de Redis.
Riesgo: el orden de handlers de señal anula el drenaje; el estado ScaleIn vive en un objeto distinto al consultado; validación por envío, no por lectura de que el balanceador dejó de enrutar.
Aplicación posible en Golden: si Modal no drena WS (BRIEF: no documentado), el MediaWorker o la tarea debe exponer "cerrado a nuevas sesiones" al mecanismo de admisión, esperar a sesiones = 0 y verificarlo leyendo el estado efectivo (conteo real), no por el éxito de la señal.
```
