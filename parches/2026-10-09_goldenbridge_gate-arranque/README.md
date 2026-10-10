# GoldenBridge · gate de arranque: cero saltadas, conteo y contrato de Modal

| | |
|---|---|
| repo | GoldenBridge |
| commit base | `origin/feat/modal-migration` @ `9c024fda6221fd10f05b4a5ab4aa30fe30e25bbe` (su `docker/arranque.sh` = `fb44e8a`) |
| archivo | `docker/arranque.sh` |
| entregables | `0001-*.patch` (para `git am`) y `arranque.sh` completo (para montar en la prueba) |
| afecta a | la imagen `goldenbridge:314t` |

## Corrección de lo que se dijo antes (2026-10-09)

Se había afirmado que "la imagen de Modal sirve tráfico aunque la referencia falle" por la tubería `pytest | tail`. **Eso es cierto sólo para la rama `api`, no para la imagen.** `[CÓDIGO]` La rama `api` tiene la primera versión del Dockerfile (`6e7843c`). Los arreglos posteriores del 31-ago (`24f9c6b`: tubería; `fb44e8a`: muerte muda por `set -e`; `9c024fd`: `.dockerignore`) están **sólo** en `feat/modal-migration` y nunca llegaron a `api`.

La imagen entregada (`files/goldenbridge-314t.tar.gz`, sha256 verificado, creada 2026-08-31T14:36Z) trae el `arranque` de **`24f9c6b`** `[CÓDIGO]` (comparado por hash de blob contra la capa 13 de la imagen).

## Estado real de cada versión

Simulado en la laptop con un pytest de juguete sobre los scripts reales:

| escenario | `api` (`6e7843c`) | imagen (`24f9c6b`) | rama (`fb44e8a`) | **este parche** |
|---|---|---|---|---|
| referencia correcta | sirve | sirve | sirve | sirve |
| prueba fallida | **sirve** | no sirve, **muere mudo** (código 1) | no sirve, diagnóstico (3) | no sirve, diagnóstico (3) |
| pruebas saltadas (falta `vad_probs.npz`) | **sirve** | **sirve** | **sirve** | no sirve (2 por artefacto, 3 por salto) |
| un caso de menos | sirve | sirve | sirve | no sirve (3) |
| informe ilegible | sirve | sirve | sirve | no sirve (3) |
| Modal pasa su comando al entrypoint | **ignora y lanza GB** | **ignora y lanza GB** | **ignora y lanza GB** | corre el gate y hace `exec "$@"` |

## Qué corrige el parche

1. **Pruebas saltadas.** `test_reference_features.py` L61 y `test_reference_vad.py` L41 marcan su módulo completo `skipif(not os.path.exists(NPZ))`. Sin `vad_probs.npz`, pytest salta 6 pruebas y sale con 0. Ahora se exige **cero saltadas**, leídas del informe JUnit, y el chequeo de artefactos incluye `vad_probs.npz` y `vad_stack.json`. Exposición actual: baja, porque la imagen trae esos archivos; es defensa ante una imagen construida sin ellos.
2. **Conteo.** Se exige exactamente `GB_GATE_CASOS_ESPERADOS` casos (12: 6 de smart-turn, 6 de Silero). Si la suite cambia a propósito, se actualiza ese número con ella.
3. **Contrato de Modal** `[DOCUMENTACIÓN]` ([Modal, existing images](https://modal.com/docs/guide/existing-images)): Modal ejecuta el `ENTRYPOINT` de una imagen propia pasándole el comando de su runtime, y exige que el script haga `exec "$@"`. El arranque lo ignoraba y lanzaba GoldenBridge, así que con `Image.from_registry` el runtime de Modal nunca arranca, o hay que quitar el entrypoint (`.entrypoint([])`) y entonces **el gate no corre en Modal**. Ahora: con argumentos, verifica y hace `exec "$@"`; sin argumentos, sirve como antes.
4. `GB_SOLO_GATE=1`: verifica y sale sin servir (CI y prueba negativa).

## Lo que queda abierto `[ABIERTO]`

**Si el gate corre hoy en Modal** depende del archivo de despliegue de Modal, que no está en ningún repo. El traspaso (`docker/README.md` de `feat/modal-migration`) muestra `Image.from_registry(...)` + `@modal.asgi_app()` sin `.entrypoint([])`: con ese arranque, eso no podría funcionar, así que el despliegue real hace otra cosa. Para cerrarlo: leer el archivo de despliegue, o buscar en los logs de un arranque en frío de Modal la línea `[arranque] verificando la referencia congelada`.

## Aplicar

```bash
cd /srv/GoldenBridge
git fetch origin
git switch -c fix/gate-arranque origin/feat/modal-migration
git am /ruta/a/goldenbridge-analisis/parches/2026-10-09_goldenbridge_gate-arranque/0001-*.patch
```

Y decidir qué hacer con `api`: hoy no tiene ninguno de los arreglos de Docker del 31-ago.

## Revertir

`git revert <commit del parche>`, o reconstruir la imagen desde `9c024fd`.
