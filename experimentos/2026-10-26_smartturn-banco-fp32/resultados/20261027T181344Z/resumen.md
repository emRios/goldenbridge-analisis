# Resumen del banco · 20261027T181344Z

Condiciones: 45 · inválidas: 0


| serie | P | sesión | Silero | tasa | reps | caudal/s | Run p50 / p90 / p99 ms | CPU del hilo p50 ms | espera p50 / p90 / p99 ms | paridad máx | núcleos | Silero núcleos | memoria sesiones MB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 1 | compartida |  |  | 3 | 17,8 | 55,8 / 57,5 / 60,6 | 55,8 |  /  /  | 0.0e+00 | 1,00 |  | 57 |
| A | 2 | compartida |  |  | 3 | 34,5 | 57,7 / 59,3 / 62,4 | 57,7 |  /  /  | 0.0e+00 | 2,00 |  | 56 |
| A | 4 | compartida |  |  | 3 | 63,3 | 62,6 / 63,5 / 88,6 | 62,6 |  /  /  | 0.0e+00 | 4,00 |  | 55 |
| D | 1 | compartida |  | 8,9 | 3 | 9,3 | 56,6 / 58,6 / 61,9 | 56,6 | 62,4 / 152,1 / 315,3 | 0.0e+00 | 0,53 |  | 55 |
| D | 1 | compartida |  | 13,4 | 3 | 13,7 | 55,2 / 56,9 / 59,7 | 55,2 | 113,7 / 256,0 / 497,4 | 0.0e+00 | 0,76 |  | 58 |
| D | 1 | compartida |  | 14,3 | 3 | 14,5 | 55,9 / 57,6 / 60,8 | 55,9 | 141,8 / 339,9 / 573,8 | 0.0e+00 | 0,81 |  | 55 |
| D | 1 | compartida |  | 16,0 | 3 | 16,3 | 55,8 / 57,6 / 60,2 | 55,8 | 308,4 / 1156,1 / 1379,0 | 0.0e+00 | 0,92 |  | 57 |
| D | 2 | compartida |  | 17,2 | 3 | 17,3 | 57,6 / 59,7 / 63,5 | 57,6 | 58,7 / 97,0 / 177,6 | 0.0e+00 | 1,01 |  | 56 |
| D | 2 | compartida |  | 25,9 | 3 | 24,9 | 57,5 / 60,0 / 63,3 | 57,5 | 75,2 / 160,8 / 240,7 | 0.0e+00 | 1,44 |  | 56 |
| D | 2 | compartida |  | 27,6 | 3 | 26,6 | 57,3 / 59,7 / 62,8 | 57,3 | 85,3 / 179,2 / 277,2 | 0.0e+00 | 1,53 |  | 54 |
| D | 2 | compartida |  | 31,0 | 3 | 29,9 | 57,8 / 59,7 / 62,8 | 57,8 | 122,8 / 330,6 / 713,0 | 0.0e+00 | 1,74 |  | 56 |
| D | 4 | compartida |  | 31,6 | 3 | 30,0 | 61,4 / 63,4 / 94,1 | 61,3 | 61,7 / 78,2 / 111,9 | 0.0e+00 | 1,85 |  | 57 |
| D | 4 | compartida |  | 47,5 | 3 | 45,7 | 62,5 / 63,7 / 88,9 | 62,5 | 63,9 / 115,6 / 162,4 | 0.0e+00 | 2,88 |  | 55 |
| D | 4 | compartida |  | 50,6 | 3 | 48,6 | 62,3 / 63,4 / 88,9 | 62,3 | 68,0 / 128,3 / 185,8 | 0.0e+00 | 3,05 |  | 56 |
| D | 4 | compartida |  | 56,9 | 3 | 54,7 | 62,5 / 63,5 / 88,4 | 62,5 | 91,6 / 180,9 / 268,2 | 0.0e+00 | 3,45 |  | 55 |

## Criterios preregistrados

- **Paridad** (máx |Δp| ≤ 1e-6, 0 volteos en 0,35 y 0,85): CUMPLE en todas las condiciones válidas.
- **Escala, P = 1:** caudal 17,8/s contra 0,8 × 1 × 17,8 = 14,3/s → escala (100 % de lo lineal).
- **Escala, P = 2:** caudal 34,5/s contra 0,8 × 2 × 17,8 = 28,5/s → escala (97 % de lo lineal).
- **Escala, P = 4:** caudal 63,3/s contra 0,8 × 4 × 17,8 = 57,1/s → escala (89 % de lo lineal).
- **Mejor P** (regla del README): 4.
- **Mejor forma de sesión con P = 4:** compartida.
- **Reposo:** CPU máxima en los 5 s previos = 0,20 % de un núcleo (OK).
