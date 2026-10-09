# mediaworker — laboratorio por componentes

Código del carril C de la [estrategia](../doctos/ESTRATEGIA_MEDIAWORKER_20261009.md). Vacío hasta empezar C0.

| carpeta | pasos | contenido |
|---|---|---|
| `core/` | C0-C3 | componentes **sans-I/O**: códec µ-law y soxr, Silero y máquina de estados del VAD, log-mel y SmartTurn, gates de barge-in y watchdog |
| `lab/` | C4 | dueño por llamada (escenarios A, S1, B), simuladores de Steno y Ursula, cliente de carga adaptado a 60 ms |
| `parity/` | todos | trazas doradas grabadas desde GoldenBridge Python y la compuerta de paridad por capa |

## Reglas de diseño de `core/` (no negociables)

Si se viola una, el componente queda atado a una estructura de hilos y la comparación A/S/B deja de medir sólo la estructura.

- Ningún estado global mutable ni singletons; el estado por sesión vive en un objeto que el dueño presta.
- Ningún hilo, bloqueo ni E/S dentro de un componente.
- El reloj se inyecta: cada llamada recibe un instante monotónico, nunca lee la hora.
- Ninguna asignación de memoria por trama después del warm-up, verificada con contador en los microbenchmarks.
- Resultados como estructuras de datos, nunca callbacks hacia el dueño.
- Recursos de inferencia sólo por handle; la política de concurrencia vive fuera.
- Sin excepciones en el camino por trama. `ORT_NO_EXCEPTIONS` está prohibido: aborta el proceso.

## Paridad

Cada componente pasa su compuerta contra GoldenBridge `api@08932a6` antes de que empiece el siguiente. Tolerancias: µ-law y soxr bit a bit; Silero `1e-3` por chunk y transiciones idénticas; features de SmartTurn `1e-3` con decisiones exactas y drift marginal < `0,05`. Detalle en la estrategia §4.
