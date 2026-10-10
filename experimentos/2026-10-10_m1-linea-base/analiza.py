#!/usr/bin/env python3
"""M1 · resume cada escalón de la escalera en una fila de resumen.tsv.

    python3 analiza.py <crudo/SELLO> <resultados/SELLO> --ramp 60 --warmup 60 --estable 600

Sólo biblioteca estándar: corre en el host, sin el venv de GoldenBridge.

Ventana de cada escalón: [t0 + 2 + ramp + warm-up, t0 + ramp + warm-up + estable].
Lo de fuera (rampa, calentamiento, apagado) se descarta para todas las métricas
salvo muertes y errores, que se cuentan en todo el escalón.

CYCLE-PROBE emite por sesión, cada 5 s, el p50 y el p90 de cada fase en esa
ventana. Las columnas cp_* son la MEDIANA y el P90 de esos p90 por ventana a
través de sesiones y ventanas: misma construcción que la curva histórica de
CLAUDE.md (bandas de concurrencia → p90 de fase).
"""
import argparse
import glob
import os
import re
import statistics
from datetime import datetime, timedelta, timezone

ANSI = re.compile(r"\x1b\[[0-9;]*m")
KV = re.compile(r"(\w+)=('[^']*'|\"[^\"]*\"|\S+)")
FASES = ("cycle_p90", "submit_p90", "job_p90", "resume_vad_p90", "smartturn_p90",
         "post_p90", "steno_p90")


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * q))]


def fmt(x, nd=1):
    return "" if x is None else f"{x:.{nd}f}"


def leer_efectivo(path):
    d = {}
    for linea in open(path, encoding="utf-8"):
        if "=" in linea:
            k, v = linea.rstrip("\n").split("=", 1)
            d[k] = v
    return d


def ts_docker(token):
    """2026-10-10T14:03:22.123456789Z -> epoch (s)."""
    base, _, frac = token.rstrip("Z").partition(".")
    t = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return t.timestamp() + (float("0." + frac[:6]) if frac else 0.0)


def leer_gb(path, ini, fin):
    fases = {f: [] for f in FASES}
    fpc, errores, lineas = [], 0, 0
    for crudo in open(path, encoding="utf-8", errors="replace"):
        token, _, resto = crudo.partition(" ")
        try:
            t = ts_docker(token)
        except ValueError:
            continue
        if not (ini <= t <= fin):
            continue
        resto = ANSI.sub("", resto)
        if "[error" in resto or "[critical" in resto:
            errores += 1
        if "CYCLE-PROBE" not in resto:
            continue
        lineas += 1
        kv = dict(KV.findall(resto))
        for f in FASES:
            try:
                fases[f].append(float(kv[f]))
            except (KeyError, ValueError):
                pass
        try:
            fpc.append(float(kv["frames_per_cycle"]))
        except (KeyError, ValueError):
            pass
    return fases, fpc, errores, lineas


def leer_cliente(path, t0, ini, fin):
    dia = datetime.fromtimestamp(t0, timezone.utc).replace(hour=0, minute=0, second=0,
                                                            microsecond=0)
    ping = {"load": [], "ctrl": []}
    ttfab, corte_c, corte_n, stalls, genlag = [], [], 0, 0, []
    muertes, errores, aplicadas = 0, 0, 0
    speak = done = perdidos = 0
    for linea in open(path, encoding="utf-8"):
        c = linea.rstrip("\n").split("\t")
        if len(c) < 5 or c[0] == "ts_utc":
            continue
        ts, kind, _sid, role, val = c[:5]
        extra = c[5] if len(c) > 5 else ""
        h = datetime.strptime(ts, "%H:%M:%S.%f")
        t = (dia + timedelta(hours=h.hour, minutes=h.minute, seconds=h.second,
                             microseconds=h.microsecond)).timestamp()
        if t < t0 - 3600:                     # cruzó la medianoche
            t += 86400
        v = float(val)
        dentro = ini <= t <= fin
        if kind == "applied":
            aplicadas += 1
        elif kind == "death":
            muertes += 1
        elif kind == "error":
            errores += 1
        elif kind == "gen_lag":
            genlag.append(v)
        elif kind == "end_ok":
            kv = dict(p.split("=", 1) for p in extra.split("|") if "=" in p)
            if role == "load":
                speak += int(kv.get("speak", 0))
                done += int(kv.get("audio_done", 0))
                perdidos += int(kv.get("ttfab_perdidos", 0))
        elif not dentro:
            continue
        elif kind == "ping":
            ping.setdefault(role, []).append(v)
        elif kind in ("ping_timeout", "ping_deadconn", "ping_err"):
            ping.setdefault(role, []).append(float("inf"))
        elif kind == "ttfab":
            ttfab.append(v)
        elif kind == "corte":
            if "fin=natural" in extra:
                corte_n += 1
            else:
                corte_c.append(v)
        elif kind == "send_stall":
            stalls += 1
    return dict(ping=ping, ttfab=ttfab, corte_c=corte_c, corte_n=corte_n, stalls=stalls,
                genlag=genlag, muertes=muertes, errores=errores, aplicadas=aplicadas,
                speak=speak, done=done, perdidos=perdidos)


def leer_stats(path, ini, fin):
    cpu, mem = [], []
    for linea in open(path, encoding="utf-8"):
        c = linea.rstrip("\n").split("\t")
        if len(c) < 3 or not c[1]:
            continue
        try:
            t = float(c[0])
        except ValueError:
            continue
        if not (ini <= t <= fin):
            continue
        try:
            cpu.append(float(c[1].rstrip("%")) / 100.0)
        except ValueError:
            pass
        m = re.match(r"([\d.]+)\s*([KMG]i?B)", c[2])
        if m:
            esc = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "KB": 1 / 1000, "MB": 1,
                   "GB": 1000}.get(m.group(2), 1)
            mem.append(float(m.group(1)) * esc)
    return cpu, mem


def _num(r, c):
    try:
        return float(r[c])
    except (KeyError, TypeError, ValueError):
        return None


def _empeora(v, base):
    """README, criterios 2 y 4: más de máx(10 %, 20 ms) contra el escalón base."""
    if v is None or base is None:
        return False
    return v > base + max(0.10 * base, 20.0)


def aplicar_criterios(filas):
    """Criterios preregistrados del README. La base de cada repetición es su
    escalón más chico (10 en la escalera completa)."""
    base = {}
    for r in filas:
        if r["valido"] == "si" and (r["rep"] not in base or r["n"] < base[r["rep"]]["n"]):
            base[r["rep"]] = r
    for r in filas:
        b = base.get(r["rep"])
        fallas = []
        if r["valido"] != "si":
            fallas.append("no valido")
        cmed, cp90 = _num(r, "cp_cycle_p90_med"), _num(r, "cp_cycle_p90_p90")
        if cmed is None or cmed > 5.0:
            fallas.append(f"cycle_p90 med {r['cp_cycle_p90_med']}>5")
        if cp90 is None or cp90 > 20.0:
            fallas.append(f"cycle_p90 p90 {r['cp_cycle_p90_p90']}>20")
        pc = _num(r, "ping_ctrl_p99")
        if pc is None or pc > 50.0:
            fallas.append(f"ping_ctrl p99 {r['ping_ctrl_p99']}>50")
        if b is not None and b is not r:
            if _empeora(_num(r, "ttfab_p50"), _num(b, "ttfab_p50")):
                fallas.append(f"ttfab_p50 {r['ttfab_p50']} vs {b['ttfab_p50']}")
            if _empeora(_num(r, "corte_p90"), _num(b, "corte_p90")):
                fallas.append(f"corte_p90 {r['corte_p90']} vs {b['corte_p90']}")
        r["sano"] = "si" if not fallas else "NO"
        r["falla"] = "; ".join(fallas)

    reps = sorted({r["rep"] for r in filas})
    ns = sorted({r["n"] for r in filas})
    sanos = [n for n in ns
             if all(any(r["n"] == n and r["rep"] == rp and r["sano"] == "si" for r in filas)
                    for rp in reps)]
    # K = mayor N tal que él y todos los menores son sanos en todas las repeticiones
    k = None
    for n in ns:
        if n in sanos:
            k = n
        else:
            break
    rodillas = []
    for rp in reps:
        b = base.get(rp)
        bc = _num(b, "cp_cycle_p90_med") if b else None
        for r in sorted((r for r in filas if r["rep"] == rp), key=lambda r: r["n"]):
            c = _num(r, "cp_cycle_p90_med")
            if bc and c is not None and c > 2 * bc:
                rodillas.append(f"rep {rp}: N={r['n']}")
                break
        else:
            rodillas.append(f"rep {rp}: no se alcanzó")
    lineas = [f"repeticiones={len(reps)} escalones={ns}",
              f"K60 (mayor N sano en todas las repeticiones, con todos los menores sanos) = "
              f"{k if k is not None else 'ninguno'}"
              + (" — sólo establece K60 >= ese N, no un techo" if k == max(ns) else ""),
              "rodilla60 (primer N con cycle_p90 med > 2x el de la base) = " + " · ".join(rodillas)]
    if k is not None and k >= 100:
        lineas.append("K60 >= 100: aplica la regla de beneficio de la estrategia §7 "
                      "(no se reescribe por capacidad).")
    return "\n".join(lineas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("crudo")
    ap.add_argument("salida")
    ap.add_argument("--ramp", type=float, default=60)
    ap.add_argument("--warmup", type=float, default=60)
    ap.add_argument("--estable", type=float, default=600)
    a = ap.parse_args()

    cols = ["paso", "rep", "n", "valido", "motivo", "aplicadas", "muertes", "errores_cli",
            "errores_gb", "send_stall", "gen_lag_p99_max",
            "ping_load_p50", "ping_load_p99", "ping_ctrl_p50", "ping_ctrl_p99",
            "ttfab_n", "ttfab_p50", "ttfab_p90", "ttfab_p99", "ttfab_perdidos",
            "corte_n", "corte_p50", "corte_p90", "corte_p99", "corte_natural",
            "speak", "audio_done", "cp_lineas", "cp_fpc_med", "sano", "falla"]
    for f in FASES:
        base = f[:-4]
        cols += [f"cp_{base}_p90_med", f"cp_{base}_p90_p90"]
    cols += ["cpu_nucleos_med", "cpu_nucleos_max", "cpu_por_llamada", "mem_mib_max",
             "arranque_s", "nano_cpus", "cgroup_cpu_max", "cgroup_cpuset"]

    filas = []
    for d in sorted(glob.glob(os.path.join(a.crudo, "r*_n*"))):
        ef = leer_efectivo(os.path.join(d, "efectivo.txt"))
        if "t0_epoch" not in ef:
            continue
        t0 = float(ef["t0_epoch"])
        ini, fin = t0 + 2 + a.ramp + a.warmup, t0 + a.ramp + a.warmup + a.estable
        n = int(ef["n"])
        cli = leer_cliente(os.path.join(d, "cliente.tsv"), t0, ini, fin)
        fases, fpc, err_gb, cp_lineas = leer_gb(os.path.join(d, "gb.log"), ini, fin)
        cpu, mem = leer_stats(os.path.join(d, "stats.tsv"), ini, fin)

        gl = max(cli["genlag"]) if cli["genlag"] else None
        motivos = []
        if cli["aplicadas"] < n + 3:
            motivos.append(f"aplicadas {cli['aplicadas']}<{n + 3}")
        if cli["muertes"] or cli["errores"]:
            motivos.append("muertes/errores")
        if gl is None or gl > 5.0:
            motivos.append(f"gen_lag p99 {fmt(gl)}>5ms")
        if cp_lineas == 0:
            motivos.append("sin CYCLE-PROBE")
        if ef.get("cliente_rc") not in ("0", None):
            motivos.append(f"cliente rc={ef.get('cliente_rc')}")
        cpu_med = statistics.median(cpu) if cpu else None
        r = {
            "paso": os.path.basename(d), "rep": ef.get("rep"), "n": n,
            "valido": "si" if not motivos else "NO", "motivo": "; ".join(motivos),
            "aplicadas": cli["aplicadas"], "muertes": cli["muertes"],
            "errores_cli": cli["errores"], "errores_gb": err_gb, "send_stall": cli["stalls"],
            "gen_lag_p99_max": fmt(gl),
            "ping_load_p50": fmt(pct(cli["ping"]["load"], .5)),
            "ping_load_p99": fmt(pct(cli["ping"]["load"], .99)),
            "ping_ctrl_p50": fmt(pct(cli["ping"]["ctrl"], .5)),
            "ping_ctrl_p99": fmt(pct(cli["ping"]["ctrl"], .99)),
            "ttfab_n": len(cli["ttfab"]), "ttfab_p50": fmt(pct(cli["ttfab"], .5), 0),
            "ttfab_p90": fmt(pct(cli["ttfab"], .9), 0), "ttfab_p99": fmt(pct(cli["ttfab"], .99), 0),
            "ttfab_perdidos": cli["perdidos"],
            "corte_n": len(cli["corte_c"]), "corte_p50": fmt(pct(cli["corte_c"], .5), 0),
            "corte_p90": fmt(pct(cli["corte_c"], .9), 0),
            "corte_p99": fmt(pct(cli["corte_c"], .99), 0), "corte_natural": cli["corte_n"],
            "speak": cli["speak"], "audio_done": cli["done"],
            "cp_lineas": cp_lineas, "cp_fpc_med": fmt(statistics.median(fpc) if fpc else None, 2),
            "cpu_nucleos_med": fmt(cpu_med, 2), "cpu_nucleos_max": fmt(max(cpu) if cpu else None, 2),
            "cpu_por_llamada": fmt(cpu_med / n if cpu_med is not None else None, 4),
            "mem_mib_max": fmt(max(mem) if mem else None, 0),
            "arranque_s": ef.get("arranque_s", ""), "nano_cpus": ef.get("nano_cpus", ""),
            "cgroup_cpu_max": ef.get("cgroup_cpu_max", ""),
            "cgroup_cpuset": ef.get("cgroup_cpuset", ""),
        }
        for f in FASES:
            base = f[:-4]
            r[f"cp_{base}_p90_med"] = fmt(statistics.median(fases[f]) if fases[f] else None, 2)
            r[f"cp_{base}_p90_p90"] = fmt(pct(fases[f], .9), 2)
        filas.append(r)

    filas.sort(key=lambda r: (r["n"], str(r["rep"])))
    veredicto = aplicar_criterios(filas)
    with open(os.path.join(a.salida, "resumen.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(cols) + "\n")
        for r in filas:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    with open(os.path.join(a.salida, "veredicto.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(veredicto + "\n")

    vista = ["n", "rep", "valido", "sano", "muertes", "gen_lag_p99_max", "cp_cycle_p90_med",
             "cp_resume_vad_p90_med", "cp_submit_p90_med", "ping_ctrl_p99", "ttfab_p50",
             "ttfab_p90", "corte_p90", "cpu_nucleos_med"]
    print("\t".join(vista))
    for r in filas:
        print("\t".join(str(r.get(c, "")) for c in vista))
    if not filas:
        print("(sin escalones con t0_epoch)")
    print()
    print(veredicto)


if __name__ == "__main__":
    main()
