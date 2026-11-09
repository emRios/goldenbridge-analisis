#!/usr/bin/env python3
"""Ley de Little sobre el permiso de síntesis de TTS (asyncio.Semaphore(50), tts_processor.py L769).

Ventana estable [t0+122, t0+720] s (598 s). Se cuentan los FINES de cada síntesis dentro de la ventana (en régimen
estable, fines/s = inicios/s):
  completas  : 'Ursula TTS persistent WS synthesis completed'  (traen total_latency_ms = permiso tomado → fin)
  canceladas : 'Ursula TTS persistent WS cancelled by barge-in' (no traen duración)
  errores    : 'Ursula TTS persistent WS error' / 'TTS circuit breaker'
  λ = (completas + canceladas + errores) / 598
  W = media de total_latency_ms de las completas (las canceladas no registran duración: ver cotas)
  L = λ × W ; cotas: L_bajo = completas/598 × W (canceladas con W=0) ; L_alto = λ × W (canceladas con W igual a las completas)
total_latency_ms arranca en synthesize_session (L342), después de obtener el permiso, y termina con el aviso de fin,
justo antes de soltarlo: mide el tiempo con el permiso tomado.
Uso: tts_little.py <crudo/SELLO/r1_nNNN> ...
"""
import re, sys, os, calendar, datetime
ANSI = re.compile(r"\x1b\[[0-9;]*m"); TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)Z")
def ts(s):
    m = TS.match(s)
    return calendar.timegm(datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple()) + float("0." + m.group(2)[:6]) if m else None
def num(s, k):
    m = re.search(r"\b%s=([0-9.]+)" % k, s); return float(m.group(1)) if m else None
print("corrida\tN\tcompletas\tcanceladas\terrores\tlambda_s\tW_s\tL_bajo\tL\tespera_permiso_p90_ms")
for d in sys.argv[1:]:
    ef = dict(l.strip().split("=", 1) for l in open(os.path.join(d, "efectivo.txt")) if "=" in l)
    t0 = float(ef["t0_epoch"]); ini, fin = t0 + 122, t0 + 720; dur = fin - ini
    comp, canc, err, w = 0, 0, 0, []
    for s in open(os.path.join(d, "gb.log"), errors="replace"):
        if "Ursula TTS" not in s and "TTS circuit breaker" not in s: continue
        s = ANSI.sub("", s); t = ts(s)
        if t is None or not (ini <= t <= fin): continue
        if "persistent WS synthesis completed" in s:
            comp += 1; v = num(s, "total_latency_ms"); w.append(v / 1000) if v is not None else None
        elif "persistent WS cancelled by barge-in" in s: canc += 1
        elif "persistent WS error" in s or "TTS circuit breaker" in s: err += 1
    lam = (comp + canc + err) / dur; W = sum(w) / len(w) if w else 0
    corrida = d.rstrip("/").split("/")[-2]; n = d.rstrip("/").split("_n")[-1]
    p90 = ""
    tsv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_semaforo.tsv")
    if os.path.exists(tsv):
        for row in open(tsv):
            c = row.rstrip("\n").split("\t")
            if c[0] == corrida and c[1] == n: p90 = c[5].split("/")[1]
    print(f"{corrida}\t{n}\t{comp}\t{canc}\t{err}\t{lam:.2f}\t{W:.2f}\t{comp/dur*W:.1f}\t{lam*W:.1f}\t{p90}")
