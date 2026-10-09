# Estado consolidado de GoldenBridge — actualizado 13-sep-2026

Integra **tres cuerpos de evidencia** que hasta ahora vivían separados:

1. El expediente histórico (`EVENT_LOOP.md`, los dos `deep-research-report`, el Prompt maestro).
2. `doctos/CONCURRENCIA_100_METRICAS_Y_EXPERIMENTOS.md` — los experimentos E1-E8 de `.143`.
3. Mediciones propias sobre **6 campañas** (28.803 llamadas) y el código de la rama `api`.

Etiquetas: `[MEDIDO]` · `[CÓDIGO]` · `[INFERIDO]` · `[ABIERTO]`.
La arquitectura objetivo está en `context/ARQUITECTURA_STACK.md` y **no se cuestiona aquí**.

---

## 0 · LO QUE CAMBIA AL INTEGRAR

**La aritmética de las 100 llamadas está apoyada en el dato atípico.**

`CONCURRENCIA_100 §6` concluye *"100 llamadas piden ~22 núcleos, la caja tiene 16 ⇒ no cierra"*.
Ese 0,22 sale de **una** fuente. Hay **tres** que dicen otra cosa, incluida una del propio documento:

| fuente | CPU | N | núcleos/llamada | descontando el spin (1,57) |
|---|---|---|---|---|
| **E1** spike C2, 29-jul | 3,90 | **100** | 0,0390 | **0,0233** |
| **E4** sandbox burst, 26-ago | 12,54 | 50 | 0,2508 | **0,2194** ← atípico |
| **E3** producción post-fase 1 | 1,76 | *sin declarar* | — | — |
| **mi regresión** sobre campañas | — | — | **0,0220** | (job 0,0173 + ST 0,0043) |

```
E1 descontado   0,0233  ┐
mi regresión    0,0220  ┘  dos métodos independientes, convergen
E4              0,2194     9× más alto — el único que sostiene §6
```

**⇒ 100 llamadas piden ~2,3 núcleos, no 22. El CPU no es la restricción.**

Pero el mismo E1 muestra qué **sí** lo es: a N=100, `submit+resume` p50 = **10,9 ms** — 30× peor que los 0,3 ms de baja concurrencia, **con el GIL ya eliminado y CPU de sobra**.

> **Conclusión integrada:** el techo de 100 no lo pone la CPU, lo pone **la coordinación en un hilo único**. Eso no invalida escalar horizontal en Modal —hace falta igual para 2.000— pero **sí invalida el argumento de §6**, y con él el `cap ~50` que se derivó de esa aritmética.

`[ABIERTO]` — por qué E4 da 9× más. Candidatos: el cliente de burst tenía habla continua y 452 barge-in/4 min contra un tráfico real con 33 % de silencio y llamadas de 19,5 s de mediana. **Lo cierra**: la concurrencia a la que se midieron los 1,76 núcleos de E3 (está en `informe-fase1-hilos-31ago.md`).

---

## 1 · CAPACIDAD — la imagen integrada

### 1.1 · Techos y rodilla no son lo mismo

```
 1 ─────────── 16 ══════════════════ 51 ──── 58 ──── ✗
     sano          DEGRADADO          techo   +afinidad
                   cycle_p90 ×5→×35   3.14t
                   rvad ×13→×169
```

| configuración | pared | techo | fuente |
|---|---|---|---|
| Python 3.11 (GIL) | GIL | 40-45 | `[MEDIDO]` doc §2 |
| Python 3.14t | **coordinación** | 51 | `[MEDIDO]` doc §2 |
| 3.14t + afinidad de loop | coordinación | 58 | `[MEDIDO]` doc §2 |
| **rodilla** (donde empieza a degradar) | — | **16-25** | `[MEDIDO]` CYCLE-PROBE |

Entre 16 y 51 el sistema **completa turnos pero degradado**. El `cap ~50` y el
`max_sessions: 120` desplegado operan **dentro de la zona degradada**.

> **K no es el techo. K = rodilla + margen.**

### 1.2 · La curva completa

`[MEDIDO]` — CYCLE-PROBE bantrab 7-sep + Ficohsa-27 para la banda 41+:

| conc | `job_p90` | `cycle_p90` | `resume_vad_p90` |
|---|---|---|---|
| 1-5 | 2,60 | 3,50 | 0,30 |
| 6-10 | 2,70 | 4,20 | 0,60 |
| 11-15 | 2,90 | 7,10 | 2,00 |
| **16-25** | **3,90** | **18,00** | **3,80** |
| 26-40 | 4,30 | 29,80 | 8,70 |
| **41+** | **7,5** | **122** | **50,7** |

**El trabajo crece 2,9×; el ciclo 35×; el regreso al loop 169×.**
Y `steno_p90` = 0,10 ms **plano en todas las bandas** ⇒ la orquestación de
STT/LLM/TTS no cuesta nada: **el cuello es el camino de audio a 50 fps**.

### 1.3 · Contradicción a resolver

- doc §2: techo 3.14t = **51**
- doc §3 **E2**: *"burst_habla N=100/120 — C2 sostiene N=100 con habla"*

No pueden ser ambas con el mismo criterio. `[ABIERTO]` — **declarar con qué
métrica E2 dice "sostiene"**. Si es comparable a la del techo de 51, E2 es
evidencia directa de que K=100 ya se alcanzó una vez, y el objetivo de fase
está mucho más cerca de lo que el resto del documento supone.

---

## 2 · COSTES Y CADENCIAS — `[MEDIDO]`

Base: 6 campañas · 28.803 llamadas · 1.097.897 s-sesión.

```
 50    /s/llamada   trama WS (160 B µ-law, 20 ms @ 8 kHz)
 31,25 /s/llamada   Silero VAD (ONNX)      256 muestras @ 8 kHz = 32 ms
 10-11 /s/llamada   ciclo / lote           frames_per_cycle 4,4-5,0
  0,32 /s/llamada   mensajes STT
  0,23 /s/llamada   chunks TTS
  0,071/s/llamada   smart-turn             una cada 14,1 s
```

> **~706 tramas procesadas por cada decisión de turno.**

| componente | núcleos/llamada activa | cómo se obtuvo |
|---|---|---|
| `job` (µ-law, resample, RMS, noisereduce, **Silero**) | **0,0173** | regresión, ver abajo |
| ├─ **Silero** solo | **0,0125** (72 % del job) | 401 µs/chunk × 31,25/s |
| └─ resto del job | 0,0048 | por diferencia |
| **smart-turn** | **0,0043** | 4.765 s de inferencia / 1.097.897 s-sesión |
| **total medido** | **0,022** | |

```
job_ms = 0,2507 × frames_per_cycle + 0,4191      n = 3.820, linealidad verificada
         └─ 251 µs por trama          └─ 419 µs fijos por ciclo (28 % del job)
```

**Smart-turn por campaña** — la tasa es estable en dos clientes y un mes:

| campaña | llamadas | s-sesión | pico | tasa /s | `inference_ms` p50 |
|---|---|---|---|---|---|
| ficohsa 12-ago | 6.971 | 267.873 | 34 | 0,0747 | 62,8 |
| ficohsa 13-ago | 4.782 | 186.154 | 37 | 0,0793 | 72,6 |
| ficohsa 18-ago | 4.367 | 189.391 | 42 | 0,0717 | 38,6 |
| ficohsa 20-ago | 5.792 | 211.385 | 42 | 0,0617 | 46,2 |
| ficohsa 21-ago | 4.722 | 179.117 | 39 | 0,0636 | 38,3 |
| bantrab 7-sep | 2.169 | 63.977 | 26 | 0,0710 | 51,4 |
| **total** | **28.803** | **1.097.897** | — | **0,0704** | — |

⚠ **Ninguna campaña acumula tiempo en la banda 41-99.** Todo lo que se diga de
esa banda es extrapolación — salvo el punto de Ficohsa-27 en §1.2.

**Otras distribuciones (bantrab)** `[MEDIDO]`:

| métrica | p50 | p90 | p99 |
|---|---|---|---|
| `max_arrival_gap_ms` (llegada real) | 61,2 | 1.089,5 | 1.219,1 |
| `max_offload_wait_ms` (cola del pool) | 3,1 | 71,4 | 116,0 |
| **time-to-first-audio-byte** ← *la latencia E2E real* | **557,8** | **1.330,2** | 1.896,8 |
| `stt_latency_ms` | 200,1 | 832,1 | 1.122 |

`dropped_frames` = 0 siempre · `inbox_full_waits` = 0 siempre · tramas gateadas = **9,5 %**.

**Modal** `[MEDIDO]` 12-sep, vía `/health`: arranque en frío **33,0 s** · warm **0,3 s**
· `max_sessions: 120` en la config desplegada.

---

## 3 · CÓDIGO — rama `api` @ `08932a6`

- `frame_offload: true` → **todo** el DSP por-trama corre en el pool `vad` (4 workers); el loop paga sólo submit + resume.
- **`_batch_eligible` = False mientras el TTS vive o nace** ⇒ durante TTS el camino es **por-trama**: 1 trama = 1 viaje (50/s, no ~11/s). **Es el estado de máxima carga de loop.**
- `_drain_batch` cuenta **mensajes**, tope `inbox_batch_max: 10`.
- `chunk_samples = 256` @ `vad.sample_rate: 8000` ⇒ Silero **31,25/s**.
- Silero es **ONNX** (`torch.hub.load(..., onnx=True)`); sesión ORT **separada** de smart-turn.
- Smart-turn gateado por transición de silencio (`vad.stop_secs: 0.6` ≡ `silence_window_ms` p50 608 ms).
- `noisereduce` dentro del job con guarda `MIN_CHUNK_SIZE = 100 ms`; el lote p50 es 96 ms ⇒ **hoy se salta casi siempre**.
- Tramas gateadas hacen `return` **antes** del VAD (`min_tts_time: 1.2`).
- `max_handler_ms` **incluye los `await`** ⇒ síntoma, no trabajo del loop.
- Despliegue Modal = rama `api` (verificado vía `/openapi.json`); `cycle_probe: true` en el `active.yaml` desplegado, `false` en el repo.

---

## 4 · CORRECCIONES AL CORPUS — no repetir estos errores

| # | dónde | decía | corrección |
|---|---|---|---|
| 1 | perf / doc §5 | "42,4 % ONNX = smart-turn" | **Es Silero.** A 0,07/s es aritméticamente imposible *(ya corregido en el doc)* |
| 2 | doc §3 E3 | "~15 hilos **de ONNX Runtime**" en spin | `EVENT_LOOP` §6 los atribuye a **OpenBLAS/OpenMP** (`OPENBLAS/OMP=1`, fase 2). Son pools y variables distintas. `[ABIERTO]`: o son dos reducciones fusionadas, o una atribución es falsa — afecta al "~36 %" sin fuente de doc §8.4 |
| 3 | `EVENT_LOOP` §2 | "2.073,5 ms de espera" sin unidad | Es **por ventana de 5 s** ⇒ 41 % del tiempo de pared |
| 4 | `EVENT_LOOP` §2 | "el 12 % de CPU ahorrado confirma thrash" | Confundido con el −13 % de barge-ins de la misma corrida |
| 5 | `EVENT_LOOP` §3 | rodilla ~40 | **16-25** |
| 6 | `CONTEXTO` §4.1 | baseline de jitter | Compara `max_arrival_gap_ms` con `max_frame_gap_ms`: **campos distintos**; el segundo está contaminado por el gating de TTS |
| 7 | análisis propio | "smart-turn ~5 inv./s" | **0,071/s** — error de 70× |
| 8 | análisis propio | "el loop hace el DSP por trama" | **No**: `frame_offload` lo movió al pool |
| 9 | análisis propio | "smart-turn = 1,6 % del presupuesto" | Usaba el denominador atípico (0,25). Contra el coste real: **~20 %** |
| 10 | análisis propio | "`total_latency_ms` p90 = 11 s es una alarma" | **Falsa alarma**: son tres métricas con el mismo nombre. La de TTS mide *cuánto habla el agente* (~100 ms/chunk); la real es time-to-first-audio-byte: 558/1.330 ms |
| 11 | análisis propio | "33 % de tramas gateadas" | **9,5 %** — el gate es la ventana `min_tts_time` de 1,2 s, no la duración del TTS |

---

## 5 · DESCARTADO, Y POR QUÉ

**Criterio:** una intervención es estructural sólo si cambia **los eventos de loop por llamada** o **la forma del recurso que los ejecuta**. Con el CPU ya descartado como restricción (§0), todo lo que reparte CPU es irrelevante.

| opción | veredicto |
|---|---|
| Afinidad A1/A2, `nice`, `SCHED_FIFO/RR` | reparte CPU; **no hay contención de CPU que repartir** |
| `cgroup v2` / `cpu.weight` | ídem · además no viaja a Modal · sólo protección operativa en EC2 |
| EEVDF custom slice | **refutado**: kernel 6.8, `setattr`→0 pero `getattr`→`runtime=0` |
| Split control/compute **dentro** de GB | la frontera cara es por-trama; no puede cruzarse |
| Extraer smart-turn como servicio | 0,0043 núcleos/llamada; el premio no paga el trabajo |
| Gatear smart-turn por silencio | **ya está hecho** (`stop_secs: 0.6`) |
| Log-mel incremental | 0,07/s ⇒ ~0,1 % del presupuesto |
| Reducir workers | corrida B: −17 % turnos, `inbox` p99 ×7 |

---

## 6 · LA PALANCA VIVA

Si el cuello son **eventos de loop por llamada**, hay un solo camino barato:

```
hoy, durante TTS:   50 lecturas/s  +  50 viajes/s     (batching desactivado)
hoy, fuera de TTS:  50 lecturas/s  +  ~11 viajes/s
con tramas de 60 ms: 16,7 lecturas/s + 16,7 viajes/s   ← 3× en el peor estado
```

`[MEDIDO]` — **experimento natural**: a igual concurrencia, las ventanas con
lotes más grandes (menos viajes) degradan menos.

| conc | viajes/s | `cycle_p90` | `rvad_p90` | `submit_p90` |
|---|---|---|---|---|
| 6-10 · lote chico | 15,5 | 4,60 | 0,90 | 1,40 |
| 6-10 · lote grande | **7,5** | **3,90** | **0,40** | **0,40** |
| 16-25 · lote chico | 15,5 | 17,00 | 3,70 | 2,40 |
| 16-25 · lote grande | **10,3** | **13,90** | **3,30** | **1,80** |

El confusor obvio juega **en contra** (loop lento → lotes más grandes), lo que
refuerza el efecto. Pero sólo varía la mitad de la ecuación: **las lecturas
siguen fijas a 50/s**. Coalescer en el engine corta **las dos**.

⇒ **Lo observado es el piso del efecto esperable, no el techo.**

`[INFERIDO]` — a primer orden, 60 ms lleva K de ~20 a ~60. Para 100 harían falta
~100 ms **o** 60 ms más lo que aporte el contenedor limpio (sin co-tenencia ni
spin). **Lo prueba el PoC.**

---

## 7 · ABIERTO, con su mecanismo

| # | qué | mecanismo | bloqueado por |
|---|---|---|---|
| 1 | **K real por task en Modal** | `max_containers=1`, `max_inputs=120`, `cpu=1`, rampa 20→120 | token de cuenta Modal |
| 2 | **Read-back de `OMP`/`OPENBLAS`/`ORT_INTRA`** | `print` en `@enter` | — sin esto, K sale contaminado |
| 3 | ¿Cuánto compra la trama de 60 ms? | **PoC 20 vs 60 ms, sólo cliente** | nada · ejecutable ya |
| 4 | Concurrencia de los **1,76 núcleos** de E3 | leer `informe-fase1-hilos-31ago.md` | nada |
| 5 | Criterio de éxito de **E2** (¿sostiene 100?) | leer `spike_c2/burst_habla/RESULTADOS.md` | nada |
| 6 | Atribución de los 15 hilos: ORT o OpenMP | `ps -L` con y sin cada variable | ventana de tráfico |
| 7 | El **28 % del job que no es Silero** | `perf report` con símbolos sobre `perf_314.data` | nada · offline |
| 8 | Techo del ai-engine (Python, 1 proceso) | nunca probado > ~60 llamadas | — |

**Cerrados en esta integración:** coste de Silero (0,0125 n/llamada) · falsa
alarma de `total_latency_ms` · tasa de gating (9,5 %) · aritmética de las 100
llamadas (§0).

---

## 8 · REGLAS QUE SIGUEN VIGENTES

1. **Una variable por campaña.** Fase 2 sigue sin medirse con tráfico.
2. **Nunca validar una capacidad por el código de retorno del syscall** — leer de vuelta el estado efectivo.
3. **Ninguna métrica interna basta.** La corrida B mejoró `resume_vad` 78 % y empeoró el sistema.
4. **Parear por concurrencia** con barrido de eventos, no con `active_sessions` de `Periodic cleanup` (submuestrea: pico 20 vs 26).
5. **El criterio de éxito es mover la rodilla**, no bajar `resume_vad` a concurrencia fija.
6. En Modal: **fase 1 y 2 explícitas en la imagen** — Modal las pisa con el tamaño del request de CPU.
7. **`min_containers ≥ 1`** no es optimización de coste: con arranque en frío de 33 s, es requisito funcional.

---

## 9 · PRÓXIMO PASO

| prioridad | acción | requiere |
|---|---|---|
| **1** | `min_containers ≥ 1` | acceso a la app Modal — **es un fallo en producción hoy** |
| **2** | **PoC 20 vs 60 ms** contra el endpoint, escalones 10→70 | nada |
| **3** | Ítems 4, 5 y 7 de §7 — tres lecturas, cero tráfico | nada |
| **4** | Token de Modal → K real (§7.1 + §7.2) | gestión |
| **5** | ai-engine control/media | tras cerrar K |

Los ítems 2 y 3 no dependen de nadie y **el 3 puede cambiar el objetivo de
fase**: si E2 sostuvo N=100 con un criterio comparable, K=100 ya se alcanzó una
vez y la pregunta pasa a ser por qué no se sostiene hoy.
