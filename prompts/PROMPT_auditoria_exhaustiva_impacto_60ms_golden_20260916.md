# Auditoría exhaustiva: impacto de coalescer 20 ms → 60 ms en GoldenBridge

## Rol y objetivo

Actúa como auditor técnico independiente. Audita de forma exhaustiva si el cambio ya desplegado en **ai-engine** —agrupar tres frames G.711 µ-law de 20 ms en un único mensaje WebSocket de 60 ms / 480 B— puede cambiar, degradar o romper el comportamiento de **GoldenBridge**.

No asumas que “acepta 480 B” equivale a “no hay impacto”. Distingue siempre entre:

1. compatibilidad de formato y conservación de bytes;
2. semántica temporal y latencia;
3. comportamiento bajo backlog, jitter, reconexión y barge-in;
4. interpretación correcta de métricas.

La auditoría no debe implementar cambios, editar configuración ni desplegar nada. Puede ejecutar verificaciones de sólo lectura y pruebas locales que no modifiquen el árbol de trabajo. No usar `git reset`, `checkout`, `clean`, ni alterar archivos no rastreados.

## Contexto fijo

- Asterisk entrega AudioSocket a **ai-engine**.
- ai-engine conserva afinidad por llamada, códecs y ARI; se conecta por WSS a GoldenBridge completo en Modal.
- GoldenBridge contiene VAD Silero/ONNX, SmartTurn, STT, LLM, TTS y barge-in.
- El coalescer de ai-engine conserva el orden y la velocidad temporal del audio: acumula exactamente tres frames µ-law de 160 B y emite 480 B cada 60 ms. No adelanta audio futuro.
- El cambio es táctico para reducir mensajes y viajes al loop de Golden; no es todavía el refactor arquitectónico de Golden.
- Golden corre con Python 3.14 free-threaded y sus restricciones ONNX/ORT ya fueron revisadas y están aplicadas en Golden-Modal. No propongas cambiar esas restricciones como respuesta al coalescer.
- La configuración activa (`active.yaml`) no está en Git y puede no coincidir con `config/default.yaml`. No presentes un valor de `default.yaml` como valor desplegado sin comprobarlo.

## Evidencia ya disponible

1. P0 con 10 sesiones a 20 ms y luego 60 ms: todas terminaron `end_ok`, sin errores; se preservaron marcas/texto y a 60 ms hubo aproximadamente un tercio de paquetes, conservando los frames lógicos.
2. P0 de barge-in: los casos de interrupción funcionaron en ambos tamaños; los conteos de cortes no fueron idénticos.
3. Producción: el coalescer emitió 16.6 mensajes/s, `frame_bytes=480`, `frames_per_msg=3`; Golden/Modal recibió los chunks, STT y agente funcionaron, y el flush terminal fue correcto. Hubo una llamada inicial afectada por cold start de Modal, asunto separado del coalescer.
4. La evidencia anterior prueba funcionalidad básica, pero no certifica ausencia de impacto en timing, condiciones límite ni K por contenedor.

## Alcance obligatorio de revisión

Audita el checkout de:

`C:\Users\GESTION\Documents\proyectos\refGolden\GoldenBridge`

Primero identifica `branch`, `SHA`, estado del árbol y si hay archivos modificados/no rastreados; no los toques. Indica explícitamente la diferencia entre lo auditado localmente y lo que puede afirmarse del contenedor desplegado en Modal.

### A. Contrato de entrada WebSocket

Rastrea desde el receptor WebSocket hasta VAD, STT y cierre de turno.

- ¿Hay validación explícita o implícita de 160/320/480 bytes?
- ¿Se preserva orden, duración, codificación, resampling stateful y audio de cola?
- ¿Algún buffer, límite, cálculo de duración o contador usa número de mensajes en vez de bytes/muestras/tiempo?
- Comprueba tanto `read_decouple=true` como la ruta legacy si sigue viva.
- Revisa el comportamiento de desconexión y del flush parcial final.

### B. VAD, SmartTurn y turn-taking

- Determina el tamaño real de inferencia de Silero y si 60 ms se vuelve a segmentar internamente sin pérdida.
- Revisa los timestamps: ¿varios chunks internos reciben el mismo timestamp al procesar un mensaje de 60 ms? Cuantifica el posible cambio de resolución o retardo, sin inventar precisión que el código no garantiza.
- Audita `start_secs`, `stop_secs`, contadores consecutivos, `last_voice_frame_time`, watchdog y auto-flush.
- Audita SmartTurn de transición y mid-speech. Busca valores configurados como `*_every_n_chunks` o similares que pasen de una cadencia temporal a otra al triplicar bytes por mensaje.
- Separa el impacto esperado del que esté comprobado por código o prueba.

### C. Barge-in y TTS

- Revisa todas las compuertas: probabilidad VAD, chunks consecutivos, duración de habla, RMS/energía, `min_tts_time`, cancelación de TTS y recuperación de la cabeza de audio.
- Evalúa específicamente si RMS sobre 60 ms puede ocultar o alterar una interrupción breve frente a RMS sobre 20 ms.
- Revisa qué ocurre si TTS inicia o termina mientras hay un paquete 60 ms en tránsito o un lote interno procesándose.
- Define una prueba que mida **latencia de barge-in**, no sólo “ocurrió/no ocurrió”.

### D. Colas, batching, backpressure y watchdog

- Audita `inbox_batch_max` y cualquier batch por número de frames. Convierte cada límite a duración de audio para 20 y 60 ms.
- Examina `inbox_depth_max`, carry, cut rules, `_frames_in_flight`, barrera de inicio de TTS, `max_batch_frames` y el peor caso de audio que puede atravesar un job.
- Determina si el watchdog de 150 ms sigue siendo seguro con una cadencia nominal de 60 ms y qué margen deja para jitter.
- Distingue un atraso real de procesamiento de un `max_frame_gap_ms` normal de 60 ms.

### E. STT, pre-contexto y recuperación

- Sigue los dos caminos: `stt_prepared` y conversión/envío normal.
- Verifica límites de tamaño del WebSocket hacia STT, re-sampling y auto-finalize.
- Audita `pre_context_buffer`: su capacidad debe expresarse en duración real de audio, no sólo número de entradas. Evalúa apertura de STT, fallo de envío, reconexión, eco/TTS y speech-start flush.
- Busca cualquier log que calcula segundos como `número_de_chunks × 0.02`.

### F. DSP auxiliar, recursos y observabilidad

- Verifica si noise reduction se activa o se salta con 60 ms y por qué.
- Examina memoria: buffers por bytes frente a buffers por cantidad de entradas.
- Identifica métricas cuyos nombres dejan de significar lo mismo: `audio_in_frames`, `max_frame_gap_ms`, `max_payload_bytes`, `max_arrival_gap_ms`, `max_batch_frames`, Cycle-Probe y contadores STT.
- Propón cómo comparar 20 vs 60 sin confundir mensajes con milisegundos de audio.

## Validación solicitada

1. Busca y ejecuta, sólo si son seguras y están disponibles, pruebas unitarias pertinentes. Declara exactamente cuáles ejecutaste y su resultado. No presentes un test del packetizer del cliente como prueba de Golden completo.
2. Revisa si existen pruebas que inyecten 480 B en Golden; identifica huecos de cobertura.
3. Propón pruebas mínimas adicionales, separadas por propósito:
   - compatibilidad/orden de bytes;
   - VAD start/stop y timestamps;
   - barge-in de energía breve y débil;
   - backlog/batching;
   - pre-contexto durante reconexión STT;
   - 10 llamadas reales warm 20 vs 60;
   - medición posterior de K por tarea Modal.
4. Para cada prueba define: estímulo, métrica, criterio de aprobación y qué hallazgo invalida 60 ms.

## Formato de entrega obligatorio

Empieza con un veredicto inequívoco, eligiendo una de estas opciones:

- **Aprobado sin cambio semántico material**;
- **Compatible, con impactos temporales acotados y condiciones por validar**;
- **No apto para generalizar hasta corregir dependencias de cadencia**.

Después entrega:

1. Una tabla de hallazgos con: severidad, componente, evidencia exacta (archivo/línea o log), impacto 20→60, condición de activación y acción propuesta.
2. Una tabla de “confirmado / inferido / no verificable”.
3. Un apartado explícito: **"No confundir"**, que separe cold start de Modal, capacidad K y refactor arquitectónico del efecto específico 20→60 ms.
4. Las configuraciones efectivas que faltan por comprobar de `active.yaml`/logs.
5. Un plan de corrección mínimo y reversible, ordenado por prioridad. No propongas un refactor grande si basta corregir una dependencia de tiempo por conteo.
6. Un gate final de despliegue: qué debe estar verde antes de dejar `frame_ms: 60` como valor permanente.

Incluye también este bloque, con evidencia concreta:

```text
Segmento:
Patrón principal:
Estado: implemented | latent | not recommended
Confianza: high | medium | low
Evidencia:
- ...
Patrones cercanos:
- ...
Riesgo:
- ...
Siguiente actividad:
- ...
```

No des garantías absolutas. No confundas que el sistema “funcione en una llamada” con la ausencia de regresión bajo concurrencia o en rutas de recuperación.
