#!/usr/bin/env bash
# M1s · smart-turn int8 contra fp32, con MOCKS locales en vez de Modal. Lo invoca run.sh.
# Es correr.sh de M1 (2026-10-10_m1-linea-base) con los cambios marcados [MOCKS]; todo lo demás
# (cliente, cadencia, lectura de vuelta, analiza.py) queda igual y se toma del directorio de M1.
#
# Por cada escalón (N llamadas vivas):
#   1. comprueba que los mocks estén arriba, fijados en MOCKS_CPUSET y que GB_CONFIG apunte a ellos;
#   2. levanta un contenedor NUEVO de la imagen, en red del host (puerto 8777 en 127.0.0.1), con CPU reservada;
#   3. lee de vuelta lo efectivo (gate, cgroup, modelo cargado, hilos, puerto en loopback, mocks);
#   4. corre el cliente M1 (ramp + warm-up + estable) y muestrea docker stats y la CPU de los mocks;
#   5. guarda los logs, corre el extractor de la auditoría (primarias) y borra el contenedor.
#
# Variables (las obligatorias no tienen default a propósito):
#   SMART_MODEL     OBLIGATORIA. /app/smart-turn/smart-turn-v3.2-gpu.onnx (fp32, control)
#                   o /app/smart-turn/smart-turn-v3.2-cpu.onnx (int8, variante)
#   ETIQUETA        OBLIGATORIA. control_corto | C_sube | V_sube | C_baja | V_baja (va al parametros.txt)
#   GB_CONFIG       config base ya apuntada a los mocks ($AQUI/config/m1_mocks_base.yaml, de preparar.sh)
#   GB_ENV          .env de relleno ($AQUI/.env.mocks). Se niega a usar /srv/GoldenBridge/.env
#   MOCKS_DIR       ~/analisis_tmp/loopab/mocks      MOCKS_CPUSET  0-1,8-9 (lectura de vuelta en /proc)
#   M1_DIR          ../2026-10-10_m1-linea-base      (cliente, analiza.py, extractor de la auditoría)
#   GB_IMAGEN       goldenbridge:remarcacion-v1-jef-fp32
#   GB_CPUS 8   GB_CPUSET 2-5,10-13   CLIENTE_CPUSET 6,14   LAB_PUERTO 8777
#   ESCALERA "50 75 100"  REPS 1  PACKET_MS 60  MIDSPEECH_N 17  RAMP_S 60  WARMUP_S 60  ESTABLE_S 600  PAUSA_S 8
#   TURNOS espera  HUECO_MS 800  SIN_RESP_S 10
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0

SALIDA="${1:?uso: correr.sh <directorio de salida>}"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELLO="$(basename "$SALIDA")"
CRUDO="$AQUI/resultados/crudo/$SELLO"
mkdir -p "$CRUDO"

GB_DIR="${GB_DIR:-/srv/GoldenBridge}"
GB_IMAGEN="${GB_IMAGEN:-goldenbridge:remarcacion-v1-jef-fp32}"
SMART_MODEL="${SMART_MODEL:?falta SMART_MODEL (fp32 gpu.onnx o int8 cpu.onnx, ruta DENTRO de la imagen)}"
ETIQUETA="${ETIQUETA:?falta ETIQUETA (control_corto | C_sube | V_sube | C_baja | V_baja)}"
GB_HEALTH="${GB_HEALTH:-http://127.0.0.1:8765/health}"
GB_CONFIG="${GB_CONFIG:-$AQUI/config/m1_mocks_base.yaml}"
GB_ENV="${GB_ENV:-$AQUI/.env.mocks}"
GB_ENV_VARS="${GB_ENV_VARS:-GOTENKS_API_KEY URSULA_API_KEY CEREBRAS_API_KEY}"
GB_CPUS="${GB_CPUS:-8}"
GB_CPUSET="${GB_CPUSET:-2-5,10-13}"
CLIENTE_CPUSET="${CLIENTE_CPUSET:-6,14}"
M1_DIR="${M1_DIR:-$(cd "$AQUI/../2026-10-10_m1-linea-base" && pwd)}"
MOCKS_DIR="${MOCKS_DIR:-$HOME/analisis_tmp/loopab/mocks}"
MOCKS_CPUSET="${MOCKS_CPUSET:-0-1,8-9}"
PY="${PY:-$GB_DIR/.venv_314/bin/python}"
CLIENTE_PY="${CLIENTE_PY:-$PY}"
SPEECH="${SPEECH:-$GB_DIR/tools/loadtest/utterances/listos}"
ESCALERA="${ESCALERA:-50 75 100}"
REPS="${REPS:-1}"
PACKET_MS="${PACKET_MS:-60}"
MIDSPEECH_N="${MIDSPEECH_N:-17}"
RAMP_S="${RAMP_S:-60}"
WARMUP_S="${WARMUP_S:-60}"
ESTABLE_S="${ESTABLE_S:-600}"
PAUSA_S="${PAUSA_S:-8}"
TURNOS="${TURNOS:-espera}"
HUECO_MS="${HUECO_MS:-800}"
SIN_RESP_S="${SIN_RESP_S:-10}"
LAB_PUERTO="${LAB_PUERTO:-8777}"
FORZAR="${FORZAR:-0}"
LAB="m1slab"

log() { printf '[m1s %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
muere() { log "ERROR: $*"; docker rm -f "$LAB" >/dev/null 2>&1 || true; exit 4; }
trap 'docker rm -f "$LAB" >/dev/null 2>&1 || true' EXIT
sin_ansi() { sed 's/\x1b\[[0-9;]*m//g'; }

# ── requisitos ───────────────────────────────────────────────────────────────
[ -r "$GB_ENV" ] || muere "no se puede leer GB_ENV=$GB_ENV (correr preparar.sh)"
# [MOCKS] nunca las credenciales reales: los mocks no las necesitan y así una URL escapada no factura
[ "$(readlink -f "$GB_ENV")" != "$(readlink -f "$GB_DIR/.env")" ] || muere "GB_ENV apunta al .env real de GB; usar $AQUI/.env.mocks"
[ -r "$GB_CONFIG" ] || muere "no se puede leer GB_CONFIG=$GB_CONFIG (correr preparar.sh)"
# [MOCKS] la config base tiene que apuntar a los mocks ya antes de fusionar nada
"$PY" "$MOCKS_DIR/mock_cfg.py" validate "$GB_CONFIG" || muere "GB_CONFIG no pasa mock_cfg.py validate"
[ -d "$SPEECH" ] || muere "no existe SPEECH=$SPEECH"
[ -f "$M1_DIR/cliente/td1_client_m1.py" ] || muere "no está el cliente de M1 en $M1_DIR/cliente/"
[ -f "$M1_DIR/analiza.py" ] && [ -f "$M1_DIR/auditoria_20261019_extrae.py" ] || muere "faltan analiza.py / extractor en $M1_DIR"
"$CLIENTE_PY" -c 'import audioop, wave; from websockets.asyncio.client import connect' 2>/dev/null \
    || muere "CLIENTE_PY=$CLIENTE_PY necesita websockets>=13 y audioop"
command -v curl >/dev/null || muere "falta curl"
docker image inspect "$GB_IMAGEN" >/dev/null 2>&1 || muere "no está la imagen $GB_IMAGEN"
docker run --rm --network none --entrypoint test "$GB_IMAGEN" -f "$SMART_MODEL" \
    || muere "la imagen $GB_IMAGEN no trae SMART_MODEL=$SMART_MODEL"

# [MOCKS] los tres mocks arriba, en 127.0.0.1, y fijados en MOCKS_CPUSET (se lee /proc, no se confía en taskset)
for p in 18081 18082 18083; do
    code="$(curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$p/health" || true)"
    [ "$code" = "200" ] || muere "mock en 127.0.0.1:$p no responde /health (código '$code'); arrancar con: taskset -c $MOCKS_CPUSET bash $MOCKS_DIR/start_mocks.sh start"
done
[ -r "$MOCKS_DIR/mocks.pids" ] || muere "no existe $MOCKS_DIR/mocks.pids: los mocks no los arrancó start_mocks.sh"
MOCK_PIDS=()
while read -r pid st script; do
    [ -n "$pid" ] || continue
    cpus="$(awk '/^Cpus_allowed_list/ {print $2}' "/proc/$pid/status" 2>/dev/null || echo ?)"
    [ "$cpus" = "$MOCKS_CPUSET" ] || muere "mock $script (pid $pid) corre en CPUs '$cpus', se pedía '$MOCKS_CPUSET'"
    MOCK_PIDS+=("$pid")
done < "$MOCKS_DIR/mocks.pids"
[ "${#MOCK_PIDS[@]}" = "3" ] || muere "se esperaban 3 PIDs de mocks, hay ${#MOCK_PIDS[@]}"
log "mocks arriba (pids ${MOCK_PIDS[*]}) en CPUs $MOCKS_CPUSET"

# Credenciales de relleno: mismo mecanismo que M1 (nunca en argv ni en el log)
ENV_ARGS=()
for nombre in $GB_ENV_VARS; do
    linea="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${nombre}=" "$GB_ENV" | tail -1 || true)"
    [ -n "$linea" ] || { log "AVISO: $nombre no está en GB_ENV"; continue; }
    valor="${linea#*=}"; valor="${valor%$'\r'}"
    valor="${valor#\"}"; valor="${valor%\"}"; valor="${valor#\'}"; valor="${valor%\'}"
    export "$nombre=$valor"
    ENV_ARGS+=(-e "$nombre")
done
unset linea valor
log "credenciales (de relleno) pasadas: $(( ${#ENV_ARGS[@]} / 2 )) de $(wc -w <<<"$GB_ENV_VARS")"

# ── config del laboratorio ───────────────────────────────────────────────────
# Igual que M1: la base + cycle_probe + midspeech_check_every_n_chunks + model_path (SMART_MODEL).
# [MOCKS] la base ya trae host 127.0.0.1 y las URLs de los mocks; la fusión no toca eso, y se
# vuelve a validar el archivo FINAL, que es el que se monta en el contenedor.
CONFIG_LAB="$SALIDA/config_lab.yaml"
docker run --rm --network none --entrypoint python \
    -v "$(readlink -f "$GB_CONFIG")":/in.yaml:ro \
    -e MIDSPEECH_N="$MIDSPEECH_N" -e SMART_MODEL="$SMART_MODEL" "$GB_IMAGEN" -c '
import os, sys, yaml
c = yaml.safe_load(open("/in.yaml")) or {}
c.setdefault("server", {})["cycle_probe"] = True
c.setdefault("smart_turn", {})["midspeech_check_every_n_chunks"] = int(os.environ["MIDSPEECH_N"])
c["smart_turn"]["model_path"] = os.environ["SMART_MODEL"]
yaml.safe_dump(c, sys.stdout, sort_keys=False, allow_unicode=True)
' > "$CONFIG_LAB" || muere "no se pudo armar la config del laboratorio"
"$PY" "$MOCKS_DIR/mock_cfg.py" validate "$CONFIG_LAB" || muere "la config FINAL del laboratorio no pasa mock_cfg.py validate"
log "config del laboratorio: $CONFIG_LAB (sha256 $(sha256sum "$CONFIG_LAB" | cut -c1-12)) validada contra mocks"

{
    echo "## M1s · parámetros"
    echo "etiqueta=$ETIQUETA"
    echo "gb_imagen=$GB_IMAGEN ($(docker image inspect "$GB_IMAGEN" --format '{{.Id}}'))"
    echo "smart_model=$SMART_MODEL"
    echo "escalera=$ESCALERA"; echo "reps=$REPS"; echo "packet_ms=$PACKET_MS"
    echo "midspeech_n=$MIDSPEECH_N"; echo "ramp_s=$RAMP_S"; echo "warmup_s=$WARMUP_S"
    echo "estable_s=$ESTABLE_S"; echo "pausa_s=$PAUSA_S"; echo "speech=$SPEECH"
    echo "turnos=$TURNOS"; echo "hueco_ms=$HUECO_MS"; echo "sin_resp_s=$SIN_RESP_S"
    echo "speech_sha256=$(cat "$SPEECH"/*.wav | sha256sum | cut -d' ' -f1)"
    echo "gb_cpus=$GB_CPUS"; echo "gb_cpuset=$GB_CPUSET"; echo "cliente_cpuset=$CLIENTE_CPUSET"
    echo "lab_puerto=$LAB_PUERTO"; echo "red=host"
    echo "mocks_dir=$MOCKS_DIR"; echo "mocks_cpuset=$MOCKS_CPUSET"; echo "mocks_pids=${MOCK_PIDS[*]}"
    for f in mock_stt.py mock_llm.py mock_tts.py start_mocks.sh mock_cfg.py; do
        echo "mocks_sha256_$f=$(sha256sum "$MOCKS_DIR/$f" | cut -d' ' -f1)"; done
    echo "mocks_cmdline=$(for p in "${MOCK_PIDS[@]}"; do tr '\0' ' ' < /proc/$p/cmdline | sed 's/ *$//'; echo -n ' | '; done)"
    echo "cliente_py=$CLIENTE_PY ($("$CLIENTE_PY" -V 2>&1))"
    echo "cliente_sha256=$(sha256sum "$M1_DIR/cliente/td1_client_m1.py" | cut -d' ' -f1)"
    echo "m1_dir=$M1_DIR"
    echo "config_base=$GB_CONFIG"; echo "config_base_sha256=$(sha256sum "$GB_CONFIG" | cut -d' ' -f1)"
    echo "config_lab_sha256=$(sha256sum "$CONFIG_LAB" | cut -d' ' -f1)"
    echo "credenciales=$GB_ENV_VARS (de $GB_ENV, relleno)"
} > "$SALIDA/parametros.txt"
lscpu -e=CPU,CORE,SOCKET,ONLINE > "$SALIDA/topologia_cpu.txt" 2>/dev/null || echo "lscpu no disponible" > "$SALIDA/topologia_cpu.txt"

# ── un escalón ───────────────────────────────────────────────────────────────
salud_prod() {
    curl -fsS --max-time 3 "$GB_HEALTH" 2>/dev/null \
        | sed -n 's/.*"active_sessions"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p'
}
mocks_foto() {  # [MOCKS] estado de los tres mocks a un archivo (conteos, sin contenido)
    { echo "stt=$(curl -s -m 2 http://127.0.0.1:18081/health)"
      echo "llm=$(curl -s -m 2 http://127.0.0.1:18082/stats)"
      echo "tts=$(curl -s -m 2 http://127.0.0.1:18083/health)"; } > "$1" 2>/dev/null || true
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
    # [MOCKS] el puerto del laboratorio tiene que estar libre en el host (red host, sin NAT)
    ss -ltn | awk '{print $4}' | grep -q ":$LAB_PUERTO\$" && muere "el puerto $LAB_PUERTO ya está ocupado en el host"

    local cpu_args=(--cpus "$GB_CPUS")
    [ -n "$GB_CPUSET" ] && cpu_args+=(--cpuset-cpus "$GB_CPUSET")
    docker rm -f "$LAB" >/dev/null 2>&1 || true
    # [MOCKS] --network host en vez de -p: GB dentro del contenedor alcanza 127.0.0.1:1808x del host
    # y escucha SOLO en 127.0.0.1:$LAB_PUERTO porque server.host es 127.0.0.1 en la config validada.
    docker run -d --name "$LAB" "${cpu_args[@]}" --network host \
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

    # Leer de vuelta lo efectivo. No basta con que docker haya aceptado.
    local escucha; escucha="$(ss -ltn | awk -v p=":$LAB_PUERTO" 'length($4) > length(p) && substr($4, length($4) - length(p) + 1) == p {print $4}' | tr '\n' ' ')"
    {
        echo "paso=$paso"; echo "rep=$rep"; echo "n=$n"; echo "arranque_s=$t"
        docker inspect "$LAB" --format \
'nano_cpus={{.HostConfig.NanoCpus}}
cpuset={{.HostConfig.CpusetCpus}}
red={{.HostConfig.NetworkMode}}
imagen={{.Image}}'
        echo "cgroup_cpu_max=$(docker exec "$LAB" cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo ?)"
        echo "cgroup_cpuset=$(docker exec "$LAB" cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null || echo ?)"
        echo "nproc_dentro=$(docker exec "$LAB" nproc 2>/dev/null || echo ?)   # nproc obedece OMP_NUM_THREADS=1: no mide CPUs"
        echo "process_cpu_count_dentro=$(docker exec "$LAB" python -c 'import os; print(os.process_cpu_count())' 2>/dev/null || echo ?)"
        echo "escucha_host=$escucha"
        echo "prod_activas=${prod:-?}"
    } > "$dir/efectivo.txt"
    # [MOCKS] GB debe escuchar SOLO en loopback; con red host, 0.0.0.0 sería exponerlo a la red
    [[ "$escucha" == "127.0.0.1:$LAB_PUERTO "* ]] && ! grep -qE '(0\.0\.0\.0|\*|\[::\]):' <<<"$escucha" \
        || muere "GB escucha en '$escucha' en vez de 127.0.0.1:$LAB_PUERTO ($paso)"
    [ "$(docker inspect "$LAB" --format '{{.HostConfig.CpusetCpus}}')" = "$GB_CPUSET" ] || muere "cpuset del contenedor no es $GB_CPUSET"
    [ "$(docker exec "$LAB" cat /sys/fs/cgroup/cpuset.cpus.effective 2>/dev/null)" = "$GB_CPUSET" ] || muere "cgroup cpuset efectivo no es $GB_CPUSET ($paso)"
    ps -eo pid,pcpu,rss,nlwp,comm --sort=-pcpu | head -12 > "$dir/procesos_inicio.txt" || true
    mocks_foto "$dir/mocks_pre.txt"
    local arranque; arranque="$(docker logs "$LAB" 2>&1 | sin_ansi)"
    grep -q "referencia OK" <<<"$arranque" || muere "el gate no imprimió 'referencia OK' en $paso"
    grep -q "Smart Turn processor created.*model_path=$SMART_MODEL" <<<"$arranque" || {
        docker logs "$LAB" > "$dir/gb.log" 2>&1 || true
        muere "GB no cargó $SMART_MODEL en $paso (ver 'Smart Turn processor created' en $dir/gb.log)"; }
    local hilos
    hilos="$(docker exec "$LAB" sh -c 'echo "$GB_ORT_INTRA_OP $OMP_NUM_THREADS $OPENBLAS_NUM_THREADS"' 2>/dev/null || echo ?)"
    echo "hilos_ort_omp_openblas=$hilos" >> "$dir/efectivo.txt"
    [ "$hilos" = "1 1 1" ] || muere "hilos efectivos '$hilos' en $paso; se esperaba '1 1 1'"
    if grep -qiE "(LLM|TTS|STT) not available" <<<"$arranque"; then
        docker logs "$LAB" > "$dir/gb.log" 2>&1 || true
        muere "proveedores (mocks) no disponibles en $paso (ver $dir/gb.log)"
    fi

    # docker stats cada 5 s + [MOCKS] CPU de cada mock (deltas de /proc, % de un núcleo)
    ( while docker ps -q -f name="^${LAB}$" | grep -q .; do
          printf '%s\t%s\n' "$(date -u +%s)" \
              "$(docker stats --no-stream --format '{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}' "$LAB" 2>/dev/null)"
          sleep 5
      done ) > "$dir/stats.tsv" &
    local muestreo=$!
    ( hz=$(getconf CLK_TCK); declare -A prev; t_prev=$(date -u +%s.%N)
      for p in "${MOCK_PIDS[@]}"; do prev[$p]=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null || echo 0); done
      echo -e "ts_utc\tdt_s$(printf '\tcpu_pct_%s' "${MOCK_PIDS[@]}")"
      while docker ps -q -f name="^${LAB}$" | grep -q .; do
          sleep 5; t_now=$(date -u +%s.%N); dt=$(awk -v a=$t_now -v b=$t_prev 'BEGIN{printf "%.2f", a-b}'); t_prev=$t_now
          linea="${t_now%.*}\t$dt"
          for p in "${MOCK_PIDS[@]}"; do
              now=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null || echo 0)
              linea="$linea\t$(awk -v a=$now -v b=${prev[$p]} -v hz=$hz -v dt=$dt 'BEGIN{printf "%.1f",(a-b)*100/hz/dt}')"
              prev[$p]=$now
          done
          echo -e "$linea"
      done ) > "$dir/mocks_cpu.tsv" &
    local muestreo_mocks=$!

    local t0; t0="$(date -u +%s.%N)"
    echo "t0_epoch=$t0" >> "$dir/efectivo.txt"
    log "$paso [$ETIQUETA $(basename "$SMART_MODEL")]: $n llamadas · ramp ${RAMP_S}s + warm-up ${WARMUP_S}s + estable ${ESTABLE_S}s"
    local pre=(); [ -n "$CLIENTE_CPUSET" ] && pre=(taskset -c "$CLIENTE_CPUSET")
    local turnos=()
    case "$TURNOS" in
        espera) turnos=(--espera-bot --hueco-ms "$HUECO_MS" --sin-respuesta-s "$SIN_RESP_S") ;;
        reloj)  ;;
        *) muere "TURNOS=$TURNOS no es espera ni reloj" ;;
    esac
    set +e
    "${pre[@]}" "$CLIENTE_PY" -W ignore "$M1_DIR/cliente/td1_client_m1.py" \
        --url "ws://127.0.0.1:$LAB_PUERTO/v1/agent/converse" \
        --sessions "$n" --controls 3 --ramp "$RAMP_S" --hold "$((WARMUP_S + ESTABLE_S))" \
        --speech "$SPEECH" --utt-s 0 --pause-s "$PAUSA_S" --packet-ms "$PACKET_MS" \
        --barge-in-ms 1800 --barge-in-cada 3 "${turnos[@]}" \
        --out "$dir/cliente.tsv" > "$dir/cliente.log" 2>&1
    local rc=$?
    set -e
    echo "cliente_rc=$rc" >> "$dir/efectivo.txt"
    (( rc == 0 )) || log "ERROR: el cliente terminó con rc=$rc en $paso (el escalón quedará inválido en la compuerta/veredicto)"
    [ -n "$CLIENTE_CPUSET" ] && echo "cliente_cpuset_leido=$CLIENTE_CPUSET" >> "$dir/efectivo.txt"

    ps -eo pid,pcpu,rss,nlwp,comm --sort=-pcpu | head -12 > "$dir/procesos_fin.txt" || true
    mocks_foto "$dir/mocks_post.txt"
    docker logs --timestamps "$LAB" > "$dir/gb.log" 2>&1 || true
    docker rm -f "$LAB" >/dev/null 2>&1 || true
    wait "$muestreo" 2>/dev/null || true
    wait "$muestreo_mocks" 2>/dev/null || true

    # [MOCKS] lectura de vuelta del camino integrado y de otros cuellos. Conteos de router/AMD/not available
    # sobre TODO el log (una sola aparición ya es otro camino); latencias y conteos de turnos sobre la
    # VENTANA ESTABLE [t0+2+ramp+warm, t0+ramp+warm+estable], la misma que usa el extractor de M1.
    local L="$dir/gb.sin_ansi.tmp"; sin_ansi < "$dir/gb.log" > "$L"
    {
        echo "router_transitions=$(grep -c 'Router transition executed' "$L" || true)"
        echo "amd_classifications=$(grep -c 'AMD classification' "$L" || true)"
        echo "not_available=$(grep -ci 'not available' "$L" || true)"
        echo "athrow=$(grep -c "didn't stop after athrow" "$L" || true)   # telemetría: M1 real n=100 tuvo 216; comparar entre brazos"
        echo "aclose_running=$(grep -c "asynchronous generator is already running" "$L" || true)"
        T0="$t0" INI=$((2 + RAMP_S + WARMUP_S)) FIN=$((RAMP_S + WARMUP_S + ESTABLE_S)) "$PY" -B - "$L" <<'PY2'
import os, re, sys, calendar, datetime
t0 = float(os.environ["T0"]); ini = t0 + float(os.environ["INI"]); fin = t0 + float(os.environ["FIN"])
TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)Z")
def num(s, k):
    m = re.search(r"\b%s=([0-9.]+)" % k, s); return float(m.group(1)) if m else None
def pct(v, q):
    v = sorted(v); return v[min(len(v)-1, int(len(v)*q))] if v else None
def ts(s):
    m = TS.match(s)
    if not m: return None
    return calendar.timegm(datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple()) + float("0." + m.group(2)[:6])
tts_wait, first, ttft_n, fin_n, fin_txt, auto, drained, turnos = [], [], 0, 0, 0, 0, 0, 0
for s in open(sys.argv[1], errors="replace"):
    t = ts(s)
    if t is None or not (ini <= t <= fin): continue
    if "LATENCY BREAKDOWN" in s:
        v = num(s, "tts_latency_ms"); tts_wait.append(v) if v is not None else None
    elif "Ursula TTS persistent WS first chunk" in s:
        v = num(s, "latency_ms"); first.append(v) if v is not None else None
    elif "Cerebras LLM time to first token" in s: ttft_n += 1
    elif "STT Finalize completed" in s:
        fin_n += 1; fin_txt += 0 if "transcript_preview=''" in s else 1
    elif "STT auto-flush: no Finalize" in s: auto += 1
    elif "Drained stale TTS chunks" in s: drained += 1
    elif "Smart Turn: Turn complete" in s: turnos += 1
f = lambda x: "" if x is None else f"{x:.0f}"
print(f"ventana_ini_rel={os.environ['INI']}"); print(f"ventana_fin_rel={os.environ['FIN']}")
print(f"turnos_ventana={turnos}"); print(f"llm_ttft_n={ttft_n}"); print(f"stt_finalize_n={fin_n}"); print(f"stt_finalize_con_texto={fin_txt}")
print(f"auto_flush_n={auto}"); print(f"drained_stale_tts={drained}")
print(f"tts_latency_breakdown_n={len(tts_wait)}"); print(f"tts_latency_breakdown_p50={f(pct(tts_wait,.5))}"); print(f"tts_latency_breakdown_p90={f(pct(tts_wait,.9))}")
print(f"tts_first_chunk_n={len(first)}"); print(f"tts_first_chunk_p50={f(pct(first,.5))}"); print(f"tts_first_chunk_p90={f(pct(first,.9))}")
PY2
    } > "$dir/lectura.txt"
    rm -f "$L"
    # Primarias (inferencias/s, cierres/s, inference_ms p50/p90/mín, intentos por turno): extractor de la auditoría de M1
    python3 -B "$M1_DIR/auditoria_20261019_extrae.py" "$SALIDA/auditoria" --estable "$ESTABLE_S" "$dir" >/dev/null \
        || muere "el extractor de la auditoría falló en $paso: sin primarias no hay resultado"
    log "$paso: cliente rc=$rc · $(grep -c CYCLE-PROBE "$dir/gb.log" || true) líneas CYCLE-PROBE · $(cat "$dir/lectura.txt" | tr '\n' ' ' | cut -c1-200)"
}

# ── la escalera ──────────────────────────────────────────────────────────────
for rep in $(seq 1 "$REPS"); do
    if (( rep % 2 == 0 )); then orden="$(tr ' ' '\n' <<<"$ESCALERA" | tac | tr '\n' ' ')"
    else orden="$ESCALERA"; fi
    for n in $orden; do escalon "$rep" "$n"; done
done

python3 -B "$M1_DIR/analiza.py" "$CRUDO" "$SALIDA" \
    --ramp "$RAMP_S" --warmup "$WARMUP_S" --estable "$ESTABLE_S"
log "resumen: $SALIDA/resumen.tsv · primarias: $SALIDA/auditoria/*/resumen_auditoria.tsv"
