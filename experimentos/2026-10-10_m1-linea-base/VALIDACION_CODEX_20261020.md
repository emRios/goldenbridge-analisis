# Validación independiente con Codex — 2026-10-20

Informe final del subagente `codex:rescue` (Codex CLI 0.162.0, hilo `01a156f1-2cdf-7592-876f-739e1f5d3925`), transcrito tal cual lo entregó. Encargo: validar `AUDITORIA_20261019.md` (commit `c66a82e`) contra `resultados/crudo/` y el código de la imagen `goldenbridge:remarcacion-v1-jef-fp32`, sólo lectura. Las correcciones 1-5 ya están aplicadas en la auditoría (commit `1e91a6e`, sección 10).

---

**Validación parcial: los resultados numéricos principales se reproducen, pero la auditoría necesita correcciones en la interpretación del probe y en varias conclusiones causales.**

Verifiqué la rama `paso/m1-linea-base` y el commit `c66a82ef8dca54d7a3cd526f9d730e32dd3f2b24`. Recalculé directamente desde los 13 escalones crudos, en memoria, y contrasté el código extraído. No ejecuté Docker, toqué contenedores ni modifiqué archivos.

Las correcciones más importantes son:

1. **`frames` no cuenta exclusivamente mensajes procesados.** El probe (`server/websocket_server.py:3042`) suma `len(frames)`, aunque el lote puede consumir menos mensajes y devolver los restantes a `carry`. Estos pueden volver a contabilizarse. Reproduzco aritméticamente los porcentajes publicados —incluido 43,946 %—, pero **no representan cobertura real de mensajes únicos procesados**. Esto también invalida presentar `job_p50_med / fpc / 3` como coste por audio libre de sesgo. La corrección de unidades 60/20 ms sí es válida; la conclusión cuantitativa de la afirmación 10 queda abierta.

2. **Es falsa la afirmación de que el cancel siempre agota 200 ms.** En la ventana estable de 60 ms, N=10, encuentro **81 de 254** cancelaciones con `reason=barge-in` inferiores a 200 ms. Hay una de **1,7 ms** en `resultados/crudo/20261019T210000Z/r1_n010/gb.log`, línea 4747. La mediana cercana a 202 ms se confirma; no demuestra que cada cancelación alcance el timeout ni identifica por sí sola cuánto tiempo corresponde a ese `wait_for`.

3. **Los ~1,5 s atribuidos a colas no están medidos.** La afirmación 3 resta la mediana de p90 del probe, 2.469 ms, y el p50 de `inference_ms`, 912 ms. Son estadísticos de muestras diferentes: esa resta no localiza tiempo antes de la inferencia. El código confirma gate, executor y lock, pero falta instrumentación para repartir sus tiempos. Tampoco un mínimo de 854 ms demuestra por sí solo espera del lock: incluye preparación de features y ejecución.

4. **`close_attempts − 1` no es una cota superior general de mid-speech.** La propuesta de §7 falla cuando el turno termina por mid-speech: dos inferencias mid-speech pueden cerrar el turno sin inferencia de transición; restar uno daría uno, inferior a las dos realizadas.

5. **"El loop no acumula" a 60 ms es demasiado categórico.** A N=100 reproduzco `inbox` mediano **28** y máximo **532** en la ventana estable. Son 1,68 y 31,92 segundos de audio pendiente, respectivamente. El atraso observado es menor que a 20 ms; los datos no justifican afirmar ausencia de acumulación.

Los resultados centrales de N=100 sí coinciden:

| Métrica, ventana estable | 60 ms | 20 ms |
|---|---:|---:|
| CPU mediana, núcleos | 3,2970 | 4,4787 |
| TTFAB p50 / p90, ms | 4.126 / 7.391 | 2.992 / 5.277 |
| Ping de control p99, ms | 14 | 32 |
| Corte p90, ms | 2.233 | 6.190 |
| Transiciones a silencio/s | 7,311 | 7,901 |
| Σ `close_attempts` / duración | 13,109 | 12,197 |
| `inference_ms` p50, ms | 912,21 | 953,59 |

También confirmé los parámetros efectivos de los 13 escalones, ausencia de errores y muertes del cliente, el fallo de handshake de Steno del ensayo `204420Z`, y que `noisereduce` queda excluido por la guarda de tamaño por mensaje.

Dos precisiones para interpretar el resultado:

- El probe sólo observa lotes y conserva hasta 600 muestras por fase: **la objeción a comparar directamente `cp_*` entre brazos se sostiene**, incluso con el error adicional de `frames`.
- Aplicando literalmente los criterios registrados, **60 ms tiene K=10; 20 ms no tiene ningún escalón sano**, pues N=10 ya presenta `cp_cycle_p90_p90=190,6 ms`. Los 12 hilos del executor siguen siendo una inferencia plausible, no una lectura efectiva del proceso.

La reducción observada de CPU a N=100 es **26,4 %**, acompañada de peor TTFAB a 60 ms. Eso está respaldado; la cobertura exacta, el coste normalizado del job y el reparto causal del cuello de botella requieren corregir las conclusiones indicadas.
