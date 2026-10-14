# 2026-10-14 · smart-turn en punto flotante (fp32) contra el cuantizado (int8)

## Pregunta

Si GoldenBridge usa la versión en punto flotante del modelo de fin de turno (`smart-turn-v3.2-gpu.onnx`, 32,4 MB) en vez de la cuantizada que usa hoy (`smart-turn-v3.2-cpu.onnx`, 8,68 MB), ¿desaparece la dependencia de `avx512_vnni`? ¿Qué cambia en las decisiones y en la latencia?

## Qué se sabe antes de correr

- `[CÓDIGO]` El requisito de `avx512_vnni` es **sólo por smart-turn**, el modelo de fin de turno: `DECIDEN = ("avx512_vnni",)` en `test/test_reference_features.py`. Silero, el detector de voz, corre en punto flotante y su guardia no mira la CPU.
- `[MEDIDO 2026-08-24]` Con el cuantizado, un Skylake-SP sin VNNI volteó 6 de 14 decisiones contra dos CPUs con VNNI, que coincidieron entre sí al sexto decimal.
- `[DOCUMENTACIÓN]` En CPUs sin VNNI, ONNX Runtime ejecuta las multiplicaciones de 8 bits con `VPMADDUBSW`, que satura en 16 bits; con VNNI usa `VPDPBUSD`, que acumula en 32 bits.
- `[CÓDIGO]` El guardián de arranque compara las banderas **en las dos direcciones** (`faltan` y `sobran`). Una referencia generada en una CPU sin VNNI hace que la imagen **se niegue a arrancar en una CPU con VNNI**. Por eso esta prueba va **antes** de regenerar la referencia.
- `[CÓDIGO]` La referencia del guardián se eligió con el umbral 0,6 (`generate_reference.py`, `THRESHOLD = 0.6`), pero en `.143` el umbral efectivo de smart-turn es **0,35** `[MEDIDO: log de arranque bantrab 2026-09-07]`. Los casos "marginales" del guardián vigilan un umbral que producción no usa. Esta prueba mide los tres umbrales que existen: 0,35 (`.143`), 0,6 (`default.yaml`) y 0,85 (`midspeech_threshold`).

## Hipótesis

1. **fp32:** las probabilidades coinciden entre una CPU con VNNI y una sin VNNI hasta ~1e-6 y **ninguna** decisión cambia, en ningún umbral.
2. **int8 (control):** las probabilidades se mueven entre las dos CPUs (≥ 1e-3), como en agosto. Si no se mueven, la prueba no está comparando lo que tiene que comparar.
3. **fp32 contra int8 en la misma CPU:** algunas decisiones cambian. Cuántas y cuáles, se mide aquí; **si son mejores o peores no lo decide esta prueba** (hace falta un corpus etiquetado o escuchar llamadas).

## Diseño

- **Una variable:** la CPU. Misma imagen (`goldenbridge:314t`: Python 3.14t, numpy 2.5.2, ONNX Runtime 1.24.4), mismos dos archivos de modelo (se verifica por hash), mismas entradas, un hilo por biblioteca (`GB_ORT_INTRA_OP=1`, `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`).
- La sesión de ONNX Runtime se arma igual que en producción (`smart_turn_processor.py` L118-122).
- **Entradas:**
  - las **14 features congeladas** del guardián (`test/reference/features.npz`): byte a byte iguales en cualquier máquina; aíslan el efecto modelo + CPU;
  - un **barrido** cada 20 ms de `utterance.wav` y de los 10 audios de `tools/loadtest/utterances/`, por el extractor y la ventana de producción: el camino completo de una llamada (~3.000 casos).
- **Latencia:** 150 inferencias por modelo sobre tres entradas fijas, tras calentar.
- Corre **dentro de la imagen**, sin red, con la entrada del guardián reemplazada: la máquina sin VNNI no necesita que el guardián pase. No genera tráfico ni toca el servicio.

## Criterios preregistrados

| | aceptación |
|---|---|
| corrida válida | una máquina con `avx512_vnni` y otra sin; mismos hashes de modelo; misma versión de ONNX Runtime y numpy |
| control | int8, features congeladas: máx \|Δp\| entre CPUs ≥ 1e-3 |
| **fp32 independiente de la CPU** | congeladas **y** barrido: máx \|Δp\| ≤ 1e-4 **y** 0 decisiones volteadas en 0,35, 0,6 y 0,85 |
| int8 vs fp32 | se reporta; no decide |
| latencia | se reporta; el fp32 está en el camino crítico del fin de turno |

Si se cumple "fp32 independiente de la CPU", se puede quitar `avx512_vnni` de `DECIDEN` para la imagen con fp32. Si no, el requisito sigue y hay que mirar qué casos se movieron.

## Preparar (en cada una de las dos máquinas)

```bash
git clone https://github.com/emRios/goldenbridge-analisis.git    # o git pull
cd goldenbridge-analisis && git config core.autocrlf false && git switch paso/smartturn-fp32

# la imagen actual (en .143 ya está; en el servidor nuevo, cargar el tar)
sha256sum -c goldenbridge-314t.tar.gz.sha256 && docker load < goldenbridge-314t.tar.gz

# el modelo fp32, el MISMO archivo en las dos máquinas
curl -L -o ~/smart-turn-v3.2-gpu.onnx \
  https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-gpu.onnx
sha256sum ~/smart-turn-v3.2-gpu.onnx    # anotarlo: tiene que dar igual en las dos

# los 10 audios (si no hay checkout de GoldenBridge en esa máquina, copiar la carpeta)
ls /srv/GoldenBridge/tools/loadtest/utterances/*.wav
```

## Correr

```bash
cd experimentos/2026-10-14_smartturn-fp32
MODELO_FP32=~/smart-turn-v3.2-gpu.onnx ./run.sh
# si los audios están en otra ruta:  AUDIOS=/ruta/utterances MODELO_FP32=... ./run.sh
```

Dura unos minutos. Deja `resultados/<sello>/resultado.json`, `manifiesto.txt` y `parametros.txt`.

## Analizar

Con los dos `resultado.json` en la misma máquina (cualquiera, sólo necesita `python3`):

```bash
python3 analiza.py resultados/<sello_con_vnni>/resultado.json resultados/<sello_sin_vnni>/resultado.json
```

Imprime, para cada modelo, cuánto cambia entre CPUs y qué decisiones se voltean en cada umbral; int8 contra fp32 en cada CPU; la latencia; y el veredicto. Sale con 0 si el fp32 es independiente de la CPU.

## Devolver el resultado

```bash
git add experimentos/2026-10-14_smartturn-fp32/resultados/*/{resultado.json,manifiesto.txt,parametros.txt}
git commit -m "smart-turn fp32: resultado en <cpu>"
git push
```

`resultado.json` sólo trae probabilidades, latencias y datos de la CPU: no lleva audio ni datos de clientes (los audios son de prueba, sin datos de ningún cliente).

## Si el fp32 resulta independiente de la CPU: qué cambiar en la imagen

En GoldenBridge, sobre `feat/modal-migration` (o la rama de donde salga la imagen), en este orden:

1. **El modelo:** `smart-turn-v3.2-gpu.onnx` en `modelos/smart-turn/` (el `Dockerfile` copia esa carpeta a `/app/smart-turn/`).
2. **La ruta:** `smart_turn.model_path` en `config/default.yaml` y en `active.yaml`, y `DEFAULT_MODEL_PATH` en `src/processors/smart_turn_processor.py` (L26).
3. **El generador de la referencia** (`test/reference/generate_reference.py`): `MODEL` al fp32; `THRESHOLD` al umbral de producción (0,35 en `.143`); y **volver a elegir los casos marginales** barriendo el audio con el fp32, porque los actuales están elegidos para el int8 y el 0,6. Fijar `intra_op_num_threads = 1` también aquí (hoy no se fija).
4. **El guardián:** `DECIDEN = ()` en `test/test_reference_features.py`, citando esta corrida como evidencia (el propio código exige demostrar el efecto para cambiar esa lista).
5. **Regenerar la referencia dentro de la imagen nueva** (mismo stack), copiarla al repo y reconstruir.
6. **Validar el umbral:** el 0,35 se ajustó con el int8. Con el fp32 las probabilidades se corren; hay que decidir si el umbral se mantiene. Esta prueba da la lista de decisiones que cambiarían.
7. Correr el guardián en las dos máquinas: en las dos tiene que decir `referencia OK`.

## Resultado (2026-10-14)

Corridas: sin VNNI `resultados/20261014T193450Z/` (laptop, AMD Ryzen 5 7535HS, `avx2`+`fma`) · con VNNI `resultados/20261014T195156Z/` (`.143`, Xeon 8488C). Misma imagen (`goldenbridge:314t`, ONNX Runtime 1.24.4, numpy 2.5.2), mismo `smart-turn-v3.2-gpu.onnx` (sha256 `ab8dc64b…39be` verificado en las dos), 3.136 casos cada una (3.122 en común con audio más las 14 congeladas). El manifiesto de `.143` no está en el repo; sólo su `resultado.json`.

| criterio | resultado |
|---|---|
| control: el int8 se mueve entre CPUs | **reproducido**: máx \|Δp\| 0,51 (congeladas) y 0,94 (barrido) |
| fp32 independiente de la CPU, congeladas | **sí**: máx \|Δp\| 2,1e-6, 0 volteadas en 0,35 / 0,6 / 0,85 |
| fp32 independiente de la CPU, barrido | **sí**: máx \|Δp\| 1,0e-5, 0 volteadas en 3.122 casos y tres umbrales |
| veredicto de `analiza.py` | `salida=0` |

Decisiones volteadas **entre CPUs** del barrido, 3.122 casos: int8 **279** (umbral 0,35) · **304** (0,6) · **363** (0,85) — entre 9 y 12 %; fp32 **0**.

Latencia por inferencia en `.143` (la que cuenta): int8 27,1 ms · fp32 47,6 ms (p99 27,4 y 48,8). En la laptop: 107 y 137 ms, sin valor para decidir.

### Lo que el resultado NO resuelve

**Entre int8 y fp32, en la misma CPU, las decisiones difieren mucho**, y no es un efecto de la CPU: en `.143` se voltean 470 de 3.136 casos en 0,35 (15 %), 931 en 0,6 (30 %) y 980 en 0,85 (31 %); en la laptop, similar. Ejemplo: `u01_girasol@1000`, int8 0,987 contra fp32 0,462 en las dos CPUs. Las probabilidades de los dos archivos describen comportamientos distintos, no el mismo con otra precisión. `[ABIERTO]` por qué; no se ha comparado qué es cada archivo más allá de su nombre y tamaño.

Los casos del barrido son cortes cada 20 ms en cualquier punto del audio, incluso a mitad de palabra. En producción smart-turn sólo se evalúa tras una pausa de la voz, así que el porcentaje de volteadas no equivale al porcentaje de turnos que cambiarían.

**Se probó una verdad de referencia con los audios** (cortes a mitad de frase = "no ha terminado"; ≥ 400 ms de silencio tras la última voz = "terminó"). Resultó **no concluyente**: sólo 4 casos de "terminó" (los audios casi no traen silencio al final), y ambos modelos "cortan a mitad de frase" en más de la mitad de los cortes (int8 75 % y fp32 56 % en 0,35), lo que dice que el corpus no representa la distribución de producción, no que los modelos sean malos. **Por tanto este experimento no dice si el fp32 es mejor ni peor que el int8.**

### Lo que sí queda establecido

- Pasar a fp32 **elimina la dependencia de la CPU**: el requisito de `avx512_vnni` deja de hacer falta para smart-turn `[MEDIDO]`.
- Mantener el int8 en una CPU sin VNNI cambia entre 9 y 12 % de las decisiones de los cortes medidos `[MEDIDO]`, más que lo visto en agosto (6 de 14, sobre 14 casos).
- El fp32 cuesta ~20 ms más por inferencia en `.143` `[MEDIDO]`.

### Antes de cambiar la imagen (paso 4 de la guía)

Hace falta medir los dos modelos **sobre decisiones reales de producción**: la probabilidad de cada uno en cada pausa detectada por el VAD, en llamadas verdaderas, con el resultado (¿la persona había terminado?). Opciones, de menor a mayor costo:
1. **Modo sombra** en las llamadas de prueba (paso 9 de la guía): el contenedor decide con el int8 y registra además la probabilidad del fp32 en cada decisión. Sin riesgo para la llamada.
2. Escuchar una muestra de los casos que el barrido marca como volteados.
3. Un corpus etiquetado de pausas reales.

## Imagen `goldenbridge:314t-fp32` (2026-10-14)

Construida en la laptop (WSL) sobre `feat/modal-migration@9c024fd` + el parche del guardián (`1406660`) + el cambio a fp32 (`cbe7e4c`: modelo, ruta, `DECIDEN = ()`, `THRESHOLD = 0.35`, casos elegidos con `elige_cortes.py`, `intra_op = 1` en las pruebas, `GB_ORT_INTRA_OP` por defecto `1`) + la referencia regenerada dentro de la propia imagen. Rama local `fix/smartturn-fp32` en `~/gb-fp32` (WSL); no está en el remoto de GoldenBridge.

`[MEDIDO]`
- La imagen trae `GB_ORT_INTRA_OP=1`, `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1` (`docker image inspect`).
- La referencia generada en la laptop coincide con el barrido de `.143` hasta la quinta cifra (`marg_1200ms` 0,353132 contra 0,353133).
- **El guardián pasa en una CPU sin VNNI** (Ryzen 5 7535HS): `12 passed`, `casos=12/12 fallos=0 errores=0 saltadas=0`, `referencia OK`, `salida=0`. La imagen int8 no pasa en esa misma CPU.

Pendiente: guardián y llamadas de prueba en `.143` (pasos 8-13 de la guía); ajustar el umbral con las decisiones registradas (`Smart Turn prediction`, nivel DEBUG, `GOLDEN_BRIDGE_DEBUG=1`).
