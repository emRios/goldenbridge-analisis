# 04 — LiveKit server (`livekit/livekit`)

Auditoría comparativa contra GoldenBridge (rama `api` @ `08932a6`, ver `../BRIEF_GOLDEN.md`). Sólo lectura: no se compiló, no se ejecutaron tests ni benchmarks, no se modificó el clon.

Convención de citas: `ruta:Símbolo:líneas` relativas a `repos/livekit-server/`.

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/livekit/livekit.git` |
| Nombre local | `repo_analisis/repos/livekit-server` |
| Branch | `master` (clon superficial, `git rev-list --count HEAD` = 1) |
| Commit | `5a897c239cde995e615047be3fd5b27ca1afdad7` |
| Fecha (`git log -1 --format=%cI`) | `2026-10-09T13:37:04+05:30` |
| Mensaje | `fix(cli): return non-zero exit status on command errors (#4888)` |
| Versión declarada | `version/version.go:Version = "1.13.9"`; `CHANGELOG.md` registra 1.13.9 (2026-10-07) y 1.13.8 (2026-10-06) |
| Licencia | Apache-2.0 (`LICENSE`, `NOTICE`). `pkg/sfu/NOTICE`: partes del paquete vienen de ion-sfu (MIT) |
| Submódulos | ninguno (no hay `.gitmodules`) |
| Lenguaje / toolchain | Go, `go.mod: go 1.26.0` |
| Mantenimiento visible | Activo: dos releases en tres días y un commit del día de la auditoría `[DOCUMENTACIÓN]` (CHANGELOG) |

**Dependencias donde vive parte del camino caliente (fuera del clon, no verificables aquí):**

- `github.com/pion/webrtc/v4`, sustituida por `github.com/livekit/webrtc-pion/v4 v4.2.18-warp.6` (`go.mod` `replace`): DTLS/SRTP, `TrackRemote`, `TrackLocalWriter.WriteRTP`, goroutine lectora del transporte.
- `github.com/pion/ice/v4`, sustituida por `livekit/ice/v4 …-warp.3`: UDP mux, es decir, el socket compartido y su lector.
- `github.com/livekit/mediatransportutil`: `rtcconfig` (construcción del UDP mux y del *batch IO*), `bucket` (ring de retransmisión), `nack`, `twcc`.
- `github.com/livekit/protocol`: `utils.OpsQueue`/`TypedOpsQueue`, `utils.ParallelExec`, `webhook`, `rpc` (temas psrpc).
- `github.com/livekit/psrpc`: el RPC/stream sobre bus (Redis o local).

Todo lo que ocurre dentro de estas dependencias va marcado como `[PENDIENTE]` o `[INFERENCIA]`.

---

## 2. Dictamen ejecutivo

1. **No existe coordinador global del camino por paquete.** Cada pista publicada, y cada capa en vídeo, tiene **su propia goroutine `forwardRTP`**. Esa goroutine bloquea en el `sync.Cond` de su `Buffer`, saca el paquete y lo escribe ella misma en cada `DownTrack` suscrito. No pasa por Room, RoomManager ni un loop compartido `[CÓDIGO]` `pkg/sfu/receiver_base.go:startForwarderForBufferLocked:864-875`, `forwardRTP:877-1019`. Las goroutines son independientes y el scheduler de Go las reparte entre `GOMAXPROCS` hilos. El resultado es **paralelismo real entre núcleos dentro de un solo proceso**.
2. **La afinidad se resuelve antes de que fluya la media.** `SelectRoomNode` fija `room → node` en Redis (`room_node_map`). La señalización puede terminar en cualquier nodo y se reenvía por psrpc al nodo dueño. El PeerConnection, y con él todo el RTP, se crea **en el nodo dueño**. Los paquetes nunca atraviesan el nodo de señalización ni el bus `[CÓDIGO]` `pkg/service/roomallocator.go:SelectRoomNode:136-175`, `pkg/routing/redisrouter.go:GetNodeForRoom/SetNodeForRoom:100-114`, `pkg/service/roommanager.go:StartSession:287-645`.
3. **El control va por identidad, no por un coordinador central.** Cada sala y cada participante registran su propio tema psrpc (`FormatRoomTopic`, `FormatParticipantTopic`). Una orden como `MutePublishedTrack` llega directamente al nodo y al objeto dueño `[CÓDIGO]` `pkg/service/roommanager.go:getOrCreateRoom:683-698`, `StartSession:571-581`, `pkg/service/roomservice.go:MutePublishedTrack:247-255`.
4. **El trabajo por paquete es trivial en el SFU.** El SFU reenvía RTP cifrado-descifrado y reescribe cabeceras, pero **no decodifica audio ni hace VAD**. La única métrica derivada del audio es el *audio level* de la extensión RTP, agregado por la sala cada 400 ms por **sondeo** `[CÓDIGO]` `pkg/rtc/room.go:audioUpdateWorker:1641-1678`, `pkg/sfu/audio/audiolevel.go:44-47`. Por eso **el modelo no es transferible tal cual a Golden**: el coste de Golden por trama son unos 400 µs de Silero, mientras que en LiveKit es de microsegundos según comentarios del propio código (sin benchmark reproducible de capacidad). En el ecosistema LiveKit, la parte "Golden" (VAD/STT/LLM/TTS) vive en `livekit-agents`, en otro proceso, que recibe audio por WebRTC como un suscriptor más.
5. **Para Golden: estudiar y adaptar patrones, no adoptar el componente.** Hay cuatro patrones directamente útiles:
   - dueño único por pista con *run-to-completion* y una sola transferencia por paquete;
   - generación atómica más reinicio en dos fases para matar trabajadores obsoletos;
   - flujo de señalización con `seq` y recorte de duplicados;
   - agregación de baja frecuencia por sondeo en vez de un evento por paquete.

   Adoptar LiveKit server exigiría sustituir AudioSocket/WS por WebRTC o SIP (vía `livekit-sip`), lo que impacta a Asterisk y al ai-engine.

---

## 3. Diagrama del flujo relevante

```text
                        ┌──────────── cualquier nodo (signal) ────────────┐
 cliente WS  ──────────▶│ RTCService.serve ─ SelectRoomNode ─ Redis HSET  │
 (señal)                │   room_node_map[room]=nodeX                     │
                        │ signalClient.StartParticipantSignal ──psrpc──┐  │
                        └──────────────────────────────────────────────┼──┘
                                                                       │ RelaySignal stream (seq)
                        ┌───────────────── nodo X (RTC, dueño de la sala) ───────────────────────┐
                        │ SignalServer.RelaySignal → RoomManager.StartSession                    │
                        │   rtcSessionWorker (1 goroutine/participante: SOLO señal, sin RTP)     │
                        │                                                                         │
 RTP/SRTP UDP ─────────▶│ [pion/ice UDP mux: lector por socket]  (fuera del clon)                 │
 (directo al nodo X)    │        │ demux por ufrag/5-tupla → transporte del participante          │
                        │        ▼                                                                │
                        │ [pion srtp] descifra → Buffer.Write(pkt)  (SettingEngine.BufferFactory) │
                        │        │  lock por SSRC; bucket (ring) + extPackets.PushBack            │
                        │        │  readCond.Broadcast()                                          │
                        │        ▼                                                                │
                        │ goroutine forwardRTP  ◀── 1 por pista×capa, dueña del paquete           │
                        │   ReadExtended() ─ BroadcastRTP(downTrackSpreader)                      │
                        │        │  < umbral (20 / 300): bucle inline                              │
                        │        │  ≥ umbral: min(NumCPU, N) goroutines efímeras, pasos de 2      │
                        │        ▼                                                                │
                        │ DownTrack.WriteRTP (por suscriptor; lock del Forwarder propio)           │
                        │   GetTranslationParams → muted? drop                                    │
                        │   munger seq/ts → pacer.Enqueue                                         │
                        │        │  PassThrough (default): SendPacket inline                      │
                        │        │  NoQueue (send-side BWE): deque + 1 goroutine/transporte        │
                        │        ▼                                                                │
                        │ pion TrackLocalWriter.WriteRTP → SRTP → socket (o BatchConn)            │
                        │                                                                         │
                        │ Room: audioUpdateWorker (sondeo 400 ms), connectionQualityWorker,       │
                        │       changeUpdateWorker  ── NUNCA tocan paquetes                       │
                        └─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Mapa de procesos, threads, loops y colas

| unidad | cuántas | qué hace | cola | ¿acotada? | evidencia |
|---|---|---|---|---|---|
| Proceso `livekit-server` | 1 por nodo | todo: señal, SFU, TURN opcional | — | — | `cmd/server/main.go:300-320` |
| Lector UDP mux | 1 por socket (pion/ice) | demux de paquetes al transporte | interna de pion | `[PENDIENTE]` | `config-sample.yaml:82-86` recomienda "un rango de puertos ≥ vCPUs" `[DOCUMENTACIÓN]` |
| Lector SRTP del transporte | `[PENDIENTE]` (pion) | descifra y llama a `Buffer.Write` | — | — | `pkg/rtc/config.go:SetBufferFactory:102-105` |
| `Buffer` (por SSRC) | 1 por stream entrante | parseo, stats, NACK, audio level, encolado | `extPackets deque` | **No**: sólo avisa con `"too much ext packets"` | `pkg/sfu/buffer/buffer_base.go:HandleIncomingPacketLocked:927-936` |
| `bucket` (retransmisión) | 1 por Buffer | ring para NACK/RTX | ring | **Sí**: crece hasta `MaxAudioPkts=200` / `MaxVideoPkts=500` | `buffer_base.go:maybeGrowBucket:1256-1300`; `pkg/config/config.go:577-579` |
| Pendientes antes de `Bind` | por Buffer | paquetes sin bind | `pPackets` | **Sí**: descarta los más viejos | `pkg/sfu/buffer/buffer.go:Write:205-232` |
| goroutine `forwardRTP` | 1 por pista×capa | camino caliente completo | — | — | `receiver_base.go:874`, `877-1019` |
| Goroutines de `BroadcastRTP` | `min(NumCPU, N)` efímeras por paquete, sólo si N ≥ umbral | fan-out paralelo | — | — | `pkg/sfu/utils/downtrackspreader.go:BroadcastRTP:200-223` |
| Pacer `PassThrough` | por transporte suscriptor (default) | envía inline | ninguna | n/a | `pkg/rtc/transport.go:609-613`; `pacer/pass_through.go:30-32` |
| Pacer `NoQueue` | por transporte, si `UseSendSideBWE` | 1 goroutine `sendWorker` | `deque` | **No** | `pkg/sfu/pacer/no_queue.go:Enqueue:52-61` |
| `StreamAllocator` | 1 por transporte suscriptor | asignación de capas/BWE por eventos | `TypedOpsQueue MinSize 64` | `[PENDIENTE]` (vive en `livekit/protocol`) | `pkg/sfu/streamallocator/streamallocator.go:268-272, 643-675` |
| `rtcSessionWorker` | 1 por participante | lee mensajes de señal y llama a `HandleSignalMessage` | `MessageChannel` 200 | **Sí**: escritura no bloqueante, `ErrChannelFull` | `pkg/service/roommanager.go:761-809`; `pkg/routing/messagechannel.go:24, 56-71` |
| `signalMessageSink` | 1 por stream de señal | envío con reintentos | `queue []proto.Message` | **No** (`append`) | `pkg/routing/signal.go:WriteMessage:365-381` |
| Workers de Room | 4 por sala | audio level, calidad, cambios, simulación | — | — | `pkg/rtc/room.go:312-315` |
| `pubRTCPQueue` | 1 por participante | RTCP del publicador | `TypedOpsQueue MinSize 64` | `[PENDIENTE]` | `pkg/rtc/participant.go:383-387` |
| Telemetría / webhooks | **1 global por nodo** | eventos de baja frecuencia | `OpsQueue MinSize 2048, FlushOnStop` | `[PENDIENTE]` | `pkg/telemetry/telemetryservice.go:187-201` |
| `ForwardStats` | 1 global | latencia de reenvío por paquete | 16 *shards* × 8192, lock-free | **Sí**: sobrescribe y cuenta `dropped` | `pkg/sfu/forwardstats.go:54-97, 261-265` |
| `RedisRouter.statsWorker` / `keepaliveWorker` | 1 + 1 | ping por bus, stats de nodo, vigilancia de bloqueo | — | — | `pkg/routing/redisrouter.go:202-252` |

**Única cola global en el camino de cualquier paquete:** ninguna en este clon. La única estructura de nodo que toca cada paquete es el anillo *sharded* lock-free de `ForwardStats`, que es telemetría. El lector del UDP mux sí ve todos los paquetes de su socket, pero vive en pion. La propia documentación de LiveKit lo trata como punto de serialización: pide usar "un rango de puertos ≥ vCPUs" `[DOCUMENTACIÓN]` `config-sample.yaml:82-86`, `[INFERENCIA]`.

---

## 5. Ownership por sesión y recursos compartidos

- **Unidad de afinidad: la sala** (`room_node_map`). **Unidad de ownership del camino de media: la pista publicada** (`ReceiverBase` + su goroutine `forwardRTP`), y para cada suscriptor el `DownTrack`, con su `Forwarder` y su `RTPMunger` `[CÓDIGO]`.
- **Estado por pista (local):**
  - `ReceiverBase.buffers`, `downTrackSpreader`, `forwardersGeneration` (`receiver_base.go:119-161`);
  - `BufferBase` con su `RWMutex` y `readCond` (`buffer_base.go:160-232`);
  - `Forwarder.lock`, `muted`/`pubMuted` (`forwarder.go:536-600`).
- **Estado por participante:** `ParticipantImpl` (~4.600 líneas), con `TransportManager`, `UpTrackManager`, `SubscriptionManager` y la cola RTCP.
- **Estado por sala:** `Room.lock` protege mapas de participantes, `participantRequestSources` y despachos de agentes (`room.go:103-135`). **No se toma por paquete.**
- **Estado de nodo compartido:**
  - `RoomManager.rooms` con `lock` (`roommanager.go:72-108`): sólo en alta y baja;
  - `Factory` de buffers por sala (`room.go:279`, `411`; `buffer/factory.go:55-101`);
  - `ForwardStats`;
  - la cola de telemetría.
- **Estado de flota compartido (Redis):** `nodes` (hash `node_id → Node proto`), `room_node_map` y el `roomStore` (sala y participantes) `[CÓDIGO]` `redisrouter.go:35-41`. Redis es fuente de verdad **de ubicación**, no del estado de media.
- **Vida útil de la sala:** contador `Hold()/Release()` en `getOrCreateRoom` para evitar carreras de creación y cierre (`roommanager.go:647-670`). El cierre por `EmptyTimeout`/`DepartureTimeout` lo hace `CloseIfEmpty`, sondeado cada 1 s por `backgroundWorker` (`server.go:467-481`; `room.go:795-799`).

---

## 6. Contrato de control/IPC

| canal | transporte | qué cruza | orden / dedup | evidencia |
|---|---|---|---|---|
| Cliente ⇄ nodo de señal | WebSocket (protobuf `SignalRequest/Response`) | SDP, ICE, mute, suscripciones, ping | TCP | `pkg/service/rtcservice.go:serve:307-717` |
| Nodo de señal ⇄ nodo RTC | psrpc stream `RelaySignal` sobre bus (Redis pub/sub o local) | los mismos mensajes, en lotes con `seq` | **`seq` monótono por stream**: el receptor recorta el prefijo ya visto y cierra el stream si detecta un hueco (`ErrSignalMessageDropped`) | `pkg/routing/signal.go:signalMessageReader.Read:233-252`, `signalMessageSink.write:310-363` |
| API de servidor → objeto dueño | psrpc unario por tema `room` / `participant` | `RemoveParticipant`, `MutePublishedTrack`, `UpdateSubscriptions`, `SendData`… | por tema; `MultitonService.Replace` sustituye el servidor del tema al reconectar | `roommanager.go:571-581, 683-698`; `roomservice.go:224-255` |
| Nodo → flota | Redis `HSET nodes`, keepalive por bus | `NodeStats` y `NodeState` (SERVING/SHUTTING_DOWN) | último valor gana | `redisrouter.go:69-98, 185-190, 229-252` |
| Nodo → externo | webhooks HTTP (`webhook.QueuedNotifier`) | `room_started`, `participant_joined`… | cola de telemetría | `pkg/telemetry/events.go:32-43` |
| Nodo → agentes | psrpc `JobRequest` por tipo de job | asignación de job; **el audio va aparte, por WebRTC** | — | `pkg/agent/client.go:LaunchJob:132-157` |

**Semántica de entrega de señal** `[CÓDIGO]` `signal.go:310-363`:
- El emisor reintenta con backoff exponencial (`MinRetryInterval` → `MaxRetryInterval`) hasta `RetryTimeout`.
- Al expirar, **descarta la cola y avanza `seq`** (`s.seq += len(queue); queue = nil`). Es *at-most-once* con detección de pérdida en el receptor: el siguiente lote trae un `seq` mayor que el esperado y el receptor corta el stream.
- No hay ACK de aplicación ni exactly-once.

**Protección frente a fuentes obsoletas (equivalente a `session_epoch`)** `[CÓDIGO]`:
- `Room.ReplaceParticipantRequestSource` cierra la fuente anterior e instala la nueva (`room.go:535-542`).
- Cuando una fuente se cierra, `rtcSessionWorker` sólo llama a `HandleSignalSourceClose` **si sigue siendo la fuente vigente**: `room.GetParticipantRequestSource(identity) == requestSource` (`roommanager.go:791`).
- Una conexión vieja que muere después de un *resume* no derriba al participante.

---

## 7. Ruta de audio y frecuencia de cruces

Cadena de callers verificada `[CÓDIGO]`:
`transport.go:655 pc.OnTrack` → `participant.go:2034 PublisherTransportHandler.OnTrack` → `participant.go:2370 onMediaTrack` → `participant.go:3458 mt.AddReceiver` → `mediatrack.go:535 AddUpTrack` → `receiver.go:171-201 AddUpTrack` → `receiver_base.go:760-764 StartBuffer` → `864-875 startForwarderForBufferLocked` → `go forwardRTP`.

Por paquete de audio de un publicador:

1. **[pion]** lector del socket → demux → descifrado SRTP `[PENDIENTE]`.
2. `Buffer.Write` (`buffer.go:159-249`), en la goroutine de pion:
   - `Unmarshal`;
   - lock del Buffer;
   - TWCC;
   - `calc` → `HandleIncomingPacketLocked`: stats, NACK, audio level (`buffer_base.go:972-983`), `bucket.AddPacketWithSequenceNumber`;
   - `extPackets.PushBack` y `readCond.Broadcast()`.
3. **Única transferencia entre goroutines:** `forwardRTP` despierta en `ReadExtended` (`buffer_base.go:630-658`).
4. `BroadcastRTP` → por cada suscriptor `DownTrack.WriteRTP` (`downtrack.go:1025-1200`): traducción bajo `Forwarder.lock`, copia de payload a un pool, reescritura de cabecera, `sequencer.push`, `rtpStats.Update`, `pacer.Enqueue`.
5. Con el pacer por defecto `PassThrough` (`transport.go:609-613`; `config.go:587-589` `UseSendSideBWE: false`), `Enqueue` llama a `SendPacket` → `WriteStream.WriteRTP` **inline** (`pacer/base.go:44-80`) → SRTP → socket. Con *batch IO*, el syscall pasa a un flush goroutine de `BatchConn` `[DOCUMENTACIÓN]`: comentario en `mediatrack.go:277-280`. La implementación está en `mediatransportutil`, `[PENDIENTE]`.

**Cruces del runtime de control por paquete: 0.** Ni `rtcSessionWorker`, ni `Room`, ni `RoomManager`, ni psrpc ven RTP `[CÓDIGO]` + `[INFERENCIA]` (no existe ruta de código que lo haga). El audio cruza **una** frontera de goroutine (pion → `forwardRTP`) y ningún IPC.

**Frecuencia:** el SFU no impone cadencia. Reenvía cada paquete cuando llega, normalmente 20 ms en Opus, es decir 50 pps por pista, según el cliente. El trabajo por paquete y suscriptor se describe sólo en comentarios: "~3.3us per downtrack, against ~26us when the send runs inline" (`mediatrack.go:277-280`) y "WriteRTP takes about 50µs on average" (`downtrackspreader.go:27-29`). Ambos comentarios son `[DOCUMENTACIÓN]`, no `[MEDICIÓN]`, y no coinciden entre sí.

---

## 8. Secuencia de barge-in

LiveKit server **no implementa barge-in**: no conoce turnos ni habla del agente. El equivalente más cercano es **silenciar o desuscribir una pista**:

1. Una orden (`MutePublishedTrack` por psrpc o `MuteTrackRequest` del cliente) → `participant.SetTrackMuted` → `MediaTrackReceiver.SetMuted` actualiza `TrackInfo` y propaga a los receivers (`mediatrackreceiver.go:500-508`). Cada `DownTrack.PubMute`/`Mute` cambia el flag en su `Forwarder` (`downtrack.go:1348-1361`; `forwarder.go:536-600`).
2. **Efecto local e inmediato:** en el siguiente paquete, `GetTranslationParams` devuelve `shouldDrop` si `muted || pubMuted` (`forwarder.go:1789-1797`). El corte ocurre en la misma goroutine dueña, sin coordinador.
3. Al silenciar, `resyncLocked()` prepara el munger para que `seq`/`ts` no salten al reanudar (`forwarder.go:569-573, 597-600`).
4. **Purga:** el pacer **no tiene `Clear`/`Flush`**. Lo ya encolado en `NoQueue` se envía igualmente. Con `PassThrough` no hay cola en el SFU (`grep` en `pkg/sfu/pacer/`: sin purga) `[CÓDIGO]`. El "flush" de `DownTrack.CloseWithFlush` es lo contrario de una purga: **envía** frames en blanco para vaciar el decodificador remoto (`downtrack.go:1426-1451`).

**Audio que ya no se puede retirar:** lo escrito al socket, lo que esté en el `deque` de `NoQueue` o en `BatchConn`, y el *jitter buffer* del receptor `[CÓDIGO]` + `[INFERENCIA]`.

Para Golden esto confirma el brief: la purga real tiene que ocurrir **antes del pacer** y en el dueño de la reproducción. LiveKit aporta el patrón de flag por suscriptor comprobado por paquete, más resincronización de numeración, pero no una purga.

---

## 9. Backpressure, fallos, reconexión y drenaje

**Backpressure por llamada:**
- **Entrada:** `extPackets` no está acotado, sólo avisa (`buffer_base.go:932-936`). Si `forwardRTP` se atrasa, crece la memoria **de esa pista**, pero no bloquea a otras pistas: cada una tiene su goroutine. El `bucket` sí está acotado y descarta lo demasiado viejo (`ErrPacketTooOld`, `889-911`).
- **Salida:** con `PassThrough`, un `WriteRTP` lento frena la goroutine `forwardRTP` de ese publicador, es decir, a todos sus suscriptores. Por eso existe el fan-out paralelo por umbral (`downtrackspreader.go:200-223`; umbral 20, o 300 con *batch IO*: `mediatrack.go:276-286`). Con `NoQueue`, la cola por transporte suscriptor no está acotada (`no_queue.go:52-61`).
- **Control de congestión** por suscriptor: `StreamAllocator` (pausa de capas de vídeo según BWE) en una cola de eventos propia por transporte (`streamallocator.go:643-675`). En audio no aplica la selección de capas.
- **Señal:** `MessageChannel` de 200 elementos, no bloqueante. Si se llena, `ErrChannelFull` hace que `CopySignalStreamToMessageChannel` devuelva error y se cierre **ese** stream (`messagechannel.go:56-71`; `signal.go:191-226`). El fallo queda confinado al participante.

**Fallos:**
- **Pánico en el camino de señal:** `rtc.Recover` registra el pánico y ejecuta **`os.Exit(1)`** (`roommanager.go:766-772`; `rtcservice.go:603-608`).
- **Pánico en `forwardRTP`:** no tiene `recover` y aborta el proceso (comportamiento estándar de Go) `[CÓDIGO]` + `[INFERENCIA]`.
- **Dominio de fallo = proceso del nodo**, con todas sus salas.
- **Nodo muerto:** deja de actualizar `Stats.UpdatedAt`. Pasados `AvailableSeconds = 5`, deja de ser elegible (`selector/utils.go:30-47`). Si una sala apunta a un nodo no disponible, `SelectRoomNode` elige otro y reescribe `room_node_map` (`roomallocator.go:143-172`). Los clientes hacen *full reconnect*; no hay migración de estado de media `[INFERENCIA]`.
- **Vigilancia de bloqueo:** si las stats de nodo se retrasan más de `StatsMaxDelay`, `statsWorker` vuelca todas las goroutines con `"status update delayed, possible deadlock"` (`redisrouter.go:210-221`). `/health` devuelve 406 si las stats tienen más de 4 s (`server.go:451-465`).

**Reconexión:**
- Si el WS cae pero el PeerConnection sigue vivo, `StartSession` con `pi.Reconnect` reutiliza el participante, cambia la fuente y el sumidero de señal (`ResumeParticipant`, `room.go:550-582`) y relanza `rtcSessionWorker` (`roommanager.go:330-395`).
- Si el participante ya está cerrado, envía `LeaveRequest{Action: RECONNECT}` (`roommanager.go:331-358`).
- **El reinicio de un receiver usa generación:** incrementa `forwardersGeneration`, marca los buffers, **espera a que terminen las goroutines viejas (`WaitGroup`)**, reinicia los buffers, avisa a los downtracks, abre una nueva generación y relanza los forwarders (`receiver_base.go:restartInternal:310-412`). El bucle de `forwardRTP` sale en cuanto `forwardersGeneration` cambia (`:928`).

**Drenaje (shutdown):**
- `SIGTERM`/`SIGINT`/`SIGQUIT`: la primera señal llama a `Stop(false)` y la segunda a `Stop(true)` (`cmd/server/main.go:309-319`).
- `Stop` llama a `router.Drain()` (estado `SHUTTING_DOWN` publicado en Redis: `redisrouter.go:185-190`) y luego **espera sin límite de tiempo**, sondeando cada 5 s, hasta `!HasParticipants()` (`server.go:375-399`). Después para el router, cierra el HTTP con 5 s de gracia y ejecuta `roomManager.Stop()`, que cierra todas las salas con `RoomCloseReasonServerShutdown` (`roommanager.go:242-274`).
- **Hallazgo:** `GetAvailableNodes` excluye a los nodos que no están en `SERVING` (`selector/utils.go:43-47`). Sin embargo, **`SelectRoomNode` mantiene una sala existente en su nodo si `IsAvailable(existing)`, y esa función sólo mira la frescura de las stats, no `State`** (`roomallocator.go:143-151`; `utils.go:33-41`). Un nodo en drenaje **sigue aceptando participantes nuevos en salas que ya hospeda** `[CÓDIGO]`. Para salas 1:1 tipo llamada el efecto es nulo; para salas largas puede alargar el drenaje sin límite `[INFERENCIA]`.
- `RTCService.DrainConnections` (cierre escalonado con jitter, `rtcservice.go:719-741`) **no tiene caller fuera de tests en este clon** `[CÓDIGO]`: es latente.

---

## 10. Escalado y uso real de núcleos

- **Dentro del nodo:** goroutines independientes por pista (forwarder), por transporte (allocator y pacer opcional), por participante (señal) y por sala (4 workers). El scheduler M:N de Go las reparte entre `GOMAXPROCS` hilos; no hay ajuste explícito de `GOMAXPROCS` en `cmd/` ni `pkg/` (`grep`). El fan-out grande se paraleliza con `min(NumCPU, N)` goroutines efímeras (`downtrackspreader.go:213-220`). **Es paralelismo real**, no réplica.
- **Entre nodos:** afinidad `sala → nodo`. Una sala nunca se reparte entre nodos en este clon. Los selectores disponibles son `any`, `cpuload`, `sysload` y `regionaware`, ordenados por `random`, `sysload`, `cpuload`, `rooms`, `clients`, `tracks` o `bytespersec`, con algoritmo `lowest` o `twochoice` (*power of two choices*) (`selector/interfaces.go:33-66`; `utils.go:79-179`). Los límites de admisión sólo cubren `NumTracks` y `BytesPerSec` (`utils.go:59-77`); el propio código marca un `TODO: check remote node configured limit` (`:58`).
- **Capacidad (distinción obligatoria):**
  - `K_loop`: **no aplica**. No hay un loop compartido por sesiones; la unidad es una goroutine por pista, cuyo límite lo pone el coste por paquete × pps, no una cola común `[INFERENCIA]`.
  - `K_worker` = `K_task` = capacidad de un proceso `livekit-server`. **No existe medición reproducible en el repo** `[PENDIENTE]`. Los benchmarks presentes (`receiver_test.go:BenchmarkWriteRTP:131-178`, con trabajo sintético FNV en `writeRTP:282-289`, y `forwardstats_test.go:BenchmarkForwardStatsUpdate:397`) comparan estrategias de fan-out o microcostes; no miden participantes por nodo.
  - `K_flota` = Σ nodos con afinidad por sala, limitada por el desbalance del selector y por el tamaño de sala más grande, que debe caber en un nodo `[INFERENCIA]`.
- **Comparación con Golden:** LiveKit eleva su capacidad por nodo usando todos los núcleos para un trabajo **pequeño** por paquete. Golden tiene un trabajo **mediano** por trama (Silero ~400 µs), más un salto loop → pool → loop. El patrón que importa es **eliminar el salto**: que el dueño de la sesión ejecute su trabajo hasta completarlo y tenga su propio hilo de ejecución.

---

## 11. Patrones y antipatrones

**Implementados (con evidencia):**

| patrón | dónde | estado |
|---|---|---|
| Dueño único por pista, *run-to-completion*, 1 transferencia por paquete | `receiver_base.go:877-1019` | implemented |
| Fan-out adaptativo: inline bajo el umbral, *work-stealing* en lotes de 2 por encima | `downtrackspreader.go:169-223` | implemented |
| Snapshot copy-on-write de suscriptores (`downTracksShadow`) leído bajo `RLock` | `downtrackspreader.go:54-58, 112-151` | implemented |
| Generación atómica + reinicio en dos fases con espera de `WaitGroup` | `receiver_base.go:310-412, 846-875, 928` | implemented |
| Flujo de señal con `seq`, recorte de duplicados y corte ante hueco | `signal.go:233-252, 310-363` | implemented |
| Fuente vigente: un cierre obsoleto no derriba al participante | `room.go:535-547`; `roommanager.go:791` | implemented |
| Afinidad decidida antes de la media; control enrutado por tema/identidad | `roomallocator.go:136-175`; `roommanager.go:571-581, 683-698` | implemented |
| Agregación por sondeo (active speakers cada 400 ms) en vez de evento por paquete | `room.go:383-408, 1641-1678` | implemented |
| Métrica por paquete en anillo *sharded* lock-free, drenado periódicamente | `forwardstats.go:54-97, 241-300` | implemented |
| Vigilancia de bloqueo con volcado de goroutines | `redisrouter.go:202-227` | implemented |
| Flag de silencio comprobado por paquete en el dueño, con resincronización de numeración | `forwarder.go:536-600, 1789-1797` | implemented |

**Latentes:** `RTCService.DrainConnections` (sin caller); pacer `LeakyBucket` (reconocido pero inalcanzable: cae a `NoQueue`, `transport.go:592-606`).

**Antipatrones o riesgos observados:**
- colas sin límite en caminos calientes (`extPackets`, `NoQueue.packets`, `signalMessageSink.queue`);
- pánico = `os.Exit(1)` (dominio de fallo = nodo completo);
- drenaje sin tiempo límite que admite participantes nuevos en salas existentes;
- comentarios de coste contradictorios (26 µs frente a 50 µs).

---

## 12. Callers verificados de las piezas propuestas como referencia

| pieza | callers |
|---|---|
| `forwardRTP` | `startForwarderForBufferLocked` (`receiver_base.go:874`) ← `StartBuffer` (`:760-764`) ← `WebRTCReceiver.AddUpTrack` (`receiver.go:199`) ← `MediaTrack.AddReceiver` (`mediatrack.go:535`) ← `ParticipantImpl.onMediaTrack` (`participant.go:3458`) ← `PublisherTransportHandler.OnTrack` (`participant.go:2034`) ← `pc.OnTrack` (`transport.go:655`). También ← `restartInternal` (`receiver_base.go:408`) |
| `restartInternal` | `ReceiverBase.Restart` (`:305-308`) ← `wrappedreceiver.go:488`, `receiver_base.go:301` (`update-track-info`), `MediaTrack.Restart` (`mediatrack.go:620-621`) ← `participant.go:1838` (antes de `ICERestart`) |
| `BroadcastRTP` | `forwardRTP` (`receiver_base.go:968`); RED en `redprimaryreceiver.go`/`redreceiver.go` (vía `DownTrackSpreader`) |
| `signalMessageReader.Read` | `CopySignalStreamToMessageChannel` (`signal.go:191-226`) ← `signalClient.StartParticipantSignal` (`signal.go:126-140`) y su espejo del lado servidor (`pkg/service/signal.go:RelaySignal:134-200`) |
| `ReplaceParticipantRequestSource` | `Room.ResumeParticipant` (`room.go:558`) ← `RoomManager.StartSession` (`roommanager.go:373`) |
| `SelectRoomNode` | `rtcservice.go:757` (`startConnection`), `roomservice.go:100`, `whipservice.go:224`, `agent_dispatch_service.go:76` |
| `router.Drain` | `LivekitServer.Stop` (`server.go:380`) ← señal en `main.go:317`, error de `Serve` en `server.go:336` |
| `ForwardStats.Update` | `forwardRTP` (`receiver_base.go:981`); creado una sola vez en `wire.go:296` |

---

## 13. Aplicabilidad a GoldenBridge

**Respuesta a la pregunta específica.** Lo que evita que un coordinador global procese todos los paquetes es la combinación de tres mecanismos:
1. **Ownership por pista con goroutine propia.** El paquete lo recibe y lo procesa hasta el final quien es dueño de esa pista, sin pasar por una cola común.
2. **Afinidad resuelta antes de la media.** La selección de nodo y la señalización usan Redis y psrpc, pero el RTP llega por ICE directamente al nodo dueño.
3. **Agregados de sala por sondeo de baja frecuencia.** La sala lee el estado de cada pista cada 400 ms, en lugar de que cada paquete le notifique.

El único punto que ve todos los paquetes de un socket es el lector del UDP mux de pion, y la propia documentación lo neutraliza recomendando ≥ vCPU puertos.

**Traducción a Golden (MediaWorker):**
1. **Dueño por sesión con hilo propio y run-to-completion.** Hoy, por cada mensaje, el loop paga `submit` al pool `vad` y luego `resume` (brief). El análogo LiveKit sería que **el mismo hilo de la sesión lea su trama, ejecute Silero y smart-turn, decida y envíe**, con como máximo una transferencia (lector → hilo de sesión). En Python 3.14t sin GIL podría ser un hilo por sesión, que a 100 sesiones son unos 100 hilos y es factible `[INFERENCIA]`, o N loops con sesiones asignadas por hash. **Esto paraleliza; no mejora `K_loop`.** Habría que medir el `K_worker` resultante y el coste de cambio de contexto de 100 hilos con ORT `intra_op=1` `[PENDIENTE]`.
2. **Repartir la entrada.** El equivalente a "≥ vCPU puertos UDP" sería que la aceptación y lectura de los WebSocket no pase por un único loop: varios loops o procesos aceptores (`SO_REUSEPORT` con `@modal.web_server`), cada uno dueño de su lectura `[INFERENCIA]`.
3. **Generación + parada en dos fases** para `speech_id`/`session_epoch`. `restartInternal` es la plantilla para `CancelSpeech`: incrementar la generación, que el productor salga del bucle al ver otra generación, esperar a que termine (`join`) y sólo entonces arrancar la siguiente.
4. **`seq` con recorte de prefijo y corte ante hueco** en el canal Media ⇄ Conversación. Encaja con la dedup `(epoch, seq)` del contrato de referencia y añade la regla "un hueco es fatal para el stream; se reabre". Es más simple que exigir ACK.
5. **Fuente vigente.** Un cierre tardío del WS viejo no debe cerrar una sesión que ya tiene WS nuevo: comparar identidad de conexión antes de ejecutar el cleanup (`roommanager.go:791`).
6. **Sondeo para agregados** (estado para Conversación, métricas) en vez de eventos por trama, y métricas por trama en estructuras sin lock (`ForwardStats`).
7. **No sirve como modelo para:** la purga en barge-in (no la tiene), el drenaje (sin tiempo límite y con admisión en salas existentes) y el aislamiento de fallos (pánico = nodo).

---

## 14. Riesgos de adopción o copia

- **Adoptar el servidor completo** obliga a meter WebRTC/SIP entre Asterisk y Golden (vía `livekit-sip`), lo que sustituye el WS del ai-engine y el protocolo estilo Deepgram. Golden pasaría a ser un "agente" suscriptor. **Impacto alto** en Asterisk, ai-engine y Modal (Modal no ofrece UDP público por contenedor para ICE `[PENDIENTE]`: verificar en la documentación de Modal antes de considerarlo).
- **Copiar código:** Apache-2.0 obliga a conservar `NOTICE`, y `pkg/sfu` arrastra el aviso MIT de ion-sfu. El código es Go: lo transferible a Golden es el **patrón**, no el código.
- **Falsa equivalencia:** el coste por paquete del SFU es órdenes de magnitud menor que el DSP de Golden. Replicar "goroutine por pista" con hilos Python puede trasladar el cuello a la contención de ORT, al planificador del sistema o a la memoria por hilo. Hay que medirlo con un número de punta a punta, no por analogía.
- **Colas sin límite:** si se copian `extPackets` o `NoQueue` tal cual, se pierde el requisito de "backpressure limitado a la llamada afectada" en forma de memoria. Golden necesita colas acotadas con política de descarte explícita.
- **Dependencia de dependencias:** el camino de entrada (UDP mux, SRTP, batch IO) y las `OpsQueue` no se pueden auditar en este clon. Cualquier conclusión sobre ellas es `[PENDIENTE]`.

---

## 15. Lecciones concretas que pasan a la matriz comparativa

1. **Dueño por pista y run-to-completion** con una sola transferencia entre goroutines por paquete y cero cruces por el control (`receiver_base.go:874, 928-968`).
2. **Afinidad antes de la media**: sala → nodo en Redis; media directa al dueño; control por tema psrpc (`roomallocator.go:136-175`; `roommanager.go:571-581`).
3. **Generación + reinicio en dos fases** con espera explícita de las goroutines viejas (`receiver_base.go:310-412`).
4. **`seq` por stream** con recorte de duplicados y corte ante hueco (`signal.go:233-252`).
5. **Fuente vigente** para ignorar cierres obsoletos (`roommanager.go:791`; `room.go:535-542`).
6. **Agregación por sondeo** (400 ms) y métricas por paquete sin lock (`room.go:1641-1678`; `forwardstats.go:54-97`).
7. **Lo que no hay que imitar:** colas sin límite, pánico = `os.Exit`, drenaje sin tiempo límite, y una purga que no existe.

---

## Piezas reutilizables (plantilla)

```text
Segmento: pkg/sfu/receiver_base.go — forwardRTP / startForwarderForBufferLocked
Responsabilidad principal: consumir los paquetes de UNA pista y entregarlos a sus suscriptores sin coordinador
Pattern principal: Active Object / dueño único por stream (goroutine por recurso, run-to-completion)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] receiver_base.go:startForwarderForBufferLocked:864-875, forwardRTP:877-1019;
           buffer_base.go:ReadExtended:630-658 (sync.Cond); downtrackspreader.go:BroadcastRTP:200-223
Callers verificados: transport.go:655 → participant.go:2034 → 2370 → 3458 → mediatrack.go:535 →
                     receiver.go:199 → receiver_base.go:760-764 → 874; y restartInternal:408
Contrato de entrada/salida: entra *ExtPacket desde Buffer (bloqueante, io.EOF al cerrar);
                            sale WriteRTP(pkt, layer) → int32 aceptados por suscriptor
Política de error: EOF → cierra el receiver si IsSelfClosing; PT inesperado → drop con contador;
                   sin recover (un pánico aborta el proceso)
Ownership: la goroutine es la única lectora del Buffer de su capa; los suscriptores se leen por snapshot COW
Riesgo: extPackets sin límite; WriteRTP inline lento frena a todos los suscriptores de ese publicador
Aplicación posible en Golden: MediaWorker con un hilo (3.14t) o tarea dueña por sesión que lea, ejecute VAD/SmartTurn
                              y reaccione sin ir y volver al pool; cola de entrada acotada por sesión con descarte
                              de lo más viejo. Mide K_worker, no K_loop.
```

```text
Segmento: pkg/sfu/receiver_base.go — restartInternal + forwardersGeneration
Responsabilidad principal: reemplazar los trabajadores de una pista sin que el trabajador viejo emita tras el corte
Pattern principal: Generation counter + two-phase stop (mark → join → restart → new generation)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] receiver_base.go:restartInternal:310-412, startForwardersGeneration:846-852,
           waitForForwardersStop:854-862, condición de bucle :928
Callers verificados: Restart:305-308 ← wrappedreceiver.go:488, receiver_base.go:301, mediatrack.go:621 ← participant.go:1838
Contrato de entrada/salida: Restart(reason) síncrono; devuelve cuando los forwarders nuevos están lanzados
Política de error: reinicios concurrentes deduplicados con restartInProgress; no-op si está cerrado
Ownership: ReceiverBase (bufferMu) dueño de la generación y del WaitGroup
Riesgo: Wait() sin timeout; si un forwarder queda bloqueado fuera de ReadExtended, el reinicio se cuelga
Aplicación posible en Golden: CancelSpeech/nuevo speech_id: subir la generación, que el productor TTS y el pacer
                              salgan al ver otra generación, join con timeout, purgar el presupuesto 0,6 s y sólo
                              entonces aceptar audio de la nueva generación. Sustituye la mutación directa de
                              _cancel_active_tts por un protocolo verificable.
```

```text
Segmento: pkg/routing/signal.go — signalMessageSink.write / signalMessageReader.Read
Responsabilidad principal: transportar mensajes de control por un stream con reintentos sin duplicar ni reordenar
Pattern principal: Sequenced reliable-ish stream (seq de lote + recorte de prefijo + corte ante hueco)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] signal.go:Read:233-252, write:310-363, WriteMessage:365-381, CopySignalStreamToMessageChannel:191-226;
           messagechannel.go:WriteMessage:56-71 (cola 200, no bloqueante)
Callers verificados: signalClient.StartParticipantSignal:75-148 (lado señal); pkg/service/signal.go:RelaySignal:134-200 (lado RTC)
Contrato de entrada/salida: lote {seq del primer msg, msgs[], close}; el receptor descarta seq ya vistos
                            y corta si seq > esperado
Política de error: reintento con backoff hasta RetryTimeout, luego descarta la cola y avanza seq (el receptor lo
                   detecta como hueco y cierra); ErrChannelFull cierra el stream de ese participante
Ownership: un sink y un reader por conexión de señal (connectionID)
Riesgo: la cola del sink no está acotada; at-most-once (sin ACK de aplicación)
Aplicación posible en Golden: canal Media ⇄ Conversación por sesión con (session_epoch, seq); duplicado → descartar;
                              hueco → fin de stream y re-sincronización vía SessionOpened/estado; cola acotada.
```

```text
Segmento: pkg/rtc/room.go + pkg/service/roommanager.go — ReplaceParticipantRequestSource / chequeo de fuente vigente
Responsabilidad principal: impedir que el cierre de una conexión obsoleta destruya la sesión reanudada
Pattern principal: Current-owner token (comparar identidad antes del cleanup)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] room.go:ReplaceParticipantRequestSource:535-542, GetParticipantRequestSource:544-548;
           roommanager.go:rtcSessionWorker:786-795
Callers verificados: Room.ResumeParticipant:550-558 ← RoomManager.StartSession:373 (rama pi.Reconnect, :330)
Contrato de entrada/salida: (identity, MessageSource); el cleanup sólo procede si source == vigente
Política de error: la fuente anterior se cierra activamente al reemplazarla
Ownership: Room.lock protege el mapa identity → source
Riesgo: la comparación es por puntero; no viaja en los mensajes (no protege mensajes ya en vuelo)
Aplicación posible en Golden: si el ai-engine llega a reconectar, el cierre del WS viejo sólo limpia si su
                              connection_id/epoch sigue siendo el vigente de la sesión.
```

```text
Segmento: pkg/sfu/forwardstats.go — forwardSampleBuffer + ForwardStats
Responsabilidad principal: medir la latencia de cada paquete sin contención ni bloqueo del camino caliente
Pattern principal: Sharded lock-free ring + drenado periódico (agregación fuera de banda)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] forwardstats.go:push:54-68, drain:70-97, Update:261-265, run/flush:271-310;
           prometheus/packets.go:RecordForwardLatencySample:370
Callers verificados: receiver_base.go:981 (forwardRTP); instancia única en wire.go:296
Contrato de entrada/salida: Update(arrival, left) → (transit, isHigh); histograma + gauge p90 en Prometheus
Política de error: el desbordamiento sobrescribe y cuenta dropped
Ownership: productores = todas las goroutines forwardRTP; consumidor único = run()
Riesgo: ninguno relevante; mide sólo la latencia de reenvío interna
Aplicación posible en Golden: métricas de rodilla (submit/resume/job/cycle por trama) registradas sin lock por los
                              hilos de sesión y drenadas por un hilo de métricas, sin pasar por el loop.
```

---

## Respuestas explícitas a las 18 preguntas

1. **Unidad de ownership.** Afinidad de flota: **sala**. Ownership del camino de media: **pista publicada** (`ReceiverBase` + goroutine `forwardRTP`) y **DownTrack por suscriptor**. Ownership de señal: **participante** (`rtcSessionWorker`) `[CÓDIGO]` §5.
2. **Quién recibe cada paquete.** El lector de socket o transporte de pion `[PENDIENTE]` lo descifra y llama a `Buffer.Write` de su SSRC. Luego la goroutine `forwardRTP` de esa pista lo consume y lo escribe en cada `DownTrack` `[CÓDIGO]` §7.
3. **Cuántas veces cruza el runtime de control.** **Cero.** Una transferencia entre goroutines (pion → forwarder), sin IPC ni bus `[CÓDIGO]` + `[INFERENCIA]`.
4. **¿Loop único compartido?** No en el camino de media. Hay serialización compartida sólo en: el lector del UDP mux por socket (pion, mitigado con varios puertos `[DOCUMENTACIÓN]`), la cola global de telemetría y webhooks (baja frecuencia) y el lock de `RoomManager.rooms` (sólo en alta y baja) `[CÓDIGO]`.
5. **Varios núcleos.** Goroutines independientes repartidas por el scheduler de Go en un proceso por nodo, más fan-out con `min(NumCPU, N)` goroutines por encima del umbral. Entre nodos: réplicas con afinidad por sala `[CÓDIGO]` §10.
6. **Asignación y afinidad.** `SelectRoomNode` → selector (`twochoice`/`lowest` sobre sysload, cpuload, etc.) → `HSET room_node_map`. Las conexiones siguientes de la sala van al mismo nodo mientras esté "disponible" por frescura de stats. La media va directa a ese nodo por ICE `[CÓDIGO]` §2 y §10.
7. **Estado compartido y local.** Compartido en Redis: nodos, `room_node_map`, `roomStore`. Compartido en el nodo: `RoomManager.rooms`, `ForwardStats`, telemetría. Local: todo el estado de media por pista, suscriptor y transporte `[CÓDIGO]` §5.
8. **Protocolo de control.** WebSocket protobuf (cliente), psrpc stream y unario sobre bus Redis o local (entre nodos y API → dueño), Redis hash (ubicación y stats) y webhooks HTTP `[CÓDIGO]` §6.
9. **Orden, correlación, ACK, dedup y mensajes tardíos.** Señal: `seq` por stream, recorte de duplicados, hueco = cierre; sin ACK de aplicación. Correlación por `connectionID`, por tema `room`/`participant` y por identidad. Fuente obsoleta ignorada por comparación de identidad. Media: RTP `seq` extendido, `bucket` descarta lo demasiado viejo, `RTPMunger` traduce los paquetes fuera de orden con `snRangeMap` o los descarta si no los encuentra (`rtpmunger.go:171-235`) `[CÓDIGO]`.
10. **Backpressure sin afectar a otros.** Aislamiento estructural por goroutine y pista. Cola de señal acotada, cuyo desbordamiento cierra sólo ese stream. Pero `extPackets`, `NoQueue` y la cola del sink **no están acotadas**: el aislamiento es de CPU y orden, no de memoria `[CÓDIGO]` §9.
11. **Dónde ocurre el barge-in y la purga.** No hay barge-in. El equivalente (silenciar) corta en el `Forwarder` de cada suscriptor en el siguiente paquete. **No hay purga** del pacer `[CÓDIGO]` §8.
12. **Audio que ya no se puede retirar.** El escrito al socket, el que está en `NoQueue`/`BatchConn` y el *jitter buffer* remoto `[CÓDIGO]` + `[INFERENCIA]`.
13. **Si muere Media, Control o el canal.** Media y control son el mismo proceso: un pánico termina el nodo (`os.Exit(1)`). Con un nodo muerto, a los 5 s deja de ser elegible y la sala se reasigna; los clientes reconectan por completo. Si cae el canal de señal, hay *resume* sobre el PeerConnection vivo y la fuente nueva reemplaza a la vieja `[CÓDIGO]` §9.
14. **Drenaje.** `SHUTTING_DOWN` en Redis y espera **sin límite** a que no quede ningún participante (sondeo cada 5 s), con salida forzada en la segunda señal. Las salas ya alojadas siguen aceptando participantes durante el drenaje `[CÓDIGO]` §9.
15. **¿Duplica modelos o recursos por proceso?** No hay modelos. Hay un proceso por nodo; pools `sync.Pool` compartidos por paquete (`PacketFactory`, `rtpPacketFactory`) y una factoría de buffers por sala `[CÓDIGO]`.
16. **Métricas para atribuir la rodilla.**
   - latencia de reenvío por paquete (histograma + p90; `forwardstats.go`), con `queuingLatency` en los logs de alta latencia (`receiver_base.go:980-992`);
   - `NodeStats` (load avg, CPU, rooms, clients, tracks in/out, bytes y paquetes por segundo, drops de `tc`; `prometheus/node.go:137-200`);
   - contadores de señal;
   - vigilancia de bloqueo.

   No hay métrica de "lag del scheduler" ni de profundidad de `extPackets` exportada (sólo un aviso en el log) `[CÓDIGO]`.
17. **Qué se puede reutilizar legal y técnicamente.** Legal: Apache-2.0, conservando NOTICE y el aviso MIT de ion-sfu. Técnicamente: los **patrones** de §15 y las piezas de la plantilla. No el código (Go frente a Python), salvo que se adoptara el servidor entero `[INFERENCIA]`.
18. **¿Exigiría reemplazar Asterisk, ai-engine, Modal o proveedores?** Adoptar el **servidor** exige meter WebRTC/SIP (`livekit-sip`) en lugar del WS del ai-engine, reconvertir Golden en agente suscriptor y resolver ICE/UDP en Modal `[PENDIENTE]`. Es de alto impacto. Adoptar sólo los **patrones** no exige reemplazar nada `[INFERENCIA]`.
