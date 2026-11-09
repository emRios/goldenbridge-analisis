#!/usr/bin/env python3
"""Separa la espera de TTS (LATENCY BREAKDOWN tts_latency_ms) entre sesiones que interrumpen (load l0, l3, l6…)
y las que no, en la ventana estable [t0+122, t0+720] s; y por respuestas que siguen a un corte (hubo
'Drained stale TTS chunks' en esa sesión desde la respuesta anterior) contra las que no.
Uso: tts_split.py <crudo/SELLO/r1_nNNN> ..."""
import re, sys, calendar, datetime, os
ANSI = re.compile(r"\x1b\[[0-9;]*m"); TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)Z")
def ts(s):
    m = TS.match(s)
    return calendar.timegm(datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple()) + float("0." + m.group(2)[:6]) if m else None
def num(s, k):
    m = re.search(r"\b%s=([0-9.]+)" % k, s); return float(m.group(1)) if m else None
def pct(v, q):
    v = sorted(v); return v[min(len(v)-1, int(len(v)*q))] if v else None
def f(x): return "-" if x is None else f"{x:.0f}"
print("corrida\tN\tgrupo\tn\tp50\tp90\tp99\tmax")
for d in sys.argv[1:]:
    ef = dict(l.strip().split("=", 1) for l in open(os.path.join(d, "efectivo.txt")) if "=" in l)
    t0 = float(ef["t0_epoch"]); ini, fin = t0 + 122, t0 + 720
    rol, cortado, g = {}, {}, {}
    for s in open(os.path.join(d, "gb.log"), errors="replace"):
        s = ANSI.sub("", s)
        if "Per-call variables received" in s:
            m = re.search(r"call_uuid=td1(\w+)-(\w+)", s); sid = re.search(r"session_id=([0-9a-f-]+)", s)
            if m and sid: rol[sid.group(1)] = m.group(2)
            continue
        sid = re.search(r"session_id=([0-9a-f-]+)", s)
        if not sid: continue
        sid = sid.group(1)
        if "Drained stale TTS chunks" in s:
            cortado[sid] = True; continue
        if "LATENCY BREAKDOWN" in s:
            t = ts(s); v = num(s, "tts_latency_ms")
            tras = cortado.pop(sid, False)
            if t is None or v is None or not (ini <= t <= fin): continue
            r = rol.get(sid, "?")
            if r.startswith("l"):
                grupo = "interrumpe" if int(r[1:]) % 3 == 0 else "no_interrumpe"
            else:
                grupo = "control"
            for k in (grupo, ("tras_corte" if tras else "sin_corte_previo")):
                g.setdefault(k, []).append(v)
    corrida = d.rstrip("/").split("/")[-2]; n = d.rstrip("/").split("_n")[-1]
    for k in ("interrumpe", "no_interrumpe", "control", "tras_corte", "sin_corte_previo"):
        v = g.get(k, [])
        print(f"{corrida}\t{n}\t{k}\t{len(v)}\t{f(pct(v,.5))}\t{f(pct(v,.9))}\t{f(pct(v,.99))}\t{f(max(v) if v else None)}")
