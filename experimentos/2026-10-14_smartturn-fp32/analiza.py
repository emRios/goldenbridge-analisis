#!/usr/bin/env python3
"""Compara el resultado de dos máquinas (una CON avx512_vnni, otra SIN).

    python3 analiza.py <resultado_vnni.json> <resultado_sin_vnni.json>

Sólo biblioteca estándar. Imprime el veredicto con los criterios
preregistrados del README y sale con 0 si el fp32 es independiente de la CPU.
"""
import json
import sys

UMBRALES = (0.35, 0.6, 0.85)   # .143 efectivo · default.yaml · midspeech_threshold
TOL_FP32 = 1e-4


def cargar(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def comparar(pa, pb, claves):
    """máx |Δp| y decisiones volteadas por umbral, sobre las claves comunes."""
    peor, peor_k = 0.0, None
    vueltas = {u: [] for u in UMBRALES}
    for k in claves:
        a, b = pa[k], pb[k]
        d = abs(a - b)
        if d > peor:
            peor, peor_k = d, k
        for u in UMBRALES:
            if (a > u) != (b > u):
                vueltas[u].append((k, a, b))
    return peor, peor_k, vueltas


def imprimir(titulo, peor, peor_k, vueltas, n):
    print(f"\n{titulo}  ({n} casos)")
    print(f"  máx |Δp| = {peor:.2e}  (en {peor_k})")
    for u, v in vueltas.items():
        print(f"  umbral {u:<4}: {len(v)} volteadas"
              + ("" if not v else "  → " + ", ".join(f"{k} {a:.4f}/{b:.4f}" for k, a, b in v[:6])
                 + (" …" if len(v) > 6 else "")))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    A, B = cargar(sys.argv[1]), cargar(sys.argv[2])
    print(f"A: {A['cpu_modelo']} · vnni={A['avx512_vnni']} · ort {A['onnxruntime']} · numpy {A['numpy']}")
    print(f"B: {B['cpu_modelo']} · vnni={B['avx512_vnni']} · ort {B['onnxruntime']} · numpy {B['numpy']}")

    avisos = []
    if A["avx512_vnni"] == B["avx512_vnni"]:
        avisos.append("las dos máquinas tienen el mismo valor de avx512_vnni: la prueba no compara lo que tiene que comparar")
    if A["modelos_sha256_16"] != B["modelos_sha256_16"]:
        avisos.append(f"los modelos no son los mismos archivos: {A['modelos_sha256_16']} vs {B['modelos_sha256_16']}")
    if (A["onnxruntime"], A["numpy"]) != (B["onnxruntime"], B["numpy"]):
        avisos.append("ONNX Runtime o numpy difieren entre máquinas: no es la misma imagen")
    for w in avisos:
        print(f"AVISO: {w}")

    comunes = sorted(set(A["probabilidades"]["int8"]) & set(B["probabilidades"]["int8"]))
    congeladas = [k for k in comunes if k.startswith("congeladas_")]
    barrido = [k for k in comunes if not k.startswith("congeladas_")]

    r = {}
    for modelo in ("int8", "fp32"):
        for nombre, claves in (("congeladas", congeladas), ("barrido", barrido)):
            if not claves:
                continue
            res = comparar(A["probabilidades"][modelo], B["probabilidades"][modelo], claves)
            r[(modelo, nombre)] = res
            imprimir(f"[entre CPUs] {modelo} · {nombre}", *res, len(claves))

    for etiqueta, R in (("A", A), ("B", B)):
        res = comparar(R["probabilidades"]["int8"], R["probabilidades"]["fp32"], comunes)
        imprimir(f"[mismo CPU {etiqueta}] int8 vs fp32", *res, len(comunes))

    print("\nlatencia por inferencia (ms)      p50     p90     p99     máx")
    for etiqueta, R in (("A", A), ("B", B)):
        for m in ("int8", "fp32"):
            l = R["latencia_ms"][m]
            print(f"  {etiqueta} {m:5s}                    {l['p50']:7.1f} {l['p90']:7.1f} {l['p99']:7.1f} {l['max']:7.1f}")

    # ── veredicto preregistrado ──────────────────────────────────────────────
    print("\nVEREDICTO")
    control = r.get(("int8", "congeladas"))
    control_ok = bool(control and control[0] >= 1e-3)
    print(f"  1. control (el int8 se mueve entre CPUs con las features congeladas, máx |Δp| ≥ 1e-3): "
          f"{'REPRODUCIDO' if control_ok else 'NO reproducido — revisar que una máquina tenga VNNI y la otra no'}"
          + (f" · {control[0]:.2e} · volteadas en 0,6: {len(control[2][0.6])} de {len(congeladas)}"
             f" (2026-08-24 se midieron 6 de 14)" if control else ""))
    fp32_ok = True
    for nombre in ("congeladas", "barrido"):
        res = r.get(("fp32", nombre))
        if res is None:
            continue
        peor, _, vueltas = res
        vol = sum(len(v) for v in vueltas.values())
        ok = vol == 0 and peor <= TOL_FP32
        fp32_ok &= ok
        print(f"  2. fp32 independiente de la CPU ({nombre}): {'SÍ' if ok else 'NO'} "
              f"(máx |Δp| {peor:.2e} ≤ {TOL_FP32:g}, volteadas {vol})")
    print("  3. int8 vs fp32 en la misma CPU: ver arriba. Las volteadas son decisiones que el "
          "modelo nuevo tomaría distinto; si es mejor o peor NO lo decide esta prueba")
    print("  4. latencia: ver tabla; el fp32 está en el camino crítico del fin de turno")
    if avisos:
        print("  CORRIDA NO VÁLIDA por los avisos de arriba")
        return 2
    return 0 if (control_ok and fp32_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
