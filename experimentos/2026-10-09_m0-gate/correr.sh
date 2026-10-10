#!/usr/bin/env bash
# M0 — prueba negativa del gate de arranque de la imagen goldenbridge.
#
# Corre la MISMA imagen con dos arranques: el de api (VIEJO, el que trae la
# imagen) y el del parche (NUEVO, montado encima). Para cada caso se monta un
# artefacto alterado dentro del contenedor y se observa si el arranque llega a
# servir. Nada de esto toca GoldenBridge ni la imagen: todo va por montajes de
# sólo lectura y contenedores --rm sin red.
#
# Lo invoca run.sh con el directorio de salida como $1.
set -euo pipefail

SALIDA="$1"
CRUDO="$(dirname "$SALIDA")/crudo/$(basename "$SALIDA")"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$AQUI" rev-parse --show-toplevel)"
PARCHE="$REPO/parches/2026-10-09_goldenbridge_gate-arranque/0001-fix-arranque-el-gate-de-referencia-ahora-bloquea-de-.patch"
GB_DIR="${GB_DIR:-/srv/GoldenBridge}"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:314t}"
# El arranque VIEJO no conoce GB_SOLO_GATE: tras el gate intenta servir. Se le
# da este plazo para imprimir su veredicto y se lo mata.
PLAZO_VIEJO="${PLAZO_VIEJO:-180}"
mkdir -p "$CRUDO"
log() { printf '[m0 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# ── precondiciones ──────────────────────────────────────────────────────────
command -v docker >/dev/null || { log "ERROR: docker no está instalado"; exit 10; }
docker image inspect "$GB_IMAGEN" >/dev/null 2>&1 || {
    log "ERROR: no existe la imagen $GB_IMAGEN. Ver README §Preparar."; exit 11; }
[ -f "$PARCHE" ] || { log "ERROR: falta el parche $PARCHE"; exit 12; }
command -v python3 >/dev/null || { log "ERROR: hace falta python3 en el host"; exit 13; }
grep -qw avx512_vnni /proc/cpuinfo || log "AVISO: CPU sin avx512_vnni — el caso 'normal' DEBE fallar aquí"

# ── el arranque NUEVO, aplicando el parche sobre el de api ──────────────────
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/docker"
if [ -d "$GB_DIR/.git" ] && git -C "$GB_DIR" rev-parse -q --verify api >/dev/null; then
    git -C "$GB_DIR" show api:docker/arranque.sh > "$TMP/docker/arranque.sh"
else
    log "GB_DIR sin rama api: extraigo el arranque de la propia imagen"
    c="$(docker create "$GB_IMAGEN")"; docker cp "$c:/usr/local/bin/arranque" "$TMP/docker/arranque.sh"; docker rm "$c" >/dev/null
fi
(cd "$TMP" && git -c core.autocrlf=false apply "$PARCHE")
if grep -q $'\r' "$TMP/docker/arranque.sh"; then log "ERROR: el arranque nuevo quedó con CRLF"; exit 14; fi
NUEVO="$TMP/arranque_nuevo.sh"; mv "$TMP/docker/arranque.sh" "$NUEVO"; chmod 755 "$NUEVO"
sha256sum "$NUEVO" | tee "$SALIDA/arranque_nuevo.sha256"

# ── artefactos originales de la imagen, para alterarlos ─────────────────────
c="$(docker create "$GB_IMAGEN")"
docker cp "$c:/app/smart-turn/smart-turn-v3.2-cpu.onnx" "$TMP/modelo.onnx"
docker cp "$c:/app/test/reference/stack.json" "$TMP/stack.json"
docker rm "$c" >/dev/null

# caso modelo_alterado: mismo tamaño, 64 bytes cambiados en la mitad del archivo
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

# caso decision_alterada: la referencia afirma una probabilidad imposible para
# el primer caso, así que una decisión de referencia deja de coincidir
python3 - "$TMP/stack.json" "$TMP/stack_alterado.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
k = sorted(d["probabilidades"])[0]
d["probabilidades"][k] = 0.99 if d["probabilidades"][k] < 0.5 else 0.01
json.dump(d, open(sys.argv[2], "w"), indent=2)
print("alterado", k)
PY

# caso referencia_vad_ausente: se monta una copia de /app/test/reference sin
# vad_probs.npz. El arranque viejo no la busca y pytest salta las 6 pruebas
# del VAD (pytestmark skipif) dándolas por buenas.
c="$(docker create "$GB_IMAGEN")"
docker cp "$c:/app/test/reference" "$TMP/reference_sin_vad"
docker rm "$c" >/dev/null
rm -f "$TMP/reference_sin_vad/vad_probs.npz"
chmod -R a+rX "$TMP"

# ── casos ───────────────────────────────────────────────────────────────────
# nombre | montajes extra | variables extra | NUEVO esperado | VIEJO esperado
declare -A MONTA ENVX ESPERA_NUEVO
MONTA[normal]="";                                                  ENVX[normal]="";                          ESPERA_NUEVO[normal]=0
MONTA[modelo_alterado]="-v $TMP/modelo_alterado.onnx:/app/smart-turn/smart-turn-v3.2-cpu.onnx:ro"; ENVX[modelo_alterado]=""; ESPERA_NUEVO[modelo_alterado]=3
MONTA[decision_alterada]="-v $TMP/stack_alterado.json:/app/test/reference/stack.json:ro";         ENVX[decision_alterada]=""; ESPERA_NUEVO[decision_alterada]=3
MONTA[referencia_vad_ausente]="-v $TMP/reference_sin_vad:/app/test/reference:ro";                  ENVX[referencia_vad_ausente]=""; ESPERA_NUEVO[referencia_vad_ausente]=2
MONTA[conteo_distinto]="";                                         ENVX[conteo_distinto]="-e GB_GATE_CASOS_ESPERADOS=13"; ESPERA_NUEVO[conteo_distinto]=3
CASOS="normal modelo_alterado decision_alterada referencia_vad_ausente conteo_distinto"

printf 'caso\tarranque\tcodigo\tsirve\tveredicto_gate\tesperado\tresultado\n' > "$SALIDA/resumen.tsv"
fallos_prueba=0

for caso in $CASOS; do
    # NUEVO: GB_SOLO_GATE=1 → sale tras el gate; el código decide
    log "caso $caso · NUEVO"
    set +e
    # shellcheck disable=SC2086
    docker run --rm --network none \
        -v "$NUEVO:/usr/local/bin/arranque:ro" ${MONTA[$caso]} \
        -e GB_SOLO_GATE=1 ${ENVX[$caso]} \
        "$GB_IMAGEN" > "$CRUDO/${caso}_nuevo.log" 2>&1
    rc=$?
    set -e
    veredicto="$(grep -m1 -E '^\[arranque\] gate:' "$CRUDO/${caso}_nuevo.log" | sed 's/^\[arranque\] //' || true)"
    [ -n "$veredicto" ] || veredicto="$(grep -m1 -E 'ABORTA|FALTA' "$CRUDO/${caso}_nuevo.log" | sed 's/^\[arranque\] //' || true)"
    sirve=no; grep -q 'referencia OK' "$CRUDO/${caso}_nuevo.log" && [ "$rc" -eq 0 ] && sirve=si
    esperado="${ESPERA_NUEVO[$caso]}"
    if [ "$rc" -eq "$esperado" ]; then r=OK; else r=FALLA; fallos_prueba=$((fallos_prueba+1)); fi
    printf '%s\tnuevo\t%s\t%s\t%s\tcodigo=%s\t%s\n' "$caso" "$rc" "$sirve" "$veredicto" "$esperado" "$r" >> "$SALIDA/resumen.tsv"

    # VIEJO: el de la imagen; tras el gate intenta servir. Se observa hasta que
    # anuncia el puerto o termina (tope PLAZO_VIEJO) y se lo detiene.
    # conteo_distinto no aplica: GB_GATE_CASOS_ESPERADOS no existe en el viejo.
    if [ "$caso" = conteo_distinto ]; then
        printf '%s\tviejo\t-\t-\tno aplica\t-\t-\n' "$caso" >> "$SALIDA/resumen.tsv"
        continue
    fi
    log "caso $caso · VIEJO (tope ${PLAZO_VIEJO}s)"
    nombre="m0_viejo_${caso}_$$"
    # shellcheck disable=SC2086
    docker run -d --name "$nombre" --network none ${MONTA[$caso]} ${ENVX[$caso]} \
        "$GB_IMAGEN" >/dev/null
    t=0; rc=124
    while [ "$t" -lt "$PLAZO_VIEJO" ]; do
        if [ "$(docker inspect -f '{{.State.Running}}' "$nombre" 2>/dev/null)" != "true" ]; then
            rc="$(docker inspect -f '{{.State.ExitCode}}' "$nombre" 2>/dev/null || echo 125)"; break
        fi
        if docker logs "$nombre" 2>&1 | grep -q 'iniciando en el puerto'; then rc=servia; break; fi
        sleep 2; t=$((t+2))
    done
    docker logs "$nombre" > "$CRUDO/${caso}_viejo.log" 2>&1 || true
    docker rm -f "$nombre" >/dev/null 2>&1 || true
    sirve=no; grep -q 'iniciando en el puerto' "$CRUDO/${caso}_viejo.log" && sirve=si
    veredicto=rechaza; grep -q 'referencia OK' "$CRUDO/${caso}_viejo.log" && veredicto="dice referencia OK"
    if [ "$caso" = normal ]; then espera_viejo="sirve=si"; else espera_viejo="sirve=no si el gate funcionara"; fi
    if [ "$caso" != normal ] && [ "$sirve" = si ]; then r="BUG CONFIRMADO"; else r="-"; fi
    printf '%s\tviejo\t%s\t%s\t%s\t%s\t%s\n' "$caso" "$rc" "$sirve" "$veredicto" "$espera_viejo" "$r" >> "$SALIDA/resumen.tsv"
done

column -t -s $'\t' "$SALIDA/resumen.tsv"
echo
if [ "$fallos_prueba" -eq 0 ]; then
    log "ACEPTADO: el arranque nuevo rechaza los 4 casos alterados y acepta el normal."
else
    log "RECHAZADO: $fallos_prueba casos del arranque nuevo no dieron el código esperado. Ver $CRUDO/"
    exit 1
fi
