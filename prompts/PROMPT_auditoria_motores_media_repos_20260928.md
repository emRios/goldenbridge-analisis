# Encargo: auditoría comparativa de motores de llamadas y arquitecturas Media–Conversación

## Objetivo

Descarga y audita los repositorios indicados para identificar patrones comprobados que puedan aplicarse al refactor de GoldenBridge. La pregunta central no es si cada proyecto ofrece funciones similares, sino **cómo evita que un único event loop de control coordine todas las tramas de todas las llamadas**.

El resultado debe determinar qué diseño permite separar el motor Media del proceso Conversación manteniendo:

- un único dueño por llamada;
- aislamiento absoluto entre canales;
- orden de audio por sesión;
- barge-in local e inmediato;
- descarte de respuestas, audio y comandos obsoletos;
- backpressure limitado a la llamada afectada;
- paralelismo real entre núcleos;
- compatibilidad con el despliegue autoescalable de GoldenBridge en Modal;
- posibilidad de atender 100 llamadas por tarea como objetivo de fase y 2.000 llamadas mediante una flota de tareas.

No asumas que dividir Media y Conversación resuelve la capacidad. Debes comprobar si el diseño estudiado elimina el coordinador único del camino por trama, lo paraleliza realmente o sólo lo replica con otro nombre.

## Alcance actual autorizado

Este encargo reabre expresamente el análisis del split Media–Conversación y del motor Media. La prohibición histórica de reabrir ese split en `CLAUDE.md` no bloquea esta auditoría. Conserva las demás reglas del proyecto: evidencia etiquetada, ninguna capacidad inventada, lectura del estado efectivo y separación entre hechos, mediciones e inferencias.

La tarea es de descarga y análisis. No modifiques GoldenBridge, ai-engine ni el código de los repositorios descargados. No hagas commits, merges, pushes ni despliegues.

## Contexto obligatorio antes de descargar

Lee completamente, en este orden:

1. `CLAUDE.md`.
2. `context/ARQUITECTURA_STACK.md`.
3. `doctos/AUDITORIA_REFACTOR_websocket_server_api_08932a6.md`.
4. `doctos/DICTAMEN_MULTIPROCESO_POR_TAREA_MODAL_20260928.md`.
5. `doctos/DICTAMEN_FRONTERA_MEDIA_CONVERSACION_20260928.md`.
6. `prompts/EVENT_LOOP.md`, aplicando las correcciones de `CLAUDE.md`.

Audita GoldenBridge sobre la rama `api` y el commit indicado por los documentos. No cambies el checkout si el repositorio tiene trabajo local. Cuando sea necesario, usa `git show api:<ruta>` para consultar la versión estable sin tocar el árbol de trabajo.

## Directorio de trabajo

Todo el material externo y todos los resultados deben quedar dentro de:

```text
repo_analisis/
├── repos/
├── informes/
├── README.md
├── MATRIZ_COMPARATIVA.md
├── PROPUESTA_APLICABLE_GOLDEN.md
└── DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md
```

Descarga cada repositorio dentro de `repo_analisis/repos/<nombre>`. Usa clones superficiales del branch por defecto salvo que el historial sea indispensable. Para cada descarga registra:

- URL;
- branch analizado;
- commit exacto;
- fecha del commit;
- licencia observada;
- submódulos relevantes;
- estado de mantenimiento visible en el propio repositorio.

No presentes estrellas, publicidad del proyecto o afirmaciones comerciales como evidencia arquitectónica.

## Repositorios obligatorios

### 1. mediasoup

- URL: `https://github.com/versatica/mediasoup.git`
- Nombre local: `mediasoup`

Propósito de la auditoría: estudiar la separación entre la capa de control Node/Rust y los procesos nativos `mediasoup-worker` en C++.

Segmentos iniciales que debes localizar y seguir hasta sus callers reales:

- creación y supervisión de workers;
- `node/src/Worker.ts`;
- `node/src/Channel.ts`;
- proceso y loop nativo en `worker/src/Worker.cpp`;
- framing del canal IPC;
- IDs de recursos y enrutamiento de respuestas/notificaciones;
- backpressure, límites de mensajes y cierre del canal;
- propagación de muerte del worker;
- asignación de routers/transports a workers;
- modelo de uno o varios workers por núcleo.

Pregunta específica: ¿qué partes de este diseño permitirían que Python controle Golden sin recibir ni reenviar cada trama de audio?

### 2. FreeSWITCH

- URL: `https://github.com/signalwire/freeswitch.git`
- Nombre local: `freeswitch`

Propósito de la auditoría: estudiar un motor telefónico nativo que conserva canales y media y expone eventos/comandos a procesos externos.

Segmentos iniciales:

- lifecycle y ownership de canales;
- threads/loops utilizados por sesión y por media;
- `mod_event_socket` y sus modos inbound/outbound;
- identificación de llamada/canal en eventos y comandos;
- reproducción, break, flush y cancelación de media;
- VAD, ASR/TTS o media bugs cuando sean relevantes;
- transferencia, hangup y cierre idempotente;
- comportamiento cuando el controlador externo muere o deja de drenar;
- qué audio queda ya enviado o encolado al interrumpir.

Pregunta específica: ¿qué garantías telefónicas debería conservar un MediaWorker de Golden y cuáles ya pertenecen hoy a Asterisk/ai-engine?

### 3. jambonz feature server

- URL: `https://github.com/jambonz/jambonz-feature-server.git`
- Nombre local: `jambonz-feature-server`

Propósito de la auditoría: estudiar cómo una plataforma de voz separa FreeSWITCH/SBC, sesión de llamada, verbos de aplicación, STT/TTS y control conversacional.

Segmentos iniciales:

- clase o estructura canónica de sesión de llamada;
- scheduler/ejecutor de tareas o verbos;
- `gather`, speech recognition, streaming TTS y barge-in;
- conexión y comandos hacia FreeSWITCH;
- correlación de eventos con una llamada;
- cancelación de una tarea activa al llegar habla;
- transferencias, hangup y acciones terminales;
- routing y escalado entre feature servers;
- estado local, Redis y cualquier fuente duplicada de verdad.

Pregunta específica: ¿cómo separa estado telefónico, estado de reproducción y decisiones de aplicación sin mezclar llamadas?

### 4. LiveKit server

- URL: `https://github.com/livekit/livekit.git`
- Nombre local: `livekit-server`

Propósito de la auditoría: estudiar el plano de media distribuido en Go, afinidad de participantes/salas, routing y escalado.

Segmentos iniciales:

- ownership de Room/Participant/Track;
- distribución entre nodos;
- goroutines y colas del camino de audio;
- congestión y backpressure;
- cierre y drenaje;
- eventos de participante y publicación;
- separación entre señalización y media.

Pregunta específica: ¿qué mecanismo evita que un coordinador global procese todos los paquetes de todos los participantes?

### 5. LiveKit SIP

- URL: `https://github.com/livekit/sip.git`
- Nombre local: `livekit-sip`

Propósito de la auditoría: estudiar el puente SIP/RTP hacia el plano de media y la persistencia/identidad de sesiones.

Segmentos iniciales:

- creación y lifecycle de sesión SIP;
- ownership de puertos y streams RTP;
- asociación llamada–sala–participante;
- Redis/PSRPC y qué cruza por esos canales;
- DTMF, cierre y reconexión;
- métricas usadas para autoescalado.

Pregunta específica: ¿cómo ingresa una llamada al proceso Media dueño sin pasar su audio por un dispatcher de aplicación?

### 6. LiveKit Agents

- URL: `https://github.com/livekit/agents.git`
- Nombre local: `livekit-agents`

Propósito de la auditoría: estudiar dispatcher, pool de procesos, aislamiento de jobs, IPC, carga y drenaje.

Segmentos iniciales:

- `livekit-agents/livekit/agents/worker.py`;
- `livekit-agents/livekit/agents/ipc/proc_pool.py`;
- `job_proc_executor.py`, canal duplex y protocolo de mensajes;
- reserva de capacidad antes de aceptar jobs;
- procesos calientes/prewarm;
- muerte de hijos, timeouts y cleanup;
- lifecycle de `AgentSession`;
- interrupción, generación de habla y descarte de resultados tardíos;
- qué trabajo por trama sigue ejecutándose en Python.

Pregunta específica: ¿qué puede reutilizar Golden para supervisión y asignación y qué no sirve para elevar K porque aísla una llamada por proceso?

### 7. Pipecat

- URL: `https://github.com/pipecat-ai/pipecat.git`
- Nombre local: `pipecat`

Propósito de la auditoría: estudiar el contrato de frames, las interrupciones y la limpieza de audio pendiente.

Segmentos iniciales:

- `PipelineTask`, runners y queues;
- `FrameProcessor` y direcciones upstream/downstream;
- `InterruptionFrame` y variantes;
- `BaseOutputTransport` y sus media senders;
- cancelación de tareas de audio/reloj/video;
- limpieza de buffers y reset del resampler;
- audio interrumpible frente a no interrumpible;
- correlación por participante/destino;
- backpressure y aislamiento entre pipelines.

Pregunta específica: ¿qué invariantes de interrupción se pueden traducir a `SpeechInterrupted`, `CancelSpeech` y `speech_id` de Golden?

### 8. TEN Framework

- URL: `https://github.com/TEN-framework/ten-framework.git`
- Nombre local: `ten-framework`

Propósito de la auditoría: estudiar un runtime multilenguaje que conecta extensiones RTC nativas con extensiones AI en Python.

Segmentos iniciales:

- runtime y lifecycle de aplicaciones/grafos;
- grupos de extensiones y asignación de threads;
- frontera C/C++/Rust/Go/Python;
- comandos, datos, audio y video entre extensiones;
- routing por identidad;
- orden, backpressure y cierre;
- aislamiento entre sesiones o grafos;
- comportamiento ante muerte o bloqueo de una extensión;
- condiciones y restricciones de licencia para reutilización.

Pregunta específica: ¿permite conservar audio y barge-in en una extensión nativa mientras Python recibe solamente eventos de conversación?

## Arquitectura objetivo contra la cual comparar

No evalúes los repos en abstracto. Compáralos con este objetivo:

```text
ai-engine × N
      │ WebSocket de audio; afinidad durante toda la llamada
      ▼
Modal endpoint autoescalable
      │
      ├── MediaWorker 1 ── llamadas asignadas A…M
      ├── MediaWorker 2 ── llamadas asignadas N…Z
      └── MediaWorker W
              │
              │ eventos y comandos de baja frecuencia
              │ nunca audio por trama
              ▼
         Conversación
         campaña · historial · LLM · tools · política
```

Cada MediaWorker debe poseer de extremo a extremo:

- conexión WebSocket de sus llamadas;
- buffers y orden de audio;
- decodificación/resampleo;
- VAD y SmartTurn;
- streaming y Finalize de STT;
- conexión TTS, pacing y presupuesto de reproducción;
- cancelación y barge-in;
- estado temporal de inferencia;
- cleanup técnico.

Conversación debe recibir únicamente eventos semánticos y emitir comandos identificados. Usa como contrato de referencia inicial:

```text
(session_id, session_epoch, turn_id, speech_id, seq)
```

No lo aceptes automáticamente: compáralo con los mecanismos verificados en los repos y propón cambios cuando exista evidencia mejor.

## Método obligatorio de auditoría

Para cada segmento:

1. Localiza primero su implementación y todos sus callers relevantes.
2. Identifica el dueño del estado mutable.
3. Reconstruye el flujo completo de entrada, procesamiento, salida, interrupción y cierre.
4. Distingue llamadas síncronas, `await`, threads, procesos, goroutines y cruces de red/IPC.
5. Comprueba si el audio cruza la frontera de control y con qué frecuencia.
6. Identifica las colas y establece si están acotadas.
7. Comprueba cómo se mantiene el orden por llamada.
8. Comprueba cómo se rechazan mensajes de sesiones, turnos o generaciones obsoletas.
9. Sigue las rutas de error y cleanup hasta sus callers; no te limites al happy path.
10. Separa patrones realmente implementados, patrones latentes y opciones no recomendadas.

Para cada afirmación usa una etiqueta:

- `[CÓDIGO]`: verificada en código, con archivo, símbolo y líneas.
- `[DOCUMENTACIÓN]`: declarada por documentación oficial del repositorio.
- `[MEDICIÓN]`: respaldada por benchmark o prueba reproducible encontrada.
- `[INFERENCIA]`: conclusión razonada a partir de evidencia.
- `[PENDIENTE]`: no verificable con el material disponible.

No conviertas documentación comercial en `[MEDICIÓN]`. No afirmes concurrencia, llamadas por worker, CPU o latencia si el repositorio no contiene la medición reproducible.

## Preguntas obligatorias por repositorio

Responde explícitamente:

1. ¿Cuál es la unidad de ownership: llamada, participante, sala, pipeline, router o worker?
2. ¿Quién recibe cada paquete de audio?
3. ¿Cuántas veces cruza el runtime de control cada paquete?
4. ¿Existe un loop único compartido por muchas sesiones? ¿Qué trabajo hace?
5. ¿Cómo usa varios núcleos: threads, procesos, workers nativos o réplicas?
6. ¿Cómo se asigna una llamada a un worker y cómo se conserva la afinidad?
7. ¿Qué estado se comparte y qué estado permanece local?
8. ¿Qué protocolo usa el control: IPC, sockets, Redis, RPC o memoria?
9. ¿Cómo implementa orden, correlación, ACK, deduplicación y mensajes tardíos?
10. ¿Cómo maneja backpressure sin detener llamadas no relacionadas?
11. ¿Dónde ocurre el barge-in y dónde se purga el audio pendiente?
12. ¿Qué audio ya no puede retirarse cuando llega una interrupción?
13. ¿Qué ocurre si muere Media, Control o el canal entre ambos?
14. ¿Cómo drena llamadas durante shutdown o despliegue?
15. ¿Duplica modelos o recursos por proceso?
16. ¿Qué métricas expone para atribuir una rodilla de capacidad?
17. ¿Qué código o patrón se puede reutilizar legal y técnicamente?
18. ¿Adoptarlo exigiría reemplazar Asterisk, ai-engine, Modal o proveedores actuales?

## Formato de cada informe

Crea `repo_analisis/informes/NN_<repo>.md` con:

1. Identidad del repositorio y commit.
2. Dictamen ejecutivo.
3. Diagrama del flujo relevante.
4. Mapa de procesos, threads, loops y colas.
5. Ownership por sesión y recursos compartidos.
6. Contrato de control/IPC.
7. Ruta de audio y frecuencia de cruces.
8. Secuencia de barge-in.
9. Backpressure, fallos, reconexión y drenaje.
10. Escalado y uso real de núcleos.
11. Patrones y antipatrones.
12. Callers verificados de las piezas propuestas como referencia.
13. Aplicabilidad a GoldenBridge.
14. Riesgos de adopción o copia.
15. Lecciones concretas que pasan a la matriz comparativa.

Cuando identifiques una pieza reutilizable, documenta:

```text
Segmento:
Responsabilidad principal:
Pattern principal:
Estado: implemented | latent | not recommended
Confianza: high | medium | low
Evidencia:
Callers verificados:
Contrato de entrada/salida:
Política de error:
Ownership:
Riesgo:
Aplicación posible en Golden:
```

## Matriz comparativa final

`repo_analisis/MATRIZ_COMPARATIVA.md` debe comparar al menos:

- media fuera del runtime de control;
- unidad de afinidad;
- paralelismo entre núcleos;
- workers por proceso/tarea;
- audio a través de IPC;
- orden por sesión;
- protección frente a mensajes obsoletos;
- backpressure;
- barge-in y purga;
- supervisión y reinicio;
- drenaje;
- dominio de fallo;
- memoria duplicada;
- observabilidad;
- complejidad de integración;
- impacto sobre Asterisk y ai-engine;
- licencia;
- valor para Golden: adoptar, adaptar, estudiar o descartar.

## Propuesta aplicable a GoldenBridge

En `repo_analisis/PROPUESTA_APLICABLE_GOLDEN.md` produce un diseño derivado de evidencia, no una combinación superficial de proyectos.

Debe incluir:

- frontera definitiva Media–Conversación;
- interior de MediaWorker y modelo de concurrencia;
- mecanismo de distribución previo al flujo de audio;
- afinidad de llamada;
- número y rol de loops/procesos/threads;
- ubicación de VAD, SmartTurn, STT, TTS y pacer;
- protocolo de eventos y comandos;
- estado por sesión y generación;
- backpressure;
- secuencia completa de barge-in;
- comportamiento ante caída de cada componente;
- compatibilidad con Python 3.14t, ONNX Runtime y los límites de threads ya fijados;
- empaquetado dentro de una tarea Modal y relación con el autoescalado;
- estrategia para que el endpoint acepte y entregue directamente cada WebSocket al MediaWorker dueño, sin relay Python por trama;
- qué parte puede construirse en Python y cuál necesitaría runtime nativo o proceso separado;
- plan de PoC mínimo con criterios falsables.

Distingue obligatoriamente:

```text
K_loop   = capacidad de un loop Media
K_worker = capacidad de un proceso Media
K_task   = suma efectiva de workers limitada por CPU, memoria y desbalance
K_flota  = capacidad agregada de tareas Modal
```

No presentes `N × K_loop` como aumento de K por loop. Explica si una propuesta mejora eficiencia individual o solamente paraleliza mediante más workers.

## Dictamen adoptar/adaptar/construir

En `repo_analisis/DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md` entrega tres decisiones separadas:

1. componentes o bibliotecas que podrían adoptarse;
2. patrones que conviene adaptar sin incorporar el proyecto completo;
3. piezas que Golden debe construir porque ningún repo satisface el contrato.

Para cada decisión incluye:

- beneficio esperado;
- dependencia introducida;
- impacto de migración;
- riesgo técnico;
- riesgo de licencia;
- experimento mínimo;
- criterio objetivo para aceptar o rechazar.

## Criterios de calidad y detención

La auditoría no está terminada si sólo resume README o documentación. Debe citar código y callers.

No propongas reemplazar todo el stack sólo porque un proyecto contiene más funciones. Cualquier reemplazo de Asterisk, ai-engine o Modal debe aparecer como alternativa de alto impacto con costes explícitos.

Si un repositorio es demasiado grande, limita el análisis a los segmentos definidos, pero sigue cada segmento hasta sus callers, estado y manejo de errores.

No compiles todos los proyectos por defecto. Ejecuta pruebas existentes únicamente cuando ayuden a confirmar un contrato concreto y no requieran desplegar infraestructura externa.

Actualiza `repo_analisis/README.md` como índice con el estado de cada descarga e informe. Al finalizar, responde en el chat con un resumen ejecutivo breve y las rutas de los documentos creados; todo el análisis detallado debe quedar en los archivos.
