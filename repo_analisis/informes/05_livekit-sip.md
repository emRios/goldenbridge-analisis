# 05 — LiveKit SIP (`livekit/sip`)

> Auditoría del puente SIP/RTP ⇄ sala LiveKit, comparado contra el objetivo MediaWorker/Conversación de GoldenBridge (`../BRIEF_GOLDEN.md`).
> Etiquetas: `[CÓDIGO]` archivo:símbolo:líneas · `[DOCUMENTACIÓN]` · `[MEDICIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.
> Rutas sin prefijo = `repos/livekit-sip/`. Prefijos de dependencias: `media-sdk/` = `repos/livekit-media-sdk/`, `psrpc/` = `repos/livekit-psrpc/`, `protocol/` = `repos/livekit-protocol/`, `server/` = `repos/livekit-server/`.

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/livekit/sip.git` |
| Branch | `main` (clon superficial, `git rev-list --count HEAD` = 1) |
| Commit | `765790aeccfe905106afcd9001db1026d32743b5` — "chore: traceID cleanup (#881)" |
| Fecha (`git log -1 --format=%cI`) | `2026-10-09T11:27:05-07:00` |
| Licencia observada | Apache-2.0 (`LICENSE.txt`, `NOTICE` "Copyright 2023 LiveKit, Inc.") |
| Submódulos | ninguno (`.gitmodules` ausente) |
| Lenguaje / toolchain | Go `1.26.3` (`go.mod:3`); build con `CGO_ENABLED=1`, runtime con `libopus0 libopusfile0 libsoxr0` (`build/sip/Dockerfile`) |
| Señal de mantenimiento | commit del mismo día de la auditoría, número de PR #881, `renovate.json`, `codecov.yml`, tests extensos (`pkg/sip/*_test.go` ≈ 9.000 líneas). Sin benchmarks (`grep "func Benchmark"` vacío en sip y media-sdk). |

**Dependencias descargadas adicionalmente** (necesarias porque el camino de media vive fuera del repo; no modificadas):

| local | origen | commit/tag | por qué |
|---|---|---|---|
| `repos/livekit-media-sdk` | `github.com/livekit/media-sdk` | `bc3eace31b2e7bf69c5cacd7c722265fb1789fab` (2026-09-25), el pin exacto de `go.mod:11` | mixer, ring, jitter, rtp session, resample, switches. Apache-2.0 |
| `repos/livekit-psrpc` | `github.com/livekit/psrpc` | tag `v0.8.0` = `da876f1472bd…` (2026-09-20), pin de `go.mod:14` | semántica real de claim/affinity/queue. Apache-2.0 |
| `repos/livekit-protocol` | `github.com/livekit/protocol` | `8c8c356c106d…` (2026-09-25), pin de `go.mod:13`; sparse `rpc/`, `protobufs/rpc/` | código generado `SIPInternal` (cómo se registra el handler). Apache-2.0 |

`pkg/media/` del repo sólo contiene `opus/` y `rtpconn/`; **rtp, mixer, jitter, resample y codecs están en `media-sdk`** `[CÓDIGO]` (`find pkg -type d`; `go.mod:11`).

---

## 2. Dictamen ejecutivo

1. **Ingreso sin dispatcher de aplicación: el SDP es la "redirección".** Cada llamada abre su **propio socket UDP** en un puerto par aleatorio del rango 10000–20000 y anuncia `IP_media:puerto` en el SDP de respuesta; el kernel entrega cada paquete RTP directamente al socket de esa llamada. El servidor de señalización (sipgo) sólo ve mensajes SIP; jamás un paquete RTP `[CÓDIGO]` `pkg/sip/media_port.go:NewMediaPortWith:468-513` (bind en `:475`), `media-sdk/rtp/listen.go:bindEvenRange:85-113`, `pkg/sip/media_port.go:GenerateAnswer:792-819`.
2. **No hay loop compartido en el camino por trama.** Por llamada existen goroutines propias: lector de socket + demux por SSRC, lector por SSRC que ejecuta *síncronamente* decodificación → relleno de silencio → resampleo → Opus → `WriteSample` a la pista WebRTC; y en sentido inverso, una goroutine por pista remota suscrita, un mixer con ticker de 20 ms y su escritor `[CÓDIGO]` `pkg/sip/media_pipeline.go:rtpLoop:321-339`, `rtpReadLoop:341-401`, `pkg/sip/room.go:OnTrackSubscribed:483-555`, `media-sdk/mixer/mixer.go:start:287-300`. El runtime de control de la llamada (`waitForCallEnd`) **nunca toca paquetes**; sólo observa fusibles y un timestamp atómico `[CÓDIGO]` `pkg/sip/inbound.go:waitForCallEnd:1216-1267`, `pkg/sip/media_port.go:onNewMediaPacket:1003-1006`.
3. **El paralelismo es el del scheduler M:N de Go sobre `GOMAXPROCS` hilos, dentro de un solo proceso por contenedor.** No hay procesos media separados ni pool de workers; el "MediaWorker" es la propia goroutine de la llamada `[CÓDIGO]` `cmd/livekit-sip/main.go:runService:68-127` `[INFERENCIA]` (GOMAXPROCS por defecto; el repo no lo fija — `grep GOMAXPROCS` vacío).
4. **Colas acotadas y con política explícita, todas por llamada:** socket→SSRC 10 paquetes *drop-newest* (`media-sdk/rtp/session.go:readStream.write:164-187`); entrada de mixer 5 tramas = 100 ms *drop-oldest* con arranque a 3 tramas (`media-sdk/mixer/mixer.go:30-33,353-400`, `media-sdk/ring/buffer.go:Write:136-166`); salida del mixer canal de 1 bloqueante (`mixer.go:99-103,222-230`); jitter opcional 60 ms (`media-sdk/rtp/jitter.go:25-27`). Ningún buffer compartido entre llamadas.
5. **No existe barge-in ni purga en el puente.** El agente (otro proceso) decide; el puente sólo retiene ≤100 ms en el ring del mixer (+20 ms de canal, +≤60 ms jitter si está activo). Si el agente **despublica** la pista, `mTrack.Close()` retira la entrada del mixer y **descarta su buffer en el acto** `[CÓDIGO]` `pkg/sip/room.go:493-497`, `media-sdk/mixer/mixer.go:RemoveInput:331-343`. La lección es de **dimensionado**: con buffers post-decisión ≤100 ms no hace falta identidad de generación en el transporte.
6. **El "modelo de afinidad" declarado es código muerto.** `Service.CreateSIPParticipantAffinity` (`pkg/sip/service.go:317-329`) no tiene callers fuera de tests; el servidor generado registra el handler con `affinityFunc = nil` y como RPC *queue* (`protocol/rpc/sip.psrpc.go:118,126-128`), y psrpc **prohíbe** affinity en RPC queue (`psrpc/pkg/server/rpc.go:newRPCHandler:61-66`; `psrpc/pkg/client/rpc.go:80-89`). En la práctica: puja fija `affinity=1`, selección `AcceptFirstAvailable` (gana el primero en pujar) y **rechazo tardío por CPU dentro del handler** (`pkg/sip/client.go:createSIPParticipant:375-378`) que, por ser queue, es definitivo para esa petición (`psrpc/pkg/client/rpc.go:289-293`). Para entrantes no hay control de admisión en proceso: la carga se gobierna con `/health` = 429/503 hacia un balanceador externo (`pkg/service/service.go:103-125`).
7. **Valor para Golden: ADAPTAR** (no adoptar). Tres piezas transferibles: (a) identidad de transporte por llamada fijada en el handshake y cuarentena del recurso al cerrar (puerto drenado 30 s/10 min, `pkg/sip/draining_port.go:26-82`); (b) cadena por trama síncrona dentro del contexto de ejecución de la llamada con un único *hand-off* acotado; (c) "switch" atómico de escritor como corte instantáneo y como mecanismo de reconstrucción por generación (`media-sdk/media.go:WriteCloserSwitch:168-235`, `pkg/sip/media_port.go:configure:905-1001`). Adoptar el proyecto exigiría reemplazar AudioSocket/ai-engine por un trunk SIP y añadir LiveKit server + Redis: alternativa de alto impacto (§13–14).

---

## 3. Diagrama del flujo relevante

```text
           Trunk/Asterisk (SIP + RTP)
              │ INVITE (SIP :5060 udp/tcp/tls)               RTP a IP_media:PUERTO_DE_LA_LLAMADA
              ▼                                                   │  (anunciado en el SDP 200 OK)
┌──────────────────────── proceso livekit-sip (1 por contenedor) ──┼───────────────────────────────┐
│ sipgo Server ─ onInvite → processInvite (goroutine de la llamada,│bloqueada hasta el fin)         │
│   │  GetAuthCredentials ─psrpc/Redis─▶ livekit-server (1×INVITE) │                                │
│   │  DispatchCall ───────psrpc/Redis─▶ livekit-server (1×INVITE) │                                │
│   │  createMediaPort → socket UDP propio (bind par aleatorio)    │                                │
│   │  joinRoom → lksdk.Room (WebRTC PeerConnection propia)        │                                │
│   └▶ waitForCallEnd: select{ctx, endCall, room.Closed, MediaTimeout, ACK, tickers}  ← CONTROL     │
│                       (nunca recibe tramas; sólo fusibles/atómicos)                              │
│                                                                  ▼                                │
│  SIP→SALA  rtpLoop [g1]: conn.Read → unmarshal → demux SSRC ──(chan 10, drop)──▶ rtpReadLoop [g2] │
│             g2 síncrono: [jitter 60ms opc.] → mux PT → decode G711/G722/… → silenceFiller →       │
│             resample→48k → (GetMediaProcessor hook, nil por defecto) → Opus enc → pion WriteSample│
│                                                                  │ WebRTC (SRTP) ─▶ SFU LiveKit   │
│  SALA→SIP  por pista remota [g3..]: track.ReadRTP → [jitter] → Opus dec → mixer.Input (ring 100ms,│
│             drop-oldest)                                                                         │
│             mixer ticker 20ms [g4] → outchan(1, bloqueante) → writer [g5] → WriteCloserSwitch →   │
│             resample → encode → SeqWriter → udpConn.Write ─▶ RTP al trunk                        │
│  DTMF in:  RFC4733 → dedup(ts<<8|code) → data packet SipDTMF a la sala                            │
│  DTMF out: data packet SipDTMF → dtmfOutWriter → eventos RTP (+audio opcional vía 2º mixer)      │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
   Control que cruza Redis/PSRPC: auth, dispatch, UpdateSIPCallState (snapshot), CreateSIPParticipant
   (queue), TransferSIPParticipant (topic = sip_call_id). Control agente→puente: RPC de sala
   lk.sip.EndCall / lk.sip.GetRemoteHeaders, atributos de participante. Audio: nunca por Redis.
```

---

## 4. Mapa de procesos, threads, loops y colas

**Procesos.** Uno por contenedor (`build/sip/Dockerfile` ENTRYPOINT único; `cmd/livekit-sip/main.go:runService:68-127`). Sin fork, sin workers hijos `[CÓDIGO]`.

**Goroutines de proceso (compartidas).**

| goroutine | trabajo | evidencia |
|---|---|---|
| `ServeUDP/TCP/TLS` de sipgo | lectura de señalización SIP (no RTP) | `pkg/sip/server.go:startUDP:260-281`, `startTCP:283-304`, `startTLS:306-328` |
| servidor psrpc | 1 lector de suscripción por método + **1 goroutine por petición** | `psrpc/pkg/server/rpc.go:run:126-166` (spawn en `:143-148`) |
| prometheus/pprof/health HTTP | métricas y salud | `pkg/service/service.go:Run:138-169` |
| `hwstats.CPUStats` | muestreo de CPU para `Health()` | `pkg/stats/monitor.go:NewMonitor:97-112` (`[PENDIENTE]` interior de `hwstats`, está en `protocol/utils`, no descargado) |
| `redisbus` publish workers | publicación por lotes a Redis | `psrpc/pkg/bus/redisbus/redisbus.go:59,77-78,401-…` |

**Goroutines por llamada (entrante).**

| goroutine | vida | trabajo | evidencia |
|---|---|---|---|
| handler INVITE (`processInvite`→`handleInvite`→`waitForCallEnd`) | toda la llamada | control: auth, dispatch, SDP, join, espera de eventos | `pkg/sip/inbound.go:372-651, 875-1134, 1216-1267`. Que sipgo lance una goroutine por petición es `[INFERENCIA]` (la función bloquea hasta el fin de la llamada; sipgo no descargado) |
| ringing ticker | hasta 200 OK/CANCEL | 180 Ringing periódico, detecta CANCEL | `pkg/sip/inbound.go:StartRinging:2241-2268` |
| `discardLoop` | hasta negociar SDP | vacía el socket antes de tener pipeline | `pkg/sip/media_port.go:304-344,507` |
| `mediaTimeoutLoop` | toda la llamada | timeout 30 s inicial / 15 s general sin RTP | `pkg/sip/media_port.go:576-675`, defaults `:45-47` |
| `rtpLoop` | toda la llamada | `conn.Read` + unmarshal + demux SSRC | `pkg/sip/media_pipeline.go:321-339`, `media-sdk/rtp/session.go:AcceptStream:76-114` |
| `rtpReadLoop` ×SSRC | por flujo | cadena síncrona RTP→sala | `pkg/sip/media_pipeline.go:341-401` |
| jitter timer ×instancia | si `EnableJitterBuffer` | `popReady` por temporizador | `media-sdk/jitter/buffer.go:NewBuffer:87-119` |
| pista remota ×track suscrito | por pista | `rtp.HandleLoop(track)` → decode → mixer | `pkg/sip/room.go:483-555` |
| mixer ticker + writer | toda la sala | mezcla cada 20 ms y escribe | `media-sdk/mixer/mixer.go:start:287-300`, `writer:273-285` |
| resolución de logger de sala | hasta ready/close | cosmético | `pkg/sip/room.go:289-302` |
| vigilante de cierre | ≤5 min tras `close` | métrica `calls_termination_failures` | `pkg/sip/inbound.go:close:1590-1600` |
| `OnSessionEnd` | al cerrar | callback asíncrono | `pkg/sip/inbound.go:1688-1698` |
| `DrainPort` | ≤10 min tras cerrar | cuarentena del puerto UDP | `pkg/sip/media_port.go:710`, `pkg/sip/draining_port.go:26-82` |
| internos pion/server-sdk-go | toda la llamada | ICE/DTLS/SRTP/señal WS de la PeerConnection | `[PENDIENTE]` (dependencias no descargadas) |

**Colas.**

| cola | dueño | cota | política al llenarse | evidencia |
|---|---|---|---|---|
| socket→`readStream.recv` | llamada/SSRC | 10 paquetes | **descarta el nuevo** (`select default`) | `media-sdk/rtp/session.go:105, 181-185` |
| hand-off directo socket→lector | llamada/SSRC | 1 (una copia) | bloquea hasta que el lector copie | `media-sdk/rtp/session.go:167-178, 202-235` |
| `mixer.Input.buf` (ring) | pista remota | 5 tramas × 20 ms = 100 ms | **descarta lo más viejo** | `media-sdk/mixer/mixer.go:30-33,321-326,387-400`; `media-sdk/ring/buffer.go:Write:136-166` (`:153` "drop old") |
| `mixer.outchan` | sala | 1 | **bloquea** al ticker de esa sala | `media-sdk/mixer/mixer.go:99-103, 222-230` |
| jitter | llamada o pista | latencia máx. 60 ms | emite/descarta por expiración | `media-sdk/rtp/jitter.go:25-42`, `jitter/buffer.go:popReady:415-447` |
| `inboundCall.dtmf` | llamada | 10 eventos | descarta (`default`) | `pkg/sip/inbound.go:822`, `pinDTMFWriter.WriteSample:1286-1304` |
| `inboundCall.endCall` | llamada | 1 | el emisor espera o se cancela por ctx | `pkg/sip/inbound.go:823, EndCall:1575-1586` |
| suscripción psrpc | proceso | `ChannelSize` | `[PENDIENTE]` política en `bus.Subscribe` | `psrpc/pkg/server/rpc.go:74-82` |

---

## 5. Ownership por sesión y recursos compartidos

**Unidad de ownership: la llamada** (`inboundCall` / `outboundCall`), que posee su `mediaPort` (socket UDP + pipeline) y su `Room` (conexión WebRTC propia como participante) `[CÓDIGO]` `pkg/sip/inbound.go:newInboundCall:799-836` (crea `lkRoom` en `:830`), `createMediaPort:1306-1339`; `pkg/sip/outbound.go:newCall:90-166`.

Dentro de la llamada el estado mutable está partido por responsabilidad:

- `mediaPort` (`pkg/sip/media_port.go:516-549`): socket, pipeline (`mu`), SDP local/oferta/negociado, cuatro *anchors* `WriteCloserSwitch` (audioIn, audioOut, dtmfIn, dtmfOut). La interfaz declara explícitamente que "no sabe de llamadas, salas ni diálogos SIP" `[CÓDIGO]` `media_port.go:406-408`.
- `mediaPortPipeline` (`pkg/sip/media_pipeline.go:66-86`): una **generación** por negociación; se reconstruye entera y se intercambia en los anchors (`media_port.go:configure:905-1001`).
- `Room` (`pkg/sip/room.go:226-247`): mixer, switches de salida, puntero atómico a `lksdk.Room`, fusibles `ready/subscribed/stopped/closed`.
- `CallState` (`pkg/sip/analytics.go:101-107`): `SIPCallInfo` bajo mutex, con *dirty bit*.

**Compartido entre llamadas (todo de control, nada por trama salvo métricas):**

| recurso | protección | frecuencia | evidencia |
|---|---|---|---|
| `Server.byLocalTag` (entrantes) | `sync.RWMutex cmu` | por INVITE/ACK/BYE/cierre | `pkg/sip/server.go:170-171`; `inbound.go:441-443, 832-834, 1670-1672` |
| `Client.activeCalls` (salientes) | `cmu` | por llamada | `pkg/sip/outbound.go:162-165, 396-398` |
| `pendingTransfers` | `Service.mu` | por transferencia | `pkg/sip/service.go:474-491` |
| LRUs `provisionalInvites`, `rejectedInvites`, `infos` | `cmu` / mutex propio | por INVITE | `pkg/sip/server.go:172-178, 238-247` |
| tabla global de códecs | arranque | una vez | `pkg/sip/service.go:Start:229` |
| **Prometheus `packets_rtp` CounterVec** | interna de prometheus | **por paquete RTP, ambas direcciones** | `pkg/sip/media.go:rtpStatsHandler.HandleRTP:251-263`, `rtpStatsWriter.WriteRTP:295-313` → `pkg/stats/monitor.go:RTPPacketRecv:496-498` |
| scheduler y GC de Go | runtime | continuo | `[INFERENCIA]` |

El contador por paquete construye un `prometheus.Labels` y hace `With(...)` en cada paquete (`monitor.go:428-434, 492-498`): es el único punto compartido en el camino por trama. `[INFERENCIA]` coste de hashing/lock por paquete; sin medición en el repo `[PENDIENTE]`.

**Estado en Redis:** ninguno propio. El README afirma "Redis is also used as storage for the SIP session state" `[DOCUMENTACIÓN]` `README.md` §Service Architecture, pero el código sólo **publica** snapshots `UpdateSIPCallState` hacia livekit-server (`pkg/sip/analytics.go:rpcStateHandler.HandleUpdate:66-71`); el puente no lee estado de llamada desde Redis para operar `[CÓDIGO]`.

---

## 6. Contrato de control/IPC

**Canal 1 — PSRPC sobre Redis (bus `redisbus`)** `[CÓDIGO]` `cmd/livekit-sip/main.go:78-89`.

| RPC | dirección | cardinalidad | semántica | evidencia |
|---|---|---|---|---|
| `GetSIPTrunkAuthentication` | SIP→server | 1×INVITE | request/response síncrono en la goroutine de la llamada | `pkg/service/psrpc.go:GetAuthCredentials:14-86`, caller `inbound.go:538-547` |
| `EvaluateSIPDispatchRules` | SIP→server | 1×INVITE (+PIN) | idem; error de transporte ⇒ `503 Try again later` | `pkg/service/psrpc.go:DispatchCall:99-220`, caller `inbound.go:893-899, 960-964` |
| `UpdateSIPCallState` | SIP→server | por cambio de estado + keep-alive cada 10 min | **snapshot completo** de `SIPCallInfo`, errores ignorados, síncrono bajo `CallState.mu` | `pkg/sip/analytics.go:66-71, 130-136, 206-209`; tick `inbound.go:58, 1230-1232` |
| `CreateSIPParticipant` | server→SIP | 1×llamada saliente | **queue + claim**, topic `ClusterID` (server publica con topic `""`) | `protocol/rpc/sip.psrpc.go:87-89, 118, 126-128`; `pkg/service/service.go:228-240`; `server/pkg/service/sip.go:626` |
| `TransferSIPParticipant` | server→SIP | por transferencia | **topic = `sip_call_id`**, registrado por el proceso dueño al unirse a la sala y desregistrado al cerrar | `protocol/protobufs/rpc/sip.proto:31-39`; `pkg/sip/inbound.go:createLiveKitParticipant:1844-1849`, `close:1674`; `outbound.go:connectToRoom:458-461, close:400` |

Mecánica psrpc relevante (verificada en la dependencia):

- Expiración: el servidor descarta peticiones vencidas antes de ejecutar (`psrpc/pkg/server/rpc.go:143-151`); el cliente acota la expiración al deadline del caller (`psrpc/pkg/client/rpc.go:97-103`).
- Claim: el servidor publica una puja y espera la concesión hasta la expiración; si gana otro, abandona (`psrpc/pkg/server/rpc.go:claimRequest:205-286`). Sin `affinityFunc`, la puja es `1` (`:212-220`).
- Queue sobre Redis: todos los procesos suscritos reciben el mensaje por pub/sub (`redisbus.go:250` suscribe el canal tanto para `subs` como `queues`); `dispatchQueue` sólo hace round-robin **entre suscriptores locales del mismo proceso** (`redisbus.go:dispatchQueue:298-304`). Entre procesos decide la carrera de claims con `AcceptFirstAvailable` (`psrpc/pkg/client/options.go:62-65`, `client/rpc.go:271-273`) `[CÓDIGO]`.

**Canal 2 — sala LiveKit (WebRTC data/RPC + atributos)**, entre el agente y el puente, sin Redis:

- `lk.sip.EndCall` → `call.EndCall` → `endCall` (cap. 1) → `waitForCallEnd` cierra `[CÓDIGO]` `pkg/sip/rpc.go:registerCallRPC:90-98`, `rpcEndCall:68-74`; `inbound.go:1575-1586, 1236-1238`.
- `lk.sip.GetRemoteHeaders` `[CÓDIGO]` `pkg/sip/rpc.go:32-84`.
- DTMF saliente como `livekit.SipDTMF` data packet `[CÓDIGO]` `pkg/sip/room.go:OnDataPacket:556-567`.
- Estado de llamada como atributo `sip.callStatus` del participante `[CÓDIGO]` `inbound.go:setStatus:1809-1825`.

**Canal 3 — la señalización SIP misma** (INVITE/ACK/BYE/re-INVITE/REFER/NOTIFY) con su propia correlación por `Call-ID`, tags y `CSeq` (`pkg/sip/inbound.go:441-463`, `onBye:672-…`).

**Identidad.** `LocalTag` (to-tag local = `LkCallId`) es la clave de ownership (`inbound.go:517-524`, `server.go:171`); `sip_call_id` es el topic de transferencia; `traceid` derivado del GUID (`inbound.go:424`). No hay `epoch`/`seq` de aplicación en los mensajes de control: el estado sube como snapshot idempotente (último gana) `[CÓDIGO]` `analytics.go:206-209`.

---

## 7. Ruta de audio y frecuencia de cruces

**SIP → sala (por paquete RTP de 20 ms, 50/s):**

1. `rtpLoop` (g1): `udpConn.Read` → `rtp.Packet.Unmarshal` → mapa `bySSRC` → `readStream.write` `[CÓDIGO]` `media-sdk/rtp/session.go:76-114`; `pkg/sip/media_port.go:udpConn.Read:265-294` (aprende `src` y fija `dst` si RTP simétrico).
2. **Único hand-off entre goroutines**: copia directa al buffer del lector si está esperando; si no, cola de 10 con descarte `[CÓDIGO]` `session.go:164-187`.
3. `rtpReadLoop` (g2), todo síncrono: `onPacket` (atómico para timeout) → `muxToRoom.Load()` (puntero atómico a la generación vigente) → [jitter] → `rtpStreamStats` → mux por payload type → `rtpStatsHandler` (Prometheus) → `SerializedRTPHandler` (mutex) → `silenceFiller` → decode → contador → resample a 48 kHz → latencia → `NopCloser(audioToRoom)` `[CÓDIGO]` `pkg/sip/media_pipeline.go:setupInput:158-225`, `rtpReadLoop:341-401`.
4. `audioToRoom` = encoder Opus → `FromSampleWriter` (copia) → `TrackLocalStaticSample.WriteSample` de pion `[CÓDIGO]` `pkg/sip/room.go:NewParticipantTrack:753-774`, `media-sdk/pcm.go:sampleWriter.WriteSample:177-181`. Lo que pion hace después `[PENDIENTE]`.

**Sala → SIP:**

1. goroutine por pista (g3): `rtp.HandleLoop(track, h)` → [jitter] → stats → decode Opus/PCMU/PCMA → `mixer.Input.WriteSample` (ring bajo mutex, sin despertar goroutine) `[CÓDIGO]` `pkg/sip/room.go:483-555`.
2. mixer ticker (g4) cada 20 ms: `mixUpdate` compensa despertares tardíos (hasta `inputBufferFrames` mezclas de golpe) → `mixOnce` suma entradas, satura a int16 → `outchan` `[CÓDIGO]` `media-sdk/mixer/mixer.go:166-271`.
3. writer (g5) → `Room.outboundAudio` (switch) → `mediaPort.audioOut` (switch) → pipeline: latencia → resample a la tasa del códec → encode → `SeqWriter` (numeración RTP) → `rtpStatsWriter` → `udpConn.Write` `[CÓDIGO]` `pkg/sip/media_pipeline.go:setupOutput:247-319`, `media_port.go:udpConn.Write:296-302`.
4. El mixer emite tramas **siempre** (ceros si no hay entradas con datos): el tramo SIP saliente tiene cadencia propia de 20 ms independiente del agente `[CÓDIGO]` `mixer.go:mixOnce:195-232` (no hay rama que omita la salida).

**Cruces del runtime de control por paquete: 0.** El control sólo lee `mediaReceived` (fusible al primer SSRC) y `lastPacketTime` (atómico) `[CÓDIGO]` `media_port.go:979-980, 1003-1006`; `inbound.go:waitMedia:1411-1445`.

**Cruces de IPC por paquete: 0 hacia Redis.** El audio sí cruza la red hacia el SFU (WebRTC/SRTP) una vez por trama y dirección; eso es plano de media, no de control.

---

## 8. Secuencia de barge-in

**El puente no implementa barge-in.** `grep -i "barge|interrupt|clear"` en `pkg/` y `media-sdk` sólo devuelve el relleno de silencio del generador DTMF (`media-sdk/dtmf/dtmf.go:337`) `[CÓDIGO]`. La detección de habla y la decisión de interrumpir viven en el agente (proceso aparte, ver informe de LiveKit Agents). La secuencia efectiva en el puente es:

1. El usuario habla → su audio sigue fluyendo SIP→sala sin interrupción ni gating (§7) `[CÓDIGO]`.
2. El agente deja de publicar tramas en su pista (o la despublica) — fuera de este repo `[PENDIENTE]` aquí.
3. **Pausa de pista:** la goroutine de la pista se queda en `ReadRTP`; el mixer sigue leyendo el ring hasta vaciarlo (≤5 tramas = 100 ms) y luego marca `buffering=true` y emite ceros `[CÓDIGO]` `mixer.go:readSample:353-369`.
4. **Despublicación:** `HandleLoop` retorna → `defer mTrack.Close()` → `RemoveInput` → **el ring y su audio se descartan inmediatamente** `[CÓDIGO]` `room.go:493-497, 550-553`; `mixer.go:331-343, 379-385`. `OnTrackUnsubscribed` sólo registra en log (`room.go:568-570`).
5. **Corte duro por switch:** durante una transferencia con tono, el puente silencia la sala hacia SIP con `WriteOutboundAudioTo(nil)`: escrituras posteriores se descartan sin error (`media-sdk/media.go:WriteSample:194-200` devuelve `nil` si no hay escritor) `[CÓDIGO]` `inbound.go:transferCall:1962-1968`. Es una primitiva de corte instantáneo reutilizable, no un barge-in.

**Audio que ya no puede retirarse cuando el agente decide parar** (cota superior desde el puente, `[INFERENCIA]` sumando cotas verificadas):

| tramo | cota | evidencia |
|---|---|---|
| ring de entrada del mixer | 100 ms (5×20 ms) | `mixer.go:30-31, 324` |
| `outchan` | 20 ms (1 trama) | `mixer.go:99-103` |
| jitter (si activo) | 60 ms | `media-sdk/rtp/jitter.go:26` |
| en tránsito SFU→puente, pion | `[PENDIENTE]` | — |
| en tránsito puente→trunk + jitter del trunk | fuera del sistema | — |

Comparación con Golden: cola residual tras barge-in ≈ 0,6 s (presupuesto GB) + 300 ms (jitter engine) + 40–200 ms (BRIEF). LiveKit SIP mantiene ≤~180 ms en su lado **por diseño de buffers**, sin protocolo de purga.

---

## 9. Backpressure, fallos, reconexión y drenaje

**Backpressure (aislado por llamada).**
- Si el consumidor de la sala (pion) se bloquea, se bloquea `rtpReadLoop` **de esa llamada**; `rtpLoop` sigue leyendo el socket y descarta al llenarse la cola de 10 (`session.go:181-185`). Otras llamadas no comparten esa goroutine `[CÓDIGO]` `[INFERENCIA]` (aislamiento).
- Sala→SIP: un productor rápido desborda su ring y pierde lo más viejo (`ring/buffer.go:153`), contabilizado en `InputFramesDropped` (`mixer.go:393-396`). Si el escritor hacia el puerto se bloquea, el ticker de **esa sala** se bloquea en `outchan` y cuenta `BlockedMixes` (`mixer.go:222-230`); el comentario reconoce que aún no descartan en ese caso ("TODO: Consider, carefully, dropping when blocked").
- Errores de handler: 50 errores consecutivos (≈1 s) matan el lector de esa llamada (`media_pipeline.go:342, 392-395`).
- Riesgo autodocumentado: carrera en `ReadRTP` que puede despertar al lector equivocado y "stall Goroutine A" (`media-sdk/rtp/session.go:218-223`) `[CÓDIGO]`.

**Fallos.**

| falla | efecto | evidencia |
|---|---|---|
| muere el proceso SIP | todas sus llamadas se pierden; no hay migración ni re-anclaje (el estado no se lee de Redis) | `[INFERENCIA]` de §5; ningún código de recuperación de sesión |
| cae Redis | entrantes nuevas fallan en auth → `503 Try again later`; llamadas activas siguen (media no depende de Redis); snapshots de estado perdidos en silencio | `inbound.go:538-547`; `analytics.go:70` (`_, _ =`) |
| cae la conexión a la sala | SDK intenta resume/reconnect; durante el reconnect "writes to a detached track are discarded"; tras reconnect re-suscribe; si se desconecta, `waitForCallEnd` cierra con la razón de la sala | `room.go:onReconnecting:603-615`, `onReconnected:617-674`, `inbound.go:1239-1245` |
| deja de llegar RTP | `mediaTimeout` cierra (30 s inicial / 15 s general; si faltó ACK, ventana corta) | `media_port.go:576-675`; `inbound.go:854-866, 1251-1264` |
| re-INVITE | se compara `CSeq`; con respuesta tardía pendiente ⇒ `491 Request Pending`; los cambios de SDP no reconfiguran el pipeline salvo negociación nueva ("Explicitly disable renegotiation for now") | `inbound.go:444-463`; `media_port.go:961-966` |
| cierre no termina | vigilante de 5 min incrementa `calls_termination_failures` | `inbound.go:1590-1600` |

**Cierre idempotente.** `inboundCall.close` usa `done.CompareAndSwap` (`inbound.go:1603-1605`); `outboundCall.close` usa `stopped.Once` (`outbound.go:352`); `mediaPort.Close` usa `closed.Once` (`media_port.go:695`); `Room.CloseWithReason` usa `closed.Once` (`room.go:725`). Orden: BYE **antes** de cerrar media/sala (`inbound.go:1661-1666`), luego quitar del mapa, desregistrar topic, `OnSessionEnd` asíncrono, `cancel()` `[CÓDIGO]`.

**Protección frente a obsoletos (transporte).** Al cerrar, el socket **no se libera**: `DrainPort` lo sigue leyendo y descartando hasta 30 s sin paquetes o 10 min máximo, para que RTP tardío de la llamada anterior nunca llegue a una llamada nueva que reutilice el puerto `[CÓDIGO]` `media_port.go:707-713`, `draining_port.go:26-82`, defaults `pkg/config/config.go:52-53, 211-220`. Otras defensas: caché de rechazos por `(Call-ID, From-tag)` (`inbound.go:428-437`), `referDone` sin buffer "para no leer el resultado de una petición vieja" (`inbound.go:2067`), deduplicación DTMF por `(timestamp<<8 | code)` (`media_pipeline.go:handleEventRTP:228-243`), expiración de peticiones psrpc (`psrpc/pkg/server/rpc.go:143`), generación de pipeline intercambiada atómicamente (`media_pipeline.go:371-376`, `Close:412-414`).

**Drenaje.** `SIGTERM/SIGQUIT` → `svc.Stop(false)`: `mon.Shutdown()` (⇒ `Health()=Stopped`, `/health`=503, gauge `available`=0) → desregistra el topic `CreateSIPParticipant` (no más salientes) → **sondea cada 5 s hasta 0 llamadas, sin tope de tiempo** → `sipServiceStop` `[CÓDIGO]` `cmd/livekit-sip/main.go:91-124`; `pkg/service/service.go:Stop:129-133, Run:185-208`; `pkg/stats/monitor.go:Health:363-374`. `SIGINT` → kill: corta todas con BYE (`server.go:Stop:397-413`, `inbound.go:Shutdown:1782-1784`). **Durante el drenaje los INVITE entrantes se siguen aceptando**: `processInvite` no consulta `Health()` ni `closing` (`inbound.go:372-651`; `closing` sólo se usa en REFER `:2565`) — depende de que el balanceador externo respete el 503 `[CÓDIGO]` (ausencia) `[INFERENCIA]`.

---

## 10. Escalado y uso real de núcleos

- **Paralelismo:** goroutines por llamada sobre el scheduler de Go, sin loop compartido en el camino por trama ⇒ paralelismo real entre núcleos dentro de un único proceso `[CÓDIGO]` (§4) `[INFERENCIA]` (semántica del runtime Go).
- **Admisión saliente:** primera puja gana; rechazo por CPU dentro del handler con `max_cpu_utilization` (default 0,9) `[CÓDIGO]` `client.go:376-378`, `config.go:221-222`, `monitor.go:370-372`. `max_active_calls` existe en config con el comentario "if set, used for affinity-based routing" (`config.go:112`) pero sólo lo lee la función de afinidad muerta (`service.go:322-327`) `[CÓDIGO]`.
- **Admisión entrante:** ninguna en proceso; el escalado es externo (`/health` 200/429/503 y `prometheus_port` "Used for autoscaling" `[DOCUMENTACIÓN]` README §Config).
- **Límite de puertos:** 10000–20000 con puertos pares ⇒ 5.001 sockets RTP posibles por IP; los puertos en drenaje (hasta 10 min) cuentan contra ese límite `[CÓDIGO]` `config.go:46`, `listen.go:92-97` `[INFERENCIA]` (con alta rotación el drenaje reduce los puertos libres).
- **Memoria por llamada:** codificador/decodificador Opus, resamplers soxr, mixer, buffers, y una PeerConnection WebRTC completa por llamada (`room.go:418-426`) `[CÓDIGO]`; cuantía `[PENDIENTE]`.
- **Capacidad:** **no hay medición reproducible** en el repo (sin benchmarks; `test/` es integración funcional) `[PENDIENTE]`.

Distinción obligatoria:

| nivel | en LiveKit SIP | evidencia |
|---|---|---|
| **K_loop** | **no aplica**: no existe un loop que coordine tramas de varias llamadas; la unidad de ejecución por trama es la goroutine de la llamada | §4, §7 |
| **K_worker** | llamadas por proceso, limitadas por CPU (gate 0,9), puertos y memoria; valor `[PENDIENTE]` | `monitor.go:363-374` |
| **K_task** | = K_worker (1 proceso por contenedor) | Dockerfile |
| **K_flota** | Σ nodos; salientes por carrera de claims, entrantes por balanceo externo | §6 |

Ninguno de estos niveles mejora la eficiencia por llamada: el diseño elimina el coordinador del camino por trama y deja el paralelismo al scheduler. No es `N × K_loop`.

---

## 11. Patrones y antipatrones

**Patrones implementados.**

| patrón | dónde | comentario |
|---|---|---|
| *Rendezvous* por handshake (SDP anuncia `IP:puerto` del dueño) | `media_port.go:GenerateAnswer:792-819` | el dato elige su destino; nadie reenvía |
| Recurso de transporte por sesión + cuarentena al liberar | `listen.go:85-113`, `draining_port.go:26-82` | anti-obsoletos sin protocolo |
| *Thread-per-session* (goroutine) con cadena síncrona | `media_pipeline.go:341-401` | 1 hand-off por paquete |
| Generación intercambiable bajo anchors estables (`WriteCloserSwitch` + `atomic.Pointer`) | `media_port.go:905-1001`, `media_pipeline.go:371-376`, `media-sdk/media.go:168-235` | reconstruir sin parar al consumidor |
| Buffers acotados con política explícita (drop-oldest / drop-newest / bloqueo local) | §4 tabla de colas | contadores por política |
| Pacer por sala con compensación de despertar tardío | `mixer.go:mixUpdate:234-271` | métricas `JumpMixes`, `TimingResets` |
| Topic de control por sesión registrado por el dueño | `inbound.go:1844-1849`, `server.go:415-421` | enrutar comandos al proceso dueño sin dispatcher |
| Snapshot de estado idempotente + keep-alive | `analytics.go:130-136, 206-209`; `inbound.go:1230-1232` | tolera pérdida; sin seq |
| Cierre idempotente con orden explícito | `inbound.go:1589-1702` | BYE antes de cerrar media |
| Hook de procesamiento por llamada en el camino de media | `server.go:144`, `inbound.go:1874-1876`, `outbound.go:553-555` | `nil` en OSS (`pkg/service/service.go:220-222`) |

**Patrones latentes.**
- `CreateSIPParticipantAffinity` (carga = `1 - activas/max`): implementado y testeado, **no cableado** e **incompatible** con el registro queue de psrpc `[CÓDIGO]` (§2.6). Indica intención de enrutamiento por carga no realizada en OSS. Posibles builds cloud distintos `[PENDIENTE]` (`NewService` acepta `opts`, `service.go:94`).

**Antipatrones / riesgos.**
- Métrica Prometheus con etiquetas resueltas por paquete (`media.go:251-263, 295-313`): único estado compartido en el camino por trama.
- Rechazo por carga **después** de ganar el claim en un RPC queue: la petición falla en vez de ir a otro nodo (`client.go:376-378` + `psrpc/pkg/client/rpc.go:289-293`).
- Entrantes aceptadas durante el drenaje (§9).
- Escritura de estado a Redis síncrona bajo el mutex de la llamada (`analytics.go:130-136` → `:70`): una latencia de Redis bloquea esa goroutine de control, no la media `[INFERENCIA]`.
- El *jitter buffer* se activa por probabilidad (`SelectValueBool(conf.EnableJitterBuffer, conf.EnableJitterBufferProb)`, `inbound.go:824`, `outbound.go:98`): dos comportamientos de latencia en la misma flota; útil como A/B, peligroso para atribuir una rodilla.

---

## 12. Callers verificados de las piezas propuestas como referencia

| pieza | definición | callers verificados |
|---|---|---|
| `NewMediaPort` / `NewMediaPortWith` | `media_port.go:464-513` | `inbound.go:createMediaPort:1314`; `outbound.go:newCall:127` |
| `mediaPort.GenerateAnswer` | `media_port.go:792-819` | `inbound.go:negotiateMedia:1373`, `updateRemoteFromSDP:1796`; `outbound.go:updateRemoteFromSDP:538` |
| `mediaPort.configure` | `media_port.go:905-1001` | `GenerateAnswer:814`, `ProcessAnswer:848` |
| `NewMediaPortPipeline` | `media_pipeline.go:47-64` | `media_port.go:configure:982-989` |
| `rtpLoop` / `rtpReadLoop` | `media_pipeline.go:321-401` | `setupOutput:248` (`rtpLoopWG.Go(p.rtpLoop)`), `rtpLoop:337` |
| `mediaPortPipeline.Close` | `media_pipeline.go:403-431` | `media_port.go:closePipelineLocked:688-691`; `NewMediaPortPipeline:60` (error de init) |
| `DrainPort` | `draining_port.go:26-82` | `media_port.go:Close:710`; tests `draining_port_test.go` |
| `WriteCloserSwitch.Swap` (anchors) | `media-sdk/media.go:222-235` | `media_port.go:755-765, 929-932, 681-686`; `room.go:794-800`; `inbound.go:1165-1172, 1877, 1887, 1963, 1966`; `outbound.go:543-560` |
| `mixer.NewMixer` / `NewInput` / `Input.Close` | `media-sdk/mixer/mixer.go:131-144, 308-329, 379-385` | `room.go:NewRoom:281`, `NewTrack:787-792` → `room.go:493-497`, `inbound.go:playAudio:1915-1919`; DTMF audio `media_pipeline.go:296-301` |
| `RegisterTransferSIPParticipantTopic` | `pkg/service/service.go:242-248` → `protocol/rpc/sip.psrpc.go:RegisterTransferSIPParticipantTopic` | `server.go:415-417` ← `inbound.go:1845`; `client.go:575-577` ← `outbound.go:460` |
| `DeregisterTransferSIPParticipantTopic` | `pkg/service/service.go:250-254` | `inbound.go:close:1674`; `outbound.go:close:400` |
| `Monitor.Health` | `monitor.go:363-374` | `client.go:376`; `pkg/service/service.go:111, 224-226`; `monitor.go:290` (gauge) |
| `CreateSIPParticipantAffinity` | `pkg/sip/service.go:317-329` | **sólo tests** (`service_test.go:1223-1380`); 0 callers en sip/protocol/server |
| `Service.Run` (drenaje) | `pkg/service/service.go:135-210` | `cmd/livekit-sip/main.go:126` |
| `CallState.Update/ForceFlush` | `analytics.go:130-136, 197-201` | `inbound.go:384-401, 922-937, 1117-1125, 1232`; `outbound.go:180-206, 221-230, 254, 368-374` |

---

## 13. Aplicabilidad a GoldenBridge

**Lo que se transfiere (patrón, no código):**

1. **El dueño se decide en el handshake y el dato llega solo.** En SIP, el SDP lleva el `puerto` del dueño. En Golden, una conexión WebSocket es *ya* afín por naturaleza: una vez aceptada por un proceso, todas sus tramas llegan a ese proceso. El equivalente del "puerto por llamada" es que **el proceso que acepta el WS sea el MediaWorker dueño** (p. ej. varios procesos aceptando en el mismo puerto) en lugar de un relay Python que reenvía tramas `[INFERENCIA]`. Que esto funcione detrás del proxy de Modal con `@modal.web_server(puerto)` y aceptación multiproceso es `[PENDIENTE]` (PoC). LiveKit SIP tampoco hace balanceo por carga *dentro* del proceso para entrantes: lo delega al borde (§10).
2. **Cadena por trama sin cruce al coordinador.** Golden paga por mensaje `submit` al pool `vad` + `resume` de la corrutina en el único loop (BRIEF). LiveKit SIP hace por paquete un único hand-off (socket→lector de la llamada) y el resto síncrono en el contexto de la llamada. Traducción a Python 3.14t: un contexto de ejecución por llamada (hilo libre de GIL, o un loop por worker con pocas llamadas) que ejecute `decode → soxr → Silero → (smart-turn)` sin volver al loop de control por trama. Es un cambio de **topología de ejecución**, y su ganancia es `[PENDIENTE]` hasta medirla (riesgos: coste de hilos en 3.14t, ORT con `intra_op=1` por hilo, contención de refcount/GC).
3. **Dimensionar el buffer posterior al punto de decisión.** LiveKit SIP no necesita purga porque retiene ≤100 ms (+20 +≤60). Golden retiene ≈0,6 s + 0,3 s y su engine descarta los JSON de control (BRIEF). Reducir el presupuesto del `_PacedAudioSender` hacia ~100 ms es la palanca más barata y es medible punta a punta (time-to-silence tras barge-in). `[INFERENCIA]` coste: más sensibilidad a jitter de red engine⇄Modal.
4. **Pacer con métricas de puntualidad.** `mixUpdate` mide despertares a tiempo (`TimedMixes`), tardíos (`JumpMixes`), reinicios (`TimingResets`) y bloqueos (`BlockedMixes`) por llamada (`mixer.go:234-271`, `media.go:153-173`). Es exactamente el instrumento para detectar la rodilla de Golden por llamada (equivalente a `cycle_p90`/`resume_vad_p90`), barato y local.
5. **Topic de comandos por sesión registrado por el dueño.** Para Conversación→Media sin dispatcher: el MediaWorker que posee `session_id` se suscribe a su canal; el emisor publica al canal de la sesión. Útil sólo si Conversación queda en otro proceso/tarea `[INFERENCIA]`.
6. **Cuarentena de identidad al cerrar.** Golden puede replicarlo a nivel de protocolo: un `session_epoch` nuevo por reconexión y una lista de `session_id` cerrados que se descartan durante N s, análogo a `DrainPort` `[INFERENCIA]`.
7. **Admisión explícita, no "affinity" de fachada.** La lección negativa: LiveKit SIP declara una función de carga que nunca se ejecuta y rechaza tarde. Golden debe rechazar **antes** de aceptar el WS (o antes del `SessionOpened`) con un contador local y un umbral = rodilla medida (16-25 hoy), y exportarlo.

**Lo que NO se transfiere:**
- El modelo "SIP participante = PeerConnection WebRTC hacia un SFU" (coste por llamada alto, `[PENDIENTE]` cuánto) y la topología LiveKit, donde el **agente Python sí recibe audio por trama** a través del SFU: LiveKit SIP es un gateway de media, no separa VAD/turnos del proceso conversacional. El contrato `(session_id, session_epoch, turn_id, speech_id, seq)` no tiene análogo aquí: el puente no conoce turnos ni generaciones de habla.

**Respuesta a la pregunta específica** — *¿cómo ingresa una llamada al proceso Media dueño sin pasar su audio por un dispatcher de aplicación?* — La señalización (SIP, baja frecuencia) llega a un servidor compartido del proceso; éste crea para la llamada un socket UDP propio y responde con un SDP que contiene `IP_media:puerto`. A partir de ahí el emisor RTP envía directamente a ese puerto y el **kernel** hace el demultiplexado por socket; dentro del socket, el demux por SSRC lo hace la goroutine de la llamada. El proceso que procesó el INVITE es por construcción el dueño del media; no existe un proceso o hilo intermedio que vea paquetes de varias llamadas `[CÓDIGO]` `inbound.go:1021-1047`, `media_port.go:468-513, 792-819`, `media-sdk/rtp/session.go:76-114`.

---

## 14. Riesgos de adopción o copia

| riesgo | detalle | severidad |
|---|---|---|
| Reemplazo de stack | adoptar LiveKit SIP exige trunk SIP desde Asterisk (hoy AudioSocket), LiveKit server (SFU) y Redis; ai-engine quedaría obsoleto y Golden pasaría a ser un *agent* LiveKit. Toca Asterisk, ai-engine y Modal | alta — alternativa de alto impacto, sólo con coste explícito |
| Lenguaje | Go; copiar código no aplica a Python. Sólo patrones | media |
| Licencias | Apache-2.0 en sip, media-sdk, psrpc, protocol (permisivas, exigen NOTICE). Enlace cgo con **libsoxr (LGPL-2.1)** y libopus (BSD) en runtime (`media-sdk/resample_soxr.go:1,20`; Dockerfile) — Golden ya usa soxr | baja |
| Falsa capacidad | ninguna cifra de llamadas/núcleo en el repo; cualquier "K" derivada de LiveKit SIP es `[PENDIENTE]` | alta si se cita |
| Afinidad de fachada | copiar `CreateSIPParticipantAffinity` creyendo que enruta por carga | media |
| Thread-per-call en Python | el éxito en Go depende de goroutines baratas y de un scheduler sin GIL; en 3.14t los hilos son del SO y el coste de 100+ hilos con ORT es desconocido | media — PoC obligatoria |
| Drenaje sin tope | espera ilimitada (llamadas hasta 24 h, `participant.go:77`); en Modal el scale-down de WS no está documentado (BRIEF) | media |

---

## 15. Lecciones concretas que pasan a la matriz comparativa

### Respuestas explícitas a las 18 preguntas

1. **Unidad de ownership:** la **llamada** (`inboundCall`/`outboundCall`), dueña de su `mediaPort` (socket + pipeline) y su `Room` `[CÓDIGO]` `inbound.go:799-836`, `outbound.go:90-166`.
2. **Quién recibe cada paquete:** el socket UDP **de esa llamada**, leído por su `rtpLoop`; en sentido inverso, la goroutine de la pista remota de esa llamada `[CÓDIGO]` `media_pipeline.go:321-339`, `room.go:483-555`.
3. **Cruces del runtime de control por paquete:** **0** `[CÓDIGO]` §7.
4. **Loop único compartido:** no en media. Compartidos: el servidor SIP (señalización), el servidor psrpc (una goroutine por petición), Prometheus (contador por paquete) `[CÓDIGO]` §4–5.
5. **Varios núcleos:** goroutines por llamada sobre el scheduler de Go en **un proceso por contenedor**; réplicas para la flota `[CÓDIGO]` `[INFERENCIA]` §10.
6. **Asignación y afinidad:** entrante = el nodo que recibe el INVITE (balanceo externo), afinidad media fijada por el SDP; saliente = cola psrpc con claim, primera puja (afinidad fija 1) gana; transferencias por topic `sip_call_id` del dueño `[CÓDIGO]` §6.
7. **Estado compartido vs local:** compartido sólo de control (mapas de llamadas, LRUs, transferencias, registro Prometheus); todo el estado de media es local a la llamada `[CÓDIGO]` §5.
8. **Protocolo de control:** PSRPC sobre Redis (request/response, queue+claim, topics), RPC y data packets de sala LiveKit, y SIP `[CÓDIGO]` §6.
9. **Orden/correlación/ACK/dedup/tardíos:** orden por un único lector por SSRC + `SerializedRTPHandler` + jitter opcional + `SeqWriter`; correlación por `LocalTag`/`Call-ID`/`CSeq`; dedup DTMF, caché de rechazos, `pendingTransfers`; tardíos: expiración psrpc, `referDone` sin buffer, cuarentena de puerto; estado como snapshot idempotente sin `seq` `[CÓDIGO]` §6, §9.
10. **Backpressure:** colas por llamada con política (drop-newest 10, drop-oldest 100 ms, bloqueo local de 1); ningún buffer compartido `[CÓDIGO]` §9.
11. **Barge-in y purga:** no existen en el puente; decide el agente; purga implícita al despublicar la pista (`RemoveInput`) y corte duro por `Swap(nil)` `[CÓDIGO]` §8.
12. **Audio irretirable:** ≤100 ms ring + 20 ms canal + ≤60 ms jitter (si activo) + tránsito SFU/pion `[PENDIENTE]` + red/trunk `[INFERENCIA]` §8.
13. **Si muere Media/Control/canal:** proceso = media y control juntos; muere ⇒ llamadas perdidas. Redis cae ⇒ no entran llamadas nuevas, las activas siguen. Sala cae ⇒ resume/reconnect o cierre `[CÓDIGO]` `[INFERENCIA]` §9.
14. **Drenaje:** SIGTERM ⇒ salud 503, sin nuevas salientes, espera sin tope a 0 llamadas; entrantes siguen aceptándose si el borde no respeta el 503 `[CÓDIGO]` §9.
15. **Duplicación de recursos:** por llamada: códecs, resamplers, mixer, PeerConnection WebRTC; sin modelos ML `[CÓDIGO]`; cuantía `[PENDIENTE]`.
16. **Métricas para la rodilla:** `livekit_sip_calls_active`, `packets_rtp`, `dur_*`, `stage_dur_sec`, `livekit_node_cpu_load`, `livekit_sip_available`, métricas completas del runtime Go (`collectors.MetricsAll`, `monitor.go:129-130`); por llamada en log cada minuto: latencia E2E in/out (avg/max), drift en ppm, `JumpMixes`, `TimingResets`, `BlockedMixes`, `InputFramesDropped`, pérdidas/reordenamientos del jitter `[CÓDIGO]` `monitor.go:132-333`, `media.go:150-189`, `inbound.go:1227-1229`. Ninguna serie medida en el repo `[PENDIENTE]`.
17. **Reutilizable legal y técnicamente:** Apache-2.0 permite copiar con atribución; técnicamente sólo patrones (Go→Python): ver piezas abajo `[CÓDIGO]` §1, §14.
18. **¿Exigiría reemplazar Asterisk/ai-engine/Modal/proveedores?** Adoptar el proyecto: sí — sustituye AudioSocket/ai-engine por SIP+SFU+Redis y convierte a Golden en agente LiveKit; Modal seguiría pudiendo alojar a Golden pero el puente necesita IP pública y rango UDP (`README.md` §Service Architecture `[DOCUMENTACIÓN]`). Adaptar los patrones: no exige reemplazar nada `[INFERENCIA]`.

### Piezas reutilizables

```text
Segmento: Ingreso por rendezvous en el handshake + recurso de transporte por llamada
Responsabilidad principal: que los datos de una llamada lleguen directamente al contexto que la posee, sin relay
Pattern principal: Rendezvous / Session-owned endpoint (el control anuncia el destino del dato)
Estado: implemented
Confianza: high
Evidencia: pkg/sip/media_port.go:NewMediaPortWith:468-513 (bind :475); media-sdk/rtp/listen.go:bindEvenRange:85-113;
           pkg/sip/media_port.go:GenerateAnswer:792-819 (answer con externalIP+Port :805); inbound.go:handleInvite:1021-1047
Callers verificados: inbound.go:createMediaPort:1314; outbound.go:newCall:127; inbound.go:negotiateMedia:1373
Contrato de entrada/salida: entrada = SDP oferta (o nada → GenerateOffer); salida = SDP respuesta con IP:puerto propio;
                            efecto = socket UDP exclusivo de la llamada
Política de error: puerto ocupado → siguiente par (EADDRINUSE); rango agotado → ErrListenFailed → rejectMedia (500/488)
Ownership: inboundCall/outboundCall vía mediaPort (cerrado sólo por close() idempotente)
Riesgo: en Golden no hay "puerto por llamada" detrás de Modal; el análogo es que el proceso que acepta el WS sea el dueño.
        Requiere PoC de aceptación multiproceso tras @modal.web_server.
Aplicación posible en Golden: MediaWorker acepta el WS directamente (sin relay Python por trama); el handshake
                              (SessionOpened) fija session_id/epoch y el worker; nada más reenvía audio.
```

```text
Segmento: Cadena por trama síncrona en el contexto de la llamada, con un único hand-off acotado
Responsabilidad principal: decodificar, resamplear, procesar y reenviar cada trama sin pasar por un coordinador compartido
Pattern principal: Thread(goroutine)-per-session + Pipes-and-Filters síncrono
Estado: implemented
Confianza: high
Evidencia: pkg/sip/media_pipeline.go:setupInput:158-225, rtpLoop:321-339, rtpReadLoop:341-401;
           media-sdk/rtp/session.go:AcceptStream:76-114, readStream.write:164-187 (cola 10, drop-newest)
Callers verificados: media_pipeline.go:setupOutput:248 (arranca rtpLoop); rtpLoop:337 (arranca rtpReadLoop por SSRC);
                     media_port.go:configure:982-989 (construye la pipeline)
Contrato de entrada/salida: entrada = datagrama RTP; salida = PCM16 48 kHz → Opus → WriteSample; errores contados;
                            50 errores seguidos matan el lector de esa llamada
Política de error: paquete > MTU ignorado; puntero de generación nil → ignorado y contado; cola llena → descarte
Ownership: mediaPortPipeline (una generación por negociación), sustituida bajo p.mu
Riesgo: en Python 3.14t los hilos son del SO; coste de 100+ hilos con ORT (intra_op=1) y contención de refcount/GC
        desconocidos. La métrica Prometheus por paquete es estado compartido en el camino caliente.
Aplicación posible en Golden: dentro del MediaWorker, ejecutar decode→soxr→Silero(→smart-turn) en el contexto de la
                              sesión (hilo propio o loop por worker con pocas sesiones) y elevar al loop de control sólo
                              eventos (SpeechStarted, TurnCompleted). PoC: medir resume/cycle p90 vs. hoy a 25/50/100.
```

```text
Segmento: Buffers post-decisión acotados + corte por switch atómico + purga al retirar la entrada
Responsabilidad principal: limitar el audio irretirable y permitir silenciar/purgar sin protocolo de generación
Pattern principal: Bounded Buffer (drop-oldest) + Switch/Proxy atómico (WriteCloserSwitch)
Estado: implemented
Confianza: high
Evidencia: media-sdk/mixer/mixer.go:30-33 (5 tramas), NewInput:308-329, readSample:353-369, RemoveInput:331-343,
           WriteSample:387-400, mixOnce:195-232 (outchan 1 bloqueante :222-230); media-sdk/ring/buffer.go:Write:136-166;
           media-sdk/media.go:WriteCloserSwitch:168-235; pkg/sip/room.go:493-497; pkg/sip/inbound.go:transferCall:1962-1968
Callers verificados: room.go:NewRoom:281; room.go:NewTrack:787-792 ← room.go:493, inbound.go:playAudio:1915;
                     Swap ← media_port.go:755-765, room.go:794-800, inbound.go:1165-1172/1963/1966, outbound.go:543-560
Contrato de entrada/salida: WriteSample(PCM) nunca bloquea al productor (descarta lo viejo); Close() de la entrada
                            descarta su buffer; Swap(nil) hace que escrituras posteriores se descarten sin error
Política de error: sobreflujo → InputFramesDropped; escritor bloqueado → BlockedMixes (bloquea sólo esa sala)
Ownership: Room (mixer) por llamada; cada entrada pertenece a su goroutine de pista
Riesgo: buffer pequeño ⇒ más sensibilidad al jitter de red (en Golden: tramo engine⇄Modal ahora público);
        un switch no sustituye el flush en ai-engine, que hoy descarta los JSON de GB
Aplicación posible en Golden: bajar el presupuesto de _PacedAudioSender (0,6 s) hacia ~100 ms medido punta a punta;
                              CancelSpeech = Swap(nil)+descartar buffer de la generación activa en el MediaWorker
                              (local, inmediato), sin esperar a Conversación.
```

```text
Segmento: Cuarentena del recurso de transporte al cerrar (anti-obsoletos)
Responsabilidad principal: impedir que datos tardíos de una sesión cerrada contaminen una nueva que reutiliza el recurso
Pattern principal: Quarantine / Tombstone con TTL
Estado: implemented
Confianza: high
Evidencia: pkg/sip/media_port.go:Close:694-720 (go DrainPort :710); pkg/sip/draining_port.go:DrainPort:26-82;
           pkg/config/config.go:52-53, 211-220 (30 s inactividad, 10 min máx; negativos lo desactivan)
Callers verificados: media_port.go:Close:710 (único caller de producción); draining_port_test.go
Contrato de entrada/salida: socket UDP cerrado lógicamente → lectura y descarte hasta inactividad o tope
Política de error: cualquier error de lectura distinto de deadline libera el puerto y lo registra
Ownership: goroutine DrainPort, desacoplada de la llamada ya cerrada
Riesgo: reduce puertos libres con alta rotación (5.001 pares por IP)
Aplicación posible en Golden: tombstones de (session_id, session_epoch) en el MediaWorker durante N s tras
                              SessionClosed; comandos/eventos con epoch viejo se descartan y se cuentan.
```

```text
Segmento: Función de afinidad por carga para PSRPC (CreateSIPParticipantAffinity)
Responsabilidad principal: pujar por llamadas salientes según ocupación (1 - activas/max)
Pattern principal: Load-aware bidding
Estado: not recommended (latente y sin efecto: 0 callers de producción; psrpc rechaza affinity en RPC queue)
Confianza: high
Evidencia: pkg/sip/service.go:317-329; protocol/rpc/sip.psrpc.go:118,126-128 (affinityFunc nil, queue=true);
           psrpc/pkg/server/rpc.go:61-66, 212-220; psrpc/pkg/client/rpc.go:80-89, 289-293; psrpc/pkg/client/options.go:62-65
Callers verificados: sólo pkg/sip/service_test.go:1223-1380
Contrato de entrada/salida: request → float32 en [0,1]
Política de error: n/a (no se ejecuta); el rechazo real es client.go:376-378 tras ganar el claim
Ownership: Service
Riesgo: dar por hecho un enrutamiento por carga que no existe
Aplicación posible en Golden: ninguna directa; lección: admisión antes de aceptar la sesión, con umbral = rodilla medida.
```

### Filas para `MATRIZ_COMPARATIVA.md`

| dimensión | LiveKit SIP |
|---|---|
| media fuera del runtime de control | **sí**: goroutines de media por llamada; el control sólo ve fusibles/atómicos |
| unidad de afinidad | llamada (socket UDP propio fijado por SDP); transferencias por topic `sip_call_id` |
| paralelismo entre núcleos | real, scheduler Go M:N en 1 proceso |
| workers por proceso/tarea | 1 proceso por contenedor; "worker" = goroutines de la llamada |
| audio a través de IPC | no (0 por Redis); sí por WebRTC al SFU (plano de media) |
| orden por sesión | 1 lector por SSRC + mutex de handler + jitter opcional + SeqWriter |
| protección frente a obsoletos | cuarentena de puerto, expiración psrpc, dedup DTMF, caché de rechazos, generación de pipeline atómica; sin epoch/seq en control |
| backpressure | colas por llamada acotadas (10 drop-newest, 100 ms drop-oldest, 1 bloqueante local) |
| barge-in y purga | no en el puente; purga implícita al despublicar; residual ≤~180 ms por diseño |
| supervisión/reinicio | sin supervisor de media; vigilante de cierre 5 min; media timeout |
| drenaje | 503 + sin nuevas salientes + espera sin tope; entrantes no bloqueadas en proceso |
| dominio de fallo | proceso = todas sus llamadas |
| memoria duplicada | códecs/resamplers/mixer/PeerConnection por llamada; sin modelos |
| observabilidad | Prometheus por nodo + stats por llamada en log (latencia, ppm, puntualidad del mixer) |
| complejidad de integración | alta para adoptar (SIP+SFU+Redis); baja para adaptar patrones |
| impacto sobre Asterisk/ai-engine | adoptar: reemplaza AudioSocket/ai-engine; adaptar: ninguno |
| licencia | Apache-2.0 (+ libsoxr LGPL, libopus BSD vía cgo) |
| valor para Golden | **adaptar** |

**Veredicto sobre el coordinador único:** LiveKit SIP **elimina** el coordinador del camino por trama dentro del puente (socket y goroutines por llamada, cero cruces al control) y lo **paraleliza de verdad** mediante el scheduler de Go; no lo replica. Pero eso vale para el puente: en la topología LiveKit completa el audio sí llega por trama al agente Python a través del SFU, así que el sistema entero no separa VAD/turnos del proceso conversacional.
