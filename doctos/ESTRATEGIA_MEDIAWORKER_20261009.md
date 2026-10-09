# Estrategia — MediaWorker por componentes, con la estructura de hilos decidida por medición

Fecha: 2026-10-09. Base: auditoría de refactor (25-sep), dictámenes de multiproceso y frontera (28-sep), auditoría de ocho motores de media (`repo_analisis/`, 9-oct), dictamen de Codex sobre el MediaWorker C++ y su contraste (9-oct). Etiquetas: `[CÓDIGO]` · `[DOCUMENTACIÓN]` · `[MEDICIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.

---

## 1. La idea en tres frases

1. El cuello medido es un coordinador único que toca cada trama de todas las llamadas; se elimina dando a cada llamada un dueño que ejecute su cadena por trama sin volver a un planificador compartido `[MEDICIÓN]`+`[CÓDIGO]` (precedentes: FreeSWITCH, LiveKit server y SIP).
2. Los componentes de esa cadena se construyen **sin E/S y sin saber en qué hilo corren**, para que la comparación entre estructuras de hilos (A, S, B) mida sólo la estructura.
3. La estructura, e incluso el lenguaje, los decide un experimento con criterios preregistrados, no una analogía. Antes de portar nada a C++ se mide el monolito a 60 ms y una variante Python con un loop por llamada.

---

## 2. Dos carriles y un punto de decisión

```
CARRIL M · medir                         CARRIL C · construir (sans-I/O)
───────────────────────────              ──────────────────────────────────────
M0  corregir el gate de arranque  ──┐    C0  códec µ-law + soxr            ─┐
M1  línea base monolito a 60 ms     │    C1  Silero + máquina de estados     │ paridad por capa
M2  PoC P: Python, loop por llamada │    C2  log-mel + SmartTurn             │ contra Python
                                    │    C3  gates, turn_audio, watchdog    ─┘
                 ▼                  │                     ▼
        ¿P cumple los criterios? ◀──┘    C4  dueño por llamada (A) + simuladores
            sí → integrar P                 S1 (un shard) como control causal
            no → continuar C ───────────▶  laboratorio: A vs S1 vs M60 vs P
                                                          ▼
                                            ¿A o S cumplen y mejoran a M60/P?
                                              sí → C5-C8 (red real, contrato, Conversación)
                                              no → reabrir lenguaje (Go) o diseño
```

Con una persona, el carril M va primero (unas dos semanas) y C empieza al terminar M2. Con dos personas corren en paralelo: C0-C3 son útiles en cualquier resultado de M2, porque sus compuertas de paridad y sus microbenchmarks son la base del laboratorio. **Lo que no se empieza antes del veredicto del laboratorio es C5-C8**: clientes de red reales, contrato y Conversación.

---

## 3. Carril M · medir

| paso | qué | por qué primero | listo cuando |
|---|---|---|---|
| **M0** | Corregir `docker/arranque.sh` L43-44: el gate de referencia evalúa el status de `tail`, no de pytest; la rama de fallo es inalcanzable `[CÓDIGO]` | la imagen Modal puede servir tráfico con la referencia fallida; y el laboratorio se apoya en este gate para certificar el binario nativo | prueba negativa en la imagen (modelo corrupto, NPZ ausente, decisión alterada): el puerto no abre; cero `skipped`; conteo de casos exacto |
| **M1** | Línea base **M60**: monolito `api` limpio con cadencia de 60 ms; cliente T-D1 adaptado a 480 B/60 ms (tres tramas de 160 B por mensaje, mismas muestras) con proxy-auth; `cycle_probe` instrumentado desde la lectura del socket | la rodilla 16-25 se midió a 20 ms; nunca se midió a 60 ms, que es la cadencia real desde el coalescer `[PENDIENTE]` | curva de rodilla a 60 ms con las métricas de §6, tres repeticiones |
| **M2** | PoC **P**: un hilo por llamada con su propio loop asyncio, DSP síncrono directo (`_process_frame_sync` y `process_audio_float_sync` ya existen `[CÓDIGO]`), clientes asyncio de Steno/Ursula dentro de ese loop, SmartTurn a un pool pequeño; sin `websockets.sync` | es la falsación más barata del mecanismo (5-8 días `[INFERENCIA]`) y comparte banco de medición con A | criterios de §7 sobre la misma escalera que M1; medición de pausas de GC correlacionadas con el lag de cada loop |

Decisión al cierre de M2: si P cumple a 100 llamadas bajo churn, el camino es integrar P y el carril C se detiene en C3 (sus componentes quedan como reserva). Si P falla por GC o contención reproducibles, el port queda justificado con un número.

---

## 4. Carril C · construir por componentes

Reglas que se fijan antes de escribir C0, porque violarlas ata un componente a un escenario:

- ningún estado global mutable ni singletons; el estado por sesión vive en un objeto que el dueño presta;
- ningún hilo, bloqueo ni E/S dentro de un componente;
- el reloj se inyecta: cada llamada recibe un instante monotónico, nunca lee la hora;
- ninguna asignación de memoria por trama después del warm-up, verificada con contador de asignaciones en los microbenchmarks;
- resultados como estructuras de datos, nunca callbacks hacia el dueño;
- recursos de inferencia sólo por handle; la política de concurrencia vive fuera;
- sin excepciones en el camino por trama; `ORT_NO_EXCEPTIONS` queda prohibido porque aborta `[DOCUMENTACIÓN]`; errores de ORT por `OrtStatus` o capturados en el borde del hilo.

| paso | componente | entrada → salida | estado por sesión | oráculo de paridad y tolerancia | microbenchmark | listo cuando |
|---|---|---|---|---|---|---|
| **C0** | códec µ-law y resampleo | bytes µ-law → PCM16; PCM16 a 8 kHz → 16 kHz con drenaje | un `soxr_t` por sesión y dirección | µ-law: 256 valores bit a bit (`g711.h` de FreeSWITCH, dominio público `[CÓDIGO]`); soxr: PCM16 **idéntico** a `resample_audio` de Python sobre las diez utterances, `SOXR_HQ`, int16 intercalado, drenaje incluido | ns por trama de 480 B | paridad verde; versión de libsoxr igual a la del wheel `[PENDIENTE: leer `soxr.__libsoxr_version__` en la imagen]` |
| **C1** | Silero + máquina de estados del VAD | PCM16 + instante → `{prob, is_speaking, transición, chunks consecutivos}` | LSTM, `pending_audio`, contadores, relojes de inicio/silencio | `vad_probs.npz`: delta máx < `1e-3` por chunk; `vad_stack.json`: transiciones idénticas, ningún cruce de umbral `[CÓDIGO]` `test_reference_vad.py` | µs por chunk de 256; asignaciones = 0 | paridad verde con reloj virtual sobre el corpus |
| **C2** | log-mel + SmartTurn + cuándo inferir | `turn_audio` (≤ 8 s) → features 80×800 → probabilidad → `{turn_complete, fallback armado}` | ventana, contador midspeech, timer de fallback | `features.npz`: delta máx < `1e-3` (objetivo diagnóstico `1e-5`); `stack.json`: 14 decisiones exactas, drift marginal < `0,05` `[CÓDIGO]` `test_reference_features.py` | ms por inferencia completa, separando log-mel, `Run` y commit | paridad verde; **decisión abstracta**: la inferencia se pide a un servicio con interfaz fija; quién la ejecuta se decide en C4 |
| **C3** | gates de barge-in, `turn_audio`, pre-contexto, watchdog de cierre | resultado de C1/C2 + estado de habla del agente → `{barge_in_ready, cerrar turno, bancar, flush}` | `turn_audio`, pre-contexto, relojes, estructura "habla activa / cortar" con atómicos | trazas doradas grabadas desde Python con reloj virtual: `min_tts_time`, duración, energía, chunks consecutivos, sensibilidad post-TTS, fallback, watchdog; racetest `t7` como referencia | µs por evaluación | la cadena C0→C3 reproduce la secuencia de decisiones de Python sobre el corpus con TTS superpuesto |
| **C4** | **dueño por llamada** | sockets + timers → ejecución de C0-C3 y del pacer | todo lo anterior, prestado | no tiene paridad propia: la hereda | el laboratorio de §6 | A funcional con simuladores; S1 sobre el mismo core |
| **C5** | cliente Steno como máquina de protocolo | bytes de red → mensajes; comandos → bytes | Finalize en vuelo, resultados pendientes | conformidad contra un Steno simulado grabado de una sesión real | — | sólo tras el veredicto del laboratorio |
| **C6** | Ursula + pacer + presupuesto + ledger | texto → audio; pacer devuelve "próximo plazo" y "bytes permitidos" | generación activa, ledger de lo emitido | trazas de bytes y plazos contra `_PacedAudioSender` y `_OutboundAudioBudget` `[CÓDIGO]` | — | idem |
| **C7** | servidor WS hacia ai-engine | protocolo estilo Deepgram: Settings, KeepAlive, eventos, audio | — | ai-engine real como cliente; `td1_client.py` | — | idem |
| **C8** | canal hacia Conversación | eventos y comandos del contrato con las correcciones C1-C7 y las de Codex (seq asignado en orden de wire tras la prioridad; barrera de turno STT; `emitted_ms`, no `played_ms`) | — | pruebas del dictamen de la frontera | — | depende de "proceso aparte o extensión", no de A/S/B |

Decisiones de C que se toman en su momento y no antes:
- **Política de SmartTurn compartido** (C2 → C4): `Run` de ORT es seguro entre hilos por documentación `[DOCUMENTACIÓN]`; se verifica en laboratorio con salidas serial contra concurrente y, si pasa, se retira el lock global. Comparar un pool de 1 contra 2 con todo lo demás fijo.
- **Estructura de hilos** (C4): A principal, S1 control causal, S con W>1 si la presión es de CPU o planificador, B sólo si el pacer pierde plazos por cómputo de entrada y tras demostrar el ownership del stream de Beast `[DOCUMENTACIÓN]`.
- **Lenguaje** (tras el laboratorio): si A falla por el costo de cientos de hilos y no por el DSP, evaluar Go antes que S: es el único precedente con la forma exacta (LiveKit SIP) y resuelve ese costo por diseño `[INFERENCIA]`.
- **Proceso aparte o extensión** (C8): primera entrega como proceso con UDS; la extensión queda como opción.

Condiciones del toolchain, fijadas al empezar C0 `[INFERENCIA]`: C++20; Beast/Asio para red asíncrona; artefacto nativo de ORT 1.24.4 CPU con hash verificado y sin enlazar la copia privada del wheel; libsoxr de la misma versión que el wheel; compilar en la construcción de la imagen, nunca al arrancar; ASan/UBSan en todos los escenarios, TSan en B.

---

## 5. Dónde viven los artefactos

Fuera de la rama `api` y fuera de `GoldenBridge/`, para no mezclar el laboratorio con producción: un directorio `mediaworker/` en el workspace con `core/` (C0-C3), `lab/` (C4, simuladores, cliente adaptado), `parity/` (trazas doradas grabadas desde Python y los NPZ de referencia) y `informes/` (resultados por campaña con manifiesto de commit, config, binarios, CPU y cgroup). Nada de esto se crea hasta aprobar esta estrategia.

---

## 6. El laboratorio (una vez, para M1, M2, A, S1 y B)

- **Caja**: la misma para todas las variantes; reserva de CPU fija y leída; generador y simuladores con recursos aparte del presupuesto de servicio.
- **Simuladores**: Steno (handshake, PCM16, Finalize, resultados con marca por sesión, latencia fija), Ursula (audio grabado, síntesis más rápida que tiempo real para ejercer el pacer), LLM stub determinista, proveedor lento como campaña aparte.
- **Carga**: escalera 10 → 25 → 50 → 75 → 100 llamadas vivas, rampa 60 s, warm-up 60 s descartado, 10 min estables por escalón, tres repeticiones con orden balanceado; habla con pausas de 8 s y barge-in en un tercio de las sesiones.
- **Métricas**: latencia mensaje→decisión VAD (p50/p90/p99 por banda, separando residencia antes del DSP, DSP y post); SmartTurn disparo→commit; barge-in gates→último write viejo; pacer plazo→write; profundidad y edad de colas por llamada; CPU por hilo y por segundo de audio; hilos, RSS, FDs; lag del generador separado del de servicio; calidad E2E con los simuladores (turnos completos, cabeza de interrupción, cero mezclas con `verifica_mezclas.py` `[CÓDIGO]`).

---

## 7. Criterios preregistrados (tomados del dictamen de Codex §10.5)

| criterio | aceptación |
|---|---|
| corrida válida | generador previsto→real p99 ≤ 5 ms, cero muestras perdidas, simuladores sin cola creciente |
| paridad | toda la compuerta de §4 verde; cero mezclas; cero turnos duplicados |
| meta a N = 100 | VAD desde mensaje completo p90 ≤ 5 ms, p99 ≤ 20 ms |
| corte local | gates→último write viejo p99 ≤ 30 ms; cero writes viejos iniciados tras confirmar el corte |
| pacer | lag de plazo p99 ≤ 20 ms; sin underruns adicionales |
| backlog | residencia de audio p99 ≤ 120 ms; sin pendiente positiva en los últimos 5 min |
| SmartTurn | disparo→commit p99 ≤ 250 ms; ningún resultado obsoleto cierra un turno nuevo |
| E2E | cero turnos perdidos; primer audio y cierre no empeoran más de 10 % o 20 ms |
| beneficio | si M60 ya cumple a 100, no se reescribe por capacidad; para adoptar: mayor K sostenido o p99 E2E ≥ 20 % mejor sin regresiones |
| atribución | S1 contra M60 mide eficiencia del stack; A contra S1 mide paralelismo; ablación "pool VAD vs Silero en línea" sobre S1 aísla el mecanismo |

K_worker es el mayor N que cumple todos los criterios en las tres repeticiones. El escalón 100 sólo establece K ≥ 100 si pasa; no demuestra techo. `N × K_loop` nunca se presenta como aumento de K por loop.

---

## 8. Esfuerzo y riesgo `[INFERENCIA]`

| tramo | días-persona | riesgo dominante |
|---|---|---|
| M0 gate | 1-2 | ninguno; prueba negativa obligatoria |
| M1 línea base 60 ms | 3-5 | confusores del generador y de la config efectiva |
| M2 PoC P | 5-8 | objetos ligados a loops, GC, traspaso del socket |
| C0-C3 core con paridad | 12-20 | FFT, normalización y estado recurrente del log-mel |
| C4 laboratorio A + simuladores, S1 | 7-11 | red asíncrona, Finalize, generación |
| campañas y análisis causal | 3-5 | muestras de p99, atribución |
| C5-C8 red real, contrato, Conversación | 15-25 | historial, cancelación, funciones, orden de tardíos |
| empaquetado, supervisor, gates, Modal | 4-7 | señales, readiness, ORT efectivo |
| **total si se recorre todo** | **50-83** | con dos puntos de parada: tras M2 y tras el laboratorio |

Prerrequisitos fuera de este plan y necesarios para cualquier despliegue nuevo: reconexión del proveedor GB en ai-engine y la orden de flush de reproducción `[CÓDIGO]` d059295.

---

## 9. Qué queda pendiente de obtener antes de M1

- El wrapper real de Modal de GoldenBridge: `max_inputs`, `target_inputs`, `cpu`, `memory`, `startup_timeout` `[PENDIENTE]`.
- `config/active.yaml` efectivo en Modal y en `.143` `[PENDIENTE]`.
- Versión de libsoxr embebida en python-soxr 1.1.0 `[PENDIENTE]`.
- Tamaño en disco y RSS de los dos modelos ONNX `[PENDIENTE]`.
