#!/usr/bin/env bash
# M1 · línea base del monolito a 60 ms. Lo invoca run.sh con el directorio de salida.
#
# Por cada escalón (N llamadas vivas) y repetición:
#   1. comprueba que producción siga sin llamadas (salvo FORZAR=1);
#   2. levanta un contenedor de laboratorio NUEVO de la imagen, en otro puerto,
#      con CPU reservada y CYCLE-PROBE prendido;
#   3. lee de vuelta lo efectivo (gate, cgroup, proveedores) — no se confía en
#      que docker aceptó la orden;
#   4. corre el cliente M1 (ramp + warm-up + estable) y muestrea docker stats;
#   5. guarda los logs con sello de tiempo y borra el contenedor.
# Al final, analiza.py resume todo en <salida>/resumen.tsv.
#
# Variables (las obligatorias no tienen default a propósito):
#   GB_ENV          OBLIGATORIA. .env con las credenciales de los proveedores.
#                   Se pasan sólo las de GB_ENV_VARS, nunca se imprimen.
#   GB_ENV_VARS     nombres a pasar  (GOTENKS_API_KEY URSULA_API_KEY CEREBRAS_API_KEY)
#   GB_CONFIG       config base      ($GB_DIR/config/active.yaml)
#   GB_CPUS         núcleos del contenedor (8)       GB_CPUSET     núcleos fijos (vacío)
#   CLIENTE_CPUSET  núcleos del cliente (vacío)      CLIENTE_PY    ($GB_DIR/.venv_golden/bin/python)
#   SPEECH          carpeta de audios ($GB_DIR/tools/loadtest/utterances/listos)
#   ESCALERA        "10 25 50 75 100"   REPS 1   PACKET_MS 60   MIDSPEECH_N 17
#   RAMP_S 60   WARMUP_S 60   ESTABLE_S 600   PAUSA_S 8
#   LAB_PUERTO 8776
set -euo pipefail

SALIDA="${1:?uso: correr.sh <directorio de salida>}"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELLO="$(basename "$SALIDA")"
CRUDO="$AQUI/resultados/crudo/$SELLO"
mkdir -p "$CRUDO"

GB_DIR="${GB_DIR:-/srv/GoldenBridge}"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:314t}"
GB_HEALTH="${GB_HEALTH:-http://127.0.0.1:8765/health}"
GB_CONFIG="${GB_CONFIG:-$GB_DIR/config/active.yaml}"
GB_ENV="${GB_ENV:-}"
GB_ENV_VARS="${GB_ENV_VARS:-GOTENKS_API_KEY URSULA_API_KEY CEREBRAS_API_KEY}"
GB_CPUS="${GB_CPUS:-8}"
GB_CPUSET="${GB_CPUSET:-}"
CLIENTE_CPUSET="${CLIENTE_CPUSET:-}"
CLIENTE_PY="${CLIENTE_PY:-$GB_DIR/.venv_golden/bin/python}"
SPEECH="${SPEECH:-$GB_DIR/tools/loadtest/utterances/listos}"
ESCALERA="${ESCALERA:-10 25 50 75 100}"
REPS="${REPS:-1}"
PACKET_MS="${PACKET_MS:-60}"
MIDSPEECH_N="${MIDSPEECH_N:-17}"
RAMP_S="${RAMP_S:-60}"
WARMUP_S="${WARMUP_S:-60}"
ESTABLE_S="${ESTABLE_S:-600}"
PAUSA_S="${PAUSA_S:-8}"
LAB_PUERTO="${LAB_PUERTO:-8776}"
FORZAR="${FORZAR:-0}"
LAB="m1lab"

log() { printf '[m1 %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
muere() { log "ERROR: $*"; docker rm -f "$LAB" >/dev/null 2>&1 || true; exit 4; }
trap 'docker rm -f "$LAB" >/dev/null 2>&1 || true' EXIT

# ── requisitos ───────────────────────────────────────────────────────────────
[ -n "$GB_ENV" ] || muere "falta GB_ENV (ruta al .env con las credenciales). No hay default: \
el traspaso pide un juego propio, no el de producción. Decidirlo a conciencia."
[ -r "$GB_ENV" ] || muere "no se puede leer GB_ENV=$GB_ENV"
[ -r "$GB_CONFIG" ] || muere "no se puede leer GB_CONFIG=$GB_CONFIG"
[ -d "$SPEECH" ] || muere "no existe SPEECH=$SPEECH. Generarla con:
    cd $GB_DIR/tools/loadtest && python procesa_audios.py utterances --largo 5.2"
"$CLIENTE_PY" -c 'import audioop, wave; from websockets.asyncio.client import connect' 2>/dev/null \
    || muere "CLIENTE_PY=$CLIENTE_PY necesita websockets>=13 y audioop (python<=3.12)"
command -v curl >/dev/null || muere "falta curl"
docker image inspect "$GB_IMAGEN" >/dev/null 2>&1 || muere "no está la imagen $GB_IMAGEN"
grep -qw avx512_vnni /proc/cpuinfo || log "AVISO: CPU sin avx512_vnni — el gate de la imagen va a negarse a servir"

# Credenciales: se leen del .env sin ejecutarlo y se exportan sólo las pedidas.
# docker run -e NOMBRE (sin =valor) las toma del entorno: el valor no aparece
# en argv, ni en ps, ni en este log.
ENV_ARGS=()
for nombre in $GB_ENV_VARS; do
    linea="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${nombre}=" "$GB_ENV" | tail -1 || true)"
    [ -n "$linea" ] || { log "AVISO: $nombre no está en GB_ENV"; continue; }
    valor="${linea#*=}"
    valor="${valor%$'\r'}"
    valor="${valor#\"}"; valor="${valor%\"}"; valor="${valor#\'}"; valor="${valor%\'}"
    export "$nombre=$valor"
    ENV_ARGS+=(-e "$nombre")
done
unset linea valor
log "credenciales pasadas: ${#ENV_ARGS[@]} de $(wc -w <<<"$GB_ENV_VARS") (nombres: $GB_ENV_VARS)"

# ── config del laboratorio ───────────────────────────────────────────────────
# La de producción, con tres cambios explícitos y nada más:
#   server.cycle_probe: true                       (instrumento)
#   smart_turn.midspeech_check_every_n_chunks: N   (cuenta mensajes; 17 ≈ 1 s a 60 ms)
#   smart_turn.model_path: la ruta DENTRO de la imagen. active.yaml puede traer
#     una ruta absoluta de /srv que en el contenedor no existe, y GB lanza
#     FileNotFoundError al cargar smart-turn (smart_turn_processor.py L92).
# Se fusiona con el python de la imagen para no depender de PyYAML en el host.
CONFIG_LAB="$SALIDA/config_lab.yaml"
docker run --rm --network none --entrypoint python \
    -v "$(readlink -f "$GB_CONFIG")":/in.yaml:ro \
    -e MIDSPEECH_N="$MIDSPEECH_N" "$GB_IMAGEN" -c '
import os, sys, yaml
c = yaml.safe_load(open("/in.yaml")) or {}
c.setdefault("server", {})["cycle_probe"] = True
c.setdefault("smart_turn", {})["midspeech_check_every_n_chunks"] = int(os.environ["MIDSPEECH_N"])
c["smart_turn"]["model_path"] = "/app/smart-turn/smart-turn-v3.2-cpu.onnx"
yaml.safe_dump(c, sys.stdout, sort_keys=False, allow_unicode=True)
' > "$CONFIG_LAB" || muere "no se pudo armar la config del laboratorio"
log "config del laboratorio: $CONFIG_LAB (sha256 $(sha256sum "$CONFIG_LAB" | cut -c1-12))"

{
    echo "## M1 · parámetros"
    echo "escalera=$ESCALERA"; echo "reps=$REPS"; echo "packet_ms=$PACKET_MS"
    echo "midspeech_n=$MIDSPEECH_N"; echo "ramp_s=$RAMP_S"; echo "warmup_s=$WARMUP_S"
    echo "estable_s=$ESTABLE_S"; echo "pausa_s=$PAUSA_S"; echo "speech=$SPEECH"
    echo "speech_sha256=$(cat "$SPEECH"/*.wav | sha256sum | cut -d' ' -f1)"
    echo "gb_cpus=$GB_CPUS"; echo "gb_cpuset=${GB_CPUSET:-<sin fijar>}"
    echo "cliente_cpuset=${CLIENTE_CPUSET:-<sin fijar>}"
    echo "cliente_py=$CLIENTE_PY ($("$CLIENTE_PY" -V 2>&1))"
    echo "cliente_sha256=$(sha256sum "$AQUI/cliente/td1_client_m1.py" | cut -d' ' -f1)"
    echo "config_base=$GB_CONFIG"
    echo "config_lab_sha256=$(sha256sum "$CONFIG_LAB" | cut -d' ' -f1)"
    echo "credenciales=$GB_ENV_VARS (de $GB_ENV)"
} > "$SALIDA/parametros.txt"
# Topología: qué vCPU comparten núcleo físico. GB_CPUSET y CLIENTE_CPUSET deben
# caer en núcleos físicos distintos entre sí y de lo que usa producción.
lscpu -e=CPU,CORE,SOCKET,ONLINE > "$SALIDA/topologia_cpu.txt" 2>/dev/null     || echo "lscpu no disponible" > "$SALIDA/topologia_cpu.txt"

# ── un escalón ───────────────────────────────────────────────────────────────
salud_prod() {
    curl -fsS --max-time 3 "$GB_HEALTH" 2>/dev/null \
        | sed -n 's/.*"active_sessions"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p'
}

escalon() {
    local rep="$1" n="$2"
    local paso="r${rep}_n$(printf '%03d' "$n")"
    local dir="$CRUDO/$paso"
    mkdir -p "$dir"

    local prod; prod="$(salud_prod || true)"
    if [[ "$prod" =~ ^[0-9]+$ ]] && (( prod > 0 )) && [[ "$FORZAR" != "1" ]]; then
        muere "producción tiene $prod llamadas activas antes de $paso. Se detiene la escalera."
    fi

    local cpu_args=(--cpus "$GB_CPUS")
    [ -n "$GB_CPUSET" ] && cpu_args+=(--cpuset-cpus "$GB_CPUSET")
    docker rm -f "$LAB" >/dev/null 2>&1 || true
    docker run -d --name "$LAB" "${cpu_args[@]}" \
        -p "127.0.0.1:$LAB_PUERTO:$LAB_PUERTO" \
        -e GB_PUERTO="$LAB_PUERTO" -e GB_CONFIG=/lab/config.yaml \
        -v "$CONFIG_LAB":/lab/config.yaml:ro \
        "${ENV_ARGS[@]}" "$GB_IMAGEN" >/dev/null || muere "docker run falló en $paso"

    local t=0
    until curl -fsS --max-time 2 "http://127.0.0.1:$LAB_PUERTO/health" >/dev/null 2>&1; do
        sleep 2; t=$((t + 2))
        docker ps -q -f name="^${LAB}$" | grep -q . || {
            docker logs "$LAB" > "$dir/gb.log" 2>&1 || true
            muere "el contenedor murió al arrancar en $paso (ver $dir/gb.log)"; }
        (( t < 240 )) || muere "el laboratorio no respondió /health en 240 s ($paso)"
    done

    # Leer de vuelta lo efectivo. Regla 4: no basta con que docker haya aceptado.
    {
        echo "paso=$paso"; echo "rep=$rep"; echo "n=$n"; echo "arranque_s=$t"
        docker inspect "$LAB" --format \
'nano_cpus={{.HostConfig.NanoCpus}}
cpuset={{.HostConfig.CpusetCpus}}
imagen={{.Image}}'
        echo "cgroup_cpu_max=$(docker exec "$LAB" cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo ?)"
        echo "cgroup_cpuset=$(docker exec "$LAB" cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null || echo ?)"
        echo "nproc_dentro=$(docker exec "$LAB" nproc 2>/dev/null || echo ?)"
        echo "prod_activas=${prod:-?}"
    } > "$dir/efectivo.txt"
    # quién más consume CPU en la máquina (producción, ai-engine, otros)
    ps -eo pid,pcpu,rss,nlwp,comm --sort=-pcpu | head -12 > "$dir/procesos_inicio.txt" || true
    local arranque; arranque="$(docker logs "$LAB" 2>&1)"
    grep -q "referencia OK" <<<"$arranque" || muere "el gate no imprimió 'referencia OK' en $paso"
    if grep -qiE "(LLM|TTS|STT) not available" <<<"$arranque"; then
        docker logs "$LAB" > "$dir/gb.log" 2>&1 || true
        muere "proveedores no disponibles en $paso — faltan credenciales (ver $dir/gb.log)"
    fi

    # docker stats cada 5 s, en segundo plano, mientras corre el cliente
    ( while docker ps -q -f name="^${LAB}$" | grep -q .; do
          printf '%s\t%s\n' "$(date -u +%s)" \
              "$(docker stats --no-stream --format '{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}' "$LAB" 2>/dev/null)"
          sleep 5
      done ) > "$dir/stats.tsv" &
    local muestreo=$!

    local t0; t0="$(date -u +%s.%N)"
    echo "t0_epoch=$t0" >> "$dir/efectivo.txt"
    log "$paso: $n llamadas · ramp ${RAMP_S}s + warm-up ${WARMUP_S}s + estable ${ESTABLE_S}s"
    local pre=(); [ -n "$CLIENTE_CPUSET" ] && pre=(taskset -c "$CLIENTE_CPUSET")
    set +e
    "${pre[@]}" "$CLIENTE_PY" -W ignore "$AQUI/cliente/td1_client_m1.py" \
        --url "ws://127.0.0.1:$LAB_PUERTO/v1/agent/converse" \
        --sessions "$n" --controls 3 --ramp "$RAMP_S" --hold "$((WARMUP_S + ESTABLE_S))" \
        --speech "$SPEECH" --utt-s 0 --pause-s "$PAUSA_S" --packet-ms "$PACKET_MS" \
        --barge-in-ms 1800 --barge-in-cada 3 \
        --out "$dir/cliente.tsv" > "$dir/cliente.log" 2>&1
    local rc=$?
    set -e
    echo "cliente_rc=$rc" >> "$dir/efectivo.txt"
    [ -n "$CLIENTE_CPUSET" ] && echo "cliente_cpuset_leido=$CLIENTE_CPUSET" >> "$dir/efectivo.txt"

    ps -eo pid,pcpu,rss,nlwp,comm --sort=-pcpu | head -12 > "$dir/procesos_fin.txt" || true
    docker logs --timestamps "$LAB" > "$dir/gb.log" 2>&1 || true
    docker rm -f "$LAB" >/dev/null 2>&1 || true
    wait "$muestreo" 2>/dev/null || true
    log "$paso: cliente rc=$rc · $(grep -c CYCLE-PROBE "$dir/gb.log" || true) líneas CYCLE-PROBE"
}

# ── la escalera ──────────────────────────────────────────────────────────────
# Orden balanceado: la repetición par recorre la escalera al revés, para que el
# calentamiento de la máquina o de los proveedores no se confunda con N.
for rep in $(seq 1 "$REPS"); do
    if (( rep % 2 == 0 )); then orden="$(tr ' ' '\n' <<<"$ESCALERA" | tac | tr '\n' ' ')"
    else orden="$ESCALERA"; fi
    for n in $orden; do escalon "$rep" "$n"; done
done

python3 "$AQUI/analiza.py" "$CRUDO" "$SALIDA" \
    --ramp "$RAMP_S" --warmup "$WARMUP_S" --estable "$ESTABLE_S"
log "resumen: $SALIDA/resumen.tsv"
