#!/usr/bin/env bash
# M0 — prueba negativa del gate de arranque de la imagen goldenbridge.
#
# Corre la MISMA imagen con dos arranques: el que trae la imagen (VIEJO) y el
# del parche (NUEVO, montado encima de /usr/local/bin/arranque). Para cada caso
# se monta un artefacto alterado de sólo lectura y se observa si el arranque
# llega a servir. Nada toca GoldenBridge ni la imagen: contenedores sin red,
# borrados al terminar.
#
# Lo invoca run.sh con el directorio de salida como $1.
set -euo pipefail

SALIDA="$1"
CRUDO="$(dirname "$SALIDA")/crudo/$(basename "$SALIDA")"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$AQUI" rev-parse --show-toplevel)"
NUEVO_SRC="$REPO/parches/2026-10-09_goldenbridge_gate-arranque/arranque.sh"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:314t}"
# El arranque VIEJO no conoce GB_SOLO_GATE: tras el gate intenta servir. Se lo
# observa hasta que anuncia el puerto o termina, con este tope.
PLAZO_VIEJO="${PLAZO_VIEJO:-180}"
mkdir -p "$CRUDO"
log() { printf '[m0 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# ── precondiciones ──────────────────────────────────────────────────────────
command -v docker >/dev/null || { log "ERROR: docker no está instalado"; exit 10; }
docker image inspect "$GB_IMAGEN" >/dev/null 2>&1 || {
    log "ERROR: no existe la imagen $GB_IMAGEN. Ver README §Preparar."; exit 11; }
[ -f "$NUEVO_SRC" ] || { log "ERROR: falta $NUEVO_SRC"; exit 12; }
command -v python3 >/dev/null || { log "ERROR: hace falta python3 en el host"; exit 13; }
grep -qw avx512_vnni /proc/cpuinfo || log "AVISO: CPU sin avx512_vnni — el caso 'normal' DEBE fallar aquí"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
NUEVO="$TMP/arranque_nuevo.sh"
tr -d '\r' < "$NUEVO_SRC" > "$NUEVO"; chmod 755 "$NUEVO"
sha256sum "$NUEVO" | tee "$SALIDA/arranque_nuevo.sha256"

# ── artefactos de la imagen y el arranque que trae ──────────────────────────
c="$(docker create "$GB_IMAGEN")"
docker cp "$c:/usr/local/bin/arranque" "$TMP/arranque_imagen.sh"
docker cp "$c:/app/smart-turn/smart-turn-v3.2-cpu.onnx" "$TMP/modelo.onnx"
docker cp "$c:/app/test/reference" "$TMP/reference_sin_vad"
docker rm "$c" >/dev/null
sha256sum "$TMP/arranque_imagen.sh" | sed "s#$TMP/##" | tee "$SALIDA/arranque_imagen.sha256"
cp "$TMP/reference_sin_vad/stack.json" "$TMP/stack.json"

# modelo_alterado: mismo tamaño, 64 bytes cambiados en la mitad del archivo
cp "$TMP/modelo.onnx" "$TMP/modelo_alterado.onnx"
python3 - "$TMP/modelo_alterado.onnx" <<'PY'
import sys
p = sys.argv[1]
b = bytearray(open(p, "rb").read())
m = len(b) // 2
for i in range(m, m + 64):
    b[i] ^= 0x5A
open(p, "wb").write(b)
PY

# decision_alterada: la referencia afirma la probabilidad contraria para el
# primer caso, así que una decisión de referencia deja de coincidir
python3 - "$TMP/stack.json" "$TMP/stack_alterado.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
k = sorted(d["probabilidades"])[0]
d["probabilidades"][k] = 0.99 if d["probabilidades"][k] < 0.5 else 0.01
json.dump(d, open(sys.argv[2], "w"), indent=2)
print("alterado", k)
PY

# referencia_vad_ausente: copia de /app/test/reference sin vad_probs.npz.
# pytest salta las 6 pruebas de Silero (pytestmark skipif) y sale con 0.
rm -f "$TMP/reference_sin_vad/vad_probs.npz"
chmod -R a+rX "$TMP"

# ── casos ───────────────────────────────────────────────────────────────────
# MONTA: montajes extra · ENVX: variables extra · ESPERA_NUEVO: código esperado
declare -A MONTA ENVX ESPERA_NUEVO
MONTA[normal]="";                ENVX[normal]="";   ESPERA_NUEVO[normal]=0
MONTA[modelo_alterado]="-v $TMP/modelo_alterado.onnx:/app/smart-turn/smart-turn-v3.2-cpu.onnx:ro"
ENVX[modelo_alterado]="";        ESPERA_NUEVO[modelo_alterado]=3
MONTA[decision_alterada]="-v $TMP/stack_alterado.json:/app/test/reference/stack.json:ro"
ENVX[decision_alterada]="";      ESPERA_NUEVO[decision_alterada]=3
MONTA[referencia_vad_ausente]="-v $TMP/reference_sin_vad:/app/test/reference:ro"
ENVX[referencia_vad_ausente]=""; ESPERA_NUEVO[referencia_vad_ausente]=2
MONTA[conteo_distinto]="";       ENVX[conteo_distinto]="-e GB_GATE_CASOS_ESPERADOS=13"
ESPERA_NUEVO[conteo_distinto]=3
# modal_entrypoint: Modal ejecuta el ENTRYPOINT pasándole el comando de su
# runtime. Aquí ese "runtime" es un echo: si el arranque lo ejecuta tras el
# gate, el contrato se cumple; si lo ignora y lanza GoldenBridge, no.
MONTA[modal_entrypoint]="";      ENVX[modal_entrypoint]="";            ESPERA_NUEVO[modal_entrypoint]=0
CASOS="normal modelo_alterado decision_alterada referencia_vad_ausente conteo_distinto modal_entrypoint"


printf 'caso\tarranque\tcodigo\tsirve_gb\truntime_modal\tveredicto_gate\tesperado\tresultado\n' > "$SALIDA/resumen.tsv"
fallos_prueba=0

for caso in $CASOS; do
    # comando que se pasa al entrypoint (así lo invoca Modal); vacío en el resto
    extra=()
    [ "$caso" = modal_entrypoint ] && extra=(sh -c 'echo RUNTIME_MODAL')

    # ── NUEVO ────────────────────────────────────────────────────────────────
    log "caso $caso · NUEVO"
    solo="-e GB_SOLO_GATE=1"; [ "$caso" = modal_entrypoint ] && solo=""
    set +e
    # shellcheck disable=SC2086
    docker run --rm --network none \
        -v "$NUEVO:/usr/local/bin/arranque:ro" ${MONTA[$caso]} \
        $solo ${ENVX[$caso]} "$GB_IMAGEN" "${extra[@]}" > "$CRUDO/${caso}_nuevo.log" 2>&1
    rc=$?
    set -e
    veredicto="$(grep -m1 -E '^\[arranque\] (gate:|ABORTA)' "$CRUDO/${caso}_nuevo.log" | sed 's/^\[arranque\] //' || true)"
    sirve=no;  grep -q 'iniciando en el puerto' "$CRUDO/${caso}_nuevo.log" && sirve=si
    modal=no;  grep -q 'RUNTIME_MODAL' "$CRUDO/${caso}_nuevo.log" && modal=si
    esperado="${ESPERA_NUEVO[$caso]}"
    ok=1; [ "$rc" -eq "$esperado" ] || ok=0
    [ "$caso" = modal_entrypoint ] && [ "$modal" != si ] && ok=0
    if [ "$ok" -eq 1 ]; then r=OK; else r=FALLA; fallos_prueba=$((fallos_prueba+1)); fi
    printf '%s\tnuevo\t%s\t%s\t%s\t%s\tcodigo=%s\t%s\n' "$caso" "$rc" "$sirve" "$modal" "$veredicto" "$esperado" "$r" >> "$SALIDA/resumen.tsv"

    # ── VIEJO (el de la imagen) ─────────────────────────────────────────────
    if [ "$caso" = conteo_distinto ]; then
        printf '%s\tviejo\t-\t-\t-\tno aplica\t-\t-\n' "$caso" >> "$SALIDA/resumen.tsv"
        continue
    fi
    log "caso $caso · VIEJO (tope ${PLAZO_VIEJO}s)"
    nombre="m0_viejo_${caso}_$$"
    # shellcheck disable=SC2086
    docker run -d --name "$nombre" --network none ${MONTA[$caso]} ${ENVX[$caso]} \
        "$GB_IMAGEN" "${extra[@]}" >/dev/null
    t=0; rc=124
    while [ "$t" -lt "$PLAZO_VIEJO" ]; do
        if [ "$(docker inspect -f '{{.State.Running}}' "$nombre" 2>/dev/null)" != "true" ]; then
            rc="$(docker inspect -f '{{.State.ExitCode}}' "$nombre" 2>/dev/null || echo 125)"; break
        fi
        if docker logs "$nombre" 2>&1 | grep -qE 'iniciando en el puerto|RUNTIME_MODAL'; then rc=en_marcha; break; fi
        sleep 2; t=$((t+2))
    done
    docker logs "$nombre" > "$CRUDO/${caso}_viejo.log" 2>&1 || true
    docker rm -f "$nombre" >/dev/null 2>&1 || true
    sirve=no;  grep -q 'iniciando en el puerto' "$CRUDO/${caso}_viejo.log" && sirve=si
    modal=no;  grep -q 'RUNTIME_MODAL' "$CRUDO/${caso}_viejo.log" && modal=si
    veredicto=calla; grep -q 'referencia OK' "$CRUDO/${caso}_viejo.log" && veredicto="dice referencia OK"
    grep -q 'NO PASÓ' "$CRUDO/${caso}_viejo.log" && veredicto="rechaza con diagnóstico"
    case "$caso" in
        normal)           esp="sirve"; r="-" ;;
        modal_entrypoint) esp="runtime Modal"; [ "$modal" = si ] && r="-" || r="NO CUMPLE CONTRATO MODAL" ;;
        *)                esp="no sirve"; [ "$sirve" = si ] && r="HUECO CONFIRMADO" || r="falla cerrado" ;;
    esac
    printf '%s\tviejo\t%s\t%s\t%s\t%s\t%s\t%s\n' "$caso" "$rc" "$sirve" "$modal" "$veredicto" "$esp" "$r" >> "$SALIDA/resumen.tsv"
done

column -t -s $'\t' "$SALIDA/resumen.tsv"
echo
if [ "$fallos_prueba" -eq 0 ]; then
    log "ACEPTADO: el arranque nuevo rechaza los 4 casos alterados, acepta el normal y cumple el contrato de Modal."
else
    log "RECHAZADO: $fallos_prueba casos del arranque nuevo no dieron lo esperado. Ver $CRUDO/"
    exit 1
fi
