#!/usr/bin/env python3
"""Banco de smart-turn fp32. Corre DENTRO de la imagen goldenbridge:remarcacion-v1-jef-fp32 (Python 3.14t, ORT 1.24.4).
Una condición por proceso. Escribe un JSON con la lectura de vuelta, las medidas y los crudos por inferencia.

  python banco.py --serie A --p 4 --sesiones compartida --salida /out/x.json
  python banco.py --serie C --p 4 --sesiones compartida --silero-llamadas 100 --salida ...
  python banco.py --serie D --p 1 --tasa 10.5 --salida ...
  python banco.py --serie A --p 1 --medir 10 --calentar 2 --salida ...   (prueba corta)

Series (ver README):
  A/B/C: lazo cerrado — P hilos llaman a Run sin pausa sobre las entradas, sólo el Run (las features se miden aparte).
  C    : además, N llamadas simuladas de Silero, 31,25 chunks/s cada una, atendidas por 12 hilos (como vad.inference_workers).
  D    : llegadas Poisson a --tasa inferencias/s, en cola, atendidas por P hilos. Espera = cola + Run.
"""
import argparse, glob, hashlib, json, os, queue, random, sys, threading, time, wave
import numpy as np
import onnxruntime as ort

MODELO = "/app/smart-turn/smart-turn-v3.2-gpu.onnx"
SHA_ESPERADO = "ab8dc64b88713f90b571c15b714bd1330e6c883cad8763dacf65c9376dc539be"
NPZ = "/app/test/reference/features.npz"
SILERO = os.environ.get("TORCH_HOME", "/opt/modelos/torch") + "/hub/snakers4_silero-vad_master/src/silero_vad/data/silero_vad.onnx"
UMBRALES = (0.35, 0.85)
CHUNK_8K = 256                     # 32 ms a 8 kHz, como GB (vad.sample_rate 8000)
CHUNKS_POR_S = 1000.0 / 32.0       # 31,25


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cpu_proceso_s():
    st = open("/proc/self/stat").read().rsplit(")", 1)[1].split()
    hz = os.sysconf("SC_CLK_TCK")
    return (int(st[11]) + int(st[12])) / hz   # utime + stime


def cpu_del_hilo(tid):
    try:
        st = open(f"/proc/self/task/{tid}/stat").read().rsplit(")", 1)[1].split()
        return int(st[36])                    # campo 39 = processor (índice 36 tras el nombre)
    except OSError:
        return None


def rss_kb(campo="VmRSS"):
    for ln in open("/proc/self/status"):
        if ln.startswith(campo + ":"):
            return int(ln.split()[1])
    return None


def opciones_sesion():
    so = ort.SessionOptions()
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.inter_op_num_threads = 1
    so.intra_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return so


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * q))]


def resumen(xs):
    return {"n": len(xs), "p50": pct(xs, .5), "p90": pct(xs, .9), "p99": pct(xs, .99),
            "min": min(xs) if xs else None, "max": max(xs) if xs else None}


# ── lectura de vuelta ───────────────────────────────────────────────────────
def lectura_de_vuelta(sesiones):
    o = sesiones[0].get_session_options()
    cpuinfo = open("/proc/cpuinfo").read()
    modelo_cpu = next((l.split(":", 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith("model name")), "?")
    banderas = next((l.split(":", 1)[1].split() for l in cpuinfo.splitlines() if l.startswith("flags")), [])
    try:
        cpuset = open("/sys/fs/cgroup/cpuset.cpus.effective").read().strip()
    except OSError:
        cpuset = "?"
    return {
        "modelo_sha256": sha256(MODELO),
        "ort_version": ort.__version__,
        "python": sys.version.split()[0],
        "gil_activo": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True,
        "sesiones": len(sesiones),
        "intra_op_leido": o.intra_op_num_threads,
        "inter_op_leido": o.inter_op_num_threads,
        "modo_leido": str(o.execution_mode),
        "cpuset": cpuset,
        "cpu_modelo": modelo_cpu,
        "avx512_vnni": "avx512_vnni" in banderas,
        "avx512f": "avx512f" in banderas,
        "os_cpu_count": os.cpu_count(),
        "process_cpu_count": os.process_cpu_count() if hasattr(os, "process_cpu_count") else None,
    }


# ── entradas ────────────────────────────────────────────────────────────────
def entradas(audios_dir):
    """→ (lista de (nombre, features [1,80,800]), tiempos de features en ms)"""
    ent, t_feat = [], []
    d = np.load(NPZ)
    for k in sorted(d.files):
        ent.append(("npz:" + k, d[k].astype(np.float32)))
    wavs = sorted(glob.glob(os.path.join(audios_dir, "*.wav"))) if audios_dir else []
    if wavs:
        import soxr
        sys.path.insert(0, "/app")
        from src.processors.whisper_features import WhisperFeatureExtractor
        fe = WhisperFeatureExtractor(chunk_length=8)
        for f in wavs:
            w = wave.open(f)
            sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
            a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
            if ch > 1:
                a = a.reshape(-1, ch).mean(axis=1)
            a16 = soxr.resample(a, sr, 16000).astype(np.float32)
            # cortes como los de producción: los últimos 8 s del audio acumulado a 1/3, 2/3 y el final
            for frac in (1 / 3, 2 / 3, 1.0):
                fin = max(1, int(len(a16) * frac))
                corte = a16[max(0, fin - 128000):fin]
                corte = np.pad(corte, (128000 - len(corte), 0)) if len(corte) < 128000 else corte
                t = time.perf_counter()
                r = fe(corte, sampling_rate=16000, return_tensors="np", padding="max_length",
                       max_length=128000, truncation=True, do_normalize=True)
                t_feat.append((time.perf_counter() - t) * 1000)
                x = np.expand_dims(r.input_features.squeeze(0).astype(np.float32), 0)
                ent.append((f"wav:{os.path.basename(f)}@{frac:.2f}", x))
    return ent, t_feat


def audio_8k(audios_dir):
    """Audio real a 8 kHz para alimentar a Silero (concatenación de los wav)."""
    wavs = sorted(glob.glob(os.path.join(audios_dir, "*.wav"))) if audios_dir else []
    if not wavs:
        return np.random.default_rng(1).normal(0, 0.05, 8000 * 30).astype(np.float32)
    import soxr
    partes = []
    for f in wavs:
        w = wave.open(f)
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            a = a.reshape(-1, ch).mean(axis=1)
        partes.append(soxr.resample(a, sr, 8000).astype(np.float32))
    return np.concatenate(partes)


# ── carga de Silero (serie C) ───────────────────────────────────────────────
class CargaSilero:
    """N llamadas, cada una con su estado, 31,25 chunks/s; 12 hilos sobre una sesión compartida de silero_vad.onnx."""

    def __init__(self, n_llamadas, audio, hilos=12):
        self.n = n_llamadas
        self.audio = audio
        self.ses = ort.InferenceSession(SILERO, sess_options=opciones_sesion())
        self.q = queue.Queue()
        self.alto = threading.Event()
        self.hilos = hilos
        self.estado = [np.zeros((2, 1, 128), np.float32) for _ in range(n_llamadas)]
        self.contexto = [np.zeros((1, 32), np.float32) for _ in range(n_llamadas)]   # 32 muestras de contexto a 8 kHz
        self.lock_llamada = [threading.Lock() for _ in range(n_llamadas)]
        self.hechos = 0
        self.atraso_ms = []
        self.cpu_hilos_s = 0.0
        self._lk = threading.Lock()

    def _trabajador(self):
        c0 = time.thread_time()
        while not self.alto.is_set():
            try:
                i, pos, t_plan = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            x = self.audio[pos:pos + CHUNK_8K]
            if len(x) < CHUNK_8K:
                x = np.pad(x, (0, CHUNK_8K - len(x)))
            with self.lock_llamada[i]:                      # orden por llamada, como el lock por instancia de GB
                entrada = np.concatenate([self.contexto[i], x[None, :]], axis=1)
                out, st = self.ses.run(None, {"input": entrada, "state": self.estado[i], "sr": np.array(8000, dtype=np.int64)})
                self.estado[i] = st
                self.contexto[i] = entrada[:, -32:]
            with self._lk:
                self.hechos += 1
                self.atraso_ms.append((time.perf_counter() - t_plan) * 1000)
        with self._lk:
            self.cpu_hilos_s += time.thread_time() - c0

    def _reloj(self):
        periodo = 1.0 / CHUNKS_POR_S
        t0 = time.perf_counter()
        fases = [random.Random(i).random() * periodo for i in range(self.n)]
        k = [0] * self.n
        largo = len(self.audio) - CHUNK_8K
        while not self.alto.is_set():
            ahora = time.perf_counter() - t0
            prox = None
            for i in range(self.n):
                t_i = fases[i] + k[i] * periodo
                if t_i <= ahora:
                    pos = ((i * 7919 + k[i]) * CHUNK_8K) % max(1, largo)
                    self.q.put((i, pos, t0 + t_i))
                    k[i] += 1
                    t_i += periodo
                prox = t_i if prox is None else min(prox, t_i)
            espera = (prox - (time.perf_counter() - t0)) if prox is not None else 0.005
            if espera > 0:
                time.sleep(min(espera, 0.005))

    def arrancar(self):
        self.ths = [threading.Thread(target=self._trabajador, daemon=True) for _ in range(self.hilos)]
        self.ths.append(threading.Thread(target=self._reloj, daemon=True))
        for t in self.ths:
            t.start()

    def reiniciar_medida(self):
        with self._lk:
            self.hechos = 0
            self.atraso_ms = []

    def parar(self):
        self.alto.set()
        for t in self.ths:
            t.join(timeout=2)


# ── principal ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serie", required=True, choices=list("ABCD"))
    ap.add_argument("--p", type=int, required=True, help="hilos llamando a Run")
    ap.add_argument("--sesiones", choices=["compartida", "por_hilo"], default="compartida")
    ap.add_argument("--silero-llamadas", type=int, default=0)
    ap.add_argument("--tasa", type=float, default=0.0, help="serie D: inferencias/s Poisson")
    ap.add_argument("--calentar", type=float, default=10.0)
    ap.add_argument("--medir", type=float, default=60.0)
    ap.add_argument("--reposo", type=float, default=5.0)
    ap.add_argument("--audios", default="/audios")
    ap.add_argument("--etiqueta", default="")
    ap.add_argument("--semilla", type=int, default=11)
    ap.add_argument("--salida", required=True)
    a = ap.parse_args()
    random.seed(a.semilla)

    rss_antes = rss_kb()
    n_ses = a.p if a.sesiones == "por_hilo" else 1
    sesiones = [ort.InferenceSession(MODELO, sess_options=opciones_sesion()) for _ in range(n_ses)]
    rss_con_sesiones = rss_kb()
    lv = lectura_de_vuelta(sesiones)
    problemas = []
    if lv["modelo_sha256"] != SHA_ESPERADO: problemas.append("sha256 del modelo")
    if (lv["intra_op_leido"], lv["inter_op_leido"]) != (1, 1): problemas.append("intra/inter leídos de la sesión no son 1/1")
    if "SEQUENTIAL" not in lv["modo_leido"]: problemas.append("modo no secuencial")
    if lv["cpuset"] != os.environ.get("CPUSET_ESPERADO", lv["cpuset"]): problemas.append(f"cpuset {lv['cpuset']}")

    ent, t_feat = entradas(a.audios)
    nombre_in = sesiones[0].get_inputs()[0].name
    # referencia en serie (una sesión, un hilo)
    ref = [float(sesiones[0].run(None, {nombre_in: x})[0].reshape(-1)[0]) for _, x in ent]

    # reposo: CPU del proceso sin trabajo
    c0, t0 = cpu_proceso_s(), time.perf_counter()
    time.sleep(a.reposo)
    cpu_reposo_pct = 100.0 * (cpu_proceso_s() - c0) / (time.perf_counter() - t0)
    hilos_reposo = len(os.listdir("/proc/self/task"))
    if cpu_reposo_pct > 1.0: problemas.append(f"CPU en reposo {cpu_reposo_pct:.2f} % > 1 %")

    silero = None
    if a.serie == "C" and a.silero_llamadas > 0:
        silero = CargaSilero(a.silero_llamadas, audio_8k(a.audios))
        silero.arrancar()

    midiendo = threading.Event()
    alto = threading.Event()
    lk = threading.Lock()
    regs = []            # (t_fin, idx_hilo, idx_entrada, pared_ms, cpu_ms, espera_ms)
    dif = {"max_abs": 0.0, "n_dif": 0, "volteos": {str(u): 0 for u in UMBRALES}}
    tids = []
    cola = queue.Queue()

    def correr(h):
        ses = sesiones[h % n_ses]
        tids.append(threading.get_native_id())
        rng = random.Random(a.semilla * 1000 + h)
        while not alto.is_set():
            if a.serie == "D":
                try:
                    j, t_llega = cola.get(timeout=0.05)
                except queue.Empty:
                    continue
            else:
                j, t_llega = rng.randrange(len(ent)), None
            x = ent[j][1]
            t_ini, c_ini = time.perf_counter(), time.thread_time()
            p = float(ses.run(None, {nombre_in: x})[0].reshape(-1)[0])
            t_fin, c_fin = time.perf_counter(), time.thread_time()
            d = abs(p - ref[j])
            with lk:
                if d > dif["max_abs"]: dif["max_abs"] = d
                if d != 0.0: dif["n_dif"] += 1
                for u in UMBRALES:
                    if (p > u) != (ref[j] > u): dif["volteos"][str(u)] += 1
                if midiendo.is_set():
                    regs.append((t_fin, h, j, (t_fin - t_ini) * 1000, (c_fin - c_ini) * 1000,
                                 ((t_ini - t_llega) * 1000) if t_llega else None))

    def llegadas():
        while not alto.is_set():
            time.sleep(random.expovariate(a.tasa))
            cola.put((random.randrange(len(ent)), time.perf_counter()))

    ths = [threading.Thread(target=correr, args=(h,), daemon=True) for h in range(a.p)]
    for t in ths: t.start()
    if a.serie == "D":
        if a.tasa <= 0: sys.exit("serie D exige --tasa > 0")
        threading.Thread(target=llegadas, daemon=True).start()

    time.sleep(a.calentar)
    if silero: silero.reiniciar_medida(); silero_c0 = silero.cpu_hilos_s
    muestras_cpu, muestras_cola = {}, []
    c_ini_proc = cpu_proceso_s(); t_ini = time.perf_counter(); midiendo.set()
    while time.perf_counter() - t_ini < a.medir:
        time.sleep(0.5)
        for tid in list(tids):
            c = cpu_del_hilo(tid)
            if c is not None: muestras_cpu.setdefault(str(tid), []).append(c)
        if a.serie == "D": muestras_cola.append(cola.qsize())
    midiendo.clear(); dur = time.perf_counter() - t_ini
    cpu_proc = cpu_proceso_s() - c_ini_proc
    rss_fin, rss_pico = rss_kb(), rss_kb("VmHWM")
    alto.set()
    sil = None
    if silero:
        hechos, atrasos = silero.hechos, list(silero.atraso_ms)
        silero.parar()
        sil = {"llamadas": a.silero_llamadas, "chunks_s_pedidos": a.silero_llamadas * CHUNKS_POR_S,
               "chunks_s_hechos": hechos / dur, "atraso_ms": resumen(atrasos),
               "nucleos_silero": (silero.cpu_hilos_s) / dur}
    for t in ths: t.join(timeout=2)

    pared = [r[3] for r in regs]; cpu = [r[4] for r in regs]; esp = [r[5] for r in regs if r[5] is not None]
    out = {
        "etiqueta": a.etiqueta, "serie": a.serie, "p": a.p, "sesiones": a.sesiones, "n_sesiones_ort": n_ses,
        "silero_llamadas": a.silero_llamadas, "tasa": a.tasa, "calentar_s": a.calentar, "medir_s": dur,
        "lectura_de_vuelta": {**lv, "cpu_reposo_pct": cpu_reposo_pct, "hilos_en_reposo": hilos_reposo},
        "problemas": problemas, "valido": not problemas,
        "entradas": len(ent), "features_ms": resumen(t_feat),
        "caudal_inf_s": len(regs) / dur,
        "run_pared_ms": resumen(pared), "run_cpu_ms": resumen(cpu),
        "espera_total_ms": resumen([e + w for e, w in zip(esp, [r[3] for r in regs if r[5] is not None])]) if esp else None,
        "espera_cola_ms": resumen(esp) if esp else None,
        "cola_largo": resumen(muestras_cola) if muestras_cola else None,
        "nucleos_proceso": cpu_proc / dur,
        "paridad": {**dif, "criterio_ok": dif["max_abs"] <= 1e-6 and all(v == 0 for v in dif["volteos"].values())},
        "memoria_kb": {"antes": rss_antes, "con_sesiones": rss_con_sesiones, "fin": rss_fin, "pico": rss_pico,
                       "sesiones_kb": (rss_con_sesiones or 0) - (rss_antes or 0)},
        "cpu_por_hilo": {tid: sorted(set(v)) for tid, v in muestras_cpu.items()},
        "silero": sil,
        "crudo": {"pared_ms": [round(x, 3) for x in pared], "cpu_ms": [round(x, 3) for x in cpu],
                  "hilo": [r[1] for r in regs], "entrada": [r[2] for r in regs]},
    }
    os.makedirs(os.path.dirname(a.salida) or ".", exist_ok=True)
    with open(a.salida, "w") as f:
        json.dump(out, f, ensure_ascii=False)
    r = out["run_pared_ms"]
    print(f"[banco] {a.serie} P={a.p} {a.sesiones} silero={a.silero_llamadas} tasa={a.tasa} · válido={out['valido']} "
          f"{problemas or ''} · caudal {out['caudal_inf_s']:.1f}/s · Run p50 {r['p50']:.1f} p90 {r['p90']:.1f} p99 {r['p99']:.1f} ms · "
          f"paridad máx {dif['max_abs']:.2e} volteos {dif['volteos']} · reposo {cpu_reposo_pct:.2f} % · "
          f"núcleos {out['nucleos_proceso']:.2f}" + (f" · silero {sil['nucleos_silero']:.2f} núcleos, {sil['chunks_s_hechos']:.0f}/{sil['chunks_s_pedidos']:.0f} chunks/s" if sil else ""))


if __name__ == "__main__":
    main()
