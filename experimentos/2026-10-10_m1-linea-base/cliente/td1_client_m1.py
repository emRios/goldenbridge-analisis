#!/usr/bin/env python3
"""[M1 · copia del experimento 2026-10-10_m1-linea-base]
Agrega tres filas al TSV, sin cambiar el envío ni la recepción:
  gen_lag  retraso del generador (p99 por sesión, en end_ok y fila propia)
  ttfab    fin de la VOZ del usuario -> primer audio del bot
  corte    inicio de la VOZ que interrumpe -> último audio del bot de ese episodio
           (extra fin=natural si llegó AgentAudioDone: la interrupción no cortó)
La voz de cada frase se ubica por energía (voz_span): los audios traen
silencio al inicio y al final, y anclar al archivo medía otra cosa.
Al interrumpir, el locutor salta al inicio de la VOZ, no del archivo.
[M1b 2026-10-19] --espera-bot: cada frase espera a que el bot termine su
respuesta, como una persona, en vez de seguir un horario fijo. Con horario
fijo (pausa 8 s) el ensayo del 19-oct pisó al bot en ~6 de cada 10 frases
(90 barge-in, 28 intencionales) y midió interrupciones, no turnos.
Origen y hash en PROCEDENCIA.txt.

T-D1 — burst con sondas por-conexión para zanjar M1 (app-drain) vs M2 (loop-starvation).

Extiende latency_incident/scripts/burst_client.py con:
  * roles: N sesiones LOAD (silencio mulaw 50fps) + K sesiones CTRL (5fps) — la CTRL
    nunca llena su cola websockets (tope 32); si sus pings TAMBIÉN se degradan, el
    problema es global (M2); si solo las LOAD mueren con CTRL sana → M1.
  * prober por sesión: ws.ping() explícito cada --ping-every s; la librería devuelve
    la latencia medida (Awaitable[float]). Si transfer_data() del server está
    bloqueado en la cola llena, NO parsea el PING → el RTT crece hasta el desbloqueo
    o la muerte. RTT por-conexión = sonda del bloqueo por-conexión SIN tocar GB.
  * detección de send-stall (backpressure TCP: el server dejó de leer el socket).
  * atribución del cierre: quién envió 1011 primero (rcvd=GB-1o / sent=cliente-nopong),
    equivalente al split sent/recv de la tabla admosa.
  * TSV incremental para td1_analyze.py + puerto local por sesión (correlación con
    la sonda opcional del server, que loguea remote_address).

Uso típico (ver RUNBOOK_TD1.md):
  # T-D1  (silencio puro — Fase 1):
  taskset -c 2,3 python3 td1_client.py --sessions 40 --controls 3 --ramp 40 --hold 180 \
      --out td1_out.tsv
  # T-D1b (con habla — dispara VAD→turno→STT→LLM→TTS; ejercita el downlink de C2/C3):
  taskset -c 2,3 python3 td1_client.py --sessions 12 --controls 3 --ramp 30 --hold 180 \
      --speech utterance.wav --pause-s 8 --out td1b_out.tsv
  # --speech acepta WAV PCM16 (cualquier rate, mono/estéreo; convierte a mulaw 8k con
  # audioop) o crudo .ulaw/.raw ya en mulaw 8k. Las CTRL siguen en silencio SIEMPRE.
  # Cada sesión LOAD alterna utterance→pausa (silencio) en fase distinta, manteniendo
  # 50fps continuos como el uplink real del engine. ⚠ T-D1b genera turnos REALES
  # (Steno + LLM + TTS): costo por uso — empezar con pocas sesiones.

  # T-D1c (detección de mezclas entre llamadas): --speech acepta una CARPETA.
  #   taskset -c 6,7 python3 td1_client.py --sessions 100 --controls 3 \
  #       --speech utterances/listos --pause-s 8 --out td1c.tsv
  # Con un solo audio todas las sesiones dicen lo mismo, así que una contaminación
  # entre llamadas produciría una transcripción idéntica a la correcta: la corrida
  # es CIEGA al defecto que se quiere cazar. Con una carpeta, la sesión i reproduce
  # el audio i%N y su marca viaja en el call_uuid; el servidor ya loguea session_id
  # junto al call_uuid, así que verifica_mezclas.py puede cruzar log→grupo sin
  # tabla aparte. Ver utterances/firmas.json.

  # P0 Modal (misma señal, tres frames por mensaje; CTRL queda a 20 ms):
  #   GB_PROXY_KEY=... GB_PROXY_SECRET=... python3 td1_client.py \
  #       --url wss://<endpoint>/v1/agent/converse --sessions 10 --controls 0 \
  #       --speech <carpeta-ulaw> --packet-ms 60 --capture-text --out p0_60ms.tsv
"""
import argparse
import asyncio
import glob
import json
import os
import statistics
import time
from datetime import datetime, timezone

try:
    import audioop  # py<=3.12; solo requerido con --speech WAV
    import wave
except ImportError:  # pragma: no cover
    audioop = wave = None

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

SILENCE_FRAME = bytes([0xFF]) * 160  # 20ms mulaw @ 8k
FRAME_S = 0.02

SETTINGS = {
    "type": "Settings",
    "audio": {
        "input": {"encoding": "mulaw", "sample_rate": 8000},
        "output": {"encoding": "mulaw", "sample_rate": 8000, "container": "none"},
    },
    "agent": {
        "language": "en",
        "listen": {"provider": {"type": "deepgram", "model": "nova-3"}},
        "think": {
            "provider": {"type": "open_ai", "model": "gpt-4o-mini", "temperature": 0.7},
            "prompt": "You are a silent test agent. Never speak unless spoken to.",
        },
        "speak": {"provider": {"type": "deepgram", "model": "aura-2-luna-es"}},
        "greeting": "",
    },
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def proxy_headers_from_env():
    """Devuelve los headers del proxy de Modal sin exponer secretos en argv.

    La prueba local no necesita autenticación. Para un endpoint protegido se
    requieren AMBAS variables; una sola suele ser un error de copiado y fallar
    más adelante con 401 ocultaría el motivo real del PoC.
    """
    key = os.environ.get("GB_PROXY_KEY")
    secret = os.environ.get("GB_PROXY_SECRET")
    if bool(key) != bool(secret):
        raise SystemExit(
            "Defina ambas variables GB_PROXY_KEY y GB_PROXY_SECRET, o ninguna. "
            "No pase secretos por argumentos."
        )
    if not key:
        return None
    return [("Modal-Key", key), ("Modal-Secret", secret)]


def load_speech(path, utt_s):
    """Carga la utterance y la deja como frames mulaw 8k de 160B (20ms).

    .ulaw/.raw → bytes tal cual (ya mulaw 8k). .wav → PCM16 mono/estéreo a
    cualquier rate: tomono + ratecv(→8k) + lin2ulaw (sin sox/ffmpeg, no hay en el host).
    """
    if path.endswith((".ulaw", ".raw")):
        ulaw = open(path, "rb").read()
    else:
        if wave is None or audioop is None:
            raise SystemExit("--speech WAV requiere módulos wave/audioop (python <=3.12)")
        w = wave.open(path, "rb")
        nch, sw, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        pcm = w.readframes(w.getnframes())
        w.close()
        if sw != 2:
            raise SystemExit(f"--speech: WAV debe ser PCM16 (sampwidth=2, no {sw})")
        if nch == 2:
            pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        elif nch != 1:
            raise SystemExit(f"--speech: {nch} canales no soportados")
        if rate != 8000:
            pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 8000, None)
        ulaw = audioop.lin2ulaw(pcm, 2)
    if utt_s > 0:
        ulaw = ulaw[: int(utt_s * 8000)]
    frames = [ulaw[i:i + 160] for i in range(0, len(ulaw), 160)]
    if frames and len(frames[-1]) < 160:
        frames[-1] = frames[-1] + bytes([0xFF]) * (160 - len(frames[-1]))
    if not frames:
        raise SystemExit("--speech: archivo vacío")
    return frames


def _ulaw_a_lineal(u):
    """G.711 µ-law -> PCM16. Tabla propia: audioop no existe desde Python 3.13."""
    u = ~u & 0xFF
    t = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4)
    return (0x84 - t) if (u & 0x80) else (t - 0x84)


_ULAW = [_ulaw_a_lineal(i) for i in range(256)]


def voz_span(frames, piso=300.0, rel_db=-20.0):
    """[M1] (primer, último) índice de frame con voz, por energía.

    Umbral = máx(piso, pico − 20 dB): relativo para que el ruido de fondo de
    una grabación no cuente como voz. Sin voz -> (0, len-1), el archivo entero.
    """
    rms = []
    for f in frames:
        acc = 0
        for b in f:
            v = _ULAW[b]
            acc += v * v
        rms.append((acc / len(f)) ** 0.5)
    umbral = max(piso, max(rms) * 10 ** (rel_db / 20.0)) if rms else piso
    voz = [i for i, r in enumerate(rms) if r > umbral]
    return (voz[0], voz[-1]) if voz else (0, len(frames) - 1)


def load_speech_set(path, utt_s):
    """Devuelve [(marca, frames), ...] — un audio o una carpeta entera.

    La MARCA sale del nombre del archivo (u07_paraguas.wav → "paraguas") y viaja
    después en el call_uuid. Es lo que permite saber, leyendo el log del server,
    qué audio le tocó a cada sesión.

    Con una carpeta se exige que todos los audios midan lo MISMO: el desfase de
    ciclo entre sesiones (cycle_offset) asume un período único. Audios de largos
    distintos desincronizarían los ciclos y además meterían más carga en unos
    grupos que en otros, confundiendo la comparación de latencias.
    """
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.wav"))
                       + glob.glob(os.path.join(path, "*.ulaw"))
                       + glob.glob(os.path.join(path, "*.raw")))
        if not files:
            raise SystemExit(f"--speech: no hay audios en {path}")
    else:
        files = [path]

    juego = []
    for f in files:
        base = os.path.basename(f).rsplit(".", 1)[0]
        marca = base.split("_", 1)[1] if "_" in base else base
        juego.append((marca, load_speech(f, utt_s)))

    largos = {len(fr) for _, fr in juego}
    if len(largos) > 1:
        detalle = ", ".join(f"{m}={len(fr) * FRAME_S:.2f}s" for m, fr in juego)
        raise SystemExit(
            f"--speech: los audios miden distinto ({detalle}).\n"
            f"Igualalos con procesa_audios.py --largo, o el desfase de ciclo entre "
            f"sesiones deja de tener sentido.")
    return juego


class Locutor:
    """Decide qué frame de 20 ms mandar en cada tick. Sabe interrumpir al bot.

    Sin `barge_ms` es la secuencia de siempre: utterance→pausa→repetir, con
    `offset` desfasando el ciclo entre sesiones para no sincronizar los turnos.
    El caudal es SIEMPRE 50 fps como el uplink real: la pausa son frames de
    silencio, no ausencia de frames.

    Con `barge_ms` la sesión deja de hablar por reloj y pasa a hablar por lo que
    OYE: si está en la pausa y el bot lleva `barge_ms` sonando, salta al inicio
    de su utterance y le habla ENCIMA. Eso es una interrupción real, no un
    solapamiento por casualidad.

    El retraso tiene que superar `barge_in.min_tts_time` del servidor (1,2 s en
    producción): antes de eso el gate rechaza la interrupción por eco y no se
    ejercita nada. Y se interrumpe UNA vez por episodio de habla del bot —
    `_episodio` recuerda cuál ya se cortó.

    [M1b] Con `espera` la pausa deja de ser un número fijo de frames: al
    terminar la frase, el locutor manda silencio hasta que el bot respondió y
    calló (AgentAudioDone después del inicio de su habla, más `hueco_s` sin
    audio). Si el bot no empieza a responder en `sin_resp_s`, sigue con la
    frase siguiente (cuenta como perdido). Si el bot no calla en `max_s`, le
    habla encima. El desfase entre sesiones (`offset`) pasa a ser un retraso
    antes de la primera frase, que además espera a que termine el saludo.
    La interrupción intencional (`barge_ms`) funciona igual: corre mientras
    se espera.
    """

    def __init__(self, speech_frames, pause_frames, offset, barge_ms, st, espera=None):
        self.sp = speech_frames or []
        self.st = st
        self.barge_s = (barge_ms / 1000.0) if barge_ms else None
        self.periodo = (len(self.sp) + pause_frames) if self.sp else 1
        self.k = (offset % self.periodo) if self.sp else 0
        self.cortes = 0
        self._episodio = 0.0
        self.v_ini, self.v_fin = voz_span(self.sp) if self.sp else (0, 0)   # [M1]
        st["ttfab_perdidos"] = 0
        # [M1b] turnos por respuesta del bot
        self.espera = espera if self.sp else None
        self._esperando_desde = None
        self._primera_en = None
        if self.espera:
            self._retraso = (offset % self.periodo) * FRAME_S
            self.k = len(self.sp)                            # arranca esperando
            st["sin_respuesta"] = 0
            st["espera_max"] = 0

    def _puede_hablar(self, ahora):
        """[M1b] ¿Ya puede empezar la frase siguiente?"""
        e = self.espera
        if self._esperando_desde is None:
            self._esperando_desde = ahora
        if ahora - self._esperando_desde >= e["max_s"]:
            self.st["espera_max"] += 1                       # el bot no calla: le hablo encima
            return True
        if ahora - self.st.get("bot_ultimo", 0.0) < e["hueco_s"]:
            return False                                     # el bot habla o acaba de callar
        utt_fin = self.st.get("utt_fin")
        if utt_fin is None:                                  # primera frase: retraso inicial
            if self._primera_en is None:
                self._primera_en = ahora + self._retraso
            return ahora >= self._primera_en
        desde = self.st.get("bot_desde", 0.0)
        if desde > utt_fin:                                  # respondió a mi última frase
            if self.st.get("audio_done_t", 0.0) > desde:
                return True                                  # ...y terminó (AgentAudioDone)
            # sin AgentAudioDone (p. ej. un corte): basta un silencio largo
            return ahora - self.st.get("bot_ultimo", 0.0) >= e["silencio_s"]
        if ahora - utt_fin >= e["sin_resp_s"]:
            self.st["sin_respuesta"] += 1                    # no respondió: sigo
            return True
        return False

    def siguiente(self):
        if not self.sp:
            return SILENCE_FRAME
        if self.barge_s and self.k >= len(self.sp):          # estoy en la pausa
            desde = self.st.get("bot_desde")
            ultimo = self.st.get("bot_ultimo")
            if desde and ultimo and desde != self._episodio:
                ahora = time.perf_counter()
                sonando = (ahora - ultimo) < 0.25
                if sonando and (ahora - desde) >= self.barge_s:
                    self._episodio = desde
                    self.k = self.v_ini                      # [M1] arranco encima del bot, con voz
                    self.cortes += 1
                    self.st["barge_t"] = ahora               # [M1] sale ya, en este paquete
                    self.st["corte_abierto"] = True
                    self._esperando_desde = None             # [M1b]
                    self._nueva_frase()
        if self.espera and self.k >= len(self.sp):           # [M1b] esperando al bot
            if not self._puede_hablar(time.perf_counter()):
                return SILENCE_FRAME
            self.k = 0
            self._esperando_desde = None
        if self.k == 0:
            self._nueva_frase()
        f = self.sp[self.k] if self.k < len(self.sp) else SILENCE_FRAME
        if self.k == self.v_fin:
            self.st["utt_fin_pendiente"] = True              # [M1] último frame con voz
        if self.espera:
            self.k += 1                                      # [M1b] al llegar al final, espera
        else:
            self.k = (self.k + 1) % self.periodo
        return f

    def _nueva_frase(self):
        """[M1] Empieza otra frase: un ttfab aún abierto no tuvo respuesta."""
        if self.st.get("ttfab_abierto"):
            self.st["ttfab_abierto"] = False
            self.st["ttfab_perdidos"] += 1

    def siguiente_paquete(self, frames_por_paquete):
        """Agrupa frames consecutivos sin alterar su orden ni su duración.

        El primer paquete de una sesión a 60 ms se agenda a t=60 ms en
        `session`: así no se entrega al servidor audio que aún no existiría
        en un ai-engine que realmente lo está coalesciendo.
        """
        if frames_por_paquete < 1:
            raise ValueError("frames_por_paquete debe ser >= 1")
        return b"".join(self.siguiente() for _ in range(frames_por_paquete))


class Out:
    """TSV incremental, flush por línea (si el cliente muere, lo escrito queda)."""

    def __init__(self, path):
        self.f = open(path, "w", buffering=1)
        self.f.write("ts_utc\tkind\tsid\trole\tvalue_ms\textra\n")

    def row(self, kind, sid, role, value_ms, extra=""):
        self.f.write(f"{now_iso()}\t{kind}\t{sid}\t{role}\t{value_ms:.0f}\t{extra}\n")


async def prober(ws, sid, role, out, every, pong_timeout, stop_evt):
    """ws.ping() explícito periódico. RTT = latencia devuelta por la librería."""
    while not stop_evt.is_set():
        await asyncio.sleep(every)
        t0 = time.perf_counter()
        try:
            pong_waiter = await ws.ping()
            lat = await asyncio.wait_for(pong_waiter, timeout=pong_timeout)
            out.row("ping", sid, role, lat * 1000.0)
        except asyncio.TimeoutError:
            out.row("ping_timeout", sid, role, (time.perf_counter() - t0) * 1000.0,
                    f"sin_pong_en_{pong_timeout}s")
        except ConnectionClosed:
            out.row("ping_deadconn", sid, role, (time.perf_counter() - t0) * 1000.0)
            return
        except Exception as e:
            out.row("ping_err", sid, role, (time.perf_counter() - t0) * 1000.0,
                    f"{type(e).__name__}")
            return


async def drain(ws, stats, out=None, sid="", role="", capture_text=False):
    """Vacía el downlink y, de paso, detecta CUÁNDO habla el bot.

    Cada mensaje binario es audio de TTS bajando. Un hueco de más de 250 ms
    entre binarios cierra un episodio de habla y abre el siguiente: así
    `bot_desde` marca el arranque del episodio EN CURSO, que es contra lo que
    se mide el retraso de la interrupción.
    """
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                stats["in_bytes"] += len(msg)
                ahora = time.perf_counter()
                if ahora - stats.get("bot_ultimo", 0.0) > 0.25:
                    # [M1] el episodio anterior terminó en bot_ultimo
                    if out is not None and stats.get("corte_abierto"):
                        corte = max(0.0, stats.get("bot_ultimo", 0.0) - stats["barge_t"])
                        out.row("corte", sid, role, corte * 1000.0,
                                "fin=natural" if stats.pop("corte_natural", False)
                                else "fin=cortado")
                        stats["corte_abierto"] = False
                    stats["bot_desde"] = ahora     # el bot arrancó a hablar
                    # [M1] primera respuesta después del fin de la frase
                    if out is not None and stats.get("ttfab_abierto") and ahora > stats["utt_fin"]:
                        out.row("ttfab", sid, role, (ahora - stats["utt_fin"]) * 1000.0)
                        stats["ttfab_abierto"] = False
                stats["bot_ultimo"] = ahora
            else:
                try:
                    parsed = json.loads(msg)
                    t = parsed.get("type", "?")
                except Exception:
                    parsed = {}
                    t = "_unparsable"
                stats["in_msgs"][t] = stats["in_msgs"].get(t, 0) + 1
                if t == "AgentAudioDone":
                    stats["audio_done_t"] = time.perf_counter()   # [M1b]
                if t == "AgentAudioDone" and stats.get("corte_abierto"):
                    stats["corte_natural"] = True     # [M1] el bot terminó su frase
                # Opt-in: las transcripciones pueden contener datos de una
                # llamada real. En P0 sirven para contrastar que cada marker
                # conserva su propia conversación aun sin logs de Modal.
                if capture_text and t == "ConversationText" and out is not None:
                    content = str(parsed.get("content") or "")
                    content = content.replace("\r", " ").replace("\n", " ").replace("\t", " ")
                    out.row(
                        "conversation_text", sid, role, 0,
                        json.dumps(
                            {"speaker": str(parsed.get("role") or ""), "content": content},
                            ensure_ascii=False,
                        ),
                    )
    except Exception:
        pass


def close_attribution(exc: ConnectionClosed) -> str:
    """rcvd=Close recibido del server (GB-1o) / sent=Close enviado por nosotros."""
    r = exc.rcvd
    s = exc.sent
    rr = f"rcvd={r.code}:{r.reason}" if r else "rcvd=None"
    ss = f"sent={s.code}:{s.reason}" if s else "sent=None"
    first = "GB-1o" if getattr(exc, "rcvd_then_sent", None) else "cliente-1o"
    return f"{rr}|{ss}|first={first}"


async def session(sid, role, url, start_delay, stop_at, fps, args, out, summary,
                  speech_frames=None, cycle_offset=0, marca="", barge_ms=0,
                  frames_por_paquete=1):
    await asyncio.sleep(start_delay)
    stop_evt = asyncio.Event()
    # Las CTRL son una sonda liviana intencional: conservan su ritmo y mensaje
    # de 20 ms. Sólo LOAD emula el coalescer que luego vivirá en ai-engine.
    packet_s = frames_por_paquete * FRAME_S if role == "load" else 1.0 / fps
    primer_envio_s = packet_s if role == "load" and frames_por_paquete > 1 else 0.0
    st = {"in_bytes": 0, "in_msgs": {}, "frames": 0, "packets": 0}
    summary[sid] = st
    pause_frames = int(args.pause_s / FRAME_S)
    espera = ({"hueco_s": args.hueco_ms / 1000.0, "sin_resp_s": args.sin_respuesta_s,
               "silencio_s": args.silencio_s, "max_s": args.espera_max_s}
              if args.espera_bot and role == "load" else None)          # [M1b]
    loc = Locutor(speech_frames, pause_frames, cycle_offset, barge_ms, st, espera)
    try:
        async with connect(url, open_timeout=30, close_timeout=5, max_size=2**22,
                           ping_interval=20, ping_timeout=20,
                           additional_headers=args.proxy_headers) as ws:
            try:
                lport = ws.transport.get_extra_info("sockname")[1]
            except Exception:
                lport = -1
            settings = json.loads(json.dumps(SETTINGS))
            # La marca va en el call_uuid porque el server ya loguea
            # "Per-call variables received" con session_id Y call_uuid: eso da el
            # cruce log→grupo gratis, sin que el verificador dependa del TSV.
            settings["variables"] = {
                "call_uuid": f"td1{role}-{sid}" + (f"-{marca}" if marca else "")
            }
            t0 = time.perf_counter()
            await ws.send(json.dumps(settings))
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                if isinstance(msg, bytes):
                    continue
                if json.loads(msg).get("type") == "SettingsApplied":
                    out.row("applied", sid, role, (time.perf_counter() - t0) * 1000.0,
                            f"lport={lport}" + (f" marca={marca}" if marca else ""))
                    break

            drainer = asyncio.create_task(
                drain(ws, st, out, sid, role, args.capture_text)
            )
            probe = asyncio.create_task(
                prober(ws, sid, role, out, args.ping_every, args.pong_timeout, stop_evt))

            loop = asyncio.get_running_loop()
            base = loop.time()
            k = 0
            lags = []                                        # [M1] retraso del generador
            while loop.time() < stop_at:
                target = base + primer_envio_s + k * packet_s
                delay = target - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                lags.append((loop.time() - target) * 1000.0)
                ts = time.perf_counter()
                payload = (loc.siguiente_paquete(frames_por_paquete)
                           if role == "load" else loc.siguiente())
                await ws.send(payload)
                if st.pop("utt_fin_pendiente", False):       # [M1] la frase terminó de salir
                    st["utt_fin"] = time.perf_counter()
                    st["ttfab_abierto"] = True
                dur = (time.perf_counter() - ts) * 1000.0
                if dur > args.stall_ms:
                    out.row("send_stall", sid, role, dur)  # backpressure TCP
                st["frames"] += frames_por_paquete if role == "load" else 1
                st["packets"] += 1
                k += 1

            stop_evt.set()
            probe.cancel()
            drainer.cancel()
            if st.get("corte_abierto") and time.perf_counter() - st.get("bot_ultimo", 0.0) > 0.25:
                out.row("corte", sid, role,
                        max(0.0, st.get("bot_ultimo", 0.0) - st["barge_t"]) * 1000.0,
                        "fin=natural" if st.pop("corte_natural", False) else "fin=cortado")
            lags.sort()
            def _q(q):
                return lags[min(len(lags) - 1, int(len(lags) * q))] if lags else 0.0
            out.row("gen_lag", sid, role, _q(0.99),
                    f"p50={_q(0.5):.1f}|p99={_q(0.99):.1f}|max={(lags[-1] if lags else 0.0):.1f}"
                    f"|n={len(lags)}")
            im = st["in_msgs"]
            st["cortes"] = loc.cortes
            out.row("end_ok", sid, role, 0,
                    f"in_bytes={st['in_bytes']}|speak={im.get('AgentStartedSpeaking',0)}"
                    f"|audio_done={im.get('AgentAudioDone',0)}"
                    f"|text={im.get('ConversationText',0)}"
                    f"|frames={st['frames']}|packets={st['packets']}"
                    f"|packet_ms={frames_por_paquete * 20 if role == 'load' else 20}"
                    + (f"|marca={marca}" if marca else "")
                    + f"|ttfab_perdidos={st.get('ttfab_perdidos', 0)}"
                    + (f"|espera_bot=si|sin_respuesta={st.get('sin_respuesta', 0)}"
                       f"|espera_max={st.get('espera_max', 0)}" if espera else "")
                    + (f"|interrumpe=si|cortes={loc.cortes}" if barge_ms
                       else "|interrumpe=no"))  # validez T-D1b: hubo turnos
    except ConnectionClosed as e:
        out.row("death", sid, role, 0, close_attribution(e))
    except Exception as e:
        out.row("error", sid, role, 0, f"{type(e).__name__}: {e}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=40, help="sesiones LOAD 50fps")
    ap.add_argument("--controls", type=int, default=3, help="sesiones CTRL 5fps")
    ap.add_argument("--ramp", type=float, default=40.0)
    ap.add_argument("--hold", type=float, default=180.0)
    ap.add_argument("--ping-every", type=float, default=2.0)
    ap.add_argument("--pong-timeout", type=float, default=25.0)
    ap.add_argument("--stall-ms", type=float, default=200.0)
    ap.add_argument("--fps-ctrl", type=float, default=5.0)
    ap.add_argument("--packet-ms", type=int, choices=(20, 60), default=20,
                    help="duración de cada mensaje LOAD; 20 conserva la ruta actual, "
                         "60 coalesce tres frames. CTRL permanece en 20 ms")
    ap.add_argument("--url", default="ws://127.0.0.1:8765/v1/agent/converse")
    ap.add_argument("--out", default="td1_out.tsv")
    ap.add_argument("--speech", default=None, metavar="ARCHIVO_O_CARPETA",
                    help="T-D1b: WAV PCM16 o .ulaw con habla real; LOAD alterna "
                         "utterance→pausa (CTRL sigue en silencio). T-D1c: una "
                         "CARPETA reparte un audio distinto por sesión (i%%N) y "
                         "manda la marca en el call_uuid, para detectar mezclas "
                         "entre llamadas")
    ap.add_argument("--utt-s", type=float, default=6.0,
                    help="recorta la utterance a N s (0 = completa)")
    ap.add_argument("--barge-in-ms", type=float, default=0,
                    help="ms que deja hablar al bot antes de interrumpirlo (0 = nunca). "
                         "DEBE superar barge_in.min_tts_time del servidor —1200 ms en "
                         "producción— o el gate rechaza la interrupción y no se "
                         "ejercita nada. Sugerido: 1800")
    ap.add_argument("--barge-in-cada", type=int, default=0,
                    help="1 de cada N sesiones interrumpe (0 = ninguna). Con 3 quedan "
                         "~33%% interrumpiendo y el resto normales: A/B en la misma "
                         "corrida. Usa módulo distinto al de los audios (10) para que "
                         "cada marca reciba una mezcla de los dos comportamientos")
    ap.add_argument("--pause-s", type=float, default=8.0,
                    help="silencio entre utterances (deja terminar la respuesta TTS)")
    ap.add_argument("--espera-bot", action="store_true",
                    help="[M1b] cada frase espera a que el bot termine su respuesta "
                         "(AgentAudioDone + --hueco-ms sin audio) en vez de seguir un "
                         "horario fijo; --pause-s sólo fija el desfase inicial")
    ap.add_argument("--hueco-ms", type=float, default=800,
                    help="[M1b] silencio del bot, tras AgentAudioDone, antes de hablar")
    ap.add_argument("--silencio-s", type=float, default=3.0,
                    help="[M1b] sin AgentAudioDone, silencio del bot que cuenta como fin")
    ap.add_argument("--sin-respuesta-s", type=float, default=10.0,
                    help="[M1b] si el bot no empieza a responder en N s, sigue (perdido)")
    ap.add_argument("--espera-max-s", type=float, default=60.0,
                    help="[M1b] si el bot no calla en N s, le habla encima")
    ap.add_argument("--capture-text", action="store_true",
                    help="guarda ConversationText en el TSV para validar P0; puede "
                         "contener transcripciones, por eso es opt-in")
    args = ap.parse_args()
    args.packet_frames = args.packet_ms // 20
    args.proxy_headers = proxy_headers_from_env()

    juego = load_speech_set(args.speech, args.utt_s) if args.speech else []
    speech = juego[0][1] if juego else None
    bi = (f" BARGE-IN cada {args.barge_in_cada} sesiones a los {args.barge_in_ms:.0f}ms"
          if args.barge_in_cada and args.barge_in_ms else "")
    if args.barge_in_cada and not args.barge_in_ms:
        raise SystemExit("--barge-in-cada sin --barge-in-ms: no interrumpiría nunca")
    if len(juego) > 1:
        mode = (f"T-D1c speech={args.speech} audios={len(juego)} "
                f"utt={len(speech)*FRAME_S:.1f}s pause={args.pause_s}s "
                f"marcas={','.join(m for m, _ in juego)}{bi}")
    elif juego:
        mode = f"T-D1b speech={args.speech} utt={len(speech)*FRAME_S:.1f}s pause={args.pause_s}s"
    else:
        mode = "T-D1 silencio"
    out = Out(args.out)
    print(f"[td1] start_utc={datetime.now(timezone.utc).isoformat()} load={args.sessions} "
          f"ctrl={args.controls} ramp={args.ramp}s hold={args.hold}s [{mode}] "
          f"packet_ms={args.packet_ms} proxy={'env' if args.proxy_headers else 'none'} "
          f"turnos={'espera-bot' if args.espera_bot else 'reloj'} "
          f"out={args.out}", flush=True)

    loop = asyncio.get_running_loop()
    stop_at = loop.time() + args.ramp + args.hold
    summary = {}
    tasks = []
    # CTRL primero: establecidas ANTES de la rampa (miden el antes/durante/después)
    for i in range(args.controls):
        tasks.append(asyncio.create_task(session(
            f"c{i}", "ctrl", args.url, 0.5 * i, stop_at, args.fps_ctrl, args, out, summary,
            frames_por_paquete=1)))
    period = (len(speech) + int(args.pause_s / FRAME_S)) if speech else 1
    for i in range(args.sessions):
        # Reparto redondo: la sesión i habla el audio i%N. Con 100 sesiones y 10
        # audios quedan 10 llamadas por marca — una mezcla DENTRO de un grupo es
        # invisible, así que la sensibilidad del detector es ~1-1/N, no 100%.
        marca, frames = juego[i % len(juego)] if juego else ("", None)
        # Quién interrumpe: módulo distinto al de los audios (10) a propósito, para
        # que cada marca reciba sesiones de los dos comportamientos y el A/B no
        # quede confundido con el audio que le tocó.
        interrumpe = bool(args.barge_in_cada and frames
                          and i % args.barge_in_cada == 0)
        tasks.append(asyncio.create_task(session(
            f"l{i}", "load", args.url, 2.0 + i * (args.ramp / args.sessions), stop_at, 50.0,
            args, out, summary, speech_frames=frames,
            cycle_offset=(i * period) // max(1, args.sessions), marca=marca,
            barge_ms=args.barge_in_ms if interrumpe else 0,
            frames_por_paquete=args.packet_frames)))
    await asyncio.gather(*tasks)
    print(f"[td1] end_utc={datetime.now(timezone.utc).isoformat()}", flush=True)
    frames = sum(s["frames"] for s in summary.values())
    packets = sum(s["packets"] for s in summary.values())
    print(f"[td1] frames_sent={frames} packets_sent={packets} "
          f"(analiza con: python3 td1_analyze.py {args.out})", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
