#!/usr/bin/env python3
"""Smart-turn int8 vs fp32 en ESTA CPU. Corre dentro de la imagen goldenbridge:314t.

Escribe un JSON por stdout (el progreso va a stderr) con, para cada modelo:
  - la probabilidad de "terminó de hablar" en cada caso;
  - la latencia por inferencia (p50/p90/p99/máx).

Casos:
  congeladas_*  las 14 features de test/reference/features.npz. Son la MISMA
                entrada byte a byte en cualquier máquina: aíslan el efecto
                modelo + CPU, sin el extractor de features de por medio.
  <audio>@<ms>  cortes cada --paso-ms de cada audio, pasados por el extractor
                y la ventana de producción (últimos 8 s, ceros al principio).
                Es el camino completo, como en una llamada.

La sesión de ONNX Runtime se arma igual que en producción
(smart_turn_processor.py L118-122): inter_op=1, intra_op=GB_ORT_INTRA_OP,
ORT_ENABLE_ALL.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import wave

import numpy as np

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/test/reference")
from generate_reference import FE_KWARGS, as_production_window, cpu_isa, cpu_modelo  # noqa: E402


def log(msg):
    print(f"[compara] {msg}", file=sys.stderr, flush=True)


def sha16(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:16]


def sesion(path):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.inter_op_num_threads = 1
    so.intra_op_num_threads = int(os.environ.get("GB_ORT_INTRA_OP", "1"))
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])


def prob(sess, f):
    return float(sess.run(None, {"input_features": f})[0][0].item())


def leer_wav_16k(path):
    import soxr
    w = wave.open(path, "rb")
    nch, sw, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
    raw = w.readframes(w.getnframes())
    w.close()
    if sw != 2:
        raise SystemExit(f"{path}: se espera PCM16")
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if nch == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    if rate != 16000:
        x = soxr.resample(x, rate, 16000).astype(np.float32)
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--int8", required=True)
    ap.add_argument("--fp32", required=True)
    ap.add_argument("--audios", nargs="*", default=[],
                    help="archivos .wav o carpetas con .wav")
    ap.add_argument("--paso-ms", type=int, default=20)
    ap.add_argument("--desde-ms", type=int, default=300)
    ap.add_argument("--reps-latencia", type=int, default=50)
    a = ap.parse_args()

    import onnxruntime as ort
    from importlib.metadata import version
    from src.processors.whisper_features import WhisperFeatureExtractor

    modelos = {"int8": a.int8, "fp32": a.fp32}
    sesiones = {k: sesion(p) for k, p in modelos.items()}
    log(f"cpu={cpu_modelo()} isa={cpu_isa()} ort={ort.__version__} "
        f"intra_op={os.environ.get('GB_ORT_INTRA_OP', '1')}")

    # ── entradas ─────────────────────────────────────────────────────────────
    casos = {}
    ref = np.load("/app/test/reference/features.npz")
    for k in sorted(ref.files):
        casos[f"congeladas_{k}"] = np.asarray(ref[k], np.float32)

    wavs = []
    for p in a.audios:
        if os.path.isdir(p):
            wavs += sorted(os.path.join(p, f) for f in os.listdir(p) if f.endswith(".wav"))
        elif os.path.isfile(p):
            wavs.append(p)
    fe = WhisperFeatureExtractor(chunk_length=8)
    for w in wavs:
        x = leer_wav_16k(w)
        nombre = os.path.basename(w).rsplit(".", 1)[0]
        n = 0
        for ms in range(a.desde_ms, int(len(x) / 16) + 1, a.paso_ms):
            win = as_production_window(x[: ms * 16])
            f = np.asarray(fe(win, **FE_KWARGS).input_features, np.float32).squeeze(0)[None, ...]
            casos[f"{nombre}@{ms}"] = f
            n += 1
        log(f"{nombre}: {n} cortes")
    log(f"total de casos: {len(casos)}")

    # ── probabilidades ───────────────────────────────────────────────────────
    probs = {k: {} for k in modelos}
    for i, (c, f) in enumerate(casos.items()):
        for k, s in sesiones.items():
            probs[k][c] = prob(s, f)
        if i and i % 200 == 0:
            log(f"{i}/{len(casos)}")

    # ── latencia: tres entradas fijas, calentamiento descartado ─────────────
    muestras = [casos[c] for c in list(casos)[:3]]
    lat = {}
    for k, s in sesiones.items():
        for f in muestras:
            prob(s, f)
        t = []
        for _ in range(a.reps_latencia):
            for f in muestras:
                t0 = time.perf_counter()
                prob(s, f)
                t.append((time.perf_counter() - t0) * 1000.0)
        t.sort()
        q = lambda p: t[min(len(t) - 1, int(len(t) * p))]
        lat[k] = {"p50": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": t[-1], "n": len(t)}
        log(f"latencia {k}: p50={lat[k]['p50']:.1f} ms p99={lat[k]['p99']:.1f} ms")

    json.dump({
        "cpu_modelo": cpu_modelo(),
        "cpu_isa": cpu_isa(),
        "avx512_vnni": "avx512_vnni" in cpu_isa(),
        "nproc": os.cpu_count(),
        "intra_op": int(os.environ.get("GB_ORT_INTRA_OP", "1")),
        "onnxruntime": ort.__version__,
        "numpy": version("numpy"),
        "python": sys.version.split()[0],
        "modelos_sha256_16": {k: sha16(p) for k, p in modelos.items()},
        "audios": [os.path.basename(w) for w in wavs],
        "paso_ms": a.paso_ms,
        "probabilidades": probs,
        "latencia_ms": lat,
    }, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
