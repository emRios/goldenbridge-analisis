#!/bin/sh
# Arranque del contenedor de GoldenBridge.
#
# Corre la REFERENCIA CONGELADA antes de aceptar tráfico y se niega a servir si
# no pasa. Es lo único que puede detectar el problema que ni el lock de
# dependencias ni los modelos horneados resuelven: que el CPU donde cayó este
# contenedor tenga otro juego de instrucciones.
#
# Medido el 2026-08-24: un procesador sin `avx512_vnni` volteó 6 de 14
# decisiones de fin de turno contra uno con él, con las MISMAS features de
# entrada y la MISMA versión de onnxruntime. El modelo es cuantizado — cada
# valor vive en 256 escalones — así que un redondeo distinto lo empuja al
# escalón vecino en vez de mover la última cifra.
#
# En producción de voz eso es el bot cortando (o no) a alguien a mitad de frase.
# Sin este gate, el contenedor arrancaría igual y nadie se enteraría.
#
# Dos modos de uso:
#   - sin argumentos: verifica y sirve GoldenBridge en GB_PUERTO (docker run);
#   - con argumentos: verifica y hace `exec "$@"`. Es el contrato que Modal
#     exige a un ENTRYPOINT de imagen propia: Modal lo ejecuta pasándole el
#     comando de SU runtime, y si el script no lo ejecuta el runtime nunca
#     arranca. Sin esto, la imagen sólo funciona en Modal quitándole el
#     entrypoint, y entonces el gate no corre.
set -eu

GB_GATE_CASOS_ESPERADOS="${GB_GATE_CASOS_ESPERADOS:-12}"

echo "[arranque] GoldenBridge — $(python -c 'import sys;print(sys.version.split()[0], "GIL:", sys._is_gil_enabled())')"
echo "[arranque] hilos fijados: ORT_INTRA_OP=${GB_ORT_INTRA_OP:-?} OPENBLAS=${OPENBLAS_NUM_THREADS:-?} OMP=${OMP_NUM_THREADS:-?}"
echo "[arranque] TORCH_HOME=${TORCH_HOME:-?}  ·  config=${GB_CONFIG}"

# ── los artefactos tienen que estar ANTES de mirar nada más ─────────────────
# Incluye los dos de Silero: sin vad_probs.npz el módulo del VAD se salta
# completo (ver trampa 3 más abajo).
falta=0
for f in /app/smart-turn/smart-turn-v3.2-cpu.onnx \
         /app/test/reference/features.npz \
         /app/test/reference/stack.json \
         /app/test/reference/vad_probs.npz \
         /app/test/reference/vad_stack.json; do
    [ -f "$f" ] || { echo "[arranque] FALTA: $f" >&2; falta=1; }
done
[ -d "${TORCH_HOME}/hub/snakers4_silero-vad_master" ] || {
    echo "[arranque] FALTA el caché de silero en ${TORCH_HOME}/hub" >&2
    echo "[arranque]   sin eso torch.hub sale a GitHub al arrancar" >&2
    falta=1
}
[ "$falta" -eq 0 ] || { echo "[arranque] ABORTA: faltan artefactos de la imagen" >&2; exit 2; }

# ── el gate ─────────────────────────────────────────────────────────────────
if [ "${GB_SALTAR_GATE:-0}" = "1" ]; then
    echo "[arranque] ⚠ GATE SALTEADO por GB_SALTAR_GATE=1 — sólo para depurar."
    echo "[arranque] ⚠ El comportamiento del modelo en este CPU NO está verificado."
else
    echo "[arranque] verificando la referencia congelada (se esperan ${GB_GATE_CASOS_ESPERADOS} casos, cero saltados)..."
    # CUATRO trampas distintas; las dos primeras ya nos mordieron. Las líneas
    # de abajo son feas a propósito: cada rareza tapa una.
    #
    # 1) La tubería. `pytest | tail` devuelve el código de TAIL, no el de
    #    pytest — tail siempre sale 0, así que un `if ! pytest | tail` NUNCA
    #    detecta el fallo. Pasó en la primera corrida de esta imagen: la
    #    referencia falló con 2 failed + 7 errors y el gate imprimió "OK" y
    #    siguió. Por eso se evalúa el código a mano.
    #
    # 2) El `set -e` de arriba. Una asignación por sustitución de comandos
    #    HEREDA el código del comando, así que `salida=$(pytest ...)` con
    #    pytest fallando mata el script EN ESA LÍNEA y el diagnóstico de abajo
    #    es inalcanzable (probado el 2026-08-31: el contenedor murió mudo). Por
    #    eso se apaga el `set -e` alrededor.
    #
    # 3) Las pruebas saltadas (2026-10-09). Los dos módulos de referencia se
    #    marcan `skipif(not os.path.exists(NPZ))` completos: sin su .npz pytest
    #    salta 6 pruebas y sale con 0. Un salto no verifica nada, así que el
    #    gate exige CERO saltadas, leídas del informe JUnit.
    #
    # 4) El conteo. Si alguien borra o renombra una prueba, pytest sigue en 0.
    #    El gate exige exactamente GB_GATE_CASOS_ESPERADOS casos. Si la suite
    #    cambia a propósito, se actualiza ese número junto con ella.
    #
    # Cualquier duda (informe ilegible, conteo imposible) cuenta como fallo.
    informe="$(mktemp)"
    set +e
    salida=$(python -m pytest test/reference/ test/test_reference_features.py \
                              test/test_reference_vad.py -q --no-header -rs \
                              -p no:cacheprovider --junitxml="$informe" 2>&1)
    codigo=$?
    conteo=$(python - "$informe" <<'PY' 2>/dev/null
import sys
import xml.etree.ElementTree as ET
raiz = ET.parse(sys.argv[1]).getroot()
suites = [raiz] if raiz.tag == "testsuite" else raiz.findall("testsuite")
total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
for s in suites:
    for k in total:
        total[k] += int(s.get(k, 0))
print(total["tests"], total["failures"], total["errors"], total["skipped"])
PY
)
    set -e
    rm -f "$informe"
    echo "$salida" | tail -20

    casos=-1; fallos=-1; errores=-1; saltadas=-1
    if [ -n "$conteo" ]; then
        # shellcheck disable=SC2086
        set -- $conteo "$@"
        casos="$1"; fallos="$2"; errores="$3"; saltadas="$4"
        shift 4
    fi
    echo "[arranque] gate: pytest=${codigo} casos=${casos}/${GB_GATE_CASOS_ESPERADOS} fallos=${fallos} errores=${errores} saltadas=${saltadas}"

    motivo=""
    [ "$codigo" -eq 0 ] || motivo="${motivo} pytest salió con ${codigo};"
    [ "$saltadas" = "0" ] || motivo="${motivo} ${saltadas} pruebas saltadas (debe ser 0);"
    [ "$casos" = "$GB_GATE_CASOS_ESPERADOS" ] || motivo="${motivo} ${casos} casos (se esperaban ${GB_GATE_CASOS_ESPERADOS});"

    if [ -n "$motivo" ]; then
        echo "[arranque] motivo:${motivo}" >&2
        cat >&2 <<'FIN'

[arranque] ══════════════════════════════════════════════════════════════════
[arranque] LA REFERENCIA CONGELADA NO PASÓ. No se sirve tráfico.
[arranque]
[arranque] La causa más probable es que este CPU no tenga el mismo juego de
[arranque] instrucciones que aquel donde se generó la referencia. Con un
[arranque] modelo cuantizado eso MUEVE decisiones de fin de turno: el bot
[arranque] cortaría a la gente distinto, sin error y sin log.
[arranque]
[arranque] Qué mirar, en este orden:
[arranque]   1. test_mismo_juego_de_instrucciones — dice qué bandera falta o
[arranque]      sobra. Hoy sólo `avx512_vnni` tiene efecto medido.
[arranque]   2. test_corpus_intacto — el hash del modelo o del audio cambió.
[arranque]   3. los tests de decisión — si fallan y (1) pasó, el stack se movió.
[arranque]   4. pruebas saltadas o conteo distinto — falta una referencia o se
[arranque]      tocó la suite; eso también deja el modelo sin verificar.
[arranque]
[arranque] NO regenerar la referencia por reflejo: primero hay que decidir cuál
[arranque] hardware es el de producción, porque ese define lo correcto.
[arranque] ══════════════════════════════════════════════════════════════════
FIN
        exit 3
    fi
    echo "[arranque] referencia OK — el modelo decide igual que en producción."
fi

# GB_SOLO_GATE=1: verificar y salir sin servir. Para CI y para la prueba
# negativa del gate; en producción no se define.
if [ "${GB_SOLO_GATE:-0}" = "1" ]; then
    echo "[arranque] GB_SOLO_GATE=1 — gate verificado, no se sirve tráfico."
    exit 0
fi

# ── a servir ────────────────────────────────────────────────────────────────
if [ "$#" -gt 0 ]; then
    # Modal (u otro orquestador) pasa su propio comando: el gate ya corrió.
    echo "[arranque] entregando el control al comando del orquestador: $1"
    exec "$@"
fi
echo "[arranque] iniciando en el puerto ${GB_PUERTO}"
exec python -m src.main --config "${GB_CONFIG}" --port "${GB_PUERTO}"
