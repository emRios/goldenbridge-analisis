# 02 — FreeSWITCH: auditoría de canal, media y control externo (event socket)

> Auditoría de segmentos (no del árbol completo). Todas las rutas relativas a `repos/freeswitch/`.
> Etiquetas: `[CÓDIGO]` archivo:símbolo:líneas · `[DOCUMENTACIÓN]` · `[MEDICIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.
> Término de comparación: `../BRIEF_GOLDEN.md` (GoldenBridge `api@08932a6`, ai-engine `d059295`).

---

## 1. Identidad del repositorio y commit

| campo | valor |
|---|---|
| URL | `https://github.com/signalwire/freeswitch.git` |
| branch | `master` (clon superficial, 1 commit en el historial local) |
| commit | `f4778e7f2c98417b2b23c088f7007d6b2c1653fb` |
| fecha del commit | `2026-10-09T16:18:38+03:00` ("[UTILS] FSGET: Add source/build-dep/showsrc actions…", #3217) |
| licencia observada | **MPL-1.1** para `Files: *` — `LICENSE` (formato Debian copyright, l.5-8) y `docs/COPYING` (texto MPL 1.1). Cabecera por archivo "Version: MPL 1.1" en `src/switch_core_session.c:5` y `src/mod/event_handlers/mod_event_socket/mod_event_socket.c:5`. Subcomponentes con otras licencias en `LICENSE` (APR Apache-2.0 l.1205-1215, libs/js MPL/GPL/LGPL l.1257-1264, freetdm GPL-2+ parcial l.1170, `docs/zrtp_agpl-3.0.txt`) |
| submódulos | 0 (`.gitmodules` ausente); dependencias vendorizadas en `libs/` (apr, esl, srtp, libvpx, libyuv, libsndfile, xmlrpc-c, …) |
| mantenimiento visible | commit del mismo día de la auditoría; 8 workflows CI en `.github/workflows/` (build, ci, unit-test, scan-build, windows, macos…); suite `tests/unit/` (incluye `test_mod_event_socket.c`, `switch_ivr_play_say.c`, `switch_vad.c`); `SECURITY.md` con canal de reporte |
| compilado | **No** (por encargo). Ninguna afirmación de capacidad proviene de ejecución |

---

## 2. Dictamen ejecutivo

1. **FreeSWITCH no tiene coordinador único en el camino por trama.** Cada llamada (sesión) tiene **su propio thread del SO**, que hace `poll()` sobre **su propio socket RTP**, decodifica, ejecuta los *media bugs* (ASR, VAD, grabación) **en el mismo thread** y escribe la trama de salida sincrónicamente `[CÓDIGO]` `src/switch_core_session.c:switch_core_session_thread:1721-1772`, `src/switch_rtp.c:rtp_common_read:7744,7954`, `src/switch_core_io.c:switch_core_session_read_frame:172-175,718-752`.
2. **El controlador externo (event socket) nunca ve audio.** Recibe eventos de estado/aplicación y envía comandos que se encolan en una **cola privada por sesión** acotada (256) y despiertan al thread dueño con una señal `SIG_BREAK` `[CÓDIGO]` `src/switch_core_session.c:switch_core_session_queue_private_event:1263-1283`, `src/include/private/switch_core_pvt.h:65-66`.
3. **Barge-in y purga ocurren en el thread dueño, por trama**: la reproducción es *pull* (una trama por tick) y comprueba `CF_BREAK` en cada iteración; al romper destruye sus buffers locales. Lo único no retirable es lo ya escrito en RTP `[CÓDIGO]` `src/switch_ivr_play_say.c:switch_ivr_play_file:1678-1685,1981,2048-2056`.
4. **Paralelismo real**: threads del SO, uno por llamada, en un único proceso; lo compartido (reloj *soft timer*, bus de eventos, tabla hash de sesiones) **no transporta tramas** `[CÓDIGO]` `src/switch_time.c:timer_next:841-899`, `src/switch_event.c:switch_event_dispatch_thread:300-355`.
5. **Debilidades del plano de control** (relevantes para Golden como antipatrones): fan-out de cada evento a todos los listeners bajo un **mutex global**; `send` bloqueante con reintento hasta ~600 s; empuje **bloqueante** a la cola global de despacho; un listener atascado retiene el *read lock* de la sesión y retrasa su destrucción `[CÓDIGO]` + `[INFERENCIA]` (§9).
6. **Valor para Golden: ESTUDIAR / ADAPTAR patrones; DESCARTAR adopción.** Adoptarlo exigiría sustituir o interponer un softswitch frente a Asterisk (alto impacto). Los patrones transferibles son: dueño único por sesión con refcount antes de destruir, cola de comandos por sesión acotada con rechazo local, *break* fuera de banda + flush de comandos encolados, pacing *pull* donde el que marca el ritmo es el que corta, cierre idempotente y estado monótono, correlación por `Application-UUID`, informe de lo reproducido (`playback_ms`) al parar, y política explícita ante muerte del controlador (`linger`/`resume`).
7. **Capacidad**: el repositorio **no contiene benchmark reproducible** de llamadas por núcleo, latencia o rodilla `[PENDIENTE]`. K_loop = 1 llamada por thread por construcción `[CÓDIGO]`; K_worker/K_task/K_flota no derivables del repo.

---

## 3. Diagrama del flujo relevante

```text
           Controlador externo (ESL: inbound :8021 ó outbound por llamada)
             ▲  eventos (texto, Content-Length)          │ comandos (sendmsg/api/bgapi)
             │  Unique-ID / Application-UUID / Job-UUID   │  uuid destino en la cabecera
   ┌─────────┴──────────────── proceso FreeSWITCH ────────┼───────────────────────────────┐
   │  listener thread (1 por conexión)                    ▼                               │
   │   read_packet(): recv 1 byte/iter, poll 20 ms   parse_command()                      │
   │   event_queue[100000] ◄── trypush ── event_handler()   │                             │
   │            ▲            (mutex global, fan-out a TODOS │ queue_private_event()       │
   │            │             los listeners por evento)     │ (cola por sesión, 256)      │
   │   dispatch threads (1..cpu/2+1) ◄─ push BLOQUEANTE ─┐  │ + kill_channel(SIG_BREAK)    │
   │   EVENT_DISPATCH_QUEUE (10000×MAX_DISPATCH)         │  ▼                             │
   │                                                     │ ┌───────── sesión A ─────────┐ │
   │  softtimer_runtime (1 thread global):               │ │ session thread (dueño)     │ │
   │   tick++ y cond_broadcast cada 1 ms ───────────────►│ │  state machine CS_*        │ │
   │                                                     │ │  app activa (park/playback)│ │
   │                                       switch_event_ │ │  loop por trama:           │ │
   │                                       fire() ───────┘ │   read_frame → poll(RTP A) │ │
   │                                                       │   media bugs (ASR/VAD) ────┼─┼─► speech_thread A
   │                                                       │   CF_BREAK? parse_events   │ │   (resultados, baja f.)
   │                                                       │   write_frame → sendto(A)  │ │
   │                                                       └────────────────────────────┘ │
   │                                                       ┌───────── sesión B ─────────┐ │
   │                                                       │ (idéntico, thread propio)  │ │
   │                                                       └────────────────────────────┘ │
   └──────────────────────────────────────────────────────────────────────────────────────┘
         RTP A ⇄ socket UDP A            RTP B ⇄ socket UDP B     (nunca pasan por el ESL)
```

---

## 4. Mapa de procesos, threads, loops y colas

| entidad | cardinalidad | qué hace | cola / límite | evidencia |
|---|---|---|---|---|
| proceso FreeSWITCH | 1 | todo | — | `[INFERENCIA]` (no hay supervisor multiproceso en los segmentos) |
| session thread | **1 por sesión** (pool reutilizable, activo por defecto) | ejecuta `switch_core_session_run` (máquina de estados) y la aplicación activa; lee/escribe tramas | — | `[CÓDIGO]` `src/switch_core_session.c:switch_core_session_thread_launch:1947-1990`, `…thread_pool_launch:1922-1945`; `src/switch_core.c:1827` (`SCF_SESSION_THREAD_POOL` por defecto) |
| pool de threads de sesión | crece bajo demanda (`check_queue`: si `running < busy` crea un thread nuevo); los ociosos mueren tras 5 s | reutiliza threads, **no multiplexa sesiones** | `session_manager.thread_queue` | `[CÓDIGO]` `src/switch_core_session.c:switch_core_session_thread_pool_worker:1778-1830`, `check_queue:1854-1895` |
| colas por sesión | 5 por sesión | `message_queue`, `signal_data_queue`, `event_queue`, `private_event_queue`, `private_event_queue_pri` | **256 cada una**, `trypush` (no bloquea, falla) | `[CÓDIGO]` `src/switch_core_session.c:2499-2503`; `src/include/private/switch_core_pvt.h:65-66` |
| audio write thread | 0 ó 1 por sesión, **sólo** si hay *ptime mismatch* | escribe tramas a ritmo de timer desde `write_fb` | `switch_frame_buffer` de 500 | `[CÓDIGO]` `src/switch_core_media.c:audio_write_thread:7185-7252`, `perform_write:7115-7121`, disparo `:15915-15917` |
| video/text helper threads | 0-1 por sesión con vídeo/texto | fuera de alcance de audio | — | `[CÓDIGO]` `src/switch_core_media.c:7449,7293` |
| speech_thread (ASR) | 1 por `detect_speech` activo | despierta por `cond`, recoge resultados ASR y emite `DETECTED_SPEECH` | entrega a `event_queue` de la sesión (256) | `[CÓDIGO]` `src/switch_ivr_async.c:speech_thread:4986-5141` |
| softtimer_runtime | **1 global** | incrementa `TIMER_MATRIX[x].tick` y hace `cond_broadcast` cada 1 ms | — | `[CÓDIGO]` `src/switch_time.c:1274-1300` |
| event dispatch threads | 1..`cpu/2+1` (mín. 2), crecen si la cola supera `10000×hilos` | `switch_event_deliver` → handlers enlazados (p. ej. `mod_event_socket:event_handler`) | `EVENT_DISPATCH_QUEUE` = `10000 × MAX_DISPATCH`, **push bloqueante** | `[CÓDIGO]` `src/switch_event.c:44,300-355,359-397,697-700` |
| listener thread (ESL) | 1 por conexión TCP (inbound) ó por llamada (outbound `async`); en outbound *sync* corre **dentro del session thread** | lee comandos, serializa y envía eventos | `event_queue` y `log_queue` de **100 000** con `trypush`; mata al listener tras **500** pérdidas | `[CÓDIGO]` `mod_event_socket.c:36-37,500-501,525-555,1208-1520,2634-2866` |
| api/bgapi exec thread | 1 por `bgapi` | ejecuta API y emite `BACKGROUND_JOB` con `Job-UUID` | — | `[CÓDIGO]` `mod_event_socket.c:1580-1582,2382-2383` |

**Loop por trama (camino caliente)**: la aplicación activa (p. ej. `switch_ivr_park`, `switch_ivr_play_file`) itera *una vez por trama* en el session thread: `switch_core_session_read_frame` → endpoint → `switch_core_media_read_frame` → `switch_rtp_zerocopy_read_frame` → `rtp_common_read` (poll sobre el fd de la sesión con timer propio) `[CÓDIGO]` `src/switch_ivr.c:switch_ivr_park:1007,1049`, `src/switch_core_media.c:switch_core_media_read_frame:2778-2831`, `src/switch_rtp.c:7744,7867,7954`.

---

## 5. Ownership por sesión y recursos compartidos

**Dueño**: `switch_core_session_t` (+ `switch_channel_t`) es la unidad; su thread es el único que ejecuta la máquina de estados y la aplicación. Todo lo de la llamada se aloca en el *memory pool* de la sesión (`switch_core_session_alloc`) y se libera en bloque al destruir `[CÓDIGO]` `src/switch_core_state_machine.c:switch_core_session_run:537-550` (comentario de contrato) y `:528-718`.

**Referencias externas con refcount**: cualquier otro thread que quiera tocar la sesión debe hacer `switch_core_session_locate(uuid)`, que toma un **read lock** bajo `session_hash_mutex`; falla si la sesión ya no lo concede `[CÓDIGO]` `src/switch_core_session.c:switch_core_session_perform_locate:121-145`. Al terminar `switch_core_session_run`, el session thread hace `switch_core_session_write_lock` (espera a que **todas** las referencias externas suelten), marca `SSF_DESTROYED` y destruye `[CÓDIGO]` `src/switch_core_session.c:1731-1770`. Existe además `soft_lock` (espera acotada a "entidades externas") `[CÓDIGO]` `:1734-1745`.

**Estado mutable local** (por sesión): estado del canal (`state_mutex`), flags `CF_*`, variables de canal, colas (§4), `bugs` (lista protegida por `bug_rwlock`), media handle/RTP, códecs y resamplers (`codec_read_mutex`, `codec_write_mutex`, `resample_mutex`) `[CÓDIGO]` `src/switch_core_session.c:2485-2503`.

**Compartido (global)**:

| recurso | uso | ¿toca tramas? | evidencia |
|---|---|---|---|
| `session_manager.session_table` + `runtime.session_hash_mutex` | localizar/insertar/borrar por UUID | no | `[CÓDIGO]` `switch_core_session.c:121-145,2411-2438,2505` |
| `TIMER_MATRIX` + softtimer thread | reloj de 1 ms compartido; cada sesión duerme en `cond_wait` sobre la matriz | no (sólo despierta) | `[CÓDIGO]` `switch_time.c:841-899,1274-1300` |
| bus de eventos + dispatch threads | eventos de todas las sesiones | no | `[CÓDIGO]` `switch_event.c:300-397,2025-2052` |
| `globals.listener_mutex` (ESL) | recorre todos los listeners por cada evento | no | `[CÓDIGO]` `mod_event_socket.c:event_handler:274-413` (mutex en `:287`) |
| módulos (códecs, ASR, TTS, endpoints) | interfaces cargadas una vez; *handles* por sesión | — | `[INFERENCIA]` |

`[INFERENCIA]` El `cond_broadcast` de 1 ms despierta a todos los threads que esperan en el mismo intervalo (*thundering herd*); su coste crece con N sesiones, pero no hay medición en el repo `[PENDIENTE]`. Existe `runtime.tipping_point` para dejar de usar `cond` por encima de un número de timers `[CÓDIGO]` `switch_time.c:885-886`.

---

## 6. Contrato de control/IPC

### 6.1 Modos de `mod_event_socket`

| modo | quién conecta | alcance | thread del listener | evidencia |
|---|---|---|---|---|
| **inbound** | el controlador → `127.0.0.1:8021` (auth por password) | cualquier UUID; suscripción por `event`/`filter`; `myevents <uuid>` restringe a una sesión | thread propio por conexión | `[CÓDIGO]` `mod_event_socket.c:listener_run:2634-2866`, `myevents:2063-2120`; conf `conf/autoload_configs/event_socket.conf.xml` |
| **outbound sync** (`socket host:port`) | FreeSWITCH → controlador, **una conexión por llamada** | sólo la sesión que ejecuta la app (salvo `full`) | `listener_run` corre **dentro del session thread** | `[CÓDIGO]` `mod_event_socket.c:socket_function:415-564` (`listener_run(NULL,…)` en `:554`) |
| **outbound async** (`socket host:port async [full]`) | igual | igual | thread propio; el session thread queda en `switch_ivr_park` consumiendo media y ejecutando la cola privada | `[CÓDIGO]` `mod_event_socket.c:525-549` (`switch_ivr_park` en `:542`) |
| `full` | — | outbound con todos los comandos inbound | — | `[CÓDIGO]` `mod_event_socket.c:517-523,2236-2238` |

### 6.2 Framing

Texto estilo cabeceras MIME: líneas `Nombre: valor`, fin de cabecera con línea vacía, cuerpo opcional por `Content-Length` (tope `MAX_CONTENT_LENGTH` = 16 MiB, rechazo fuera de rango) `[CÓDIGO]` `mod_event_socket.c:38,read_packet:1281-1350`. La lectura se hace **byte a byte** (`mlen = 1`) con poll de 20 ms entre intentos `[CÓDIGO]` `:1244,1501-1506`. Eventos salientes: `Content-Length` + `Content-Type: text/event-{plain|json|xml}` `[CÓDIGO]` `:1419-1462`. Respuestas: `Content-Type: command/reply` + `Reply-Text: +OK|-ERR …` `[CÓDIGO]` `:2780-2788`.

### 6.3 Identidad y correlación

| identificador | origen | uso | evidencia |
|---|---|---|---|
| `Unique-ID` (UUID de sesión) | asignado al crear la sesión; duplicados rechazados | cabecera de todo evento de canal; destino de `sendmsg <uuid>` y de `uuid_*` | `[CÓDIGO]` `switch_core_session.c:2411-2416`; filtro `myevents` `mod_event_socket.c:377-383` |
| `Event-UUID` → `Application-UUID` | lo pone el controlador en `sendmsg execute` | se copia a `app_uuid` y vuelve en `CHANNEL_EXECUTE`/`CHANNEL_EXECUTE_COMPLETE` | `[CÓDIGO]` `switch_ivr.c:switch_ivr_parse_event:636-641`; `switch_core_session.c:2855-2857,2953,2978` |
| `Job-UUID` / `Job-Owner-UUID` | `bgapi` | correlación de `BACKGROUND_JOB`; `myevents` deja pasar los del dueño | `[CÓDIGO]` `mod_event_socket.c:380-382,1580-1582,2382` |
| `Controlled-Session-UUID` | aviso de desconexión/linger | identifica la sesión al cerrar | `[CÓDIGO]` `mod_event_socket.c:644-647,1481-1487` |

### 6.4 Semántica de comandos

- `sendmsg <uuid>` con `call-command: execute|hangup|unicast|nomedia|xferext` → evento privado en la cola de la sesión destino; `+OK` **significa "encolado", no "ejecutado"**; la ejecución se confirma con `CHANNEL_EXECUTE_COMPLETE` `[CÓDIGO]` `mod_event_socket.c:2180-2232`, `switch_ivr.c:503-…`.
- En outbound **sin** `async`, `sendmsg` sobre la propia sesión llama `switch_ivr_parse_event` **en línea** (bloquea el listener, que es el session thread) `[CÓDIGO]` `mod_event_socket.c:2216`. `[INFERENCIA]` Mientras un `playback` corre en este modo, el socket no se lee: no se puede mandar `break` por esa misma conexión.
- `event-lock` / `event-lock-pri`: impiden desencolar el siguiente comando hasta terminar el actual `[CÓDIGO]` `switch_ivr.c:530-538`; `switch_core_session.c:dequeue_private_event:1316-1346`.
- Prioridad: `private_event_queue_pri` se drena antes que la normal `[CÓDIGO]` `switch_core_session.c:1323-1335`.
- `api`/`uuid_break`/`uuid_kill`… se ejecutan en el thread del listener (o del `bgapi`) y **mutan flags directamente** en la sesión, sin pasar por la cola `[CÓDIGO]` `mod_commands.c:break_function:4985-5050`.

---

## 7. Ruta de audio y frecuencia de cruces

1. **Entrada**: RTP llega al socket UDP de la sesión. El session thread, dentro de la app activa, llama `switch_core_session_read_frame` → `switch_core_media_read_frame` (trylock de `read_mutex` por tipo: si otro thread ya lee, devuelve CNG) → `rtp_common_read` (timer por sesión + `switch_poll` sobre el fd propio) `[CÓDIGO]` `switch_core_io.c:67-89,172-175`; `switch_core_media.c:2815-2831`; `switch_rtp.c:7744-7960`.
2. **Procesamiento**: decodificación/resampleo en el mismo thread; *media bugs* `SMBF_READ_STREAM` copian la trama a su buffer (`raw_read_buffer`, máx. `MAX_BUG_BUFFER` = 512 KiB) y ejecutan su callback `SWITCH_ABC_TYPE_READ` **en el thread de la sesión** `[CÓDIGO]` `switch_core_io.c:697-752`; `switch_core_media_bug.c:793,902-909`.
3. **ASR**: `speech_callback(READ)` hace `switch_core_asr_feed` por trama en el session thread; sólo cuando `asr_check_results` indica algo, señaliza al `speech_thread` (baja frecuencia) `[CÓDIGO]` `switch_ivr_async.c:5185-5197`.
4. **Salida**: la app genera una trama por tick y llama `switch_core_session_write_frame` → `perform_write` → endpoint → RTP `sendto` en el mismo thread; sólo con *ptime mismatch* se interpone `write_fb` + `audio_write_thread` `[CÓDIGO]` `switch_core_media.c:7104-7121,15828,15915-15917`.

**Cruces del runtime de control por paquete: 0.** El event socket no transporta audio en ningún comando de los segmentos auditados `[CÓDIGO]` (`mod_event_socket.c` completo: sólo eventos/log/comandos). Excepción *opt-in*: `call-command: unicast` hace que el propio session thread envíe cada trama por UDP a un destino externo, **sin** pasar por el listener `[CÓDIGO]` `switch_ivr.c:1076-1146` (`switch_socket_sendto` en `:1141`).

**Frecuencia de cruces de control**: eventos por cambio de estado, por app (EXECUTE/EXECUTE_COMPLETE), por `PLAYBACK_START/STOP`, `DETECTED_SPEECH`, `DTMF`, `TALK/NOTALK`; nunca a 50 fps `[INFERENCIA]` a partir de la lista de `myevents` `mod_event_socket.c:2091-2113`.

---

## 8. Secuencia de barge-in

### 8.1 Barge-in local (sin ida y vuelta al controlador): `play_and_detect_speech`

```text
session thread (sesión A)                                         speech_thread A
 play_file loop, 1 iteración por trama
  ├─ read_frame → media bug speech_callback(READ) → asr_feed ──► (cond_signal si hay resultado)
  │                                                               asr_get_results →
  │                                                               evento DETECTED_SPEECH
  │                                                               "begin-speaking" →
  │◄──────────────── switch_core_session_queue_event(A) ──────────┘
  ├─ input_callback: dequeue_event → begin-speaking → return BREAK
  └─ sale del loop, destruye fh->audio_buffer, PLAYBACK_STOP{Playback-Status: break, playback_ms}
```

`[CÓDIGO]` `switch_ivr_async.c:play_and_detect_input_callback:4814-4850` (`begin-speaking` → `SWITCH_STATUS_BREAK` en `:4843-4845`); `switch_ivr_play_say.c:1719-1729` (dequeue de evento por iteración), `:2013-2056` (stop + destrucción de buffers). Callers verificados: `mod_dptools.c:547`, `mod_httapi.c:600`.

### 8.2 Barge-in remoto: `uuid_break [all|both]`

1. El controlador envía `api uuid_break <uuid> all` → `break_function` en el thread del listener `[CÓDIGO]` `mod_commands.c:4985-5050`; registro `:7648,7729`.
2. `all` → `switch_core_session_flush_private_events`: **vacía la cola de comandos pendientes** (las reproducciones encoladas detrás) `[CÓDIGO]` `mod_commands.c:5029-5034`; `switch_core_session.c:1348-1371`.
3. Si hay *broadcast* activo → `switch_channel_stop_broadcast` (flags `CF_STOP_BROADCAST`+`CF_BREAK`); si no → `CF_BREAK = 2` (`all`) ó `1` `[CÓDIGO]` `switch_channel.h:664`; `mod_commands.c:5038-5042`.
4. El loop de reproducción ve `CF_BREAK` en su siguiente iteración (≤ 1 tick) y sale; con valor 2 marca `done` `[CÓDIGO]` `switch_ivr_play_say.c:1678-1685`; TTS: `switch_ivr_play_say.c:2860-2864` + `switch_core_speech_flush_tts` `:3001`.
5. Cuando el comando entra por la cola (`sendmsg`), además se envía `SIG_BREAK` al endpoint para sacar al thread de un `poll` bloqueado (`switch_rtp_break` levanta `SWITCH_RTP_FLAG_BREAK`) `[CÓDIGO]` `switch_core_session.c:1277`; `mod_sofia.c:sofia_kill_channel:1282-1289`; `switch_rtp.c:switch_rtp_break:5238-5268`.

### 8.3 Qué audio queda y qué se purga

| capa | ¿se purga al break? | evidencia |
|---|---|---|
| comandos encolados (playbacks siguientes) | **sí** con `all`; no sin `all` | `[CÓDIGO]` `mod_commands.c:5029-5034` |
| buffer del archivo/TTS no escrito (`fh->audio_buffer`, `sp_audio_buffer`, buffers del motor TTS) | **sí** | `[CÓDIGO]` `switch_ivr_play_say.c:2048-2056`, `:3001` |
| trama en curso | se termina de escribir la iteración actual | `[INFERENCIA]` del orden check→write del loop `:1678-1981` |
| `write_fb` (sólo ptime mismatch, hasta 500 tramas) | **no** se vacía en `break` (sólo con `(void*)1` al parar el thread) | `[CÓDIGO]` `switch_core_media.c:7232-7236,8315-8316`; `[INFERENCIA]` normalmente contiene pocas tramas porque la producción es *pull* |
| `session->raw_write_buffer` (re-paquetizado) | no en break; sí en `switch_core_session_reset` | `[CÓDIGO]` `switch_core_session.c:switch_core_session_reset:1391-1431` |
| tramas ya enviadas por RTP, en red o en el jitter buffer remoto | **no retirable** | `[INFERENCIA]` |

**Informe de lo reproducido**: al parar se fijan `playback_ms`, `playback_samples`, `playback_last_offset_pos` y se emite `PLAYBACK_STOP{Playback-Status: break|done}` `[CÓDIGO]` `switch_ivr_play_say.c:2013-2040`. `fh->samples_in` cuenta muestras **extraídas del archivo** (incluido pre-buffer), no muestras oídas `[CÓDIGO]` `switch_core_file.c:547,568`; `[INFERENCIA]` sobreestima lo oído en el residuo del buffer de resampleo + red.

---

## 9. Backpressure, fallos, reconexión y drenaje

### 9.1 Backpressure

| punto | política | ¿aísla por llamada? | evidencia |
|---|---|---|---|
| colas por sesión (256) | `trypush`; si llena, `sendmsg` responde `-ERR memory error` | **sí** (sólo afecta a esa sesión) | `[CÓDIGO]` `switch_core_session.c:1263-1283`; `mod_event_socket.c:2210-2224` |
| `event_queue` del listener (100 000) | `trypush`; descarta y cuenta `lost_events`; resetea el contador al primer éxito; **> 500 seguidos ⇒ `kill_listener`** | por listener | `[CÓDIGO]` `mod_event_socket.c:385-408` |
| `event_handler` | itera **todos** los listeners bajo `globals.listener_mutex` por cada evento | **no**: coste O(listeners) por evento global; con outbound (1 listener por llamada) el agregado crece ~N² | `[CÓDIGO]` `:287-412`; `[INFERENCIA]` del bucle |
| `EVENT_DISPATCH_QUEUE` | `switch_queue_push` **bloqueante**; crece en hilos hasta `cpu/2+1` | **no**: si se llena, bloquea a cualquier thread que haga `switch_event_fire`, incluidos session threads | `[CÓDIGO]` `switch_event.c:359-397,697-700`; `[INFERENCIA]` |
| `switch_socket_send` | en EAGAIN reintenta cada 10 ms hasta 60 000 veces (~600 s) | **no** si se llama desde el thread de dispatch (`kill_listener → send_disconnect` dentro de `event_handler`, con el mutex global tomado) | `[CÓDIGO]` `switch_apr.c:764-787`; `mod_event_socket.c:400-403,631-674`; `[INFERENCIA]` posible bloqueo del despacho global |
| media (RTP) | sin cola: *pull* a ritmo de timer; una sesión lenta sólo se retrasa a sí misma | **sí** (salvo CPU) | `[CÓDIGO]` `switch_ivr_play_say.c:1910-1981`; `[INFERENCIA]` |
| creación de sesiones | `sessions-per-second` (throttle) y `max-sessions`; si falla `thread_create`, baja el límite automáticamente (`sess_count - 10` si > 110) | admisión global | `[CÓDIGO]` `switch_core_session.c:2419-2436,1834-1852` |

### 9.2 Muerte o atasco de cada componente

| fallo | efecto | evidencia |
|---|---|---|
| **muere el controlador (outbound)** | `read_packet` falla → `listener_run` sale → limpia `CF_CONTROLLED` → `switch_ivr_park` termina (condición de bucle) → la app `socket` retorna → el dialplan sigue; si no hay más apps, **cuelga con NORMAL_CLEARING**. Con `resume`/`socket_resume=true`, pasa a `CS_RESET`/`CS_EXECUTE` y continúa el dialplan | `[CÓDIGO]` `mod_event_socket.c:2760-2770,2822-2826,2846`; `switch_ivr.c:1007,1210`; `switch_core_state_machine.c:380-384` |
| **muere el controlador (inbound)** | el listener se quita; las llamadas siguen en lo que estaban (p. ej. *park* indefinido salvo `park_timeout`) | `[CÓDIGO]` `mod_event_socket.c:2760-2770,2796-2800`; `switch_ivr.c:986-997,1071-1073` |
| **controlador vivo pero no drena** | el listener queda en `switch_socket_send` (hasta ~600 s); la cola llega a 100 000; tras 500 pérdidas seguidas, `kill_listener` cierra el socket. Mientras tanto el listener **retiene el read lock de la sesión** (outbound), y el session thread, al terminar, se bloquea en `switch_core_session_write_lock` | `[CÓDIGO]` `switch_apr.c:770-783`; `mod_event_socket.c:400-403,2655-2662`; `switch_core_session.c:1750`; `[INFERENCIA]` destrucción diferida y thread retenido |
| **cuelga la llamada con listener outbound** | sin `linger`: el listener cierra al detectar `channel_down`; con `linger [s]`: envía `text/disconnect-notice` con `Content-Disposition: linger` y sigue entregando eventos finales hasta el timeout | `[CÓDIGO]` `mod_event_socket.c:1466-1499,2431-2455` |
| **muere el proceso FreeSWITCH** | todas sus llamadas caen (un dominio de fallo) | `[INFERENCIA]`; recuperación (`CF_RECOVERED`) fuera de segmento `[PENDIENTE]` |
| **sesión sin media** | `media_timeout` por canal | `[CÓDIGO]` `switch_core_media.c:check_media_timeout_params:2730-2760` |

### 9.3 Hangup y cierre idempotente

- `switch_channel_perform_hangup`: "one per customer" — `OCF_HANGUP` bajo `state_mutex`; la segunda llamada devuelve el estado sin efecto `[CÓDIGO]` `switch_channel.c:3389-3404`.
- Estado monótono: no se puede retroceder desde `CS_HANGUP` `[CÓDIGO]` `switch_channel.c:switch_channel_perform_set_state:2437`.
- `switch_core_session_hangup_state`: guarda `SSF_HANGUP`, quita todos los media bugs, para *broadcast*, fija causa y timestamps, ejecuta hooks `[CÓDIGO]` `switch_core_state_machine.c:793-863`.
- Destrucción: `media_bug_remove_all` → `soft_lock` → `write_lock` → `SSF_DESTROYED` → `destroy`, que **vacía la cola privada** `[CÓDIGO]` `switch_core_session.c:1732-1770,1541`.

### 9.4 Transferencia

`switch_ivr_session_transfer` marca `CF_TRANSFER`, cambia el perfil y pasa a `CS_ROUTING` `[CÓDIGO]` `switch_ivr.c:2256,2296`. `switch_channel_ready()` es falso mientras `CF_TRANSFER` o haya cambio de estado pendiente, así que **todo loop de media en curso sale en su siguiente iteración** `[CÓDIGO]` `switch_channel.c:switch_channel_test_ready:2294-2297`. `[INFERENCIA]` Es una invalidación de "generación" implícita; **no** vacía la cola privada: comandos encolados antes de la transferencia pueden ejecutarse después.

### 9.5 Drenaje

- `fsctl pause [inbound|outbound]` → `SCF_NO_NEW_INBOUND/OUTBOUND_SESSIONS` `[CÓDIGO]` `switch_core.c:2776-2795,3014,3019`.
- `shutdown elegant` → espera en bucle a `session_count == 0`; **no bloquea sesiones nuevas por sí solo** (sólo `asap` pone `SCF_NO_NEW_SESSIONS`) `[CÓDIGO]` `switch_core.c:2819-2845`. Drenaje correcto = `pause` + `elegant` `[INFERENCIA]`.

---

## 10. Escalado y uso real de núcleos

- **Mecanismo**: threads nativos del SO, uno por sesión, planificados por el kernel; sin afinidad explícita en los segmentos auditados `[CÓDIGO]` §4. El trabajo por trama no comparte lock global salvo: `session_hash_mutex` (sólo en *locate*), `TIMER_MATRIX` (cond de 1 ms), `bug_rwlock` (por sesión) `[CÓDIGO]`.
- **K_loop** = 1 llamada: cada session thread es su propio "loop" `[CÓDIGO]`. No es comparable con el loop asyncio de Golden: aquí no existe un loop que sirva a muchas sesiones.
- **K_worker** (un proceso FreeSWITCH) = Σ threads de sesión, limitado por `max-sessions`, `sessions-per-second`, CPU y el coste agregado del reloj/eventos. **Sin medición reproducible en el repo** `[PENDIENTE]`. El umbral 110 de `thread_launch_failure` es una heurística de degradación, no una capacidad `[CÓDIGO]` `switch_core_session.c:1840`.
- **K_task / K_flota**: el repo no trae distribución entre procesos/máquinas; el reparto de llamadas entre instancias es externo (SBC/proxy SIP) `[INFERENCIA]`; no auditado.
- Respecto a la pregunta central: FreeSWITCH **elimina** el coordinador del camino por trama (cada trama la mueve el thread dueño), y **paraleliza de verdad** (threads del SO). El precio es un thread por llamada en C nativo, con buffers y timers propios.

---

## 11. Patrones y antipatrones

**Implementados (transferibles)**

| patrón | dónde | por qué importa a Golden |
|---|---|---|
| Dueño único por sesión + referencias con read lock; destrucción espera a `write_lock` | `switch_core_session.c:121-145,1750` | cierre seguro con referencias externas (Conversación, tareas STT/TTS) |
| Cola de comandos por sesión acotada con `trypush` y rechazo local | `switch_core_session.c:1263-1283` | backpressure limitado a la llamada |
| Cola prioritaria + `event-lock` | `switch_core_session.c:1316-1346`; `switch_ivr.c:530-538` | `CancelSpeech` antes que `Speak` |
| *Break* fuera de banda (flag) + flush opcional de comandos encolados | `mod_commands.c:4985-5050` | `CancelSpeech` no debe esperar en la cola FIFO |
| Despertar del thread dueño con señal al endpoint (`SIG_BREAK` → `switch_rtp_break`) | `switch_core_session.c:1277`; `mod_sofia.c:1282-1289` | el dueño no debe dormir sobre I/O ignorando comandos |
| Pacing *pull*: una trama por tick, chequeo de break por iteración, destrucción de buffers al salir | `switch_ivr_play_say.c:1669-1981,2048-2056` | lo no enviado se descarta en el mismo sitio que marca el ritmo |
| Informe de lo reproducido al parar (`playback_ms`, `Playback-Status`) | `switch_ivr_play_say.c:2013-2040` | base de `SpeechInterrupted{spoken_text}` sin que Media mute el historial |
| Correlación de generación por `Event-UUID → Application-UUID` | `switch_ivr.c:636-641`; `switch_core_session.c:2953,2978` | equivalente directo de `speech_id` |
| Hangup idempotente + estado monótono | `switch_channel.c:3389-3404,2437` | `CloseSession` repetido / cruzado |
| Trabajo por trama en callback del dueño, resultados en thread de baja frecuencia → evento semántico | `switch_ivr_async.c:4986-5213` | VAD/STT dentro de Media; Conversación sólo recibe `TurnCompleted` |
| Política explícita ante muerte del controlador (`linger`, `resume`, `park_timeout`) | `mod_event_socket.c:1466-1499,2822-2826`; `switch_ivr.c:986-997` | define qué hace Media si cae Conversación |

**Latentes**: priorización `SMF_PRIORITY` en `switch_ivr_broadcast` (`switch_ivr_async.c:5716,5746`) — prioridad por comando ya disponible pero no expuesta como contrato general `[CÓDIGO]`.

**Antipatrones (no copiar)**

| antipatrón | evidencia |
|---|---|
| Fan-out de eventos bajo mutex global, iterando todos los listeners por evento | `mod_event_socket.c:287-412` |
| `send` con reintento ~600 s en un socket no bloqueante, invocable desde el thread de despacho global | `switch_apr.c:764-787`; `mod_event_socket.c:400-403,631-659` |
| Push bloqueante a la cola global de eventos desde threads de media | `switch_event.c:393` |
| Lectura de socket byte a byte | `mod_event_socket.c:1244` |
| `+OK` = "encolado"; ACK de ejecución en otro evento (fácil de malinterpretar) | `mod_event_socket.c:2210-2224` |
| Sin época de sesión: comandos encolados pre-transferencia sobreviven | `switch_ivr.c:2163-2315` (no hay flush) |
| Contrato invertido entre `uuid_break all` (flush si `all`) y la app `break` (flush si **no** es `all`) | `mod_commands.c:5029-5034` vs `mod_dptools.c:1133-1135` |
| Listener outbound sync dentro del session thread: no puede recibir `break` mientras ejecuta | `mod_event_socket.c:554,2216` |

---

## 12. Callers verificados de las piezas propuestas como referencia

| pieza | callers verificados |
|---|---|
| `switch_core_session_queue_private_event` | `mod_event_socket.c:2210,2221`; `mod_erlang_event/handle_msg.c:914`; `mod_callcenter.c:2917`; `switch_core_session.c:2710`; `switch_ivr_async.c:5716,5746` (9 apariciones) |
| `switch_core_session_flush_private_events` | `mod_commands.c:5030,5032` (`uuid_break all`); `mod_dptools.c:1134` (app `break`); `mod_fifo.c:2812,2844`; `mod_callcenter.c:3207,3249`; `switch_ivr.c:618`; `switch_core_session.c:1541` (destroy) |
| `break_function` (API) | registrado como `break` y `uuid_break` `mod_commands.c:7648,7729` |
| `switch_ivr_play_and_detect_speech` | `mod_dptools.c:547`; `mod_httapi.c:600` |
| `switch_core_media_bug_add("detect_speech")` | `switch_ivr_async.c:5414-5415` (desde `switch_ivr_detect_speech:5457`) |
| `switch_channel_perform_hangup` | macro `switch_channel_hangup`, usada p. ej. en `switch_core_state_machine.c:341,384`; `switch_ivr.c:1072` |
| `event_handler` (ESL) | enlazado a `SWITCH_EVENT_ALL` en `mod_event_socket.c:1192`; invocado por `switch_event_deliver` desde `switch_event_dispatch_thread` `switch_event.c:341` |
| `switch_core_session_thread` | `switch_core_session_thread_launch:1977` y `…thread_pool_launch:1939`; launch llamado también desde `switch_channel_perform_hangup` si el thread no arrancó `switch_channel.c:3443-3445` |

---

## 13. Aplicabilidad a GoldenBridge

### 13.1 Respuestas a las 18 preguntas obligatorias

1. **Unidad de ownership**: la **sesión/canal** (una pata de llamada), con su thread, su pool de memoria y sus colas `[CÓDIGO]` §5.
2. **Quién recibe cada paquete de audio**: el **session thread** de esa llamada, por `poll` sobre su propio socket RTP, dentro de la app activa `[CÓDIGO]` `switch_rtp.c:7954`.
3. **Cruces del runtime de control por paquete**: **cero** `[CÓDIGO]` §7.
4. **¿Loop único compartido?** No para tramas. Compartidos: softtimer (tick 1 ms + broadcast), dispatch de eventos, mutex de listeners ESL, hash de sesiones; ninguno procesa audio `[CÓDIGO]` §4-5.
5. **Varios núcleos**: threads del SO, uno por sesión, en un proceso `[CÓDIGO]` §4.
6. **Asignación y afinidad**: la llamada nace en el proceso que recibe/origina la señalización y su thread la atiende hasta destruirse; no migra `[CÓDIGO]` `switch_core_session.c:1922-1990`; reparto entre procesos: externo `[INFERENCIA]`.
7. **Estado compartido vs local**: local = canal, variables, colas, bugs, media/RTP, códecs; compartido = tabla de sesiones, reloj, bus de eventos, módulos `[CÓDIGO]` §5.
8. **Protocolo de control**: TCP texto con cabeceras + `Content-Length` (ESL inbound/outbound); internamente, colas de APR por sesión `[CÓDIGO]` §6.
9. **Orden, correlación, ACK, dedup, tardíos**: FIFO por cola de sesión (+ cola prioritaria); correlación por `Unique-ID`, `Application-UUID`, `Job-UUID`; ACK `+OK` = encolado, ejecución por `CHANNEL_EXECUTE_COMPLETE`; **sin dedup**; tardíos: comandos a UUID inexistente → `-ERR invalid session id`; sin época, los encolados sobreviven a una transferencia `[CÓDIGO]` §6.3-6.4, §9.4.
10. **Backpressure sin afectar a otras llamadas**: sí en el camino de media y en las colas por sesión; **no** en el plano de eventos (mutex global, push bloqueante, send de 600 s) `[CÓDIGO]` §9.1.
11. **Dónde ocurre el barge-in y la purga**: en el **session thread**, en el loop de reproducción; purga de buffers locales al salir y de comandos encolados con `all` `[CÓDIGO]` §8.
12. **Audio no retirable**: lo ya escrito al RTP (≤ 1 trama de la iteración en curso), lo que está en red y en el jitter buffer remoto; con ptime mismatch, lo que quede en `write_fb` `[CÓDIGO]`+`[INFERENCIA]` §8.3.
13. **Muerte de Media/Control/canal**: Media muere ⇒ caen todas sus llamadas; Control muere ⇒ outbound: sigue dialplan o cuelga (o `resume`); inbound: la llamada sigue en su estado; canal atascado ⇒ drop + kill tras 500 pérdidas, con riesgo de bloqueo largo en `send` `[CÓDIGO]` §9.2.
14. **Drenaje**: `fsctl pause` + `shutdown elegant` (espera a 0 sesiones) `[CÓDIGO]` §9.5.
15. **Duplicación de modelos/recursos**: un proceso; módulos cargados una vez, *handles* por sesión (ASR/TTS/códec) `[INFERENCIA]`.
16. **Métricas para la rodilla**: `HEARTBEAT` (Session-Count, Session-Per-Sec, picos, Idle-CPU) `[CÓDIGO]` `switch_core.c:send_heartbeat:88-127`; estadísticas RTP por llamada (jitter, `in_skip_packet_count`, `in_flaw_total`) `[CÓDIGO]` `switch_core_media.c:1885-1896`; `playback_ms`. **No** hay métrica de retraso de ciclo por sesión ni de lag del timer: no permite atribuir una rodilla `[INFERENCIA]`.
17. **Reutilizable legal y técnicamente**: el **código** es MPL-1.1 (copyleft por archivo: modificaciones a esos archivos deben publicarse) y C nativo acoplado a APR y al core; no es copiable a Python de forma útil. Los **patrones** (§11) sí son reutilizables sin restricción `[INFERENCIA]`.
18. **¿Exigiría reemplazar Asterisk/ai-engine/Modal/proveedores?** Adoptar FreeSWITCH = sustituir Asterisk o interponer un softswitch (y reescribir ai-engine como cliente ESL o módulo). **Alto impacto; no recomendado** `[INFERENCIA]`.

### 13.2 Pregunta específica: ¿qué garantías telefónicas debe conservar un MediaWorker de Golden y cuáles ya pertenecen a Asterisk/ai-engine?

**Debe conservar el MediaWorker** (porque es el dueño del audio que genera y del que consume):

| garantía | equivalente FreeSWITCH | aplicación en Golden |
|---|---|---|
| G1. Un único dueño por sesión; referencias externas con refcount; destrucción sólo cuando nadie la referencia | `locate` + read lock; `write_lock` antes de destruir | `MediaSession` dueña de WS, VAD/SmartTurn, STT/TTS; Conversación sólo tiene un *handle* por `session_id` |
| G2. Cierre idempotente y estado monótono | `OCF_HANGUP`; no retroceder desde `CS_HANGUP` | `CloseSession` y cierre del WS convergen en un único `closing→closed` |
| G3. Cola de comandos por sesión acotada con rechazo local | colas de 256 con `trypush` | comandos C→M por sesión con tope; `MediaError{queue_full}` sólo para esa sesión |
| G4. Cancelación fuera de banda y flush de lo encolado | `uuid_break all` (flag + flush) | `CancelSpeech` salta la FIFO, invalida el `speech_id` vigente y descarta `SpeakChunk` pendientes |
| G5. El que marca el ritmo es el que corta | loop *pull* de `play_file` | `_PacedAudioSender` debe comprobar la cancelación por trama y destruir su buffer; el residuo queda acotado por su presupuesto (hoy 0,6 s en GB, `BRIEF_GOLDEN.md`) |
| G6. Informar lo realmente emitido al parar | `PLAYBACK_STOP{break, playback_ms}` | `SpeechInterrupted{speech_id, emitted_ms, spoken_text}` → Conversación trunca el historial; Media **no** lo muta |
| G7. Correlación por generación | `Application-UUID` | `speech_id` (y `session_epoch`) en todo evento de reproducción |
| G8. Política ante muerte de Conversación | `linger`/`resume`/`park_timeout` | decidir y documentar: silencio + timeout + cierre, o mensaje de cortesía |
| G9. Barge-in local sin ida y vuelta | `play_and_detect_speech` | VAD/SmartTurn en Media disparan la cancelación local antes de notificar a Conversación |
| G10. Timeout de media | `media_timeout` | detectar WS sin audio entrante y cerrar la sesión |

**Ya pertenecen a Asterisk / ai-engine** (fuera del alcance del MediaWorker):

- Señalización SIP, respuesta/colgado real de la pata, causa de colgado, CDR, transferencia/bridge, DTMF, negociación de códec, RTP y jitter buffer de red — Asterisk (proveedor) `[INFERENCIA]` a partir de la topología de `BRIEF_GOLDEN.md`.
- El búfer de reproducción **posterior** a GB (jitter de ~300 ms del engine) y su flush; la reconexión del WS — ai-engine. Hoy el engine descarta los JSON de GB y no hace flush para este proveedor (`BRIEF_GOLDEN.md`, hechos de `d059295`). Lección FreeSWITCH: el flush sólo funciona en la capa que posee el búfer (§8.3). El MediaWorker **no puede** garantizar el corte de lo que ya entregó al engine; sólo puede (a) minimizar su presupuesto comprometido y (b) emitir el evento correlacionado para que el engine haga flush cuando se active la costura `ProviderBargeIn → _apply_barge_in_action`.

### 13.3 Qué NO se traduce

- "Un thread del SO por llamada" es la forma de FreeSWITCH de quitar el coordinador; en Golden (Python 3.14t) sería una **opción a medir**, no una conclusión: implica 100 threads con su propio pacing y su propia sesión ORT, y entra en tensión con `GB_ORT_INTRA_OP=1`/pool `vad` de 4 `[INFERENCIA]`; requiere PoC con métrica punta a punta (regla 3 del proyecto).
- El ESL como protocolo: Golden ya tiene WS por llamada; copiar el texto de cabeceras no aporta.

---

## 14. Riesgos de adopción o copia

| riesgo | detalle |
|---|---|
| Licencia | MPL-1.1 por archivo: copiar/modificar archivos obliga a publicar esas modificaciones; vendorizados con licencias propias (APR Apache-2.0, partes GPL/AGPL en `libs/`/`docs/`) |
| Sustitución de stack | introducir FreeSWITCH es reemplazar o duplicar Asterisk (alto impacto, fuera de la arquitectura decidida) |
| Falsa transferencia del modelo de threads | el beneficio proviene de C nativo + un thread por llamada; no hay medición en el repo que permita extrapolarlo a Python |
| Copiar el plano de eventos | mutex global, push bloqueante, `send` de 600 s, lectura byte a byte: patrones que **reintroducen** acoplamiento entre llamadas |
| Semántica de ACK | `+OK` ≠ ejecutado; si Golden copia esto debe separar `accepted` de `completed` |
| Ausencia de época | FreeSWITCH no invalida comandos encolados al transferir; Golden necesita `session_epoch`/`speech_id` explícitos |
| Contratos inconsistentes | `break` vs `uuid_break all` con lógica de flush opuesta (`mod_dptools.c:1133` vs `mod_commands.c:5029`) — evidencia de que el contrato de cancelación debe especificarse y probarse |

---

## 15. Lecciones concretas que pasan a la matriz comparativa

**L1 — Cancelación fuera de banda + flush de lo encolado.** `uuid_break all` vacía la cola de comandos y levanta un flag que el loop de reproducción comprueba por trama `[CÓDIGO]` `mod_commands.c:5029-5042`; `switch_ivr_play_say.c:1678-1685`. ⇒ `CancelSpeech` no debe ir en la FIFO detrás de `SpeakChunk`.

**L2 — El que marca el ritmo es el dueño del corte, y reporta lo emitido.** Producción *pull* por tick, buffers destruidos al salir, `PLAYBACK_STOP{break, playback_ms}` `[CÓDIGO]` `switch_ivr_play_say.c:1981,2013-2056`. ⇒ `SpeechInterrupted` debe llevar `emitted_ms` medido por el pacer; Conversación trunca su historial.

**L3 — Trabajo por trama en el dueño; resultados por evento a baja frecuencia.** `asr_feed` en el callback del media bug del session thread; `speech_thread` sólo despierta con resultados y emite `DETECTED_SPEECH` `[CÓDIGO]` `switch_ivr_async.c:5185-5197,4986-5141`. ⇒ Frontera Media→Conversación por eventos semánticos, nunca por trama.

**L4 — Backpressure por sesión con rechazo local; nunca bloqueo global.** Colas de 256 con `trypush` `[CÓDIGO]` `switch_core_session.c:1263-1283`; contraejemplo: `switch_event.c:393` y `mod_event_socket.c:287-412`.

**L5 — Ciclo de vida con refcount e idempotencia.** `locate` + read lock, `write_lock` antes de destruir, `OCF_HANGUP` `[CÓDIGO]` `switch_core_session.c:121-145,1750`; `switch_channel.c:3389-3404`.

**L6 — Política explícita ante muerte del controlador.** `linger`/`resume`/`park_timeout` `[CÓDIGO]` `mod_event_socket.c:1466-1499,2822-2826`.

### Piezas reutilizables (plantilla)

```text
Segmento: Cancelación de reproducción (uuid_break / CF_BREAK)
Responsabilidad principal: interrumpir la reproducción en curso y, opcionalmente, descartar los comandos de media encolados
Pattern principal: Command fuera de banda (flag de cancelación) + purga de cola (flush)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] src/mod/applications/mod_commands/mod_commands.c:break_function:4985-5050; src/switch_core_session.c:switch_core_session_flush_private_events:1348-1371; src/include/switch_channel.h:664; src/switch_ivr_play_say.c:switch_ivr_play_file:1678-1685; :switch_ivr_speak_text_handle:2860-2864,3001
Callers verificados: API "uuid_break"/"break" (mod_commands.c:7648,7729); app "break" (mod_dptools.c:1127-1141); flush también desde mod_fifo.c:2812,2844, mod_callcenter.c:3207,3249, switch_core_session.c:1541
Contrato de entrada/salida: entrada uuid + {all|both}; salida "+OK"/"-ERR No such channel"; efecto observable: PLAYBACK_STOP{Playback-Status: break}
Política de error: UUID inexistente → -ERR; si no hay reproducción activa el flag queda puesto y lo consume la siguiente lectura de CF_BREAK [INFERENCIA]
Ownership: el flag lo escribe cualquier thread; sólo el session thread lo consume y limpia
Riesgo: contrato inconsistente break vs uuid_break all (mod_dptools.c:1133 vs mod_commands.c:5029); flag "pegajoso" puede cortar una reproducción posterior si no se correlaciona con generación
Aplicación posible en Golden: CancelSpeech{speech_id} fuera de la FIFO de comandos: marca la generación cancelada, el pacer la comprueba por trama, se descartan SpeakChunk con speech_id cancelado; correlación obligatoria por speech_id para evitar el flag pegajoso
```

```text
Segmento: Cola de comandos privada por sesión (private_event_queue / _pri)
Responsabilidad principal: entregar comandos de control al thread dueño en orden FIFO con prioridad, sin bloquear al emisor
Pattern principal: Active Object (mailbox por sesión) + Priority Queue acotada
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] src/switch_core_session.c:switch_core_session_queue_private_event:1263-1283; :switch_core_session_dequeue_private_event:1316-1346; :2502-2503; src/include/private/switch_core_pvt.h:65; src/switch_ivr.c:switch_ivr_parse_next_event:796-810, :switch_ivr_parse_all_events:913-944, event-lock:530-538
Callers verificados: mod_event_socket.c:2210,2221 (sendmsg); mod_erlang_event/handle_msg.c:914; mod_callcenter.c:2917; switch_core_session.c:2710; switch_ivr_async.c:5716,5746 (broadcast con SMF_PRIORITY)
Contrato de entrada/salida: entrada evento con call-command, Event-UUID opcional, event-lock opcional; salida SUCCESS=encolado (+OK) / FALSE (-ERR memory error); ejecución confirmada por CHANNEL_EXECUTE_COMPLETE{Application-UUID}
Política de error: trypush sobre cola de 256: si llena, el comando se rechaza (no bloquea, no afecta a otras sesiones)
Ownership: escribe cualquier thread; desencola y ejecuta sólo el session thread (parse_all_events en los loops de media y del state machine)
Riesgo: +OK no significa ejecutado; sin época, los comandos sobreviven a una transferencia; sin dedup
Aplicación posible en Golden: inbox de comandos C→M por MediaSession, acotado, con carril prioritario para CancelSpeech/CloseSession, ACK en dos fases (accepted / completed{speech_id}), descarte de comandos con session_epoch antiguo
```

```text
Segmento: Reproducción paced pull con informe de lo emitido (switch_ivr_play_file / speak_text_handle)
Responsabilidad principal: generar una trama por tick, comprobar cancelación por iteración, destruir buffers no enviados y reportar lo reproducido
Pattern principal: Template Method del loop de media (check → parse events → produce → write → tick)
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] src/switch_ivr_play_say.c:1669-1687 (break + parse events), 1719-1729 (input_callback por iteración), 1910-1981 (timer/read + write_frame), 2013-2040 (playback_ms + PLAYBACK_STOP), 2048-2056 (destrucción de buffers); src/switch_core_file.c:547,568 (samples_in)
Callers verificados: switch_ivr_play_and_detect_speech (switch_ivr_async.c:4878) llamado desde mod_dptools.c:547 y mod_httapi.c:600; apps playback/speak de mod_dptools [INFERENCIA de nombre, no seguido línea a línea]
Contrato de entrada/salida: entrada archivo/texto + args(input_callback, dmachine); salida SUCCESS|BREAK|FALSE + variables playback_ms/playback_samples + evento PLAYBACK_STOP{Playback-Status}
Política de error: canal no listo (hangup/transfer) → sale; write_frame falla → sale; timeout_samples → TIMEOUT
Ownership: todo el estado (fh, buffers, timer) es local al session thread
Riesgo: samples_in mide lo extraído del archivo, no lo oído; write_fb (ptime mismatch) no se vacía en break
Aplicación posible en Golden: _PacedAudioSender como único punto de corte: comprobar cancelación por trama, descartar su buffer, emitir PlaybackFinished/SpeechInterrupted{speech_id, emitted_ms} medidos en el pacer; el truncado de historial lo hace Conversación con ese dato
```

```text
Segmento: Media bug ASR (detect_speech): feed por trama en el dueño, resultados en thread de baja frecuencia
Responsabilidad principal: alimentar el reconocedor con cada trama sin salir del thread dueño y publicar sólo eventos semánticos
Pattern principal: Observer sobre el camino de media + Producer/Consumer con cond variable
Estado: implemented
Confianza: high
Evidencia: [CÓDIGO] src/switch_ivr_async.c:speech_callback:5143-5213 (asr_feed 5187, signal 5191-5196), :speech_thread:4986-5141 (DETECTED_SPEECH begin-speaking/partial/detected/closed), bug add 5414-5415; src/switch_core_io.c:697-752 (ejecución de bugs en read_frame)
Callers verificados: switch_ivr_detect_speech (switch_ivr_async.c:5457); consumidor del evento: play_and_detect_input_callback (switch_ivr_async.c:4814-4850)
Contrato de entrada/salida: entrada tramas leídas; salida eventos DETECTED_SPEECH en la event_queue de la sesión (y opcionalmente al bus global)
Política de error: asr_feed falla → el callback devuelve FALSE y el bug se poda; cola de sesión llena → evento al bus con delivery-failure=true
Ownership: el bug pertenece a la sesión (lista bajo bug_rwlock); el speech_thread toma read lock de la sesión y se une en CLOSE
Riesgo: la cola de eventos de sesión (256) puede llenarse si nadie la consume; el thread ASR por sesión multiplica threads
Aplicación posible en Golden: VAD/SmartTurn/feed STT por trama dentro de MediaWorker; TurnCompleted/SpeechStarted como únicos cruces a Conversación
```

### Fila para la matriz (resumen)

| dimensión | FreeSWITCH |
|---|---|
| media fuera del runtime de control | sí, total: el ESL no transporta audio |
| unidad de afinidad | sesión/canal ↔ session thread, toda la vida de la llamada |
| paralelismo entre núcleos | threads nativos del SO, 1 por llamada, 1 proceso |
| workers por proceso/tarea | N threads de sesión por proceso; sin multiproceso interno |
| audio a través de IPC | no (salvo `unicast`/forks opt-in, emitidos por el propio session thread) |
| orden por sesión | FIFO por cola de sesión + cola prioritaria; `event-lock` |
| protección frente a obsoletos | parcial: `Application-UUID`, `CF_TRANSFER`/state-pending cortan loops; sin época ni dedup |
| backpressure | por sesión (256, trypush) y por listener (100 000, drop+kill a 500); plano de eventos con acoplamientos globales |
| barge-in y purga | en el dueño, por trama; `uuid_break all` = flag + flush; buffers locales destruidos |
| supervisión/reinicio | no hay supervisor de procesos; `thread_launch_failure` baja el límite |
| drenaje | `fsctl pause` + `shutdown elegant` |
| dominio de fallo | el proceso entero |
| memoria duplicada | no (un proceso; handles por sesión) |
| observabilidad | HEARTBEAT, stats RTP por llamada, `playback_ms`; sin métrica de lag de ciclo |
| complejidad de integración | muy alta (softswitch completo, C, MPL) |
| impacto sobre Asterisk/ai-engine | adopción = reemplazar/duplicar Asterisk y reescribir engine |
| licencia | MPL-1.1 (+ vendorizados heterogéneos) |
| valor para Golden | **estudiar/adaptar patrones; descartar adopción** |
