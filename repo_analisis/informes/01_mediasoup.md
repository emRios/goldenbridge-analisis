# 01 — mediasoup: auditoría de la separación control (Node/Rust) ⇄ `mediasoup-worker` (C++)

Auditor: Claude (subagente). Fecha: 2026-10-09. Término de comparación: `../BRIEF_GOLDEN.md` (GoldenBridge `api@08932a6`, ai-engine `d059295`).
Rutas relativas a `repos/mediasoup/`. Líneas verificadas sobre el commit indicado abajo. No se compiló ni se ejecutó nada; los tests citados se leyeron, no se corrieron.

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/versatica/mediasoup.git` |
| Branch analizado | `v3` (default; clon superficial, `git log` = 1 commit) |
| Commit | `7d21d63d15ce233be0e7b592cc03a54327054403` |
| Fecha del commit | `2026-10-09T15:08:29+02:00` (autor Iñaki Baz Castillo) — "SCTP: Don't emit association errors in `Association::ResetStreams()` (#1990)" |
| Versión | Node `3.29.0` (`package.json:3`) · crate Rust `0.30.0` (`rust/Cargo.toml:3`) · `mediasoup-sys` `0.20.0` (`worker/Cargo.toml:3`) |
| Licencia observada | **ISC** (`LICENSE`; `package.json:10`, `rust/Cargo.toml:12`, `worker/Cargo.toml:11`). Dependencias nativas vía Meson wraps: libuv 1.51.0, openssl, libsrtp3, abseil, flatbuffers, unordered-dense (`worker/subprojects/*.wrap`); `worker/deps/libwebrtc` trae su propio `LICENSE`/`PATENTS` (BSD estilo WebRTC) `[CÓDIGO]` |
| Submódulos | ninguno (`.gitmodules` ausente) |
| Mantenimiento visible | último commit el mismo día de la auditoría; `CHANGELOG.md` con sección `NEXT` abierta (PRs #1985, #1987, #1990) y releases 3.27.0 → 3.29.0 recientes `[DOCUMENTACIÓN]` |

---

## 2. Dictamen ejecutivo

1. **mediasoup saca completamente el plano de media del runtime de control.** Cada paquete RTP entra por un socket UDP/TCP que pertenece al loop libuv de un único `mediasoup-worker`, se enruta Transport → Producer → Router → Consumers y sale por otro socket, **sin cruzar nunca el canal hacia Node/Rust** `[CÓDIGO]` (cadena `UdpSocketHandle.cpp:80` → `Transport.cpp:1589-1641` → `Producer.cpp:711` → `Transport.cpp:2629-2633` → `Router.cpp:669-719` → `Consumer.cpp:1142-1170`). El canal sólo transporta requests/responses/notificaciones de control, salvo la vía opt-in `DirectTransport`/`Producer.send()`, que **sí** mete cada paquete en el IPC y es precisamente el antipatrón para Golden.
2. **No paraleliza un loop: replica loops sin estado compartido.** Cada worker es **un hilo, un `uv_loop_t`, sin hilos auxiliares** (`DepLibUV.cpp:10`, `DepLibUV.cpp:110-120`; ningún `std::thread`/`uv_queue_work` en `worker/src`) `[CÓDIGO]`. Dentro de un worker todos los routers comparten ese único loop: es un coordinador único por worker. El uso de varios núcleos se obtiene creando N workers (N procesos en Node; N hilos con estado `thread_local` en Rust) y repartiendo routers entre ellos **a criterio de la aplicación**: la librería no trae balanceador `[CÓDIGO]` (`index.ts:99-139` sólo crea uno).
3. **El valor para Golden no es el componente (descartar como dependencia) sino cinco patrones del contrato** (adaptar): (a) canal "salvavidas" cuyo EOF mata al worker y cuya muerte cascada a todos los proxies de control; (b) registro de handlers por ID opaco no reutilizable (UUIDv4) con `NotFoundError` "suave" para comandos a recursos ya cerrados; (c) analizadores nativos que observan cada paquete y emiten **eventos agregados/de flanco** a baja frecuencia (`AudioLevelObserver`); (d) ingreso directo al worker dueño: el socket se abre *dentro* del worker y la señalización le dice al cliente a qué puerto hablar; (e) modo "worker como hilo" (Rust) con estado `thread_local` y despertador `uv_async_send`, análogo directo a un MediaWorker por hilo en Python 3.14t.
4. **Huecos que Golden no debe copiar:** IPC sin backpressure ni topes de cola, requests sin timeout ni heartbeat (un worker colgado deja promesas pendientes para siempre), sin drenaje, sin reinicio automático, sin métricas de lag del loop, y barge-in **mediado por control** (detección en worker → evento → decisión en Node → comando de vuelta), con intervalo mínimo de 250 ms en el observador de nivel.
5. **Capacidad:** el repo no contiene benchmark reproducible (`doc/Charts.md` son PNG de mediasoup **v2**, sin script) → toda cifra de K para mediasoup es `[PENDIENTE]`. Además, su trabajo por paquete es reenvío sin decodificar; el de Golden es DSP + inferencia ONNX. Las capacidades no se pueden trasladar.

---

## 3. Diagrama del flujo relevante

```text
                      ┌──────────────────────────── proceso de CONTROL (Node: 1 loop V8 | Rust: executor + hilos) ───────────────────────────┐
  señalización app ──▶│ Worker(proxy) ─ Router(proxy) ─ Transport(proxy) ─ Producer/Consumer(proxy)    #sents: Map<id,Sent>  (sin timeout)  │
  (fuera del repo)    │      │  request(method, handlerId=UUID, body)  ▲ response(id, accepted|error)  ▲ notification(handlerId, event)    │
                      └──────┼─────────────────────────────────────────┼───────────────────────────────┼─────────────────────────────────────┘
                             │ fd3/fd4 pipes (Node) | readFn/writeFn + uv_async_send (Rust)  — framing: u32 LE len + FlatBuffer Message
                             │ tope 8 MiB por mensaje · colas de escritura SIN tope · FIFO por canal
              ┌──────────────▼───────────────────────────── mediasoup-worker #k  (1 hilo, 1 uv_loop_t, estado thread_local) ─────────────┐
              │ ChannelSocket ─▶ Worker::HandleRequest ─▶ ChannelMessageRegistrator[handlerId] ─▶ Router/Transport/Producer/Consumer     │
              │                       (respuesta síncrona dentro del mismo callback)                                                       │
              │                                                                                                                             │
   RTP in ═══▶│ UdpSocketHandle ─▶ WebRtcServer demux(tupla|ufrag) ─▶ Transport::ReceiveRtpPacket ─▶ Producer ─▶ Router ─▶ Consumer[i] ═══▶│═══▶ RTP out
   (kernel    │                                                                     └─▶ RtpObserver (AudioLevel/ActiveSpeaker): acumula   │
    entrega   │                                                                          por paquete; timer 250–5000 ms ─▶ Emit(VOLUMES |   │
    al worker │                                                                          SILENCE) ─────────────────────────▲ notificación  │
    dueño)    │  Consumer::IsActive() gate (pause/resume por request)                                                                       │
              └─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
       ╳ media NUNCA cruza el canal, salvo DirectTransport: Producer.send()=1 notificación/paquete ↓ · CONSUMER_RTP=1 notificación/paquete ↑
       Workers k≠j no comparten nada; un router en k habla con uno en j sólo vía PipeTransport (UDP localhost), no vía control.
```

---

## 4. Mapa de procesos, threads, loops y colas

| Componente | Tipo | Loops/hilos | Colas | ¿Acotada? | Evidencia |
|---|---|---|---|---|---|
| Control Node | 1 proceso | 1 loop V8 para **todos** los workers | `#sents` (requests pendientes), buffer de escritura del `Duplex` (fd3), `#recvBuffer` | `#sents` sin tope ni timeout; escritura sin tope (valor de retorno de `write()` ignorado); `#recvBuffer` ≤ 8 MiB y **se descarta entero** si se excede | `Channel.ts:44-53`, `:292`, `:366`, `:82-89` `[CÓDIGO]` |
| `mediasoup-worker` (Node) | 1 proceso hijo por `createWorker()` | 1 hilo, 1 `uv_loop_t` propio | buffer de lectura fijo 8 MiB; cola `uv_write` (copia lo no escrito) | lectura acotada; escritura **sin tope** | `Worker.ts:142-167`, `main.cpp:10-39`, `ChannelSocket.cpp:13-14,44-49`, `UnixStreamSocketHandle.cpp:206-269,343-367` `[CÓDIGO]` |
| Worker (Rust) | 1 **hilo** `mediasoup-worker-{id}` dentro del proceso de la app | 1 `uv_loop_t` `thread_local` por hilo | `OutgoingMessageBuffer.messages: VecDeque` (control→worker); `async_channel::unbounded` para logs | **sin tope** ambas | `rust/src/worker/utils.rs:39-103`, `rust/src/worker/channel.rs:173-176,206-209,237,392-395` `[CÓDIGO]` |
| Control Rust | executor `async_executor` (1 hilo propio por defecto o externo multi-hilo) | callbacks de notificación se ejecutan **en el hilo del worker** | `buffered_notifications_for` (mientras se crea un recurso) | por recurso, sin tope | `worker_manager.rs:85-122`, `common.rs:82-93`, `channel.rs:247-275,344-356` `[CÓDIGO]` |
| Hilos auxiliares en el worker | — | **ninguno**: sin `std::thread`, `pthread_create`, `uv_thread_create` ni `uv_queue_work` en `worker/src`, `worker/include` | — | — | `grep` vacío `[CÓDIGO]` |

Ownership del loop: dentro del worker, `Worker::Worker` registra el canal y llama `DepLibUV::RunLoop()` (bloqueante) — `Worker.cpp:14-44`. Todo (sockets RTP, timers, canal) vive en ese loop.

---

## 5. Ownership por sesión y recursos compartidos

- **Unidad de ownership: el worker es dueño de sus routers; el router de sus transports; el transport de sus producers/consumers.** En C++: `Worker::mapRouters` (`Worker.cpp:313-329`), `Router` registra su handler (`Router.cpp:25`), y así cada entidad (`Transport`/`Producer`/`Consumer`… `RegisterHandler` en `Consumer.cpp:445`, `Producer.cpp:214`, `WebRtcTransport.cpp:248,328`, etc.) `[CÓDIGO]`. En control, cada entidad es un **proxy** que guarda su ID y el `Channel` del worker (`Worker.ts:537-548`) `[CÓDIGO]`.
- **No hay estado compartido entre workers**: loop, logger, settings, mapa de puertos y builders son `thread_local` (`DepLibUV.cpp:10-11`, `Logger.cpp:10-11`, `Settings.cpp:24`, `PortManager.hpp:143`, `ChannelRequest.cpp:89`; 57 apariciones de `thread_local`). Lo único global con mutex es la inicialización de librerías (`DepLibSRTP.cpp:11,61,84`, `DepOpenSSL.cpp:12`, `DepLibWebRTC.cpp:12`, `Settings.cpp:20`) `[CÓDIGO]`.
- **Recurso compartido dentro de un worker:** `WebRtcServer` (un puerto UDP/TCP para muchos transports) **sólo** sirve a routers del mismo worker: `Router.cpp:245-251` → `Worker::OnRouterNeedWebRtcServer` busca en el mapa del worker (`Worker.cpp:451-466`) `[CÓDIGO]`.
- **Comparación con Golden:** el equivalente a "llamada" sería un router (o un transport) asignado a un worker de por vida. mediasoup no tiene un objeto de 106 campos que mezcle media y conversación: el estado de media vive en C++ y el estado de aplicación (salas, usuarios) fuera de la librería `[INFERENCIA]`.

---

## 6. Contrato de control/IPC

**Transporte.** Node: el hijo hereda `fd 3` (control→worker) y `fd 4` (worker→control) como pipes (`Worker.ts:159-164`; `main.cpp:10-11`). Rust: sin fds; el worker se arranca en un hilo con `mediasoup_worker_run(..., channelReadFn, ctx, channelWriteFn, ctx)` (`lib.cpp:39-69`, `lib.hpp`, `utils.rs:71-88`); el control encola y despierta al worker con `uv_async_send` (`channel.rs:391-411`), y el worker drena **todos** los mensajes pendientes en un callback (`ChannelSocket.cpp:18-26,189-283`) `[CÓDIGO]`.

**Framing.** Prefijo `uint32` little-endian con la longitud + `FBS.Message` (`message.fbs`: union `Request | Response | Notification | Log`). Node: `finishSizePrefixed` (`Channel.ts:275,352`) y parseo en bucle (`Channel.ts:91-168`); C++: `ConsumerSocket::UserOnUnixStreamRead` (`ChannelSocket.cpp:389-439`). Tope `MESSAGE_MAX_LEN = 8388608` en ambos lados (`Channel.ts:30-31`, `ChannelSocket.cpp:13-14`). En Rust el prefijo se quita porque "sólo hace falta para NodeJS" (`channel.rs:94-98`) `[CÓDIGO]`.

**Esquema.**
- `Request{ id: uint32, method: Method(uint8), handler_id: string(required), body }` (`request.fbs`).
- `Response{ id, accepted: bool, body, error: string, reason: string }` (`response.fbs:48-54`).
- `Notification{ handler_id: string(required), event: Event(uint8), body }` (`notification.fbs:81-85`); `Event` separa "Notifications to worker" (`WORKER_CLOSE`, `TRANSPORT_SEND_RTCP`, `PRODUCER_SEND`, `DATAPRODUCER_SEND`) de "from worker" (`WORKER_RUNNING`, estados ICE/DTLS, `PRODUCER_SCORE`, `CONSUMER_RTP`, `AUDIOLEVELOBSERVER_VOLUMES/SILENCE`, …) (`notification.fbs:13-49`) `[CÓDIGO]`.

**Correlación y enrutamiento.**
- Respuestas: `id` incremental `uint32` con wrap a 1 en Node (`Channel.ts:314-320`) / `wrapping_add` en Rust (`channel.rs:379-382`); mapa `#sents` (`Channel.ts:396`) y `processResponse` (`Channel.ts:400-444`) `[CÓDIGO]`. No hay detección de colisión tras el wrap (`Map.set` sobrescribe) `[CÓDIGO]`.
- Requests al worker: los de nivel worker se despachan en `Worker::HandleRequest` (`Worker.cpp:249-347`); el resto por `handlerId` → `ChannelMessageRegistrator::GetChannelRequestHandler` (`Worker.cpp:349-366`, `ChannelMessageRegistrator.cpp:94-109`) `[CÓDIGO]`.
- Notificaciones del worker: `ChannelNotifier::Emit(targetId, event, body)` (`ChannelNotifier.cpp:16-28`, `.hpp:20-36`); en Node se emiten como evento con nombre = `handlerId` (`Channel.ts:446-456`); cada proxy se suscribe con `channel.on(id, …)` (p. ej. `Consumer.ts` `handleWorkerNotifications`) `[CÓDIGO]`.
- **IDs**: los genera el control (`randomUUID`, `utils.ts:22-24`; usos en `Worker.ts:475,524`, `Router.ts:389,580,739,851`, `Transport.ts:537,659`…). Son únicos y no se reutilizan, por lo que funcionan como identidad + época a la vez. Excepción: `produce({id})` y `pipeToRouter(keepId)` permiten reutilizar un `producerId` (`Transport.ts:537`, `Router.ts:1130`) `[CÓDIGO]`. El worker rechaza duplicados (`ChannelMessageRegistrator.cpp:60-83`; `Worker.cpp:221-240`) `[CÓDIGO]`.
- La notificación `WORKER_RUNNING` usa como `handlerId` el **PID** (`Worker.cpp:37-39`; `Worker.ts:182-190`; Rust la distingue parseando a `u64`, `channel.rs:254-259`) `[CÓDIGO]`.

**Orden.**
- FIFO por canal (un stream, un lector) y el worker responde **síncronamente**: `CallbackRead`/`OnConsumerSocketMessage` hacen `delete request` justo después de `HandleRequest` (`ChannelSocket.cpp:229-251,319-341`), de modo que la respuesta sale en el mismo turno y en el orden de llegada `[CÓDIGO]`.
- Node retrasa las notificaciones con `setImmediate` para que una respuesta resuelta como Promise no quede detrás de una notificación posterior (issue #510) — `Channel.ts:446-456` `[CÓDIGO]`.
- Rust: el control *bufferiza* las notificaciones de un recurso mientras se crea (`buffer_messages_for`, `channel.rs:342-356`; uso en `worker.rs` `create_router`, `let _buffer_guard = …buffer_messages_for(router_id.into())`; y al arrancar el worker `utils.rs:51-52`, `worker.rs:465-505`) y las entrega al soltar el guard (`channel.rs:65-85`) `[CÓDIGO]`.

**ACK / dedup / tardíos.** Todo request recibe `accepted` o `error` (`ChannelRequest.cpp:118-182`), excepto cuando muere el canal: entonces `Channel.close()` rechaza todos los pendientes con `WorkerClosedError` (`Channel.ts:208-211`; Rust: el `oneshot` cerrado se mapea a `WorkerClosedError`, `channel.rs:425-431`). **No hay dedup ni `seq`**; es at-most-once sobre un stream fiable. Para los mensajes tardíos hay tres barreras: (1) el proxy cerrado hace `removeAllListeners(id)` y comprueba `#closed` (`Consumer.ts:210-213,242-245,468-476,578-581`); (2) el worker contesta `NotFoundError` a un `handlerId` desconocido (`Worker.cpp:352-361`) y registra (sin responder) las notificaciones a handlers inexistentes (`Worker.cpp:394-401`, `ChannelSocket.cpp:265-268`); (3) Rust trata `NotFoundError` como error "suave" con respuesta por defecto (`channel.rs:462-467`) `[CÓDIGO]`.

**Sin timeouts.** `Channel.ts` no tiene ningún temporizador: un request a un worker con el loop bloqueado queda pendiente indefinidamente. No hay heartbeat `[CÓDIGO]` (lectura completa de `Channel.ts` y `Worker.ts`).

---

## 7. Ruta de audio y frecuencia de cruces

| Camino | Cruces del runtime de control por paquete | Evidencia |
|---|---|---|
| WebRTC/Plain/Pipe Transport (normal) | **0** | `UdpSocketHandle.cpp:80` (recv en el loop del worker) → `WebRtcServer.cpp:416-494` (demux por tupla o ufrag ICE) → `Transport.cpp:1589-1641` → `Producer.cpp:711` → `Transport.cpp:2629-2633` → `Router.cpp:669-719` (fan-out a consumers + RtpObservers) → `Consumer.cpp:1142-1170` → `UdpSocketHandle.cpp:127-184` `[CÓDIGO]` |
| `Producer.send()` (DirectTransport, entrada) | **1 notificación por paquete** control→worker, con copia (`memcpy`) | `Producer.ts:317-336`, `Producer.cpp:512-540` `[CÓDIGO]` |
| `consumer.on('rtp')` (DirectTransport, salida) | **1 notificación por paquete** worker→control | `DirectTransport.cpp:157-192`, `Consumer.ts:578-590`; Rust `consumer.rs:916-919` `[CÓDIGO]` |
| `AudioLevelObserver` | 0 por paquete; **1 notificación cada `interval`** (250–5000 ms, por defecto 1000) o 1 al pasar a silencio | `AudioLevelObserver.cpp:24-45,83-104,146-207,222-227`, `Router.ts:1285-1287` `[CÓDIGO]` |

Un detalle relevante para Golden: mediasoup **nunca decodifica audio**. `AudioLevelObserver` lee la extensión de cabecera RFC 6464 (`packet->ReadSsrcAudioLevel`, `AudioLevelObserver.cpp:95`) `[CÓDIGO]`. El coste por paquete es de enrutamiento, no de DSP; el de Golden es µ-law→pcm16→soxr→Silero (≈401 µs/chunk según el brief).

---

## 8. Secuencia de barge-in

mediasoup no tiene concepto de TTS, reproducción ni turno. Lo más parecido es "dejar de reenviar":

1. **Detección:** `AudioLevelObserver`/`ActiveSpeakerObserver` en el worker → notificación a control cada ≥250 ms (`AudioLevelObserver.cpp:33-40`) `[CÓDIGO]`.
2. **Decisión:** en la aplicación (fuera del repo) `[INFERENCIA]`.
3. **Comando:** `consumer.pause()` → request `CONSUMER_PAUSE` → `Consumer.cpp:747-770` pone `paused=true` → la puerta `IsActive()` (`Consumer.hpp:119-129`) corta en `Consumer::SendRtpPacket` (`Consumer.cpp:1170`) el siguiente paquete que procese el loop `[CÓDIGO]`.
4. **Purga:** no existe porque no hay cola de reproducción. Un SFU reenvía en cuanto recibe. Lo que ya salió por UDP (`uv_udp_try_send`/`uv_udp_send`, `UdpSocketHandle.cpp:152-190`) o quedó en la cola libuv **no se puede retirar** `[CÓDIGO]`. El jitter buffer del receptor queda fuera del alcance `[INFERENCIA]`.

**Lección (negativa):** el barge-in "detectar en media → decidir en control → comandar a media" cuesta dos cruces de IPC y el intervalo del observador. Golden necesita lo contrario: que el MediaWorker corte el TTS **localmente** y luego informe `SpeechInterrupted` a Conversación `[INFERENCIA]`.

---

## 9. Backpressure, fallos, reconexión y drenaje

**Backpressure del IPC: no existe.**
- Node: `this.#producerSocket.write(buffer)` sin mirar el valor de retorno ni `drain` (`Channel.ts:292,366`) `[CÓDIGO]`.
- C++: `uv_try_write`, y lo que no cabe se copia a un `UvWriteData` y se encola con `uv_write` sin tope (`UnixStreamSocketHandle.cpp:220-268`) `[CÓDIGO]`.
- Rust: `VecDeque` y `async_channel::unbounded` (`channel.rs:206-209,237`) `[CÓDIGO]`.
- Recepción: buffer fijo de 8 MiB. En Node, si se supera, **se descarta todo el buffer** ("discarding all data in it", `Channel.ts:82-89`), y con ello el framing de lo que venga después `[CÓDIGO]`. En C++, sin espacio se pasa `buf->len=0` (`UnixStreamSocketHandle.cpp:357-366`); que esto acabe cerrando el pipe por la rama de error de `OnUvRead` (`:398-410`) es `[INFERENCIA]` (depende de que libuv devuelva `UV_ENOBUFS`).
- **Backpressure por recurso (SCTP):** umbral configurable más eventos `DATACONSUMER_BUFFERED_AMOUNT_LOW` y `DATACONSUMER_SCTP_SENDBUFFER_FULL` (`DataConsumer.cpp:454-466`; request `DATACONSUMER_SET_BUFFERED_AMOUNT_LOW_THRESHOLD`) `[CÓDIGO]`. La señal llega como evento al control, sin bloquear el loop. Patrón reutilizable.
- **Aislamiento:** entre workers es total (nada compartido). Dentro de un worker no lo hay: un handler lento o un fan-out grande retrasa a todos los routers del mismo loop `[INFERENCIA]` basada en §4 (1 hilo, sin pool).

**Fallos.**

| Evento | Qué ocurre | Evidencia |
|---|---|---|
| Muere el worker (Node) | `child.on('exit')` → si ya estaba `running`, `workerDied()` → `#closed/#died=true`, `channel.close()` (rechaza pendientes) → `router.workerClosed()` en cascada → `emit('died')`, `'subprocessclose'`, `observer 'close'` | `Worker.ts:192-228,556-589`, `Router.ts:243-275`, `Channel.ts:199-230`; test `test-Worker.ts:214-310` (SIGINT/SIGTERM/SIGKILL al PID) `[CÓDIGO]` |
| Falla antes de `WORKER_RUNNING` | `@failure` con `TypeError('wrong settings')` si el código de salida es 42 | `Worker.ts:198-218`, `lib.cpp:97-106` `[CÓDIGO]` |
| Muere el control | EOF en fd3 → `OnUvRead(UV_EOF)` → `ConsumerSocket::UserOnUnixStreamSocketClosed` → `ChannelSocket::OnConsumerSocketClosed` → `Worker::OnChannelClosed` ("channel remotely closed, closing myself") → `Close()` borra todos los routers; el loop se vacía y el proceso sale. Además `detached:false` | `UnixStreamSocketHandle.cpp:388-396`, `ChannelSocket.cpp:368-373,441-447`, `Worker.cpp:408-421,56-90`, `Worker.ts:157` `[CÓDIGO]` |
| Error de escritura del canal | `OnUvWriteError` → `Close()` y se notifica a la subclase | `UnixStreamSocketHandle.cpp:413-428` `[CÓDIGO]` |
| Worker-hilo (Rust) muere | El hilo devuelve un código de estado → `on_exit` → `handlers.dead` + `close` | `utils.rs:90-95`, `worker.rs:432-452` `[CÓDIGO]` |
| Assert/abort en C++ | Ejecutable: `std::abort()`, muere el proceso del worker. Librería: `throw std::runtime_error`, capturado en `lib.cpp:191-195` → 134. Un segfault en modo hilo tumba el proceso entero de control | `Logger.hpp:460-479` `[CÓDIGO]`; lo del segfault es `[INFERENCIA]` |
| Excepción al construir `ChannelRequest` (método desconocido) | El constructor ya respondió (`Error`) y lanza; el `catch` llama `request->Error()` con `request == nullptr` | `ChannelRequest.cpp:105-112` + `ChannelSocket.cpp:229-249` — **posible desreferencia nula**, `[INFERENCIA]` (sólo alcanzable con versiones desalineadas) |

**Reconexión / reinicio:** no existe. Tras `died`, la aplicación tiene que crear otro worker y reconstruir routers, transports y producers, y los clientes renegociar. El estado se pierde `[CÓDIGO]` (no hay lógica de reinicio en `Worker.ts` ni en `worker.rs`).

**Drenaje: no existe.** SIGINT/SIGTERM en el control → `worker.close()` inmediato (`Worker.ts:293-294,601-607`). En el worker, `OnSignal` → `Close()` borra routers en el acto (`Worker.cpp:423-449`). `worker.close()` envía `WORKER_CLOSE` y destruye los sockets sin esperar (`Worker.ts:341-373`). `doc/Closures.md` dice "Kills the mediasoup-worker process … via signal", pero el código envía una notificación y cierra el canal: **la documentación está desfasada** `[DOCUMENTACIÓN]` vs `[CÓDIGO]`.

---

## 10. Escalado y uso real de núcleos

- **K_loop = K_worker** en mediasoup: un worker es exactamente un loop de un hilo `[CÓDIGO]` (§4).
- **K_task** (máquina/proceso): suma de N workers. La librería no fija N. La doc Rust dice "A worker represents a mediasoup C++ thread that runs on a single CPU core" (`rust/src/worker.rs:1-2,556`) y ofrece `thread_initializer` "for pinning worker threads to CPU cores" (`worker.rs:190-192`) `[DOCUMENTACIÓN]`. Uno o varios workers por núcleo es decisión del despliegue. El repo no trae ninguna medición `[PENDIENTE]`.
- **Afinidad de un "room":** un router vive en un worker para siempre. Para repartir un room grande entre núcleos existe `router.pipeToRouter()`: crea un par de `PipeTransport` UDP entre routers de workers distintos y la media pasa worker↔worker **sin pasar por el control** (`Router.ts:960-1110`; limpieza del par fallido `:1081-1103`, corregida en 3.28.0) `[CÓDIGO]`. Es fan-out por réplica, no paralelismo dentro de un loop.
- **Asignación:** a cargo de la aplicación (`createRouter` sobre el worker que elija). El ejemplo Rust `videoroom` crea **un worker por room** (`rust/examples/videoroom.rs:87-120,247-296`) `[CÓDIGO]`. No hay medición de carga ni balanceo integrados.
- **Ingreso:** el kernel entrega cada datagrama directamente al socket del worker dueño (puerto del rango `rtcMinPort/rtcMaxPort` del worker, o el puerto de su `WebRtcServer`). El cliente aprende el puerto por los candidatos ICE que devuelve la señalización (`Router.cpp:253-258`) `[CÓDIGO]`. **No hay dispatcher en el plano de media.** Cada worker necesita su propio puerto o su propio rango.
- **K_flota:** fuera del alcance del repo `[PENDIENTE]`.
- **Conclusión de escalado:** mediasoup **replica** coordinadores de un hilo sin estado compartido y saca de su camino el control. No hace más rápido un loop. Para Golden esto equivale a "N MediaWorkers × K_loop", que es paralelismo, no aumento de K por loop `[INFERENCIA]`. La diferencia con el "N Goldens completos por tarea" del dictamen del 28-sep es que aquí lo replicado es **sólo el plano de media** bajo **un** control común.

---

## 11. Patrones y antipatrones

| Patrón | Estado | Dónde |
|---|---|---|
| Worker de media sin estado compartido, un hilo por loop (actor por núcleo) | implemented | `DepLibUV.cpp:10`, `thread_local` ×57, `utils.rs:54-97` |
| Proxy remoto (objetos de control = ID + canal) | implemented | `Worker.ts:537-548`, `Router.ts` |
| Request/Response con id de correlación + registro de handlers por ID | implemented | `Channel.ts:300-444`, `ChannelMessageRegistrator.cpp:53-126` |
| Observer para notificaciones dirigidas por `handlerId` | implemented | `Channel.ts:446-456`, `ChannelNotifier.cpp` |
| Canal salvavidas (EOF ⇒ el hijo se cierra) | implemented | `Worker.cpp:408-421` |
| Cierre en cascada (Composite: worker→router→transport→producer/consumer) | implemented | `Worker.ts:556-589`, `Router.ts:198-275`, `doc/Closures.md` |
| Buffer de notificaciones hasta que exista el suscriptor | implemented (Rust) | `channel.rs:57-85,342-356` |
| Analizador nativo con salida agregada o de flanco | implemented | `AudioLevelObserver.cpp` |
| Watermarks de backpressure como eventos | implemented (SCTP) | `DataConsumer.cpp:454-466` |
| Supervisor con reinicio | **ausente** | — |
| Timeouts / heartbeat de requests | **ausente** | `Channel.ts` |
| Drenaje | **ausente** | `Worker.ts:601-607` |

Antipatrones o riesgos: media por IPC (`DirectTransport`, `Producer.send`); colas de IPC sin tope; descarte total de `#recvBuffer`; callbacks de usuario ejecutados **en el hilo del worker** bajo un mutex en Rust (`common.rs:82-93`), que el propio código esquiva para `close` mandándolo al executor ("to prevent potential deadlock", `consumer.rs:875-881`); documentación de cierre desfasada; posible nullptr en `ChannelSocket.cpp:240-249`.

---

## 12. Callers verificados de las piezas propuestas como referencia

```text
Segmento: Canal salvavidas + propagación de muerte del worker
Responsabilidad principal: atar la vida del worker a la del control y la de los proxies a la del worker; rechazar todo lo pendiente.
Pattern principal: Lifeline channel + Composite close (cascada) + Observer ('died').
Estado: implemented
Confianza: high
Evidencia: Worker.ts:142-175 (spawn, fds), :192-228 (exit→workerDied), :556-589 (workerDied); Channel.ts:199-230 (close rechaza #sents);
  Router.ts:243-275 (workerClosed→transport.routerClosed); UnixStreamSocketHandle.cpp:388-396 (EOF) → ChannelSocket.cpp:368-373,441-447
  → Worker.cpp:408-421 (OnChannelClosed) → Worker.cpp:56-90 (Close borra routers, cierra canal).
Callers verificados: index.ts:117-138 (createWorker crea WorkerImpl y espera @success/@failure); test-Worker.ts:214-310 (mata el PID con SIGINT/SIGTERM/SIGKILL y verifica 'died' antes que observer 'close').
  Rust: worker.rs:432-452 (exit status → dead/close), router.rs:523-536 (router se cierra con worker.on_close).
Contrato de entrada/salida: entrada = exit/error del hijo o EOF/error del pipe; salida = 'died'(Error), 'subprocessclose', 'workerclose' en cada router, WorkerClosedError en cada request pendiente.
Política de error: idempotente (#closed); no reinicia; si falla antes de RUNNING, @failure (código 42 = settings).
Ownership: WorkerImpl es dueño de #child, #channel, #routers; el C++ Worker de mapRouters.
Riesgo: un worker COLGADO (loop bloqueado, proceso vivo) no dispara nada: sin timeout ni heartbeat.
Aplicación posible en Golden: MediaWorker con canal de control cuyo cierre ⇒ el worker cierra sus sesiones (y viceversa: Conversación marca
  todas las sesiones del worker como SessionClosed{reason=worker_died}, con época nueva si se reabren). Añadir lo que falta: heartbeat
  y timeout por comando, y lag del loop como señal de vida.
```

```text
Segmento: Registro de handlers por ID opaco + correlación de respuestas + NotFound suave
Responsabilidad principal: enrutar comandos y eventos a la entidad dueña sin un dispatcher semántico central; rechazar lo obsoleto.
Pattern principal: Registry/Mediator por handlerId + Request/Response con id.
Estado: implemented
Confianza: high
Evidencia: request.fbs (id, method, handler_id required); notification.fbs:81-85; Channel.ts:300-398 (request), :400-444 (processResponse),
  :446-456 (notificación por handlerId, setImmediate); ChannelMessageRegistrator.cpp:53-126; Worker.cpp:349-366, 392-404;
  utils.ts:22-24 (randomUUID); Consumer.ts:210-213, 468-476, 578-581 (removeAllListeners + #closed); channel.rs:462-467 (NotFound → soft).
Callers verificados: RegisterHandler en Router.cpp:25, Consumer.cpp:445, Producer.cpp:214, DirectTransport.cpp:26, WebRtcTransport.cpp:248/328,
  AudioLevelObserver.cpp:47 (+ Unregister en sus destructores); IDs generados en Worker.ts:475,524, Router.ts:389,580,739,851, Transport.ts:537,659.
Contrato de entrada/salida: Request(id,u8 method,handlerId,body) → Response(id,accepted|error+reason); Notification(handlerId,u8 event,body).
Política de error: método/handler desconocido → NotFoundError/Error; duplicado → Error; notificación a handler inexistente → log.
Ownership: el control asigna IDs; el worker posee el mapa id→handler; el proxy, su suscripción.
Riesgo: sin seq ni época explícita: la protección depende de que los IDs no se reutilicen (produce({id}) y keepId lo permiten);
  wrap del id de request sin comprobar colisión.
Aplicación posible en Golden: usar session_id opaco (UUID) que NO se reutiliza como clave de enrutamiento en ambos sentidos; un comando a una
  sesión cerrada = respuesta NotFound "suave" (no excepción en Conversación). Mantener session_epoch/speech_id del contrato de Golden,
  porque Golden SÍ reabre identidades lógicas (re-Speak dentro de la misma sesión) y mediasoup no lo resuelve.
```

```text
Segmento: Analizador nativo por paquete con salida agregada/de flanco (AudioLevelObserver)
Responsabilidad principal: observar cada paquete en el loop de media y notificar al control sólo cambios semánticos a baja frecuencia.
Pattern principal: Observer + agregador temporal (timer) + edge-trigger (silence).
Estado: implemented
Confianza: high
Evidencia: AudioLevelObserver.cpp:20-50 (interval acotado 250–5000 ms, timer), :83-104 (ReceiveRtpPacket acumula), :146-207 (Update
  emite VOLUMES o SILENCE de flanco), :222-227 (OnTimer); Router.ts:1285-1287 (por defecto maxEntries=1, threshold=-80, interval=1000).
Callers verificados: Router.cpp:708-718 (Router::OnTransportProducerRtpPacketReceived → rtpObserver->ReceiveRtpPacket);
  AudioLevelObserver.ts:53-92 (Node: VOLUMES/SILENCE → eventos públicos).
Contrato de entrada/salida: entrada = paquetes RTP (extensión de nivel, sin decodificar); salida = Notification(handlerId=observerId, VOLUMES{[producerId,dBov]}) | SILENCE.
Política de error: paquete sin extensión → ignorado; pausado → reinicia acumuladores y emite SILENCE.
Ownership: el observer pertenece a un router (worker).
Riesgo: el intervalo mínimo de 250 ms no sirve para barge-in; no decodifica (Golden sí necesita PCM para Silero).
Aplicación posible en Golden: misma forma, con otro contenido: VAD + SmartTurn por trama DENTRO del MediaWorker; a Conversación sólo
  SpeechStarted (flanco), TurnCompleted (fin de turno) y SpeechInterrupted. Ninguna trama ni "volumen" periódico.
```

```text
Segmento: Worker como hilo con estado thread_local y despertador asíncrono (modo Rust)
Responsabilidad principal: varios loops de media en un mismo proceso sin compartir estado, con control en otro hilo.
Pattern principal: shared-nothing thread-per-loop + cola MPSC + wakeup (uv_async_send).
Estado: implemented
Confianza: high (estructura) / low (capacidad: no medida)
Evidencia: utils.rs:39-103 (hilo mediasoup-worker-{id} llama mediasoup_worker_run con readFn/writeFn); lib.cpp:62-70; ChannelSocket.cpp:51-88
  (uv_async_init/send), :18-26 (drena todo), :189-283; channel.rs:217-235 (readFn saca de la VecDeque), :391-411 (push + uv_async_send),
  :247-275 (writeFn ejecuta callbacks en el hilo del worker); DepLibUV.cpp:10-11, Settings.cpp:24, Logger.cpp:10-11 (thread_local).
Callers verificados: worker_manager.rs:127-157 (create_worker → Worker::new → run_worker_with_channels).
Contrato de entrada/salida: bytes FlatBuffer sin prefijo; propiedad del buffer transferida con free_fn (channel_read_fn.rs:6-9,62-75).
Política de error: uv_async_send ≠ 0 → ChannelClosed; worker_closed bloquea nuevos envíos salvo WorkerClose (channel.rs:495-502).
Ownership: cada hilo es dueño de su loop y sus sockets; el control sólo toca la cola protegida por mutex.
Riesgo: cola sin tope; drena sin presupuesto; callbacks del usuario en el hilo del worker (un callback lento bloquea la media);
  un crash nativo tumba el proceso entero.
Aplicación posible en Golden (3.14t): N hilos MediaWorker, cada uno con su propio event loop y su conjunto de sesiones; Conversación en otro
  loop; mando por cola acotada + loop.call_soon_threadsafe (equivalente a uv_async_send); los eventos M→C se ENCOLAN, nunca se
  ejecutan como callbacks del lado contrario. ONNX ya es secuencial (GB_ORT_INTRA_OP=1), así que encaja con un hilo por loop.
```

```text
Segmento: DirectTransport / Producer.send() / consumer.on('rtp')
Responsabilidad principal: inyectar/extraer RTP desde el proceso de control.
Pattern principal: media sobre el canal de control.
Estado: not recommended (para Golden)
Confianza: high
Evidencia: Producer.ts:317-336; Producer.cpp:512-540 (memcpy a ProducerSendBuffer por paquete); DirectTransport.cpp:157-192 (Emit CONSUMER_RTP por paquete); Consumer.ts:578-590; consumer.rs:916-919.
Callers verificados: Router.createDirectTransport (Router.ts) → Transport.produce/consume; test-DirectTransport.ts.
Contrato de entrada/salida: 1 notificación por paquete, sin ACK ni backpressure.
Política de error: paquete > ProducerSendBufferSize o inválido → warn y descarte.
Ownership: el control posee el ritmo de envío; el worker, el enrutamiento.
Riesgo: reproduce exactamente el cuello de Golden (coordinador del control en el camino por trama).
Aplicación posible en Golden: ninguna, salvo como contraejemplo: el relay Python→MediaWorker por trama equivale a esto.
```

```text
Segmento: Puerta de reenvío por request (Consumer pause/resume)
Responsabilidad principal: cortar la salida de media de una entidad con un comando de control, aplicado en el loop de media.
Pattern principal: Command + guard de estado (IsActive).
Estado: implemented
Confianza: high
Evidencia: Consumer.cpp:747-770 (CONSUMER_PAUSE idempotente), Consumer.hpp:119-129 (IsActive), Consumer.cpp:1170 (gate en SendRtpPacket).
Callers verificados: Consumer.ts pause()/resume() vía channel.request; Router::OnTransportProducerRtpPacketReceived → Consumer::SendRtpPacket.
Contrato de entrada/salida: request sin body → Accept; efecto desde el siguiente paquete que procese el loop.
Política de error: pausa repetida → Accept sin cambio.
Ownership: el Consumer en el worker.
Riesgo: lo ya enviado no se retira; depende de un viaje de ida y vuelta por el IPC.
Aplicación posible en Golden: CancelSpeech{speech_id} como comando idempotente aplicado por el MediaWorker en su loop; pero el corte por
  barge-in debe ser LOCAL (sin esperar el comando) y CancelSpeech sólo un respaldo de Conversación.
```

---

## 13. Aplicabilidad a GoldenBridge

**Pregunta específica: ¿qué partes permitirían que Python controle Golden sin recibir ni reenviar cada trama?**

1. **El socket de media se abre dentro del worker dueño** y la señalización le dice al cliente a dónde hablar (puerto del worker / candidatos ICE, `Router.cpp:253-258`; `WebRtcServer` del mismo worker, `Worker.cpp:451-466`). En Golden equivale a que el WebSocket de cada llamada termine en el loop del MediaWorker dueño. Con Modal (una función `@modal.web_server(puerto)`, un puerto expuesto según el brief) no se puede anunciar un puerto por worker. La traducción viable es aceptar y hacer el upgrade en un hilo aceptador y **traspasar el socket** al loop del worker antes del primer frame de audio. La alternativa es un puerto interno por worker con un proxy L4 que no parsee frames `[INFERENCIA]`; ambas son `[PENDIENTE]` de PoC con Python 3.14t + uvloop.
2. **Canal de control = requests/notificaciones con `handlerId`**, nunca media (§6). Golden ya lo tiene definido como contrato (eventos M→C y comandos C→M del brief). mediasoup confirma que basta un stream FIFO por worker con respuesta síncrona para el orden por sesión.
3. **Analizadores en media, eventos semánticos al control** (§12, AudioLevelObserver). Es el punto clave: VAD, SmartTurn, Finalize de STT y el pacer de TTS dentro del MediaWorker, y Conversación sólo ve flancos.
4. **Ciclo de vida atado al canal + cascada de cierre** (§12). Si Conversación muere, los MediaWorkers cierran sus llamadas. Si un MediaWorker muere, Conversación cierra sus sesiones sin esperar timeouts.
5. **Modo hilo con estado `thread_local`** (Rust) como plantilla de "W MediaWorkers por tarea Modal" en un solo proceso 3.14t sin GIL. Evita multiproceso (que en Modal exige `web_server`) y duplicar modelos por proceso. **Pero no eleva K_loop**: cada MediaWorker sigue siendo un loop que paga la cadencia de trama de sus llamadas `[INFERENCIA]`.

**Qué NO resuelve:** mediasoup no demuestra que un loop *Python* aguante K llamadas a 50 fps con Silero. Su loop es C++ y reenvía sin decodificar. La rodilla medida de Golden (16-25) es de coordinación en Python, no de CPU (brief). Por tanto, para Golden **K_task ≈ W × K_loop_python − desbalance**, y la ganancia viene de W (paralelismo real entre núcleos en 3.14t), no de que el loop rinda más. Si hiciera falta subir K_loop, la vía sería sacar la cadencia de trama del loop asyncio (DSP en un hilo dedicado por worker que consume un ring buffer, o código nativo), y eso mediasoup no lo cubre.

---

## 14. Riesgos de adopción o copia

- **Adoptar mediasoup como componente:** exigiría que ai-engine hable RTP (PlainTransport) en lugar de WS µ-law. No aporta VAD, STT, TTS ni decodificación, y añadiría Node o Rust al stack. **Descartar** `[INFERENCIA]`.
- **Copiar código:** ISC es permisivo (basta con conservar el aviso). El riesgo legal es bajo. El riesgo técnico está en copiar también los huecos: colas sin tope, falta de timeouts, descarte total del buffer de recepción, ningún drenaje.
- **Copiar el modo hilo:** en Python 3.14t los callbacks entre hilos son peligrosos (mediasoup Rust ya tuvo que mandar `close` al executor para evitar deadlocks, `consumer.rs:875-881`). Regla para Golden: comunicación sólo por colas acotadas, sin callbacks cruzados.
- **Dominio de fallo:** en modo hilo, un crash nativo (p. ej. dentro de ORT) se lleva todas las llamadas de la tarea. En modo proceso se pierde sólo un worker, pero se duplica la memoria y en Modal aparece la restricción de `web_server` (brief).
- **Observabilidad insuficiente para atribuir la rodilla:** mediasoup expone `getResourceUsage` (rusage del worker, `Worker.cpp:130-190`), `dump` (IDs y handlers, `Worker.cpp:92-128`), `getStats` y trace events por entidad, `receivedAtUs` por notificación (`ChannelSocket.cpp:215-217`) y el log tag `message`. **No** expone lag del loop, profundidad de colas del canal ni latencia de request `[CÓDIGO]`. Golden tiene que medir lag por MediaWorker, profundidad de inbox por sesión y tiempo submit→resume por worker.

---

## 15. Lecciones concretas que pasan a la matriz comparativa

| Dimensión | mediasoup |
|---|---|
| Media fuera del runtime de control | **Sí, total** (0 cruces por paquete), salvo `DirectTransport` opt-in (1 cruce/paquete) `[CÓDIGO]` |
| Unidad de afinidad | Router (y sus transports) → worker, de por vida; cross-worker sólo con `pipeToRouter` (UDP) `[CÓDIGO]` |
| Paralelismo entre núcleos | Réplica de workers de un hilo y un loop, sin estado compartido; no paraleliza dentro de un loop `[CÓDIGO]` |
| Workers por proceso/tarea | Node: 1 worker = 1 proceso hijo; Rust: N workers = N hilos en 1 proceso; N lo decide la app `[CÓDIGO]` |
| Audio a través de IPC | No (salvo DirectTransport) `[CÓDIGO]` |
| Orden por sesión | FIFO por canal + respuesta síncrona + `setImmediate` para notificaciones en Node `[CÓDIGO]` |
| Protección frente a obsoletos | UUID no reutilizado + `removeAllListeners` + `#closed` + `NotFoundError` (suave en Rust); sin `seq`/época `[CÓDIGO]` |
| Backpressure | Ninguno en IPC (colas sin tope); watermarks como eventos sólo en SCTP `[CÓDIGO]` |
| Barge-in y purga | No aplica; `consumer.pause()` mediado por control; lo enviado no se retira `[CÓDIGO]` |
| Supervisión/reinicio | Detección de muerte por exit/EOF y cascada; **sin reinicio, sin timeout, sin heartbeat** `[CÓDIGO]` |
| Drenaje | Ninguno (SIGTERM ⇒ cierre inmediato) `[CÓDIGO]` |
| Dominio de fallo | Node: el proceso worker; Rust: el proceso entero ante un crash nativo `[CÓDIGO]`/`[INFERENCIA]` |
| Memoria duplicada | Sin modelos; cada proceso/hilo carga sus librerías y estado `thread_local` `[INFERENCIA]` |
| Observabilidad | rusage, dump, getStats, trace, `receivedAtUs`; sin lag del loop ni profundidad de colas `[CÓDIGO]` |
| Complejidad de integración | Alta como componente (RTP/ICE/DTLS, Node o Rust); baja-media como patrón `[INFERENCIA]` |
| Impacto sobre Asterisk/ai-engine | Como componente: ai-engine pasaría a RTP; como patrón: ninguno `[INFERENCIA]` |
| Licencia | ISC (deps BSD/Apache/OpenSSL) `[CÓDIGO]` |
| Valor para Golden | **Adaptar** patrones (salvavidas, registro por ID, analizador→evento, ingreso al dueño, worker-hilo shared-nothing); **descartar** como dependencia |

**Respuesta a la pregunta central:** mediasoup **elimina** el coordinador de control del camino por paquete (0 cruces). Pero dentro de cada worker hay **un loop único** que coordina todos los paquetes de todos sus routers, y el uso de varios núcleos sale de **replicar** esos loops sin estado compartido, no de paralelizar uno. Lo replicado es sólo el plano de media bajo un control común, no la aplicación entera.

---

## Anexo — respuestas explícitas a las 18 preguntas

1. **Unidad de ownership:** el *worker* es dueño de sus *routers*; el router de sus *transports*; el transport de sus *producers/consumers* (`Worker.cpp:313-329`, `ChannelMessageRegistrator.cpp:53-84`) `[CÓDIGO]`. La afinidad práctica es router→worker.
2. **Quién recibe cada paquete:** el `UdpSocketHandle`/`TcpConnectionHandle` del worker dueño, en su loop libuv; lo demultiplexa el transport o el `WebRtcServer` (tupla o ufrag ICE) (`WebRtcServer.cpp:416-494`) `[CÓDIGO]`.
3. **Cruces del runtime de control por paquete:** 0 en transports normales; 1 por paquete en DirectTransport (`Producer.ts:317-336`, `DirectTransport.cpp:157-192`) `[CÓDIGO]`.
4. **¿Loop único compartido?** Sí, **uno por worker**, compartido por todos sus routers. Hace recepción, enrutamiento, RTCP/BWE, timers, observers y además el canal de control (`Worker.cpp:14-44`) `[CÓDIGO]`. En Node también hay **un loop de control** para todos los workers, fuera del camino de media `[CÓDIGO]`.
5. **Varios núcleos:** procesos nativos (Node) o hilos nativos `thread_local` (Rust), uno por worker. Sin hilos internos `[CÓDIGO]`.
6. **Asignación y afinidad:** la app elige el worker en `createRouter`. La afinidad es permanente porque el socket vive en el worker y el cliente recibe su puerto por señalización (`Router.cpp:253-258`). Cross-worker con `pipeToRouter` (`Router.ts:960-1110`) `[CÓDIGO]`.
7. **Estado compartido/local:** local por worker (`thread_local`); compartido sólo la inicialización de librerías con mutex/once (`DepLibSRTP.cpp:11`, `DepOpenSSL.cpp:12`) `[CÓDIGO]`. El control guarda proxies e IDs.
8. **Protocolo:** IPC por pipes (Node, fd 3/4) o llamadas de función + `uv_async_send` (Rust), con FlatBuffers con prefijo de longitud (§6) `[CÓDIGO]`.
9. **Orden/correlación/ACK/dedup/tardíos:** FIFO por canal; `id` uint32 por request; `accepted|error` como ACK; sin dedup; tardíos neutralizados por UUID único + `removeAllListeners` + `#closed` + `NotFoundError` (§6) `[CÓDIGO]`.
10. **Backpressure sin afectar a otros:** entre workers, aislamiento total por diseño. Dentro de un worker, ninguno. En IPC, colas sin tope. Sólo SCTP tiene watermarks por recurso (§9) `[CÓDIGO]`.
11. **Barge-in y purga:** no existe como concepto. Lo más cercano es `consumer.pause()` aplicado en el loop del worker (`Consumer.cpp:747-770,1170`). No hay purga porque no hay cola de reproducción `[CÓDIGO]`.
12. **Audio no retirable:** todo lo ya entregado a `uv_udp_try_send`/`uv_udp_send` (`UdpSocketHandle.cpp:152-190`) y lo que está en vuelo o en el jitter buffer del receptor `[CÓDIGO]`/`[INFERENCIA]`.
13. **Si muere Media / Control / canal:** Media → `died` + cascada + rechazo de pendientes. Control → EOF ⇒ el worker se cierra. Canal → error de lectura/escritura ⇒ `Close()` en el worker. Worker colgado → **nada lo detecta** (§9) `[CÓDIGO]`.
14. **Drenaje:** no existe. SIGINT/SIGTERM ⇒ cierre inmediato (`Worker.ts:601-607`, `Worker.cpp:423-449`) `[CÓDIGO]`.
15. **¿Duplica modelos/recursos?** No carga modelos. Cada worker duplica librerías (proceso) o estado `thread_local` (hilo), y cada uno necesita su propio rango de puertos `[CÓDIGO]`/`[INFERENCIA]`.
16. **Métricas para la rodilla:** `getResourceUsage` (rusage), `dump`, `getStats`/trace por entidad, `receivedAtUs`. **No** expone lag del loop, profundidad de colas ni latencia de request. Insuficiente para atribuir una rodilla `[CÓDIGO]`.
17. **Reutilizable legal y técnicamente:** ISC permite copiar con aviso. Técnicamente vale la pena llevarse los patrones de §12 (salvavidas, registro por ID, analizador→evento, worker-hilo shared-nothing, watermarks como eventos), no el código de RTP `[INFERENCIA]`.
18. **¿Exige reemplazar Asterisk/ai-engine/Modal/proveedores?** Como componente, sí: ai-engine tendría que pasar de WS µ-law a RTP, y seguiría haciendo falta todo el pipeline VAD/STT/TTS. Como patrón, no reemplaza nada `[INFERENCIA]`.
