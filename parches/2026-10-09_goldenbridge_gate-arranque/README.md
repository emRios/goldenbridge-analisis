# GoldenBridge · gate de arranque que bloquea de verdad

| | |
|---|---|
| repo | GoldenBridge |
| commit base | `api@08932a64645bff74030ad2bd63543dd2334b8101` |
| archivo | `docker/arranque.sh` |
| parche | `0001-fix-arranque-el-gate-de-referencia-ahora-bloquea-de-.patch` |
| afecta a | la imagen `goldenbridge:314t` (despliegue en Modal). No afecta a `.143`: su `deploy.sh` no usa tubería y ya bloquea |

## Qué corrige

1. `[CÓDIGO]` L43-44 `if ! python -m pytest … | tail -20`: en `sh` sin `pipefail` el estado es el de `tail` (0), así que un fallo de la referencia nunca impedía servir.
2. `[CÓDIGO]` `test_reference_features.py` L61 y `test_reference_vad.py` L41 marcan su módulo completo como `skipif(not os.path.exists(NPZ))`. Una referencia ausente se convertía en pruebas saltadas, y pytest salía con 0. El chequeo de artefactos de L25-35 no buscaba `vad_probs.npz` ni `vad_stack.json`.

## Cómo decide ahora

Lee el informe JUnit de pytest y exige: código 0, **cero saltadas**, y **exactamente** `GB_GATE_CASOS_ESPERADOS` casos (12). Cualquier duda cuenta como fallo y el contenedor sale con 3 sin servir. Nuevo: `GB_SOLO_GATE=1` para verificar sin servir.

**Si alguien agrega o quita pruebas de referencia**, hay que actualizar `GB_GATE_CASOS_ESPERADOS` (en el script o como variable de entorno de la imagen). Es intencional: que el gate note el cambio.

## Prueba

- Simulación local con pytest de juguete: el viejo sirve en los cinco escenarios (éxito, fallo, saltadas, un caso menos, informe ilegible); el nuevo sólo en éxito.
- Prueba con la imagen real: `experimentos/2026-10-09_m0-gate/`.

## Aplicar

```bash
cd /srv/GoldenBridge
git switch -c fix/gate-arranque 08932a64645bff74030ad2bd63543dd2334b8101
git am /ruta/a/goldenbridge-analisis/parches/2026-10-09_goldenbridge_gate-arranque/0001-*.patch
```

## Revertir

```bash
git revert <commit del parche>
```

o volver a construir la imagen desde `api@08932a6`. El parche no cambia ninguna otra parte de la imagen.
