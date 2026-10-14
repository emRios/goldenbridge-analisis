#!/usr/bin/env bash
# Smart-turn int8 vs fp32 en esta máquina. Lo invoca run.sh con el directorio de salida.
#
# Corre compara.py DENTRO de la imagen actual (mismo Python, numpy y ONNX
# Runtime que producción), sin red, con la entrada del guardián de arranque
# reemplazada: no hace falta que esta CPU tenga avx512_vnni.
#
# Variables:
#   MODELO_FP32  OBLIGATORIA. ruta a smart-turn-v3.2-gpu.onnx (fp32, 32,4 MB)
#   GB_IMAGEN    imagen  (goldenbridge:314t)
#   AUDIOS       carpeta con .wav  ($GB_DIR/tools/loadtest/utterances; si no
#                existe, sólo se usan utterance.wav y las 14 features congeladas)
#   PASO_MS      separación entre cortes  (20)
#   REPS_LAT     repeticiones de latencia (50)
set -euo pipefail

SALIDA="${1:?uso: correr.sh <directorio de salida>}"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GB_DIR="${GB_DIR:-/srv/GoldenBridge}"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:314t}"
MODELO_FP32="${MODELO_FP32:-}"
AUDIOS="${AUDIOS:-$GB_DIR/tools/loadtest/utterances}"
PASO_MS="${PASO_MS:-20}"
REPS_LAT="${REPS_LAT:-50}"

log() { printf '[fp32 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

[ -n "$MODELO_FP32" ] && [ -r "$MODELO_FP32" ] || {
    log "ERROR: MODELO_FP32 no apunta a un archivo legible. Descargarlo con:"
    log "  curl -L -o smart-turn-v3.2-gpu.onnx https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-gpu.onnx"
    exit 2; }
docker image inspect "$GB_IMAGEN" >/dev/null 2>&1 || { log "ERROR: no está la imagen $GB_IMAGEN"; exit 2; }

{
    echo "## fp32 · parámetros"
    echo "modelo_fp32=$MODELO_FP32"
    echo "modelo_fp32_sha256=$(sha256sum "$MODELO_FP32" | cut -d' ' -f1)"
    echo "modelo_fp32_bytes=$(stat -c %s "$MODELO_FP32")"
    echo "audios=$AUDIOS ($( [ -d "$AUDIOS" ] && ls "$AUDIOS"/*.wav 2>/dev/null | wc -l || echo 0) wav)"
    echo "paso_ms=$PASO_MS"; echo "reps_lat=$REPS_LAT"
} > "$SALIDA/parametros.txt"

AUD_ARGS=(/app/tools/loadtest/utterance.wav)
MONTAJES=()
if [ -d "$AUDIOS" ]; then
    MONTAJES+=(-v "$(readlink -f "$AUDIOS")":/audios:ro)
    AUD_ARGS+=(/audios)
else
    log "AVISO: no existe AUDIOS=$AUDIOS; sólo utterance.wav y las 14 features congeladas"
fi

log "corriendo en $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //') · vnni=$(grep -qw avx512_vnni /proc/cpuinfo && echo si || echo NO)"
docker run --rm --network none --entrypoint python \
    -e GB_ORT_INTRA_OP=1 -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 \
    -v "$AQUI/compara.py":/exp/compara.py:ro \
    -v "$(readlink -f "$MODELO_FP32")":/modelos/fp32.onnx:ro \
    "${MONTAJES[@]}" \
    "$GB_IMAGEN" /exp/compara.py \
        --int8 /app/smart-turn/smart-turn-v3.2-cpu.onnx \
        --fp32 /modelos/fp32.onnx \
        --audios "${AUD_ARGS[@]}" \
        --paso-ms "$PASO_MS" --reps-latencia "$REPS_LAT" \
    > "$SALIDA/resultado.json"

python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("casos:", len(d["probabilidades"]["int8"]), "· vnni:", d["avx512_vnni"], "· lat p50 int8/fp32:", round(d["latencia_ms"]["int8"]["p50"],1), "/", round(d["latencia_ms"]["fp32"]["p50"],1), "ms")' "$SALIDA/resultado.json"
log "resultado: $SALIDA/resultado.json"
