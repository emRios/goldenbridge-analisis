#!/usr/bin/env python3
"""Veredicto preregistrado del A/B (Codex B5, 2026-10-27). Tres salidas, no dos:
  EFECTO        — las DOS corridas de la variante (int8) quedan por DEBAJO del rango de los dos controles (fp32)
                  en infer_ms_p50 Y en infer_ms_min a 100 llamadas (dirección esperada: run más corto → menos fila).
                  intentos/s y cierres/s se reportan como secundarios (la demanda los acota; no se exigen).
  EFECTO_INVERSO— las dos corridas de la variante quedan por ENCIMA del rango en ambas métricas (inesperado; se reporta).
  SIN_EFECTO    — las dos corridas de la variante caen DENTRO del rango de los controles en ambas métricas, con los
                  dos controles SATURADOS: ausencia de efecto detectado con esta demanda, no prueba de que no exista.
  INCONCLUSO    — cualquier escalón (50/75/100) de cualquiera de las 4 corridas que no pase la validación ESTRICTA de
                  compuerta_control.evaluar (validez de M1, datos obligatorios, camino integrado), un control no
                  SATURADO a 100, cuello TTS a 100 en cualquier corrida (misma regla que la compuerta), o las dos
                  corridas de la variante en desacuerdo. No se interpreta.
Uso: veredicto_ab.py <corridas.tsv> [--sellos C_sube=<s> V_sube=<s> C_baja=<s> V_baja=<s>]   |   veredicto_ab.py --test
"""
import csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compuerta_control import evaluar, saturado, cuello_tts  # noqa: E402

PASOS = ("r1_n050", "r1_n075", "r1_n100")
ETIQ = ("C_sube", "V_sube", "C_baja", "V_baja")
MET = ("infer_ms_p50", "infer_ms_min")

def cargar_corrida(aqui, sello):
    """→ {"sello", "valido", "motivos", "pasos": {paso: medidas}} usando la validación estricta de la compuerta."""
    out = {"sello": sello, "valido": True, "motivos": [], "pasos": {}}
    salida = os.path.join(aqui, "resultados", sello)
    if not os.path.isdir(salida): out["motivos"].append(f"no existe {salida}"); out["valido"] = False; return out
    for paso in PASOS:
        m, prob = evaluar(salida, paso)
        out["motivos"].extend(prob)
        if not prob: out["pasos"][paso] = m
    out["valido"] = not out["motivos"] and all(p in out["pasos"] for p in PASOS)
    return out

def decidir(corr):
    """corr: {etiqueta: corrida}. → (veredicto, detalle dict). Nunca lanza: todo faltante es INCONCLUSO."""
    det = {"motivos": []}
    for e in ETIQ:
        c = corr.get(e)
        if c is None: det["motivos"].append(f"falta {e}"); continue
        if not c["valido"]: det["motivos"].append(f"{e} ({c.get('sello')}) inválida: " + "; ".join(c["motivos"] or ["escalones incompletos"]))
    if det["motivos"]: return "INCONCLUSO", det
    n100 = {e: corr[e]["pasos"]["r1_n100"] for e in ETIQ}
    det["n100"] = {e: {k: n100[e].get(k) for k in ("infer_ms_p50", "infer_ms_min", "infer_ms_p90", "inferencias_por_s_total",
                                                   "closes_sil_por_s", "tts_latency_breakdown_p50", "tts_latency_breakdown_p90")} for e in ETIQ}
    for e in ("C_sube", "C_baja"):
        if not saturado(n100[e]):
            det["motivos"].append(f"{e} no SATURADO a 100 (p50 {n100[e]['infer_ms_p50']}, mín {n100[e]['infer_ms_min']}, intentos/s {n100[e]['inferencias_por_s_total']})")
    for e in ETIQ:
        if cuello_tts(n100[e]):
            det["motivos"].append(f"{e}: cuello TTS a 100 (espera total p50 {n100[e]['tts_latency_breakdown_p50']}, p90 {n100[e]['tts_latency_breakdown_p90']})")
    if det["motivos"]: return "INCONCLUSO", det
    rango = {k: (min(n100["C_sube"][k], n100["C_baja"][k]), max(n100["C_sube"][k], n100["C_baja"][k])) for k in MET}
    det["rango_control"] = rango
    pos = {}
    for e in ("V_sube", "V_baja"):
        abajo = all(n100[e][k] < rango[k][0] for k in MET)
        arriba = all(n100[e][k] > rango[k][1] for k in MET)
        dentro = all(rango[k][0] <= n100[e][k] <= rango[k][1] for k in MET)
        pos[e] = "abajo" if abajo else "arriba" if arriba else "dentro" if dentro else "mixto"
    det["posicion_variante"] = pos
    if pos["V_sube"] == pos["V_baja"] == "abajo": return "EFECTO", det
    if pos["V_sube"] == pos["V_baja"] == "arriba": return "EFECTO_INVERSO", det
    if pos["V_sube"] == pos["V_baja"] == "dentro": return "SIN_EFECTO", det
    det["motivos"].append(f"las dos corridas de la variante no coinciden: {pos}")
    return "INCONCLUSO", det

def imprimir(v, det, sellos):
    """Siempre imprime; nunca depende de claves que sólo existen en algunos caminos (N1)."""
    print(f"corridas: {sellos}")
    if "n100" in det:
        print("  n=100        p50     mín     p90   intentos/s  cierres/s  tts_p50  tts_p90")
        for e in ETIQ:
            p = det["n100"][e]
            print(f"  {e:7s} {p['infer_ms_p50']:8.1f} {p['infer_ms_min']:7.1f} {p['infer_ms_p90']:7.1f} {p['inferencias_por_s_total']:10.2f} "
                  f"{p['closes_sil_por_s']:9.2f} {p['tts_latency_breakdown_p50']:8.0f} {p['tts_latency_breakdown_p90']:8.0f}")
    if "rango_control" in det:
        print(f"  rango control p50={det['rango_control']['infer_ms_p50']} mín={det['rango_control']['infer_ms_min']}  variante: {det.get('posicion_variante')}")
    for m in det.get("motivos", []): print("  -", m)
    print(f"VEREDICTO: {v}")

# ── pruebas sintéticas (decidir + imprimir; cargar_corrida se prueba con el layout real en el README) ──
def _m(p50, mn, ips=13.0, cps=8.0, tts50=45.0, tts90=166.0):
    return {"infer_ms_p50": p50, "infer_ms_min": mn, "infer_ms_p90": p50 * 1.05, "inferencias_por_s_total": ips,
            "closes_sil_por_s": cps, "tts_latency_breakdown_p50": tts50, "tts_latency_breakdown_p90": tts90}

def _corrida(p50, mn, valido=True, **kw):
    return {"sello": "sint", "valido": valido, "motivos": [] if valido else ["r1_n075: inválido según analiza.py"],
            "pasos": {p: _m(p50, mn, **kw) for p in PASOS}}

def test():
    import io, contextlib
    C = lambda: _corrida(912, 854)
    casos = [
        ("efecto", {"C_sube": _corrida(912, 854), "C_baja": _corrida(880, 830), "V_sube": _corrida(250, 90), "V_baja": _corrida(300, 100)}, "EFECTO"),
        ("sin efecto", {"C_sube": _corrida(912, 854), "C_baja": _corrida(880, 830), "V_sube": _corrida(900, 840), "V_baja": _corrida(890, 835)}, "SIN_EFECTO"),
        ("control no saturado", {"C_sube": _corrida(150, 80), "C_baja": _corrida(880, 830), "V_sube": _corrida(100, 75), "V_baja": _corrida(100, 75)}, "INCONCLUSO"),
        ("corrida inválida", {"C_sube": C(), "C_baja": C(), "V_sube": _corrida(250, 90, valido=False), "V_baja": _corrida(250, 90)}, "INCONCLUSO"),
        ("variantes en desacuerdo", {"C_sube": _corrida(912, 854), "C_baja": _corrida(880, 830), "V_sube": _corrida(250, 90), "V_baja": _corrida(900, 840)}, "INCONCLUSO"),
        ("cuello TTS p50", {"C_sube": C(), "C_baja": C(), "V_sube": _corrida(250, 90, tts50=900), "V_baja": _corrida(250, 90)}, "INCONCLUSO"),
        ("cuello TTS p90", {"C_sube": _corrida(912, 854, tts90=1500), "C_baja": C(), "V_sube": _corrida(250, 90), "V_baja": _corrida(250, 90)}, "INCONCLUSO"),
        ("falta una corrida", {"C_sube": C(), "C_baja": C(), "V_sube": _corrida(250, 90)}, "INCONCLUSO"),
        ("efecto inverso", {"C_sube": _corrida(600, 400), "C_baja": _corrida(650, 420), "V_sube": _corrida(950, 900), "V_baja": _corrida(960, 905)}, "EFECTO_INVERSO"),
        ("mixto (p50 abajo, mín dentro)", {"C_sube": _corrida(912, 854), "C_baja": _corrida(880, 830), "V_sube": _corrida(250, 840), "V_baja": _corrida(300, 835)}, "INCONCLUSO"),
    ]
    ok = True
    for nombre, corr, esperado in casos:
        v, det = decidir(corr); ok &= (v == esperado)
        try:
            with contextlib.redirect_stdout(io.StringIO()): imprimir(v, det, {e: "sint" for e in corr})  # N1: nunca debe lanzar
            imp = "imprime OK"
        except Exception as ex:
            ok = False; imp = f"imprimir LANZA {type(ex).__name__}: {ex}"
        print(f"  {'OK ' if v == esperado else 'MAL'} {nombre}: {v} (esperado {esperado}); {imp}")
    print("test:", "OK" if ok else "FALLÓ"); return 0 if ok else 1

def main():
    if "--test" in sys.argv: return test()
    if len(sys.argv) < 2: print(__doc__); return 2
    tsv = os.path.abspath(sys.argv[1]); aqui = os.path.dirname(tsv)
    sellos = {}
    if "--sellos" in sys.argv:
        for kv in sys.argv[sys.argv.index("--sellos") + 1:]:
            if "=" in kv: e, s = kv.split("=", 1); sellos[e] = s
    elif os.path.exists(tsv):  # última corrida con rc 0 de cada etiqueta
        for row in csv.DictReader(open(tsv), delimiter="\t"):
            if row.get("etiqueta") in ETIQ and row.get("rc") == "0": sellos[row["etiqueta"]] = row["sello"]
    corr = {e: cargar_corrida(aqui, s) for e, s in sellos.items()}
    v, det = decidir(corr)
    imprimir(v, det, sellos)
    try:
        with open(os.path.join(aqui, "veredicto_ab.json"), "w") as f:
            json.dump({"veredicto": v, "sellos": sellos, "detalle": det}, f, indent=1, ensure_ascii=False, default=str)
    except OSError as e:
        print(f"(no se pudo escribir el json: {e})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
