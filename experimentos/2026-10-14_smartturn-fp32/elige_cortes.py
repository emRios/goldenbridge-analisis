#!/usr/bin/env python3
"""Elige los cortes de la referencia congelada para el modelo fp32.

    python3 elige_cortes.py <resultado.json de .143> --umbral 0.35

Lee el barrido de utterance.wav (claves "utterance@<ms>") del modelo fp32 y
propone, como generate_reference.py los usa:
  MARGINAL_MS   los 8 cortes con la probabilidad más pegada al umbral;
  CONFIDENT_MS  en cada tramo de 800 ms, el corte más lejos del umbral.

Imprime las dos líneas listas para pegar en test/reference/generate_reference.py.
Sólo biblioteca estándar.
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("resultado")
    ap.add_argument("--umbral", type=float, required=True)
    ap.add_argument("--modelo", default="fp32")
    ap.add_argument("--marginales", type=int, default=8)
    ap.add_argument("--tramo-ms", type=int, default=800)
    a = ap.parse_args()

    with open(a.resultado, encoding="utf-8") as fh:
        d = json.load(fh)
    p = {int(k.split("@", 1)[1]): v for k, v in d["probabilidades"][a.modelo].items()
         if k.startswith("utterance@")}
    if not p:
        raise SystemExit("el resultado no trae el barrido de utterance.wav")
    u = a.umbral

    marg = sorted(sorted(p, key=lambda ms: abs(p[ms] - u))[: a.marginales])
    conf = []
    for ini in range(0, max(p) + 1, a.tramo_ms):
        tramo = [ms for ms in p if ini <= ms < ini + a.tramo_ms and ms not in marg]
        if tramo:
            conf.append(max(tramo, key=lambda ms: abs(p[ms] - u)))
    conf = sorted(conf)

    print(f"# {a.modelo} · umbral {u} · {len(p)} cortes de utterance.wav · CPU {d['cpu_modelo']}")
    for ms in marg:
        print(f"#   marginal  {ms:5d} ms  p={p[ms]:.6f}  |p-u|={abs(p[ms]-u):.4f}")
    for ms in conf:
        print(f"#   confiado  {ms:5d} ms  p={p[ms]:.6f}  |p-u|={abs(p[ms]-u):.4f}")
    flojos = [ms for ms in conf if abs(p[ms] - u) < 0.1]
    if flojos:
        print(f"# AVISO: confiados a menos de 0,1 del umbral: {flojos}")
    print(f"CONFIDENT_MS = {conf}")
    print(f"MARGINAL_MS = {marg}")


if __name__ == "__main__":
    main()
