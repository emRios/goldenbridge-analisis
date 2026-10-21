# refGolden — contexto del proyecto

Workspace de análisis de **GoldenBridge**, motor de voz con IA para campañas
salientes de banca. El trabajo es de **capacidad y escalado**, no de features.

---

## LEE ESTO PRIMERO

**`context/ARQUITECTURA_STACK.md` es la descripción autoritativa de la
arquitectura.** Cárgala como contexto al empezar. No la cuestiones, no propongas
alternativas a las decisiones que ya recoge, no re-derives lo que ya dice.

Resumen para orientarse (el detalle está en ese documento):

```
Marcador (Lambda del proveedor)  →  origina las llamadas
Asterisk (proveedor, 10.132.112.148:8088 ARI)
      ═══ TÚNEL (termina en el borde de la VPC) ═══
.143 (16.58.43.143 púb · 172.31.7.92 priv)
      ai-engine (Python, asyncio, 1 proceso) — AudioSocket :9092, slin16 320B/20ms
      ├─ convierte slin16 ⇄ µ-law 160 B
      └─ WS loopback 127.0.0.1:8765 → GoldenBridge
GoldenBridge (Python 3.14t, sin GIL, uvloop) — /v1/agent/converse
      pipeline por trama: µ-law→pcm16 → soxr → Silero VAD → smart-turn (ONNX)
      orquesta STT / LLM / TTS, que son REMOTOS
```

**Destino:** GB se mueve a Modal (imagen `goldenbridge:314t`, endpoint único,
autoescalado). **Asterisk, el túnel y el ai-engine se quedan en `.143`.** El
tramo engine⇄GB pasa de loopback a red pública. Con el smart-turn cuantizado
(imagen `314t`) la flota exige `avx512_vnni`; **con la imagen fp32
(2026-10-14) ya no** — ver ESTADO ACTUAL.

---

## REGLAS DE TRABAJO

1. **La arquitectura objetivo está decidida.** No reabrir: afinidad de CPU
   (A1/A2), `nice`, `SCHED_FIFO/RR`, EEVDF custom slice (refutado: kernel 6.8,
   `setattr`→0 pero `getattr`→`runtime=0`). `cgroups` sólo como protección
   operativa en EC2, nunca como solución arquitectónica.
   **Excepción vigente (2026-09-28 y 2026-10-09):** el split Media–Conversación
   y el motor Media (incluido un MediaWorker nativo) **quedaron reabiertos por
   encargo explícito** y tienen estrategia aprobada — ver "ESTADO ACTUAL".
   Ese trabajo sí se hace; lo demás de esta regla sigue cerrado.
2. **Etiquetar toda afirmación**: `[MEDIDO]` · `[CÓDIGO]` · `[INFERIDO]` ·
   `[ABIERTO]`. No inventar capacidades, costes, número de máquinas ni
   porcentajes de CPU sin decir qué medición los demostraría.
3. **Una variable por campaña.** Ninguna métrica interna basta por sí sola:
   toda mejora necesita un número punta a punta (precedente: bajar de 12 a 6
   workers mejoró `resume_vad` 78 % y empeoró el sistema — −17 % turnos,
   `inbox` p99 ×7).
4. **Nunca validar una capacidad por el código de retorno de un syscall.** Leer
   de vuelta el estado efectivo.
5. **No recomendar un refactor grande antes de agotar medición y PoC baratos.**

---

## ESTADO ACTUAL — leer antes de proponer nada

**Estrategia aprobada:** `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md`. Dos carriles:

- **M · medir.** M0 corregir el gate de arranque de la imagen · M1 línea base
  del monolito a 60 ms · M2 PoC Python con un loop por llamada.
- **C · construir (sans-I/O, independiente de la estructura de hilos).**
  C0 códec+soxr · C1 Silero+VAD · C2 log-mel+SmartTurn · C3 gates/watchdog ·
  C4 dueño por llamada (escenarios A, S1; B condicionado) · C5-C8 red real,
  contrato y Conversación, sólo tras el veredicto del laboratorio.

**Estado al 2026-10-16** (esta rama, `paso/smartturn-fp32`; **no integrado a `main`**):

- `[MEDIDO]` **Imagen con el modelo de fin de turno sin cuantizar:
  `goldenbridge:314t-fp32`**, construida en la laptop (WSL) y **cargada en
  `.143`**. Usa `smart-turn-v3.2-gpu.onnx` (fp32) en lugar del int8. El
  guardián de arranque corregido (0 saltadas, 12/12, contrato de Modal,
  **ya no exige `avx512_vnni`**) pasa en el Xeon 8488C de `.143` **y** en un
  Ryzen 7535HS sin VNNI. Llamadas reales de prueba en `.143` (contenedor en el
  puerto 8776, engine desviado con `GOLDEN_WS_URL`): "se escucha bien", 8
  hilos en llamada, ninguno girando. Hilos: `GB_ORT_INTRA_OP`,
  `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS` = 1. Detalle, números y guía en
  `experimentos/2026-10-14_smartturn-fp32/` (`README.md`, `GUIA.md`).
- `[MEDIDO]` fp32 frente a int8, 3.136 casos, tres umbrales: el **fp32 no se
  mueve entre CPUs** (máx Δp 1e-5, 0 volteadas); el **int8 sí** (279-363
  volteadas, 9-12 %). fp32 e int8 se contradicen entre sí 15-31 % en la misma
  CPU: comportamiento distinto, **sin veredicto de cuál es mejor**. Latencia
  en `.143`: 47,6 ms fp32 contra 27,1 ms int8.
- `[CÓDIGO]` Código de la imagen fp32: rama local `fix/smartturn-fp32` en
  `~/gb-fp32` (WSL), commits `1406660` (guardián), `cbe7e4c` (fp32),
  `f34600e` (referencia regenerada, 15 casos, umbral 0,35). **No está en el
  remoto de GoldenBridge.** Para Modal, `active.yaml` debe cambiar
  `smart_turn.model_path` a `…-gpu.onnx` (si no, carga el int8 y el guardián
  no lo detecta) y conservar el entrypoint.
- `[CÓDIGO]` **`release/remarcacion-v1`** (`3ec0732`) = `api` + ventana tardía
  del detector de buzón + F261 + remarcación; **sin los arreglos de Docker**
  (su guardián es el de `pytest | tail`, que no bloquea). Probado en copia
  descartable: arreglos de Docker + guardián + fp32 + referencia se aplican
  con un solo conflicto (`config/default.yaml`). Imagen
  `goldenbridge:remarcacion-v1-fp32` `[ABIERTO]` sin construir.
  Procedimiento en `prompts/PROMPT_imagen_docker_goldenbridge_20261016.md`.
- `[MEDIDO 2026-10-19/20]` **M1 (línea base del sistema actual) corrida a 60 y a 20 ms**,
  una repetición, N 10-100, imagen `goldenbridge:remarcacion-v1-jef-fp32`, proveedores
  reales, cliente que espera la respuesta del bot. Detalle en
  `experimentos/2026-10-10_m1-linea-base/AUDITORIA_20261019.md` (+ validación de Codex).
  **El coalescer a 60 ms reduce la carga del event loop y mejora las llamadas:** −22 a −32 %
  de CPU por llamada; a N=100, `ping_ctrl_p99` 14 contra 32 ms y corte 2,2 contra 6,2 s. El
  loop no se atrasa, pero cada sesión acumula mientras espera su smart-turn (`inbox` mediano 28).
  **Los dos brazos chocan con el mismo techo:** ~7,5 cierres de turno/s y 12-13 inferencias de
  smart-turn/s por proceso, ~0,9 s por inferencia a N=100; por eso a N=100 el `ttfab_p50` es peor
  a 60 ms (4,1 contra 3,0 s). Mecanismo `[ABIERTO]`: el `_inference_lock` global `[CÓDIGO]` o el
  executor por defecto (~12 hilos `[INFERIDO]`). K₆₀ = 10 y a 20 ms ninguno por los criterios.
  CYCLE-PROBE sólo ve ciclos por lote: no sirve para la rodilla. El coalescer del engine real
  está validado (480 B, 60-62 ms). Siguiente: 3 repeticiones a 60 ms, luego int8 contra fp32.
  Rama `paso/m1-linea-base`.
- **Refactor:** traspaso a una sesión nueva en
  `prompts/PROMPT_refactor_goldenbridge_20261016.md`.

**Anterior:** M0 (el guardián de arranque) **aceptado** el 2026-10-10
(`experimentos/2026-10-09_m0-gate/`). Orden acordado entonces:
M60 → P → A (C++) → S1 → (B); P (M2) quedó cancelado el 2026-10-13.

- `[CÓDIGO]` **Producción en `.143` no corre `api@08932a6`:** el checkout de
  `/srv/GoldenBridge` está en `fb8a3b9` (`feat/f261-call-summary`, 2026-10-07,
  con cambios locales) = `api` + 24 commits de F261, 393 líneas nuevas en
  `websocket_server.py`. Ramas de GoldenBridge: `api` (auditada),
  `feat/f261-call-summary` (producción `.143`), `feat/modal-migration`
  (imagen de Modal, `src/` y `config/` idénticos a `api`),
  `release/remarcacion-v1`.

**Hechos nuevos que no están más abajo** (verificados; detalle en
`doctos/CONTRASTE_CODEX_MEDIAWORKER_CPP_20261009.md`):

- `[CÓDIGO]` **Gate de arranque de la imagen (M0) — corregido el 2026-10-09.**
  El `pytest | tail` que no bloquea existe **sólo en la rama `api`**
  (`6e7843c`). Los arreglos de Docker del 31-ago (`24f9c6b`, `fb44e8a`,
  `9c024fd`) están **sólo en `feat/modal-migration`** y nunca llegaron a
  `api`. La imagen entregada (`files/goldenbridge-314t.tar.gz`, creada
  2026-08-31) trae el arranque de `24f9c6b`: **falla cerrado** ante una
  prueba fallida (muere mudo). Huecos reales en la imagen: (1) sin
  `vad_probs.npz` pytest **salta** las 6 pruebas de Silero y sale con 0;
  (2) **no cumple el contrato de ENTRYPOINT de Modal** (no hace `exec "$@"`),
  así que en Modal o el runtime no arranca o el entrypoint fue quitado y el
  gate no corre. `[ABIERTO]` cuál de las dos: falta el archivo de despliegue
  de Modal. Parche (sobre `feat/modal-migration`) y prueba en
  `parches/2026-10-09_goldenbridge_gate-arranque/` y
  `experimentos/2026-10-09_m0-gate/` (rama `paso/m0-gate`).
- `[MEDIDO]` **Endpoint Modal en vivo (2026-10-10 05:11 UTC, un `GET /health`
  con proxy-auth):** `healthy`, `max_sessions: 120` efectivo, respuesta en
  **26,4 s** = arranque en frío ⇒ la app escala a cero sin tráfico
  (`[INFERENCIA]` no hay `min_containers=1`). Sin cabeceras: 401. El
  `/health` no informa CPU ni si corrió el gate.
- `[CÓDIGO]` **Dos líneas de despliegue divergentes en GoldenBridge:** `api`
  (código, auditado) y `feat/modal-migration` (Docker + traspaso a Modal en
  `docker/README.md`, con el ejemplo de wrapper `Image.from_registry` +
  `@modal.asgi_app` + `@modal.concurrent(max_inputs=50)`).
- `[DOCUMENTACIÓN]` ORT 1.24.4: `InferenceSession::Run` es seguro entre hilos.
  El comentario de SMART L224 está equivocado; el lock de smart-turn sólo
  cubre `run`, las features van fuera.
- `[CÓDIGO]` ai-engine `d059295` descarta todos los JSON de GB (exige
  `call_id`), no tiene flush de reproducción y no reconecta si cae el WS a GB.
- `[INFERENCIA]` Ninguno de los precedentes auditados (FreeSWITCH, LiveKit)
  corre IA por trama dentro del trabajador dueño: la propuesta es una
  hipótesis a falsar, no una solución copiada.
- `[DECISIÓN DEL DUEÑO 2026-10-13]` **GoldenBridge se reescribe en el stack
  propio (C++ o C#). Es estratégica.** Criterios: control de punta a punta,
  previsibilidad de K ("500 más es aritmética"), diagnóstico por llamada,
  contención de fallas. **Costo de cómputo y días de desarrollo no son
  restricción: no argumentar con ellos.** Carril C sigue con ese propósito;
  M2 cancelado; M1 sigue como línea base y banco. Requisitos de control en
  `DICTAMEN_ESCALAR_SIN_REFACTOR_2000_20261013.md`.
- `[DATO DEL USUARIO]` (2026-10-12/13) **K real por proceso 40-50, a veces
  38-45 según los ciclos de conversación** (cae en la banda degradada de la
  curva medida). **El refactor, si se hace, es todo en C++**, incluida la
  Conversación. **Existe un ai-engine en C#** (~500 llamadas por engine) que
  no está en el workspace. Valores de hilos en golden-modal: `[ABIERTO]`. El
  usuario pidió `1` al equipo de Modal y no sabe qué quedó aplicado; la imagen
  los fija a 1 (`[CÓDIGO]` config del tar). Lo confirma la línea
  `hilos fijados` del guardián en el log de cada contenedor.
- `[DOCUMENTACIÓN]` Modal cobra $0,0472 por núcleo físico-hora (= 2 vCPU),
  $0,008/GiB-h, el mayor entre pedido y usado; egreso $0,04/GiB desde
  2026-10-01; vida máxima de un WebSocket **sin documentar**.

**Índice de decisiones, en orden:**
`doctos/AUDITORIA_REFACTOR_websocket_server_api_08932a6.md` →
`doctos/DICTAMEN_MULTIPROCESO_POR_TAREA_MODAL_20260928.md` →
`doctos/DICTAMEN_FRONTERA_MEDIA_CONVERSACION_20260928.md` →
`repo_analisis/` (8 motores de media) →
`doctos/DICTAMEN_CODEX_MEDIAWORKER_CPP_20261009.md` →
`doctos/CONTRASTE_CODEX_MEDIAWORKER_CPP_20261009.md` →
`doctos/ESTRATEGIA_MEDIAWORKER_20261009.md` →
`doctos/DICTAMEN_ESCALAR_SIN_REFACTOR_2000_20261013.md` (hechos de costo,
trazabilidad y fallas por contenedor; **decisión del dueño: reescribir en el
stack propio, estratégica**; requisitos de control para la reescritura;
M2 cancelado; `[ABIERTO]` C++ o C#).

---

## LA PREGUNTA ABIERTA CENTRAL

> **K = cuántas llamadas concurrentes aguanta UN task de GB antes de degradarse.**

De K sale todo: `contenedores = 2000 / K`, `max_inputs`, `max_sessions`, y si
hace falta o no cambiar la composición. **Objetivo de fase: K = 100.**

**El CPU no es la restricción.** Cuatro mediciones independientes:
E1 (N=100) 3,90 núcleos · E4 sandbox (N=50) 12,54 · E3 producción 1,76 ·
mi regresión 0,022 n/llamada. Descontando el spin fijo (1,57), E1 da **0,0233**
y mi regresión **0,0220** — convergen. E4 (0,219) es el atípico, y es el único
que sostiene el *"100 llamadas = 22 núcleos, no cierra en una caja"* de
`doctos/CONCURRENCIA_100 §6`. **100 llamadas piden ~2,3 núcleos, no 22.**
El techo lo pone la **coordinación en un hilo único**, no la CPU
(E1 a N=100: `submit+resume` p50 = 10,9 ms, con CPU de sobra).

**Techo ≠ rodilla.** Techo 3.14t = 51 (58 con afinidad); rodilla = **16-25**.
Entre ambos el sistema completa turnos **degradado**. `K = rodilla + margen`,
nunca el techo. Los valores circulando (`max_sessions: 120` desplegado,
`cap ~50` en `CONTEXTO.md`) operan dentro de la zona degradada.

---

## HECHOS ESTABLECIDOS — no re-derivar

### `[MEDIDO]` — 6 campañas, 28.803 llamadas, 1.098.000 s-sesión

| métrica | valor |
|---|---|
| **Rodilla** | **16-25 sesiones** (en `.143`, con co-tenencia y spin) |
| `job` por llamada | **0,0173 núcleos** |
| Silero | **401 µs/chunk × 31,25/s = 0,0125 núcleos/llamada** (72 % del job) |
| smart-turn | **0,0704 inv./s-sesión** (1 cada 14 s) = **0,0043 núcleos/llamada** |
| regresión del job | `job_ms = 0,2507 × frames_per_cycle + 0,4191` (n=3.820, lineal) |
| tramas gateadas | 9,5 % (ventana `min_tts_time=1.2` al inicio de cada TTS) |
| latencia E2E real | time-to-first-audio-byte p50 **558 ms** · p90 **1.330 ms** |
| Modal | arranque frío **33,0 s** · warm **0,3 s** · `max_sessions: 120` desplegado |
| Experimento natural | a igual concurrencia, lotes grandes → `submit_p90` −71 %, `rvad_p90` −56 % |

Curva de degradación (CYCLE-PROBE + Ficohsa-27):

| conc | `job_p90` | `cycle_p90` | `resume_vad_p90` |
|---|---|---|---|
| 1-5 | 2,60 | 3,50 | 0,30 |
| 6-10 | 2,70 | 4,20 | 0,60 |
| 11-15 | 2,90 | 7,10 | 2,00 |
| 16-25 | 3,90 | 18,00 | 3,80 |
| 26-40 | 4,30 | 29,80 | 8,70 |
| **41+** | **7,5** | **122** | **50,7** |

Techos históricos: 3.11 con GIL **40-45** · 3.14t **51** · +afinidad **58**.

> El trabajo crece **2,9×** y el ciclo **35×**: el cuello es coordinación en un
> hilo único, **no CPU**. `steno_p90` = 0,10 ms plano en todas las bandas ⇒ la
> orquestación de STT/LLM/TTS no cuesta nada; **el cuello es el camino de audio
> a 50 fps**.

### `[CÓDIGO]` — rama `api` @ `08932a6` (en la laptop el checkout está en otra rama con trabajo local: leer `api` con `git show api:<ruta>`)

- `frame_offload: true` → todo el DSP por-trama corre en el pool `vad`
  (4 workers); el loop sólo paga submit + resume.
- **`_batch_eligible` = False mientras el TTS vive o nace** → durante TTS el
  camino es **por-trama**: 1 trama = 1 viaje al pool (50/s, no ~11/s).
- `_drain_batch` cuenta **mensajes**, tope `inbox_batch_max: 10`.
- `chunk_samples = 256` @ 8 kHz ⇒ Silero **31,25/s**; `vad.sample_rate: 8000`.
- Silero es **ONNX** (`torch.hub.load(..., onnx=True)`); torch es sólo envoltorio.
- Sesiones ORT **separadas** para Silero y smart-turn.
- Smart-turn gateado por transición de silencio (`vad.stop_secs: 0.6`).
- `noisereduce` tiene la guarda `MIN_CHUNK_SIZE` = 800 muestras **por trama**, no por lote:
  con mensajes de 160 o 480 muestras **no corre nunca** (`[MEDIDO]` 0 cargas en 13 logs, M1 2026-10-19).
- `max_handler_ms` **incluye los `await`** ⇒ es síntoma, no trabajo del loop.
- `cycle_probe: false` en el repo, **`true` en el `active.yaml` desplegado**.

### Correcciones al corpus previo — ya aplicadas, no repetir el error

- El `perf` etiquetó el 42,4 % de ONNX como "smart-turn": **es Silero**.
  Smart-turn corre a 0,07/s, es aritméticamente imposible.
- Los ~15 hilos en spin son de **OpenBLAS/OpenMP**, no de ONNX Runtime.
- `EVENT_LOOP.md` §2: los "2.073,5 ms" son **por ventana de 5 s** (= 41 %).
- `max_frame_gap_ms` ≠ `max_arrival_gap_ms`: el primero está contaminado por el
  gating intencional de TTS.
- `total_latency_ms` son **tres métricas distintas** con el mismo nombre. La
  latencia real es *"Time to first audio byte"*: p50 558 ms · p90 1.330 ms.

---

## MAPA DEL WORKSPACE

| ruta | qué es |
|---|---|
| `context/ARQUITECTURA_STACK.md` | **arquitectura autoritativa** |
| `prompts/PROMPT_estado_consolidado_20260912.md` | estado consolidado del análisis |
| `prompts/EVENT_LOOP.md` | evidencia del sandbox — **leer las correcciones de arriba antes** |
| `doctos/CONCURRENCIA_100_METRICAS_Y_EXPERIMENTOS.md` | experimentos E1-E8 de `.143` + índice de evidencia cruda. **Su §6 usa el dato atípico — ver el consolidado** |
| `prompts/deep-research-report*.md` | investigación previa; su opción #1 (EEVDF) está refutada |
| `prompts/Prompt maestro — …` | encargo original del análisis |
| `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md` | **estrategia vigente** |
| `repo_analisis/` | auditoría de 8 motores de media; `repos/` no se versiona |
| `mediaworker/` | código del laboratorio: `core/` (C0-C3), `lab/` (C4), `parity/` |
| `experimentos/` | un directorio por corrida, con `run.sh` y manifiesto; plantilla en `_plantilla/` |
| `parches/` | cambios propuestos para GoldenBridge, aplicables en su propio repo |
| `GoldenBridge/` | repo aparte, rama **`api`** — **no se versiona aquí** |
| `Asterisk-AI-Voice-Agent-FX/` | ai-engine, repo aparte — **no se versiona aquí** |
| `files/*.gz` | dumps de campaña (6 campañas) — **datos de producción, nunca al repo** |

---

## FLUJO DE TRABAJO LAPTOP ⇄ SERVIDOR

Este directorio es el repo `github.com/emRios/goldenbridge-analisis`. Se trabaja
en dos lugares:

- **Laptop (Windows):** documentos, análisis, código del laboratorio, scripts.
  Pruebas que no necesitan modelos ni tráfico. Push.
- **Servidor (Linux):** pull y ejecución de `experimentos/*/run.sh` con modelos,
  imagen `goldenbridge:314t` y carga. Los resúmenes de resultados vuelven al
  repo; lo crudo queda en `resultados/crudo/`, que no se versiona.

Reglas:

- **El contexto compartido es este repo**, no la memoria de ningún asistente.
  Toda decisión, hallazgo o cambio de estado se escribe aquí (ESTADO ACTUAL o
  un documento en `doctos/`). Una sesión en el servidor sólo ve lo que está
  commiteado.
- `main` tiene lo consolidado; una rama corta por paso (`paso/m0-gate`,
  `paso/m1-linea-base`), que se integra al cerrar el paso.
- Finales de línea: LF siempre (`.gitattributes`). Nunca commitear un `.sh`
  con CRLF.
- Ningún experimento se ejecuta sin manifiesto (lo genera `run.sh`) y ningún
  número entra en un documento como `[MEDIDO]` sin la ruta de su manifiesto.
- Los cambios a GoldenBridge o ai-engine no se hacen aquí: van a `parches/` o a
  una rama de su propio repo.

---

## ENTORNO

- `[MEDIDO 2026-10-14]` `.143` = **c7i.4xlarge**, `us-east-2a`, Xeon Platinum
  8488C (Sapphire Rapids, con `avx512_vnni`), 16 vCPU = **8 núcleos físicos ×
  2 hilos (la vCPU *i* comparte núcleo con la *i+8*)**, 30,8 GiB visibles,
  disco 194 GB (43 % usado), Ubuntu 22.04.5 con kernel 6.8.0-1066-aws.
- `.143` cicla: **apaga 06:00 UTC, arranca 14:00 UTC**. Las mediciones con
  tráfico necesitan ventana de campaña.
- Endpoint Modal: `https://shejo--golden-bridge-bridge-serve.modal.run`
  (WS en `/v1/agent/converse`). Auth por cabeceras `Modal-Key`/`Modal-Secret`
  — son **proxy-auth del endpoint**, no sirven para `modal app logs`, que exige
  token de cuenta (`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`).
- Para parear métricas por concurrencia: usar **barrido de eventos**
  (`Campaign state machine initialized` → `WebSocket connection closed`), no
  `active_sessions` de `Periodic cleanup`, que submuestrea.
