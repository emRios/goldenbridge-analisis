# <AAAA-MM-DD>_<paso>-<tema>

> Copia de `experimentos/_plantilla/`. Completa todas las secciones **antes** de la primera corrida.

## Paso de la estrategia

`<M0 | M1 | M2 | C4-A | C4-S1 | …>` — ver `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md`.

## Hipótesis

Una frase falsable.

## Variable que cambia

Exactamente una. Todo lo demás queda fijo y se lista aquí: commit de GoldenBridge, imagen, config, cadencia, corpus, reserva de CPU.

## Criterios preregistrados

| criterio | aceptación | rechazo |
|---|---|---|
| corrida válida | lag del generador p99 ≤ 5 ms; cero muestras perdidas | cualquiera de los dos falla ⇒ corrida inválida, no se interpreta |
| … | … | … |

## Cómo correrlo (servidor)

```bash
git fetch && git switch <rama> && git pull
cd experimentos/<esta-carpeta>
./run.sh                      # aborta si hay llamadas activas
FORZAR=1 ./run.sh             # sólo con decisión explícita
```

Variables de entorno que lee `run.sh`: `GB_DIR` (checkout de GoldenBridge), `GB_IMAGEN` (imagen Docker), `GB_HEALTH` (URL de `/health`), `GB_CONFIG` (config efectiva a registrar).

## Resultado

Se completa al terminar: ruta del manifiesto, tabla de resultados, veredicto contra los criterios y qué decide para el siguiente paso.
