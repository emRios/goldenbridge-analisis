# Resultados por escalón · int8 contra fp32 con mocks · 2026-10-27

Etiquetas: `[MEDIDO]` = medido en esta corrida, con el manifiesto indicado · `[INFERIDO]` = deducido de lo medido, sin medirlo directo.

## Corridas y manifiestos

| brazo | modelo | orden | sello (manifiesto en `resultados/<sello>/manifiesto.txt`) |
|---|---|---|---|
| control corto | fp32 `gpu.onnx` | 100, 300 s | `20261027T011336Z` |
| C_sube | fp32 `gpu.onnx` | 50 → 75 → 100 | `20261027T012224Z` |
| V_sube | int8 `cpu.onnx` | 50 → 75 → 100 | `20261027T015959Z` |
| C_baja | fp32 `gpu.onnx` | 100 → 75 → 50 | `20261027T023736Z` |
| V_baja | int8 `cpu.onnx` | 100 → 75 → 50 | `20261027T031510Z` |

Cada sello trae `manifiesto.txt`, `parametros.txt`, `resumen.tsv` (de `analiza.py` de M1) y, por escalón,
`auditoria/<sello>_r1_nNNN/resumen_auditoria.tsv` (extractor de M1, ventana estable [122, 720] s = 598 s).
Los crudos (`gb.log`, `cliente.tsv`, `stats.tsv`, `mocks_cpu.tsv`) están en `resultados/crudo/` en `.143`, fuera de git.

## Tabla por escalón `[MEDIDO]`

Ventana estable de 598 s. "Inferencias/s" = suma de `close_attempts` de los turnos cerrados ÷ 598 (estima los `run`
asociados a cierres; no cuenta directamente todos los `run`). "Cierres/s" = cierres por silencio ÷ 598.
"A mitad del habla" = turnos cerrados por el chequeo mid-speech; "respaldo" = turnos cerrados por el temporizador
de respaldo (silencio sostenido cuando smart-turn dijo "incompleto").

| N | brazo | inferencias/s | cierres/s | inferencias/turno | `inference_ms` p50 / p90 / mín | turnos cerrados | a mitad del habla | respaldo |
|---|---|---|---|---|---|---|---|---|
| 50 | C_sube fp32 | 8,26 | 5,06 | 1,52 | 89 / 214 / 71 | 3.253 | 224 | 75 |
| 50 | C_baja fp32 | 8,25 | 5,06 | 1,52 | 92 / 226 / 72 | 3.243 | 220 | 76 |
| 50 | V_sube int8 | 8,29 | 5,12 | 1,49 | 41 / 77 / 38 | 3.336 | 275 | 0 |
| 50 | V_baja int8 | 8,32 | 5,14 | 1,49 | 41 / 78 / 38 | 3.339 | 263 | 0 |
| 75 | C_sube fp32 | 12,32 | 7,60 | 1,53 | 232 / 493 / 73 | 4.805 | 259 | 115 |
| 75 | C_baja fp32 | 12,45 | 7,60 | 1,54 | 237 / 521 / 73 | 4.827 | 278 | 131 |
| 75 | V_sube int8 | 12,45 | 7,65 | 1,50 | 49 / 95 / 41 | 4.963 | 384 | 0 |
| 75 | V_baja int8 | 12,27 | 7,62 | 1,49 | 51 / 105 / 42 | 4.938 | 381 | 0 |
| 100 | C_sube fp32 | 13,92 | 7,23 | 1,68 | 858 / 885 / 820 | 4.948 | 616 | 99 |
| 100 | C_baja fp32 | 13,92 | 7,28 | 1,67 | 857 / 886 / 819 | 4.972 | 606 | 95 |
| 100 | V_sube int8 | 16,11 | 9,87 | 1,53 | 68 / 136 / 39 | 6.278 | 382 | 0 |
| 100 | V_baja int8 | 16,00 | 9,90 | 1,52 | 67 / 140 / 39 | 6.274 | 355 | 0 |

**Panel de proveedores antes de cada corrida:** no aplica a Gotenks. Esta prueba usó mocks locales en lugar de
Gotenks/Modal. En su lugar, antes de cada escalón se tomó el `/health` y `/stats` de los tres mocks
(`resultados/crudo/<sello>/<paso>/mocks_pre.txt`): 3 de 3 sanos en los 12 escalones `[MEDIDO]`.

## Lectura de las inferencias por turno

- A 50 llamadas, donde no hay fila en ningún brazo (mínimo 71 ms fp32 y 38 ms int8, cerca del tiempo sin carga),
  las inferencias por turno casi coinciden: 1,52 fp32 contra 1,49 int8, un 2 % `[MEDIDO]`. La diferencia de 1,68
  contra 1,53 a 100 llamadas aparece con la espera: las inferencias por turno del fp32 suben de 1,52 a 1,68 cuando
  la fila crece, y las del int8 se quedan en ~1,5 `[MEDIDO]`. Es efecto de la espera, no de que el int8 decida
  distinto `[INFERIDO]`. Esto corrige lo que se le dijo al dueño el 2026-10-27.
- Los cierres a mitad del habla del fp32 crecen con la espera (224 → ~270 → ~610 de 50 a 100), igual que en M1. A 50
  llamadas el int8 tiene más que el fp32 (269 contra 222 de media) `[MEDIDO]`; no apuntan a menos chequeos por modelo.
- **Los cierres por respaldo no se explican sólo por la espera:** el fp32 ya tiene 75-76 a 50 llamadas, donde casi no
  hay fila, y el int8 tiene 0 en todos los escalones `[MEDIDO]`. El respaldo se dispara cuando smart-turn dijo
  "incompleto" y el silencio sigue; que el fp32 lo dispare y el int8 no es compatible con que los dos modelos
  decidan distinto sobre estos 10 audios `[INFERIDO]`. Son 2-3 % de los turnos; no cambian el veredicto.
