#!/usr/bin/env python3
"""(a) Espera de TTS por respuestas que siguen a una interrupción ('Barge-in triggered' de esa sesión desde su respuesta
anterior) contra las que no. (b) Síntesis de TTS simultáneas: cada 'Ursula TTS persistent WS synthesis completed' define
el intervalo [t_fin - total_latency_ms, t_fin]; se cuentan las activas cada 100 ms en la ventana estable.
Uso: tts_concurrencia.py <crudo/SELLO/r1_nNNN> ..."""
import re, sys, os, calendar, datetime
ANSI = re.compile(r"\x1b\[[0-9;]*m"); TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)Z")
def ts(s):
    m = TS.match(s)
    return calendar.timegm(datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple()) + float("0." + m.group(2)[:6]) if m else None
def num(s, k):
    m = re.search(r"\b%s=([0-9.]+)" % k, s); return float(m.group(1)) if m else None
def pct(v, q):
    v = sorted(v); return v[min(len(v)-1, int(len(v)*q))] if v else None
print("corrida\tN\tespera_tras_interrupcion_n/p50/p90\tespera_sin_interrupcion_n/p50/p90\tsintesis_simultaneas_p50/p90/max\t%tiempo_con_>=50")
for d in sys.argv[1:]:
    ef = dict(l.strip().split("=", 1) for l in open(os.path.join(d, "efectivo.txt")) if "=" in l)
    t0 = float(ef["t0_epoch"]); ini, fin = t0 + 122, t0 + 720
    bi, tras, sin, iv = {}, [], [], []
    for s in open(os.path.join(d, "gb.log"), errors="replace"):
        s = ANSI.sub("", s)
        if "Ursula TTS persistent WS synthesis completed" in s:
            t = ts(s); tl = num(s, "total_latency_ms")
            if t and tl is not None: iv.append((t - tl / 1000, t))
            continue
        m = re.search(r"session_id=([0-9a-f-]+)", s)
        if not m: continue
        sid = m.group(1)
        if "Barge-in triggered" in s: bi[sid] = True; continue
        if "LATENCY BREAKDOWN" in s:
            t = ts(s); v = num(s, "tts_latency_ms"); b = bi.pop(sid, False)
            if t and v is not None and ini <= t <= fin: (tras if b else sin).append(v)
    act = []
    k = ini
    while k <= fin:
        act.append(sum(1 for a, b in iv if a <= k < b)); k += 0.1
    f = lambda x: "-" if x is None else f"{x:.0f}"
    corrida = d.rstrip("/").split("/")[-2]; n = d.rstrip("/").split("_n")[-1]
    print(f"{corrida}\t{n}\t{len(tras)}/{f(pct(tras,.5))}/{f(pct(tras,.9))}\t{len(sin)}/{f(pct(sin,.5))}/{f(pct(sin,.9))}\t"
          f"{f(pct(act,.5))}/{f(pct(act,.9))}/{max(act)}\t{100*sum(1 for a in act if a>=50)/len(act):.0f}")
