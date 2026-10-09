# PROMPT — Coalescer AudioSocket 60 ms NO se activa con GoldenBridge (bypass en ruta continuous-input)

## Rol y objetivo
Eres un ingeniero senior trabajando sobre el repo `Asterisk-AI-Voice-Agent-FX` (ai-engine, Python/asyncio, Asterisk ARI + AudioSocket). Necesitamos que el coalescer de audio entrante (agrupa 3 tramas de 20 ms en 1 mensaje de 60 ms hacia el proveedor de IA) **funcione de verdad** en la ruta de producción, y hoy no lo hace pese a estar desplegado. Abajo está el diagnóstico completo (ya verificado empíricamente y validado por un segundo revisor), el fix evaluado y el proceso recomendado. Tu tarea: implementar el fix gated como commit en la rama, añadir el test de routing que codifica el gap, y dejar instrucciones de verificación.

## Contexto del despliegue
- Servidor: EC2 us-east-2 (host-net), checkout en `/srv/ai-engine`.
- Rama desplegada: `feat/goldenbridge-modal-coalescer-60ms` @ `0115e1a` (base `e15acd1` = migración GoldenBridge→Modal por env vars; ancestro verificado).
- ai-engine corre como servicio Docker Compose `ai_engine` con bind-mounts de `./src`, `./config`, `./main.py` → un cambio de código aplica con `docker compose restart ai_engine` (sin rebuild; la rama no toca Dockerfile/requirements).
- Config efectiva: `config/ai-agent.yaml` → `audiosocket: {format: slin, frame_ms: 60, host: 172.31.7.92, port: 9092}`. `frame_ms: Literal[20,60]` en `src/config.py:77`. `/reload` NO aplica frame_ms (startup-only).
- Proveedor activo: `deepgram` tipo full-agent, pero el endpoint real es **GoldenBridge en Modal**: `security.py` inyecta `GOLDEN_WS_URL` (wss://…modal.run/v1/agent/converse) + `MODAL_KEY`/`MODAL_SECRET` desde `.env`; `deepgram.py` los manda como headers `Modal-Key`/`Modal-Secret`. **No tocar nada de esto.**
- Estado operativo verificado en llamada real: ARI conectado, AudioSocket OK, `SettingsApplied` de Modal, agente habla. El flujo funciona; lo único roto es el coalescer.

## El inconveniente (hallazgo empírico)
Tras una llamada real completa con ~1.7 MB de audio entrante (`ai_agent_audiosocket_rx_bytes_total`):
- `avb_coalesce_msgs_out_total = 0.0` (contador Prometheus de mensajes coalescidos emitidos, endpoint `http://127.0.0.1:15000/metrics`).
- `avb_coalesce_partial_flush_total` sin muestras (nunca incrementó).
- Cero líneas de log `"AudioSocket provider coalescer first send"`.

Es decir: el audio del llamante sigue yendo a GoldenBridge en tramas individuales de 20 ms. `frame_ms: 60` está cargado pero es inerte.

## Causa raíz (cadena con file:line, sobre `0115e1a`)
1. `src/providers/deepgram.py:367` → el provider declara `requires_continuous_audio=True` (igual que openai_realtime:272, google_live:149, elevenlabs_agent:112).
2. Por esa capability, el audio del caller toma la rama **continuous-input** en `src/engine.py` (~6063–6227): `continuous_input=True` (~6079-6083), encodea con `_encode_for_provider` (~6161), y reenvía **DIRECTO** con `await provider.send_audio(prov_payload, prov_rate, prov_enc)` en ~6194 (fallback firma vieja de 1 arg en ~6202 vía `except TypeError`), y **retorna** (~6227).
3. El call site del coalescer `_send_or_coalesce_provider_audio` (`engine.py:6715`) está en la ruta NO-continuous (VAD-gated), que **ningún full-agent alcanza jamás** → para el propósito de la feature, 6715 es código muerto en producción. Los otros dos call sites (5743 `_stop_call_provider` y 7273 `_audiosocket_handle_disconnect`) son solo flushes de un estado que nunca se acumuló.
4. El guard interno del coalescer (`engine.py:5677-5682`: `frame_ms==60 AND encoding∈{ulaw,mulaw,g711_ulaw} AND provider!=openai_realtime`) habría pasado (deepgram resuelve encoding "ulaw"); el problema es exclusivamente que **la ruta viva nunca llama a la función**.

## El bypass NO fue intencional (evidencia en tests)
- `tests/test_audiosocket_coalescer.py`: todos los tests llaman `_send_or_coalesce_provider_audio` directamente (unidad aislada, mock `send_audio(payload)` de 1 arg). Ninguno codifica "continuous debe hacer bypass".
- `tests/test_coalescer_provider_passthrough.py`: `test_provider_no_retrocea_mulaw_480_con_firma_productiva` y `test_prestream_queue_con_480_se_vacia_en_siguiente_send` preparan al provider deepgram para **recibir mensajes de 480 bytes (= 60 ms µ-law)** → el autor esperaba que llegaran coalescidos. Falta el cableado, no la intención.

## Fix evaluado — IMPORTANTE: la variante naive es INCORRECTA
Reemplazar los sends de 6194/6202 incondicionalmente **rompe Google Live**: el coalescer llama `send_audio(payload)` con 1 solo argumento en todos sus sends internos (5639, 5684, 5723, 5737 — incluso el passthrough), y Google Live necesita `(payload, rate, enc)` para no re-resamplear (comentario CRITICAL en ~6192).

### Fix correcto (gated) — `src/engine.py`, justo antes del send de ~6194:
```python
                    # CRITICAL: Pass sample_rate and encoding to prevent double resampling
                    # Google Live needs to know audio is already at provider_rate to skip resampling
                    if (
                        self._audiosocket_frame_ms == 60
                        and provider_name == "deepgram"
                        and self._canonicalize_encoding(prov_enc) == "ulaw"
                    ):
                        # 60 ms coalescer: route Deepgram µ-law ingress through the
                        # per-call accumulator (frame_ms=60). Other providers keep the
                        # direct send below to preserve (rate, enc) semantics.
                        await self._send_or_coalesce_provider_audio(
                            caller_channel_id,
                            provider,
                            prov_payload,
                            prov_enc,
                            provider_name,
                            provider_active=True,
                        )
                    else:
                        try:
                            await provider.send_audio(prov_payload, prov_rate, prov_enc)
                            # ... bloque existente 6194-6207 SIN cambios (una indentación más)
                        except TypeError:
                            await provider.send_audio(prov_payload)
                            # ... resto del bloque existente
```
Un solo punto de cambio. El guard interno del coalescer re-verifica las mismas condiciones (redundancia inofensiva). Diagnostics (`_update_audio_diagnostics`), `audio_capture.append_encoded` y el bookkeeping quedan intactos (ocurren antes del if).

## Riesgos ya verificados (no re-litigar, pero sí re-testear)
| Riesgo | Verificación |
|---|---|
| `send_audio` con 1 arg y payload de 480 B | `deepgram.py:786+`: con `encoding=None`, 480 B no matchea 160/320/640 → fallback a `input_encoding` config (mulaw→"ulaw") y 8000 Hz; `480 % 160 == 0` → sin warning; µ-law pasa intacto |
| Silencio (gating Google Live / echo-tail guard) | En continuous el silencio se sustituye como PCM16-cero ANTES de `_encode_for_provider` → llega como µ-law válido (0xFF). El quirk de preservación µ-law de 6698-6704 (ruta no-continuous) NO necesita replicarse |
| Latencia +0–40 ms caller→provider | Endpointing de Deepgram opera en cientos de ms; `barge_in.enabled: false` en la config actual → no-issue |
| Flush en disconnect/stall | `7273` (última desconexión AudioSocket) y `5743` (stop provider) son agnósticos de ruta (estado por call_id); stall flush (`_COALESCE_STALL_SECONDS`) ya testeado |
| openai_realtime / google_live / elevenlabs | Con el gate `provider_name == "deepgram"` jamás se desvían; su send directo con fallback TypeError queda intacto |

## Tarea adicional: test de routing que codifique el gap
Añadir a `tests/test_audiosocket_coalescer.py` (o archivo nuevo) un test que ejercite la RUTA, no la función: engine con `_audiosocket_frame_ms=60`, sesión con provider mock deepgram cuyo `get_capabilities()` devuelva `requires_continuous_audio=True`, inyectar 3 tramas de 20 ms µ-law por el path continuous-input y assert de que el provider recibe UN `send_audio` de 480 bytes (y con frame_ms=20, tres sends de 160 B). Ese test falla sobre `0115e1a` y pasa con el fix — es la prueba del gap.

## Proceso de despliegue y verificación (obligatorio)
1. Commit en `feat/goldenbridge-modal-coalescer-60ms` + push → **nuevo SHA** (regla del proyecto: el árbol desplegado debe igualar un SHA verificable; nada de injertos locales sin commitear).
2. En el servidor: `git fetch && git switch feat/goldenbridge-modal-coalescer-60ms && git pull` (sin reset --hard; hay 2 untracked que NO deben tocarse: `scripts/monitor_streaming_perf.py` y `{c[cnt]}`).
3. `docker compose restart ai_engine` (bind mounts recargan; NO usar /reload).
4. Con 1 llamada real, verificar:
   - `curl -s http://127.0.0.1:15000/metrics | grep avb_coalesce_msgs_out_total` → **> 0** y creciendo (~16-17 msgs/seg de audio entrante).
   - `docker logs ai_engine | grep "coalescer first send"` → presente, `frame_bytes=480`.
   - `docker logs ai_engine | grep "Connecting to Deepgram Voice Agent"` → url sigue siendo `wss://…modal.run/…` y llega `SettingsApplied` (Modal intacto).
   - Audio bidireccional OK (saludo del agente audible, sin degradación).
5. Rollback si algo falla: `git switch` de vuelta a `0115e1a` + `docker compose restart ai_engine`.

## Restricciones duras
- NO cambiar GoldenBridge, Modal, Asterisk, `.env`, credenciales ni URLs.
- NO usar `git reset --hard`, `git clean`, `checkout -f`, ni borrar archivos.
- NO imprimir valores de secretos (solo presencia).
- El valor `frame_ms` admite solo 20 o 60; con 20 el comportamiento debe ser byte-idéntico al actual (passthrough).
