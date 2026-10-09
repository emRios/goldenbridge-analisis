# Contraste — dictamen de Codex sobre el MediaWorker C++ (2026-10-09)

Fuente contrastada: `doctos/DICTAMEN_CODEX_MEDIAWORKER_CPP_20261009.md`, respuesta al encargo `prompts/PROMPT_codex_mediaworker_cpp_escenarios_20261009.md`. Cada corrección de Codex se verificó contra el código de `api` @ `08932a6`, contra las fuentes oficiales citadas, o por demostración directa. Etiquetas: `[CÓDIGO]` · `[DOCUMENTACIÓN]` · `[MEDICIÓN]` · `[INFERENCIA]` · `[PENDIENTE]`.

---

## 1. Veredicto sobre el dictamen

Codex coincide en lo central y corrige con evidencia en lo periférico. Coincide en que el mecanismo correcto es retirar el coordinador compartido y el viaje al executor de la cadena frecuente; en que **A va primero**, **S con W=1 es un control causal obligatorio** y **B después**; y en aceptar C++ sobre C. Corrige nueve puntos nuestros, todos verificados abajo, y aporta un hallazgo nuevo de seguridad operativa que no es parte del refactor y hay que atender antes que cualquier otra cosa.

Donde difiere de nuestra secuencia es en una decisión de orden, no de diseño: pide **medir el monolito a 60 ms y una variante Python con loop por llamada antes de comprometer el port**. Lo evalúo en §4.

---

## 2. Hallazgo nuevo y grave: el gate de arranque de la imagen no bloquea

`[CÓDIGO]` `docker/arranque.sh` L43-44:

```sh
if ! python -m pytest test/reference/ test/test_reference_features.py \
                      test/test_reference_vad.py -q --no-header 2>&1 | tail -20; then
```

En `sh` sin `pipefail`, el estado de una tubería es el del último mandato. `tail` sale con 0 aunque pytest haya fallado, así que `!` niega un éxito y **la rama de fallo es inalcanzable**: el contenedor imprime "referencia OK" y sirve tráfico con cualquier resultado de la referencia. Demostración directa en esta máquina:

```
bash -c 'if ! false | tail -1; then echo RAMA_FALLO; else echo RAMA_OK; fi'   → RAMA_OK
bash -c 'set -o pipefail; if ! false | tail -1; then …'                        → RAMA_FALLO
```

El script de `.143` (`deploy.sh`) no tiene la tubería y sí bloquea. **El defecto afecta sólo a la imagen `goldenbridge:314t`, es decir, al despliegue en Modal**: un contenedor caído en una CPU sin `avx512_vnni`, o con un modelo distinto, arrancaría igual. Es exactamente el fallo silencioso que todo el bloque "Antes de recibir tráfico" de `DEPLOYMENT.md` existe para impedir. `[INFERENCIA]` No afirmo que haya ocurrido en producción; afirmo que hoy no puede detectarse.

Corrección mínima, portable a cualquier `sh`: ejecutar pytest con salida a archivo, capturar `$?` por separado, mostrar el `tail` después y decidir con el código capturado; exigir además cero `skipped` y el número de casos esperado, como propone Codex. Hay que corregirlo **antes** del PoC, porque el PoC se apoya en ese mismo gate para certificar el binario nativo.

---

## 3. Correcciones de Codex, verificadas

| # | lo que decíamos | corrección de Codex | verificación | consecuencia |
|---|---|---|---|---|
| 1 | `ORT_NO_EXCEPTIONS` como opción para aislar errores de inferencia | con esa macro el header **llama a `abort()`**; no devuelve códigos | `[DOCUMENTACIÓN]` `onnxruntime_cxx_api.h` v1.24.4, `ORT_CXX_API_THROW` ≈ L61-70: `std::cerr << …; abort();` | retirar de la disciplina de C++; errores por llamada con la API C y `OrtStatus`, o `catch` en el borde del hilo |
| 2 | el lock global de SmartTurn está ~43 % ocupado a 100 llamadas | el lock sólo cubre `session.run`; las features se extraen **antes** de tomarlo; y `Run` **es seguro entre hilos** según ORT | `[CÓDIGO]` SMART L205-225; `[DOCUMENTACIÓN]` `inference_session.h` v1.24.4, `InferenceSession::Run`: "Multiple threads are allowed to run this function; hence its thread-safe" (feeds y fetches no deben mutar durante la ejecución) | nuestro 43 % mezclaba CPU total por inferencia con tiempo bajo lock; el comentario "ONNX session is not thread-safe" (SMART L224) contradice al propio ORT; verificar `Run` concurrente en laboratorio y retirar la serialización |
| 3 | B tiene "0 traspasos por dirección" | el stream de Beast **no es seguro entre hilos**; admite un `async_read` y un `async_write` pendientes, pero sincronizados por strand o dueño único | `[DOCUMENTACIÓN]` Beast, notas de `websocket::stream` | el socket hacia ai-engine lo usan las dos direcciones; B necesita un dueño del stream y una cola SPSC desde salida ⇒ al menos un traspaso; B no se aprueba hasta demostrar ese contrato |
| 4 | A = "un hilo por llamada con la cadena por trama síncrona" | síncrono debe aplicar al DSP y a la decisión, **no** a la red: `_audio_post_vad` reconecta Steno en línea y Finalize espera hasta 3 s | `[CÓDIGO]` WS L3780-3816 (`reconnect_session` tras fallo de envío), STENO L627-720 | A es un reactor asíncrono por llamada con DSP en línea; una traducción bloqueante congela el pacer de esa llamada, y en S el shard entero |
| 5 | paridad "bit a bit" | el gate existente tolera `1e-3` en features y `0,05` de drift en casos marginales; sólo las **decisiones** son exactas; Silero: `1e-3` por chunk, transiciones idénticas | `[CÓDIGO]` `test_reference_features.py` L50-51, L170-227; `test_reference_vad.py` L39, L70-123 | bit a bit sólo para µ-law (256 valores) y soxr; el log-mel se mide contra `1e-3` con objetivo diagnóstico `1e-5` |
| 6 | el contrato puede llevar `seq` global FIFO **y** un carril prioritario para `CancelSpeech` | conflicto: si el cancel adelanta con un `seq` mayor, el receptor ve un hueco y cierra la sesión | `[INFERENCIA]` sobre C1 y C7 de `MATRIZ_COMPARATIVA.md` §3 | asignar `seq` al serializar, en orden de wire y después de decidir prioridad; lo purgado antes del wire no consume `seq`; dependencias explícitas por `command_id` |
| 7 | `discard_pending` se conserva como está | su premisa es que el receive-loop está bloqueado durante Finalize y no llega audio nuevo a Steno; en un reactor asíncrono esa premisa deja de cumplirse | `[CÓDIGO]` STENO L836-848, docstring | hace falta una barrera de turno STT: cerrar el segmento y retener el audio siguiente, o se purgan resultados válidos del turno nuevo |
| 8 | `played_ms` medido en el pacer | `_TTSPacer.played_bytes` estima por reloj de pared; AudioSocket sólo confirma `write`/`drain` | `[CÓDIGO]` WS L401-408; ai-engine AS L263-267 | distinguir emitido, aceptado por el engine y oído; `emitted_ms` es lo único medido |
| 9 | IXWebSocket como alternativa de "hilo bloqueante por conexión" | `WebSocket::start` crea un `std::thread` por objeto | `[DOCUMENTACIÓN]` IXWebSocket.cpp L167-172 | no sirve para contar hilos; Beast/Asio con ejecutor por dueño |

Otras verificaciones menores que también se sostienen `[CÓDIGO]`: `turn_audio` acotado a 8 s (SMART L1808-1813); extractor propio con normalización `(x-mean)/sqrt(var+1e-7)`, Hann periódica, padding reflect, `rfft` 400/hop 160, mel Slaney, `log10` y piso `max−8` (`whisper_features.py`); `td1_client.py` envía 160 B/20 ms y no pasa proxy-auth; `main.py` fuerza `frame_offload` off con `inference_workers ≤ 0`, así que "workers = 0" no implementa la comparación deseada; no existe `t3` en `tools/racetests/`.

---

## 4. Donde difiere el juicio

**Medir antes de portar.** Codex pide, antes de comprometer C++: (M) el monolito a 60 ms, que nunca se midió, y (P) un hilo por llamada con su propio loop asyncio, DSP síncrono directo (`_process_frame_sync` ya existe) y clientes asyncio de Steno/Ursula en ese loop. Presupuesto: 5-8 días-persona, contra 12-20 sólo para el core C++ con paridad. Su argumento: descartamos Python por la *posibilidad* de pausas de GC y contención, no por medirlas, y P elimina igualmente el loop compartido.

Mi lectura: tiene razón en el fondo. P es la falsación más barata del mecanismo y comparte con A el banco de medición, los simuladores y los criterios. Si P cumple los criterios preregistrados bajo churn, el port se evita; si falla por GC o contención reproducibles, el port queda justificado con un número en vez de con una analogía. Cuesta una semana y puede ahorrar un mes. **Recomiendo adoptar la secuencia de Codex: M60 → P → A → S1 → (B)**, con una sola condición: que P use el mismo core de medición que A, para que la comparación sea válida.

**Go no está en el dictamen.** El encargo preguntaba C contra C++ y Codex no abre la tercera opción. Sigue siendo la única pregunta de lenguaje con precedente directo de forma (LiveKit SIP) y con el riesgo de "cientos de hilos" resuelto por diseño. Queda abierta.

**Esfuerzo.** Los 63-105 días-persona de Codex cubren todas las fases hasta el rollout de laboratorio y no son comparables con los 38-60 de la frontera en Python del dictamen anterior. Sumadas las dos estimaciones, la nuestra y la suya son coherentes.

**Lo que ambos dejamos `[PENDIENTE]`**: el wrapper real de Modal y sus `max_inputs`/`cpu`/`memory`; la versión de libsoxr que embebe el wheel; el RSS por modelo; la rodilla a 60 ms; el comportamiento del GC de 3.14t bajo este diseño; el drenaje de WebSockets vivos en un redeploy de Modal.

---

## 5. Orden de trabajo resultante

1. **Hoy**: corregir `docker/arranque.sh` para que un fallo de la referencia bloquee el arranque, con prueba negativa (modelo corrupto, NPZ ausente) que demuestre que el puerto queda cerrado.
2. Línea base **M60**: cliente T-D1 adaptado a 480 B/60 ms con proxy-auth; monolito con `cycle_probe` instrumentado desde la lectura del socket, no sólo desde el pre.
3. **PoC P** (Python, loop por llamada) con los criterios preregistrados de Codex §10.5.
4. Si P no cumple: core C++ pasos 0-3 con la compuerta de paridad por capas de Codex §6, después **A** y **S1** sobre el mismo core.
5. **B** sólo tras resolver el ownership del stream de Beast y contar los traspasos reales.
6. En paralelo y fuera del refactor: reconexión del proveedor GB en ai-engine y la orden de flush, prerrequisitos de cualquier despliegue nuevo.
