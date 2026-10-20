#!/usr/bin/env python3
"""Auditoría M1 2026-10-19 (AUDITORIA_20261019.md): extrae de cada escalón crudo
(gb.log + cliente.tsv + stats.tsv) lo que analiza.py no mira: todas las líneas
CYCLE-PROBE (cycle_probe.tsv), las líneas de cierre de turno con close_attempts e
inference_ms (turn_complete.tsv), barge-in (barge_in.tsv), una tabla por minuto
(por_minuto.tsv) y un resumen de la ventana estable (resumen_auditoria.tsv).
Sólo biblioteca estándar. Los ensayos de 120 s se corren con --estable 120."""
import os, re, sys, statistics, json, glob
from datetime import datetime, timedelta, timezone

ANSI = re.compile(r"\x1b\[[0-9;]*m")
KV = re.compile(r"(\w+)=('[^']*'|\"[^\"]*\"|\S+)")

def ts_docker(tok):
    base, _, frac = tok.rstrip("Z").partition(".")
    t = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return t.timestamp() + (float("0." + frac[:6]) if frac else 0.0)

def pct(xs, q):
    if not xs: return None
    s = sorted(xs); return s[min(len(s)-1, int(len(s)*q))]

def f(x, nd=1):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))

def leer_efectivo(p):
    d = {}
    for l in open(p):
        if "=" in l:
            k, v = l.rstrip("\n").split("=", 1); d[k] = v
    return d

EVENTOS = {
    "CYCLE-PROBE": "cp",
    "Smart Turn: Turn complete (mid-speech periodic check)": "tc_mid",
    "Smart Turn: Turn complete": "tc_st",
    "Smart Turn: Fallback timer triggered turn_complete": "tc_fb",
    "Smart Turn: Turn incomplete, fallback timer started": "st_incomplete",
    "Turn close: silence transition": "close_sil",
    "Turn close: arrival cadence": "close_arr",
    "Barge-in triggered": "bi_trig",
    "Barge-in gates not met during TTS": "bi_notmet",
    "TTS cancellation complete": "tts_cancel",
    "LATENCY BREAKDOWN - Time to first audio byte (streaming)": "lat",
    "Cerebras LLM time to first token": "ttft",
    "STT Finalize completed": "stt_fin",
    "Ursula TTS persistent WS first chunk": "tts_first",
    "No transcript from audio": "no_transcript",
    "Discarded stale STT results at turn resolution": "stale_stt",
    "LLM circuit breaker: TTFT": "cb",
    "STT auto-flush": "autoflush",
    "Session inbox full": "inbox_full",
    "Agent-speech barrier timed out": "barrier_to",
    "Audio batch job failed": "batch_fail",
    "Agent response (streaming)": "agent_resp",
    "Starting streaming speech pipeline (Finalize)": "pipeline",
}
ORDEN = sorted(EVENTOS, key=len, reverse=True)  # el más largo primero

def parse_gb(path, t0):
    ev = {v: [] for v in EVENTOS.values()}
    nivel = {"error": 0, "warning": 0}
    for raw in open(path, encoding="utf-8", errors="replace"):
        tok, _, resto = raw.partition(" ")
        try: t = ts_docker(tok)
        except ValueError: continue
        resto = ANSI.sub("", resto)
        if "[error" in resto or "[critical" in resto: nivel["error"] += 1
        elif "[warning" in resto: nivel["warning"] += 1
        for k in ORDEN:
            if k in resto:
                kv = dict(KV.findall(resto))
                kv["_t"] = t - t0
                ev[EVENTOS[k]].append(kv)
                break
    return ev, nivel

def parse_cliente(path, t0):
    dia = datetime.fromtimestamp(t0, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for l in open(path, encoding="utf-8"):
        c = l.rstrip("\n").split("\t")
        if len(c) < 5 or c[0] == "ts_utc": continue
        h = datetime.strptime(c[0], "%H:%M:%S.%f")
        t = (dia + timedelta(hours=h.hour, minutes=h.minute, seconds=h.second, microseconds=h.microsecond)).timestamp()
        if t < t0 - 3600: t += 86400
        rows.append(dict(t=t - t0, kind=c[1], sid=c[2], role=c[3], v=float(c[4]), extra=c[5] if len(c) > 5 else ""))
    return rows

def parse_stats(path, t0):
    out = []
    for l in open(path):
        c = l.rstrip("\n").split("\t")
        if len(c) < 3 or not c[1]: continue
        try: out.append((float(c[0]) - t0, float(c[1].rstrip("%")) / 100.0))
        except ValueError: pass
    return out

def num(kv, k):
    try: return float(kv[k])
    except (KeyError, ValueError): return None

def main(d, out, ramp=60, warm=60, estable=600):
    ef = leer_efectivo(os.path.join(d, "efectivo.txt"))
    t0 = float(ef["t0_epoch"]); n = int(ef["n"])
    ini, fin = 2 + ramp + warm, ramp + warm + estable
    sello = os.path.basename(os.path.dirname(d)); paso = os.path.basename(d)
    od = os.path.join(out, f"{sello}_{paso}"); os.makedirs(od, exist_ok=True)
    ev, nivel = parse_gb(os.path.join(d, "gb.log"), t0)
    cli = parse_cliente(os.path.join(d, "cliente.tsv"), t0)
    st = parse_stats(os.path.join(d, "stats.tsv"), t0)
    R = {"sello": sello, "paso": paso, "n": n, "t0_utc": datetime.fromtimestamp(t0, timezone.utc).strftime("%H:%M:%S")}

    # ---- CYCLE-PROBE crudo
    cpk = ["cycles", "frames", "frames_per_cycle", "cierres", "pct_cierres", "inbox", "carry", "consumed"]
    fases = ["cycle", "pre", "submit", "job", "resume", "resume_vad", "smartturn", "post", "steno", "otros"]
    with open(os.path.join(od, "cycle_probe.tsv"), "w") as fo:
        fo.write("t_rel\tsession\t" + "\t".join(cpk) + "\t" + "\t".join(f"{p}_p50\t{p}_p90" for p in fases) + "\n")
        for kv in ev["cp"]:
            fo.write(f"{kv['_t']:.1f}\t{kv.get('session_id','')[:8]}\t" + "\t".join(kv.get(k, "") for k in cpk) + "\t"
                     + "\t".join(f"{kv.get(p+'_p50','')}\t{kv.get(p+'_p90','')}" for p in fases) + "\n")
    # por minuto (relativo a t0): mediana de p90 por fase, mediana de fpc, pct_cierres
    def minuto(t): return int(t // 60)
    cp_min = {}
    for kv in ev["cp"]:
        m = minuto(kv["_t"]); cp_min.setdefault(m, []).append(kv)
    # ---- turnos
    tc_all = ev["tc_st"] + ev["tc_mid"] + ev["tc_fb"]
    # ---- cliente
    ttfab = [r for r in cli if r["kind"] == "ttfab"]
    corte = [r for r in cli if r["kind"] == "corte"]
    end_ok = [r for r in cli if r["kind"] == "end_ok" and r["role"] == "load"]
    ping_ctrl = [r for r in cli if r["kind"] == "ping" and r["role"] == "ctrl"]
    # ---- tabla por minuto
    with open(os.path.join(od, "por_minuto.tsv"), "w") as fo:
        cols = ["min", "cp_lineas", "fpc_med", "pct_cierres_med", "inbox_med", "cycle_p90_med", "job_p90_med", "resume_vad_p90_med",
                "smartturn_p90_med", "smartturn_p90_max", "post_p90_med", "post_p90_max", "steno_p90_med", "pre_p90_med", "otros_p90_med",
                "closes_sil", "tc_total", "tc_st", "tc_mid", "tc_fb", "close_attempts_sum", "infer_ms_p50", "infer_ms_p90", "infer_ms_max",
                "bi_trig", "bi_notmet", "stt_lat_p50", "stt_lat_p90", "llm_first_p50", "llm_first_p90", "ttft_p50", "ttft_p90", "tts_first_p50", "tts_first_p90",
                "no_transcript", "stale_stt", "cb", "autoflush", "ttfab_n", "ttfab_p50", "ttfab_p90", "corte_cortado_n", "corte_cortado_p50", "corte_cortado_p90", "corte_natural_n",
                "ping_ctrl_p99", "cpu_med"]
        fo.write("\t".join(cols) + "\n")
        mmax = int(max([kv["_t"] for kv in ev["cp"]] + [r["t"] for r in cli] + [0]) // 60)
        for m in range(0, mmax + 1):
            lo, hi = m * 60, (m + 1) * 60
            sel = lambda L: [x for x in L if lo <= x["_t"] < hi]
            selc = lambda L: [x for x in L if lo <= x["t"] < hi]
            cps = cp_min.get(m, [])
            def med(L, k):
                xs = [num(x, k) for x in L]; xs = [x for x in xs if x is not None]
                return statistics.median(xs) if xs else None
            def mx(L, k):
                xs = [num(x, k) for x in L]; xs = [x for x in xs if x is not None]
                return max(xs) if xs else None
            def pq(L, k, q):
                xs = [num(x, k) for x in L]; xs = [x for x in xs if x is not None]
                return pct(xs, q)
            tcs = sel(tc_all)
            inf = [num(x, "inference_ms") for x in sel(ev["tc_st"])]; inf = [x for x in inf if x is not None]
            ca = [num(x, "close_attempts") for x in tcs]; ca = [x for x in ca if x is not None]
            tt = [r["v"] for r in selc(ttfab)]
            cc = [r["v"] for r in selc(corte) if "fin=natural" not in r["extra"]]
            cn = [r for r in selc(corte) if "fin=natural" in r["extra"]]
            pc = [r["v"] for r in selc(ping_ctrl)]
            cpu = [c for t, c in st if lo <= t < hi]
            row = [m, len(cps), f(med(cps, "frames_per_cycle"), 2), f(med(cps, "pct_cierres")), f(med(cps, "inbox")),
                   f(med(cps, "cycle_p90")), f(med(cps, "job_p90")), f(med(cps, "resume_vad_p90")),
                   f(med(cps, "smartturn_p90")), f(mx(cps, "smartturn_p90")), f(med(cps, "post_p90")), f(mx(cps, "post_p90")),
                   f(med(cps, "steno_p90")), f(med(cps, "pre_p90")), f(med(cps, "otros_p90")),
                   len(sel(ev["close_sil"])), len(tcs), len(sel(ev["tc_st"])), len(sel(ev["tc_mid"])), len(sel(ev["tc_fb"])),
                   int(sum(ca)), f(pct(inf, .5)), f(pct(inf, .9)), f(max(inf) if inf else None),
                   len(sel(ev["bi_trig"])), len(sel(ev["bi_notmet"])),
                   f(pq(sel(ev["lat"]), "stt_latency_ms", .5), 0), f(pq(sel(ev["lat"]), "stt_latency_ms", .9), 0),
                   f(pq(sel(ev["lat"]), "llm_first_chunk_ms", .5), 0), f(pq(sel(ev["lat"]), "llm_first_chunk_ms", .9), 0),
                   f(pq(sel(ev["ttft"]), "ttft_ms", .5), 0), f(pq(sel(ev["ttft"]), "ttft_ms", .9), 0),
                   f(pq(sel(ev["tts_first"]), "latency_ms", .5), 0), f(pq(sel(ev["tts_first"]), "latency_ms", .9), 0),
                   len(sel(ev["no_transcript"])), len(sel(ev["stale_stt"])), len(sel(ev["cb"])), len(sel(ev["autoflush"])),
                   len(tt), f(pct(tt, .5), 0), f(pct(tt, .9), 0), len(cc), f(pct(cc, .5), 0), f(pct(cc, .9), 0), len(cn),
                   f(pct(pc, .99), 0), f(statistics.median(cpu) if cpu else None, 2)]
            fo.write("\t".join(str(x) for x in row) + "\n")

    # ---- resumen ventana estable
    est = lambda L: [x for x in L if ini <= x["_t"] <= fin]
    estc = lambda L: [x for x in L if ini <= x["t"] <= fin]
    tcs = est(tc_all)
    ca = [num(x, "close_attempts") for x in tcs]; ca = [x for x in ca if x is not None]
    inf = [num(x, "inference_ms") for x in est(ev["tc_st"])]; inf = [x for x in inf if x is not None]
    infm = [num(x, "inference_ms") for x in est(ev["tc_mid"])]
    R["estable_s"] = fin - ini
    R["closes_sil"] = len(est(ev["close_sil"])); R["closes_sil_por_s"] = round(R["closes_sil"] / (fin - ini), 2)
    R["tc_total"] = len(tcs); R["tc_st"] = len(est(ev["tc_st"])); R["tc_mid"] = len(est(ev["tc_mid"])); R["tc_fb"] = len(est(ev["tc_fb"]))
    R["st_incomplete"] = len(est(ev["st_incomplete"]))
    R["close_attempts_sum"] = int(sum(ca)); R["close_attempts_por_turno"] = round(sum(ca) / len(ca), 2) if ca else None
    R["inferencias_por_s_sesion"] = round(sum(ca) / (fin - ini) / n, 4) if ca else None
    R["inferencias_por_s_total"] = round(sum(ca) / (fin - ini), 2) if ca else None
    R["infer_ms_p50"] = f(pct(inf, .5)); R["infer_ms_p90"] = f(pct(inf, .9)); R["infer_ms_p99"] = f(pct(inf, .99)); R["infer_ms_max"] = f(max(inf) if inf else None)
    R["infer_ms_min"] = f(min(inf) if inf else None)
    R["bi_trig"] = len(est(ev["bi_trig"])); R["bi_notmet"] = len(est(ev["bi_notmet"]))
    sd = [num(x, "speech_duration") for x in est(ev["bi_trig"])]; sd = [x for x in sd if x is not None]
    R["bi_speech_dur_p50"] = f(pct(sd, .5), 2); R["bi_speech_dur_p90"] = f(pct(sd, .9), 2); R["bi_speech_dur_max"] = f(max(sd) if sd else None, 2)
    gates = {}
    for x in est(ev["bi_notmet"]):
        g = x.get("first_blocked_gate", "?"); gates[g] = gates.get(g, 0) + 1
    R["bi_notmet_gates"] = json.dumps(gates)
    cl = [num(x, "cancel_latency_ms") for x in est(ev["tts_cancel"])]; cl = [x for x in cl if x is not None]
    R["tts_cancel_p50"] = f(pct(cl, .5)); R["tts_cancel_p90"] = f(pct(cl, .9))
    for k, name in (("stt_latency_ms", "stt_lat"), ("llm_first_chunk_ms", "llm_first"), ("tts_latency_ms", "tts_lat"), ("total_latency_ms", "gb_ttfab")):
        xs = [num(x, k) for x in est(ev["lat"])]; xs = [x for x in xs if x is not None]
        R[f"{name}_n"] = len(xs); R[f"{name}_p50"] = f(pct(xs, .5), 0); R[f"{name}_p90"] = f(pct(xs, .9), 0)
    xs = [num(x, "ttft_ms") for x in est(ev["ttft"])]; xs = [x for x in xs if x is not None]
    R["ttft_n"] = len(xs); R["ttft_p50"] = f(pct(xs, .5), 0); R["ttft_p90"] = f(pct(xs, .9), 0); R["ttft_p99"] = f(pct(xs, .99), 0)
    xs = [num(x, "stt_latency_ms") for x in est(ev["stt_fin"])]; xs = [x for x in xs if x is not None]
    R["stt_fin_n"] = len(xs); R["stt_fin_p50"] = f(pct(xs, .5), 0); R["stt_fin_p90"] = f(pct(xs, .9), 0); R["stt_fin_p99"] = f(pct(xs, .99), 0)
    for k in ("no_transcript", "stale_stt", "cb", "autoflush", "inbox_full", "barrier_to", "batch_fail", "agent_resp", "pipeline"):
        R[k] = len(est(ev[k]))
    R["errores_gb_total"] = nivel["error"]; R["warnings_gb_total"] = nivel["warning"]
    # CYCLE-PROBE ventana estable
    cps = est(ev["cp"])
    R["cp_lineas"] = len(cps)
    for p in fases:
        xs = [num(x, p + "_p90") for x in cps]; xs = [x for x in xs if x is not None]
        R[f"cp_{p}_p90_med"] = f(statistics.median(xs) if xs else None, 2); R[f"cp_{p}_p90_p90"] = f(pct(xs, .9), 2); R[f"cp_{p}_p90_max"] = f(max(xs) if xs else None, 1)
        xs = [num(x, p + "_p50") for x in cps]; xs = [x for x in xs if x is not None]
        R[f"cp_{p}_p50_med"] = f(statistics.median(xs) if xs else None, 2)
    xs = [num(x, "pct_cierres") for x in cps]; xs = [x for x in xs if x is not None]
    R["pct_cierres_med"] = f(statistics.median(xs) if xs else None); R["pct_cierres_max"] = f(max(xs) if xs else None)
    xs = [num(x, "frames_per_cycle") for x in cps]; xs = [x for x in xs if x is not None]
    R["fpc_med"] = f(statistics.median(xs) if xs else None, 2); R["fpc_p90"] = f(pct(xs, .9), 2)
    xs = [num(x, "inbox") for x in cps]; xs = [x for x in xs if x is not None]
    R["inbox_med"] = f(statistics.median(xs) if xs else None); R["inbox_max"] = f(max(xs) if xs else None)
    # ciclos por lote por sesión: último cycles de cada sesión en la ventana / duración
    ult = {}
    for x in cps: ult[x.get("session_id")] = x
    cyc = [num(x, "cycles") for x in ult.values()]; cyc = [c for c in cyc if c]
    R["cp_sesiones_con_lineas"] = len(ult)
    R["cp_ciclos_lote_acum_med_por_sesion"] = f(statistics.median(cyc) if cyc else None, 0)
    # job por mensaje: job_p50 / fpc
    # cliente
    tt = [r["v"] for r in estc(ttfab)]
    R["ttfab_n"] = len(tt); R["ttfab_p50"] = f(pct(tt, .5), 0); R["ttfab_p90"] = f(pct(tt, .9), 0); R["ttfab_p99"] = f(pct(tt, .99), 0)
    R["ttfab_gt10s"] = sum(1 for x in tt if x > 10000)
    cc = [r["v"] for r in estc(corte) if "fin=natural" not in r["extra"]]
    R["corte_n"] = len(cc); R["corte_p50"] = f(pct(cc, .5), 0); R["corte_p90"] = f(pct(cc, .9), 0); R["corte_p99"] = f(pct(cc, .99), 0)
    R["corte_natural_n"] = sum(1 for r in estc(corte) if "fin=natural" in r["extra"])
    # end_ok por sesión, separando interrumpe
    agg = {"si": {}, "no": {}}
    for r in end_ok:
        kv = dict(p.split("=", 1) for p in r["extra"].split("|") if "=" in p)
        g = "si" if kv.get("interrumpe") == "si" else "no"
        a = agg[g]; a["sesiones"] = a.get("sesiones", 0) + 1
        for k in ("speak", "audio_done", "ttfab_perdidos", "sin_respuesta", "espera_max", "cortes", "text"):
            a[k] = a.get(k, 0) + int(kv.get(k, 0) or 0)
    R["end_ok_interrumpe"] = json.dumps(agg["si"]); R["end_ok_no_interrumpe"] = json.dumps(agg["no"])
    # sesiones con cortes == speak-1 (nunca cortan): cortes ≥ audio_done?
    nunca = []
    for r in end_ok:
        kv = dict(p.split("=", 1) for p in r["extra"].split("|") if "=" in p)
        if kv.get("interrumpe") == "si":
            nunca.append((r["sid"], kv.get("marca"), int(kv.get("speak", 0)), int(kv.get("audio_done", 0)), int(kv.get("cortes", 0))))
    R["interrumpe_detalle"] = json.dumps(nunca) if n <= 10 else ""
    pc = [r["v"] for r in estc(ping_ctrl)]
    R["ping_ctrl_p99"] = f(pct(pc, .99), 0); R["ping_ctrl_max"] = f(max(pc) if pc else None, 0)
    cpu = [c for t, c in st if ini <= t <= fin]
    R["cpu_med"] = f(statistics.median(cpu) if cpu else None, 2); R["cpu_p90"] = f(pct(cpu, .9), 2); R["cpu_max"] = f(max(cpu) if cpu else None, 2); R["cpu_min"] = f(min(cpu) if cpu else None, 2)
    with open(os.path.join(od, "resumen_auditoria.tsv"), "w") as fo:
        for k, v in R.items(): fo.write(f"{k}\t{v}\n")
    # barge-in speech_duration histogram and tts_cancel to file
    with open(os.path.join(od, "barge_in.tsv"), "w") as fo:
        fo.write("t_rel\tspeech_duration\taudio_energy\n")
        for x in ev["bi_trig"]:
            fo.write(f"{x['_t']:.1f}\t{x.get('speech_duration','')}\t{x.get('audio_energy','')}\n")
    with open(os.path.join(od, "turn_complete.tsv"), "w") as fo:
        fo.write("t_rel\tkind\tclose_attempts\tinference_ms\tprobability\tturn_end_latency_ms\n")
        for k in ("tc_st", "tc_mid", "tc_fb"):
            for x in ev[k]:
                fo.write(f"{x['_t']:.1f}\t{k}\t{x.get('close_attempts','')}\t{x.get('inference_ms','')}\t{x.get('probability','')}\t{x.get('turn_end_latency_ms','')}\n")
    return R

if __name__ == "__main__":
    # uso: auditoria_20261019_extrae.py <salida> [--estable 600] <crudo/SELLO/r1_nNNN> ...
    args = sys.argv[1:]
    estable = 600.0
    if "--estable" in args:
        i = args.index("--estable"); estable = float(args[i + 1]); del args[i:i + 2]
    out = args[0]
    Rs = []
    for d in args[1:]:
        Rs.append(main(d, out, estable=estable))
    keys = []
    for R in Rs:
        for k in R:
            if k not in keys: keys.append(k)
    with open(os.path.join(out, "resumen_auditoria_todos.tsv"), "w") as fo:
        fo.write("\t".join(keys) + "\n")
        for R in Rs: fo.write("\t".join(str(R.get(k, "")) for k in keys) + "\n")
    print("ok", len(Rs))
