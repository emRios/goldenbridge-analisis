# Camino a 100 llamadas simultáneas — métricas, experimentos y pruebas

**Consolidado 2026-09-12** en `.143` (`16.58.43.143`). Reúne todo lo medido para romper el techo de concurrencia de GoldenBridge y llegar a **100 llamadas simultáneas**. Fuentes al pie de cada sección; evidencia cruda indexada en §9.

---

## 1 · Objetivo y estado

- **Meta:** 100 llamadas simultáneas en producción.
- **Techo histórico (Python 3.11, con GIL):** ~40–45 llamadas — no lo mueven más núcleos (pared del GIL).
- **Diagnóstico raíz:** un único **event loop de Python** hambriento de CPU → *frame starvation* del loop (las tramas de 20 ms se encolan). Es lo que motivó el refactor **C2** (Python 3.14 free-threaded, sin GIL).
- **Estado hoy:** 3.14 free-threaded en producción; techo medido **51 → 58** llamadas (con afinidad de loop). 100 en una sola caja **no cierra por aritmética de CPU** (§6) → estrategia = **escalar GB horizontal en Modal** (§7).

## 2 · Tabla maestra de techos (ceilings) por configuración

| configuración | pared | techo medido | nota |
|---|---|---|---|
| Python 3.11 (GIL) | GIL | **~40–45** | no sube con más núcleos |
| Python 3.14t (hoy) | CPU | **51** | GIL eliminado; ahora el cuello es CPU |
| 3.14t + afinidad de loop | CPU | **58** | +7 llamadas apartando el loop a un núcleo físico |
| Objetivo 100 | CPU | ✗ en 1 caja | pide **~22 núcleos**, la caja tiene **16** |

Fuente: `spike_c2/gb/docs/informe-afinidad-loop-314.md §6`.

## 3 · Experimentos y veredictos

### E1 · Spike C2 free-threaded (29-jul, N=100) — *viabilidad*
Quitar el GIL rompe el encolamiento del loop.
- espera-GIL del loop **45,7 % → 0,0 %**
- submit+resume p50 **383 ms → 10,9 ms**
- frames tirados **56 % → 0,02 %**
- CPU **1,67 → 3,90 núcleos** (ahora paraleliza)
- **Veredicto:** viable, correctitud validada. Fuente: `refactor-python-314-freethreaded.md`.

### E2 · burst_habla (13–25 ago, N=40/70/100/120) — *C2 aguanta 100*
Bursts con habla real + barge-in a 40/70/100/120 sesiones (incl. caja c7i).
- **Veredicto:** C2 sostiene N=100 con habla. Fuente: `spike_c2/burst_habla/` (`RESULTADOS.md`, `RESULTADO_n100_real.md`, `RESULTADO_n120.md`, `RESULTADO_c7i_n100.md`, `RESULTADO_bargein.md`, `RESULTADO_habla_instrumentado.md` + 29 logs crudos).

### E3 · Fase 1 de hilos / `intra_op` (31-ago, tráfico real, 3.587 llamadas) — *medido en prod*
`inference_ms` (solo ONNX smart-turn) por concurrencia; deja de empeorar bajo carga.

| simultáneas | p50 base→hoy | p90 base→hoy |
|---|---|---|
| 1–5 | 48,3 → 50,2 (+4 %) | 80,0 → 71,9 (−10 %) |
| 16–25 | 60,6 → 53,8 (−11 %) | 139,2 → 95,8 (−31 %) |
| 26–40 | 64,7 → 56,7 (−12 %) | 144,4 → 109,7 (−24 %) |

- **Hallazgo clave:** ~15 hilos de ONNX Runtime **quemaban 89 % del CPU del proceso (~1,57 núcleos) girando en vacío**; fijar `GB_ORT_INTRA_OP=1` + política de spin los llevó de **23→1**. CPU hoy no es el cuello (1,76 de 16 núcleos). Fuente: `informe-fase1-hilos-31ago.md`.

### E4 · Afinidad del event loop (26-ago) — *el loop es la víctima*
3 bursts (12 workers / 6 workers / 12 + loop apartado), 50 sesiones, carga idéntica.
- Sin GIL el loop pasa **41 % del tiempo esperando un núcleo libre** (1.966 expropiaciones/5 s) — pelea CPU contra su propio pool.
- Bajar workers 12→6 **degrada** el sistema (refutado).
- **Fix:** apartar el loop a un núcleo físico → espera **−94 %**, CPU **−12 %**, todos los indicadores punta a punta mejoran. `resume_vad` p50 8,90→0,40, p90 33,2→7,0.
- Fuente: `informe-afinidad-loop-314.md`.

### E5 · Perfilado `perf` (proceso más pesado) — §7
- `onnxruntime…so` **42,4 %** + `libscipy_openblas64…so` **33,1 %** = **~75 % del CPU on-core** en inferencia.
- El hilo del loop **no** es pesado por sí mismo — es la víctima que se queda sin CPU.
- Fuente: `spike_c2/perfil/perf_314.data`.

### E6 · Campañas reales de producción (26–27 ago) — *pico real observado*
- **admosa 26-ago:** 273 sesiones, rampa ~55/min, **pico 60 sesiones GB / 73 canales cliente** (Emerson lo vio en vivo); 0 muertes 1011, 0 dropped.
- **ficohsa 27-ago (1ª de prod sobre 3.14t):** rampa 20–68/min, ~317 ses, 0 muertes/0 ERROR/0 handoffs fallidos.
- Fuente: memoria `new_server_migration` + `informe-campana-314-26ago.md`, `informe-ficohsa-314-27ago.md`.

### E7 · Prueba de escalado en Modal (10-sep, N=60 con audio real) — *scale-out horizontal*
GB de prueba desplegado en Modal (auth Modal-Key/Secret; 1er intento 401, reintento OK).
- 60 sesiones concurrentes completaron turno conversacional completo (`speak=1|audio_done=1`).
- **PING-RTT del WS (proxy de responsividad del loop):** p50 **13** · p90 **15** · p99 **15** · max 248 ms → plano, sin degradación.
- Conexión (`applied`): p50 33 · p90 243 ms.
- **Autoscaler Modal:** 2 contenedores, `running_inputs` máx **60**, backlog máx **9**, headroom 80.
- **Veredicto:** a 60 concurrentes sobre red pública el jitter **no** rompió la responsividad y Modal escaló limpio. Falta N=100 y el `max_arrival_gap_ms` server-side. Fuente: `/srv/GoldenBridge/rampa_20260910T224520.tsv` + `escalado_20260910T224539.tsv`.

### E8 · Métricas de dumps de campaña (bantrab 07-sep post-fase1 / ficohsa 21-ago pre) — *coste de smart-turn en prod*
- **Smart-turn NO es el consumidor dominante de CPU:** gateado por transición de silencio (4.203 transiciones → 4.540 invocaciones), **~0,07 invocaciones/segundo-sesión**, **~1,5 % del presupuesto** de 0,25 núcleos/llamada. Dos campañas coinciden.
- **La contención es de planificación, no de trabajo; rodilla en 16–25 sesiones:** `job_p90` sube 50 % mientras `cycle_p90` sube 5× (3,5→18 ms).
- Fuente: dumps en `latency_incident/evidencia/` (ver §8 de la validación previa).

## 4 · Métricas clave consolidadas

**inference_ms (solo ONNX smart-turn):**
| | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|
| bantrab (post fase 1) | 3.942 | 51,4 | 74,5 | 141,8 | 221,8 |
| ficohsa (pre fase 1) | 9.540 | 38,3 | 65,8 | 102,8 | — |

**CYCLE-PROBE pareado por concurrencia (bantrab, post fase 1):**
| conc | n | job_p90 | cycle_p90 | resume_vad_p90 |
|---|---|---|---|---|
| 1–5 | 1.024 | 2,60 | 3,50 | 0,30 |
| 11–15 | 1.010 | 2,90 | 7,10 | 2,00 |
| 16–25 | 603 | **3,90** | **18,00** | **3,80** |
| 26–40 | 13 | 4,30 | 29,80 | 8,70 |

**Concurrencia (pico observado):** bantrab 26 · ficohsa 39 (ninguna llega a banda 41–99).

## 5 · El proceso más pesado (perfil)
`perf` sobre la misma CPU de prod: **~75 % del tiempo on-core es inferencia ONNX** (42,4 % onnxruntime + 33,1 % OpenBLAS). Silero VAD (por trama, sobre cada chunk de 160 muestras) es el candidato principal a ese 75 %; smart-turn corre solo a 0,07/s (gateado por silencio) → no puede ser el 75 %. El event loop es víctima, no consumidor. Silero y smart-turn corren en **sesiones ORT separadas** (código: `vad_processor.py` vs `smart_turn_processor.py`).

## 6 · La aritmética de las 100 llamadas
- Coste medido: **~0,22–0,25 núcleos/llamada** (12,54 núcleos / 50 sesiones, sandbox).
- 100 llamadas ⇒ **~22 núcleos**; la caja de prod tiene **16**. **No cierra en una sola caja.**
- Palanca de mayor impacto = **abaratar la inferencia por-trama** (Silero: el 89 % de spin ya atacado en fase 1; pendiente cuantización/hilos) **o escalar horizontal**.

## 7 · Estrategia para 100 = GB horizontal en Modal
- Cada contenedor Modal = **GB completo** (cap ~50), Modal gestiona el scale-out, **endpoint único**; **ai-engine se queda en .143**.
- Probado a 60 (E7). Riesgos abiertos: (a) **jitter** de tramas 20 ms sobre red pública a N=100 (§4.1 CONTEXTO), (b) reciclado de contenedores corta llamadas largas, (c) requisito `avx512_vnni` en toda la flota (el modelo cuantizado cambia decisiones sin él).
- **Nota de arquitectura:** el ai-engine **no** se puede partir en N celdas sin tocar al proveedor (la correlación de la pata del agente vive en memoria de una instancia; el marcador hoy no acepta app-name/destino por llamada). Por eso Modal escala **solo GB**, no el ai-engine.

## 8 · Pendientes para cerrar 100 (medición nueva)
1. **Repetir E7 a N=100** en Modal y leer `max_arrival_gap_ms` server-side (cierra §4.1).
2. **Coste/tasa de Silero aislado** (el 75 %): contador+timer alrededor de su `session.run`.
3. **Split del 42/33 % del perf** con símbolos resueltos (tenemos `perf_314.data`).
4. **Fase 2 de hilos** (`OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`) — cambia el coste ~36 %, dimensiona `cpu=` de Modal antes de fijarlo.
5. **CPU por proceso en .143** en ventana de tráfico (`top -b` + `pidstat`).

## 9 · Índice de evidencia cruda (en este box .143, bajo /srv/memories/)
| qué | ruta |
|---|---|
| Doc madre C2 | `refactor-python-314-freethreaded.md` |
| Spike burst con habla (40/70/100/120) | `spike_c2/burst_habla/` |
| Afinidad del loop (experimento) | `spike_c2/burst_workers/` + `gb/docs/informe-afinidad-loop-314.md` |
| Burst N=100 y perfiles | `spike_c2/burst_n100/`, `spike_c2/perfil/` (incl. `perf_314.data`) |
| Fase 1 de hilos (informe) | `spike_c2/gb/docs/informe-fase1-hilos-31ago.md` |
| Campañas 314 (26–27 ago) | `spike_c2/gb/docs/informe-campana-314-26ago*.md`, `informe-ficohsa-314-27ago.md` |
| Prueba Modal (10-sep) | `/srv/GoldenBridge/{rampa,escalado}_20260910T2245*.tsv` |
| Dumps de campaña (métricas E8) | `latency_incident/evidencia/informe_post_{bantrab_20260907,ficohsa_20260821}.dump.log.gz` |
| Expediente Golden→Modal | `golden_a_modal/CONTEXTO.md` |
| Expediente event loop (4 modelos) | `event_loop/EVENT_LOOP.md` |
| Índice consolidado del refactor | `INDICE_CONSOLIDADO_REFACTOR_C2_314.md` |
