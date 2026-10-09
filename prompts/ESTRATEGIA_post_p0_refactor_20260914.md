# Estrategia post-P0 · ai-engine → GoldenBridge Modal (14-sep-2026)

Arquitectura fija: `context/ARQUITECTURA_STACK.md`. Contrato de ejecución de GB
Modal (3.14.7t · ORT 1.24.4 secuencial · `GB_ORT_INTRA_OP=1` · `OMP`/`OPENBLAS=1`
· gates de referencia e ISA) **se preserva tal cual**; nada de lo que sigue lo toca.

Etiquetas: **[evidencia]** medido o leído en código · **[inferencia]** deducción
con supuesto explícito · **[pendiente]** requiere medición.

---

## 0 · Resultado de la inspección del código

### El seam del coalescer existe y está limpio — [evidencia]

`Asterisk-AI-Voice-Agent-FX` (`main` @ `0d652fa`):

```
AudioSocketServer._connection_loop            audiosocket_server.py:220
  └─ await self._on_audio(conn_id, payload)   320 B slin16 / 20 ms
       └─ Engine._audiosocket_handle_audio     engine.py:5578-6549
            ├─ … diagnósticos, transporte, RMS …
            ├─ provider_payload, enc, rate = self._encode_for_provider(...)   ~6511
            │      rama µ-law: audioop.lin2ulaw(working, 2) → ("ulaw", 8000)   10664+
            │      SIN estado (el estado de resampleo sólo existe en la rama PCM)
            │
            │   ◄──────────────── AQUÍ VA EL COALESCER ────────────────►
            │
            └─ await provider.send_audio(provider_payload)                  ~6538
                 └─ DeepgramProvider.send_audio      providers/deepgram.py:780
                      rama µ-law: payload = audio_chunk  →  websocket.send(payload)
                      PASSTHROUGH: no re-trocea. 480 % 160 == 0 → ni warning.
```

- Perfil activo `default: telephony_responsive` → `input_encoding: mulaw`, 8000 → entra por la rama passthrough. **[evidencia]**
- El troceo a 20 ms (`_pcm16_accum`) sólo existe en la rama `slin16/pcm16`, que GB **no** usa. **[evidencia]**
- Camino de vuelta TTS→Asterisk: `StreamingPlaybackManager._pacer_loop` (`streaming_playback_manager.py:974`) emite **un frame de 20 ms por tick** con rellenos de underflow. **Independiente del tamaño de entrada.** No se toca. **[evidencia]**

### GB acepta 480 B sin cambios — [evidencia]

- `_audio_pre_vad` sólo acumula `audio_in_frames` / `audio_in_bytes`.
- `resample_audio(..., state=)` es soxr `ResampleStream` **stateful por sesión** (`_resample_state`, `_stt_resample_state`, reseteados por instancia en `create_session_instance`). Continuidad preservada entre mensajes de cualquier tamaño.
- Ninguna referencia a 160 B en `websocket_server.py`, `smart_turn_processor.py`, `codec.py`.
- Silero rebana a 256 muestras (`chunk_samples`) independientemente del mensaje.

### Tres cosas que el coalescer debe respetar — [evidencia]

| dónde | qué | consecuencia |
|---|---|---|
| `engine.py:7063` `_audiosocket_handle_disconnect` | hace `pop` de `conn_to_channel`, `channel_to_conns`, `audiosocket_primary_conn`; **no** limpia `_resample_state_provider_in` ni `_audiosocket_frame_count` | el buffer del coalescer se drena y elimina **aquí**, explícitamente |
| `core/models.py:38` `CallSession` | dataclass **persistido** vía `_save_session` | el buffer de bytes **no** va en la sesión: dict del engine por `caller_channel_id`, como `_resample_state_provider_in` |
| `deepgram.py:296` `_prestream_queue` | tope 10 ítems pre-ACK, descarta el más viejo | con 480 B son 600 ms de pre-buffer y 60 ms por descarte, no 20. Riesgo bajo; documentar |

### Dos interacciones en GB que el P0 no midió — [inferencia] → [pendiente]

| mecanismo | hoy (20 ms) | con 60 ms | qué lo delata |
|---|---|---|---|
| `_drain_batch` cuenta **mensajes**, `inbox_batch_max: 10` | lote lleno = 200 ms de audio | lote lleno = **600 ms** — cubre `silence_window_ms` p50 (608 ms) | `frames_per_cycle` sube; `resolution=` se corre a `fallback`; `turn_end_latency` sube |
| `noisereduce` guarda `MIN_CHUNK_SIZE = 100 ms`; lote p50 hoy 96 ms → se salta | un mensaje de 60 ms solo sigue < 100 ms | **dos mensajes = 120 ms → se ejecuta** en lotes de ≥2 | `job_p50` por encima de la recta `0,2507·f + 0,4191` |

Ambas tienen la misma mitigación de config si aparecen: `inbox_batch_max: 3` (≈180 ms de audio por lote). No forma parte de F1.

---

## 1 · Plan por fases

### F1 · Coalescer 20/60 ms en ai-engine

| | |
|---|---|
| **Objetivo** | ai-engine envía a GB 1 mensaje WS de 480 B cada 60 ms en vez de 3 × 160 B, con rollback por config |
| **Cambio mínimo** | `src/config.py` `AudioSocketConfig`: `frame_ms: int = 20` (validador: **sólo `20` o `60`**) · `config/ai-agent.yaml` `audiosocket.frame_ms: 60` · estado, lock y helper único de flush/cleanup por llamada entre `_encode_for_provider` y `provider.send_audio`; su integración terminal ocurre antes de retirar/detener el provider · 2 contadores Prometheus. La corrección y pruebas de ciclo de vida prevalecen sobre cualquier estimación de líneas. **Fuera de alcance:** 40, 80 y 100 ms — cualquier valor distinto de 60 exige un A/B de barge-in nuevo y no forma parte de esta estrategia |
| **Evidencia de salida** | tests de §3 en verde · en una llamada real: engine loguea `frame_bytes=480` en el primer envío; GB loguea `max_payload_bytes=480` en `Turn close: arrival cadence` · `coalesce_partial_flush_total` sólo cuando realmente queda un parcial, con motivo explícito |
| **Rollback** | `audiosocket.frame_ms: 20` + reinicio del engine ⇒ bypass total, ruta idéntica a hoy. `frame_ms` es configuración de arranque: `/reload` debe conservar el valor efectivo y reportar que el cambio queda diferido hasta el próximo reinicio; nunca cambiar el modo de una llamada viva |

### F2 · Prueba real ai-engine → GB Modal, 10 llamadas

| | |
|---|---|
| **Objetivo** | Compatibilidad del camino **completo** (Asterisk → engine coalescer → Modal → TTS → Asterisk), no K |
| **Cambio mínimo** | Ninguno de código. 10 llamadas con `frame_ms=60`, luego 10 con `frame_ms=20` (referencia), mismos 10 audios |
| **Evidencia de salida** | 10/10 `end_ok` en ambos modos · 0 errores/muertes · **marca de sesión correcta** en cada transcripción (cada llamada recibe sólo el texto de su propio audio) · **conversación completa** hasta estado terminal · TTS de vuelta continuo (pacer sin underflows anómalos) · **barge-in funcional** en las 4 sesiones objetivo · **cero contaminación** entre llamadas · `max_payload_bytes=480` en GB · RTT sin degradación sostenida. **No se exige** igualdad literal de transcripciones: STT no es determinista entre corridas |
| **Rollback** | `frame_ms: 20`. La prueba no deja estado |
| **Descarte** | cualquier `end_ok < 10`, contaminación entre sesiones, barge-in perdido, o `resolution=` desplazado a `fallback` respecto al modo 20 ms |

### F3 · Medición de K por tarea Modal

| | |
|---|---|
| **Objetivo** | K real de **una** tarea, con `frame_ms=60`, y referencia a 20 ms en un escalón |
| **Cambio mínimo** | Modal: `max_containers=1`, `max_inputs=120`, `cpu` **fijado como baseline de prueba** (valor a elegir y mantener constante en toda la rampa; si más CPU influye en K es **pendiente de validar** y exigiría una segunda rampa con otro `cpu`), `min_containers=1` **sólo para mantener caliente la tarea durante F3** (evita que el arranque en frío contamine el primer escalón). GB: `cycle_probe: true` (ya activo). Read-back de `OMP`/`OPENBLAS`/`ORT_INTRA`/`GB_ORT_INTRA_OP` en `@enter`, publicado en el log |
| **Evidencia de salida** | Tabla `N → resume_vad_p90 · cycle_p90 · submit_p90 · job_p90 · inbox_depth_max · backlog · RTT p99 · turnos/s · end_ok · errores · contaminación` por escalón, con el **escalón base** como referencia de calibración. K por la regla de §4 |
| **Rollback** | Revertir `max_containers`, `max_inputs`, `cpu` y `min_containers` a los valores de producción. Nada persiste. (Si `min_containers ≥ 1` debe quedarse en producción es una **decisión operativa aparte**, fuera de esta estrategia) |

### F4 · Decisión K≥100 vs K<100

Árbol de §5. La decisión se toma **sólo** con la tabla de F3; ni el P0 ni F2 la sustituyen.

---

## 2 · Contrato del coalescer

```
ESTADO (engine, por llamada, NO en CallSession):
  Un contenedor privado por caller_channel_id, con buffer, ts monotónico del
  primer frame, lock asyncio y la marca necesaria para observabilidad.
  El helper único es el único lugar que acumula, hace flush y elimina ese estado.

PARÁMETROS:
  frame_ms ∈ {20, 60}                      # únicos valores admitidos en esta estrategia
  frames_per_msg = 3   si frame_ms == 60
  frame_ms == 20  ⇒  bypass: send inmediato, sin tocar dicts
```

| regla | especificación |
|---|---|
| **Orden de bytes** | `buf += provider_payload` en orden de llegada. El mensaje es exactamente `f1 ‖ f2 ‖ f3`, 480 B. Ningún reordenamiento, ningún relleno, ninguna cabecera |
| **Cadencia real** | Se envía **al llegar el 3er frame**, nunca antes. Como Asterisk entrega cada 20 ms, el intervalo entre envíos es ≈60 ms por construcción. **No hay temporizador que adelante audio**: el coalescer sólo espera, nunca inventa |
| **Buffers separados y orden** | Un contenedor privado y lock por `caller_channel_id`, creados sólo en µ-law/60 ms. Nunca compartido ni reutilizado entre llamadas. El lock cubre actualización del acumulador y envío para serializar handlers concurrentes de la misma llamada; al drenar se elimina el estado completo mediante `pop` en `finally` |
| **Cierre terminal** | Un helper idempotente ejecuta flush best-effort del parcial (160 o 320 B) **antes** de retirar/detener el provider durante cleanup terminal. Debe tener timeout explícito: reutilizar el timeout de envío ya existente o introducir una constante nombrada y justificada. Un error o timeout no bloquea cleanup y el estado se elimina en `finally` |
| **Desconexión AudioSocket** | Es sólo respaldo. Tras actualizar el conjunto de conexiones, drena únicamente si desapareció la **última** conexión de la llamada; no drena cuando otra sigue activa. El cleanup terminal puede drenar aunque aún existan conexiones. GB acepta tamaño variable **[evidencia]** |
| **Paquete incompleto por stall** | Al llegar un frame, si `monotonic_now − t0 > 2 × frame_ms` (120 ms) y `buf` no está vacío: enviar el parcial primero, luego arrancar paquete nuevo con el frame recibido. Cubre huecos anómalos de Asterisk sin retener audio viejo |
| **Provider ausente / sesión inactiva** | Si el handler ya decide no reenviar (ramas existentes `if not provider` / `not provider_session_active`), el coalescer **no acumula** — descarta igual que hoy. No se crea deuda de audio hacia un provider que no existe |
| **Sólo rama µ-law** | Se activa únicamente cuando `provider_encoding ∈ {ulaw, mulaw, g711_ulaw}` y `frame_ms == 60`. Las rutas `slin16`, `openai_realtime` y `_pipeline_queues` quedan **intactas** |
| **Rollback** | `audiosocket.frame_ms: 20` ⇒ la condición de activación es falsa ⇒ ruta byte-a-byte idéntica a hoy. Debe ser la opción **explícita y documentada** en el yaml, no la ausencia del campo |

**Métricas nuevas:** `avb_coalesce_msgs_out_total`, `avb_coalesce_partial_flush_total{reason=terminal_cleanup|last_disconnect|stall}`. Log INFO en el primer envío por llamada con `frame_bytes`, `frames_per_msg`.

---

## 3 · Tests mínimos de ai-engine antes de desplegar

Anclaje: `tests/conftest.py` (fixtures `set_env`, `patch_websockets_connect`, `fake_redis`); patrón de `tests/test_engine_transport_alignment.py` (`types.SimpleNamespace` como config + llamada directa a métodos del engine).

### Unitarios — `tests/test_audiosocket_coalescer.py` (nuevo)

| test | afirma |
|---|---|
| `test_paridad_bytes_20_vs_60` | Para 300 frames de 160 B con contenido distinto, `b"".join(msgs_60) == b"".join(msgs_20)` y `len(msgs_60) == 100` |
| `test_orden_preservado` | El mensaje k contiene exactamente frames `3k, 3k+1, 3k+2` en ese orden (frames con marca única por índice) |
| `test_aislamiento_entre_llamadas` | Dos `caller_channel_id` intercalados frame a frame → cada uno recibe **sólo** sus bytes, ningún mensaje mezcla marcas |
| `test_no_adelanta_audio` | Tras 2 frames no hay envío; tras el 3º hay exactamente 1 |
| `test_flush_en_cleanup_terminal` | Con 1 o 2 frames pendientes, el helper hace flush **antes** de `provider.stop_session`, una sola vez; incluso si el envío falla o vence su timeout, elimina el estado en `finally` |
| `test_disconnect_no_terminal` | Al desconectar una de varias conexiones de la misma llamada, no drena; al desaparecer la última, hace flush una vez y limpia |
| `test_dos_conexiones_misma_llamada` | Handlers concurrentes para un `caller_channel_id` preservan el orden de llegada y no ejecutan `send_audio` concurrentemente |
| `test_stall_flush` | Frame a t=0, frame a t=200 ms → el 1º se envía solo como parcial antes de acumular el 2º |
| `test_bypass_20ms` | `frame_ms=20` ⇒ `send_audio` recibe cada frame inmediato, dicts vacíos |
| `test_rama_slin16_intacta` | Con `provider_encoding="slin16"` y `frame_ms=60`, no se coalesce |
| `test_validador_frame_ms` | **sólo 20 y 60 aceptados**; 30, 40, 70, 80, 100 y cualquier otro valor rechazados |
| `test_reload_difiere_frame_ms` | Un `/reload` con valor distinto informa que requiere reinicio y no cambia el modo efectivo del proceso |

### Integración — `tests/test_coalescer_provider_passthrough.py` (nuevo)

| test | afirma |
|---|---|
| `test_provider_no_retrocea_mulaw_480` | La ruta productiva `Engine → provider.send_audio(payload)` con un único argumento y `_settings_acked=True` produce **un** `websocket.send` de 480 B; una prueba adicional puede cubrir la firma explícita por compatibilidad |
| `test_prestream_queue_con_480` | Pre-ACK, 12 mensajes de 480 B → cola retiene 10; después del ACK, el **siguiente** `send_audio()` vacía la cola en orden y envía su payload |
| `test_pacer_tts_independiente` | `StreamingPlaybackManager` recibe TTS y emite frames de 20 ms al mismo ritmo con `frame_ms` de entrada 20 y 60 (extiende `test_continuous_stream.py`) |

### Carga — script, no pytest (`tools/coalescer_load.py`, nuevo)

| test | afirma |
|---|---|
| Temporización | 50 llamadas simuladas × 30 s, frames cada 20 ms → intervalo entre envíos p50 ≈ 60 ms, p99 < 80 ms; cero envíos < 55 ms |
| Sin fuga | Al colgar las 50, `len(_coalesce_buf) == 0` |
| Barge-in | Con el provider mock emitiendo TTS, un frame con energía tras 1,8 s dispara el mismo evento en modo 20 y 60 |

**Gate de despliegue:** todos los unitarios e integración en verde + carga sin fuga. El barge-in **real** y la conversación completa con marca correcta se validan en F2, no aquí.

---

## 4 · Medición de K real en Modal

### Montaje

```
max_containers = 1        ← sin esto no hay K POR TAREA; Modal repartiría
max_inputs     = 120      ← que el cap no rechace antes de la rodilla
cpu            = <fijo>   ← BASELINE DE PRUEBA, pendiente de validar: se elige un valor
                             y se mantiene constante en toda la rampa. Si más CPU sube K
                             es una hipótesis abierta; responderla exige otra rampa con
                             otro cpu, no una afirmación previa
min_containers = 1        ← SÓLO para mantener caliente la tarea durante F3, de modo que
                             el arranque en frío (33-34 s medido) no contamine el 1er
                             escalón. No es un requisito productivo de esta estrategia
audiosocket.frame_ms = 60 en el engine · cycle_probe: true en GB (ya activo)
```

**Read-back obligatorio** en `@enter`, una línea de log:
`ENV-READBACK OMP_NUM_THREADS=… OPENBLAS_NUM_THREADS=… ORT_INTRA_OP_NUM_THREADS=… GB_ORT_INTRA_OP=…`
Si algún valor ≠ 1, la corrida **no vale**: Modal fija estas variables según el CPU solicitado.

### Rampa

`10 → 30 → 60 → 100`, **4 min por escalón**, descartando 60 s de rampa. Los 10 audios reales rotando por sesión. **El escalón N=10 es el escalón base**: de él salen los valores de referencia con los que se calibran todos los umbrales de la regla de lectura. Un escalón de **30 a 20 ms** como referencia del coalescer, misma sesión de prueba.

### Telemetría y quién la provee

| métrica | fuente | sin token | con token |
|---|---|---|---|
| **`resume_vad_p90`, `cycle_p90`, `submit_p90`, `job_p90`** | CYCLE-PROBE (log GB) | ✗ | ✔ `modal app logs` |
| `inbox_depth_max`, `frames_per_cycle`, `max_payload_bytes` | log GB | ✗ | ✔ |
| **ENV-READBACK** | log GB `@enter` | ✗ | ✔ |
| tareas activas, `running_inputs`, backlog | autoscaler Modal | ✗ | ✔ |
| **RTT p50/p90/p99** | cliente (`td1_client`) | ✔ | ✔ |
| **`end_ok`, errores, turnos/s** | cliente | ✔ | ✔ |
| `active_sessions` de la tarea | `/health` en bucle | ✔ (parcial) | ✔ |
| **contaminación entre sesiones** | marcas por sesión en cliente | ✔ | ✔ |

**Sin token** se obtiene una **cota inferior externa de K** (RTT + `end_ok` + turnos/s), pero **no** se puede garantizar `max_containers=1` ni leer CYCLE-PROBE ⇒ no es K. **Con token** se obtiene K.

### Regla de lectura, prerregistrada

**K se define por SLO y estabilidad, no por umbrales fijos.** K = el último escalón en el que se cumplen **todas** estas condiciones:

| dimensión | condición |
|---|---|
| **correctitud** | `end_ok = N` · `dropped_frames = 0` · **cero contaminación** entre sesiones |
| **latencia** | RTT p99 **estable** respecto al escalón base — sin crecimiento sostenido a lo largo del escalón |
| **backlog** | `inbox_depth_max` y backlog del autoscaler **sin crecimiento sostenido**: vuelven al nivel base entre turnos |
| **sin rodilla** | `resume_vad_p90`, `submit_p90`, `cycle_p90` **sin punto de inflexión** respecto al escalón base (crecimiento suave, no salto) |
| **throughput** | turnos/s crece **linealmente** con N |

**Los umbrales numéricos se calibran con el escalón base (N=10)**, no se fijan a priori: se registra el valor base de cada métrica y se predeclara, antes de mirar los escalones siguientes, qué múltiplo del base se considera inflexión (p. ej. ×3 en `resume_vad_p90`). El número exacto del múltiplo se elige y se anota **antes** de correr la rampa.

Contexto, **no umbrales**: en `.143` (co-tenencia, spin, 20 ms) los valores fueron sano hasta 6-10, rodilla 11-15, degradado 16-25, roto 41+. **[evidencia]** Una tarea Modal limpia con 60 ms tendrá su propia base; por eso se calibra.

### Guardarraíles que invalidan la corrida

`dropped_frames > 0` · `end_ok < N` · contaminación · read-back ≠ 1 · `max_payload_bytes ≠ 480` (el coalescer no aplicó) · más de 1 tarea en el autoscaler.

---

## 5 · Árbol de decisión

```
                          Tabla F3 (con token)
                                 │
          ┌──────────────────────┼──────────────────────┐
          ▼                      ▼                      ▼
     K ≥ 100              K < 100 por LOOP        K < 100 por REMOTOS
                          (submit/resume/cycle    (stt/llm/tts p90 crecen
                           crecen; job y steno     con N; cycle y rvad
                           planos)                 planos)
          │                      │                      │
          ▼                      ▼                      ▼
   CONGELAR                SIGUIENTE REFACTOR GB    NO TOCAR GB
   frame_ms=60             (ver abajo)              → proveedores
   DESPLEGAR
   K_op = ⌊0,7·K⌉          ← UNA sola reserva (ver nota)
   max_inputs   = K_op
   max_sessions = K_op     (reemplaza el 120)
   max_containers = ⌈2000 / K_op⌉
   SIGUIENTE ETAPA
   ai-engine × N
```

**Política de reserva — una sola.** Se aplica **un** margen, del 30 %, y se aplica **una** vez: `K_op = ⌊0,7·K⌉` es la ocupación operativa por tarea. Justificación: K se mide con audio sintético uniforme; el tráfico real trae barge-in, guiones largos y silencios variables, y el margen absorbe esa diferencia **dentro de cada tarea**. La flota es simplemente `⌈2000 / K_op⌉`; **no se añade un segundo multiplicador** a nivel de flota porque el autoescalado de Modal ya cubre las ráfagas transitorias abriendo tareas — `max_containers` es un techo, no un objetivo. Si en producción se observara que el 30 % no basta, se ajusta **ese único número**, no se apilan márgenes.

### K<100 por loop — el siguiente refactor, en orden de coste

1. **`inbox_batch_max: 3`** — config. Si el lote de 600 ms está corriendo la resolución a `fallback` o inflando `job` vía `noisereduce`, esto lo corta. **[inferencia]** → medir un escalón.
2. **Tramas más largas (80 / 100 ms) — fuera del alcance de esta estrategia.** Bajarían lecturas y viajes otro 25-40 %, pero a costa de +20-40 ms de granularidad de barge-in. Cualquier valor distinto de 60 exige **un A/B de barge-in nuevo y una estrategia propia**; no se implementa ni se prueba aquí.
3. **Sólo si 1 y 2 no alcanzan:** MediaWorker/Control (§6). Se justifica **únicamente** si la telemetría muestra que lo que queda en el loop es coordinación de audio (lecturas WS + submit/resume) y no otra cosa. Si `steno`, JSON o timers dominan, el split no ayuda.

### K<100 por remotos — qué no se refactoriza

Nada en GB. Si `stt_latency_ms` / `llm_latency_ms` / `tts_latency_ms` p90 crecen con N mientras `cycle_p90` y `resume_vad_p90` se mantienen, el cuello está fuera: concurrencia de Steno/Cerebras/Ursula, o la red hacia ellos. Escalar o particionar proveedores; GB ya hace lo que debe. **[inferencia]**: hoy `steno_p90` = 0,10 ms plano hasta 26-40 — este caso es poco probable pero debe descartarse con datos, no asumirse.

---

## 6 · MediaWorker/Control — hipótesis posterior, condicionada a F3

> **Estado: hipótesis, no diseño implementable, no trabajo aprobado.**
> Esta sección existe para que la decisión de F4 tenga un boceto contra el que
> razonar. **No** está listo para implementarse: faltan **(1)** el mecanismo de
> IPC entre los dos procesos, **(2)** el enrutamiento estable de una llamada a
> su par MediaWorker↔Control, **(3)** las garantías de orden y tratamiento de
> duplicados en el canal, y **(4)** el contrato completo de cancelación de
> barge-in a través del límite. Ninguno de esos cuatro puntos se resuelve aquí.
> Sólo se aborda si F3 muestra que el loop de coordinación sigue siendo el
> cuello **después** del coalescer de 60 ms.

**Premisa honesta:** partir GB en dos procesos Python **no** eleva K por sí solo. Cada proceso hereda un event loop. Lo que el split hace es **repartir** la carga de loop entre dos hilos: uno con las lecturas WS + submit/resume (MediaWorker) y otro con la conversación (Control). Eleva K **sólo si** la carga que hoy satura el loop es mayoritariamente de audio y la parte de conversación es pequeña — que es lo que `steno_p90` plano sugiere **[inferencia]**, pero que F3 debe confirmar midiendo qué fases crecen.

| | MediaWorker | Control |
|---|---|---|
| **Dueño de** | WS de audio del engine (entrada y TTS de salida) · µ-law→pcm16 · soxr · Silero · smart-turn · `turn_audio` · barge-in | máquina de estados · campaña · LLM · orquestación STT/TTS a nivel de decisión · transferencia · `CallEventNotification` |
| **Recibe** | 480 B / 60 ms por llamada | eventos JSON, ~1-3/s/llamada |
| **Emite** | `speech_start`, `speech_end`, `turn_end{prob, audio_ref}`, `barge_in`, `stt_final{text}` | `speak{text, gen_id}`, `cancel{gen_id}`, `transfer`, `hangup` |

**Afinidad:** una llamada vive en **un** MediaWorker y **un** Control durante toda su vida; el engine sigue siendo dueño de la afinidad y del par (uuid ↔ worker). Nunca se migra una llamada.

**Contrato:** `gen_id` monótono por llamada en `speak`/`cancel` para que barge-in cancele exactamente la generación en vuelo (hoy es in-process; pasa a ser una carrera entre procesos y necesita el id).

**Rollout:** feature flag `GB_TOPOLOGY=monolith|split`; MediaWorker+Control en la **misma** tarea Modal primero (dos procesos, un contenedor) para medir el efecto del reparto sin añadir red; después, si compensa, Control aparte.

**Rollback:** `GB_TOPOLOGY=monolith` — el proceso actual intacto.

**Métrica que lo demuestra:** a N fijo (p. ej. 60), `resume_vad_p90` y `submit_p90` del MediaWorker vs los del monolito; y K por rampa en ambas topologías. Si K no sube, el split no era la palanca.

---

## 7 · Esfuerzo y riesgo

| | Coalescer ai-engine | Medición K | Refactor mayor GB |
|---|---|---|---|
| **Esfuerzo** | ~25 líneas en `engine.py` + 1 campo config + 9 tests unitarios + 3 integración + 1 script de carga. **1-2 días** con revisión | Config Modal + script de rampa + pipeline de análisis (ya existe `parear_por_concurrencia.py`). **1 día**, **bloqueado por token** para K real | Semanas. Contrato de eventos, dos procesos, flag, cancelación distribuida, tests de orden y aislamiento |
| **Hechos** | seam sin estado en la rama µ-law · provider passthrough · GB acepta 480 B · pacer de vuelta independiente · disconnect no limpia dicts por llamada | rodilla 16-25 en .143 · `cycle_probe: true` desplegado · arranque frío 33 s · `max_sessions: 120` sin validar | `steno_p90` plano ⇒ conversación barata · `frame_offload: true` ya sacó el DSP del loop |
| **Inferencias** | 60 ms reduce 3× los **mensajes** de entrada; efecto sobre jobs/pool **no** se asume | K en tarea limpia > rodilla de .143 (co-tenencia + spin) | el split reparte, no elimina; eleva K sólo si la carga de loop es audio |
| **Riesgos abiertos** | lote de 600 ms vs `silence_window` · `noisereduce` se enciende en lotes ≥2 · granularidad de barge-in 60 ms · timeout de flush a calibrar con el mecanismo existente | Modal pisa `OMP`/`OPENBLAS`/`ORT` · sin token no hay `max_containers=1` · picos RTT del P0 sin atribuir | cancelación distribuida de barge-in · duplicar memoria de modelos · dos loops que medir en vez de uno |

---

## 8 · Orden de ejecución

1. **F1** — coalescer + tests. Sin dependencias externas.
2. **F2** — 10 llamadas reales a 60 y a 20. Requiere ventana de campaña (`.143` arranca 14:00 UTC).
3. **Pedir el token de control-plane de Modal** en paralelo a 1 y 2.
4. **F3** — rampa con `max_containers=1`. Requiere token.
5. **F4** — decisión con la tabla. Nada antes.

Lo único que hoy detiene K es el token. Todo lo demás está en nuestras manos.
