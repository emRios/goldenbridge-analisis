# Resumen del banco · 20261027T142841Z

Condiciones: 51 · inválidas: 0


| serie | P | sesión | Silero | tasa | reps | caudal/s | Run p50 / p90 / p99 ms | CPU del hilo p50 ms | espera p50 / p90 / p99 ms | paridad máx | núcleos | Silero núcleos | memoria sesiones MB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 1 | compartida |  |  | 3 | 17,7 | 56,3 / 58,3 / 61,0 | 56,3 |  /  /  | 0.0e+00 | 1,00 |  | 57 |
| A | 2 | compartida |  |  | 3 | 34,4 | 57,9 / 60,1 / 62,4 | 57,9 |  /  /  | 0.0e+00 | 2,00 |  | 58 |
| A | 4 | compartida |  |  | 3 | 63,3 | 62,6 / 63,5 / 88,6 | 62,6 |  /  /  | 0.0e+00 | 4,00 |  | 57 |
| A | 8 | compartida |  |  | 3 | 63,2 | 125,5 / 127,0 / 169,0 | 125,4 |  /  /  | 0.0e+00 | 7,90 |  | 59 |
| B | 4 | compartida |  |  | 3 | 63,6 | 62,3 / 63,2 / 88,1 | 62,3 |  /  /  | 0.0e+00 | 4,00 |  | 56 |
| B | 4 | por_hilo |  |  | 3 | 63,0 | 63,0 / 63,7 / 88,6 | 63,0 |  /  /  | 0.0e+00 | 4,00 |  | 170 |
| C | 4 | compartida | 25 |  | 3 | 62,3 | 63,6 / 65,0 / 88,1 | 63,6 |  /  /  | 0.0e+00 | 4,29 | 0,31 | 55 |
| C | 4 | compartida | 50 |  | 3 | 60,7 | 65,6 / 67,2 / 88,2 | 65,6 |  /  /  | 0.0e+00 | 4,57 | 0,60 | 54 |
| C | 4 | compartida | 100 |  | 3 | 57,7 | 69,1 / 71,2 / 88,2 | 69,1 |  /  /  | 0.0e+00 | 5,15 | 1,21 | 57 |
| D | 1 | compartida |  | 8,8 | 3 | 9,2 | 57,4 / 59,3 / 61,9 | 57,4 | 64,1 / 154,9 / 322,5 | 0.0e+00 | 0,53 |  | 55 |
| D | 1 | compartida |  | 13,3 | 3 | 13,6 | 57,1 / 58,8 / 60,9 | 57,1 | 131,0 / 310,0 / 550,6 | 0.0e+00 | 0,78 |  | 57 |
| D | 1 | compartida |  | 15,9 | 3 | 16,2 | 60,3 / 62,1 / 63,9 | 60,3 | 827,0 / 2409,6 / 2629,5 | 0.0e+00 | 0,98 |  | 56 |
| D | 1 | compartida |  | 17,7 | 3 | 16,9 | 59,1 / 60,9 / 62,7 | 59,1 | 3554,6 / 4449,9 / 4717,0 | 0.0e+00 | 1,00 |  | 56 |
| D | 4 | compartida |  | 31,6 | 3 | 30,0 | 61,2 / 63,2 / 91,5 | 61,2 | 61,5 / 77,4 / 108,1 | 0.0e+00 | 1,85 |  | 58 |
| D | 4 | compartida |  | 47,5 | 3 | 45,8 | 61,9 / 63,1 / 89,3 | 61,9 | 63,3 / 113,5 / 159,3 | 0.0e+00 | 2,85 |  | 57 |
| D | 4 | compartida |  | 57,0 | 3 | 54,7 | 62,2 / 63,3 / 88,5 | 62,2 | 89,3 / 178,8 / 264,6 | 0.0e+00 | 3,44 |  | 56 |
| D | 4 | compartida |  | 63,3 | 3 | 60,9 | 62,3 / 63,3 / 88,2 | 62,3 | 207,7 / 415,6 / 537,0 | 0.0e+00 | 3,83 |  | 55 |

## Criterios preregistrados

- **Paridad** (máx |Δp| ≤ 1e-6, 0 volteos en 0,35 y 0,85): CUMPLE en todas las condiciones válidas.
- **Escala, P = 1:** caudal 17,7/s contra 0,8 × 1 × 17,7 = 14,1/s → escala (100 % de lo lineal).
- **Escala, P = 2:** caudal 34,4/s contra 0,8 × 2 × 17,7 = 28,3/s → escala (97 % de lo lineal).
- **Escala, P = 4:** caudal 63,3/s contra 0,8 × 4 × 17,7 = 56,6/s → escala (89 % de lo lineal).
- **Escala, P = 8:** caudal 63,2/s contra 0,8 × 8 × 17,7 = 113,1/s → no escala (45 % de lo lineal).
- **Mejor P** (regla del README): 4.
- **Mejor forma de sesión con P = 4:** compartida.
- **Contraste con M1** (S ≈ 72 ms a 100 llamadas): la serie C a 100 llamadas dio `Run` p50 69,1 ms. La carga de Silero usó 1,21 núcleos, contra ~3,2 de GB a 100 llamadas (38 %). Lecturas posibles: ver README.
- **Reposo:** CPU máxima en los 5 s previos = 0,00 % de un núcleo (OK).
