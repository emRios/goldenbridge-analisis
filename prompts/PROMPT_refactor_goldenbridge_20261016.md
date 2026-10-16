# Prompt · refactor de GoldenBridge — contexto y decisiones hasta el 2026-10-16

Copia todo este archivo como primer mensaje de un chat nuevo, abierto en el workspace `refGolden` (repo `github.com/emRios/goldenbridge-analisis`).

---

## Tu papel

Vas a continuar el **refactor de GoldenBridge**: la reescritura del motor en un stack propio. La decisión de reescribir **ya está tomada** y es estratégica. Tu trabajo no es reabrirla: es que la reescritura entregue de verdad **control, previsibilidad, diagnóstico y contención de fallas**, apoyándote en todo lo que ya se analizó y decidió. Abajo está cada decisión con su documento. Léelos antes de proponer nada.

## Cómo trabajo, y cómo quiero que respondas

- **En español**, frases simples; tablas cuando ayuden.
- **Nunca uses una etiqueta sin su descripción.** No escribas "M1", "carril C", "C2" o "escenario A" a secas; di siempre qué son, por ejemplo: "M1, la medición del sistema actual con mensajes de 60 ms".
- **Etiqueta lo que afirmas:** `[MEDIDO]` (salió de una medición, con su ruta) · `[CÓDIGO]` (lo leíste en el código) · `[DOCUMENTACIÓN]` · `[INFERIDO]` · `[ABIERTO]`. No presentes una suposición como hecho. Si supusiste algo, dilo y corrígelo.
- **No argumentes con costo de cómputo ni con días de desarrollo.** No son restricción. Lo intenté tres veces en la sesión anterior y estaba mal.
- **Si una conclusión tuya no me convence dos veces seguidas, pregúntame con qué criterio juzgo** antes de reescribirla.
- **Una variable por experimento, y toda mejora con un número punta a punta.** Precedente: bajar de 12 a 6 trabajadores mejoró una métrica interna un 78 % y empeoró el sistema (−17 % de turnos).
- **Nunca valides algo por el código de retorno:** lee de vuelta el estado efectivo.
- Cuando me des comandos: **dónde se ejecuta cada uno** (WSL en la laptop o `.143`), **una línea por bloque**, y **qué debe salir**.
- El contexto compartido es el repo, no tu memoria. Toda decisión o hallazgo se escribe en `CLAUDE.md` (ESTADO ACTUAL) o en `doctos/`.

---

## 1. Qué es GoldenBridge y dónde está el cuello

GoldenBridge (GB) es el motor de voz con IA para campañas salientes de banca. Hoy es Python 3.14 sin GIL, con uvloop. Arquitectura autoritativa: `context/ARQUITECTURA_STACK.md`.

```
Marcador → Asterisk (proveedor) ══ túnel ══ .143
  ai-engine (puente de telefonía) — AudioSocket slin16 20 ms ⇄ µ-law 160 B
     └─ WebSocket /v1/agent/converse → GoldenBridge
GoldenBridge — por trama: µ-law→pcm16 → soxr → Silero (detector de voz) → smart-turn (fin de turno, ONNX)
             — orquesta STT (Steno), LLM y TTS (Ursula), que son REMOTOS (Gotenks)
```

Destino del despliegue: GB en **Modal** (contenedores con autoescalado, endpoint único). Asterisk, el túnel y el ai-engine se quedan en `.143`.

**Hechos medidos** (`CLAUDE.md`, "Hechos establecidos"; 6 campañas, 28.803 llamadas):

| hecho | valor |
|---|---|
| rodilla (donde empieza a degradarse) | **16-25 sesiones** por proceso (a 20 ms, en `.143`) |
| techo (donde se cae) | 51 (Python 3.14t); entre la rodilla y el techo las llamadas se completan **degradadas** |
| curva | de 1-5 a 41+ sesiones el trabajo crece 2,9× y el ciclo 35× (`cycle_p90` 3,5 → 122 ms) |
| CPU por llamada | ~0,022-0,023 núcleos; **100 llamadas ≈ 2,3 núcleos**. **El CPU no es la restricción** |
| el cuello | **la coordinación en un único event loop** que toca cada trama de todas las llamadas (submit al pool + resume por mensaje); la orquestación de STT/LLM/TTS no cuesta nada (`steno_p90` 0,10 ms plano) |
| Silero | 401 µs por fragmento × 31,25/s por llamada (72 % del trabajo) |
| smart-turn | ~0,07 inferencias/s por llamada |
| durante TTS | GB no agrupa tramas: cada trama es un viaje al pool (50/s, no ~11/s) `[CÓDIGO]` |
| latencia real | primer byte de audio p50 558 ms, p90 1.330 ms |

`[DATO DEL USUARIO]` En producción, K real por proceso es **40-50, a veces 38-45**, y "depende mucho de los ciclos de conversación". Hicieron falta dos escaladas de hardware y pasar de Python 3.11 a 3.14t para "casi" llegar a 50. Esos 40-50 caen en la banda degradada de la curva medida. Explicación plausible de la variación `[INFERIDO]`: sin agrupar durante el TTS, la carga depende de qué fracción del tiempo habla el bot.

---

## 2. Decisiones tomadas, en orden

| fecha | decisión | documento |
|---|---|---|
| 25-sep | **Auditoría del `websocket_server.py`** (rama `api@08932a6`): hallazgos H1-H8, 101 frente a 123 métodos; refactor de piezas sin cambiar comportamiento | `doctos/AUDITORIA_REFACTOR_websocket_server_api_08932a6.md` |
| 28-sep | **Varios procesos GB por contenedor de Modal = empaquetado de réplicas**, no mejora del cuello. Lo decisivo es medir K de un contenedor | `doctos/DICTAMEN_MULTIPROCESO_POR_TAREA_MODAL_20260928.md` |
| 28-sep | **Frontera Media–Conversación aceptada con tres ajustes.** Media es dueño del audio (WS con el engine, DSP, Silero, smart-turn, barge-in, STT, TTS y pacer); Conversación, del historial, campaña, LLM, funciones y terminales. **Entre ambos, cero audio.** Contrato con clave `(session_id, session_epoch, turn_id, speech_id, seq)`. Fases R0-R6 con trazas doradas | `doctos/DICTAMEN_FRONTERA_MEDIA_CONVERSACION_20260928.md` |
| 9-oct | **Auditoría de 8 motores de media.** Sólo FreeSWITCH, LiveKit server y LiveKit SIP paralelizan, y lo hacen con un **dueño por llamada** que ejecuta la cadena por trama de forma síncrona. Los de Python (Pipecat, LiveKit Agents, TEN) sólo replican. **Ninguno corre inferencia de IA por trama dentro del dueño**: la propuesta es una hipótesis a falsar, no una copia. Adoptar: nada. Adaptar: 11 patrones (A1-A11). Construir: el motor, el contrato y la orden de flush. Siete correcciones al contrato (C1-C7) | `repo_analisis/` (`PROPUESTA_APLICABLE_GOLDEN.md`, `DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md`, `MATRIZ_COMPARATIVA.md` §3) |
| 9-oct | **Reescribir el motor de media en nativo, C++20 (preferido sobre C).** Tres escenarios de hilos: **A**, un reactor (hilo) por llamada; **S**, W hilos-shard al estilo mediasoup (S1, un solo shard, es el control causal); **B**, un hilo de entrada y uno de salida por llamada, condicionado. Componentes **sans-I/O e independientes de la estructura de hilos**. Precedentes en C para códec y resampleo: `g711.h` de FreeSWITCH (dominio público) y libsoxr | conversación del 9-oct; `prompts/PROMPT_codex_mediaworker_cpp_escenarios_20261009.md` |
| 9-oct | **Dictamen de Codex y su contraste.** Acepta C++20 condicionado y A primero. Nueve correcciones verificadas (abajo). Orden: medir el monolito a 60 ms → una variante Python con loop por llamada → A → S1 → (B). **Go** queda abierto como lenguaje (LiveKit SIP es el precedente con la forma exacta) | `doctos/DICTAMEN_CODEX_MEDIAWORKER_CPP_20261009.md`, `doctos/CONTRASTE_CODEX_MEDIAWORKER_CPP_20261009.md` |
| 9-oct | **Estrategia aprobada**, en dos carriles: medir (M) y construir por componentes (C). Tabla C0-C8, reglas del core, laboratorio y criterios preregistrados | `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md` |
| 10-oct | **El guardián de arranque corregido y aceptado** (M0): exige 0 pruebas saltadas, el conteo exacto (12) y cumple el contrato de entrypoint de Modal | `experimentos/2026-10-09_m0-gate/`, `parches/2026-10-09_goldenbridge_gate-arranque/` |
| 12-oct | `[DATO DEL USUARIO]` **Si se refactoriza, se mueve todo, incluida la Conversación.** No es "motor de media en C++ + Conversación en Python", que yo había supuesto. **Existe un ai-engine en C#** que "ha demostrado responder bien", pensado como un engine por ~500 llamadas (4 engines para 2.000). No está en el workspace | conversación del 12-oct |
| 13-oct | **Decisión del dueño: GoldenBridge se reescribe en el stack propio (C++ o C#). Es estratégica.** Criterios: control de punta a punta; previsibilidad de K ("si necesito 500 o 1.000 más, es aritmética"); diagnóstico por llamada (hoy hay que leer logs y escuchar la llamada); contención de fallas. Costo y días **no son restricción**. **M2, la prueba de concepto en Python con un loop por llamada, se cancela.** M1 sigue, como línea base y banco de pruebas. El carril de construcción sigue, con el propósito nuevo | `doctos/DICTAMEN_ESCALAR_SIN_REFACTOR_2000_20261013.md` (encabezado y §4.4) |
| 14-oct | **El modelo de fin de turno pasa a fp32** (`smart-turn-v3.2-gpu.onnx`): decide igual en cualquier CPU, así que el requisito de `avx512_vnni` desaparece | `experimentos/2026-10-14_smartturn-fp32/README.md` |

### Las nueve correcciones de Codex (verificadas), que el diseño nativo debe respetar

1. `ORT_NO_EXCEPTIONS` **aborta el proceso**: errores de ORT por `OrtStatus` o capturados en el borde del hilo.
2. `InferenceSession::Run` de ORT **es seguro entre hilos**. El lock de smart-turn sólo cubre `run`, y su comentario "not thread-safe" está equivocado. Verificar en laboratorio y retirar la serialización.
3. El `websocket::stream` de Beast **no es seguro entre hilos**. B necesita un dueño del stream y una cola SPSC: al menos un traspaso.
4. "Síncrono" aplica al **DSP y a la decisión, no a la red**. A es un **reactor asíncrono por llamada** con el DSP en línea, porque Steno reconecta en línea y su Finalize espera hasta 3 s.
5. Paridad: bit a bit sólo para µ-law y soxr. Las features de smart-turn, con tolerancia `1e-3` (objetivo diagnóstico `1e-5`) y decisiones exactas; Silero, `1e-3` por fragmento y transiciones idénticas.
6. `seq` y carril prioritario chocan: **asignar `seq` al serializar**, en orden de envío y después de decidir la prioridad. Lo purgado antes no consume `seq`.
7. `discard_pending` de Steno supone un receive-loop bloqueado durante Finalize. En un reactor esa premisa cae: hace falta **una barrera de turno STT**.
8. `played_ms` no se mide: el pacer estima por reloj de pared. Lo único medido es **`emitted_ms`**.
9. IXWebSocket crea un hilo por objeto: **Beast/Asio con un ejecutor por dueño**.

### Reglas del core, no negociables (`mediaworker/README.md`, estrategia §4)

Ningún estado global ni singletons; ningún hilo, lock ni E/S dentro de un componente; reloj inyectado; cero asignaciones por trama después del calentamiento, verificado; resultados como datos, no callbacks; inferencia sólo por handle; sin excepciones en el camino por trama.

### Componentes de la estrategia (el carril de construcción)

- **C0** · códec µ-law + resampleo soxr.
- **C1** · Silero + máquina de estados del detector de voz.
- **C2** · log-mel + smart-turn + cuándo inferir.
- **C3** · gates de barge-in, `turn_audio`, pre-contexto, watchdog de cierre.
- **C4** · dueño por llamada (A, S1; B condicionado) + simuladores de Steno y Ursula.
- **C5** · cliente Steno. **C6** · Ursula + pacer + presupuesto. **C7** · servidor WS hacia el ai-engine. **C8** · canal hacia Conversación.

Cada uno tiene oráculo de paridad, microbenchmark y criterio de "listo" en la estrategia §4. Toolchain previsto: C++20, Beast/Asio, ORT 1.24.4 nativo con hash verificado, libsoxr de la misma versión que el wheel, compilado en la construcción de la imagen, ASan/UBSan en todos los escenarios y TSan en B.

### Criterios preregistrados del laboratorio (estrategia §7)

| criterio | aceptación |
|---|---|
| corrida válida | generador previsto→real p99 ≤ 5 ms; cero muestras perdidas |
| a N = 100 | VAD desde mensaje completo p90 ≤ 5 ms, p99 ≤ 20 ms |
| corte local | gates → último write viejo p99 ≤ 30 ms |
| pacer | lag de plazo p99 ≤ 20 ms |
| backlog | residencia de audio p99 ≤ 120 ms, sin pendiente positiva |
| smart-turn | disparo → commit p99 ≤ 250 ms |
| punta a punta | cero turnos perdidos; primer audio y cierre no empeoran más de 10 % o 20 ms |

K_worker = el mayor N que cumple todos los criterios en tres repeticiones. Nunca presentar `N × K_loop` como más K por loop.

### Los seis requisitos de control para la reescritura (dictamen del 13-oct)

1. **Costo por llamada medido y estable**: µs por trama y bytes por llamada, independientes de la mezcla de conversación.
2. **K es una cuenta**: `K = núcleos / costo por llamada`, con margen, verificado en laboratorio antes de producción.
3. **Un identificador de llamada de punta a punta** (marcador → Asterisk → engine → media → proveedores → grabación) y **una línea de tiempo por llamada**: "¿qué pasó con la llamada X?" se responde con una traza, no leyendo logs ni escuchando el audio.
4. **Contención con tamaño conocido**: proceso por N llamadas con supervisor; el engine detecta la caída y actúa en tiempo acotado. **Nunca silencio.**
5. **El entorno se verifica al arrancar**, no se supone (el guardián de arranque es el patrón).
6. **Un artefacto, una rama, una configuración**, iguales en laboratorio y producción.

Hechos operativos del mismo dictamen (§4.4), que el diseño debe resolver `[CÓDIGO]`:
- GB no le devuelve su `session_id` al engine;
- GB no sabe en qué contenedor corre;
- cuando un contenedor muere, el engine Python escribe una advertencia sin `call_id`, manda un `AgentAudioDone` falso y deja la llamada en silencio;
- el engine Python (`d059295`) no reconecta, no tiene flush de reproducción y descarta los JSON de GB.

`[ABIERTO]` si el engine C# resuelve esos tres puntos.

---

## 3. Cómo se reconcilian las decisiones (lo que quedó superado)

| decisión anterior | estado hoy |
|---|---|
| motor de media en Python 3.14t con un hilo por llamada (B1 de la auditoría de motores) | **superado**: la reescritura es nativa, en el stack propio |
| M2, la prueba de concepto en Python con un loop por llamada | **cancelada** el 13-oct |
| criterio "beneficio" de la estrategia: si el sistema actual cumple a 100 llamadas, no se reescribe | **ya no decide si se reescribe** (la decisión es estratégica); los criterios de §7 siguen siendo los que **aceptan el motor nuevo** frente a la línea base |
| Conversación en Python, detrás de la frontera; C5-C8 "sólo tras el laboratorio" | **a replanear**: también se reescribe. Si queda en el mismo binario o en otro proceso, `[ABIERTO]`. La frontera Media–Conversación y su contrato (C1-C7 + correcciones de Codex) siguen valiendo como límite de módulos |
| "la flota exige `avx512_vnni`"; el guardián exige VNNI | **superado** con el fp32: no hace falta fijar CPUs |
| oráculo de paridad del fin de turno: `stack.json` con 14 decisiones del int8 al umbral 0,6 | **ahora es la referencia del fp32**: 15 casos, umbral 0,35 (`f34600e` en la rama local `fix/smartturn-fp32`). El modelo de referencia es `smart-turn-v3.2-gpu.onnx`, sha256 `ab8dc64b…39be` |
| lenguaje: C++20 | **`[ABIERTO]` C++ o C#** desde el 13-oct; Go sigue mencionado como opción en el contraste del 9-oct |

---

## 4. El estado real hoy

- **Del motor nuevo no hay nada construido.** `mediaworker/` sólo tiene la estructura (`core/`, `lab/`, `parity/`) y el README con las reglas.
- **M1, la medición del sistema actual con mensajes de 60 ms** (escalera de 10 a 100 llamadas en `.143`), está lista en la rama `paso/m1-linea-base` y **no se ha corrido**. Su cliente de carga mide `ttfab`, `corte` y `gen_lag` del lado del cliente: son las métricas que sirven para comparar con el motor nuevo, porque no dependen de cómo esté hecho por dentro. Antes de correrla hay que ajustar su `correr.sh` a la imagen fp32 (`GB_IMAGEN` y el `model_path` que fuerza a `cpu.onnx`); usa el puerto 8776.
- **Imágenes de GB:** `goldenbridge:314t` (original, int8) y `goldenbridge:314t-fp32` (en `.143`). Para construir imágenes hay un prompt aparte: `prompts/PROMPT_imagen_docker_goldenbridge_20261016.md`.
- **Ramas de GoldenBridge** (`github.com/MrBotGT/GoldenBridge`):
  - `api` (`08932a6`): la auditada;
  - `feat/f261-call-summary` (`fb8a3b9`): la que corre en producción en `.143`;
  - `feat/modal-migration` (`9c024fd`): Docker;
  - `release/remarcacion-v1` (`3ec0732`): `api` + ventana tardía del detector de buzón + F261 + remarcación.

  El comportamiento que el motor nuevo debe reproducir es el de **la rama que esté en producción** cuando se haga la paridad. `[ABIERTO]` cuál: hoy F261; pronto quizá remarcación.

---

## 5. Lo que queda abierto, en orden

### 5.1 · Primero: C++ o C#

Es la primera decisión, porque define todo lo demás. Compáralos con **mis cuatro criterios**: control, previsibilidad, diagnóstico y contención. Hechos a considerar, todos por verificar:
- **ONNX Runtime** tiene API C++ (wrapper RAII sobre la C) y API oficial de **C#**.
- **soxr** es una biblioteca C: directa en C++; en C# vía P/Invoke.
- El **recolector de basura de .NET** en una ruta de audio de 20-60 ms: cero asignaciones en la ruta de audio y pausas medidas desde el diseño.
- **Red:** Beast/Asio en C++; sockets, `System.IO.Pipelines` y Kestrel en .NET.
- **El engine ya está en C#.** Un solo stack para engine y GB tiene peso en "control de punta a punta". Pide acceso a su repo y con qué carga se probó.
- **Codex aceptó C++20 sobre C con condiciones**; C# nunca se evaluó con el mismo rigor. Go sigue mencionado en el contraste del 9-oct.

Entrega: una tabla por criterio, con evidencia etiquetada, una recomendación, y las mediciones que la confirmarían.

### 5.2 · Replanear la Conversación nativa

Las fases C5 a C8 de la estrategia: Steno, Ursula + pacer, servidor WS y canal a Conversación. Mismo binario o proceso aparte; cómo se conservan el contrato (C1-C7 + correcciones de Codex), las trazas doradas (R0) y la paridad de comportamiento de campañas, funciones e historial.

### 5.3 · Correr la línea base

M1, la medición del sistema actual con mensajes de 60 ms, con la imagen fp32. Es contra lo que se demuestra la mejora.

### 5.4 · Resto, todo `[ABIERTO]`

- versión de libsoxr que embebe python-soxr 1.1.0;
- RSS de los modelos;
- archivo de despliegue real de Modal: `max_inputs`, `cpu`, `memory`, entrypoint, hilos;
- vida máxima de un WebSocket en Modal;
- redespliegue con llamadas vivas;
- si el engine C# reconecta, hace flush y comparte un identificador de llamada con GB.

---

## Mapa de lectura, en este orden

1. `CLAUDE.md`: el ESTADO ACTUAL más reciente está en la rama `paso/smartturn-fp32`, **no integrada a `main`**.
2. `context/ARQUITECTURA_STACK.md`
3. `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md`
4. `doctos/CONTRASTE_CODEX_MEDIAWORKER_CPP_20261009.md` y, si hace falta detalle, `doctos/DICTAMEN_CODEX_MEDIAWORKER_CPP_20261009.md`
5. `repo_analisis/PROPUESTA_APLICABLE_GOLDEN.md`, `DICTAMEN_ADOPTAR_ADAPTAR_CONSTRUIR.md` y `MATRIZ_COMPARATIVA.md`
6. `doctos/DICTAMEN_FRONTERA_MEDIA_CONVERSACION_20260928.md`
7. `doctos/DICTAMEN_ESCALAR_SIN_REFACTOR_2000_20261013.md`: la decisión del dueño, los requisitos de control y §4.4
8. `experimentos/2026-10-14_smartturn-fp32/README.md`
9. `doctos/AUDITORIA_REFACTOR_websocket_server_api_08932a6.md`, para el comportamiento actual pieza por pieza

## Primer paso

Lee los documentos del mapa. Después dime en pocas líneas qué entendiste de la decisión y de lo abierto, y propónme cómo vas a hacer la comparación C++ contra C#. Antes de hacerla, pregúntame si tengo acceso al repo del engine en C#.
