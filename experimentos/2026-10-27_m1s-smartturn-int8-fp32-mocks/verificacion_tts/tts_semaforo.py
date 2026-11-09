#!/usr/bin/env python3
"""Empareja cada 'LATENCY BREAKDOWN … tts_latency_ms' (reloj desde que GB decide sintetizar, ANTES del semáforo de 50)
con la línea 'Ursula TTS persistent WS first chunk latency_ms' inmediatamente anterior (reloj que arranca DESPUÉS de
obtener el semáforo, tts_processor.py L769 y L342). La resta estima la espera del semáforo. Ventana estable [122,720] s.
Además cuenta síntesis abiertas: +1 en 'TTS synthesis starting', −1 en 'synthesis completed' o 'TTS cancellation complete'.
Uso: tts_semaforo.py <crudo/SELLO/r1_nNNN> ..."""
import re, sys, os, calendar, datetime
ANSI = re.compile(r"\x1b\[[0-9;]*m"); TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)Z")
def ts(s):
    m = TS.match(s)
    return calendar.timegm(datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple()) + float("0." + m.group(2)[:6]) if m else None
def num(s, k):
    m = re.search(r"\b%s=([0-9.]+)" % k, s); return float(m.group(1)) if m else None
def pct(v, q):
    v = sorted(v); return v[min(len(v)-1, int(len(v)*q))] if v else None
f = lambda x: "-" if x is None else f"{x:.0f}"
print("corrida\tN\tpares\tdesde_decidir_p50/p90\tdespues_permiso_p50/p90\tespera_permiso_p50/p90/p99\tabiertas_p50/p90/max\t%tiempo_abiertas>=50")
for d in sys.argv[1:]:
    ef = dict(l.strip().split("=", 1) for l in open(os.path.join(d, "efectivo.txt")) if "=" in l)
    t0 = float(ef["t0_epoch"]); ini, fin = t0 + 122, t0 + 720
    ult_first, a, b, w = None, [], [], []
    abiertas, serie = 0, []
    for s in open(os.path.join(d, "gb.log"), errors="replace"):
        s = ANSI.sub("", s)
        if "Ursula TTS persistent WS first chunk" in s:
            ult_first = num(s, "latency_ms"); continue
        if "TTS synthesis starting" in s: abiertas += 1
        elif "Ursula TTS persistent WS synthesis completed" in s or "TTS cancellation complete" in s: abiertas = max(0, abiertas - 1)
        t = ts(s)
        if t and ini <= t <= fin and ("TTS synthesis starting" in s or "synthesis completed" in s or "cancellation complete" in s):
            serie.append(abiertas)
        if "LATENCY BREAKDOWN" in s:
            v = num(s, "tts_latency_ms")
            if t and ini <= t <= fin and v is not None and ult_first is not None:
                a.append(v); b.append(ult_first); w.append(v - ult_first)
            ult_first = None
    corrida = d.rstrip("/").split("/")[-2]; n = d.rstrip("/").split("_n")[-1]
    print(f"{corrida}\t{n}\t{len(w)}\t{f(pct(a,.5))}/{f(pct(a,.9))}\t{f(pct(b,.5))}/{f(pct(b,.9))}\t{f(pct(w,.5))}/{f(pct(w,.9))}/{f(pct(w,.99))}\t"
          f"{f(pct(serie,.5))}/{f(pct(serie,.9))}/{max(serie) if serie else '-'}\t{100*sum(1 for x in serie if x>=50)/max(1,len(serie)):.0f}")
