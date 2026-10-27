# 2026-10-26 · Banco de smart-turn fp32 para el motor nuevo

> Estado: **diseño aprobado por el dueño (2026-10-27) con las cinco decisiones de abajo. `banco.py` escrito y validado con
> pruebas cortas; `run.sh` y `analiza.py` escritos y validados con `CORTA=1`. Series corridas 2026-10-27 14:28-15:36 UTC, sello `20261027T142841Z`.** Sin commit.

Etiquetas usadas en este documento:
`[MEDIDO]` medido, con la ruta del manifiesto · `[CÓDIGO]` leído en el código, con archivo y línea ·
`[DOCUMENTACIÓN]` dicho por la documentación de una biblioteca · `[INFERIDO]` deducido, sin medir ·
`[ABIERTO]` sin resolver.

## Paso de la estrategia

Política de smart-turn del **motor nuevo** (la reescritura). No es un arreglo del GoldenBridge (GB) actual.

## Para qué

Fijar con números tres decisiones del motor nuevo:

- **P**: cuántas inferencias de smart-turn corren en paralelo.
- **S**: cuánto dura cada inferencia (`Run` de ONNX Runtime) bajo carga.
- Si los P hilos **comparten una sesión** de ONNX Runtime o cada uno tiene la suya.

De ahí sale la capacidad como aritmética: **inferencias/s ≈ P ÷ S**. Es el criterio de "K previsible".

## Qué se sabe antes de correr

- `[DOCUMENTACIÓN]` ONNX Runtime documenta `InferenceSession.Run` como seguro entre hilos sobre una misma sesión.
- `[CÓDIGO]` GB arma la sesión de smart-turn con `ORT_SEQUENTIAL`, `inter_op=1`, `intra_op=GB_ORT_INTRA_OP` (1 en producción)
  y `ORT_ENABLE_ALL` (`src/processors/smart_turn_processor.py` L118-123). Todas las sesiones de llamada comparten esa
  sesión y un único `threading.Lock` alrededor de `session.run` (L145, L224).
- `[CÓDIGO]` GB arma Silero con el `OnnxWrapper` de silero-vad: `inter_op=1`, `intra_op=1`, modelo `silero_vad.onnx`
  (`$TORCH_HOME/hub/snakers4_silero-vad_master/src/silero_vad/utils_vad.py` L17-24, dentro de la imagen).
  A 8 kHz cada chunk es de 256 muestras = 32 ms, es decir **31,25 chunks/s por llamada**.
- `[MEDIDO 2026-10-27]` Dentro de la imagen `goldenbridge:remarcacion-v1-jef-fp32`: ORT 1.24.4, Python 3.14.7 sin GIL,
  y `get_session_options()` devuelve `intra 1, inter 1, ORT_SEQUENTIAL` (comando de verificación, sin manifiesto: se
  repite como lectura de vuelta del banco). La entrada del modelo es `input_features [batch, 80, 800]`: forma fija,
  así que el costo del `Run` no depende del contenido del audio.
- `[MEDIDO 2026-10-14]` `Run` fp32 aislado en `.143`: 47,6 ms p50 (`2026-10-14_smartturn-fp32/resultados/20261014T195156Z/manifiesto.txt`).
- `[MEDIDO 2026-10-27]` Con GB saturado a 100 llamadas, fp32: 13,9 intentos/s y `inference_ms` mínimo 819 ms
  (`2026-10-27_m1s-smartturn-int8-fp32-mocks/resultados/20261027T012224Z/manifiesto.txt`, rama `paso/m1-linea-base`).
- `[INFERIDO]` Si la fila está en el candado global y el `Run` es lo único que lo ocupa, cada `Run` fp32 ocupa ~72 ms
  bajo carga (1 ÷ 13,9/s), contra 47,6 aislado. Ver "Revisión del diseño", punto 1: esta deducción tiene una
  alternativa que da la misma cifra.

## Hipótesis

1. **Paralelo sobre una sesión:** P hilos llamando a `Run` sobre una sesión compartida dan las mismas salidas que
   en serie, y el caudal crece casi lineal hasta el número de núcleos físicos libres.
2. **Sesión por hilo:** no mejora el caudal frente a una sesión compartida; sólo cuesta memoria.
3. **Convivencia con Silero:** S crece con la carga de Silero; a la carga de 100 llamadas, S queda cerca de 72 ms.

## Decisiones del dueño (2026-10-27)

1. **Serie A con P = 1, 2, 4 y 8.** El cpuset tiene 8 vCPU que son 4 núcleos físicos; P = 8 muestra si compartir núcleo frena el `Run`.
2. **La serie C mide sólo el `Run`.** Las features se miden aparte: en el motor nuevo serán C++.
3. **Serie D con P = 1 y con el mejor P,** a 0,5, 0,75, 0,9 y 1,0 × la capacidad que esa configuración dio en la serie A.
4. **Dos lecturas escritas antes de correr** para el contraste con los 72 ms (ver "Criterios").
5. **Paridad con la tolerancia del encargo:** máx \|Δp\| ≤ 1e-6 y 0 decisiones cambiadas.

Reglas para elegir "el mejor" (fijadas antes de medir):
- **Mejor P:** el de mayor caudal en la serie A entre los que cumplen paridad. Si dos quedan a menos de 5 % de caudal, el menor P.
- **Mejor forma de sesión:** compartida, salvo que una sesión por hilo dé al menos 5 % más de caudal.

## Variable de cada serie

| serie | variable | lo fijo | qué responde |
|---|---|---|---|
| A | P = 1, 2, 4 y 8 hilos llamando a `Run` | una sesión compartida, sin otra carga | ¿escala el `Run` en paralelo sobre una sesión? |
| B | una sesión por hilo, contra una compartida | P = el mejor de A | ¿hace falta una sesión por hilo, y cuánta memoria cuesta? |
| C | carga de Silero de 25, 50 y 100 llamadas (31,25 chunks/s por llamada, `silero_vad.onnx`, mismo cpuset) | el mejor P y la mejor forma de sesión de A/B | ¿cuánto se alarga S cuando convive con Silero? |
| D | llegadas abiertas (Poisson) a 0,5, 0,75, 0,9 y 1,0 × la capacidad medida en A de cada configuración | P = 1 (como GB hoy) y el mejor P | curva de espera según la carga: la rodilla |

Todo lo demás queda fijo y se registra: imagen, modelo (sha256 `ab8dc64b…`), ORT 1.24.4, opciones de sesión,
cpuset `2-5,10-13`, entradas.

## Dónde y cómo

- Un script, `banco.py`, que llama a smart-turn **directamente**: sin GB, sin event loop, sin proveedores, sin tráfico.
- Corre dentro de la imagen `goldenbridge:remarcacion-v1-jef-fp32` con `docker run --rm --network none`,
  `--cpuset-cpus 2-5,10-13`, `--entrypoint python`. Monta el script en sólo lectura. No toca `gb-rem-jef` ni ningún servicio.
  No depende de campañas ni de la hora.
- Sesión de smart-turn armada como en producción: `ORT_SEQUENTIAL`, `inter_op=1`, `intra_op=1`, `ORT_ENABLE_ALL`.
- Features con el extractor de la imagen (`src/processors/whisper_features.py`, `chunk_length=8`, ventana de 8 s).
- **Un proceso nuevo por condición.** Así la memoria (RSS) de cada condición se mide limpia y una condición no calienta a la siguiente.

### Entradas

- Las 14 features congeladas del guardián (`/app/test/reference/features.npz`): para paridad.
- Cortes de los 10 audios de `tools/loadtest/utterances/` pasados por el extractor de producción: para paridad y para
  medir las features. El cálculo del `Run` no depende de la entrada (forma fija).

### Qué hace cada hilo

- **Series A, B, C:** lazo cerrado. Cada uno de los P hilos toma la siguiente entrada y llama a `Run` sin pausa.
  El tiempo de features se mide aparte, una vez por entrada, porque en el motor nuevo será C++ y no Python.
- **Serie D:** un generador de llegadas Poisson a la tasa fijada pone pedidos en una cola; P hilos los atienden.
  Espera = cola + `Run`.
- **Carga de Silero (serie C):** N llamadas simuladas, cada una con su estado de Silero, cada una pide un chunk cada
  32 ms. Los atiende un pool de 12 hilos, como `vad.inference_workers: 12` de producción, sobre una sesión compartida.

### Duración

Cada condición: 10 s de calentamiento descartado y 60 s medidos. 3 repeticiones, con el orden de las condiciones
rotado entre repeticiones. Estimado: A 4 × 70 s × 3 ≈ 14 min · B 2 × 70 × 3 ≈ 7 min · C 3 × 70 × 3 ≈ 11 min ·
D 3 tasas × 2 valores de P × 70 × 3 ≈ 21 min. Total ≈ 55 min más arranques.

## Lectura de vuelta (nunca por código de retorno)

Antes de medir, en cada proceso; si algo no coincide, la condición aborta:

| qué | de dónde se lee |
|---|---|
| sha256 del modelo cargado | hash del archivo que se pasó a `InferenceSession` |
| versión de ORT | `onnxruntime.__version__` |
| `intra_op`, `inter_op`, modo | `session.get_session_options()`, no la variable de entorno |
| cpuset | `/sys/fs/cgroup/cpuset.cpus.effective` |
| hilos del proceso | `/proc/self/task` |
| CPU del proceso en reposo 5 s antes de medir | `/proc/self/stat`; tiene que ser ~0: sin hilos girando |
| modelo de CPU y banderas | `/proc/cpuinfo`, incluida `avx512_vnni` |
| en qué CPU corrió cada hilo | `/proc/self/task/<tid>/stat` campo 39, muestreado durante la medición |

## Qué se mide

- **S por inferencia:** tiempo de pared del `Run` y CPU del hilo (`time.thread_time`), p50/p90/p99.
- **Caudal:** inferencias/s.
- **Features (log-mel):** tiempo por entrada, por separado.
- **Memoria (RSS):** con 1 sesión y con P sesiones.
- **Serie C:** CPU consumida por la carga de Silero, para compararla con los ~3,2 núcleos que usaba GB a 100 llamadas.
- **Serie D:** espera p50/p90/p99 y largo de la cola, por tasa.

## Criterios preregistrados

| criterio | regla | si falla |
|---|---|---|
| paridad | con P en paralelo, cada salida igual a la de la ejecución en serie: máx \|Δp\| ≤ 1e-6 y 0 decisiones cambiadas en los umbrales 0,35 y 0,85 | se descarta el `Run` concurrente sobre una sesión compartida |
| escala | caudal con P ≥ 0,8 × P × caudal con 1 | se dice hasta qué P escala |
| contraste con M1 | la serie C a 100 llamadas debería dar S ≈ 72 ms | dos lecturas posibles, escritas antes de correr: (a) la deducción de 72 ms no vale; (b) la serie C no reproduce toda la carga de GB. Se informa qué fracción de los ~3,2 núcleos de GB usó la carga de Silero; si es mucho menor, pesa la lectura (b) |
| reposo | CPU del proceso ≤ 1 % de un núcleo durante los 5 s previos | la condición no se interpreta: hay hilos girando |

## Límites

- Mide el `Run` de ORT, que es el mismo núcleo C++ que usará el motor nuevo. Las features en Python no son las de C++.
- Mide la CPU de `.143` (Xeon 8488C). Modal no garantiza el hardware: el banco registra el modelo de CPU y tiene que
  poder correr también en un contenedor de Modal `[ABIERTO]`.
- No mide calidad de decisiones: sólo tiempos, caudal, memoria y paridad entre ejecuciones del mismo modelo.

## Revisión del diseño

Puntos que no se sostienen tal cual, o que conviene decidir antes de escribir código:

1. **El dato de 72 ms tiene dos explicaciones con la misma cifra.** Si la fila fuera el executor de ~12 hilos, 12 hilos
   ÷ 0,86 s de espera también dan ~14/s. Además, 13,9/s cuenta los `close_attempts` de turnos cerrados, no todos los
   `Run`: si hubo `Run` que no cerraron turno dentro de la ventana, el caudal real fue mayor y S menor `[INFERIDO]`. La
   serie C mide S directo, y por eso decide; pero un resultado distinto de 72 ms no basta para decir cuál de las dos
   explicaciones vale.
2. **La serie C no reproduce toda la carga de GB.** A 100 llamadas GB usaba ~3,2 núcleos `[MEDIDO 2026-10-27]`; Silero
   es sólo una parte. Faltan el event loop, el remuestreo, las features de las otras inferencias y la red. Si C a 100 da
   menos de 72 ms, la diferencia puede ser esa carga faltante y no un error de la deducción. Propuesta: medir la CPU de
   la carga de Silero en C y reportar cuánto de los 3,2 núcleos representa; y en C, que los hilos de smart-turn también
   calculen features antes de cada `Run`, como en producción.
3. **8 vCPU son 4 núcleos físicos.** El cpuset `2-5,10-13` empareja cada vCPU i con i+8. Con P = 4, dos hilos pueden caer
   en el mismo núcleo físico y frenarse. Propuesta: añadir P = 8 a la serie A, para ver el efecto de compartir núcleo, y
   registrar en qué CPU corrió cada hilo.
4. **Las tasas fijas de la serie D dependen de P.** Con P = 1, S ≈ 48 ms da una capacidad de ~21/s: la tasa de 21/s es el
   límite exacto y la cola crece sin fin. Con P = 4 la capacidad sería ~80/s y 21/s no llega a la rodilla. Propuesta:
   correr D con P = 1 (lo que hace GB hoy) y con el mejor P, y añadir tasas como fracción de su capacidad medida:
   0,5, 0,75, 0,9 y 1,0 × P ÷ S.
5. **Paridad casi segura.** Con `intra_op=1` y `ORT_SEQUENTIAL`, cada `Run` es determinista; se espera igualdad exacta,
   no sólo ≤ 1e-6 `[INFERIDO]`. El criterio queda como está; se reporta además si hubo alguna diferencia distinta de 0.

## Pruebas cortas del script (2026-10-27 05:41 UTC, `.143`)

Validación del código, no resultados: 2-10 s por condición, salidas en el scratchpad de la sesión, sin manifiesto.

| prueba | caudal | `Run` p50 | otros |
|---|---|---|---|
| A, P = 1, 10 s | 16,0/s | 62,4 ms | paridad 0, CPU en reposo 0 %, 1 hilo en reposo; 45 entradas (15 congeladas + 30 cortes de audio); features p50 2,6 ms |
| B, P = 4 por hilo, 5 s | 60,7/s | 65,3 ms | memoria de 4 sesiones ≈ 171 MB contra ≈ 57-59 MB de 1 |
| C, P = 4, 100 llamadas de Silero, 5 s | 55,8/s | 71,5 ms | Silero: 3.124 de 3.125 chunks/s, 1,49 núcleos |
| D, P = 1, 8/s, 5 s | 6,6/s | 63,0 ms | espera p50 68 ms |

Lectura de vuelta completa en todas: sha256 `ab8dc64b…`, ORT 1.24.4, sin GIL, intra/inter 1/1 leídos de la sesión,
cpuset `2-5,10-13`, Xeon 8488C con `avx512_vnni`.

Notas para el diseño:
- El `Run` aislado sale en ~62 ms, no en los 47,6 ms del 2026-10-14. Esa medida se hizo con otra imagen (`314t`) y otro
  script; hay que ver la diferencia antes de usar 47,6 como referencia `[ABIERTO]`.
- La carga de Silero de 100 llamadas usa ~1,5 núcleos de los ~3,2 que usaba GB a 100 llamadas: la serie C reproduce
  aproximadamente la mitad de esa carga.
- El contenedor corre con el usuario del host (`--user`) y `NUMBA_CACHE_DIR=/tmp/numba`, para poder escribir la salida.

## Cómo correrlo

```bash
cd ~/goldenbridge-analisis-banco/experimentos/2026-10-26_smartturn-banco-fp32   # worktree de la rama paso/smartturn-banco
CORTA=1 ./run.sh                                  # validación: A con P = 1, 2 + 10 s (hecha 2026-10-27, sello 20261027T054406Z)
setsid nohup ./run.sh > corrida.log 2>&1 &        # series A → B → C → D, 3 repeticiones, ≈ 65 min
```
`run.sh` escribe el manifiesto antes de medir, corre cada condición en un contenedor nuevo y aborta si la lectura de
vuelta falla. B, C y D toman el mejor P y la mejor sesión de `analiza.py` con las reglas de arriba. Al final:
`resultados/<sello>/resumen.md` y `resumen.tsv`.

## Resultado

Corrida `resultados/20261027T142841Z/` (manifiesto en esa carpeta): 51 condiciones, 3 repeticiones de 60 s cada una,
todas válidas por lectura de vuelta `[MEDIDO]`. Tabla completa en `resumen.md`. Contenedores vivos al empezar:
`ai_engine`, `lasik-scheduler-1`, `lasik-app-1` (sin cpuset propio; carga de la máquina al terminar 2,9).

| criterio | resultado `[MEDIDO]` |
|---|---|
| paridad | cumple en las 51: diferencia máxima 0, ninguna decisión cambiada |
| escala | P = 2: 97 % de lo lineal · P = 4: 89 % · P = 8: 45 % (no escala) |
| mejor P | 4 (63,3 decisiones/s; P = 8 da 63,2) |
| mejor sesión | compartida (63,6/s contra 63,0/s por hilo; 56 MB contra 170 MB) |
| contraste con M1 | serie C a 100 llamadas: `Run` p50 69,1 ms; la carga de Silero usó 1,21 núcleos de los ~3,2 de GB (38 %). Lectura: compatible con los ~72 ms deducidos, pero no los prueba (falta 62 % de la carga de GB) |
| reposo | 0,00 % de un núcleo en todas |

Capacidad `[MEDIDO]`, decisiones/s: 1 hilo sin carga 17,7 · 4 hilos sin carga 63,3 · 4 hilos con Silero de 100 llamadas 57,7.
Espera total (fila + cálculo) al 100 % de la capacidad, mediana: 1 hilo 3.555 ms · 4 hilos 208 ms. Al 90 %: 827 contra 89 ms.

`[INFERIDO]` P = 8 no escala porque las 8 vCPU del cpuset son 4 núcleos físicos (topología en `topologia_cpu.txt`):
el `Run` ocupa el núcleo entero y dos hilos en el mismo núcleo se reparten su tiempo (cada `Run` pasa de 63 a 125 ms).
