#!/usr/bin/env bash
# [MOCKS] Prepara lo que correr.sh necesita y no debe fabricar en caliente:
#   1. config/m1_mocks_base.yaml: m1_base.yaml (sha 15507f5f…) pasado por mock_cfg.py generate
#      (brazo A = call_summary igual que m1_base; URLs a los mocks; server.host 127.0.0.1;
#      cualquier otra URL remota neutralizada) y validado releyéndolo.
#   2. .env.mocks con credenciales de relleno (los mocks no validan; si una URL se escapara, no factura).
#   3. Registro de los sha256 de los dos modelos DENTRO de la imagen.
# No arranca nada: el único docker run es --network none con entrypoint sh.
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-/srv/GoldenBridge/.venv_314/bin/python}"
MOCKS_DIR="${MOCKS_DIR:-$HOME/analisis_tmp/loopab/mocks}"
BASE="${BASE:-$HOME/m1_base.yaml}"
BASE_SHA_ESPERADO="15507f5f7f94ba799aef36a4025797b93923ee7c5674dc2372743ba54516b632"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:remarcacion-v1-jef-fp32}"

log() { printf '[preparar %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
sha="$(sha256sum "$BASE" | cut -d' ' -f1)"
[ "$sha" = "$BASE_SHA_ESPERADO" ] || { log "ERROR: $BASE no es la config de M1 (sha $sha)"; exit 2; }
mkdir -p "$AQUI/config"
"$PY" "$MOCKS_DIR/mock_cfg.py" generate --src "$BASE" --arm A --out "$AQUI/config/m1_mocks_base.yaml"
"$PY" "$MOCKS_DIR/mock_cfg.py" validate "$AQUI/config/m1_mocks_base.yaml"
sha256sum "$BASE" "$AQUI/config/m1_mocks_base.yaml" "$MOCKS_DIR"/mock_cfg.py "$MOCKS_DIR"/mock_stt.py \
    "$MOCKS_DIR"/mock_llm.py "$MOCKS_DIR"/mock_tts.py "$MOCKS_DIR"/start_mocks.sh > "$AQUI/config/sha256.txt"
docker image inspect "$GB_IMAGEN" >/dev/null 2>&1 || { log "ERROR: no está la imagen $GB_IMAGEN"; exit 2; }
docker run --rm --network none --entrypoint sh "$GB_IMAGEN" -c 'sha256sum /app/smart-turn/*.onnx' > "$AQUI/config/modelos_imagen.txt"
grep -q '^ab8dc64b' "$AQUI/config/modelos_imagen.txt" || { log "ERROR: fp32 ab8dc64b… no está en la imagen"; exit 2; }
grep -q '^2bb02631' "$AQUI/config/modelos_imagen.txt" || { log "ERROR: int8 2bb02631… no está en la imagen"; exit 2; }
if [ ! -f "$AQUI/.env.mocks" ]; then
    printf 'GOTENKS_API_KEY=mock-sin-valor\nURSULA_API_KEY=mock-sin-valor\nCEREBRAS_API_KEY=mock-sin-valor\n' > "$AQUI/.env.mocks"
    chmod 600 "$AQUI/.env.mocks"
fi
log "config: $AQUI/config/m1_mocks_base.yaml ($(sha256sum "$AQUI/config/m1_mocks_base.yaml" | cut -c1-12))"
log "modelos en la imagen:"; sed 's/^/    /' "$AQUI/config/modelos_imagen.txt"
log "listo"
