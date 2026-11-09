#!/usr/bin/env python3
"""Cotas de L incluyendo las síntesis canceladas, que no dejan registro en tts_processor (0 'cancelled by barge-in'
en los 12 escalones: se cortan por cancelación de la tarea). Se cuentan las interrupciones en la ventana estable
('Cancelling TTS' en websocket_server: una por interrupción que corta voz en curso) como cota de síntesis canceladas.
  L_completas = completas/598 × W_c
  L_alto      = L_completas + interrupciones/598 × W_c   (cada cancelada con el permiso tanto como una completa)
  L_medio     = L_completas + interrupciones/598 × 2,0 s (cancelada a ~1,8 s de empezar el audio + detección) [INFERIDO]
Uso: tts_little_cotas.py <crudo/SELLO/r1_nNNN> ...  (lee tts_little.tsv para completas y W_c)
"""
import re, sys, os, calendar, datetime
ANSI = re.compile(r"\x1b\[[0-9;]*m"); TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)Z")
def ts(s):
    m = TS.match(s)
    return calendar.timegm(datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple()) + float("0." + m.group(2)[:6]) if m else None
aqui = os.path.dirname(os.path.abspath(__file__))
base = {}
for row in open(os.path.join(aqui, "tts_little.tsv")):
    c = row.rstrip("\n").split("\t")
    if c[0] != "corrida": base[(c[0], c[1])] = (int(c[2]), float(c[6]))
print("corrida\tN\tinterrupciones\tL_completas\tL_medio\tL_alto")
for d in sys.argv[1:]:
    ef = dict(l.strip().split("=", 1) for l in open(os.path.join(d, "efectivo.txt")) if "=" in l)
    t0 = float(ef["t0_epoch"]); ini, fin = t0 + 122, t0 + 720; dur = fin - ini
    n_i = 0
    for s in open(os.path.join(d, "gb.log"), errors="replace"):
        if "Cancelling TTS" not in s: continue
        t = ts(ANSI.sub("", s))
        if t and ini <= t <= fin: n_i += 1
    corrida = d.rstrip("/").split("/")[-2]; n = d.rstrip("/").split("_n")[-1]
    comp, W = base[(corrida, n)]
    Lc = comp / dur * W
    print(f"{corrida}\t{n}\t{n_i}\t{Lc:.1f}\t{Lc + n_i/dur*2.0:.1f}\t{Lc + n_i/dur*W:.1f}")
