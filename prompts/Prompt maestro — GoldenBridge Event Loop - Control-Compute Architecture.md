# CONTEXTO DEL PROYECTO

Estoy trabajando en **GoldenBridge**, un AI voice engine / contact-center engine integrado con Asterisk.

Repositorio:

```text
https://github.com/MrBotGT/GoldenBridge
```

La rama activa y sobre la cual debe realizarse todo análisis es:

```text
api
```

No analizar otras ramas salvo que sea estrictamente necesario para entender antecedentes.

Antes de proponer cambios:

1. Inspecciona el código real de la rama `api`.
2. Identifica el entry point de la aplicación.
3. Identifica dónde se inicializa `asyncio` / `uvloop`.
4. Identifica dónde se crean threads, executors, pools y tareas CPU-bound.
5. Identifica dónde se inicializan ONNX Runtime, VAD, embeddings, DSP u otras librerías nativas.
6. Identifica qué componentes participan en el hot path de una llamada.
7. Traza el flujo completo desde que entra audio/evento hasta que se procesa y se devuelve el resultado.

No asumas la arquitectura: obténla del código.

---

# PROBLEMA QUE ESTAMOS INVESTIGANDO

GoldenBridge corre actualmente en Linux usando:

```text
Python 3.14.x free-threaded
GIL deshabilitado
uvloop
Linux
SMT2
```

En el servidor de referencia existen:

```text
16 logical CPUs
8 physical cores
```

Bajo alta concurrencia, el hilo que ejecuta el event loop empieza a competir por CPU con threads del mismo proceso.

El problema observado no es principalmente I/O.

El síntoma importante es:

```text
event loop runnable
        │
        ▼
espera demasiado tiempo en runqueue
        │
        ▼
aumenta latencia para reanudar procesamiento
```

Existe una correlación clara entre concurrencia y latencia del loop.

En producción se observó una rodilla alrededor de:

```text
~40 llamadas concurrentes
```

y en pruebas con aproximadamente 50 sesiones se pudo reproducir el problema.

---

# EVIDENCIA EXPERIMENTAL PREVIA

Existe un documento:

```text
EVENT_LOOP.md
```

Debe ser leído como evidencia experimental y contexto, pero **NO como espacio obligatorio de soluciones**.

No debes asumir que las soluciones A1, A2 o B de ese documento son las únicas ni las mejores.

Entre los experimentos existentes:

## Baseline aproximado — 12 workers

Se observó aproximadamente:

```text
event-loop runqueue wait: ~2073 ms / ventana de prueba
preemptions: ~1966 / 5 s
resume_vad p50: ~8.9 ms
completed turns: ~1076
inbox p99: ~80 frames
CPU: ~12.54 cores
```

## Reducción a 6 workers

El tiempo de reanudación del loop mejoró, pero el sistema empeoró:

```text
completed turns: ~897
inbox p99: ~579 frames
```

Conclusión importante:

> Reducir workers puede simplemente mover el starvation desde el event loop hacia la cola de trabajo.

Por lo tanto:

```text
resume_vad
```

NO puede utilizarse solo como criterio de éxito.

Siempre debe medirse también comportamiento end-to-end.

## Afinidad / aislamiento de un core físico

Se probó aislar el event loop en un core físico y no usar su sibling SMT para workers.

Resultados aproximados:

```text
loop runqueue wait: 2073.5 ms → 132.9 ms
preemptions: 1966 → 73
resume_vad p50: 8.9 ms → 0.4 ms
completed turns: ~1081
inbox p99: ~46
CPU total: ~11.01 cores
```

Esto demuestra que existe competencia de scheduler.

Pero NO demuestra que reservar un core sea la mejor solución arquitectónica.

El objetivo actual es encontrar alternativas mejores.

---

# CAMBIO YA REALIZADO — PHASE 1

Existe un cambio identificado como:

```text
GB_ORT_INTRA_OP=1
```

que ya fue aplicado y medido.

Este cambio redujo parte de la latencia del loop a cargas altas.

Sin embargo los percentiles extremos de algunas métricas end-to-end empeoraron.

Por ejemplo, algunas campañas mostraron aumento de:

```text
cycle_p90 p99
turn_end_latency p99
```

Por tanto hay que estudiar no solamente medianas sino también tails.

---

# PHASE 2 PENDIENTE

Existe otro cambio identificado como:

```text
OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1
```

Fue aplicado temporalmente y se verificó que desaparecían aproximadamente:

```text
15 threads OpenMP
```

Esos threads parecían consumir una cantidad muy significativa de CPU aunque aparentemente no estaban realizando trabajo útil.

Se estimó aproximadamente:

```text
~1.57 cores continuos
```

de consumo asociado.

Sin embargo:

> Phase 2 todavía NO fue medido correctamente bajo tráfico comparable.

Debe tratarse como una variable pendiente.

No mezclarla automáticamente con otros cambios si se quiere preservar atribución causal.

---

# INVESTIGACIONES ADICIONALES

Voy a agregar al contexto varios documentos derivados de investigaciones previas.

Debes leerlos todos.

Pueden contener propuestas como:

```text
CPU affinity
taskset
sched_setaffinity
nice
SCHED_FIFO
SCHED_RR
EEVDF
custom slice
cgroup v2
cpu.weight
control-plane / compute-plane
process isolation
Modal
bounded concurrency
ONNX Runtime thread pools
OpenMP
BLAS
shared pools
IPC
Unix sockets
shared memory
multiple event loops
sharding
etc.
```

IMPORTANTE:

Los documentos de investigación:

- son evidencia y propuestas;
- NO son decisiones arquitectónicas;
- NO deben ser aceptados automáticamente;
- pueden contener hipótesis equivocadas;
- deben contrastarse contra el código real y contra las restricciones reales del sistema.

---

# HALLAZGO SOBRE EEVDF / sched_setattr

Se investigó el uso de custom time slices de EEVDF mediante `sched_setattr`.

La infraestructura actual usa:

```text
sandbox:
Linux 6.8.0-1063-aws

producción:
Linux 6.8.0-1063-aws
Ubuntu 22.04.5
```

La funcionalidad relevante de EEVDF/custom slice requiere un kernel posterior, aproximadamente Linux 6.12+.

Se realizó además una prueba empírica.

Resultado:

```text
sched_setattr(runtime=3ms)
return = 0
```

pero posteriormente:

```text
sched_getattr()
runtime = 0
```

Es decir:

> En kernel 6.8 la llamada puede aparentar éxito pero el valor solicitado no queda aplicado.

Conclusión:

**EEVDF custom slice queda fuera de las soluciones viables para la infraestructura actual.**

Además se establece una regla metodológica:

> Nunca validar una capacidad del kernel únicamente por el código de retorno del syscall. Verificar siempre el estado efectivo posterior mediante lectura de vuelta.

---

# RUTA ARQUITECTÓNICA QUE SE ESTÁ EVALUANDO

Estamos evaluando separar GoldenBridge en dos dominios conceptuales:

```text
CONTROL PLANE
```

y

```text
COMPUTE PLANE
```

No asumir todavía que esta es definitivamente la solución correcta; debe analizarse contra el código.

Conceptualmente:

```text
┌──────────────────────────────┐
│ GoldenBridge Control         │
│                              │
│ asyncio / uvloop             │
│ sockets                      │
│ AudioSocket                  │
│ timers                       │
│ session state                │
│ state machines               │
│ routing                      │
│ coordinación                 │
└──────────────┬───────────────┘
               │
               │ IPC / RPC
               ▼
┌──────────────────────────────┐
│ GoldenBridge Compute         │
│                              │
│ ONNX Runtime                 │
│ VAD                          │
│ embeddings                   │
│ DSP                          │
│ CPU-bound work               │
│ native thread pools          │
└──────────────────────────────┘
```

La idea fundamental sería:

```text
Control coordina.
Compute calcula.
```

El objetivo es que el proceso que ejecuta uvloop no compita directamente con decenas de threads nativos CPU-bound.

---

# CGROUP V2 / cpu.weight

Estamos considerando usar cgroup v2 como mecanismo LOCAL para validar QoS entre ambos procesos.

La arquitectura conceptual sería:

```text
goldenbridge.slice
│
├── goldenbridge-control.service
│       CPUWeight=400
│
└── goldenbridge-compute.service
        CPUWeight=100
```

Esto NO debe interpretarse como:

```text
control recibe siempre 80% de CPU
```

`cpu.weight` es proporcional bajo contención y work-conserving.

Cuando el proceso control está durmiendo/esperando I/O, compute debe poder aprovechar los CPUs disponibles.

Por tanto queremos investigar una hipótesis:

> El event loop no necesita necesariamente un core exclusivo; puede ser suficiente garantizar que consiga CPU rápidamente durante sus pequeñas ventanas runnable.

Eso permitiría evitar:

```text
reservar un core físico
perder su sibling SMT
topology discovery
hard-coded affinities
privilegios RT
```

---

# ENTORNO LOCAL PARA EL POC

Voy a desarrollar un PoC inicialmente en:

```text
WSL2
Ubuntu 24.04.3 LTS (Noble Numbat)
kernel:
6.6.87.2-microsoft-standard-WSL2
```

Ya se verificó:

```bash
stat -fc %T /sys/fs/cgroup
```

resultado:

```text
cgroup2fs
```

Y:

```bash
cat /sys/fs/cgroup/cgroup.controllers
```

resultado:

```text
cpuset cpu io memory hugetlb pids rdma
```

También:

```bash
cat /sys/fs/cgroup/cgroup.subtree_control
```

resultado:

```text
cpuset cpu io memory hugetlb pids rdma
```

Y:

```bash
systemctl is-system-running
```

resultado:

```text
running
```

Por lo tanto WSL es válido para:

- probar arquitectura;
- probar separación de procesos;
- probar systemd;
- probar cgroup v2;
- probar `CPUWeight`.

Pero:

> No usar resultados absolutos de performance en WSL como equivalentes a producción.

WSL debe utilizarse para comprobar mecanismo y causalidad, no para establecer capacidad final del servidor Linux de producción.

---

# CONTAINERIZACIÓN Y MODAL

La arquitectura debe diseñarse de forma que sea portable posteriormente a Modal.

Debe mantenerse una separación estricta entre:

```text
arquitectura
```

y:

```text
política de deployment
```

La separación:

```text
control plane / compute plane
```

debe ser portable.

En cambio:

```text
systemd
CPUWeight
cgroup v2
```

son mecanismos específicos del deployment Linux local.

GoldenBridge NO debe depender directamente de ellos.

Idealmente debe existir una abstracción equivalente a:

```python
class ComputeBackend:
    async def vad(...):
        ...

    async def embedding(...):
        ...

    async def inference(...):
        ...
```

con implementaciones conceptuales:

```text
ComputeBackend
│
├── LocalComputeBackend
│       IPC / Unix socket / local process
│
└── ModalComputeBackend
        RPC / Modal Function / Modal Cls
```

De esta manera la lógica principal de GoldenBridge no sabe si el compute ocurre:

```text
mismo host
otro proceso
otro container
Modal
otro nodo
```

---

# OBJETIVO PRINCIPAL DEL ANÁLISIS

No quiero simplemente implementar cgroups.

Primero quiero saber:

> ¿Dónde está realmente el conflicto de ejecución en GoldenBridge y cuál es la frontera mínima que permitiría separar coordinación y compute sin introducir más latencia de la que queremos eliminar?

Debes localizar en el código:

### 1. Event loop

Identifica:

- dónde se crea;
- quién lo ejecuta;
- thread principal;
- lifecycle;
- qué callbacks/tasks llegan al loop.

### 2. Compute CPU-bound

Clasifica todas las operaciones relevantes:

```text
VAD
ONNX
embeddings
DSP
resampling
encoding
decoding
JSON pesado
audio transformations
feature extraction
etc.
```

Para cada una indica:

```text
sync / async
Python / native
thread creation
executor
pool
latencia típica
frecuencia por sesión
frecuencia por frame
tamaño de datos
estado necesario
```

### 3. Thread topology

Determina:

```text
qué threads existen
quién los crea
cuándo se crean
si son lazy
si pertenecen a ORT
si pertenecen a OpenMP
si pertenecen a BLAS
si pertenecen a executors Python
si pertenecen a librerías externas
```

### 4. Dependencias de estado

Identifica qué objetos CPU-heavy:

- son stateless;
- mantienen modelo;
- mantienen caches;
- mantienen estado por sesión;
- requieren acceder directamente a objetos del event loop.

Esto determinará qué puede moverse a otro proceso.

---

# PREGUNTA ARQUITECTÓNICA PRINCIPAL

Determina si podemos transformar algo parecido a:

```python
async def on_audio(frame):
    result = local_cpu_operation(frame)
    await handle(result)
```

en:

```python
async def on_audio(frame):
    result = await compute_backend.operation(frame)
    await handle(result)
```

sin romper:

```text
orden de frames
estado de sesión
barge-in
cancelaciones
timeouts
latencia
backpressure
lifecycle
shutdown
```

---

# IPC

Si la separación de procesos es viable, evalúa alternativas.

Por ejemplo:

```text
Unix Domain Socket
shared memory
memory mapped ring buffer
multiprocessing shared_memory
pipes
ZeroMQ
gRPC localhost
HTTP localhost
custom binary protocol
```

No elegir por comodidad.

Evaluar:

```text
latencia
copias de memoria
serialización
GC
backpressure
cancelación
orden
multiplexación por session_id
failure isolation
observabilidad
portabilidad a containers
portabilidad a Modal
```

Para audio de alta frecuencia, evitar copiar buffers grandes innecesariamente.

Puede ser preferible un modelo:

```text
shared buffer
+
mensaje pequeño de control
```

por ejemplo:

```text
session_id
buffer_offset
frame_count
operation
deadline
sequence_number
```

---

# BACKPRESSURE

La arquitectura nueva NO debe permitir crecimiento de colas sin límite.

Debe existir explícitamente:

```text
bounded queue
```

o mecanismo equivalente.

Analiza dónde aplicar backpressure.

Por ejemplo:

```text
AudioSocket
    ↓
session ingress
    ↓
bounded compute queue
    ↓
compute
```

Determina qué hacer cuando compute no alcanza a procesar:

```text
drop?
coalesce?
replace newest?
replace oldest?
block producer?
cancel stale work?
skip intermediate VAD frame?
```

La semántica dependerá del tipo de trabajo.

No todas las operaciones deben tener la misma política.

---

# DEADLINES Y TRABAJO OBSOLETO

Analiza si GoldenBridge procesa actualmente trabajo que ya perdió utilidad.

Ejemplo:

```text
frame A
frame B
frame C
```

Si al llegar al compute C, el resultado de A ya no puede cambiar ninguna decisión, ejecutarlo puede ser desperdicio de CPU.

Investiga si pueden introducirse:

```text
deadlines
generation IDs
cancellation tokens
stale-work cancellation
latest-value wins
coalescing
```

especialmente en procesamiento de audio en tiempo real.

Esto podría reducir competencia sin modificar scheduler.

---

# ONNX RUNTIME

Inspecciona específicamente cómo se configura ONNX Runtime.

Buscar:

```text
InferenceSession
SessionOptions
intra_op_num_threads
inter_op_num_threads
execution_mode
allow_spinning
use_per_session_threads
global thread pool
OpenMP
BLAS
```

La hipótesis a evaluar es:

> Puede existir nested parallelism: paralelismo entre llamadas + paralelismo interno por inferencia.

En un servidor con muchas sesiones concurrentes podría ser mejor:

```text
muchas inferencias
×
pocos threads por inferencia
```

que:

```text
muchas inferencias
×
muchos threads por inferencia
```

No asumirlo: comprobarlo en el código y posteriormente medirlo.

---

# IMPORTANTE: cpu.weight NO ES LA ARQUITECTURA

No introducir llamadas como:

```python
open("/sys/fs/cgroup/...")
```

dentro de GoldenBridge salvo que exista una razón extraordinaria.

Preferir:

```text
systemd
Docker / OCI
orchestrator
Modal
```

como responsables de políticas de recursos.

GoldenBridge debe permanecer agnóstico.

---

# POC PROPUESTO

Si el análisis confirma viabilidad, diseñar un PoC incremental.

NO empezar refactorizando todo GoldenBridge.

Buscar la frontera CPU-heavy más pequeña que:

1. ocurra frecuentemente;
2. participe en el problema;
3. pueda aislarse con poco estado;
4. permita medir el efecto.

Por ejemplo, si VAD/ORT es buen candidato:

```text
GoldenBridge actual
       │
       │ ComputeBackend
       ▼
LocalComputeBackend
       │
       │ Unix socket
       ▼
ComputeProcess
       │
       ▼
ORT/VAD
```

La aplicación debe poder volver al backend actual mediante feature flag.

Por ejemplo conceptualmente:

```text
GB_COMPUTE_BACKEND=inprocess
GB_COMPUTE_BACKEND=local_process
```

Posteriormente:

```text
GB_COMPUTE_BACKEND=modal
```

---

# CGROUP POC

Si existen dos procesos:

```text
goldenbridge-control
goldenbridge-compute
```

probar inicialmente:

```text
baseline:

control:
CPUWeight=100

compute:
CPUWeight=100
```

versus:

```text
experiment:

control:
CPUWeight=400

compute:
CPUWeight=100
```

NO utilizar inicialmente:

```text
10000 vs 1
```

porque ocultaría el comportamiento real.

La finalidad es probar si una preferencia moderada reduce el runqueue wait del control sin provocar starvation del compute.

---

# MÉTRICAS OBLIGATORIAS

Nunca juzgar el resultado mediante una sola métrica.

## Scheduler

Para el event loop / control:

```text
schedstat:
runtime
runqueue wait
scheduler turns
```

Calcular:

```text
wait_ratio =
runqueue_wait /
(runtime + runqueue_wait)
```

También:

```text
voluntary context switches
nonvoluntary context switches
```

## Event loop

```text
resume_vad p50
resume_vad p90
resume_vad p99
```

o métricas equivalentes existentes.

## End-to-end

```text
cycle latency
cycle_p90
cycle p99
turn_end_latency
turn_end_latency p90
turn_end_latency p99
```

## Backpressure

```text
inbox depth
inbox p90
inbox p99
compute queue depth
compute queue wait
```

## Throughput

```text
completed turns
frames processed
operations/sec
calls successfully serviced
```

## Host

```text
total CPU
CPU per process
CPU per cgroup
runnable threads
load average
runqueue
```

## Controles

Registrar también:

```text
active_sessions
barge-ins
frames received
frames processed
dropped frames
cancelled work
stale work
```

---

# CONCURRENCIA

No agrupar resultados solamente por minuto.

Usar:

```text
active_sessions
```

como dimensión principal.

Analizar especialmente:

```text
1-5
6-10
11-15
16-25
26-40
41+
```

porque ya existe evidencia de una rodilla cerca de 40 sesiones.

---

# METODOLOGÍA EXPERIMENTAL

Aplicar:

> Una variable por campaña.

No cambiar simultáneamente:

```text
worker count
ORT threads
OMP configuration
cpu.weight
affinity
queue length
IPC
```

si se quiere saber qué produjo el resultado.

Cada experimento debe tener:

```text
hipótesis
variable independiente
variables controladas
métricas
predicción
criterio de éxito
criterio de falsación
```

---

# REGLA CONTRA FALSOS POSITIVOS

Un cambio NO es exitoso si únicamente mejora:

```text
runqueue wait
resume_vad
```

pero empeora:

```text
throughput
inbox
turn latency
tail latency
```

Ya ocurrió algo equivalente al reducir workers.

Por tanto:

> Toda mejora interna debe traducirse en una mejora end-to-end o al menos preservar claramente la capacidad del sistema.

---

# MODAL

La solución final debe poder evolucionar hacia Modal.

No asumir que `cpu.weight` estará disponible allí.

En Modal el equivalente arquitectónico será separar recursos y escalabilidad por componente.

Conceptualmente:

```text
GoldenBridge Control
        │
        │ RPC
        ▼
Modal Compute workers
```

Compute debe poder escalar independientemente.

Investiga cómo mapear eventualmente:

```text
concurrency
target_inputs
max_inputs
min_containers
max_containers
CPU allocation
```

pero NO mezclar todavía esa implementación con el PoC local.

---

# LO QUE NO QUIERO

No quiero una respuesta que diga simplemente:

```text
use taskset
use affinity
use nice
use cpu.weight
use multiprocessing
```

sin demostrar por qué.

Tampoco quiero que la existencia de EVENT_LOOP.md sesgue el análisis hacia las soluciones que ya aparecen allí.

No asumir:

```text
A1
A2
B
```

como espacio de soluciones.

No asumir que affinity es el baseline conceptual.

No asumir que `cpu.weight` será necesariamente mejor.

No asumir que separar procesos necesariamente será mejor.

Debemos obtener esas conclusiones del código y de mediciones.

---

# PRIMERA TAREA

Antes de modificar ningún archivo:

realiza un análisis del repositorio en la rama `api` y produce:

## 1. Mapa de ejecución

```text
startup
    ↓
event loop
    ↓
session/call handling
    ↓
audio ingress
    ↓
CPU work
    ↓
external providers / Asterisk
```

Incluye nombres concretos de:

```text
archivos
clases
funciones
métodos
```

## 2. Inventario de concurrencia

Tabla con:

```text
componente
archivo
thread/process
quién lo crea
pool
cantidad
lazy/eager
CPU-bound
I/O-bound
shared state
```

## 3. Inventario de compute

Tabla con:

```text
operación
archivo
función
frecuencia
input aproximado
output
estado
coste CPU probable
movible fuera de proceso: sí/no
```

## 4. Hot path

Traza al menos un flujo real completo de llamada/audio.

## 5. Frontera candidata

Determina cuál sería la **mínima frontera viable** para introducir:

```text
ComputeBackend
```

sin reescribir GoldenBridge.

## 6. Riesgos

Identificar especialmente:

```text
IPC overhead
serialization
ordering
session affinity
timeouts
cancellation
barge-in
backpressure
failure recovery
startup
shutdown
model loading
memory duplication
```

## 7. Propuesta de PoC

Después del análisis, proponer un PoC mínimo.

NO implementarlo todavía.

Debe indicar exactamente:

```text
archivos nuevos
archivos modificados
interfaces
procesos
IPC
feature flags
systemd units
métricas
rollback
```

## 8. Hipótesis falsable

Terminar con una frase explícita:

```text
La hipótesis que este PoC intenta falsar es:
...
```

y definir:

```text
qué resultado confirmaría la hipótesis
qué resultado la refutaría
```

---

# DOCUMENTOS DE CONTEXTO

Lee todos los documentos de investigación que agregue al workspace.

Distingue claramente:

```text
HECHO MEDIDO
```

de:

```text
HIPÓTESIS
```

de:

```text
PROPUESTA
```

de:

```text
INFERENCIA DEL ANÁLISIS ACTUAL
```

Si dos documentos se contradicen:

1. señala la contradicción;
2. usa el código real y la evidencia experimental para resolverla cuando sea posible;
3. si no puede resolverse, déjala explícitamente como cuestión pendiente;
4. no inventes una conciliación.

---

# REGLA FINAL

La meta no es mejorar una métrica aislada del event loop.

La meta es:

> maximizar la cantidad de llamadas concurrentes que GoldenBridge puede atender manteniendo latencias interactivas estables y sin trasladar el starvation desde el event loop hacia compute, colas o proveedores externos.

Prioriza:

```text
causalidad
tail latency
throughput
backpressure
portabilidad
operabilidad
simplicidad de rollback
```

por encima de optimizaciones locales difíciles de mantener.