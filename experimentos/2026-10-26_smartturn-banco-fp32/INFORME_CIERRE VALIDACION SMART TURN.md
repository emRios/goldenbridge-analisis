# Informe de cierre · Validación de smart-turn fp32

**GoldenBridge · motor nuevo · 2026-10-27**
Pruebas corridas en el servidor DEV `.143` (Xeon 8488C). Sin commit en el repositorio hasta la aprobación del dueño.

## Etiquetas

| Etiqueta | Significado |
|---|---|
| `[MEDIDO]` | medido en una corrida, con la ruta de su manifiesto en la sección Fuentes |
| `[CÓDIGO]` | leído en el código de GoldenBridge |
| `[INFERIDO]` | deducido de lo medido, sin medirlo directo |
| `[ABIERTO]` | sin resolver |

## Palabras usadas

- **Smart-turn:** el modelo que decide si la persona terminó de hablar.
- **Cálculo:** una de esas decisiones.
- **Candado:** la regla de GoldenBridge que deja calcular a uno solo a la vez, para todo el servidor.
- **Hilo:** una línea de trabajo del programa; cada hilo hace un cálculo a la vez.
- **Núcleo físico:** un procesador real. Las pruebas usaron 4, que el sistema muestra como 8.

## Resumen

1. **El techo de smart-turn lo pone el tiempo de cada cálculo,** porque GoldenBridge los hace de uno en uno detrás de un candado. Con un cálculo más corto, la fila desapareció a 100 llamadas `[MEDIDO]`.
2. **El mismo modelo fp32 puede hacer 4 cálculos a la vez sobre una sola copia, sin cambiar ninguna decisión** `[MEDIDO]`. En el servidor de pruebas eso da **57,7 decisiones por segundo** con la carga del detector de voz de 100 llamadas al lado, contra **~14** de GoldenBridge hoy.
3. **Propuesta para el motor nuevo** `[INFERIDO]`: una copia compartida del modelo, sin candado, tantos cálculos a la vez como núcleos físicos y un hilo por cálculo. Capacidad prevista = cálculos a la vez ÷ tiempo de cada cálculo.

---

## Prueba 1 · ¿El techo lo pone el tiempo de cada cálculo?

**Fecha:** 2026-10-27, 01:13-03:52 UTC.

**Cómo se hizo.** Se usó GoldenBridge completo, con 50, 75 y 100 llamadas simuladas. Los proveedores de transcripción, respuesta y voz se reemplazaron por imitaciones locales sin costo. Se cambió una sola cosa, el archivo del modelo:

- **fp32:** el modelo de producción.
- **int8:** más rápido. Se usó sólo como palanca para acortar el cálculo. No es candidato, porque Modal no garantiza el procesador que necesita.

Fueron cuatro corridas alternadas: fp32, int8, fp32, int8, en los dos órdenes de carga.

| A 100 llamadas `[MEDIDO]` | fp32 (2 corridas) | int8 (2 corridas) |
|---|---:|---:|
| Espera + cálculo de cada decisión, mediana | 857-858 ms | 67-68 ms |
| El más rápido de todos | 819-820 ms | 39 ms |
| Decisiones por segundo | 13,9 | 16,0-16,1 |
| Turnos cerrados por segundo | 7,2-7,3 | 9,9 |

**Veredicto preregistrado: EFECTO.** Las dos corridas int8 quedaron por debajo del rango de las dos fp32. Codex recalculó los números desde los registros sin conocer la hipótesis y los confirmó.

**Corrección de lectura.** A 100 llamadas el fp32 hace más decisiones por turno: 1,68 contra 1,53. A 50 llamadas, donde no hay fila en ningún modelo, casi coinciden: 1,52 contra 1,49. La diferencia viene de la espera, no de que el int8 decida distinto `[MEDIDO]` `[INFERIDO]`.

La excepción son los cierres por respaldo. El fp32 ya tiene ~75 a 50 llamadas y el int8 ninguno. Son el 2-3 % de los turnos.

**Límites.**
- Con imitaciones de los proveedores, no vale como cifra absoluta para Modal.
- Muestra que el cálculo fija el techo, pero no separa el candado del grupo de hilos que espera. Eso lo responde la prueba 2.

---

## Prueba 2 · Banco fp32: ¿cuántos cálculos a la vez, con qué copia y a qué costo?

**Fecha:** 2026-10-27, 14:28-15:36 UTC.

**Cómo se hizo.** Smart-turn fp32 se llamó directamente, sin GoldenBridge, dentro de la misma imagen y con la misma configuración: un hilo por cálculo, en 4 núcleos físicos.

- Cuatro series, 51 condiciones de 60 s, 3 repeticiones cada una. Todas válidas `[MEDIDO]`.
- Cada respuesta en paralelo se comparó con la calculada de una en una.

### Capacidad según cálculos a la vez `[MEDIDO]`

| Cálculos a la vez | Decisiones por segundo | Tiempo de cada cálculo, mediana | % de lo ideal |
|---:|---:|---:|---:|
| 1 | 17,7 | 56,3 ms | 100 % |
| 2 | 34,4 | 57,9 ms | 97 % |
| 4 | 63,3 | 62,6 ms | 89 % |
| 8 | 63,2 | 125,5 ms | 45 % |

Con 8 no se gana nada: son 4 núcleos físicos, y dos cálculos en el mismo núcleo se reparten su tiempo.

### Una copia del modelo o una por hilo, con 4 a la vez `[MEDIDO]`

| Copia | Decisiones por segundo | Memoria del modelo |
|---|---:|---:|
| Compartida | 63,6 | 56 MB |
| Una por hilo | 63,0 | 170 MB |

### Con el detector de voz al lado, 4 a la vez `[MEDIDO]`

| Llamadas simuladas | Núcleos del detector | Tiempo de cada cálculo, mediana | Decisiones por segundo |
|---:|---:|---:|---:|
| 0 | 0 | 62,3 ms | 63,6 |
| 25 | 0,31 | 63,6 ms | 62,3 |
| 50 | 0,60 | 65,6 ms | 60,7 |
| 100 | 1,21 | 69,1 ms | 57,7 |

### Espera cuando los pedidos llegan al azar `[MEDIDO]`

Espera más cálculo, mediana. El ritmo es un porcentaje de lo que aguanta cada configuración: 17,7/s con 1 hilo y 63,3/s con 4.

| Ritmo de llegada | 1 hilo, como hoy | 4 hilos sin candado |
|---:|---:|---:|
| 50 % | 64 ms | 62 ms |
| 75 % | 131 ms | 63 ms |
| 90 % | 827 ms | 89 ms |
| 100 % | 3.555 ms | 208 ms |

### Criterios preregistrados

| Criterio | Regla | Resultado `[MEDIDO]` |
|---|---|---|
| Mismas decisiones | diferencia ≤ 0,000001 y ninguna decisión cambiada en los umbrales 0,35 y 0,85 | **cumple**: diferencia 0 en las 51 |
| Escala con 2 / 4 / 8 | ≥ 80 % de lo ideal | **97 %** cumple · **89 %** cumple · **45 %** no cumple |
| Mejor cantidad a la vez | mayor capacidad; si hay empate dentro del 5 %, la menor | **4**: 63,3/s |
| Copia del modelo | compartida, salvo que una por hilo dé ≥ 5 % más | **compartida** |
| Contraste con los ~72 ms deducidos | a 100 llamadas | **compatible, no probado**: 69,1 ms, pero la carga simulada fue el 38 % de la de GoldenBridge |
| Sin hilos girando en reposo | ≤ 1 % de un núcleo | **cumple**: 0 % |

---

## Política propuesta para el motor nuevo `[INFERIDO]`

| Decisión | Propuesta | Por qué |
|---|---|---|
| Cálculos a la vez | uno por núcleo físico disponible para smart-turn | escala hasta los núcleos físicos; más hilos sólo alargan cada cálculo |
| Copia del modelo | una sola, compartida, sin candado | mismo rendimiento, un tercio de la memoria, decisiones idénticas |
| Hilos por cálculo | 1, como hoy | sin hilos girando en reposo; resultados deterministas |
| Carga de trabajo | hasta ~90 % de la capacidad | al 90 %, la mitad de los pedidos espera ≤ 89 ms con 4 hilos; al 100 %, 208 ms |

**La cuenta.** Decisiones por segundo ≈ cálculos a la vez ÷ tiempo de cada cálculo. Con 4 núcleos y el detector de voz de 100 llamadas al lado: 4 ÷ 0,069 s ≈ 58 decisiones/s, que coincide con lo medido, 57,7.

En la prueba 1, GoldenBridge pedía ~0,16 decisiones por segundo por llamada a 100 llamadas. Con esa demanda, 58 decisiones/s alcanzan para varios cientos de llamadas en la parte de smart-turn `[INFERIDO]`. El límite del motor lo pondrá otra pieza antes que smart-turn.

---

## Lo que queda abierto `[ABIERTO]`

- **El procesador de Modal.** Todo se midió en un Xeon 8488C. El banco registra el modelo de CPU y debe correrse también en un contenedor de Modal.
- **Tiempo aislado del cálculo.** El banco da 56-62 ms. La medida del 14 de octubre dio 47,6 ms, con otra imagen y otro script. Falta explicar la diferencia.
- **El resto de la carga.** El banco simuló sólo el detector de voz: 1,2 de los ~3,2 núcleos que usa GoldenBridge a 100 llamadas.
- **La preparación del audio.** Se midió en Python: 2,6 ms por pedido. En el motor nuevo será C++ y hay que medirla allí.
- **El siguiente cuello.** En la prueba 1, cuando smart-turn dejó de hacer fila, subieron otras dos esperas:
  - antes de la voz del bot, de 64 a 474 ms en el 90 % de los turnos;
  - la del bucle principal.

  La síntesis de voz y el reparto del audio son los candidatos.
- **Calidad de decisiones.** Ninguna prueba mide si las decisiones son buenas; sólo que no cambian al calcular en paralelo.

---

## Fuentes

| Prueba | Rama y carpeta | Corridas, cada una con `manifiesto.txt` |
|---|---|---|
| 1 · fp32 contra int8 con imitaciones de proveedores | `paso/m1-linea-base` · `experimentos/2026-10-27_m1s-smartturn-int8-fp32-mocks/` (README, RESULTADOS.md) | `resultados/20261027T012224Z`, `20261027T015959Z`, `20261027T023736Z`, `20261027T031510Z`; control `20261027T011336Z` |
| 2 · banco fp32 | `paso/smartturn-banco` · `experimentos/2026-10-26_smartturn-banco-fp32/` (README, banco.py, analiza.py, run.sh) | `resultados/20261027T142841Z` (resumen.md, resumen.tsv) |
| Línea base con proveedores reales | `paso/m1-linea-base` · `experimentos/2026-10-10_m1-linea-base/` | `resultados/20261019T210000Z` |

Lo común a las dos pruebas:

- **Imagen:** `goldenbridge:remarcacion-v1-jef-fp32` (`sha256:8485ac9f…`).
- **Modelo fp32:** `smart-turn-v3.2-gpu.onnx` (`sha256:ab8dc64b…`).
- **Entorno:** ONNX Runtime 1.24.4 y Python 3.14 sin GIL.
