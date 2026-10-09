# Dictamen — adoptar, adaptar o construir

Base: los ocho informes de `informes/`, `MATRIZ_COMPARATIVA.md` y `PROPUESTA_APLICABLE_GOLDEN.md`. Decisiones separadas en tres listas, cada una con beneficio, dependencia, impacto, riesgos, experimento mínimo y criterio objetivo.

Etiquetas: `[CÓDIGO]` · `[DOCUMENTACIÓN]` · `[MEDICIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.

---

## Resumen

- **Adoptar: nada como componente.** Ningún repositorio entrega un motor Media que respete a la vez Asterisk + ai-engine + WS + Modal + Python 3.14t con los modelos actuales. Los que eliminan el coordinador por trama lo hacen en C o Go y obligan a cambiar el transporte; los Python sólo replican.
- **Adaptar: once patrones**, todos con cita `[CÓDIGO]` y ninguno con dependencia nueva.
- **Construir: el MediaWorker con dueño por hilo**, el contrato tipado con sus siete correcciones y la orden de flush hacia ai-engine. Ningún repositorio satisface el contrato completo.

---

## 1. Adoptar (componentes o bibliotecas)

| candidato | beneficio | dependencia | impacto de migración | riesgo técnico | riesgo de licencia | veredicto |
|---|---|---|---|---|---|---|
| LiveKit server + SIP + Agents | plano de media Go probado; supervisión de jobs | Redis, SFU, SIP trunk, WebRTC | reemplaza AudioSocket/ai-engine; ICE/UDP en Modal `[PENDIENTE]` | Agents mantiene K_loop = 1 por proceso Python | Apache-2.0; **modelos LiveKit no portables** (`MODEL_LICENSE`) | **rechazar** |
| FreeSWITCH / jambonz | motor telefónico nativo con barge-in en el thread dueño | FS + módulos jambonz + drachtio + MySQL + Redis | duplica o reemplaza Asterisk; no encaja en Modal | muy alto; C | MPL-1.1/2.0; módulos jambonz `[PENDIENTE]` | **rechazar** |
| mediasoup | workers nativos sin estado compartido | Node o Rust + RTP/ICE/DTLS | ai-engine pasaría de WS a RTP | no hace DSP ni barge-in | ISC | **rechazar** |
| Pipecat | contrato de interrupción maduro | runtime Pipecat | reescribir el interior de GB; API en migración (`PipelineTask` → `PipelineWorker` en este commit) | **no sube K**: todo en un loop | BSD-2 | **rechazar como runtime** |
| TEN | grafo multilenguaje | runtime C/C++ + binding Python sin free-threading | reescritura total | binding incompatible con 3.14t | **condiciones Agora en el núcleo** | **rechazar; no copiar código del núcleo** |

**Experimento mínimo para reabrir cualquiera**: sólo si el PoC de la propuesta (§10) rechaza el MediaWorker Python. Entonces se evalúa un MediaWorker en Go siguiendo LiveKit SIP como **referencia de diseño**, no como dependencia.

---

## 2. Adaptar (patrones, sin incorporar el proyecto)

| # | patrón | origen `[CÓDIGO]` | beneficio en Golden | experimento | criterio |
|---|---|---|---|---|---|
| A1 | **dueño por llamada con cadena por trama síncrona** | FreeSWITCH `switch_ivr_async.c:5185-5197`; LiveKit SIP `media_pipeline.go:321-339`; LiveKit `receiver_base.go:877-1019` | elimina `submit`+`resume` por mensaje en el loop compartido | PoC de la propuesta §10 | latencia trama→decisión p90 a 100 llamadas ≤ la de hoy a 6-10 |
| A2 | **cancelación fuera de banda que vacía lo encolado** | FreeSWITCH `mod_commands.c:5029-5042`, `switch_core_session.c:1348-1371` | el corte no espera detrás de `SpeakChunk` | test: cancel con 50 chunks encolados | 0 chunks emitidos tras el cancel |
| A3 | **quien pacea corta y reporta lo emitido** | FreeSWITCH `PLAYBACK_STOP{playback_ms}` `switch_ivr_play_say.c:2013-2056`; Agents `PlaybackFinishedEvent` `voice/io.py:111-118` | historial truncado con un número medido; elimina la mutación directa de `_cancel_active_tts` | test E2E historial vs audio emitido | error del prefijo ≤ 1 palabra a 3 puntos de medición |
| A4 | **purga por clase de mensaje** | Pipecat `FrameQueue.reset:74-87`; TEN `engine/msg_interface/common.c:325-334` | no perder acciones de negocio al cancelar | test: cancel con `SendFunctionCall` encolado | la función se envía; los chunks no |
| A5 | **generación con parada en dos fases** | LiveKit `receiver_base.go:restartInternal:310-412` | ningún chunk viejo sobrevive al reinicio | fuzz de chunks tardíos | 0 bytes de generación vieja tras el reinicio |
| A6 | **registro de generaciones vivas; tardíos con id desconocido se descartan** | Pipecat `tts_service.py:1141-1181`, `cartesia/tts.py:723` | respuestas tardías nunca suenan | test: `SpeakChunk` de `speech_id` cancelado | descartado y contado |
| A7 | **heartbeat cuyo pong mide el lag del loop/hilo; admisión por lag** | Agents `proc_client.py:118-122`, `supervised_proc.py:486-495`, `worker.py:1322-1368` | atribuir la rodilla en vivo; admisión correcta para un cuello de coordinación | instrumentar Media y correr la escalera | el lag predice la degradación antes que CPU% |
| A8 | **supervisión de procesos**: handshake, ping, kill escalonado, monitor de memoria | Agents `supervised_proc.py` | Media y Conversación supervisados sin infraestructura nueva | kill −9 de cada mitad | recuperación definida, sin zombies |
| A9 | **comandos con confirmación de ejecución y caducidad** | FreeSWITCH `CHANNEL_EXECUTE_COMPLETE`; TEN `path_timer.c:61-114`, `path_table.c:467-489` | `SendFunctionCall` hoy no tiene timeout ni confirmación | test: respuesta ausente | error sintetizado al vencer; respuesta tardía descartada |
| A10 | **colas por llamada y acotadas; backpressure de texto TTS por marcas de agua** | LiveKit SIP colas 10 / 100 ms / 1 (`readStream.write:164-187`, `mixer.go:30-33`); jambonz 1000/200 (`tts-streaming-buffer.js:8-10,111-120`) | una llamada lenta no afecta a otra; memoria acotada | 100 sesiones con una saturada | las otras 99 sin degradación |
| A11 | **dimensionar el búfer posterior a la decisión** | LiveKit SIP ring 5×20 ms con descarte del más viejo, `Swap(nil)` | menos cola residual tras barge-in sin protocolo nuevo | bajar `outbound_lead_cap_secs` 0,6 → 0,2 en laboratorio | corte E2E menor sin subflujos ni cortes de reproducción |

A11 es **la acción más barata y aplicable ya**: un cambio de configuración (`audio.outbound_lead_cap_secs`, WS L1235 `[CÓDIGO]`) medible punta a punta. Respeta la regla de una variable por campaña.

**Antipatrones verificados que no copiar** `[CÓDIGO]`: correlación por rol en vez de por generación (jambonz `gather.js:440-664`); fan-out de eventos bajo mutex global y `send` bloqueante de ~600 s (FreeSWITCH `mod_event_socket.c:287-412`, `switch_apr.c:764-787`); drenaje que sigue admitiendo (LiveKit `SelectRoomNode`); SIGTERM que sale antes del manejador de drenaje (jambonz `process-monitor.js:55-65`); `Task` asyncio por trama (TEN `_proxy_on_audio_frame:394-419`); envío al doble del tiempo real (Pipecat `fastapi.py:456,598-607`); inferencia compartida servida en serie y sin cancelación (Agents `inference_proc_lazy_main.py:85-87`).

---

## 3. Construir (ningún repositorio satisface el contrato)

| pieza | por qué construir | beneficio | dependencia | riesgo técnico | experimento | criterio |
|---|---|---|---|---|---|---|
| **B1. MediaWorker Python 3.14t con dueño por hilo** | los que tienen el mecanismo son C/Go; los Python no lo tienen | eliminar el coordinador por trama | `websockets.sync` (ya pineado 17.0.1) | planificador del SO con ~200 hilos, contención de refcount, pausas de GC de 3.14t `[PENDIENTE]` | PoC §10 de la propuesta | ver criterios §10 |
| **B2. Contrato Media↔Conversación tipado** con C1-C7 | ninguno combina generación + carril prioritario + emitted_ms + caducidad | frontera estable y comprobable | ninguna | orden entre carriles | trazas doradas del dictamen del 28-sep | 0 respuestas a turnos viejos; historial antes del siguiente LLM |
| **B3. Orden de flush hacia ai-engine** (`PlaybackClear{speech_id}` → `ProviderBargeIn` con `call_id`) | ai-engine descarta los JSON de GB; no hay flush | cola residual de ~0,3 s del engine eliminable | cambio en ai-engine | coordinación entre repos | corte medido en 3 puntos | corte E2E ≤ 300 ms |
| **B4. Reconexión del proveedor GB en ai-engine** | hoy la llamada queda contra un socket muerto | requisito para cualquier despliegue nuevo de GB | cambio en ai-engine | epoch en el engine | matar el WS a mitad de llamada | la llamada se recupera o cierra con farewell |

---

## 4. Orden recomendado

1. **A11** como experimento de configuración aislado (una variable).
2. **PoC B1** en laboratorio, en paralelo con la medición de K_Modal del monolito (PoC-0 del dictamen multiproceso), que sigue siendo la línea base.
3. Si B1 acepta: **B2** con A2-A6 y A9-A10, después la frontera completa del dictamen del 28-sep con R0-R6.
4. **B3 y B4** coordinados con el equipo de ai-engine; B4 antes de cualquier despliegue nuevo de GB.
5. Si B1 rechaza: abrir el MediaWorker nativo en Go como alternativa de alto impacto, con LiveKit SIP como referencia de diseño.
