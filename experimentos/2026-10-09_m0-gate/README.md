# 2026-10-09 · M0 · gate de arranque de la imagen

## Paso de la estrategia

**M0** — `doctos/ESTRATEGIA_MEDIAWORKER_20261009.md` §3. El laboratorio se apoya en este gate para certificar que el modelo decide igual que en producción.

## Qué se sabe antes de correr

Detalle en `parches/2026-10-09_goldenbridge_gate-arranque/README.md`. En corto:

- La imagen entregada (`goldenbridge:314t`, creada 2026-08-31) trae el arranque de `24f9c6b` (rama `feat/modal-migration`). **Su gate sí bloquea cuando una prueba falla**, pero muere sin diagnóstico. El defecto de la tubería existe sólo en la rama `api`.
- Quedan dos huecos en la imagen: **pruebas saltadas pasan como éxito** (sin `vad_probs.npz` se saltan las 6 de Silero), y **el arranque no cumple el contrato de Modal** (ignora el comando que Modal le pasa).

## Hipótesis

Con la imagen real en una CPU con `avx512_vnni`:
- el arranque de la imagen **falla cerrado** con el modelo o la referencia alterados, pero **sirve** con la referencia del VAD ausente y **no ejecuta** el comando de Modal;
- el arranque del parche rechaza los cuatro casos alterados, acepta el normal y ejecuta el comando de Modal después del gate.

## Variable que cambia

Sólo el script de arranque, montado encima del de la imagen. Imagen, modelos, CPU y suite quedan fijos. Los artefactos alterados se montan de sólo lectura; los contenedores corren sin red y se borran al terminar.

## Criterios preregistrados

| caso | qué se altera | NUEVO: esperado | VIEJO (imagen): esperado según el código |
|---|---|---|---|
| `normal` | nada | **0** y "referencia OK" | sirve |
| `modelo_alterado` | 64 bytes del `.onnx` | **3** | falla cerrado, mudo |
| `decision_alterada` | una probabilidad de `stack.json` | **3** | falla cerrado, mudo |
| `referencia_vad_ausente` | sin `vad_probs.npz` | **2** | **sirve** → `HUECO CONFIRMADO` |
| `conteo_distinto` | se esperan 13 casos | **3** | no aplica |
| `modal_entrypoint` | se pasa un comando al entrypoint, como hace Modal | **0** y ejecuta el comando | lanza GoldenBridge → `NO CUMPLE CONTRATO MODAL` |

**Aceptado** si los seis resultados del NUEVO coinciden. Las filas del VIEJO no deciden la aceptación: confirman con la imagen real lo que se dedujo del código.

Si la CPU no tiene `avx512_vnni`, el `normal` del NUEVO debe dar 3 y la corrida no es válida para aceptar; el manifiesto lo registra.

## Preparar (una vez, en el servidor)

**1. El repo de análisis**

```bash
git clone https://github.com/emRios/goldenbridge-analisis.git
cd goldenbridge-analisis
git config core.autocrlf false
git switch paso/m0-gate
```

**2. La imagen.** El archivo está en la laptop, en `files/` (no va al repo: 493 MB). Copiarlo al servidor y cargarlo:

```bash
# desde la laptop
scp -i ~/.ssh/ai_key_eraxiom \
    files/goldenbridge-314t.tar.gz files/goldenbridge-314t.tar.gz.sha256 \
    eraxiom@16.58.43.143:/tmp/

# en el servidor
cd /tmp
sha256sum -c goldenbridge-314t.tar.gz.sha256      # tiene que decir OK
docker load < goldenbridge-314t.tar.gz            # carga goldenbridge:314t
docker image inspect goldenbridge:314t --format '{{.Id}} {{.Created}}'
```

El ID esperado es `sha256:98bfc202767ddaff596ad726958d6ddffc010bf65357a1ee7efce07e1ab86330` y la fecha `2026-08-31T14:36:34Z`. Con el almacén de imágenes de containerd, Docker puede mostrar en cambio el digest del índice, `sha256:fe6fc8bc8a65c087fdeabde25307345e2b98193efae921ccdf1b421b36da558e`; cualquiera de los dos confirma que es la misma imagen.

**3. Requisitos**

```bash
grep -qw avx512_vnni /proc/cpuinfo && echo "VNNI OK"
command -v python3 && docker --version
```

## Correr

```bash
cd experimentos/2026-10-09_m0-gate
./run.sh
```

- No genera carga ni toca el servicio. Si `run.sh` aborta por llamadas activas, se puede correr con `FORZAR=1` sin riesgo para producción.
- Duración: unos minutos (once contenedores).
- Resultado: `resultados/<fecha>/resumen.tsv` y `manifiesto.txt`; logs completos por caso en `resultados/crudo/<fecha>/`.

## Devolver el resultado

```bash
git add experimentos/2026-10-09_m0-gate/resultados/*/resumen.tsv \
        experimentos/2026-10-09_m0-gate/resultados/*/manifiesto.txt \
        experimentos/2026-10-09_m0-gate/resultados/*/*.sha256
git commit -m "M0: resultado de la prueba negativa del gate"
git push
```

O pegame el `resumen.tsv` y el manifiesto, y lo subo yo.

## Lo que esta prueba no responde

Si el gate corre **hoy** en Modal. Eso depende del archivo de despliegue de Modal, que no está en ningún repo. Se cierra leyendo ese archivo o buscando `[arranque] verificando la referencia congelada` en los logs de un arranque en frío del endpoint.

## Resultado

_Pendiente de la corrida en el servidor._
