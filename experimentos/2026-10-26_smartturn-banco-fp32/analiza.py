#!/usr/bin/env python3
"""Análisis del banco de smart-turn. Lee los JSON de banco.py de una corrida (resultados/<SELLO>/*.json).

  analiza.py mejor-p <dir>          → imprime el mejor P de la serie A (regla del README)
  analiza.py mejor-sesion <dir> P   → imprime 'compartida' o 'por_hilo' (regla del README)
  analiza.py capacidad <dir> P      → caudal medio de la serie A con ese P (para las tasas de la serie D)
  analiza.py resumen <dir>          → tablas por serie, criterios preregistrados, resumen.tsv y resumen.md
"""
import glob, json, os, statistics, sys


def cargar(d):
    out = []
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            x = json.load(open(f))
        except Exception:
            continue
        if "serie" in x:
            x["_archivo"] = os.path.basename(f)
            out.append(x)
    return out


def media(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def grupo(rs, **k):
    return [r for r in rs if all(r.get(a) == b for a, b in k.items())]


def caudal_a(rs, p, sesiones="compartida"):
    g = grupo(rs, serie="A", p=p, sesiones=sesiones)
    return media([r["caudal_inf_s"] for r in g if r["valido"]])


def mejor_p(rs):
    cand = []
    for p in sorted({r["p"] for r in rs if r["serie"] == "A"}):
        g = grupo(rs, serie="A", p=p)
        if not g or not all(r["valido"] and r["paridad"]["criterio_ok"] for r in g):
            continue
        cand.append((p, media([r["caudal_inf_s"] for r in g])))
    if not cand:
        return None
    top = max(c for _, c in cand)
    return min(p for p, c in cand if c >= 0.95 * top)


def mejor_sesion(rs, p):
    c = media([r["caudal_inf_s"] for r in grupo(rs, serie="B", p=p, sesiones="compartida") if r["valido"]])
    h = media([r["caudal_inf_s"] for r in grupo(rs, serie="B", p=p, sesiones="por_hilo") if r["valido"]])
    if c is None or h is None:
        return "compartida"
    return "por_hilo" if h >= 1.05 * c else "compartida"


def f(x, nd=1):
    return "" if x is None else f"{x:.{nd}f}".replace(".", ",")


def resumen(d):
    rs = cargar(d)
    filas, md = [], []
    md.append(f"# Resumen del banco · {os.path.basename(os.path.abspath(d))}\n")
    invalidas = [r["_archivo"] + ": " + "; ".join(r["problemas"]) for r in rs if not r["valido"]]
    md.append(f"Condiciones: {len(rs)} · inválidas: {len(invalidas)}\n")
    for i in invalidas:
        md.append(f"- {i}")
    claves = sorted({(r["serie"], r["p"], r["sesiones"], r["silero_llamadas"], r["tasa"]) for r in rs})
    md.append("\n| serie | P | sesión | Silero | tasa | reps | caudal/s | Run p50 / p90 / p99 ms | CPU del hilo p50 ms | espera p50 / p90 / p99 ms | paridad máx | núcleos | Silero núcleos | memoria sesiones MB |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in claves:
        g = grupo(rs, serie=k[0], p=k[1], sesiones=k[2], silero_llamadas=k[3], tasa=k[4])
        g = [r for r in g if r["valido"]]
        if not g:
            continue
        m = lambda fn: media([fn(r) for r in g])
        esp = lambda q: m(lambda r: (r["espera_total_ms"] or {}).get(q))
        fila = {
            "serie": k[0], "p": k[1], "sesiones": k[2], "silero": k[3], "tasa": k[4], "reps": len(g),
            "caudal": m(lambda r: r["caudal_inf_s"]),
            "run_p50": m(lambda r: r["run_pared_ms"]["p50"]), "run_p90": m(lambda r: r["run_pared_ms"]["p90"]),
            "run_p99": m(lambda r: r["run_pared_ms"]["p99"]), "cpu_p50": m(lambda r: r["run_cpu_ms"]["p50"]),
            "esp_p50": esp("p50"), "esp_p90": esp("p90"), "esp_p99": esp("p99"),
            "paridad_max": max(r["paridad"]["max_abs"] for r in g),
            "volteos": sum(sum(r["paridad"]["volteos"].values()) for r in g),
            "nucleos": m(lambda r: r["nucleos_proceso"]),
            "silero_nucleos": m(lambda r: (r["silero"] or {}).get("nucleos_silero")),
            "mem_mb": m(lambda r: r["memoria_kb"]["sesiones_kb"] / 1024),
        }
        filas.append(fila)
        md.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3] or ''} | {f(k[4]) if k[4] else ''} | {len(g)} | {f(fila['caudal'])} | "
                  f"{f(fila['run_p50'])} / {f(fila['run_p90'])} / {f(fila['run_p99'])} | {f(fila['cpu_p50'])} | "
                  f"{f(fila['esp_p50'])} / {f(fila['esp_p90'])} / {f(fila['esp_p99'])} | {fila['paridad_max']:.1e} | "
                  f"{f(fila['nucleos'], 2)} | {f(fila['silero_nucleos'], 2)} | {f(fila['mem_mb'], 0)} |")

    # criterios
    md.append("\n## Criterios preregistrados\n")
    c1 = caudal_a(rs, 1)
    par = all(r["paridad"]["criterio_ok"] for r in rs if r["valido"])
    md.append(f"- **Paridad** (máx |Δp| ≤ 1e-6, 0 volteos en 0,35 y 0,85): {'CUMPLE' if par else 'NO CUMPLE'} en todas las condiciones válidas.")
    for p in sorted({r["p"] for r in rs if r["serie"] == "A"}):
        cp = caudal_a(rs, p)
        if c1 and cp:
            ok = cp >= 0.8 * p * c1
            md.append(f"- **Escala, P = {p}:** caudal {f(cp)}/s contra 0,8 × {p} × {f(c1)} = {f(0.8 * p * c1)}/s → {'escala' if ok else 'no escala'} "
                      f"({f(cp / (p * c1) * 100, 0)} % de lo lineal).")
    bp = mejor_p(rs)
    md.append(f"- **Mejor P** (regla del README): {bp}.")
    if bp:
        md.append(f"- **Mejor forma de sesión con P = {bp}:** {mejor_sesion(rs, bp)}.")
    c100 = [x for x in filas if x["serie"] == "C" and x["silero"] == 100]
    if c100:
        s = c100[0]["run_p50"]
        md.append(f"- **Contraste con M1** (S ≈ 72 ms a 100 llamadas): la serie C a 100 llamadas dio `Run` p50 {f(s)} ms. "
                  f"La carga de Silero usó {f(c100[0]['silero_nucleos'], 2)} núcleos, contra ~3,2 de GB a 100 llamadas "
                  f"({f((c100[0]['silero_nucleos'] or 0) / 3.2 * 100, 0)} %). Lecturas posibles: ver README.")
    reposo = max((r["lectura_de_vuelta"]["cpu_reposo_pct"] for r in rs), default=0)
    md.append(f"- **Reposo:** CPU máxima en los 5 s previos = {f(reposo, 2)} % de un núcleo ({'OK' if reposo <= 1 else 'hay hilos girando'}).")

    with open(os.path.join(d, "resumen.md"), "w") as fo:
        fo.write("\n".join(md) + "\n")
    if filas:
        with open(os.path.join(d, "resumen.tsv"), "w") as fo:
            fo.write("\t".join(filas[0].keys()) + "\n")
            for x in filas:
                fo.write("\t".join("" if v is None else str(v) for v in x.values()) + "\n")
    print("\n".join(md))


def main():
    if len(sys.argv) < 3:
        print(__doc__); return 2
    cmd, d = sys.argv[1], sys.argv[2]
    rs = cargar(d)
    if cmd == "mejor-p":
        print(mejor_p(rs) or "")
    elif cmd == "mejor-sesion":
        print(mejor_sesion(rs, int(sys.argv[3])))
    elif cmd == "capacidad":
        c = caudal_a(rs, int(sys.argv[3]))
        print("" if c is None else f"{c:.3f}")
    elif cmd == "resumen":
        resumen(d)
    else:
        print(__doc__); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
