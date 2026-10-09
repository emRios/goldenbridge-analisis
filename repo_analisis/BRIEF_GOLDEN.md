# Brief de GoldenBridge para los auditores de repositorios

Resumen verificado del estado actual, extraído de las auditorías y dictámenes de `../doctos/` (rama `api` @ `08932a6` de GoldenBridge; ai-engine @ `d059295`). Úsalo como término de comparación; las fuentes completas están en `../doctos/`.

## Qué es y dónde vive

```
Asterisk (proveedor) ══túnel══ ai-engine (.143, Python asyncio, 1 proceso)
   AudioSocket slin16 320 B/20 ms        │ coalesce a µ-law 480 B / 60 ms
                                          ▼  WebSocket por llamada (protocolo estilo Deepgram Voice Agent)
                         GoldenBridge (Python 3.14t free-threaded, uvloop, 1 proceso, 1 event loop)
                         destino: Modal, endpoint único autoescalable, imagen goldenbridge:314t
```

- STT (Steno, WS persistente por llamada), LLM (Cerebras, stream de tokens) y TTS (Ursula, WS persistente por llamada) son **remotos**. GB los orquesta.
- Restricciones fijas: Python 3.14.7 free-threaded · ONNX Runtime 1.24.4 · ORT secuencial · `GB_ORT_INTRA_OP=1`, `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1` · gate de referencia por CPU con `avx512_vnni` (modelo smart-turn cuantizado) · aislamiento por sesión · coalescer de entrada de 60 ms.

## El cuello medido (no re-derivar)

- **No es CPU**: 100 llamadas ≈ 2,3 núcleos de DSP. `job` por llamada 0,0173 núcleos; Silero 401 µs/chunk × 31,25/s (72 % del job); smart-turn 0,07 inv/s.
- **Es coordinación en un hilo único**: el trabajo crece 2,9× de 1-5 a 41+ sesiones y el ciclo 35×. Rodilla 16-25 sesiones (en `.143`, a 20 ms/50 fps). Techo 51 (3.14t). `steno_p90 = 0,10 ms` plano ⇒ la orquestación STT/LLM/TTS no pesa; **el cuello es el camino de audio a cadencia de trama**: por cada mensaje de audio el loop paga `submit` al pool `vad` (4 hilos) + `resume` (retomar la corrutina) + reacciones post-DSP.
- Batching adaptativo ya existe (hasta 10 mensajes por viaje al pool, corte en el primer evento); durante TTS vivo se desactiva → 1 viaje por mensaje.

## Interior actual (un objeto, un loop)

Por llamada: tarea lectora del WS → `inbox` FIFO (texto y audio juntos) → consumidor único → planificador de lotes → DSP en pool de hilos (Silero + smart-turn por sesión, modelo ORT compartido) → reacciones post-DSP (inicio de habla, envío a Steno en vivo, barge-in, cierre de turno) → Finalize STT → política conversacional (router, campaña, LLM) → tarea de habla (LLM tokens → frases → TTS → `_PacedAudioSender` → presupuesto de salida 0,6 s → WS). Todo comparte `SessionState` (106 campos). Cancelación de TTS (`_cancel_active_tts`) aborta el sender, recupera la cabeza de la interrupción desde `turn_audio` del VAD y la inyecta a STT, y **escribe el historial truncado a lo realmente reproducido** (mutación directa: el gran acoplamiento Media→Conversación).

## Hechos del límite con ai-engine (d059295)

- El audio GB→engine **no lleva identidad de generación**.
- El engine **descarta todos los JSON crudos de GB** (su dispatcher exige `call_id` que el proveedor no inyecta): `UserStartedSpeaking` no limpia nada. **No hay flush de reproducción para este proveedor.** Cola residual tras un barge-in ≈ presupuesto GB 0,6 s + jitter engine 300 ms + arranque 40-200 ms.
- Barge-in local del engine existe (flush-only) pero está apagado por config. Costura latente: handler `ProviderBargeIn → _apply_barge_in_action` alcanzable si el proveedor sintetiza el evento con `call_id`.
- Si cae el WS a GB, el engine **no reconecta**.

## Arquitectura objetivo (la que hay que comparar)

```
ai-engine × N ── WS de audio, afinidad toda la llamada ──▶ Modal endpoint autoescalable
   ├── MediaWorker 1 ── llamadas A…M      (cada worker dueño de extremo a extremo:
   ├── MediaWorker 2 ── llamadas N…Z       WS, buffers, orden, decod/resampleo, VAD, SmartTurn,
   └── MediaWorker W                        STT streaming+Finalize, TTS+pacing, barge-in, cleanup)
            │ eventos/comandos de baja frecuencia — NUNCA audio por trama
            ▼
       Conversación (campaña · historial · LLM · tools · política)
```

Contrato de referencia inicial (a criticar, no a aceptar): identidad `(session_id, session_epoch, turn_id, speech_id, seq)`; eventos M→C `SessionOpened, SpeechStarted, TurnCompleted, TurnFailed, SpeechInterrupted{spoken_text}, PlaybackStarted, PlaybackFinished, ClientFunctionResponse, ClientInject, MediaError, SessionClosed`; comandos C→M `ConfigureSession, Speak{speech_id, in_reply_to_turn_id}, SpeakChunk{chunk_seq, final}, CancelSpeech, SendFunctionCall, CloseSession`. FIFO por sesión en cada dirección, dedup por `(epoch, seq)`, sin exactly-once.

## Distinción obligatoria de capacidad

```
K_loop   = capacidad de un loop Media
K_worker = capacidad de un proceso Media
K_task   = suma efectiva de workers limitada por CPU, memoria y desbalance
K_flota  = capacidad agregada de tareas Modal
```

`N × K_loop` NO es aumento de K por loop. El dictamen multiproceso (28-sep) concluyó que N Goldens completos por tarea = empaquetado de réplicas. La pregunta de esta auditoría: **¿qué diseños eliminan el coordinador único del camino por trama, lo paralelizan realmente o sólo lo replican?**

## Modal (documentación oficial, 28-sep)

1 conexión WS = 1 input mientras dure · `@modal.concurrent(max_inputs, target_inputs)` · multiproceso interno exige `@modal.web_server(puerto)` (no `asgi_app`) · WS sin permessage-deflate · 4.000 contenedores/función · drenaje de WS en scale-down: no documentado.
