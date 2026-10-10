#!/usr/bin/env bash
# Punto de entrada único de un experimento. Se ejecuta en el servidor Linux.
#
#   1. Verifica que no haya llamadas activas en GoldenBridge (salvo FORZAR=1).
#   2. Graba el manifiesto ANTES de medir: sin manifiesto no hay resultado citable.
#   3. Invoca ./correr.sh con el directorio de salida.
#   4. Propaga el código de salida real de correr.sh (pipefail activo).
#
# Variables (con defaults):
#   GB_DIR     checkout de GoldenBridge          (/srv/GoldenBridge)
#   GB_IMAGEN  imagen Docker de GoldenBridge     (goldenbridge:314t)
#   GB_HEALTH  endpoint de salud del servicio    (http://127.0.0.1:8765/health)
#   GB_CONFIG  config efectiva a registrar       ($GB_DIR/config/active.yaml)
#   FORZAR     1 = correr aunque haya llamadas activas
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$AQUI" rev-parse --show-toplevel)"
GB_DIR="${GB_DIR:-/srv/GoldenBridge}"
GB_DIR="$(cd "$GB_DIR" 2>/dev/null && pwd || echo "$GB_DIR")"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:314t}"
GB_HEALTH="${GB_HEALTH:-http://127.0.0.1:8765/health}"
GB_CONFIG="${GB_CONFIG:-$GB_DIR/config/active.yaml}"
FORZAR="${FORZAR:-0}"

SELLO="$(date -u +%Y%m%dT%H%M%SZ)"
SALIDA="$AQUI/resultados/$SELLO"
mkdir -p "$SALIDA" "$AQUI/resultados/crudo"

log() { printf '[run %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# ── 1 · guardia de campaña ───────────────────────────────────────────────────
# Correr carga sintética con llamadas reales degrada la campaña y contamina la
# medición (CLAUDE.md: una variable por campaña).
activas="desconocido"
if command -v curl >/dev/null 2>&1; then
    if cuerpo="$(curl -fsS --max-time 3 "$GB_HEALTH" 2>/dev/null)"; then
        activas="$(printf '%s' "$cuerpo" | sed -n 's/.*"active_sessions"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')"
        activas="${activas:-desconocido}"
    else
        activas="sin-servicio"
    fi
fi
log "sesiones activas en GoldenBridge: $activas"
if [[ "$activas" =~ ^[0-9]+$ ]] && (( activas > 0 )) && [[ "$FORZAR" != "1" ]]; then
    log "ABORTADO: hay $activas llamadas activas. Repetir fuera de campaña o con FORZAR=1."
    rmdir "$SALIDA" 2>/dev/null || true
    exit 2
fi

# ── 2 · manifiesto ───────────────────────────────────────────────────────────
M="$SALIDA/manifiesto.txt"
{
    echo "# manifiesto — $(basename "$AQUI") — $SELLO"
    echo "fecha_utc=$SELLO"
    echo "host=$(hostname)"
    echo "forzado=$FORZAR"
    echo "sesiones_activas_al_inicio=$activas"
    echo
    echo "## este repo"
    echo "repo_commit=$(git -C "$REPO" rev-parse HEAD)"
    echo "repo_rama=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
    # resultados/ se crea antes del manifiesto: no cuenta como cambio sin commitear
    echo "repo_sucio=$( [ -n "$(git -C "$REPO" status --porcelain -- . ':(exclude)experimentos/*/resultados/**')" ] && echo si || echo no )"
    echo
    echo "## GoldenBridge"
    if [ -d "$GB_DIR/.git" ]; then
        echo "gb_dir=$GB_DIR"
        echo "gb_commit=$(git -C "$GB_DIR" rev-parse HEAD)"
        echo "gb_rama=$(git -C "$GB_DIR" rev-parse --abbrev-ref HEAD)"
        echo "gb_sucio=$( [ -n "$(git -C "$GB_DIR" status --porcelain)" ] && echo si || echo no )"
    else
        echo "gb_dir=$GB_DIR (no es un repo git)"
    fi
    if [ -f "$GB_CONFIG" ]; then
        echo "gb_config=$GB_CONFIG"
        echo "gb_config_sha256=$(sha256sum "$GB_CONFIG" | cut -d' ' -f1)"
    else
        echo "gb_config=$GB_CONFIG (no encontrada)"
    fi
    if command -v docker >/dev/null 2>&1; then
        imagen_id="$(docker image inspect --format '{{.Id}}' "$GB_IMAGEN" 2>/dev/null | tr -d '\r\n' || true)"
        echo "gb_imagen=$GB_IMAGEN"
        echo "gb_imagen_id=${imagen_id:-no-encontrada}"
    fi
    echo
    echo "## máquina"
    echo "kernel=$(uname -r)"
    echo "cpu_modelo=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //')"
    echo "cpu_logicas=$(nproc --all)"
    echo "cpu_disponibles=$(nproc)"
    echo "avx512_vnni=$(grep -qw avx512_vnni /proc/cpuinfo && echo si || echo NO)"
    echo "mem_total_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
    if [ -r /sys/fs/cgroup/cpu.max ]; then echo "cgroup_cpu_max=$(cat /sys/fs/cgroup/cpu.max)"; fi
    if [ -r /sys/fs/cgroup/memory.max ]; then echo "cgroup_mem_max=$(cat /sys/fs/cgroup/memory.max)"; fi
    echo "loadavg_inicio=$(cut -d' ' -f1-3 /proc/loadavg)"
    echo
    echo "## entorno de hilos (deben ser 1 en GoldenBridge)"
    echo "GB_ORT_INTRA_OP=${GB_ORT_INTRA_OP:-<no definida en este shell>}"
    echo "OMP_NUM_THREADS=${OMP_NUM_THREADS:-<no definida en este shell>}"
    echo "OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-<no definida en este shell>}"
} > "$M"
log "manifiesto: $M"

if grep -q '^avx512_vnni=NO' "$M"; then
    log "AVISO: esta CPU no tiene avx512_vnni. Las decisiones de smart-turn difieren de producción."
fi
if grep -qE '^(repo|gb)_sucio=si' "$M"; then
    log "AVISO: hay cambios sin commitear; el resultado no será reproducible desde el commit."
fi

# ── 3 · medición ─────────────────────────────────────────────────────────────
if [ ! -x "$AQUI/correr.sh" ]; then
    log "ERROR: falta $AQUI/correr.sh ejecutable"
    exit 3
fi
log "inicio de la medición"
set +e
"$AQUI/correr.sh" "$SALIDA" 2>&1 | tee "$AQUI/resultados/crudo/$SELLO.log"
estado=${PIPESTATUS[0]}
set -e
echo "estado_salida=$estado" >> "$M"
echo "loadavg_fin=$(cut -d' ' -f1-3 /proc/loadavg)" >> "$M"

if (( estado != 0 )); then
    log "FALLÓ: correr.sh salió con $estado. Ver resultados/crudo/$SELLO.log"
    exit "$estado"
fi
log "OK — resultados en resultados/$SELLO/"
