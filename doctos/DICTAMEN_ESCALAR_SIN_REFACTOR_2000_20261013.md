# Dictamen · escalar a 2.000 llamadas dejando GoldenBridge como está

**Fecha:** 2026-10-13 · **Pregunta:** GoldenBridge soporta hoy 40-50 llamadas por proceso; la meta es 2.000 concurrentes. ¿Qué implica, en costo, administración y riesgo, **no refactorizar** y escalar con N contenedores en Modal?

**Decisión: no refactorizar. Sin condiciones.** Para llegar a 2.000 no hace falta, en ningún escenario: si M1 da 25 llamadas sanas por contenedor son 80 contenedores; si da 50, son 40. Las dos cifras se alcanzan hoy y cuestan < $6/h. **M1 dimensiona la flota, no decide si se escala ni si se refactoriza.** El refactor tampoco se justifica después: daría 2-5 veces menos contenedores (ahorro < $1,50/h) y menos eventos de incidente, no menos llamadas afectadas (§4.4); eso no paga 50-83 días-persona ni un equipo de C++, y ese beneficio no cambia con el tiempo. Si algún día se hace, es una decisión de producto (menos piezas en operación), no una respuesta a una falla. Las versiones anteriores de este párrafo ponían "disparadores" para reabrirlo; eran una cobertura y se retiran.

**Consecuencia sobre la estrategia aprobada (`ESTRATEGIA_MEDIAWORKER_20261009.md`):** el carril C (C0-C4, MediaWorker en C++) se concibió para subir K por proceso, y K por proceso ya no limita llegar a 2.000. **Se recomienda pararlo, junto con M2.** Queda M1, para dimensionar y validar 60 ms. Decisión pendiente del dueño.

**Lo que sí se hace:** parches (GB devuelve `session_id` y contenedor; el engine nunca deja una llamada en silencio; tres límites de hilos en Modal) → configuración de Modal (`max_inputs`/`target_inputs`/`max_sessions`, precalentado, pruebas de redespliegue y vida del WS) → tablero y alarma de degradación → subida escalonada 200 → 500 → 1.000 → 2.000. Sobreaprovisionar a 20-25 por contenedor hasta que M1 dé la cifra a 60 ms: es margen, no espera.

---

## 0. Datos de entrada

| dato | valor | etiqueta |
|---|---|---|
| K observado por proceso | 40-50, "a veces 38-45 según los ciclos de conversación" | `[DATO DEL USUARIO]` 2026-10-12 |
| rodilla medida (20 ms, `.143`, co-tenencia) | 16-25 sesiones; techo 51 | `[MEDIDO]` CLAUDE.md |
| curva de degradación | `cycle_p90` 18 ms (16-25) → 29,8 (26-40) → **122 ms (41+)**; `resume_vad_p90` 3,8 → 8,7 → **50,7 ms** | `[MEDIDO]` |
| CPU por llamada | 0,022-0,023 núcleos (vCPU) | `[MEDIDO]` E1 y regresión |
| spin fijo por proceso | 1,57 vCPU (OpenBLAS/OpenMP) cuando los hilos no están limitados | `[MEDIDO]` E1 |
| duración media de llamada | 1.098.000 s-sesión / 28.803 llamadas = **38 s** | `[MEDIDO]` derivado |
| arranque en frío en Modal | 26-33 s; la app escala a cero | `[MEDIDO]` |
| `max_sessions` desplegado | 120 | `[MEDIDO]` `/health` |
| refactor | sería **todo en C++**, incluida la Conversación | `[DATO DEL USUARIO]` 2026-10-12 |
| ai-engine | existe una versión en **C#** "que ha demostrado responder bien"; un engine por ~500 llamadas | `[DATO DEL USUARIO]`, no está en el workspace, sin medición en el repo |
| hilos en golden-modal | `OMP_NUM_THREADS` y `GB_ORT_INTRA_OP` "desactivados"; la imagen entregada los fija a 1 | `[DATO DEL USUARIO]` · `[CÓDIGO]` config de la imagen |
| esfuerzo del refactor | 50-83 días-persona, **con la Conversación en Python**; todo en C++ no está estimado | `[INFERIDO]` estrategia §8 |

Tarifas de Modal `[DOCUMENTACIÓN, leídas el 2026-10-13]`:

| concepto | valor |
|---|---|
| CPU | $0,0000131 por **núcleo físico** y segundo = **$0,0472/núcleo-hora**; 1 núcleo físico = 2 vCPU |
| memoria | $0,00000222 por GiB y segundo = **$0,0080/GiB-hora** |
| cobro | por segundo; **el mayor entre lo pedido y lo usado**; mínimo 0,125 núcleos y 128 MiB por contenedor |
| límite blando de CPU | pedido + 16 núcleos; por encima se frena, no se mata |
| región fija | ×1,15 (amplia) · ×1,75 (estrecha) |
| egreso de red | **$0,04/GiB desde el 2026-10-01**, con 1 TiB (Starter) o 10 TiB (Team) incluidos al mes |
| planes | Starter $0 + $30 de crédito · Team $250/mes + $100 de crédito |
| límite | 4.000 contenedores por función |
| endpoints web | HTTP máximo 150 s; **WebSocket sin documentar** `[ABIERTO]` |
| apagado | `@modal.exit` con 30 s de gracia en preempción; comportamiento al redesplegar `[ABIERTO]` |

Unidad: los "núcleos" medidos en `.143` son vCPU `[INFERIDO: top/psutil cuentan lógicos]`. Para Modal se dividen entre 2.

---

## 1. Qué significa "dejar como está"

No es "no tocar nada". Es esta topología:

```
marcador → Asterisk → túnel → engines C# (uno por ~500 llamadas, en .143 o similares)
                                   │  WS público, 480 B / 60 ms por llamada
                                   ▼
                      Modal · endpoint único golden-bridge
                      N contenedores de goldenbridge:314t, max_inputs = K
                                   │
                                   ▼
                      Gotenks (STT · LLM · TTS) · Ursula
```

Con el endpoint único, el engine **no elige contenedor**: Modal reparte. "Un engine por flota de 5 contenedores" es una cuenta de capacidad, no una conexión fija. Si se quisiera aislar fallas por flota habría que desplegar una app de Modal por engine; eso cambia el "endpoint único" de la arquitectura y es decisión del dueño, no de este dictamen.

---

## 2. Dimensionamiento

`contenedores = 2.000 / K`, con K = llamadas por contenedor **sin degradación**, no "sin caerse".

| K | contenedores | de dónde sale |
|---|---|---|
| 20 | 100 | rodilla medida a 20 ms; calidad intacta `[MEDIDO]` |
| 40 | 50 | K observado hoy; **banda 41+**: `cycle_p90` 122 ms, VAD 50 ms tarde `[MEDIDO]` |
| 50 | 40 | techo 3.14t; las llamadas se completan degradadas |
| 100 | 20 | objetivo del refactor; hipótesis a falsar |

Dos cosas que el "40-50" no dice:

1. **40-50 es la zona degradada de la curva medida.** Las llamadas no se caen, pero la decisión del VAD llega ~50 ms tarde y el ciclo ~120 ms. Eso es el bot cortando tarde y respondiendo tarde. Si el criterio es calidad, K es 20-25 con la medición actual. `[ABIERTO]` a 60 ms, que es lo que M1 mide.
2. **K varía con la mezcla de conversación** porque, mientras el bot habla, GB no agrupa tramas: cada trama es un viaje al pool `[CÓDIGO]`. Una campaña de respuestas largas carga más que una de respuestas cortas con las mismas llamadas. Operativamente obliga a fijar `max_inputs` por el **peor** ciclo y desperdiciar 20-25 % en las campañas suaves, o a tener un `max_inputs` por tipo de campaña.

Lo que sorprende y conviene asumir desde ya: **a 38 s de duración media, 2.000 llamadas concurrentes son ~53 llamadas nuevas por segundo**, y el arranque en frío de un contenedor (26-33 s) dura casi lo mismo que una llamada. Sin precalentar, una campaña que arranca cae sobre contenedores que todavía no existen.

---

## 3. Costos

### 3.1 La factura de GoldenBridge en Modal, por hora, a 2.000 llamadas

Descomposición: `útil + reserva + fijo por contenedor × contenedores`.

- **Útil:** 2.000 × 0,023 vCPU = 46 vCPU = 23 núcleos → **$1,09/h**. No depende de K.
- **Reserva:** si se pide CPU de sobra para no caer al precipicio (`cpu=`), Modal cobra lo pedido. Si se dimensiona proporcional a las llamadas (≈ 0,1 núcleo por llamada), vale lo mismo con K = 20 que con K = 100: ~200 núcleos → **$9,44/h**. Si se pide por contenedor sin mirar K, se paga de más.
- **Fijo por contenedor:** memoria (`[PENDIENTE]`; supuesto 1,5 GiB → $0,012/h) + spin de hilos si no están limitados (1,57 vCPU = 0,79 núcleos → $0,037/h). **≤ $0,05/h por contenedor.**

| K | contenedores | fijo/h con hilos = 1 | fijo/h con hilos sueltos | total/h sin reserva (hilos sueltos) |
|---|---|---|---|---|
| 20 | 100 | $1,20 | $4,90 | $5,99 |
| 40 | 50 | $0,60 | $2,45 | $3,54 |
| 50 | 40 | $0,48 | $1,96 | $3,05 |
| 100 | 20 | $0,24 | $0,98 | $2,07 |

Región fija: ×1,15 o ×1,75 sobre todo lo anterior.

**Lectura:** la diferencia entre K = 40 y K = 100 es **$0,36/h** con los hilos limitados y **$1,47/h** con los hilos sueltos. Y la diferencia entre operar sano (K = 20) y operar en el precipicio (K = 40) es **$0,60-2,45/h**: la calidad se compra con contenedores, barata.

### 3.2 Al mes `[SUPUESTO: 176 h a plena carga = 8 h/día × 22 días]`

| | K = 40 | K = 100 | diferencia |
|---|---|---|---|
| hilos = 1, sin reserva | $297 | $234 | **$63/mes** |
| hilos sueltos, sin reserva | $623 | $364 | **$259/mes** |
| con reserva proporcional | +$1.660 en ambos | | $0 |

Es un techo: ninguna campaña está a 2.000 las 8 horas.

### 3.3 Costos iguales en los dos caminos

Están decididos por la arquitectura (GB en Modal), no por el refactor, y **son mayores que el cómputo de GB**:

| concepto | por hora a 2.000 | al mes (176 h) | etiqueta |
|---|---|---|---|
| egreso AWS `.143` → Modal (audio de subida, 2.000 × 8 kB/s = 57,6 GB/h × $0,09) | **$5,18** | **$912** | `[DOCUMENTACIÓN]` tarifa AWS a internet |
| egreso Modal → engine (TTS, ~50 % del tiempo: ~27 GiB/h × $0,04) | $1,07 | $188; incluido en los 10 TiB de Team, no en el 1 TiB de Starter | `[DOCUMENTACIÓN]` |
| 4 engines (máquinas tipo `.143`, ~$0,7/h cada una) | $2,80 | $493 | `[INFERIDO]` por tipo de instancia |
| plan Modal Team | | $250 | `[ABIERTO]` si hace falta |
| STT · LLM · TTS por turno | el mayor costo del sistema, por mucho | | independiente de esta decisión |

### 3.4 El refactor contra ese ahorro

Estrategia §8: 50-83 días-persona **sin** llevar la Conversación a C++. Ilustrativo, a $300/día: $15.000-25.000. Con el ahorro máximo de cómputo ($259/mes) tarda **5-8 años** en pagarse; con el realista ($63/mes), décadas. **El costo de cómputo no justifica el refactor.** Si se hace, es por calidad u operación, y se decide con los números de M1.

### 3.5 La única palanca de costo que importa hoy

Los hilos "desactivados" en golden-modal. Con `OMP_NUM_THREADS` y `GB_ORT_INTRA_OP` sin fijar, cada contenedor crea un hilo por núcleo del **host** que ve (no de su cuota), y Modal cobra **lo usado** si supera lo pedido. Con 50-100 contenedores girando en vacío, la factura puede multiplicarse por varias veces y además frena los contenedores al pasar el límite blando. Es un cambio de tres variables de entorno, verificable en la página de uso de Modal. `[INFERIDO]`; la medición es el uso de CPU por contenedor antes y después.

---

## 4. Administración

### 4.1 Lo que hay que operar en cualquiera de los dos caminos

| tarea | hoy |
|---|---|
| **Precalentar por campaña** (`min_containers` / `buffer_containers` antes de que el marcador abra) | no existe; la app escala a cero `[MEDIDO]` |
| **Redesplegar sin cortar llamadas** (drenar) | `[ABIERTO]` qué hace Modal con un WS vivo al desplegar; 30 s de gracia en preempción |
| **Alinear `max_inputs`, `target_inputs` y `max_sessions`** | `max_sessions` 120 desplegado, por encima de todo K medido |
| **Rastrear una llamada de punta a punta** (engine → contenedor → CPU → versión) | el engine Python descarta los JSON de GB `[CÓDIGO d059295]`; sin identificador común |
| **Logs y métricas** de 20-100 contenedores efímeros | Logfire existe; `modal app logs` exige token de cuenta |
| **Reconexión y flush** en el engine | el engine Python no los tiene `[CÓDIGO]`; `[ABIERTO]` si el C# los tiene |
| **Hardware heterogéneo**: fijar CPU + gate | parche M0 listo; fijación en manos del usuario |
| **Secretos**: proxy-auth por engine, rotación | hoy un juego de prueba |
| **Tres ramas de GB** (`api`, `f261`, `modal-migration`) | sin resolver; la imagen no trae F261 |

Automatizar esta tabla se paga igual con o sin refactor. Lo que **no** es igual es operar 20 o 100 contenedores una vez automatizada: ver §4.4, que corrige lo que la primera versión de este dictamen decía aquí.

### 4.2 Lo que agrega dejarlo como está

- **Operar junto a un precipicio silencioso.** La degradación no da error ni log: el bot responde y corta tarde. Exige `max_inputs` con rigor, revisarlo en cada cambio de config o de mezcla de campaña, y una **alarma de degradación por contenedor** (p. ej. `resume_vad_p90` > 10 ms, con CYCLE-PROBE prendido) distinta de la alarma de caídas.
- **K por tipo de campaña**, o dimensionar por el peor ciclo.
- **Más arranques en frío** que cubrir en cada campaña (50-100 contenedores × 26-33 s, en paralelo; con rechazos del gate, más).
- **Una imagen que no coincide con lo desplegado** (hilos, entrypoint, posiblemente la versión del código). Hay que cerrar esa brecha o lo que se mide en `.143` no describe Modal.

### 4.3 Lo que agrega el refactor (todo en C++)

- **Reescribir también la Conversación**: máquina de estados de campaña, prompts, funciones, historial, F261. `[MEDIDO]` `steno_p90` = 0,10 ms plano: esa parte no limita K; llevarla a C++ no sube la capacidad, y es la parte que más cambia por negocio.
- **Equipo y guardia en C++**; manuales y depuración desde cero; un fallo de memoria se lleva todas las llamadas del proceso, sin traza legible, y `[ABIERTO]` si Modal entrega volcados.
- **Migración con dos versiones en paralelo** durante semanas o meses, comparadas con el mismo banco (M1).
- **Lo que gana:** 2,5-5 veces menos contenedores, degradación gradual en vez de precipicio (si el diseño lo logra: es hipótesis), K estable e independiente de la mezcla de conversación.

### 4.4 Corrección (2026-10-13): administrar 100 contenedores no es administrar 20

La primera versión decía que "cuesta casi lo mismo porque Modal los administra". Es falso en lo que importa. Modal reparte, arranca y apaga; no rastrea una llamada ni explica por qué murió un contenedor. Y hay una aritmética simple: si **p** es la probabilidad de que un contenedor falle en una hora, hay **N × p incidentes por hora** y cada uno corta **K llamadas**. El total de llamadas cortadas, N × p × K = 2.000 × p, **no depende de K**; lo que depende de K es cuántos eventos tiene que mirar alguien. Con K = 20 hay cinco veces más incidentes, cinco veces más chicos, que con K = 100. Y con N también crecen los arranques en frío por campaña, los rechazos del gate, las oleadas de despliegue, el volumen de logs y los contenedores que hay que inspeccionar en un incidente.

**Cómo se rastrea una llamada hoy** `[CÓDIGO]` (engine `d059295`, GB `api@08932a6`):

| eslabón | identificador | qué queda registrado |
|---|---|---|
| marcador → Asterisk | el PAYLOAD de canal que el marcador decida mandar | `[ABIERTO]` si incluye `call_uuid` |
| Asterisk → engine | `call_id` de Asterisk | todas las líneas del engine llevan `call_id` |
| engine → GB | `Settings.variables` = ese PAYLOAD (`deepgram.py` L176-252) | GB registra `Per-call variables received` con `session_id` **y** `call_uuid` (L3034): **el único punto de unión entre los dos mundos**, y sólo existe si el marcador manda `call_uuid` |
| dentro de GB | `session_id` (uuid4 que GB inventa al conectar, L2132) + `request_id` | cada línea de log y el span `voice_call` de Logfire (`session_id`, `request_id`, `campaign`) |
| GB → engine | **nada** | GB no devuelve `session_id` ni `request_id` (no hay `Welcome`; `SettingsApplied` va sin ids). El engine tiene el receptor listo (L1253-1262) y nunca recibe nada: **el engine no puede decir "mi llamada X fue la sesión Y"** |
| contenedor | **ninguno dentro de GB** | GB no registra hostname ni id de contenedor; `/health` tampoco. El id sólo existe del lado de Modal (`modal container list/logs/exec`, con token de cuenta). Para saber qué contenedor atendió una sesión hay que buscar el uuid en los logs de todos. `[ABIERTO]` si Logfire adjunta el host al span |
| muerte del contenedor vista desde el engine | — | una advertencia `Deepgram Voice Agent connection closed` **sin `call_id`** (L1764), un `AgentAudioDone` sintético, y después nada: no cuelga, no reconecta, sigue mandando audio con un log de nivel *debug* por paquete. `[INFERIDO]` la persona queda en silencio hasta que cuelga |

**Fallas por contenedor**, su radio y cómo se ven hoy:

| falla | fuente | llamadas afectadas | cómo se ve hoy |
|---|---|---|---|
| preempción | `[DOCUMENTACIÓN]` Modal: todas las funciones por defecto, "rara", señal + gracia; el input se reintenta en otro contenedor, lo que para un WS vivo es una llamada cortada; `nonpreemptible=True` cuesta 3× | K | advertencia sin `call_id` en el engine |
| OOM | `[DOCUMENTACIÓN]` límite duro de memoria | K | nada en GB (muere), lo mismo en el engine |
| heartbeat timeout | `[DOCUMENTACIÓN]` proceso principal sin latido durante minutos → Modal mata el contenedor | K | "heartbeat timeout" en los logs de Modal |
| excepción no capturada o fallo en una biblioteca nativa (ORT, soxr, torch) | GB es un proceso | K | traza en el log del contenedor, si la hay |
| arranque fallido o gate en rojo | M0 | 0 cortadas; capacidad que llega 26-33 s tarde más el reintento | logs del contenedor rechazado |
| degradación (precipicio) | `[MEDIDO]` | K degradadas | **nada**: sin error ni log |
| contenedor desbalanceado | Modal reparte por conteo de inputs, no por carga; la mezcla de conversación cambia la carga | K degradadas | nada |
| redespliegue | `[ABIERTO]` | todas las de los contenedores reemplazados | `[ABIERTO]` |
| CPU sin VNNI si falla la fijación | M0 | decisiones distintas, silencioso | el gate lo convierte en rechazo |

**Lo que hace falta para operar N contenedores.** Ninguno es un refactor:

1. GB devuelve `session_id`, `request_id` y la identidad del contenedor en `SettingsApplied` (o en un `Welcome`). El engine ya lo captura y lo registra junto a `call_id`.
2. GB registra al arrancar, en cada línea y en el span de Logfire la identidad del contenedor (`[ABIERTO]` qué variable expone Modal; si ninguna, el hostname).
3. El marcador manda `call_uuid` en el PAYLOAD (verificar).
4. El engine, al perder el WS: una línea de nivel *error* con `call_id`, `session_id` y contenedor, y una política explícita: reconectar como sesión nueva o colgar con mensaje. **Nunca silencio.**
5. Métricas por contenedor exportadas, no sólo logs: `active_sessions`, `cycle_p90`, `resume_vad_p90`, con alarma de degradación.
6. Un tablero: contenedores vivos, llamadas por contenedor, degradados, muertes por causa, rechazos del gate, arranques en frío por campaña.
7. Un procedimiento: "la llamada X sonó mal" → `call_id` → `session_id` → contenedor → sus logs y métricas.

Con esto, operar 100 sigue siendo más trabajo que operar 20: más eventos, más ruido, más contenedores que mirar. Lo que el refactor compra en este frente es dividir N entre 5. Lo que no compra es ninguno de los siete puntos, que hacen falta igual.

---

## 5. Riesgos técnicos del camino "como está" a 2.000, y cómo cerrarlos barato

| riesgo | por qué importa | prueba que lo cierra |
|---|---|---|
| **El engine C#** | es el punto único por flota de 500; sin reconexión ni flush, un WS caído es una llamada muerta | ver su código: reconexión, flush, `call_id` común; medir 500 llamadas con el cliente M1 apuntando al engine |
| **Vida máxima de un WebSocket en Modal** `[ABIERTO]` | el HTTP tiene tope de 150 s; el WS no está documentado; hay llamadas de hasta 1 h `[CÓDIGO]` guardián anti-mudas | una llamada de silencio de 60-90 min contra el endpoint: cuesta centavos |
| **Redespliegue con llamadas vivas** `[ABIERTO]` | corta llamadas si no drena | desplegar una versión con una llamada de prueba abierta y ver qué recibe |
| **Hilos sueltos** | factura y frenado por cuota | fijar las tres variables; leer uso de CPU por contenedor antes y después |
| **Ráfaga de 53 conexiones/s** | el gateway de Modal y el arranque de sesión en GB (STT connect, máquina de estados) nunca se midieron como tasa | rampa agresiva en M1 (`--ramp` corto) y leer `applied` p99 |
| **Precalentamiento** | 26-33 s de frío ≈ una llamada entera | `min_containers`/`buffer_containers` ligados al calendario de campañas; medir `applied` al inicio de campaña |
| **Degradación silenciosa** | calidad sin alarma | alarma sobre CYCLE-PROBE o sobre `ttfab` del lado del engine |
| **`max_sessions` 120** | un contenedor acepta lo que Modal le mande hasta 120 | `max_inputs` = K sano; `max_sessions` ≥ `max_inputs` pero cercano |

Ninguna de estas pruebas pasa de un día de trabajo ni de unos dólares.

---

## 6. Pros y contras

| | dejar GB como está + N contenedores | refactor todo en C++ |
|---|---|---|
| llega a 2.000 | sí, con 40-100 contenedores y 4 engines | sí, con ~20 contenedores |
| costo de cómputo de GB | $2-6/h a plena carga; la diferencia con el refactor es < $1,5/h | $1,3-2/h |
| costo que domina | egreso AWS ($5/h), engines, proveedores: **iguales en ambos** | iguales |
| tiempo hasta tenerlo | semanas de trabajo operativo (§4.1) | §4.1 **más** 50-83+ días-persona y migración |
| calidad por llamada | buena sólo con K ≤ rodilla; fuera de ahí, degradada sin aviso | hipótesis: estable hasta 100; sin medir |
| sensibilidad a la mezcla de conversación | alta; obliga a dimensionar por el peor ciclo | baja, si el diseño cumple |
| fragilidad operativa | precipicio silencioso; exige alarma y rigor en `max_inputs` | fallos de C++ se llevan más llamadas por proceso |
| incidentes por contenedor (§4.4) | N × p por hora, chicos; 5× más eventos que con K = 100 | N/5 × p, grandes; mismas llamadas afectadas en total |
| trazabilidad (§4.4) | hay que construir los 7 puntos | hay que construir los mismos 7 |
| equipo | Python, manuales existentes, CYCLE-PROBE | C++, guardia nueva, manuales nuevos |
| reversibilidad | total | difícil una vez migrado |

---

## 7. Veredicto y condiciones

**Dejar GoldenBridge como está es el camino por defecto para 2.000.** El cómputo no justifica el refactor; la calidad se compra con contenedores a menos de $2,5/h; el riesgo real está en la operación y en el engine, y eso se trabaja igual en los dos caminos.

Condiciones, en orden de costo:

1. **Fijar los tres límites de hilos en Modal** y verificar el uso de CPU por contenedor. Es la única palanca de factura.
2. **`max_inputs` = K sano** (hoy 20-25 a 20 ms; a 60 ms lo da M1), `target_inputs` un 20 % por debajo, `max_sessions` alineado. Aceptar 2-2,5× contenedores a cambio de salir del precipicio.
3. **Precalentar por campaña** y **drenar al desplegar**; antes, las dos pruebas de §5 (vida del WS y redespliegue).
4. **Engine C#:** reconexión, flush, identificador común, y una medición de 500 llamadas con el cliente M1.
5. **Alarma de degradación por contenedor**, no sólo de caídas.
6. **Cerrar la brecha imagen ⇄ Modal** (hilos, entrypoint, versión) para que M1 describa lo que corre.
7. **M1** con dos mezclas de conversación (respuestas cortas y largas): convierte el "38-50 según los ciclos" en una K por tipo de campaña.

**El refactor se reabre sólo si** M1 muestra que a 60 ms la rodilla sigue por debajo de ~25 **y** que operar a K = 20-25 con 100 contenedores produce incidentes que las alarmas y `max_inputs` no evitan, o si la latencia por llamada falla los criterios de §7 de la estrategia incluso con pocas llamadas. Ninguna de esas dos cosas se sabe hoy.

---

## Fuentes

- [Modal · pricing](https://modal.com/pricing) · [network egress billing](https://modal.com/docs/guide/network-egress-billing) · [resources](https://modal.com/docs/guide/resources) · [scale](https://modal.com/docs/guide/scale) · [webhook timeouts](https://modal.com/docs/guide/webhook-timeouts) · [lifecycle functions](https://modal.com/docs/guide/lifecycle-functions) · [concurrent inputs](https://modal.com/docs/guide/concurrent-inputs)
- Terceros que coinciden en las tarifas: [flexprice](https://flexprice.io/pricing-index/modal) · [beam.cloud](https://www.beam.cloud/blog/modal-pricing-explained) · [usagepricing](https://usagepricing.com/blueprint/modal)
- Este repo: `CLAUDE.md` (hechos medidos), `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md` §7-§8, `experimentos/2026-10-09_m0-gate/`, `experimentos/2026-10-10_m1-linea-base/`.
