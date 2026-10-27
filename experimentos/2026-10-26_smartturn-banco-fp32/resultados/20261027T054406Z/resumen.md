# Resumen del banco · 20261027T054406Z

Condiciones: 1 · inválidas: 0


| serie | P | sesión | Silero | tasa | reps | caudal/s | Run p50 / p90 / p99 ms | CPU del hilo p50 ms | espera p50 / p90 / p99 ms | paridad máx | núcleos | Silero núcleos | memoria sesiones MB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 1 | compartida |  |  | 1 | 16,0 | 62,6 / 63,6 / 64,5 | 62,5 |  /  /  | 0.0e+00 | 1,00 |  | 56 |

## Criterios preregistrados

- **Paridad** (máx |Δp| ≤ 1e-6, 0 volteos en 0,35 y 0,85): CUMPLE en todas las condiciones válidas.
- **Escala, P = 1:** caudal 16,0/s contra 0,8 × 1 × 16,0 = 12,8/s → escala (100 % de lo lineal).
- **Mejor P** (regla del README): 1.
- **Mejor forma de sesión con P = 1:** compartida.
- **Reposo:** CPU máxima en los 5 s previos = 0,00 % de un núcleo (OK).
