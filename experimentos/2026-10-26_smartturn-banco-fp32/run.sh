#!/usr/bin/env bash
# Banco de smart-turn fp32. Punto de entrada único: manifiesto ANTES de medir, luego las series en orden
# A → B → C → D (B, C y D usan el mejor P y la mejor sesión que dicta analiza.py con las reglas del README).
#   ./run.sh                 # todo, 3 repeticiones, 10 s de calentamiento + 60 s medidos por condición
#   SERIES="A" ./run.sh      # sólo una serie
#   CORTA=1 ./run.sh         # validación: A con P = 1, una repetición, 2 + 10 s
# Variables: GB_IMAGEN (goldenbridge:remarcacion-v1-jef-fp32) · CPUSET (2-5,10-13) · AUDIOS (/srv/GoldenBridge/tools/loadtest/utterances)
#            REPS (3) · CALENTAR (10) · MEDIR (60) · SERIES ("A B C D")
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$AQUI" rev-parse --show-toplevel)"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:remarcacion-v1-jef-fp32}"
CPUSET="${CPUSET:-2-5,10-13}"
AUDIOS="${AUDIOS:-/srv/GoldenBridge/tools/loadtest/utterances}"
REPS="${REPS:-3}"; CALENTAR="${CALENTAR:-10}"; MEDIR="${MEDIR:-60}"; SERIES="${SERIES:-A B C D}"
if [ "${CORTA:-0}" = 1 ]; then REPS=1; CALENTAR=2; MEDIR=10; SERIES="CORTA"; fi
SELLO="$(date -u +%Y%m%dT%H%M%SZ)"; SALIDA="$AQUI/resultados/$SELLO"; mkdir -p "$SALIDA"
log() { printf '[banco %s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$SALIDA/corrida.log"; }

docker image inspect "$GB_IMAGEN" >/dev/null 2>&1 || { log "ERROR: no está la imagen $GB_IMAGEN"; exit 2; }
[ -d "$AUDIOS" ] || { log "ERROR: no existe AUDIOS=$AUDIOS"; exit 2; }

# ── manifiesto ──────────────────────────────────────────────────────────────
{
  echo "# manifiesto — $(basename "$AQUI") — $SELLO"
  echo "fecha_utc=$SELLO"; echo "host=$(hostname)"
  echo "repo_commit=$(git -C "$REPO" rev-parse HEAD)"; echo "repo_rama=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  echo "repo_sucio=$( [ -n "$(git -C "$REPO" status --porcelain -- "$AQUI" ':(exclude)'"$AQUI"'/resultados/**')" ] && echo si || echo no )"
  echo "banco_sha256=$(sha256sum "$AQUI/banco.py" | cut -d' ' -f1)"; echo "analiza_sha256=$(sha256sum "$AQUI/analiza.py" | cut -d' ' -f1)"
  echo "gb_imagen=$GB_IMAGEN"; echo "gb_imagen_id=$(docker image inspect --format '{{.Id}}' "$GB_IMAGEN")"
  echo "cpuset=$CPUSET"; echo "reps=$REPS"; echo "calentar_s=$CALENTAR"; echo "medir_s=$MEDIR"; echo "series=$SERIES"
  echo "audios=$AUDIOS"; echo "audios_sha256=$(cat "$AUDIOS"/*.wav | sha256sum | cut -d' ' -f1)"
  echo "cpu_modelo=$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //')"
  echo "avx512_vnni=$(grep -qw avx512_vnni /proc/cpuinfo && echo si || echo NO)"
  echo "loadavg_inicio=$(cut -d' ' -f1-3 /proc/loadavg)"
  echo "contenedores_vivos=$(docker ps --format '{{.Names}}' | tr '\n' ' ')"
} > "$SALIDA/manifiesto.txt"
lscpu -e=CPU,CORE,SOCKET,ONLINE > "$SALIDA/topologia_cpu.txt" 2>/dev/null || true
log "manifiesto: $SALIDA/manifiesto.txt"

cond() {  # nombre + args de banco.py
  local nombre="$1"; shift
  log "condición $nombre: $*"
  docker run --rm --network none --user "$(id -u):$(id -g)" --cpuset-cpus "$CPUSET" \
    -e CPUSET_ESPERADO="$CPUSET" -e PYTHONDONTWRITEBYTECODE=1 -e NUMBA_CACHE_DIR=/tmp/numba \
    -v "$AQUI/banco.py":/exp/banco.py:ro -v "$(readlink -f "$AUDIOS")":/audios:ro -v "$SALIDA":/out \
    --entrypoint python "$GB_IMAGEN" /exp/banco.py --calentar "$CALENTAR" --medir "$MEDIR" \
    --etiqueta "$nombre" --salida "/out/$nombre.json" "$@" 2>>"$SALIDA/stderr.log" | grep '\[banco\]' | tee -a "$SALIDA/corrida.log"
  [ -s "$SALIDA/$nombre.json" ] || { log "ERROR: $nombre no dejó resultado (ver stderr.log)"; exit 4; }
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d['valido'] else 5)" "$SALIDA/$nombre.json" \
    || { log "ERROR: $nombre inválida por lectura de vuelta"; exit 5; }
}
rotar() { local r=$1; shift; local a=("$@"); local n=${#a[@]}; for ((i=0;i<n;i++)); do echo "${a[$(( (i + r) % n ))]}"; done; }
AN() { python3 "$AQUI/analiza.py" "$@" "$SALIDA"; }

for serie in $SERIES; do
  case "$serie" in
    CORTA) cond corta_A_p1 --serie A --p 1 ;;
    A) for r in $(seq 0 $((REPS-1))); do for p in $(rotar $r 1 2 4 8); do cond "A_p${p}_r$r" --serie A --p "$p"; done; done ;;
    B) P=$(python3 "$AQUI/analiza.py" mejor-p "$SALIDA"); [ -n "$P" ] || { log "ERROR: sin mejor P (¿corrió A?)"; exit 6; }
       log "mejor P = $P"
       for r in $(seq 0 $((REPS-1))); do for s in $(rotar $r compartida por_hilo); do cond "B_p${P}_${s}_r$r" --serie B --p "$P" --sesiones "$s"; done; done ;;
    C) P=$(python3 "$AQUI/analiza.py" mejor-p "$SALIDA"); S=$(python3 "$AQUI/analiza.py" mejor-sesion "$SALIDA" "$P")
       log "mejor P = $P, sesión = $S"
       for r in $(seq 0 $((REPS-1))); do for n in $(rotar $r 25 50 100); do cond "C_p${P}_${S}_sil${n}_r$r" --serie C --p "$P" --sesiones "$S" --silero-llamadas "$n"; done; done ;;
    D) P=$(python3 "$AQUI/analiza.py" mejor-p "$SALIDA"); S=$(python3 "$AQUI/analiza.py" mejor-sesion "$SALIDA" "$P")
       for pd in 1 "$P"; do
         cap=$(python3 "$AQUI/analiza.py" capacidad "$SALIDA" "$pd"); [ -n "$cap" ] || { log "ERROR: sin capacidad para P=$pd"; exit 6; }
         ses=$([ "$pd" = "$P" ] && echo "$S" || echo compartida)
         log "serie D con P = $pd ($ses), capacidad medida en A = $cap/s"
         for r in $(seq 0 $((REPS-1))); do for fr in $(rotar $r 0.5 0.75 0.9 1.0); do
           tasa=$(python3 -c "print(round($cap*$fr,3))")
           cond "D_p${pd}_f${fr}_r$r" --serie D --p "$pd" --sesiones "$ses" --tasa "$tasa"
         done; done
         [ "$pd" = "$P" ] && break
       done ;;
    *) log "serie desconocida: $serie"; exit 2 ;;
  esac
done
python3 "$AQUI/analiza.py" resumen "$SALIDA" > /dev/null
echo "loadavg_fin=$(cut -d' ' -f1-3 /proc/loadavg)" >> "$SALIDA/manifiesto.txt"
log "listo: $SALIDA/resumen.md"
