# Estrategia post-P0: ai-engine → GoldenBridge Modal

Actúa como arquitecto técnico y diseña la **estrategia de ejecución**, no una
reescritura de la arquitectura ni código de producción todavía.

## Regla de partida

Lee primero y toma como arquitectura fija:

`context/ARQUITECTURA_STACK.md`

No vuelvas a analizar, cuestionar ni rediseñar la topología actual. No propongas
mover Asterisk, el túnel ni ai-engine. No incluyas credenciales, tokens o
secretos en tu respuesta.

El objetivo inmediato es aumentar la capacidad de **una tarea/contenedor de
GoldenBridge en Modal hacia K≈100 llamadas**. El objetivo final de 2,000
llamadas se cubre con múltiples instancias de ai-engine y una flota autoscalable
de GoldenBridge en Modal, pero no hay que dimensionar esa flota hasta medir K.

## Estado decidido

- GoldenBridge completo continúa en Modal. No se implementa ahora un split
  MediaWorker/Control.
- ai-engine permanece dueño de AudioSocket, conversión `slin16 ↔ μ-law` y de la
  afinidad de cada llamada.
- Asterisk entrega audio al engine cada 20 ms. La propuesta es que ai-engine
  agrupe **tres frames μ-law consecutivos** y envíe a GoldenBridge un único
  mensaje WebSocket de 480 B cada 60 ms.
- GoldenBridge ya acepta audio binario μ-law de tamaño variable; no se modificó
  GoldenBridge para el P0.
- El rollback debe conservar 20 ms como opción explícita y reversible.
- Python 3.14 free-threaded: la protección actual de aislamiento y orden del
  audio por sesión es deliberada. No propongas estado global, reutilización de
  buffers entre llamadas ni paralelismo que pueda mezclar audio.

## Contrato de ejecución GoldenBridge Modal ya validado

Estas especificaciones ya fueron revisadas y **GoldenBridge Modal ya se ejecuta
con ellas**. Son restricciones de compatibilidad del refactor, no alternativas
de diseño ni puntos que deban proponerse, actualizarse o sustituirse:

- Python **3.14.7 free-threaded**.
- `onnxruntime==1.24.4`, con ejecución ORT secuencial y
  `GB_ORT_INTRA_OP=1`.
- El modelo SmartTurn y su artefacto de referencia permanecen congelados; se
  preserva el gate de arranque que valida la referencia y falla ante una
  divergencia. No se permite desactivar ese gate para obtener capacidad.
- `OMP_NUM_THREADS=1` y `OPENBLAS_NUM_THREADS=1`, además del límite de ORT.
  Como Modal puede establecer variables de threading según el CPU solicitado,
  el despliegue y toda medición de K deben registrar el valor efectivo en
  runtime; no basta con declararlo en la imagen.
- La compatibilidad de CPU/ISA, incluido AVX512/VNNI cuando aplique al gate,
  se verifica al arrancar cada tarea mediante el gate ya existente. No se debe
  asumir que Modal garantiza una ISA concreta sin evidencia de runtime.

Por tanto, el coalescer 20/60 ms y cualquier refactor posterior deben conservar
este contrato exactamente. Si una propuesta exige cambiarlo, debe considerarse
fuera de alcance y señalarse como bloqueo, no como una optimización sugerida.

## Evidencia P0 ya obtenida

El P0 no es una prueba de K. Es una prueba de compatibilidad antes de modificar
ai-engine: ¿puede GoldenBridge Modal recibir 60 ms en vez de 20 ms sin romper
la conversación?

Se usaron 10 audios `.ulaw` reales, distintos y normalizados a 9 s; se llamó
directamente al endpoint WSS de GoldenBridge en Modal. El camino ejercitado fue
audio → GoldenBridge Modal → STT → LLM → TTS → audio de vuelta.

Resultados de corridas **calientes**:

| Prueba | `applied` / `end_ok` | Errores / muertes | Frames lógicos | Mensajes WS |
|---|---:|---:|---:|---:|
| 20 ms | 10 / 10 | 0 / 0 | 79,264 | 79,264 |
| 60 ms | 10 / 10 | 0 / 0 | 79,278 | 26,426 |

El modo 60 ms transmitió prácticamente la misma cantidad de audio con 3× menos
mensajes WebSocket. Hubo transcripción, TTS y respuestas en las 10 sesiones.

En una repetición 60 ms, RTT típico p50≈63–66 ms y p90≈70–100 ms, con un pico
aislado p90=219 ms / máximo=278 ms que se recuperó de inmediato; no hubo
timeouts, cierres ni degradación sostenida. En 20 ms, RTT típico p50≈61–64 ms y
p90≈65–72 ms. No atribuyas todavía esos picos a GoldenBridge sin telemetría
interna.

También se ejecutó A/B de barge-in con cuatro sesiones objetivo (`l0`, `l3`,
`l6`, `l9`), mismo audio y `barge_in` después de 1.8 s:

| Modo | cortes en l0 / l3 / l6 / l9 |
|---|---|
| 20 ms | 7 / 2 / 16 / 6 |
| 60 ms | 6 / 8 / 2 / 1 |

Todas las sesiones objetivo interrumpieron al menos una vez, sin errores. La
cantidad de cortes no es comparable como rendimiento: depende de la duración
variable de cada respuesta LLM/TTS. El gate era preservar la capacidad de
interrumpir y se cumplió.

La primera prueba arrancó frío: `/health` tardó 34 s y luego 0.59 s. Ese costo
no es objetivo actual; sólo evita confundir handshake de cold-start con un fallo
de audio.

## Pregunta que debes resolver

Diseña la estrategia completa desde este punto hasta la decisión sobre un
refactor mayor de GoldenBridge. Antes de sugerir archivos o cambios concretos,
inspecciona el código real de ai-engine y GoldenBridge. Si el repositorio de
ai-engine no está disponible, enumera con precisión qué archivos, métodos y
datos necesitas revisar; no los inventes.

## Entregables obligatorios

1. Un plan por fases, con objetivo, cambio mínimo, evidencia de salida y
   rollback de cada fase:
   - implementar el coalescer 20/60 ms en ai-engine;
   - prueba real ai-engine → GoldenBridge Modal con 10 llamadas;
   - medición de K por tarea Modal;
   - decisión K≥100 vs K<100.

2. Para el coalescer, especifica el contrato que debe preservarse:
   orden exacto de bytes, cadencia real de 60 ms (sin adelantar audio), buffers
   separados por llamada, qué ocurre en cierre/desconexión, cómo se drena un
   paquete incompleto y cómo se activa el rollback a 20 ms.

3. Define los tests mínimos de ai-engine antes de desplegar: paridad de bytes
   20/60, aislamiento entre sesiones, temporización, TTS de vuelta y barge-in.
   Distingue tests unitarios, integración y carga.

4. Define la medición de K real en Modal. Debe impedir que Modal distribuya la
   carga entre tareas (`max_containers=1`) y llegar mediante rampa 10 → 30 → 60
   → 100. Indica telemetría requerida: CYCLE-PROBE, `inbox_depth`, backlog,
   tareas, read-back de OMP/BLAS/ORT, `end_ok`, errores y RTT. Señala qué parte
   requiere token de control-plane de Modal y qué parte puede hacerse sin él.

5. Da un árbol de decisión estricto:
   - K≥100: qué se congela, qué se despliega y qué queda como siguiente etapa.
   - K<100 por loop/coordinar audio: cuál sería el siguiente refactor de GB y
     por qué.
   - K<100 por STT/LLM/TTS remoto: qué no se debe refactorizar en GB.

6. Si propones un MediaWorker/Control posterior, describe responsabilidades,
   contrato de eventos, afinidad por llamada, estrategia de rollout y rollback.
   No afirmes que partir Python automáticamente eleva K: explica qué carga se
   elimina o se reparte y qué métrica lo demostraría.

7. Estima el esfuerzo y riesgo en tres categorías: coalescer en ai-engine,
   medición K y refactor mayor de GB. Separa hechos, inferencias y riesgos
   abiertos.

## Restricciones de respuesta

- Responde en español, de forma ejecutable y concisa.
- No propongas cgroups como solución de capacidad; sólo podrían servir después
  como aislamiento operativo.
- No afirmes que 60 ms reduce 3× todo el cómputo: reduce 3× los mensajes de
  entrada; el efecto sobre jobs/pool debe medirse.
- No declares éxito de K con P0: P0 ya pasó funcionalmente, pero K sigue sin
  medirse.
- Marca explícitamente cada conclusión como **evidencia**, **inferencia** o
  **pendiente de medir**.
