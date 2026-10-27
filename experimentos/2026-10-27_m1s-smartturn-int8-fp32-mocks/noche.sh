#!/usr/bin/env bash
# Orquestador: preparar → mocks fijados → control corto → compuerta → 4 escaleras alternadas → mocks abajo.
# Uso (siempre desacoplado):
#   setsid nohup bash noche.sh control                 > noche.log 2>&1 &   # control corto + compuerta (≈12 min)
#   setsid nohup bash noche.sh todo                    > noche.log 2>&1 &   # control y, si SATURADO, las 4 escaleras
#   CONTROL_SELLO=<sello> setsid nohup bash noche.sh escaleras > noche.log 2>&1 &   # sólo escaleras; exige un control SATURADO
#   SOLO="C_baja V_baja" CONTROL_SELLO=<sello> ... escaleras                # continuar sólo las pendientes
# Garantías (Codex B3/B4, 2026-10-27):
#   - modo validado antes de hacer nada; `escaleras` exige CONTROL_SELLO con veredicto SATURADO (se recalcula) y
#     con la MISMA config base, mocks e imagen que ahora (sha256 en parametros.txt);
#   - el tope se congela al arrancar (TOPE_EPOCH) y NO se renueva; preparar, arranque de mocks y cada corrida
#     van bajo `timeout` con ese plazo (sin --foreground: la señal va al grupo, llega a correr.sh y al cliente):
#     si se pasa, TERM → correr.sh borra el contenedor y aquí se paran los mocks;
#   - un solo orquestador a la vez (flock en noche.lock); traps instalados ANTES de arrancar nada; TERM/INT/HUP
#     limpian. SIGKILL no: ver README (limpieza manual).
#   - NOCHE_DRY=1 sustituye preparar/mocks/run.sh/compuerta por ecos (para probar la lógica sin Docker);
#     NOCHE_DRY_COMPUERTA_RC fija el rc simulado de la compuerta.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
MODO="${1:-}"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$AQUI"
log() { printf '[noche %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
case "$MODO" in control|todo|escaleras) ;; *) log "uso: noche.sh control|todo|escaleras (recibí '$MODO')"; exit 2;; esac

export GB_IMAGEN="${GB_IMAGEN:-goldenbridge:remarcacion-v1-jef-fp32}"
export GB_CPUSET="${GB_CPUSET:-2-5,10-13}" CLIENTE_CPUSET="${CLIENTE_CPUSET:-6,14}" LAB_PUERTO="${LAB_PUERTO:-8777}"
export PACKET_MS="${PACKET_MS:-60}" MIDSPEECH_N="${MIDSPEECH_N:-17}"
export GB_CONFIG="$AQUI/config/m1_mocks_base.yaml" GB_ENV="$AQUI/.env.mocks"
export MOCKS_DIR="${MOCKS_DIR:-$HOME/analisis_tmp/loopab/mocks}" MOCKS_CPUSET="${MOCKS_CPUSET:-0-1,8-9}"
FP32=/app/smart-turn/smart-turn-v3.2-gpu.onnx
INT8=/app/smart-turn/smart-turn-v3.2-cpu.onnx
TOPE_UTC="${TOPE_UTC:-05:45}"            # hora UTC a la que TODO tiene que haber terminado (DEV se apaga 06:00)
DUR_ESCALERA_MIN="${DUR_ESCALERA_MIN:-40}"
DUR_CONTROL_MIN="${DUR_CONTROL_MIN:-12}"
DRY="${NOCHE_DRY:-0}"
SOLO="${SOLO:-C_sube V_sube C_baja V_baja}"

ejecutar() { if [ "$DRY" = 1 ]; then log "[dry] $*"; return 0; else "$@"; fi; }
# [B4] El tope se congela UNA vez al arrancar (TOPE_EPOCH) y nunca se renueva: pasado el tope, todo se niega.
# Si se arranca entre TOPE_UTC y 14:00 UTC (DEV apagado 06:00-14:00) no se hace nada.
hhmm=$(date -u +%H%M)
if [ "$hhmm" -ge "${TOPE_UTC/:/}" ] && [ "$hhmm" -lt 1400 ]; then
    log "son las $(date -u +%H:%M) UTC: ventana de apagado de DEV (${TOPE_UTC}-14:00), no se arranca"; exit 7; fi
TOPE_EPOCH="${TOPE_EPOCH:-$( fin=$(date -u -d "today $TOPE_UTC" +%s); (( $(date -u +%s) < fin )) || fin=$(date -u -d "tomorrow $TOPE_UTC" +%s); echo "$fin" )}"
export TOPE_EPOCH
restante_s() { echo $(( TOPE_EPOCH - $(date -u +%s) - 30 )); }   # segundos hasta el tope, con 30 s de margen
cabe() { (( $(date -u +%s) + $1 * 60 <= TOPE_EPOCH )); }           # ¿una corrida de $1 minutos termina antes del tope?
con_plazo() {  # ejecuta $@ bajo el plazo absoluto restante (TERM al grupo; -k 30)
    local r; r=$(restante_s); (( r > 0 )) || { log "sin plazo para: $*"; return 7; }
    if [ "$DRY" = 1 ]; then log "[dry] (plazo ${r}s) $*"; return 0; fi
    timeout -k 30 -s TERM "$r" "$@"; }
ultimo_sello() { ls -1d "$AQUI"/resultados/2* 2>/dev/null | tail -1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

# ── exclusión y limpieza, antes de tocar nada ───────────────────────────────
exec 8>"$AQUI/noche.lock"
flock -n 8 || { log "otro noche.sh está corriendo (noche.lock)"; exit 3; }
MOCKS_ARRANCADOS=0
limpiar() {
    local rc=$?
    log "limpieza (rc=$rc): contenedor m1slab y mocks"
    [ "$DRY" = 1 ] || docker rm -f m1slab >/dev/null 2>&1 || true
    [ "$DRY" = 1 ] || bash "$MOCKS_DIR/start_mocks.sh" stop || true
    exit "$rc"
}
trap limpiar EXIT; trap 'exit 130' INT; trap 'exit 143' TERM HUP

# ── horario y preparación ───────────────────────────────────────────────────
PRIMERA_MIN=$([ "$MODO" = escaleras ] && echo "$DUR_ESCALERA_MIN" || echo "$DUR_CONTROL_MIN")
cabe "$PRIMERA_MIN" || { log "no cabe la primera corrida ($PRIMERA_MIN min) antes de $(date -u -d @"$TOPE_EPOCH" +%FT%TZ)"; exit 7; }
log "tope congelado: $(date -u -d @"$TOPE_EPOCH" +%FT%TZ) (restan $(( $(restante_s) / 60 )) min)"
for tok in $SOLO; do case "$tok" in C_sube|V_sube|C_baja|V_baja) ;; *) log "SOLO contiene '$tok', que no es una etiqueta válida"; exit 2;; esac; done
con_plazo bash "$AQUI/preparar.sh" || { log "preparar.sh falló o se quedó sin plazo"; exit 2; }
SHA_CFG=$([ -f "$AQUI/config/m1_mocks_base.yaml" ] && sha "$AQUI/config/m1_mocks_base.yaml" || echo "dry")
IMG_ID=$([ "$DRY" = 1 ] && echo "dry" || docker image inspect "$GB_IMAGEN" --format '{{.Id}}')

# `escaleras`: exige un control SATURADO compatible con lo que se va a correr
if [ "$MODO" = escaleras ]; then
    [ -n "${CONTROL_SELLO:-}" ] || { log "modo escaleras exige CONTROL_SELLO=<sello del control SATURADO>"; exit 2; }
    C="$AQUI/resultados/$CONTROL_SELLO"
    [ -d "$C" ] || { log "no existe $C"; exit 2; }
    if [ "$DRY" = 1 ]; then rc="${NOCHE_DRY_COMPUERTA_RC:-0}"; log "[dry] compuerta sobre $CONTROL_SELLO rc=$rc"
    else python3 -B "$AQUI/compuerta_control.py" "$C"; rc=$?; fi
    [ "$rc" -eq 0 ] || { log "el control $CONTROL_SELLO no dicta SATURADO (rc=$rc)"; exit 6; }
    if [ "$DRY" != 1 ]; then
        p="$C/parametros.txt"
        grep -q "^config_base_sha256=$SHA_CFG\$" "$p" || { log "la config base cambió desde el control"; exit 6; }
        grep -q "^gb_imagen=.*$IMG_ID" "$p" || { log "la imagen cambió desde el control"; exit 6; }
        for f in mock_stt.py mock_llm.py mock_tts.py start_mocks.sh mock_cfg.py; do
            grep -q "^mocks_sha256_$f=$(sha "$MOCKS_DIR/$f")\$" "$p" || { log "el mock $f cambió desde el control"; exit 6; }
        done
        grep -q "^etiqueta=control_corto\$" "$p" || { log "$CONTROL_SELLO no es un control corto"; exit 6; }
    fi
    log "control $CONTROL_SELLO: SATURADO y compatible"
fi

# ── mocks: reutilizar si ya están arriba y fijados; si no, (stop y) start fijado ────────────────
mocks_ok() { local p; for p in 18081 18082 18083; do
        curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$p/health" | grep -q 200 || return 1; done
    [ -f "$MOCKS_DIR/mocks.pids" ]; }
mocks_fijados() { local pid st script; while read -r pid st script; do [ -n "$pid" ] || continue
        [ "$(awk '/^Cpus_allowed_list/ {print $2}' "/proc/$pid/status" 2>/dev/null)" = "$MOCKS_CPUSET" ] || return 1
    done < "$MOCKS_DIR/mocks.pids"; }
if [ "$DRY" = 1 ]; then log "[dry] mocks"
elif mocks_ok && mocks_fijados; then log "mocks ya arriba y fijados en $MOCKS_CPUSET: se reutilizan (se paran al salir)"
else
    [ -f "$MOCKS_DIR/mocks.pids" ] && { log "mocks sin /health o sin fijar: stop y start"; bash "$MOCKS_DIR/start_mocks.sh" stop; }
    con_plazo taskset -c "$MOCKS_CPUSET" bash "$MOCKS_DIR/start_mocks.sh" start || { log "no se pudieron arrancar los mocks (o sin plazo)"; exit 2; }
    MOCKS_ARRANCADOS=1
fi

# ── registro de la secuencia ────────────────────────────────────────────────
SEQ="$AQUI/secuencias/$(date -u +%Y%m%dT%H%M%SZ)_$MODO"; mkdir -p "$SEQ"
{ echo "modo=$MODO"; echo "inicio_utc=$(date -u +%FT%TZ)"; echo "tope_utc=$TOPE_UTC"; echo "tope_epoch=$TOPE_EPOCH ($(date -u -d @"$TOPE_EPOCH" +%FT%TZ))"; echo "dry=$DRY"; echo "solo=$SOLO"
  echo "control_sello=${CONTROL_SELLO:-}"; echo "config_base_sha256=$SHA_CFG"; echo "gb_imagen_id=$IMG_ID"
  for f in README.md correr.sh run.sh preparar.sh compuerta_control.py veredicto_ab.py noche.sh; do
      [ -f "$AQUI/$f" ] && echo "sha256_$f=$(sha "$AQUI/$f")"; done
  echo "sha256_analiza=$(sha "$AQUI/../2026-10-10_m1-linea-base/analiza.py")"
  echo "sha256_extractor=$(sha "$AQUI/../2026-10-10_m1-linea-base/auditoria_20261019_extrae.py")"
} > "$SEQ/secuencia.txt"
[ -f "$AQUI/corridas.tsv" ] || echo -e "sello\tetiqueta\tmodelo\tescalera\testable_s\trc\tsecuencia" > "$AQUI/corridas.tsv"

correr() {  # etiqueta modelo escalera estable_s  → corre bajo el plazo absoluto
    local etiqueta="$1" modelo="$2" escalera="$3" estable="$4" rc sello
    local plazo; plazo=$(restante_s); (( plazo > 0 )) || { log "sin plazo para $etiqueta"; return 7; }
    if [ "$DRY" = 1 ]; then log "[dry] run.sh ETIQUETA=$etiqueta SMART_MODEL=$modelo ESCALERA='$escalera' ESTABLE_S=$estable (timeout ${plazo}s)"; rc=0; sello="dry"
    else
        ETIQUETA="$etiqueta" SMART_MODEL="$modelo" ESCALERA="$escalera" REPS=1 ESTABLE_S="$estable" \
            con_plazo bash "$AQUI/run.sh"; rc=$?
        sello="$(basename "$(ultimo_sello)")"
        (( rc == 124 )) && log "PLAZO: $etiqueta cortada por timeout al llegar al tope congelado"
    fi
    echo -e "$sello\t$etiqueta\t$(basename "$modelo")\t$escalera\t$estable\t$rc\t$(basename "$SEQ")" >> "$AQUI/corridas.tsv"
    echo "$etiqueta=$sello rc=$rc" >> "$SEQ/secuencia.txt"
    return $rc
}

if [ "$MODO" = control ] || [ "$MODO" = todo ]; then
    log "control corto: fp32, 100+3, estable 300 s"
    correr control_corto "$FP32" "100" 300 || { log "el control falló (rc=$?)"; exit 4; }
    if [ "$DRY" = 1 ]; then v="${NOCHE_DRY_COMPUERTA_RC:-0}"; log "[dry] compuerta rc=$v"
    else python3 -B "$AQUI/compuerta_control.py" "$(ultimo_sello)" | tee "$SEQ/compuerta_control.txt"; v=${PIPESTATUS[0]}; fi
    echo "veredicto_control_rc=$v" >> "$SEQ/secuencia.txt"
    [ "$v" -eq 0 ] || { log "compuerta: rc=$v, no se lanzan las escaleras"; exit "$v"; }
    [ "$MODO" = todo ] || exit 0
fi

# 4 escaleras alternadas: control↑, variante↑, control↓, variante↓ (sólo las de SOLO)
for spec in "C_sube $FP32 50_75_100" "V_sube $INT8 50_75_100" "C_baja $FP32 100_75_50" "V_baja $INT8 100_75_50"; do
    set -- $spec
    grep -qw "$1" <<<"$SOLO" || { log "$1 no está en SOLO, se omite"; continue; }
    cabe "$DUR_ESCALERA_MIN" || { log "no cabe $1 antes del tope congelado; pendiente (SOLO=\"$1 …\" mañana desde 14:00 UTC, con control nuevo)"; echo "pendiente=$1" >> "$SEQ/secuencia.txt"; exit 8; }
    log "escalera $1 ($(basename "$2")) ${3//_/ }"
    correr "$1" "$2" "${3//_/ }" 600 || { log "escalera $1 terminó con error: se detiene la secuencia (el diseño alternado necesita las 4)"; exit 4; }
done
log "secuencia completa; veredicto A/B: python3 veredicto_ab.py $AQUI/corridas.tsv"
exit 0
