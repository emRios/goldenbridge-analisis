# 2026-10-09 · M0 · gate de arranque de la imagen

## Paso de la estrategia

**M0** — `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md` §3. Primer paso del plan: el laboratorio se apoya en este gate para certificar que el modelo decide igual que en producción.

## El defecto

`[CÓDIGO]` `docker/arranque.sh` (GoldenBridge `api@08932a6`) L43-44 evalúa `if ! python -m pytest … | tail -20`. En `sh` sin `pipefail` el estado de una tubería es el del último mandato; `tail` sale con 0 y la rama "no se sirve tráfico" es inalcanzable. Además el chequeo de artefactos (L25-35) no incluye `vad_probs.npz` ni `vad_stack.json`, y sin `vad_probs.npz` pytest **salta** las 6 pruebas de Silero y sale con 0.

Simulado en la laptop con un pytest de juguete: el arranque viejo sirve tráfico en los cinco escenarios (éxito, prueba fallida, pruebas saltadas, un caso de menos, informe ilegible); el nuevo sólo en el de éxito. Este experimento lo verifica **con la imagen real y los modelos reales**.

## El parche

`parches/2026-10-09_goldenbridge_gate-arranque/0001-*.patch`, generado sobre `api@08932a6`; aplica limpio. Cambia sólo `docker/arranque.sh`:

- pytest sin tubería, con su código de salida guardado;
- conteo leído del informe JUnit: exige **0 saltadas** y **exactamente 12 casos** (`GB_GATE_CASOS_ESPERADOS`, 6 de smart-turn y 6 de Silero); un informe ilegible cuenta como fallo;
- el chequeo de artefactos agrega `vad_probs.npz` y `vad_stack.json`;
- `GB_SOLO_GATE=1` verifica y sale sin servir (para esta prueba y para CI).

## Hipótesis

Con la imagen `goldenbridge:314t` en una CPU con `avx512_vnni`, el arranque nuevo **acepta** el caso normal y **rechaza** los cuatro casos alterados; el arranque viejo da por buena la referencia en al menos uno de los alterados.

## Variable que cambia

Sólo el script de arranque (montado encima del de la imagen). Imagen, modelos, CPU y suite de pruebas quedan fijos. Los casos alterados se montan de sólo lectura; los contenedores corren sin red y se borran al terminar.

## Criterios preregistrados

| caso | qué se altera | NUEVO: código esperado | VIEJO: qué demostraría el defecto |
|---|---|---|---|
| `normal` | nada | **0** y "referencia OK" | — (debe servir) |
| `modelo_alterado` | 64 bytes del `.onnx` cambiados | **3** (pytest falla) | anuncia el puerto |
| `decision_alterada` | una probabilidad de `stack.json` invertida | **3** (decisión no coincide) | anuncia el puerto |
| `referencia_vad_ausente` | `vad_probs.npz` retirado | **2** (falta artefacto) | anuncia el puerto (pytest saltó el VAD) |
| `conteo_distinto` | `GB_GATE_CASOS_ESPERADOS=13` | **3** (conteo) | no aplica: la variable no existe en el viejo |

**Aceptado** si los cinco códigos del NUEVO coinciden con lo esperado. Lo del VIEJO no decide la aceptación: documenta el defecto con evidencia real (`BUG CONFIRMADO` en el resumen).

Si la CPU del servidor no tiene `avx512_vnni`, el caso `normal` debe dar 3 en el NUEVO y la corrida no es válida para aceptar; el manifiesto lo registra.

## Preparar (una vez, en el servidor)

```bash
# el repo de análisis
git clone https://github.com/emRios/goldenbridge-analisis.git
cd goldenbridge-analisis
git config core.autocrlf false
git fetch && git switch paso/m0-gate

# requisitos: docker, python3 y la imagen
docker image inspect goldenbridge:314t >/dev/null && echo "imagen OK"
grep -qw avx512_vnni /proc/cpuinfo && echo "VNNI OK"
```

Si la imagen no existe, se construye como indica el `Dockerfile` de GoldenBridge (necesita los modelos en `modelos/`):

```bash
git clone -b api <repo GoldenBridge> /tmp/gb-ctx && cd /tmp/gb-ctx
cp -a /opt/modelos/goldenbridge modelos
docker build -t goldenbridge:314t .
```

## Correr

```bash
cd experimentos/2026-10-09_m0-gate
GB_DIR=/srv/GoldenBridge ./run.sh
```

- No necesita ventana fuera de campaña: no genera carga ni toca el servicio. Si `run.sh` aborta por llamadas activas, se puede correr con `FORZAR=1` sin riesgo para producción.
- Duración: unos minutos (diez contenedores, cada uno corre las 12 pruebas).
- Lo que interesa: `resultados/<fecha>/resumen.tsv` y `manifiesto.txt`. Logs completos por caso en `resultados/crudo/<fecha>/`.

## Devolver el resultado

```bash
git add experimentos/2026-10-09_m0-gate/resultados/*/resumen.tsv \
        experimentos/2026-10-09_m0-gate/resultados/*/manifiesto.txt \
        experimentos/2026-10-09_m0-gate/resultados/*/arranque_nuevo.sha256
git commit -m "M0: resultado de la prueba negativa del gate"
git push
```

O pegame el `resumen.tsv` y el manifiesto, y lo subo yo.

## Después de aceptar

Aplicar el parche en el repo de GoldenBridge y reconstruir la imagen:

```bash
cd /srv/GoldenBridge
git switch -c fix/gate-arranque 08932a6
git am /ruta/a/goldenbridge-analisis/parches/2026-10-09_goldenbridge_gate-arranque/0001-*.patch
# luego: build de la imagen y redeploy en Modal según su procedimiento
```

## Resultado

_Pendiente de la corrida en el servidor._
