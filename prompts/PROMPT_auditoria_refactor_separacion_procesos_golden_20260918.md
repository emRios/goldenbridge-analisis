# Auditoría exhaustiva para el refactor y separación de procesos de GoldenBridge

Actúa como arquitecto principal y auditor de software concurrente. Analiza el
código real de GoldenBridge antes de concluir. Este encargo es de investigación,
diseño y decisión: no implementes código, no cambies Modal y no presupongas que
un split es beneficioso.

Responde en español. Clasifica toda afirmación como:

- **[código]**: comprobada con archivo, símbolo y callers verificados;
- **[medición]**: respaldada por una métrica o log identificado;
- **[inferencia]**: deducción con supuestos declarados;
- **[pendiente]**: aún no comprobable y con experimento mínimo para resolverla.

No inventes módulos, campos, métricas, límites de capacidad o contratos. No
incluyas secretos, tokens o credenciales.

## 1. Objetivo

La topología de negocio está fijada: varios contenedores ai-engine atenderán las
llamadas de Asterisk y se conectarán a una flota autoscalable de GoldenBridge en
Modal. No propongas mover Asterisk, el túnel de red, ai-engine ni reemplazar
Modal.

El objetivo técnico es determinar si un refactor de GoldenBridge puede elevar
materialmente la concurrencia segura por tarea (K), con la meta de aproximarse a
100 sesiones por tarea antes de dimensionar la flota para 2,000 llamadas.

La pregunta central es:

> ¿Qué separación de procesos, si existe, reduce trabajo crítico del loop que
> limita K sin simplemente trasladar el mismo loop Python, los mismos bytes y
> los mismos riesgos de audio a otro proceso?

MediaWorker + Control es una hipótesis, no una conclusión. No digas que
ai-engine ya es un MediaWorker sólo porque termina AudioSocket o convierte
códecs. Distingue transporte, DSP/VAD/SmartTurn, estado de turno, STT, LLM, TTS,
barge-in, cancelación y ejecución de acciones.

## 2. Lectura obligatoria y alcance

Lee antes de auditar:

1. context/ARQUITECTURA_STACK.md: topología fija.
2. prompts/EVENT_LOOP.md y Prompt maestro — GoldenBridge Event Loop -
   Control-Compute Architecture.md: antecedentes, no evidencia concluyente.
3. prompts/ANALISIS_split_goldenbridge_20260914.md y
   prompts/ESTRATEGIA_post_p0_refactor_20260914.md: hipótesis y resultados
   previos que deben verificarse contra el checkout.
4. prompts/PROMPT_auditoria_exhaustiva_impacto_60ms_golden_20260916.md:
   auditoría de 20 ms a 60 ms.
5. El checkout de GoldenBridge, configuración disponible y pruebas.

Al inicio, informa rama, commit, archivos analizados, configuración que pudiste
ver y limitaciones de acceso. Distingue código de producción, pruebas,
herramientas y posible código legado.

## 3. Restricciones que se preservan

Estas condiciones ya fueron revisadas y GoldenBridge Modal opera con ellas. El
análisis puede explicar cómo condicionan el split, pero no puede eliminarlas
para mejorar capacidad:

- Python 3.14.7 free-threaded.
- onnxruntime 1.24.4, ejecución ORT secuencial y GB_ORT_INTRA_OP=1.
- OMP_NUM_THREADS=1 y OPENBLAS_NUM_THREADS=1.
- Gate de modelo SmartTurn, artefacto de referencia y verificaciones CPU/ISA.
- Aislamiento, orden y cancelación de audio por llamada; nunca buffers o estado
  global reutilizado entre llamadas.
- No presentar cpu.weight, cgroups, más CPU o más hilos como solución de K sin
  causa medida. Pueden ser aislamiento operativo, no sustituto del diagnóstico.
- No introducir broker, base de datos, cola externa ni microservicio por
  preferencia estética. Sólo si resuelve una garantía concreta de orden,
  afinidad, recuperación o backpressure que no se logra más simplemente.

ai-engine ya puede agrupar tres tramas µ-law de 20 ms y enviar 480 B / 60 ms a
GoldenBridge. El cambio se validó extremo a extremo y no es parte de este
refactor. Por tanto:

- verifica cómo Golden acepta y procesa mensajes binarios de tamaño variable;
- modela la cadencia posterior a 60 ms;
- conserva riesgos de VAD, SmartTurn, detección de habla, fin de turno,
  barge-in y cancelación de TTS;
- no atribuyas una mejora de K al coalescer si no fue medida;
- no requieras modificaciones al GoldenBridge Modal existente para esta
  auditoría; instrumentación adicional queda como propuesta futura.

## 4. Inspección de código obligatoria

Antes de recomendar, busca y verifica callers. Para cada hallazgo registra:
archivo:símbolo, tipo de caller (producción, test, herramienta, legado o
desconocido), estado que lee/escribe, frecuencia por llamada y dependencia.

### A. Inventario de límites de ejecución

Construye un censo de procesos, hilos, event loops, pools y tareas:

- entrada WebSocket, reader, procesador, inbox, batching, carry y backpressure;
- pool o ejecutores VAD/DSP y retorno de resultados al loop;
- Silero, SmartTurn, resampleo, reducción de ruido y modelos/objetos compartidos
  o creados por sesión;
- STT, LLM y TTS: conexiones, streaming, reintentos, timeout y cancelación;
- pacer y emisor de TTS de vuelta al WebSocket ai-engine;
- health, reaper, cleanup, watchdogs, transferencia, hangup y recuperación.

Para cada uno indica: ubicación (loop, worker/hilo, remoto o desconocida),
cadencia (por trama, turno, respuesta o sesión), carga que crece con N y si el
coalescer de 60 ms la reduce o no.

### B. Recorridos críticos

Traza con símbolos reales estos cuatro caminos:

1. Audio binario ai-engine → validación/decodificación → inbox/batch →
   VAD/SmartTurn → habla o fin de turno.
2. Fin de turno → STT → estado conversacional/LLM → TTS → pacer → audio binario
   de vuelta a ai-engine.
3. Audio mientras TTS vive → detección de barge-in → cancelación →
   invalidación de turno → audio posterior.
4. Desconexión, hangup, transferencia, timeout o error de proveedor → cleanup
   de recursos y registries.

Incluye un diagrama ASCII simple. En cada flecha muestra dueño del estado, tipo
de dato, orden requerido y si cruza await, pool, hilo o proceso.

### C. Estado por sesión

Localiza todas las estructuras de sesión y presenta una tabla:

| Estado | Dueño actual | ¿mutable/caliente? | ¿cruzable? | Orden y ciclo de vida | Riesgo al duplicarlo |
|---|---|---|---|---|---|

Incluye, como mínimo:

- socket, locks, colas, carry/buffers y contadores de audio;
- VAD/SmartTurn, resamplers, contexto previo, buffers de turno e inferencia;
- STT streaming/finalización y TTS streaming/pacer;
- historia conversacional, campaña, funciones pendientes y variables;
- turn_id, generación de TTS, cancelación, tareas y watchdogs;
- registries de sesiones, ownership y liberación.

No declares algo serializable sólo porque pueda convertirse a JSON: analiza
conexiones vivas, locks, identidad, tamaño de buffer y consistencia temporal.

### D. Concurrencia y calidad

Audita qué garantías existen hoy y cómo se implementan:

- orden de audio de una misma llamada;
- exclusión entre llamadas y riesgo específico de Python free-threaded;
- barrera entre un job de audio en vuelo y la reserva/inicio de TTS;
- barge-in, cancelación de stream/TTS y protección contra respuesta tardía;
- generación/turn_id, task cancellation, flush, cierre y limpieza idempotente;
- carreras existentes protegidas y carreras nuevas al cruzar procesos.

Explica cada relación happens-before usando los locks, Events, contadores, await,
tareas o identificadores reales. Separa riesgo demostrado de hipótesis.

### E. Dependencias e integraciones

Mapea los límites externos y busca rutas duplicadas:

- WebSocket ai-engine: handshake, binario/JSON, orden, tamaño, afinidad,
  backpressure y cierre;
- adaptadores STT/LLM/TTS: contrato, timeout, retry, fallback, cancelación y
  propagación de error;
- FunctionCall, transferencia, hangup y respuesta desde ai-engine;
- health, configuración, observabilidad y ciclo de vida Modal.

Para rutas que integren el mismo sistema, localiza todos los callers antes de
proponer consolidación. Nombra diferencias concretas: sync/async, timeout,
errores, fallback, mutación de sesión, retorno, logging o estado global.

Diagnostica primero SRP/God Object, límites de integración duplicados y fuga de
infraestructura. Sólo después, si la evidencia lo sostiene, clasifica patrones
(Puertos y Adaptadores, Adapter, Mediator, State, Strategy, Facade o Command)
con estado: implementado, latente o no recomendado, y confianza alta/media/baja.

## 5. Comparación arquitectónica

Compara con evidencia estas opciones:

### Opción 0: GoldenBridge monolítico con entrada 60 ms

Determina qué trabajo queda en el loop por sesión, qué sí se redujo por el
coalescer y qué sigue crítico: batching, submit/resume, pacer, timers, callbacks
o proveedores. Es la línea base.

### Opción 1: MediaWorker + Control dentro de una tarea Modal

Evalúa dos procesos locales, sin añadir red:

- MediaWorker posible: socket de audio, entrada/salida, DSP/VAD/SmartTurn,
  buffers de turno, barge-in local y pacer.
- Control posible: máquina conversacional, campaña, STT/LLM/TTS como decisión,
  funciones, transferencia y política de turno.

No aceptes esta asignación sin validar la propiedad del estado. Calcula qué carga
queda en cada event loop. Explica por qué un MediaWorker Python que aún recibe
cada paquete puede repartir trabajo, pero no eliminar su carga de media.

### Opción 2: MediaWorker y Control en tareas Modal separadas

Analiza afinidad por llamada, IPC, eventos versionados, backpressure,
reconexión, recreación del par, fallo de una mitad, costos de red y memoria de
modelos. Indica si ofrece una ganancia que la Opción 1 no ofrece.

### Opción 3: VAD/SmartTurn en ai-engine

No presupongas que ya existe esta separación. Determina módulos/modelos/estado
a trasladar, qué conserva Golden, cómo se preserva aislamiento, quién se vuelve
la única fuente de verdad para turnos y qué impacto tiene en capacidad,
despliegue, afinidad y actualización de múltiples ai-engine.

### Opción 4: No separar aún

Define las señales que prueban que el cuello está en DSP/pool/batching,
proveedores remotos/red, semántica temporal o límites de Modal. Propón el menor
experimento que lo confirme, sin ajustes a ciegas.

Entrega una tabla:

| Opción | Qué cambia de verdad | Qué no cambia | Estado/calidad en riesgo | ¿eleva K o sólo replica K? | Evidencia necesaria | Dictamen |
|---|---|---|---|---|---|---|

## 6. Contrato entre procesos, sólo si el split es viable

Si la auditoría concluye que el split merece un PoC, diseña un contrato mínimo,
versionado y comprobable; no una lista informal de eventos.

Para cada dirección define:

- mensaje, versión, call_id, seq, turn_id, generation_id y payload;
- dueño del socket y de la salida TTS;
- orden necesario, duplicados, idempotencia y eventos no reintentables;
- timeout, tamaño máximo, backpressure, cierre y pérdida de conexión;
- semántica de Speak, Cancel, BargeIn, TurnEnd, Transcript, Transfer y Hangup
  sólo cuando haya equivalentes comprobados en el código;
- cómo un Cancel tardío no corta otro turno ni otra llamada;
- afinidad: una llamada no cambia de dueño durante la conversación.

Incluye matriz:

| Falla | Estado de media | Estado de control | Acción segura | Riesgo residual | Métrica/alerta |
|---|---|---|---|---|---|

No propongas exactly-once sin probar que el transporte y los efectos lo permiten.
Prefiere orden por llamada, idempotencia y generación monótona cuando sea la
garantía realista.

## 7. Medición de capacidad que decide el refactor

K no se deduce de max_inputs ni de conexiones exitosas. Define las mediciones
necesarias:

- externas: sesiones completadas, errores, SettingsApplied, primera respuesta y
  audio, RTT, turnos/s;
- internas: llegada de audio, profundidad inbox, frames/bytes por ciclo,
  submit, duración de job, resume, ciclo, espera de pool, tareas, backlog y
  memoria por sesión;
- calidad: habla/fin de turno, barge-in, cancelación correcta, TTS tardío o
  duplicado, pérdida/mezcla de audio, cleanup y fugas;
- entorno: read-back OMP/OpenBLAS/ORT, CPU solicitada, tareas Modal,
  running_inputs y backlog del autoscaler.

Distingue qué se observa sin logs/control-plane de Modal y qué es imposible
atribuir sin ellos. Para K por tarea exige una rampa con max_containers=1; si
Modal autoscalea, la prueba sólo mide capacidad agregada.

Propón rampa posterior a 60 ms (por ejemplo 10 → 30 → 50 → 70 → 100), con
duración, criterio de estabilidad, parada y decisión previa:

1. submit/resume/cycle crecen y DSP/remotos se mantienen: split podría aplicar.
2. job/pool/batch dominan: split no es primera palanca.
3. STT/LLM/TTS o red dominan: no refactorizar Golden por ese síntoma.
4. Calidad se degrada: investigar semántica temporal o revertir 60 ms antes de
   separar procesos.

Aclara qué mide una carga virtual ai-engine → Golden y qué aún requeriría una
llamada real controlada con Asterisk.

## 8. Fases de refactor, sólo si quedan justificadas

Propón fases pequeñas, reversibles y sin doble dueño de una llamada:

1. caracterización y extracción de interfaces dentro del monolito;
2. contrato in-process bajo feature flag y pruebas de orden/cancelación;
3. dos procesos dentro de una tarea Modal y A/B contra monolito;
4. tareas separadas sólo si la ganancia de K compensa IPC y riesgo;
5. rollout, observabilidad, rollback y criterio de descarte.

Para cada fase especifica símbolos reales afectados, pruebas, dependencia,
métrica de éxito, rollback, esfuerzo cualitativo y riesgo principal.

Si el dictamen es negativo o condicionado, entrega en su lugar el experimento
mínimo que puede refutarlo y lista explícitamente el trabajo que no debe iniciar.

## 9. Formato de entrega

1. Alcance auditado.
2. Dictamen ejecutivo: justificado ahora / sólo si la rampa lo demuestra / no
   recomendado.
3. Mapa de ejecución y recorridos críticos.
4. Inventario de estado y garantías de concurrencia.
5. Dependencias, integraciones y callers verificados.
6. Diagnóstico de responsabilidades, patrones y antipatrones.
7. Comparación de opciones 0–4.
8. Contrato de proceso e IPC, sólo si aplica.
9. Plan de medición y árbol de decisión.
10. Fases condicionadas, esfuerzo, riesgo y rollback.
11. Preguntas abiertas que realmente bloquean decisión y cómo resolverlas.

Cierra con una recomendación concreta: qué hacer primero, qué no hacer todavía y
cuál es el criterio objetivo que autoriza iniciar el split.

