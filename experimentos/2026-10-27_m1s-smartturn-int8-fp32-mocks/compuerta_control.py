#!/usr/bin/env python3
"""Compuerta de la corrida corta de control (fp32, 100+3, ESTABLE_S 300) y validación estricta por escalón
(la reutiliza veredicto_ab.py para las 4 escaleras). Dicta sobre un escalón:
  0 SATURADO          — reaparece la firma del techo: las 4 escaleras pueden correr
  3 NO_SATURADO       — no hay fila para el candado: ajustar demanda (respuesta del mock / N) y repetir
  4 AMBIGUO           — régimen intermedio: subir N (tope 117) o acortar la respuesta del mock, repetir
  5 CUELLO_TTS        — el turno espera demasiado antes del primer audio del TTS: acortar respuesta, NO subir N
  6 INTEGRACION_FALLA — escalón inválido, dato faltante o camino distinto del de M1 (router/AMD/not available/muertes)
Regla B1 (Codex 2026-10-27): TODO dato requerido tiene que existir y ser numérico; un faltante NUNCA aprueba.
Umbrales preregistrados (M1 real n=100, 60 ms: infer_ms p50 912, mín 854, 13,11 intentos/s; n=75: 231 / 77 / 11,2 → AMBIGUO):
  SATURADO     : infer_ms_p50 >= 500 y infer_ms_min >= 300 y inferencias_por_s_total >= 11
  NO_SATURADO  : infer_ms_p50 < 200 y infer_ms_min < 120
  CUELLO_TTS   : tts_latency_breakdown_p50 > 300 ms  o  tts_latency_breakdown_p90 > 1000 ms  (B2, Codex ronda 2:
                 NO se resta nada; `tts_latency_ms` de LATENCY BREAKDOWN es la espera TOTAL del turno desde que
                 GB decide sintetizar hasta el primer audio: semáforo de 50 + cola del mock + primer chunk. Es lo
                 que paga el turno, venga de donde venga. M1 real n=100: p50 45, p90 166. El primer chunk del mock
                 se reporta aparte como contexto, sin combinarlo.)
Lee: resultados/<SELLO>/auditoria/<SELLO>_<paso>/resumen_auditoria.tsv (vertical, ventana estable; extractor de M1),
     resultados/<SELLO>/resumen.tsv (analiza.py: valido, muertes, gen_lag), resultados/crudo/<SELLO>/<paso>/lectura.txt
     (ventana estable) y efectivo.txt. Escribe resultados/<SELLO>/veredicto_control_<paso>.json.
Uso: compuerta_control.py <resultados/<SELLO>> [--paso r1_n100]
"""
import csv, glob, json, math, os, sys

REQ_R = ("closes_sil_por_s", "inferencias_por_s_total", "close_attempts_por_turno", "infer_ms_p50", "infer_ms_p90",
         "infer_ms_min", "errores_gb_total", "ping_ctrl_p99", "ttfab_p50", "cpu_med", "estable_s")
REQ_L = ("router_transitions", "amd_classifications", "not_available", "llm_ttft_n", "stt_finalize_n",
         "stt_finalize_con_texto", "tts_latency_breakdown_n", "tts_latency_breakdown_p50", "tts_latency_breakdown_p90",
         "tts_first_chunk_n", "tts_first_chunk_p50", "tts_first_chunk_p90")
REQ_A = ("valido", "muertes", "gen_lag_p99_max", "errores_gb")
TTS_P50_MAX, TTS_P90_MAX = 300.0, 1000.0

def leer_kv(path, sep="="):
    d = {}
    for ln in open(path):
        if sep in ln:
            k, v = ln.rstrip("\n").split(sep, 1); d[k.strip()] = v.strip()
    return d

def fl(x):
    try:
        v = float(x); return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None

def saturado(m):
    return m["infer_ms_p50"] >= 500 and m["infer_ms_min"] >= 300 and m["inferencias_por_s_total"] >= 11

def no_saturado(m):
    return m["infer_ms_p50"] < 200 and m["infer_ms_min"] < 120

def cuello_tts(m):
    return m["tts_latency_breakdown_p50"] > TTS_P50_MAX or m["tts_latency_breakdown_p90"] > TTS_P90_MAX

def evaluar(salida, paso):
    """Validación ESTRICTA de un escalón. → (medidas dict, problemas list). problemas vacío ⇔ escalón válido e
    integrado (criterios 'escalón válido', 'dato requerido' y 'camino integrado' del README)."""
    salida = os.path.abspath(salida); sello = os.path.basename(salida)
    aqui = os.path.dirname(os.path.dirname(salida)); crudo = os.path.join(aqui, "resultados", "crudo", sello, paso)
    prob, m = [], {}
    res = glob.glob(os.path.join(salida, "auditoria", f"*_{paso}", "resumen_auditoria.tsv"))
    lect, efec, rs = (os.path.join(crudo, "lectura.txt"), os.path.join(crudo, "efectivo.txt"), os.path.join(salida, "resumen.tsv"))
    for nombre, ok in (("resumen_auditoria.tsv", len(res) == 1), ("lectura.txt", os.path.exists(lect)),
                       ("efectivo.txt", os.path.exists(efec)), ("resumen.tsv", os.path.exists(rs))):
        if not ok: prob.append(f"{paso}: falta {nombre}")
    if prob: return m, prob
    R = leer_kv(res[0], "\t"); L = leer_kv(lect); Ef = leer_kv(efec)
    filas = [row for row in csv.DictReader(open(rs), delimiter="\t") if row.get("paso") == paso]
    if len(filas) != 1: prob.append(f"{paso}: resumen.tsv tiene {len(filas)} filas (se exige 1)")
    A = filas[0] if len(filas) == 1 else {}
    for origen, d, keys in (("resumen_auditoria", R, REQ_R), ("lectura", L, REQ_L), ("resumen.tsv", A, REQ_A)):
        for k in keys:
            v = d.get(k) if k == "valido" else fl(d.get(k))
            if v is None or v == "": prob.append(f"{paso}: {origen}: falta o no numérico '{k}'")
            m[k if origen != "resumen.tsv" else f"analiza_{k}"] = v
    m["cliente_rc"] = fl(Ef.get("cliente_rc")); m["t0_epoch"] = fl(Ef.get("t0_epoch"))
    if m["cliente_rc"] is None: prob.append(f"{paso}: efectivo: falta cliente_rc")
    if m["t0_epoch"] is None: prob.append(f"{paso}: efectivo: falta t0_epoch")
    if Ef.get("hilos_ort_omp_openblas") != "1 1 1": prob.append(f"{paso}: efectivo: hilos '{Ef.get('hilos_ort_omp_openblas')}'")
    if not Ef.get("cgroup_cpuset"): prob.append(f"{paso}: efectivo: falta cgroup_cpuset")
    m["motivo_analiza"] = A.get("motivo") if A else None
    if prob: return m, prob
    # validez del escalón (criterio de M1) y camino integrado
    if m["analiza_valido"] != "si": prob.append(f"{paso}: inválido según analiza.py: {m['motivo_analiza']}")
    if m["analiza_muertes"] > 0: prob.append(f"{paso}: muertes={m['analiza_muertes']:.0f}")
    if m["analiza_gen_lag_p99_max"] > 5: prob.append(f"{paso}: gen_lag_p99_max={m['analiza_gen_lag_p99_max']} > 5")
    if m["analiza_errores_gb"] > 0 or m["errores_gb_total"] > 0: prob.append(f"{paso}: errores_gb={m['analiza_errores_gb']}/{m['errores_gb_total']}")
    if m["cliente_rc"] != 0: prob.append(f"{paso}: cliente_rc={m['cliente_rc']:.0f}")
    for k in ("router_transitions", "amd_classifications", "not_available"):
        if m[k] > 0: prob.append(f"{paso}: {k}={m[k]:.0f}")
    if m["stt_finalize_con_texto"] <= 0: prob.append(f"{paso}: 0 Finalize con texto en la ventana")
    if m["llm_ttft_n"] <= 0: prob.append(f"{paso}: 0 llamadas al LLM grande en la ventana")
    elif m["llm_ttft_n"] < 0.8 * m["stt_finalize_con_texto"]:
        prob.append(f"{paso}: LLM grande no sigue a los Finalize ({m['llm_ttft_n']:.0f} < 0.8×{m['stt_finalize_con_texto']:.0f})")
    if m["tts_latency_breakdown_n"] <= 0 or m["tts_first_chunk_n"] <= 0: prob.append(f"{paso}: sin muestras de TTS en la ventana")
    return m, prob

def dictar(m, prob):
    """→ (rc, veredicto, mensaje)"""
    if prob: return 6, "INTEGRACION_FALLA", "; ".join(prob)
    if cuello_tts(m):
        return 5, "CUELLO_TTS", (f"espera total antes del primer audio p50 {m['tts_latency_breakdown_p50']:.0f} / p90 "
                                 f"{m['tts_latency_breakdown_p90']:.0f} ms (límites {TTS_P50_MAX:.0f}/{TTS_P90_MAX:.0f}). Acortar la respuesta del mock, no subir N.")
    if saturado(m): return 0, "SATURADO", "firma del techo presente a 100. Las 4 escaleras pueden correr (la meseta la dan ellas)."
    if no_saturado(m): return 3, "NO_SATURADO", "sin fila para el candado. Acortar la respuesta del mock y/o subir N (tope 117) y repetir el control."
    return 4, "AMBIGUO", "régimen intermedio. Subir N (tope 117) o acortar la respuesta del mock, y repetir el control."

def resumen_linea(m):
    g = lambda k: m.get(k)
    return (f"cierres/s={g('closes_sil_por_s')} intentos/s={g('inferencias_por_s_total')} intentos/cierre={g('close_attempts_por_turno')} "
            f"infer_ms p50={g('infer_ms_p50')} p90={g('infer_ms_p90')} min={g('infer_ms_min')} ttfab_p50={g('ttfab_p50')} "
            f"ping_ctrl_p99={g('ping_ctrl_p99')} cpu_med={g('cpu_med')}\n"
            f"  validez: valido={g('analiza_valido')} muertes={g('analiza_muertes')} gen_lag={g('analiza_gen_lag_p99_max')} "
            f"errores_gb={g('errores_gb_total')} cliente_rc={g('cliente_rc')}\n"
            f"  integración: router={g('router_transitions')} amd={g('amd_classifications')} not_available={g('not_available')} "
            f"ttft_n={g('llm_ttft_n')} finalize_con_texto={g('stt_finalize_con_texto')} | TTS espera total p50/p90="
            f"{g('tts_latency_breakdown_p50')}/{g('tts_latency_breakdown_p90')} (n={g('tts_latency_breakdown_n')}); "
            f"primer chunk del mock p50/p90={g('tts_first_chunk_p50')}/{g('tts_first_chunk_p90')} (n={g('tts_first_chunk_n')})")

def main():
    if len(sys.argv) < 2: print(__doc__); return 2
    salida = os.path.abspath(sys.argv[1]); paso = "r1_n100"
    if "--paso" in sys.argv: paso = sys.argv[sys.argv.index("--paso") + 1]
    m, prob = evaluar(salida, paso)
    rc, veredicto, msg = dictar(m, prob)
    print(f"[{os.path.basename(salida)} {paso}] " + resumen_linea(m))
    print(f"{veredicto}: {msg}")
    out = {"sello": os.path.basename(salida), "paso": paso, "veredicto": veredicto, "rc": rc, "problemas": prob, "medidas": m}
    try:
        with open(os.path.join(salida, f"veredicto_control_{paso}.json"), "w") as f: json.dump(out, f, indent=1, ensure_ascii=False)
    except OSError as e:
        print(f"(no se pudo escribir el json: {e})")
    return rc

if __name__ == "__main__":
    sys.exit(main())
