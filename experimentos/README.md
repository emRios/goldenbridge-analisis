# experimentos

Una carpeta por experimento: `AAAA-MM-DD_<paso>-<tema>/` (por ejemplo `2026-10-14_m1-linea-base/`). Para crear uno nuevo se copia `_plantilla/`.

## Contrato de cada experimento

| archivo | obligatorio | contenido |
|---|---|---|
| `README.md` | sí | hipótesis, variable que cambia (sólo una), criterios de aceptación preregistrados, cómo correrlo, cómo leer el resultado |
| `run.sh` | sí | único punto de entrada en el servidor; graba el manifiesto antes de medir |
| `correr.sh` | sí | la medición en sí; `run.sh` lo invoca con el directorio de salida |
| `resultados/<fecha-hora>/manifiesto.txt` | lo genera `run.sh` | commits, imagen, CPU, config, versiones |
| `resultados/<fecha-hora>/*.tsv` | lo genera `correr.sh` | resúmenes pequeños que sí se versionan |
| `resultados/crudo/` | — | logs, dumps y capturas: **no se versiona** |

## Reglas

- Una variable por experimento (regla 3 de `CLAUDE.md`). Si cambian dos cosas, son dos experimentos.
- Criterios escritos **antes** de correr. No se editan después de ver resultados; si cambian, se documenta por qué en el README y se re-corre.
- `run.sh` aborta si GoldenBridge tiene llamadas activas, salvo `FORZAR=1`.
- Un resultado sólo se cita como `[MEDIDO]` con la ruta de su manifiesto.
