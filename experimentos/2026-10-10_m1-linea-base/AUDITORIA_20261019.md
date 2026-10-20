# Auditoría de las corridas M1 del 2026-10-19 (60 ms contra 20 ms)

Auditoría de sólo lectura, hecha en `.143` el 2026-10-20 (00:10-01:00 UTC) sobre la rama `paso/m1-linea-base` (`2046536`). No se lanzó ningún experimento, contenedor con carga ni llamada a proveedores. El código de la imagen `goldenbridge:remarcacion-v1-jef-fp32` (`sha256:8485ac9f…`) se sacó con `docker create` + `docker cp` (no arranca ningún proceso) y se leyó en el scratchpad.

**M1** es la línea base del sistema actual (el monolito de GoldenBridge medido con el cliente de carga). **Brazo 60 ms** = escalera `resultados/20261019T210000Z` (`PACKET_MS=60`, `MIDSPEECH_N=17`). **Brazo 20 ms** = escalera `resultados/20261019T230125Z` (`PACKET_MS=20`, `MIDSPEECH_N=51`). Los tres ensayos de N=10 y 2 min son `200602Z` (reloj), `204420Z` y `205152Z` (espera).

## Cómo se extrajo la evidencia

- `[MEDIDO]` con script: `auditoria_20261019_extrae.py` (en esta carpeta; sólo biblioteca estándar). Lee de cada escalón crudo `gb.log`, `cliente.tsv`, `stats.tsv` y `efectivo.txt`, y escribe por escalón `cycle_probe.tsv` (todas las líneas CYCLE-PROBE), `turn_complete.tsv` (líneas de cierre de turno con `close_attempts` e `inference_ms`), `barge_in.tsv`, `por_minuto.tsv` y `resumen_auditoria.tsv`; más `resumen_auditoria_todos.tsv` con todos los escalones. Ventana estable igual a la de `analiza.py`: `[t0+122, t0+720]` s (`[t0+122, t0+240]` en los ensayos, con `--estable 120`).

  ```bash
  cd experimentos/2026-10-10_m1-linea-base
  python3 auditoria_20261019_extrae.py /tmp/aud resultados/crudo/20261019T2{10000,30125}Z/r1_n*
  python3 auditoria_20261019_extrae.py /tmp/aud --estable 120 resultados/crudo/20261019T20{0602,4420,5152}Z/r1_n010
  ```

  Los números de abajo son los de `resumen_auditoria_todos.tsv` y `por_minuto.tsv`, salvo donde se indica otro comando.
- `[CÓDIGO]` = código de la imagen (`/app/src`, `/app/config/default.yaml`); las rutas y líneas son de ese árbol.
- `[DOCUMENTACIÓN]` = README, CLAUDE.md, `config_lab.yaml`, manifiestos.
- Lo crudo (`resultados/crudo/`) no va al repo; sí van, por sello, `manifiesto.txt`, `parametros.txt`, `config_lab.yaml`, `topologia_cpu.txt` y `resumen.tsv`.

## 1. Validez de los escalones

`[MEDIDO]` en los 13 `efectivo.txt` (`grep -h "hilos_ort\|cgroup_cpuset\|imagen=\|cliente_rc\|prod_activas" resultados/crudo/*/r1_n*/efectivo.txt`) y en los 13 `gb.log` (`grep -m1 "Smart Turn processor created" …`):

| comprobación | resultado en los 13 escalones |
|---|---|
| `cgroup_cpuset` leído de vuelta | `2-5,10-13` |
| `cgroup_cpu_max` | `800000 100000` (8 núcleos) |
| `hilos_ort_omp_openblas` | `1 1 1`; además el arranque imprime `hilos fijados: ORT_INTRA_OP=1 OPENBLAS=1 OMP=1` |
| imagen | `sha256:8485ac9f48d0…` |
| `Smart Turn processor created … model_path=` | `/app/smart-turn/smart-turn-v3.2-gpu.onnx threshold=0.35` |
| `Silero VAD initialized` | `start_secs=0.2 stop_secs=0.2 threshold=0.5 sample_rate=8000 min_consecutive_speech_chunks=8 min_consecutive_silence_chunks=3` |
| `Smart Turn VAD processor initialized` | `midspeech_enabled=True midspeech_threshold=0.85 min_speech_duration=0.6 tts_vad_threshold=0.9 noise_reduction_enabled=True` |
| `cliente_rc`, `prod_activas`, `cliente_cpuset_leido` | `0`, `0`, `6,14` |
| `gen_lag_p99_max`, muertes, errores del cliente, `send_stall` (`resumen.tsv`) | ≤ 2 ms, 0, 0, 0 |
| `Session inbox full` (reader bloqueado) | 0 líneas en todos; es vacío: `[CÓDIGO]` `inbox_maxsize` vale 0 por defecto (L1304) → `asyncio.Queue(maxsize=0)` es ilimitada y el aviso no puede dispararse. Por eso es posible el atraso de 13.593 mensajes de la sección 6.1 |

Dos aclaraciones:

- `nproc_dentro=1` en todos los `efectivo.txt` **no** es un límite de CPU: `nproc` de coreutils respeta `OMP_NUM_THREADS`. `[MEDIDO]` en el host: `nproc` → 16, `OMP_NUM_THREADS=1 nproc` → 1. El cgroup sí dice 8 vCPU.
- `config_lab.yaml` sale de `$HOME/m1_base.yaml`, cuyo sha256 (`15507f5f…`) es idéntico al `/app/config/default.yaml` de la imagen `[MEDIDO: sha256sum]`. Es la config de la imagen, no el `active.yaml` de producción (`vad.stop_secs: 0.2`, no 0,6; `vad.inference_workers: 12`; `noise_reduction.enabled: true`). No trae claves: `stt.api_key: ''`; sí trae URLs de endpoints de Modal y dos `endpoint_id` de RunPod (`scaling.endpoints`), que son identificadores, no credenciales.

**Corrección a la tabla de corridas del encargo** `[MEDIDO]`: el ensayo 2 (`204420Z`) no fue "LLM frío". Tuvo 46 líneas `[error]`: 33 `Steno fallback transcribe failed error='timed out during opening handshake'` y 13 `Persistent STT connection failed … timed out during opening handshake`; 0 `STT Finalize completed`, 0 `LLM time to first token`. El LLM nunca se llamó porque Steno no entregó transcripciones. Comando: `sed 's/\x1b\[[0-9;]*m//g' resultados/crudo/20261019T204420Z/r1_n010/gb.log | grep -c "opening handshake"`.

## 2. Seis hechos del código que cambian la lectura

**A. CYCLE-PROBE sólo mide los ciclos por lote (≥ 2 mensajes en cola y sin TTS vivo).** `[CÓDIGO]` `probe.add(...)` se llama sólo en `_handle_audio_batch` (`server/websocket_server.py` L2909-3046); la ruta de un mensaje, `_handle_audio_message` (L3783), no toca el probe. Un lote existe sólo si `_drain_inbox_frames` encontró más de un mensaje en el `inbox` **y** `_batch_eligible` es verdadero (sin TTS activo ni naciendo, L2800-2833). Los percentiles de cada línea son sobre un `deque(maxlen=600)` acumulado por sesión (L134), no sobre la ventana de 5 s; la línea trae `cycles`, `frames`, `frames_per_cycle`, `cierres`, `pct_cierres` (acumulados desde el inicio de la sesión), `inbox`, `carry`, `consumed` y, por fase (`cycle, pre, submit, job, resume, resume_vad, smartturn, post, steno, otros`), sólo `_p50` y `_p90`. **No hay p99 ni máximo.** Cobertura `[MEDIDO]` (Σ `frames` acumulados de la última línea de cada sesión ÷ Σ `packets` de `end_ok`):

| N | 60 ms: mensajes en lotes / enviados | 20 ms: mensajes en lotes / enviados | sesiones con alguna línea (60 / 20) |
|---|---|---|---|
| 10 | 675 / 125.958 = **0,5 %** | 4.375 / 356.288 = 1,2 % | 7/13 · 10/13 |
| 25 | 0,7 % | 1,5 % | 23/28 · 25/28 |
| 50 | 3,7 % | 2,8 % | 50/53 · 50/53 |
| 75 | 1,9 % | 4,3 % | 75/78 · 75/78 |
| 100 | 5,3 % | **43,9 %** | 100/103 · 100/103 |

A 60 ms N=10 las líneas (198 en el escalón, 177 en la ventana) vienen casi todas de las sesiones que interrumpen (`l0` 92, `l3` 79, `l6` 22 en el escalón; las que no interrumpen, 1-2 líneas en 12 min): el lote es de exactamente 3 mensajes, uno cada ~7,5 s, es decir la cola que se forma durante los ~200 ms del `_cancel_active_tts` tras cada barge-in `[MEDIDO: cycle_probe.tsv, columnas cycles/frames; INFERIDO el mecanismo]`. A 20 ms N=100, con `inbox` mediano de 113 mensajes, casi todo pasa por lotes. **Comparar `cp_*` entre brazos es comparar muestras distintas** (ver afirmaciones 1, 5, 8).

**B. Un mensaje de 60 ms entra al job como una sola trama de 480 muestras.** `[CÓDIGO]` `_session_reader` encola el mensaje entero (`carry.append((message["bytes"], False))`, L2727) y `_process_frames_batch_sync` llama a `_process_frame_sync` una vez por mensaje. `[MEDIDO]` `Audio intake stats … audio_samples=480` en los escalones de 60 ms (1.696 veces a N=50) y `=160` a 20 ms (`grep -oE "audio_samples=[0-9]+" gb.log | sort | uniq -c`). Consecuencias: `frames_per_cycle` cuenta mensajes (a 60 ms el mínimo de un lote es 2, no 3 como dice el README); `_audio_chunk_count` cuenta mensajes, así que `MIDSPEECH_N=17` a 60 ms y `51` a 20 ms son ambos ≈ 1,02 s (equivalentes, como se buscaba); Silero sigue en chunks de 256 muestras (~1,9 por mensaje de 60 ms).

**C. La inferencia de smart-turn corre dentro del ciclo y pasa por tres colas.** `[CÓDIGO]` `process_audio_batch` (`smart_turn_processor.py` L1222-1234): `async with self._inference_gate` (asyncio.Lock por sesión) → `loop.run_in_executor(None, self._resample_and_predict, …)` (el **executor por defecto** del loop, no el pool `vad`; `[CÓDIGO]` no hay `set_default_executor` en `main.py` ni en el servidor; `[INFERIDO]` el default de Python es `min(32, process_cpu_count()+4)`, que con el cpuset de 8 vCPU daría 12 hilos; `nlwp` 30-32 a N=100 es compatible con 12 `vad` + ~12 por defecto + principal. Leerlo de vuelta la próxima vez: `loop._default_executor._max_workers` o un volcado de `threading.enumerate()` a N=100) → `predict_turn_complete` (L172-250): extracción de features Whisper (8 s de log-mel) **fuera** del lock y `self.session.run` **bajo `_inference_lock`**, un `threading.Lock` compartido por todas las sesiones (L67, L145). `inference_ms` de la línea `Smart Turn: Turn complete` mide desde después del snapshot del buffer: features + espera del lock + run (L198-227). La fase `smartturn` del probe mide además la espera del `_inference_gate` y la cola del executor por defecto (L1203-1210).

**D. `post` incluye `await`s de red.** `[CÓDIGO]` `post` = `_audio_post_vad` de cada trama consumida (L3815-4050). Si la trama cierra turno: `await _resolve_turn_complete` → `_handle_new_user_turn_tts` (puede esperar `active_tts_task` hasta 30 s cuando difiere, L8677; "Reentry hold") → `_process_speech_streaming` → **`await self.stt_processor.send_finalize(...)`** (L4186) y, si vuelve vacío, `transcribe` de respaldo. El LLM y el TTS se lanzan con `_spawn_logged` (no se esperan). Si la trama dispara barge-in: `await self._cancel_active_tts` (L8849), que espera `wait_for(stream.aclose(), 0.05)` y `wait_for(task_to_cancel, timeout=0.2)` (L8937, L8952). El subcampo `steno` sólo cubre el envío en vivo de la trama a Steno (L3918-3939), no el Finalize.

**E. `AgentAudioDone` sólo se manda si el TTS no fue cancelado.** `[CÓDIGO]` L6187-6188 y L7947-7948, en la ruta streaming (la usada aquí: `LATENCY BREAKDOWN … (streaming)`): `if not session.tts_cancelled: await self._send_message(websocket, create_agent_audio_done(), session)`. Las rutas no streaming (L1955, L6244) lo mandan sin condición. Así que `fin=natural` del cliente significa que GB terminó la respuesta sin cancelar: la interrupción no cortó.

**F. `noisereduce` no corre nunca en estas corridas.** `[CÓDIGO]` `reduce_noise` recibe una trama (`_process_frame_sync`, L1726-1731) de 480 ó 160 muestras a 8 kHz y salta si `len(audio) < MIN_CHUNK_SIZE = 800` (`noise_processor.py` L111-118). La guarda es por trama, no por lote. `[MEDIDO]` la carga perezosa `noisereduce library loaded` aparece 0 veces en los 13 `gb.log` (`grep -c "noisereduce library loaded"`).

## 3. Resumen de la ventana estable, los dos brazos

`[MEDIDO: resumen_auditoria_todos.tsv]`. "cierres" = líneas `Turn close: silence transition`; "inferencias" = Σ `close_attempts` de las líneas `Smart Turn: Turn complete` (+ mid-speech + fallback); `infer_ms` = `inference_ms` de `Smart Turn: Turn complete` (features + lock + run, ver C).

| N | brazo | cierres/s | inferencias/s | inf./turno | `infer_ms` p50 / p90 / **mín** | `ttfab` p50 / p90 | `ping_ctrl` p99 | `cpu` med | cpu/llamada |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 60 | 1,28 | 1,87 | 1,43 | 75 / 85 / 73 | 1.006 / 1.417 | 4 | 0,45 | 0,0455 |
| 10 | 20 | 1,12 | 1,63 | 1,44 | 75 / 85 / 72 | 996 / 1.427 | 3 | 0,58 | 0,0582 |
| 25 | 60 | 2,66 | 3,92 | 1,45 | 77 / 144 / 73 | 1.174 / 1.523 | 3 | 1,02 | 0,0409 |
| 25 | 20 | 2,53 | 3,83 | 1,49 | 78 / 137 / 74 | 1.140 / 1.440 | 6 | 1,35 | 0,0539 |
| 50 | 60 | 4,64 | 7,13 | 1,50 | 93 / 229 / 74 | 1.882 / 3.783 | 7 | 1,77 | 0,0355 |
| 50 | 20 | 5,36 | 8,24 | 1,50 | 127 / 291 / 77 | 1.336 / 2.037 | 8 | 2,60 | 0,0521 |
| 75 | 60 | 7,46 | 11,21 | 1,47 | 231 / 548 / 77 | 1.494 / 2.334 | 11 | 2,94 | 0,0391 |
| 75 | 20 | 7,57 | 11,99 | 1,52 | 443 / 920 / 80 | 1.735 / 2.771 | 17 | 3,87 | 0,0516 |
| 100 | 60 | 7,31 | 13,11 | 1,58 | **912 / 940 / 854** | **4.126** / 7.391 | 14 | 3,30 | 0,0330 |
| 100 | 20 | 7,90 | 12,20 | 1,42 | **954 / 989 / 81** | 2.992 / 5.277 | 32 | 4,48 | 0,0448 |

Fases del probe (mediana de los p90 por línea; sólo ciclos por lote, ver A):

| N | brazo | `pct_cierres` med | `fpc` med | `cycle` | `job` | `resume_vad` | `smartturn` med / máx | `post` med / máx | `inbox` med / máx |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 60 | 0,0 | 2,93 | 3,6 | 2,3 | 0,4 | 0 / 82 | 0,1 / 337 | 0 / 1 |
| 10 | 20 | 5,0 | 6,73 | 4,4 | 2,7 | 0,5 | 0 / 134 | 0,1 / 124 | 0 / 82 |
| 25 | 60 | 1,6 | 2,92 | 4,5 | 2,6 | 0,9 | 0 / 199 | 0,1 / 317 | 0 / 9 |
| 25 | 20 | 5,1 | 6,82 | 10,6 | 3,1 | 2,0 | 0 / 215 | 0,2 / 156 | 0 / 174 |
| 50 | 60 | 3,2 | 3,69 | 11,7 | 5,2 | 2,9 | 0 / 430 | 0,2 / **10.716** | 0 / 281 |
| 50 | 20 | 4,5 | 7,54 | 28,4 | 3,9 | 10,5 | 0 / 297 | 0,2 / 1.523 | 0 / 486 |
| 75 | 60 | 12,5 | 4,18 | 346 | 5,9 | 17,1 | 160 / 1.083 | 111 / 2.361 | 0 / 98 |
| 75 | 20 | 5,7 | 5,39 | 38,1 | 4,2 | 28,8 | 0 / 1.439 | 0,2 / 144 | 1 / 596 |
| 100 | 60 | 40,5 | 7,40 | 2.582 | 8,8 | 9,4 | 2.469 / 3.081 | 128 / 3.077 | 28 / 532 |
| 100 | 20 | 1,7 | 5,05 | 44,9 | 4,4 | 40,5 | 0 / 0 | 0,1 / 0,2 | **113 / 13.593** |

## 4. Las diez afirmaciones

### Afirmación 1 — "El coalescer mejora la coordinación del loop; la rodilla pasa de 25 a 50"

| parte | veredicto | evidencia |
|---|---|---|
| `ping_ctrl_p99` menor a 60 ms | **confirmada para N ≥ 25**; a N=10 es 4 contra 3 (1 ms peor, con resolución de 1 ms), no "todos los escalones" | `[MEDIDO: resumen.tsv]` 4/3/7/11/14 (60 ms) contra 3/6/8/17/32 (20 ms). Las sesiones de control son iguales en los dos brazos (20 ms, 5 fps), así que es la única métrica de loop comparable entre brazos. Máximos: 97/59/10/50/142 contra 66/58/46/100/44 ms. |
| `cp_resume_vad_p90` menor a 60 ms | **abierta** (dirección sí, base no comparable) | mediana 0,4/0,9/2,9/17,1/9,4 contra 0,5/2,0/10,5/28,8/40,5; pero p90 3,2/9,2/21,2/**51,7**/45,2 contra 0,8/11,9/31,5/33,2/44,6 (a N=10 y 75 es mayor a 60 ms). La muestra del probe es del 0,5-5 % de los mensajes a 60 ms y del 44 % a 20 ms N=100 (hecho A). |
| rodilla 25 → 50 | **abierta** | `veredicto.txt` dice N=50 (60 ms) y N=25 (20 ms) por la regla `cycle_p90_med > 2× base`. La base a 60 ms (3,6 ms) sale de ~1 lote por sesión en 10 min, casi todo de las sesiones que interrumpen; a 20 ms N=25 el `cycle` 10,6 incluye `resume_vad` 2,0 y `job` 3,1 con fpc 6,8. Una repetición por brazo. Con la cobertura de A no se puede sostener una rodilla por `cp_cycle`. |
| ninguno de los dos brazos es "sano" más allá de N=10 | **confirmada** | criterio 2 del README: `ttfab_p50` a N=25 empeora +17 % (1.174 contra 1.006) y +14 % (1.140 contra 996); criterio 1: `cycle_p90_p90` 25,5 y 190,5 > 20. La regla de beneficio de §7 no aplica en ningún brazo. |

Lo que sí se sostiene sin el probe `[MEDIDO]`: a 20 ms N=100 el loop va atrasado de forma estable (`inbox` mediano 75-130 mensajes = 1,5-2,6 s de audio en cada minuto de `por_minuto.tsv`; `corte_p90` 6,2 s; `ping_ctrl_p99` 32 ms); a 60 ms N=100 el loop no acumula entre cierres (`resume_vad` 9 ms, `ping` 14 ms) pero cada cierre espera ~0,9-2,5 s en smart-turn (afirmación 3). **E2E el brazo de 60 ms es peor a N=100** (`ttfab_p50` 4.126 contra 2.992 ms) y mejor a N=75 (1.494 contra 1.735).

### Afirmación 2 — "A 100 llamadas, 60 ms usa ~25 % menos CPU"

| veredicto | evidencia |
|---|---|
| **confirmada**, y vale en toda la escalera | `[MEDIDO: resumen.tsv, cpu_nucleos_med]` 3,30 contra 4,48 núcleos (−26 %). Por llamada: 0,0455/0,0409/0,0355/0,0391/0,0330 contra 0,0582/0,0539/0,0521/0,0516/0,0448 (−22 % a −32 % en cada N). `ps` al final del escalón (`procesos_fin.txt`): 305 % contra 415 % a N=100, 268 % contra 353 % a N=75. |

Matices: (a) a N=100 el brazo de 60 ms produjo menos respuestas (`Agent response` 3.529 contra 4.085, −14 %; `speak` 5.206 contra 5.562), parte del ahorro es menos trabajo; a N=75 las respuestas son iguales (3.539 contra 3.579) y el ahorro es −24 %. (b) Mecanismo `[INFERIDO]`: 16,7 contra 50 mensajes/s por sesión = menos lecturas de WS, menos viajes al pool en la ruta por trama durante TTS y 3× menos envíos en vivo a Steno. (c) `docker-proxy` (fuera del cgroup, por el `-p 127.0.0.1:8777`) sube de 1,2 % a 7,1 % (60 ms) y 14,6 % (20 ms) de un núcleo: artefacto del laboratorio, no entra en `cpu_nucleos`.

### Afirmación 3 — "A 60 ms la explosión del ciclo a 75-100 viene de `cp_smartturn`, no del loop"

| veredicto | evidencia |
|---|---|
| **confirmada**, con una precisión sobre qué es "smartturn" | `[MEDIDO]` `cp_smartturn_p90_med` 159,8 y 2.469 ms contra `cycle` 346 y 2.582; `post` 111 y 128 ms (≈ `STT Finalize completed stt_latency_ms` p50 139 y 123: es el Finalize, hecho D); `resume_vad` 17,1 y 9,4; `ping_ctrl_p99` 11 y 14. Estable minuto a minuto desde el minuto 1 (`por_minuto.tsv`: a N=100, `smartturn_p90_med` 1.769 → 2.361 → 2.393 → … → 2.530; `infer_ms_p50` 908-918 en todos los minutos). |

La precisión: `infer_ms` (features + lock + run) es 912 ms p50 a N=100, pero la fase `smartturn` del probe es 2.469 ms. La diferencia (~1,5 s) está **antes** de empezar la inferencia: espera del `_inference_gate` y cola del executor por defecto (hecho C). A N=100 60 ms el **mínimo** de `infer_ms` es 854 ms: ninguna inferencia de la ventana entró sin esperar.

### Afirmación 4 — "Techo de ~7 cierres/s en los dos brazos; mecanismo: smart-turn serializado por `_inference_lock`, fp32 47,6 ms, ~3 inferencias por turno"

| parte | veredicto | evidencia |
|---|---|---|
| techo de cierres por segundo | **confirmada** | `[MEDIDO]` ventana estable: 7,46 → 7,31 cierres/s (60 ms, N=75 → 100) y 7,57 → 7,90 (20 ms). Los conteos del encargo (5.146/5.145 y 5.230/5.497) son sobre el escalón entero: `grep -c "Turn close: silence transition" gb.log`. Las inferencias también saturan: 11,2 → 13,1/s y 12,0 → 12,2/s, con `infer_ms_p50` 912 y 954 ms a N=100 en los dos brazos. |
| ~3 inferencias por turno | **refutada** | `[MEDIDO]` `close_attempts` por turno = 1,39-1,58 en los 13 escalones (`turn_complete.tsv`; el campo lo emite `_close_diagnostics`, L801-830). |
| 47,6 ms por inferencia | **confirmada como medida aislada**, no como costo en carga | `[MEDIDO]` `experimentos/2026-10-14_smartturn-fp32/resultados/crudo/20261014T195156Z.log` L32-33: `lat p50 int8/fp32: 27.1 / 47.6 ms` (3.136 casos, host sin carga, python del host). En la imagen bajo carga, `infer_ms` mínimo es 72-77 ms a N ≤ 75 (features + run sin cola). |
| el lock global es el mecanismo | **abierta** | Con un lock y 47,6 ms por run, el techo sería ~21 inferencias/s y las esperas de decenas de ms; se miden 12-13/s y ~0,9 s. Dos candidatos `[INFERIDO]` que sí cuadran: (a) el executor por defecto tiene 12 hilos y cada inferencia ocupa uno ~0,95 s esperando el lock → 12/0,95 ≈ 12,6/s, justo el techo medido (Little); (b) el `session.run` bajo contención (4 núcleos físicos con HT, 12 workers de Silero, 3-4,5 núcleos ocupados) tarda más que 47,6 ms. Los dos pasan por el lock; sólo se separan instrumentando el tiempo de espera del lock y el del run por separado (sección 7). |

El techo de turnos y el `ttfab` están acoplados: a N=100 cada turno paga ~0,9 s de cola (60 ms: todos; 20 ms: mínimo 81 ms, mediana 954), el bot responde más tarde, y el cliente en modo espera habla menos veces por minuto. No hace falta un segundo mecanismo para explicar que los cierres/s dejen de subir.

### Afirmación 5 — "`cp_smartturn_p90` da 0 a 20 ms por dilución, no porque no haya espera"

| veredicto | evidencia |
|---|---|
| **confirmada** | `[MEDIDO]` a 20 ms N=100: `infer_ms` p50 954 ms, p90 989, máx 1.139 (`turn_complete.tsv`) mientras `cp_smartturn_p90_med` = 0,0 y el **máximo de los p90** por línea también es 0,0; `pct_cierres` mediano 1,7 % (a 60 ms N=100: 40,5 %). Con < 10 % de ciclos que cierran turno, el p90 de un `deque` de 600 ciclos no alcanza la inferencia. A 20 ms N=75 (`pct_cierres` 5,7 %) el p90 mediano sigue en 0 pero el máximo de los p90 llega a 1.439 ms. |

La línea **no trae p99 ni máximo** (hecho A), así que no se puede responder "¿es 0 también en el p99?" con el probe; se responde con `inference_ms` de las líneas de cierre, que sí están a nivel INFO.

### Afirmación 6 — "Corte ~2,2 s plano a 60 ms, 6,2 s a 100 a 20 ms; `l0` nunca corta; requisitos de interrupción"

| parte | veredicto | evidencia |
|---|---|---|
| `corte_p90` ~2,2 s plano a 60 ms, 6,2 s a 20 ms N=100 | **confirmada** | `[MEDIDO: resumen.tsv]` 2.209/2.148/2.182/2.161/2.233 contra 2.111/2.150/2.151/2.137/**6.190** (p99 13.247). A 20 ms N=100 coincide con el atraso del loop (`inbox` 113 mensajes ≈ 2,3 s). |
| `l0` (girasol) nunca corta en el ensayo 3 | **confirmada en el ensayo 3, refutada como propiedad del audio** | `[MEDIDO]` `awk -F'\t' '$2=="corte" && $3=="l0"' cliente.tsv`: ensayo 3 → 0 cortados / 29 naturales; escalera 60 ms N=10 → **96 cortados / 0 naturales**; 20 ms N=10 → 95 / 0. `l9` (sombrilla) hace lo contrario: 23/0 en el ensayo 3 y 2/97, 2/98 en las escaleras. Misma marca, misma sesión, comportamiento opuesto → depende de la respuesta (largo de lo que el bot dice; con pacing `initial_burst_secs 1.5` + `lead_secs 0.8`, una respuesta corta ya salió entera cuando se detecta la voz), no del audio `[INFERIDO]`. Por el hecho E, `fin=natural` = GB terminó sin cancelar. |
| requisitos de interrupción | **confirmada** | `[DOCUMENTACIÓN: config_lab.yaml]` `barge_in: min_speech_duration 0.6, tts_vad_threshold 0.9, min_energy_threshold 0.08, min_tts_time 1.2, min_consecutive_speech_chunks 8`; `[MEDIDO]` la línea `Barge-in gates not met during TTS` imprime esos mismos gates. `vad.barge_in_min_consecutive_speech_chunks: 3` no se usa (Silero arranca con 8). |

Tres datos más sobre el corte `[MEDIDO]`: (1) `speech_duration` de `Barge-in triggered` tiene p50 0,62-0,66 s, p90 0,76-1,02, máximo 1,86 en todos los escalones: dispara apenas pasa el gate de 0,6 s. (2) `TTS cancellation complete cancel_latency_ms` tiene p50 201,4-204,8 ms en 12 de 13 escalones (228,7 a 20 ms N=100): es exactamente el `wait_for(task_to_cancel, timeout=0.2)` de L8952; el cancel **siempre** agota su timeout. (3) `analiza.py` excluye `fin=natural` de `corte_p*`: a 60 ms N=10 el 2,2 s describe 172 de 340 episodios; los otros 168 terminaron sin corte. Los `Barge-in gates not met` con `first_blocked_gate=speech_duration` o `consecutive_chunks` (100+61 a N=10 60 ms) son voz que GB vio durante el TTS sin llegar a 0,6 s / 8 chunks `[ABIERTO]` de quién es (el final de la frase del usuario pisada por una respuesta temprana es el candidato: ver mid-speech en la sección 6).

### Afirmación 7 — "Los 476 `ttfab_perdidos` del escalón 50 a 60 ms son de los proveedores, no de GB"

| veredicto | evidencia |
|---|---|
| **confirmada en lo esencial**: fue Steno | `[MEDIDO: por_minuto.tsv de 210000Z_r1_n050]` minutos 0-4 del escalón: `stt_lat_p50` 181 → 1.269 → 2.529 → 2.424 → 1.908 ms (p90 hasta 14,3 s), `no_transcript` 0/9/14/23/30 por minuto, `stale_stt` 10/9/51/14/50, `ttfab_p50` 1,2 → 3,4 s; minutos 5-11: `stt_lat_p50` 393-653 ms (3-4× la base de ~130), `no_transcript` 17-22/min. Conexión a Steno: p90 1,99 s, máx 5,98 s (`connect_ms`). 24 líneas `[error]`: 15 `Steno fallback transcribe failed … timed out during opening handshake`, 8 `Persistent STT connection failed`, 1 `Agent-speech barrier timed out`. Circuit breakers del LLM: **0**; `ttft_p90` 207-390 ms normal. `infer_ms_p50` 77-140 ms: smart-turn no estaba saturado. |

Dos matices: (1) De los 476 perdidos, 423 son `sin_respuesta` (el bot no empezó en 10 s: 280 en sesiones que no interrumpen, 143 en las que sí). En las que no interrumpen, `perdidos == sin_respuesta` en los 10 escalones; en las que interrumpen hay un extra que no es del proveedor (ver sección 6, mid-speech). (2) "No de GB" es verdad a medias: GB espera el Finalize **dentro del ciclo** (hecho D): `cp_post_p90_max` 10.716 ms y `cp_steno_p90_max` 1.672 ms en ese escalón; un proveedor lento se vuelve un loop parado para esa sesión (las demás no lo sienten: `ping_ctrl_p99` 7 ms). (3) El escalón N=50 de 20 ms (23:26) también arrancó con Steno degradado (`no_transcript` 134, `stt_fin_p90` 602, 17 errores de handshake, conexión máx 2,2 s) y los dos N=100 no (conexión p90 140/168 ms). Con la escalera ascendente el estado del proveedor se hereda del escalón anterior: confusor ligado al orden.

### Afirmación 8 — "El criterio 1 (`cp_cycle_p90_med ≤ 5 ms`) está mal planteado para lotes"

| veredicto | evidencia |
|---|---|
| **confirmada, y es peor de lo dicho** | `[CÓDIGO]` hecho A: mide por lote **y sólo cuando el loop va atrasado y no hay TTS**. `[MEDIDO]` `cycle` sigue a `job` y `job` sigue a `fpc`: ensayo 1 (reloj) fpc 9,0 → `job` p50 5,0; 60 ms N=10 fpc 2,9 → 2,1; 20 ms N=10 fpc 6,7 → 2,3. El "5 ms" se compara con un número que depende de cuántos mensajes se juntaron, y la muestra cubre 0,5 % de los mensajes a 60 ms N=10. Qué hacer con el criterio es decisión del dueño `[ABIERTO]`; lo que se puede decir es que el instrumento no mide lo que el criterio supone. |

### Afirmación 9 — "El laboratorio pide ~3,5× más smart-turn por llamada que producción (0,25 contra 0,0704/s)"

| veredicto | evidencia |
|---|---|
| **dirección confirmada, magnitud no** | `[MEDIDO]` inferencias por segundo-sesión (Σ `close_attempts` ÷ 598 s ÷ N): 0,187/0,157/0,143/0,150/0,131 (60 ms) y 0,163/0,153/0,165/0,160/0,122 (20 ms); ensayos 0,131/0,111/0,142 (ventana de 120 s). Contra 0,0704 de producción: **1,7-2,7×**, no 3,5×. Con 1,4-1,6 inferencias por turno, no 3. El 0,25 no aparece en los datos. El K del laboratorio sigue siendo conservador en esta dimensión, pero menos. |

### Afirmación 10 — "El trabajo por trama es mayor que la regresión histórica (0,25 ms/trama); candidatos: `noisereduce` o hilos de ORT en 1"

| parte | veredicto | evidencia |
|---|---|---|
| trabajo por trama mayor | **refutada: error de unidades** | Hecho B: a 60 ms una "trama" del probe es un mensaje de 60 ms = 3 tramas históricas de 20 ms. `[MEDIDO]` `job_p50_med / fpc / 3`: 60 ms → 0,24/0,24/0,20/0,18/0,19 ms por 20 ms de audio (N=10…100); ensayo 1 → 5,0/9,03/3 = 0,18. A 20 ms: `job_p50_med / fpc` = 0,34/0,32/0,32/0,32/0,30. La regresión histórica da por lote `0,2507·fpc + 0,42`: a 60 ms N=10 predice 2,62 ms (8,8 tramas-equivalentes) y se miden 2,1 (p50) / 2,3 (p90); a 20 ms N=10 predice 2,10 y se miden 2,3 (p50, +10 %) / 2,7 (p90, +29 %). El "0,64 ms/trama" del README es 5,8/9,03 mensajes de 60 ms. |
| `noisereduce` corre | **refutada** | hecho F: guarda por trama de 800 muestras; 0 cargas de la biblioteca en los 13 logs. |
| hilos de ORT en 1 | **no evaluable aquí** | los dos brazos y los históricos de CLAUDE.md corrieron con configuraciones de hilos distintas entre sí, pero dentro de estas corridas no hay variación: nada se les puede atribuir. |

`cpu_por_llamada` sí es mayor que el histórico (0,033-0,046 contra 0,022) `[MEDIDO]`, pero no por el job: el cliente del laboratorio hace un turno cada ~8-14 s por sesión (producción: una inferencia cada 14 s), con STT en vivo, LLM y TTS en cada turno, y el log a INFO escribe 92-107 MB por escalón de 10 min. No es comparable sin igualar el perfil de turnos `[INFERIDO]`.

## 5. Pedidos adicionales

### 5.1 Hilos de las bibliotecas

- `[MEDIDO]` `hilos_ort_omp_openblas=1 1 1` en los 13 `efectivo.txt`; `[arranque] hilos fijados: ORT_INTRA_OP=1 OPENBLAS=1 OMP=1` en los 13 `gb.log`. `[CÓDIGO]` smart-turn: `so.intra_op_num_threads = int(os.environ.get("GB_ORT_INTRA_OP", "1"))`, `inter_op 1`, `ORT_SEQUENTIAL` (`smart_turn_processor.py` L119-124). Silero no crea `SessionOptions` propias (`grep -n SessionOptions vad_processor.py` → nada): corre con los defaults de ORT, es decir, la variable sólo gobierna smart-turn.
- **Sin giro de hilos** `[MEDIDO]`: `cpu_nucleos` mínimo en la ventana a N=10 es 0,25 (60 ms) y 0,38 (20 ms) núcleos, mediana 0,45/0,58; E1 medía 1,57 núcleos de giro fijo con hilos sueltos. `nlwp` del proceso: 5 al arrancar, 20-32 al final del escalón (`procesos_inicio.txt`/`procesos_fin.txt`), contra los ~15 hilos de OpenBLAS/OpenMP en giro del corpus previo.
- ¿Es compatible el techo con smart-turn de un hilo detrás del lock? Sólo a medias: 47,6 ms por run darían ~21 inferencias/s; se miden 12-13/s con ~0,9 s de espera (afirmación 4). Compatible si el run bajo carga tarda ~75 ms o si el cuello es el executor de 12 hilos.
- Experimento de una variable: `GB_ORT_INTRA_OP=1` contra `2` (y luego `4`), misma imagen, 60 ms, N=100, 3 repeticiones. Lectura de vuelta obligatoria: (1) `hilos_ort_omp_openblas` en `efectivo.txt` con el valor nuevo sólo en el primero; (2) `cpu_nucleos` **mínimo** a N=10 (giro ⇒ sube hacia 1 núcleo por hilo extra aunque no haya tráfico; ORT gira por defecto en su pool); (3) `nlwp` al final; (4) `ps -L -o tid,pcpu,comm -p <pid>` dentro del escalón para ver hilos con CPU constante sin carga; (5) `inference_ms` p50/mín y cierres/s como resultado.

### 5.2 CYCLE-PROBE ventana por ventana

Campos de cada línea `[CÓDIGO: _CycleProbe.maybe_emit]`: `session_id, cycles, frames, frames_per_cycle, cierres, pct_cierres` (acumulados por sesión), `<fase>_p50, <fase>_p90` para `cycle, pre, submit, job, resume, resume_vad, smartturn, post, steno, otros` (sobre los últimos ≤ 600 ciclos por lote), e `inbox, carry, consumed` del ciclo que emite. Serie completa por escalón en `cycle_probe.tsv` y resumen por minuto en `por_minuto.tsv` (script de la sección 0). Extracto `[MEDIDO]` (mediana de los p90 por minuto; `inbox` mediano; `infer_ms` p50 de las líneas de cierre; `stt` = `stt_latency_ms` p50 de `LATENCY BREAKDOWN`):

**60 ms N=50 (21:25 UTC)** — episodio de Steno pasajero y fuerte en los minutos 0-4, parejo y leve después:

| min | `cycle` | `job` | `resume_vad` | `smartturn` med/máx | `post` med/máx | `infer_ms` | `stt` p50/p90 | `no_transcript` | `ttfab` p50 | cb |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 15,1 | — | 3,1 | 0 / 217 | 0,1 / 2.630 | 86 | 1.269 / 2.698 | 9 | 2.086 | 0 |
| 2 | 10,8 | — | 2,8 | 0 / 280 | 0,2 / 1.579 | 81 | 2.529 / 14.256 | 14 | 2.787 | 0 |
| 3 | 10,5 | — | 2,6 | 0 / 280 | 0,1 / 2.630 | 85 | 2.424 / 5.738 | 23 | 3.351 | 0 |
| 4 | 10,3 | — | 1,9 | 0 / 292 | 0,1 / 2.867 | 83 | 1.908 / 12.458 | 30 | 2.576 | 0 |
| 5 | 10,6 | — | 2,5 | 0 / 292 | 0,2 / 10.716 | 100 | 478 / 2.214 | 22 | 1.604 | 0 |
| 7 | 16,3 | — | 4,1 | 0 / 430 | 0,2 / 10.716 | 141 | 502 / 2.795 | 22 | 1.690 | 0 |
| 11 | 13,2 | — | 2,8 | 0 / 290 | 0,2 / 3.338 | 93 | 502 / 1.284 | 17 | 1.578 | 0 |

(`job` por minuto no está en la tabla resumida; en la ventana es 5,2 p90 / 2,2 p50 con fpc 3,7.)

**60 ms N=75 y N=100** — parejo desde el minuto 1: N=75 `cycle` 85 en el minuto 1 y 285-412 después, `smartturn` 83-227, `post` 106-115 en 9 de 11 minutos (0,2-0,6 en los minutos 1 y 3), `infer_ms` 209-278, `stt` p50 138-157, `ttfab` 1,40-1,58 s. N=100: `cycle` 1.878 → 2.472 → 2.525 → … → 2.652; `smartturn` 1.769 → 2.361 → … → 2.530; `post` 120-133; `infer_ms` 908-918 en los 11 minutos; `inbox` 18-32; `stt` p50 117-130; `ttfab` 3,2-4,4 s.

**20 ms N=75 y N=100** — parejo: N=75 `cycle` 32-40, `resume_vad` 24-31, `smartturn` **0** en todos los minutos (máx 411-1.439), `post` 0,1-0,2, `infer_ms` 348-575, `inbox` 1, `ttfab` 1,5-1,9 s. N=100: `cycle` 38-48, `resume_vad` 35-44, `smartturn` 0 (máx 0 desde el minuto 2), `post` 0,1, `infer_ms` 739-967, **`inbox` 75-130**, `stt` p50 147-176, `ttfab` 2,6-3,5 s, `corte` p90 4,3-7,4 s.

Fase por fase entre brazos (mediana de p90 en la ventana, 60 ms → 20 ms): `pre` 0 → 0; `submit` 0,4-0,5 → 0,4-0,5 (p90 de p90: hasta 4,3 a 60 ms, 0,8 a 20 ms); `job` 2,3/2,6/5,2/5,9/8,8 → 2,7/3,1/3,9/4,2/4,4 (por 20 ms de audio el de 60 ms es menor, afirmación 10); `resume_vad` 0,4/0,9/2,9/17,1/9,4 → 0,5/2,0/10,5/28,8/40,5; `smartturn` 0/0/0/160/2.469 → 0/0/0/0/0 (dilución, afirmación 5); `post` 0,1/0,1/0,2/111/128 → 0,1/0,2/0,2/0,2/0,1 (misma dilución: el Finalize existe en los dos, `stt_fin` p50 123-139 contra 121-162); `steno` 0,1-0,2 en todos; `otros` 0 en todos. Lo único comparable sin sesgo de muestra es el `job` normalizado por audio y las métricas que no vienen del probe.

`smartturn` a 20 ms: no hay p99 ni máximo en la línea; el máximo de los p90 emitidos es 0,0 a N=100 y 1.439 a N=75. La espera real está en `inference_ms`: 954 ms p50 a N=100.

`post`: es Finalize de Steno (`send_finalize` en línea) más, cuando aplica, el cancel del TTS (0,2 s fijos) y la espera de "Reentry hold"/deferral; esperar la habla del bot no está en `post` (el LLM+TTS se lanza con `_spawn_logged`). Para separar Finalize del resto hace falta un sello alrededor de `send_finalize`; a nivel INFO sólo se cruza con `STT Finalize completed stt_latency_ms`, que a 60 ms N=75/100 (139/123 ms) explica `post` (111/128).

## 6. Hallazgos que no estaban en las afirmaciones

1. **Atraso por sesión que no se drena** `[MEDIDO]`. A 20 ms N=100 el `inbox` mediano del probe es 113 mensajes (2,3 s) todo el escalón; una sesión (`382710eb` = `td1load-l84-ferrocarril`) llegó a **13.593 mensajes (4,5 min de audio)**, y su `end_ok` dice `speak=26 audio_done=8 ttfab_perdidos=17 sin_respuesta=10`. A 20 ms N=50 hay sesiones que entran con 700 mensajes (14 s) en la rampa y a los 300 s todavía deben 486. En los ensayos 1-2 (60 ms) el `inbox` máximo fue 498-503 (30 s). Mecanismo `[INFERIDO]`: con TTS vivo la ruta es por trama (`_batch_eligible` falso), un viaje al pool por mensaje; el propio código estima ~29 ms de viaje por trama (`_process_frames_batch_sync`, docstring), que a 50 mensajes/s no alcanza a drenar. El disparador inicial es la espera de la conexión a Steno en el primer mensaje (`_audio_pre_vad`). Cómo confirmarlo: `inbox` y `max_handler_ms` por sesión a lo largo del tiempo con TTS vivo contra sin TTS.
2. **El cancel del TTS siempre agota 200 ms** (afirmación 6). Cambiarlo es un parche a GB (`parches/`), no de este paso.
3. **Cierres mid-speech suben a N=100** `[MEDIDO]`: `Smart Turn: Turn complete (mid-speech periodic check)` = 16/18/68/101/**574** (60 ms) y 7/22/85/185/**410** (20 ms); 11,6 % y 8,0 % de los cierres a N=100 contra 1-4 % antes. Son respuestas que empiezan con el usuario todavía hablando: explican el extra de `ttfab_perdidos` sobre `sin_respuesta` en las sesiones que interrumpen (113−13 a 60 ms N=100, 279−46 a 20 ms) y parte de los `Barge-in gates not met`. Mecanismo `[ABIERTO]`: con 0,9 s de cola por inferencia, dos chequeos mid-speech consecutivos (umbral 0,85, 2 aciertos) se resuelven tarde; la relación con la cola es plausible pero no está demostrada.
4. **`STT auto-flush` crece con la cola** `[MEDIDO]`: 2/0/198/73/**840** (60 ms) y 0/4/26/98/159 (20 ms). A 60 ms N=100 Steno hace flush por su cuenta (sin Finalize en > 2 s) mientras GB espera la inferencia; es otro camino por el que la cola de smart-turn degrada el E2E `[INFERIDO]`.
5. `Barge-in gates not met` con `first_blocked_gate=vad_threshold` y `max_speech_prob` ~0,8 sobre silencio digital del cliente (0xFF): Silero da probabilidades altas justo después de voz `[MEDIDO: una línea de muestra; INFERIDO]`. No afecta nada aquí; vale para la calibración de los gates.

## 7. Lo que no se puede saber con los logs a INFO, y cómo medirlo

| pregunta | por qué no se sabe | cómo medirlo (una variable, sin tocar producción) |
|---|---|---|
| ¿Cuánto espera el lock y cuánto dura el `session.run`? | `inference_ms` junta features + lock + run; `Smart Turn prediction` es `debug` | sello en `predict_turn_complete` antes y después de `with self._inference_lock` y del `run` (parche en `parches/`), o `GB_LOG_LEVEL=debug` sólo en el laboratorio y leer `inference_time_ms`; comparar N=10 contra N=100 |
| ¿Cuánto es cola del executor por defecto? | la fase `smartturn` suma gate + cola + inferencia | sello alrededor de `run_in_executor(None, …)`; o dar a smart-turn un executor propio de tamaño conocido y variar sólo su tamaño |
| ¿Cuántas inferencias mid-speech hay (no sólo las que cierran)? | `close_attempts` es el total por turno; el desglose transición/mid-speech está a `debug` | contador `midspeech_attempts` en `_close_diagnostics`; mientras tanto, `close_attempts − 1` por turno es la cota superior de mid-speech (≈ 0,4-0,6 por turno) |
| ¿Qué pasa en los ciclos de un mensaje (95-99 % del tráfico a 60 ms)? | el probe no los ve (hecho A) | mover `probe.add` a `_run_audio_frames` para cubrir las dos rutas, con un campo `via=frame\|batch` |
| ¿`send_finalize` o cancel/deferral dentro de `post`? | un solo tiempo | sello alrededor de `send_finalize` (`steno_finalize_ms`) como ya existe para el envío en vivo |
| ¿Qué cortó de verdad el barge-in? | el cliente sólo ve el último byte recibido | cruzar `Barge-in triggered` y `TTS cancellation complete` de GB con `barge_t` del cliente por `call_uuid` (`Per-call variables received` da `session_id ↔ td1load-lN-marca`) |

## 8. Confusores entre los dos brazos (más de una variable)

1. `PACKET_MS` **y** `MIDSPEECH_N` cambian juntos (17 ↔ 51); por el hecho B son equivalentes en tiempo (1,02 s), pero el chequeo cae en fase distinta respecto a la frase.
2. Hora: 21:00-22:01 contra 23:01-00:03 UTC; proveedores en Modal con estado propio (Steno degradado al arrancar **los dos** N=50; LLM con 10 circuit breakers a 20 ms N=75 y 0 en el resto de la escalera).
3. Orden ascendente en los dos brazos: el estado del proveedor y del contenedor anterior se hereda; la repetición al revés del README no se corrió.
4. Cobertura del probe 0,5-5 % contra 1-44 % (hecho A) y `fpc` distinto (mensaje de 60 contra 20 ms): ningún `cp_*` es comparable sin normalizar.
5. Largo de las respuestas del LLM sin control (cientos de `LLM response repeats previous assistant turn` por escalón, en inglés y español): decide `fin=natural` contra `cortado` y el largo del turno.
6. Un proceso `claude` (PID 55662, 2-4,5 % de un núcleo, 20-27 hilos) corrió en el host durante todo el brazo de 20 ms y no durante el de 60 ms (`procesos_inicio.txt`). No está en el cpuset de GB por fuerza, pero es CPU del host.
7. Una repetición por brazo; sin repetición no hay varianza para ningún número de arriba.

## 9. Próximos experimentos, una variable cada uno, en orden

1. **Repetir la escalera de 60 ms tal cual, 3 repeticiones con la 2.ª al revés** (`REPS=3`). Sin esto no hay varianza para ningún criterio. Antes, comprobar la salud de Steno/LLM con una llamada de control y registrar la hora.
2. **int8 contra fp32 a 60 ms, N=100** (`SMART_MODEL=/app/smart-turn/smart-turn-v3.2-int8.onnx` si está en la imagen; si no, otra imagen idéntica salvo el modelo). Si el lock + run es el mecanismo, los cierres/s suben hacia ×1,75 (27,1 contra 47,6 ms) y `infer_ms` mínimo baja; si es la cola del executor, casi no cambia. Leer `inference_ms` p50/mín, cierres/s, `ttfab`.
3. **`GB_ORT_INTRA_OP=1` contra `2`** a 60 ms N=100 (sección 5.1), con la lectura de vuelta del giro.
4. **Executor propio para smart-turn** (parche de una línea en `parches/`: `run_in_executor(self._st_executor, …)` con 4 hilos) contra el por defecto. Separa el candidato (a) del (b) de la afirmación 4.
5. **Quitar `_inference_lock`** (ORT 1.24 documenta `Run` seguro entre hilos, ya anotado en CLAUDE.md) con `intra_op=1`. Es el que decide si el lock es el techo. Es un parche a GB: va a `parches/` y se mide contra la misma escalera.
6. **`MIDSPEECH_N=50` contra `17`** a 60 ms (3,0 s contra 1,02 s entre chequeos): mide cuánto del tráfico de inferencias y de los cierres mid-speech es del chequeo periódico.
7. **Probe en las dos rutas** (sección 7) antes de volver a usar `cp_*` para una rodilla.

## 10. Correcciones a documentos existentes (no aplicadas aquí)

- README, tabla de métricas: "`cp_fpc_med`: a 60 ms el mínimo es 3" → el mínimo de un lote es 2 mensajes; una trama del probe es un mensaje.
- README, "Ensayo del 2026-10-19": "trabajo por trama ≈ 0,64 ms (la regresión da 0,25)" → 0,64 ms por mensaje de 60 ms = 0,21 por 20 ms; dentro de la regresión. El candidato `noisereduce` queda descartado por el hecho F.
- Tabla de corridas del encargo: el ensayo 2 fue Steno sin handshake, no LLM frío.

## Párrafo para ESTADO ACTUAL de `CLAUDE.md`

- `[MEDIDO 2026-10-19/20]` **M1 (línea base del monolito) corrido en `.143` a 60 ms y a 20 ms, una repetición cada uno, N = 10-25-50-75-100, imagen `goldenbridge:remarcacion-v1-jef-fp32` (`release/remarcacion-v1` + `Jef` + fp32), config `default.yaml` de la imagen, proveedores reales.** Auditoría en `experimentos/2026-10-10_m1-linea-base/AUDITORIA_20261019.md`. Resultado: **ningún brazo es "sano" más allá de N=10** por los criterios preregistrados (`ttfab_p50` +14-17 % ya a N=25). A 60 ms la CPU por llamada baja 22-32 % en toda la escalera (3,30 contra 4,48 núcleos a N=100) y el loop no acumula (`ping_ctrl_p99` 14 contra 32 ms a N=100), pero **los dos brazos chocan con el mismo techo: ~7,3-7,9 cierres de turno/s y 12-13 inferencias de smart-turn/s, con `inference_ms` p50 ≈ 0,9 s a N=100 en ambos** (1,4-1,6 inferencias por turno; 1,7-2,7× la tasa de producción). A N=100 el E2E es peor a 60 ms (`ttfab_p50` 4,1 contra 3,0 s). `[CÓDIGO]` el techo pasa por el `_inference_lock` global + el executor por defecto de 12 hilos; cuál de los dos manda queda `[ABIERTO]` (experimentos de una variable en la auditoría §9: repeticiones, int8, `GB_ORT_INTRA_OP`, executor propio, sin lock). `[CÓDIGO]` **CYCLE-PROBE sólo mide ciclos por lote** (0,5-5 % de los mensajes a 60 ms, 44 % a 20 ms N=100), sus percentiles son sobre 600 ciclos acumulados y no trae p99: no sirve para comparar brazos ni para la rodilla; `cp_smartturn=0` a 20 ms es dilución. Un mensaje de 60 ms entra como una trama de 480 muestras: el trabajo por 20 ms de audio (0,18-0,24 ms) está dentro de la regresión histórica; `noisereduce` nunca corre (guarda de 100 ms por trama). El escalón N=50 a 60 ms fue un episodio de Steno (handshakes de hasta 6 s, `stt_latency` p50 2,5 s en los minutos 1-4), no de GB; GB espera el Finalize dentro del ciclo. El cancel del TTS por barge-in agota siempre su `timeout=0.2`. Sin giro de hilos (CPU mínima 0,25 núcleos a N=10, `1 1 1` leído de vuelta).
