# Análisis del split de GoldenBridge: MediaWorker / Control

Actúa como arquitecto de software y revisa el **código real** de GoldenBridge
antes de concluir. Este encargo es un análisis y diseño técnico: no escribas
código de producción ni propongas cambios fuera del alcance indicado.

## Contexto y límites fijos

Lee primero `context/ARQUITECTURA_STACK.md`. Esa topología es fija: no vuelvas
a analizarla ni propongas mover Asterisk, el túnel, ai-engine o el endpoint de
Modal.

El objetivo de negocio sigue siendo 2,000 llamadas mediante varios ai-engine y
una flota autoscalable de GoldenBridge Modal. El objetivo técnico inmediato es
llevar la capacidad de **una tarea GoldenBridge en Modal a K≈100**.

El P0 ya pasó: GoldenBridge Modal recibió el mismo audio en mensajes de 20 ms y
60 ms, incluyó STT/LLM/TTS y barge-in, y 60 ms redujo 3× los mensajes WebSocket
de entrada. El coalescer 20/60 ms pertenece a ai-engine y está en una estrategia
separada; no confundas ese cambio con un refactor de GoldenBridge.

La estrategia anterior en
`prompts/ESTRATEGIA_post_p0_refactor_20260914.md` es sólo una hipótesis previa.
Úsala como contexto, pero valida sus afirmaciones contra código y no la des por
correcta de antemano.

## Contrato no negociable ya activo en GoldenBridge Modal

Estas especificaciones ya fueron revisadas y el GoldenBridge desplegado en Modal
**ya opera con ellas**. No propongas cambiarlas para el split ni para alcanzar K:

- Python **3.14.7 free-threaded**.
- `onnxruntime==1.24.4`, ORT secuencial y `GB_ORT_INTRA_OP=1`.
- `OMP_NUM_THREADS=1` y `OPENBLAS_NUM_THREADS=1`.
- Modelo SmartTurn, artefacto de referencia y gate de arranque preservados;
  nunca desactivar el gate para obtener capacidad.
- La verificación de CPU/ISA, incluido AVX512/VNNI cuando aplique, se conserva
  en runtime. No asumir una ISA de Modal sin evidencia de runtime.
- El aislamiento, orden y cancelación de audio por llamada son deliberados.
  No usar estado global, buffers reutilizados entre llamadas ni paralelismo que
  pueda contaminar audio entre sesiones.

Modal puede ejecutar múltiples inputs en una tarea, pero esa admisión no prueba
que un loop soporte K. `max_containers=1` y CYCLE-PROBE siguen siendo necesarios
para medir K. No presentes cgroups como solución de capacidad.

## Pregunta principal

Determina si un split de GoldenBridge en **MediaWorker** y **Control** es un
refactor técnicamente justificado, qué separa en verdad y qué no resuelve. El
nombre de los componentes no es una respuesta: el análisis debe partir de
propiedad de estado, flujo de bytes, cancelaciones y carga de cada loop.

Compara con rigor estas tres opciones:

1. Mantener GoldenBridge monolítico en Modal, con ai-engine enviando 60 ms.
2. Separar internamente GoldenBridge en MediaWorker + Control.
3. Trasladar VAD/SmartTurn a ai-engine y dejar GoldenBridge como Control.

No declares que ai-engine ya es un MediaWorker sólo porque posee AudioSocket y
la conversión de códec. Distingue transporte de audio de VAD, SmartTurn,
resampleo, STT, barge-in, estado de turno, TTS y cancelación.

## Inspección obligatoria antes de recomendar

Localiza en el repositorio, con archivo, símbolo y callers verificados:

1. La entrada WebSocket binaria y el recorrido completo de una trama hasta VAD,
   SmartTurn, STT, LLM, TTS y audio de salida.
2. Todo estado por sesión: colas, resamplers, VAD, SmartTurn, turn state,
   buffers de TTS, locks, tareas asyncio y registries de sesión.
3. Cómo se produce, ordena y cancela barge-in/TTS; identifica carreras ya
   protegidas y las que aparecerían al cruzar un límite de proceso.
4. El lugar donde se envían `submit`, `resume`, jobs y ciclos de loop, junto con
   CYCLE-PROBE y las métricas que permitirían atribuir una rodilla.
5. Todas las rutas de integración relevantes y sus callers: WS, proveedores
   STT/LLM/TTS, transferencias, hangup, salud y recuperación de sesión. Distingue
   producción, tests, herramientas y código posiblemente muerto.

Si no puedes verificar algo en el código, marca **pendiente de comprobar**; no
inventes nombres de módulos ni contratos.

## Entregables obligatorios

### 1. Dictamen de necesidad

Da un dictamen explícito: **justificado ahora**, **justificado sólo si F3 lo
demuestra**, o **no recomendado**. Explica cuál de estas señales en la rampa K
lo justificaría:

- `resume_vad_p90`, `submit_p90` o `cycle_p90` aumentan con N mientras los
  proveedores remotos permanecen planos;
- la causa es batch/timers/DSP/proveedores, en cuyo caso el split podría no
  atacar el cuello;
- el problema procede de STT/LLM/TTS remoto, en cuyo caso no se refactoriza GB.

Separa con claridad **evidencia de código**, **inferencia** y **pendiente de
medir**. No conviertas el P0 en evidencia de K.

### 2. Mapa de responsabilidades y estado

Presenta una tabla de tres columnas: monolito actual, MediaWorker propuesto y
Control propuesto. Para cada responsabilidad indica quién es dueño y por qué:

- WebSocket/audio µ-law, resampleo, VAD, SmartTurn y detección de fin de turno;
- STT streaming/final, LLM, TTS, pacer y reproducción;
- barge-in, cancelación, transferencia, hangup, campaña y máquina de estados;
- observabilidad, health y ciclo de vida de una sesión.

Para cada elemento de estado por sesión, indica si se queda local, se serializa
como evento, o no puede cruzar el proceso. Ningún buffer de audio puede pasar a
estado global o compartirse entre llamadas.

### 3. Diseño implementable del límite MediaWorker ↔ Control

Si el split resulta viable, diseña un contrato mínimo y versionado, no una lista
informal de eventos. Incluye por cada dirección:

- mensaje, campos, `call_id`, versión, `seq`, `turn_id` y `gen_id` cuando
  corresponda;
- orden, idempotencia, duplicados, timeout, backpressure, pérdida de conexión y
  cierre;
- quién conserva el socket con ai-engine y quién emite el audio TTS de vuelta;
- cómo `barge_in` cancela exactamente el TTS/generación correspondiente sin
  cortar otro turno ni otra llamada;
- qué eventos pueden ser at-least-once y cuáles exigen orden estricto.

No introduzcas un broker, base de datos o servicio adicional salvo que el código
y el contrato hagan inevitable esa necesidad; si lo propones, cuantifica qué
problema concreto resuelve y el nuevo riesgo operativo.

### 4. Topología de despliegue y escalamiento

Compara, sin asumir que una sea correcta:

- dos procesos dentro de una misma tarea Modal;
- MediaWorker y Control como tareas Modal separadas;
- MediaWorker absorbido por ai-engine.

Para cada una, indica afinidad por llamada, cantidad de loops que realmente se
replican, dónde reside el estado caliente, costo de copiar/modelos, superficie
de fallos y si eleva K o sólo cambia el nombre de varias réplicas. Explica por
qué partir un proceso Python no aumenta K automáticamente.

### 5. Plan de refactor real, sólo si se justifica

Propón fases pequeñas, reversibles y sin doble dueño de una llamada:

1. extraer interfaces y tests de caracterización dentro del monolito;
2. ejecutar MediaWorker y Control con el contrato dentro de una tarea, bajo flag;
3. comparar K monolito vs split con la misma rampa y mismas restricciones ORT;
4. separar tareas sólo si la medición prueba ganancia;
5. rollout gradual, rollback y criterios de descarte.

Para cada fase: módulos/símbolos reales que cambiarían, pruebas de orden y
aislamiento, métrica de éxito, rollback y principal riesgo.

### 6. Patrones y riesgos de diseño

Diagnostica primero cualquier violación de responsabilidad única o límite de
integración duplicado que el código pruebe. Después, sólo si corresponde, nombra
el patrón: puerto/adaptador para el límite de procesos, máquina de estados para
el turno, mediador para coordinación de eventos, etc. No uses un patrón por su
nombre ni propongas una reescritura completa.

Identifica especialmente riesgos de: orden de audio, fuga de sesión, buffers
huérfanos, estado de VAD/SmartTurn, cancelación distribuida, reconexión, TTS
duplicado, degradación de barge-in, memoria duplicada de modelos y regresiones
por Python free-threaded.

### 7. Resultado ejecutivo

Cierra con:

- recomendación concreta y condición que la activa;
- el menor experimento que puede refutarla;
- estimación de esfuerzo/riesgo para el diseño, un PoC y un refactor de
  producción;
- una lista breve de decisiones que no deben tomarse hasta tener F3/K.

Responde en español. No incluyas secretos, tokens ni credenciales.
